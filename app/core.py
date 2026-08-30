"""Shared core: Supabase plumbing, user accounts, cost/activity accounting, and
the subscription-state helpers that both the public app and the local ops console
read and write. Extracted verbatim from server.py (PLAN_1_decompose.md).

This is the seam module: public request handlers WRITE the cost/activity tables
here and the ops dashboards READ them, so both app.* and ops.* import from here.
It never imports from app.services.* or ops.* (one-way dependency).
"""
import datetime
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import *  # noqa: F401,F403 -- shared constants by bare name (as in the monolith)
from subscription_common import (
    is_trial_expired, days_until_trial_end, trial_ends_at_iso,
)


# ---------- Subscription access gate ----------
def _iso_in_future(value):
    """True if `value` is an ISO timestamp that hasn't passed yet."""
    if not value:
        return False
    try:
        when = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) < when


def subscription_state(record):
    """Normalize a user row into the one subscription block everything reads.

    Both the client (which hides the app behind a paywall) and the server-side gate
    below derive from this single function, so the two can't disagree about whether
    an account still has access.
    """
    status = record.get("subscription_status") or "trial"
    trial_ends = record.get("trial_ends_at")
    # A trial row with no end date has not started its clock yet — that's every account
    # created before subscription_schema.sql ran, since ALTER TABLE backfills NULL. Read
    # it as "not expired": is_trial_expired(None) says True, and taking that literally
    # would paywall every pre-existing user the moment the migration lands.
    # ensure_trial_started() below stamps a real date on them at next sign-in.
    expired = is_trial_expired(trial_ends) if (status == "trial" and trial_ends) else False
    if status == "trial":
        days_left = days_until_trial_end(trial_ends) if not expired else 0
    elif status == "beta":
        # A beta grant runs on subscription_end_at, not trial_ends_at, but the client
        # renders the same countdown off days_left either way.
        days_left = days_until_trial_end(record.get("subscription_end_at"))
    else:
        days_left = 0

    if status == "active":
        has_access = True
    elif status == "trial":
        has_access = not expired
    elif status == "beta":
        # Granted by a promo code (BETAUSER). Time-boxed like a trial, and it ends the
        # same way — no card involved, so there is nothing to renew.
        has_access = _iso_in_future(record.get("subscription_end_at"))
    elif status == "canceled":
        # Cancelling is cancel-at-period-end (see subscription_common.cancel_subscription),
        # so a canceled account keeps access until the period it already paid for runs out.
        has_access = _iso_in_future(record.get("subscription_end_at"))
    else:
        # past_due and anything Stripe invents later: no access, and it surfaces as the
        # literal status so the paywall can say something more useful than "expired".
        has_access = False

    return {
        "status": status,
        "trial_ends_at": trial_ends,
        "is_trial_expired": expired,
        "days_left": days_left,
        "subscription_end_at": record.get("subscription_end_at"),
        "stripe_customer_id": record.get("stripe_customer_id"),
        "has_access": has_access,
    }


def _login_payload(record):
    """The response shape handle_login/handle_google_session/handle_google_finish all
    return — the client caches this as-is into currentUser (see loginUser() in script.js),
    so every path that hands back a signed-in session must agree on its shape."""
    return {
        "ok": True,
        # handle_login's callers already have the userid (it's the form field the user
        # typed), but the Google flow generates it server-side and the client never
        # otherwise learns it — see handleGoogleRedirect()/finishGoogleSignup() in script.js.
        "userid": record["userid"],
        "firstName": record["first_name"],
        "lastName": record["last_name"],
        "email": record["email"],
        "location": record.get("location") or "",
        "subscription": subscription_state(record),
    }


def ensure_trial_started(userid, record):
    """Give a dateless trial row a real end date, and return the updated record.

    Accounts that predate the subscription columns come out of the migration with
    subscription_status defaulting to 'trial' and trial_ends_at NULL. Rather than
    backfilling in SQL (which would start everyone's trial at migration time, including
    accounts nobody ever signs into again), the clock starts the first time they sign in.
    """
    if (record.get("subscription_status") or "trial") != "trial" or record.get("trial_ends_at"):
        return record
    starts = trial_ends_at_iso()
    try:
        update_subscription(userid, {"subscription_status": "trial", "trial_ends_at": starts})
    except Exception:
        return record  # best-effort: they keep access either way, we just re-try next login
    record = dict(record)
    record["trial_ends_at"] = starts
    return record

# ---------- Conversation logging (Supabase-backed, server-side only) ----------
# Only actual profile-chat Q&A turns are persisted to the `conversations` table,
# purely for backend visibility — nothing in script.js changes or is even aware
# this happens. Every other /api/messages call (ranking, web search, tracker
# extraction, chat-starter generation, session summarization, ...) is a one-shot
# completion with no real "student answered a question" moment, so it's skipped
# entirely rather than logged with an empty response. There's no session concept
# in this server (no cookies/auth tokens on /api/messages requests), so rows are
# NOT attributed to a specific userid; client_ip is stored as the closest
# available correlation key. Logging is fire-and-forget on a background thread and
# swallows its own errors so a logging hiccup can never break the actual API
# response the user is waiting on.
#
# Run this SQL once in the Supabase SQL editor before conversations start logging:
#   create table conversations (
#       id             bigint generated always as identity primary key,
#       created_at     timestamptz not null default now(),
#       userid         text,
#       client_ip      text,
#       mode           text,   -- 'live' or 'mock'
#       system_prompt  text,   -- reused to hold just the bot's question for this turn
#       user_content   text    -- reused to hold just the student's answer to it
#   );
# To add userid to existing table:
#   alter table conversations add column userid text;
def extract_qa_pair(user_content):
    """Pulls the most recent <bot question, student answer> pair out of a profile-chat
    'CONVERSATION SO FAR' transcript (see profileChatNextQuestion in script.js) — the
    only /api/messages call site where a real student answer is present in the prompt.
    Returns (question, answer), or (None, None) if there's no student answer yet (e.g.
    the very first question of a session, or any non-chat AI call)."""
    m = re.search(r'CONVERSATION SO FAR:\s*(.*?)\s*Respond', user_content, re.S)
    if not m:
        return None, None
    convo = m.group(1).strip()
    if convo in ('', '(nothing yet)'):
        return None, None
    lines = [l for l in convo.split('\n') if l.strip()]
    if not lines or not lines[-1].lower().startswith('student:'):
        return None, None
    answer = lines[-1].split(':', 1)[1].strip()
    if not answer:
        return None, None
    question = lines[-2].split(':', 1)[1].strip() if len(lines) >= 2 and lines[-2].lower().startswith('you:') else None
    return question, answer


# ---------- Interactive API spend ----------
# The app's own AI calls (/api/messages, /api/messages-claude) are billed exactly like the
# agents' but were never costed anywhere, so "spend" in the admin console only ever showed
# batch-agent cost. These roll up into ONE agent_runs row per surface per UTC day —
# items_processed counts calls, cost_usd accumulates — rather than a row per call, which
# would bury real agent runs under thousands of entries. Reusing agent_runs also means no
# new table and no schema migration.
#
# On-demand deadline checks are deliberately NOT rolled up here: they already write their
# own costed rows to deadline_check_log, and the summary reads that table directly. Adding
# them here too would double-count them.
INTERACTIVE_AGENTS = {
    "interactive_gemini": "App — Gemini calls",
    "interactive_claude": "App — Claude calls",
}
_interactive_lock = threading.Lock()
_interactive_rollup = {}  # {(agent, utc_date): agent_runs row id}


def record_interactive_cost(surface, usage, model=None, userid=None, system=None):
    """Add one interactive API call to today's rollup row for `surface`.

    When `userid` is present the SAME cost is additionally attributed to that user in
    `user_costs` (see record_user_cost). That is a breakdown of this rollup, never an
    addition to it — both figures are derived from one cost computation here precisely so
    they can never drift apart.

    Best-effort and fully swallowed on failure: cost accounting must never break or slow
    the user-facing request that triggered it. Called from a background thread.
    """
    if surface not in INTERACTIVE_AGENTS or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        # Price with the provider that actually served the call — Gemini and Anthropic
        # have different per-token and per-search rates, so using one pricer for both
        # would quietly misreport half the interactive spend.
        if surface == "interactive_claude":
            import claude_common as pricing
        else:
            import gemini_common as pricing
        searches = int((usage.get("server_tool_use") or {}).get("web_search_requests", 0) or 0)
        cost = (
            (usage.get("input_tokens") or 0) * pricing.INPUT_PRICE_PER_TOKEN
            + (usage.get("output_tokens") or 0) * pricing.OUTPUT_PRICE_PER_TOKEN
            + searches * pricing.WEB_SEARCH_PRICE_PER_SEARCH
        )
    except Exception as e:
        print(f"[WARN] Could not cost {surface} call: {e}")
        return

    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    key = (surface, today)
    with _interactive_lock:
        run_id = _interactive_rollup.get(key)
        if run_id is None:
            # Reuse an existing row for today if the server restarted mid-day.
            existing = _supabase_request("agent_runs", params={
                "select": "id", "agent": f"eq.{surface}", "mode": f"eq.{today}", "limit": "1"})
            if existing:
                run_id = existing[0]["id"]
            else:
                created = _supabase_request("agent_runs", method="POST", data=[{
                    "agent": surface,
                    "mode": today,
                    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "items_processed": 0, "cost_usd": 0,
                    "total_web_searches": 0, "errors": 0,
                    "notes": f"Rolled-up interactive app calls for {today}"
                             + (f" ({model})" if model else ""),
                }], extra_headers={"Prefer": "return=representation"})
                run_id = created[0]["id"] if created else None
            if run_id is None:
                return
            _interactive_rollup[key] = run_id

        current = _supabase_request("agent_runs", params={
            "select": "items_processed,cost_usd,total_web_searches", "id": f"eq.{run_id}"})
        if not current:
            return
        row = current[0]
        _supabase_request("agent_runs", method="PATCH", params={"id": f"eq.{run_id}"}, data={
            "items_processed": (row.get("items_processed") or 0) + 1,
            "cost_usd": round(float(row.get("cost_usd") or 0) + cost, 6),
            "total_web_searches": (row.get("total_web_searches") or 0) + searches,
            # finished_at is kept current so the row never reads as "interrupted".
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        invalidate_runs_cache()

    # Outside the rollup lock: per-user attribution touches a different table and must not
    # serialize behind it.
    if userid:
        record_user_cost(
            userid,
            "claude" if surface == "interactive_claude" else "gemini",
            classify_feature(system),
            cost=cost,
            model=model,
            input_tokens=usage.get("input_tokens") or 0,
            output_tokens=usage.get("output_tokens") or 0,
            searches=searches,
        )


def record_interactive_cost_async(surface, usage, model=None, userid=None, system=None):
    threading.Thread(target=record_interactive_cost,
                     args=(surface, usage, model, userid, system), daemon=True).start()


# ---------- Per-user cost attribution ----------
# Everything above answers "what did the app spend?". This answers "who spent it, and on
# what?" — the question the $9.99/month subscription makes load-bearing, since a user
# whose AI usage costs more than their plan is a loss per head that no aggregate figure
# can reveal.
#
# THIS IS A BREAKDOWN, NOT A SECOND LEDGER. Every dollar written to user_costs is already
# counted once in agent_runs' interactive_* rollups (or in deadline_check_log). The
# console reads it as a decomposition of that money and never adds the two together. Both
# numbers come from a single cost computation in record_interactive_cost() so they cannot
# drift.
#
# Attribution is best-effort by design: calls that arrive without a userid (signed-out
# visitors, anything before login) are simply not attributed. The console reports the
# residual explicitly as "unattributed" rather than pretending the split is complete.
#
# Feature is classified server-side from the system prompt rather than passed by the
# client, reusing the exact signatures generate_mock_text() already matches on. That keeps
# every existing call site in script.js untouched — but it also means adding a new AI
# feature requires a line here (and in generate_mock_text) or its spend lands in "other".
FEATURE_LABELS = {
    "profile_chat":      "Profile chat",
    "chat_starters":     "Chat starters",
    "chat_findings":     "Chat findings",
    "profile_synthesis": "Profile synthesis",
    "profile_readiness": "Profile readiness",
    "infer_subjects":    "Subject inference",
    "ranking":           "Match ranking",
    "tracker_extract":   "Tracker extraction",
    "venue_search":      "Venue web search",
    "tag_intent":        "Tag intent analysis",
    "tag_suggestions":   "Tag suggestions",
    "resume_import":     "Resume / LinkedIn import",
    "profile_basics":    "Profile basics",
    "profile_extract":   "Profile extraction (tags + basics)",
    "deadline_check":    "Deadline check",
    "match_curation":    "Match curation",
    "match_funnel":      "Match funnel question",
    "other":             "Other",
}

# ---------- Which provider (and which model) spent the money ----------
# `surface` says which of the app's call sites spent it; that is not the same question as
# which vendor is billing for it. Today gemini->Google and claude/deadline_check->Anthropic
# line up one-to-one, but that is a coincidence of the current wiring, not a rule: the
# profile chat is already a deliberate Anthropic holdout inside an otherwise-Gemini app,
# and moving one feature across providers would silently make a surface-based split wrong.
# So provider is derived from the MODEL ID, which is what the invoice is actually keyed on.
#
# Derived, never stored: a `provider` column could drift out of step with `model` after one
# bad write, and this mapping is a pure function of the id.
PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "google": "Google",
    "unknown": "Unknown provider",
}

# Prefix match, so a model bump (claude-haiku-4-5 -> whatever is next) does not need a
# code change to stay classified. Order matters only in that longer prefixes must precede
# shorter ones they extend; none currently do.
_MODEL_PREFIX_PROVIDERS = [
    ("claude", "anthropic"),
    ("gemini", "google"),
]

# Fallback only, for rows written before the model column existed ('' model) — it keeps
# those rows in the right provider bucket instead of dumping every historical dollar into
# "unknown", which would make the breakout look broken on its first day.
_SURFACE_PROVIDERS = {
    "claude": "anthropic",
    "deadline_check": "anthropic",
    "gemini": "google",
}


def provider_for_model(model, surface=None):
    """Map a model id (falling back to the surface) to a provider key."""
    m = (model or "").strip().lower()
    for prefix, provider in _MODEL_PREFIX_PROVIDERS:
        if m.startswith(prefix):
            return provider
    return _SURFACE_PROVIDERS.get(surface, "unknown")


# Tested in order: the two tracker-extraction prompts are prefixes of one another, so the
# longer one has to be checked first — the same ordering constraint generate_mock_text()
# already lives under.
_FEATURE_SIGNATURES = [
    # The merged tags+basics pass. FIRST, because it does the work of `infer_subjects`'s tag
    # branch and `profile_basics` and so matches their wording too; whichever signature is
    # tested first wins, and this one is the accurate answer for that call.
    ("pulling out everything an opportunity-matching app needs", "profile_extract"),
    ("infer which subject categories",                            "infer_subjects"),
    ("Rank the best 10-12 matches",                               "ranking"),
    ("find real, current",                                        "venue_search"),
    ("maintain a single, coherent running profile",               "profile_synthesis"),
    ("decide whether a student's profile has enough detail",      "profile_readiness"),
    # Both the 3-at-a-time regenerate and the 10-at-a-time cached pool are the same feature
    # (chat openers) and must be tested before the generic profile-chat signature below,
    # which their prompts also contain.
    ("exactly THREE distinct",                                    "chat_starters"),
    ("exactly TEN distinct",                                      "chat_starters"),
    ("helping a high schooler build a detailed personal profile", "profile_chat"),
    ("distill a casual chat conversation into new facts",         "chat_findings"),
    ("classify and extract structured tracking data",             "tracker_extract"),
    ("extract structured tracking data",                          "tracker_extract"),
    ("extract ONLY information that would be relevant",           "resume_import"),
    ("pull out a small set of specific profile facts",            "profile_basics"),
    # The ranking prompt only contains "Rank the best 10-12 matches" on one of its two
    # selectionRule branches, so it also gets matched on its stable opening line.
    ("helping a student find the best-fit extracurricular",       "ranking"),
    ("interests/goals to the best opportunities",                 "tag_intent"),
    ("Write directly to them in second person",                   "tag_suggestions"),
    ("extracting specific interests, goals, and pursuits",        "infer_subjects"),
    # The curated-matching pipeline's two live calls (OPPORTUNITY_MATCHING_PLAN.md Phase 1).
    # Distinctive opening lines; appended last since neither is a substring of an earlier
    # prompt (and 'other' is the only thing they'd otherwise fall to).
    ("building a high schooler's CURATED shortlist",              "match_curation"),
    ("narrowing a high schooler's list",                          "match_funnel"),
]


def classify_feature(system):
    """Map a system prompt to one of FEATURE_LABELS' keys.

    Unrecognised prompts bucket to 'other' rather than being dropped — spend you cannot
    name is still spend, and a growing 'other' slice is the signal that a new feature
    needs a signature added above.
    """
    if not system:
        return "other"
    for needle, key in _FEATURE_SIGNATURES:
        if needle in system:
            return key
    return "other"


# Flipped to False the first time user_costs comes back missing, so a server running
# against a database where the migration has not been applied logs once instead of on
# every single AI call. The console reports the table as not ready and shows the SQL.
_user_costs_available = True
# Separately flipped when the table exists but predates the provider/model breakout, so a
# half-migrated database keeps attributing spend (without the model split) instead of
# losing attribution entirely. Re-run user_costs_schema.sql to switch it back on.
_user_costs_has_model = True
_user_costs_lock = threading.Lock()
_user_costs_rows = {}  # {(userid, day, surface, feature, model): user_costs row id}


def record_user_cost(userid, surface, feature, cost, input_tokens=0, output_tokens=0,
                     searches=0, model=None):
    """Accumulate one call into this user's (day, surface, feature) rollup row.

    Read-then-PATCH rather than a PostgREST upsert because upsert REPLACES a conflicting
    row and these counters must ADD. Called from the same background thread that records
    the daily rollup — never on the request path — and swallows everything: cost
    accounting must not be able to break a student's chat.
    """
    global _user_costs_available, _user_costs_has_model
    if not userid or not _user_costs_available or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    userid = str(userid).strip().lower()
    if not userid:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    day = now.date().isoformat()
    # '' rather than None: the grain constraint includes this column and Postgres treats
    # NULLs as distinct, so a NULL model would create a fresh row on every single call.
    model = (model or "").strip()
    key = (userid, day, surface, feature, model)
    try:
        with _user_costs_lock:
            row_id = _user_costs_rows.get(key)
            if row_id is None:
                lookup = {
                    "select": "id", "userid": f"eq.{userid}", "day": f"eq.{day}",
                    "surface": f"eq.{surface}", "feature": f"eq.{feature}", "limit": "1"}
                if _user_costs_has_model:
                    lookup["model"] = f"eq.{model}"
                # Strict, so a missing table/column can be told apart from a network
                # blip. _supabase_request returns None for both alike, and the latches
                # below are permanent for the life of the process: swallowing one timed
                # out lookup as "the table does not exist" silently stopped attributing
                # every later call, which reads as a console frozen mid-day.
                try:
                    existing = _supabase_request_strict("user_costs", params=lookup)
                except Exception as e:
                    if not _missing_table_error(e):
                        print(f"[WARN] user_costs lookup failed (transient, attribution "
                              f"still on): {e}")
                        return
                    if _user_costs_has_model and "model" in lookup:
                        # A missing COLUMN and a missing TABLE report codes that overlap,
                        # so retry without the model filter before concluding the table
                        # itself is absent.
                        lookup.pop("model")
                        try:
                            existing = _supabase_request_strict("user_costs", params=lookup)
                        except Exception as e2:
                            if not _missing_table_error(e2):
                                print(f"[WARN] user_costs lookup failed (transient, "
                                      f"attribution still on): {e2}")
                                return
                            existing = None
                        if existing is not None:
                            _user_costs_has_model = False
                            print("[WARN] user_costs has no `model` column - provider/model "
                                  "breakout is off. Re-run user_costs_schema.sql in the "
                                  "Supabase SQL editor.")
                    else:
                        existing = None
                    if existing is None:
                        _user_costs_available = False
                        print("[WARN] user_costs table unavailable - per-user cost "
                              "attribution is off. Run user_costs_schema.sql in the "
                              "Supabase SQL editor.")
                        return
                if existing:
                    row_id = existing[0]["id"]
                else:
                    new_row = {
                        "userid": userid, "day": day, "surface": surface, "feature": feature,
                        "calls": 0, "input_tokens": 0, "output_tokens": 0,
                        "web_searches": 0, "cost_usd": 0,
                        "first_at": now.isoformat(), "last_at": now.isoformat(),
                    }
                    if _user_costs_has_model:
                        new_row["model"] = model
                    created = _supabase_request("user_costs", method="POST", data=[new_row],
                                                extra_headers={"Prefer": "return=representation"})
                    row_id = created[0]["id"] if created else None
                if row_id is None:
                    return
                _user_costs_rows[key] = row_id

            current = _supabase_request("user_costs", params={
                "select": "calls,input_tokens,output_tokens,web_searches,cost_usd",
                "id": f"eq.{row_id}"})
            if not current:
                return
            r = current[0]
            _supabase_request("user_costs", method="PATCH", params={"id": f"eq.{row_id}"}, data={
                "calls": (r.get("calls") or 0) + 1,
                "input_tokens": (r.get("input_tokens") or 0) + int(input_tokens or 0),
                "output_tokens": (r.get("output_tokens") or 0) + int(output_tokens or 0),
                "web_searches": (r.get("web_searches") or 0) + int(searches or 0),
                "cost_usd": round(float(r.get("cost_usd") or 0) + float(cost or 0), 6),
                "last_at": now.isoformat(),
            })
    except Exception as e:
        print(f"[WARN] Could not attribute cost to {userid}: {e}")


def record_user_cost_async(userid, surface, feature, cost, input_tokens=0,
                           output_tokens=0, searches=0, model=None):
    if not userid:
        return
    threading.Thread(
        target=record_user_cost,
        args=(userid, surface, feature, cost, input_tokens, output_tokens, searches, model),
        daemon=True).start()


def log_conversation(userid, client_ip, mode, system_question, user_response):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/conversations",
            data=json.dumps([{
                "userid": userid,
                "client_ip": client_ip,
                "mode": mode,
                "system_prompt": system_question,
                "user_content": user_response,
            }]).encode(),
            method="POST",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print(f"[INFO] Logged conversation for userid={userid}")
    except Exception as e:
        print(f"[WARN] Failed to log conversation for userid={userid}: {e}")


def log_conversation_async(userid, client_ip, mode, system_prompt, user_content, response_text):
    # system_prompt/response_text are unused now (kept in the signature so both call
    # sites below don't need to change) — only a real <question, answer> pair from the
    # transcript embedded in user_content gets logged.
    question, answer = extract_qa_pair(user_content)
    if not answer:
        return
    threading.Thread(
        target=log_conversation,
        args=(userid, client_ip, mode, question, answer),
        daemon=True,
    ).start()


def _users_request(method, query="", data=None):
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if method == "POST":
        headers["Prefer"] = "return=minimal"
    elif method == "PATCH":
        headers["Prefer"] = "return=minimal"
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/users{query}",
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def _check_signup_consent(is_adult, parental_consent, accepted_terms):
    """The three account-creation consent conditions from Terms of Use §2, shared by
    handle_register and handle_google_finish so a Google signup can't skip the gate a
    password signup enforces. Returns an error message, or None if consent holds."""
    if not accepted_terms:
        return ("You must accept the Terms of Use and Privacy Policy to create an "
                "account.")
    if not is_adult and not parental_consent:
        return ("If you are under 18, a parent or guardian must give permission "
                "before you can create an account.")
    return None


def _unique_userid_from_email(email):
    """Derive a free userid for a Google signup, since Google never supplies one — a
    password registration has the user pick it, but this flow has no form for it.

    Slugified local-part of the email, deduped against existing rows by an incrementing
    numeric suffix. Userid has no format validation elsewhere in this codebase (the
    register form takes free text, only checked for uniqueness), so this only needs to
    be free, not "valid" by some stricter rule.
    """
    local = normalize_email(email).split("@", 1)[0]
    base = re.sub(r"[^a-z0-9._-]", "", local) or "user"
    candidate = base
    suffix = 2
    while get_user(candidate):
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate


def normalize_email(email):
    """The canonical stored/compared form of an address: trimmed and lowercased.

    Uniqueness is case-insensitive, and normalizing on write is what makes that cheap —
    an exact `eq.` match on the normalized value is then the case-insensitive lookup, with
    no `ilike` involved. That matters: `_` is an ILIKE wildcard and a legitimate email
    character, so an ilike search would over-match and refuse registrations it shouldn't.
    """
    return (email or "").strip().lower()


def get_user(userid):
    query = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}", "select": "*"})
    rows = _users_request("GET", query)
    return rows[0] if rows else None


# Every column on `users` EXCEPT `data`. There is no "select everything but X" in
# PostgREST, so the list is explicit — and because a missing column 400s the whole read
# (the trap mailing_list_schema.sql and user_costs_schema.sql both carry), a failure here
# falls back to `*` once and latches, so a pre-migration database degrades to the old
# behaviour rather than breaking sign-in.
_ACCOUNT_COLUMNS = (
    "userid,first_name,last_name,email,password_hash,location,google_id,created_at,"
    "updated_at,token_version,subscription_status,trial_ends_at,subscription_end_at,"
    "stripe_customer_id,stripe_subscription_id,promo_codes_used,is_adult,"
    "parental_consent,terms_accepted_at,privacy_accepted_at,terms_version"
)
_account_select = _ACCOUNT_COLUMNS


def get_user_account(userid):
    """The account row WITHOUT the `data` blob — identity, tokens, subscription.

    `data` holds the student's whole app state (a 37KB tracker is ordinary), and the two
    hottest reads on the app — /api/auth/refresh and the subscription gate — need none of
    it. Reading it anyway made every app open pull that blob down from Supabase several
    times over to answer questions about the columns beside it.
    """
    global _account_select
    query = "?" + urllib.parse.urlencode(
        {"userid": f"eq.{userid}", "select": _account_select})
    try:
        rows = _users_request("GET", query)
    except urllib.error.HTTPError as e:
        if _account_select == "*" or e.code != 400:
            raise
        # One of the columns above has not been migrated in yet. Stop asking for the list.
        print("[WARN] users select fell back to '*' (a column above is missing): "
              f"{_error_body(e).get('message') or e}")
        _account_select = "*"
        return get_user_account(userid)
    return rows[0] if rows else None


def get_user_data(userid):
    """Just the `data` jsonb for this account, or None if there is no such account.

    The inverse of get_user_account: /api/data/load wants only this, and pulling the
    account columns alongside it is what made three key reads cost three full-row reads.
    None means "no such account" and {} means "account with nothing stored" — the two must
    stay distinguishable, because update_user_data turns the first into handle_data_save's
    404 and the second into an ordinary first write.
    """
    query = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}", "select": "data"})
    rows = _users_request("GET", query)
    if not rows:
        return None
    return rows[0].get("data") or {}


def get_user_by_email(email):
    """The account using this address, or None. Case-insensitive; see normalize_email.

    Every account in the table is a live one — `users` has no is_active/deleted column —
    so any hit here is a genuine conflict.
    """
    normalized = normalize_email(email)
    if not normalized:
        return None
    query = "?" + urllib.parse.urlencode({
        "email": f"eq.{normalized}", "select": "userid,email"})
    rows = _users_request("GET", query)
    return rows[0] if rows else None


def get_user_by_google_id(google_id):
    """The account linked to this Google account's `sub`, or None."""
    if not google_id:
        return None
    query = "?" + urllib.parse.urlencode({"google_id": f"eq.{google_id}", "select": "*"})
    rows = _users_request("GET", query)
    return rows[0] if rows else None


class DuplicateEmail(Exception):
    """The email is already on another account.

    Raised for the database's own unique-index rejection, so a race between two
    simultaneous signups reports the same thing as the pre-insert check does.
    """


class MissingUserColumns(Exception):
    """The `users` table is missing the subscription/consent columns.

    Raised instead of letting PostgREST's bare 400 surface as "Could not reach
    Supabase", which is what the missing migration looked like before and cost a
    session of debugging. See subscription_schema.sql.
    """


def create_user(userid, first_name, last_name, email, password_hash, location="",
                is_adult=False, parental_consent=False, google_id=None):
    """Insert a new account, starting its free trial and recording signup consent.

    is_adult / parental_consent come from the registration checkboxes; the caller
    (handle_register / handle_google_finish) is what enforces them, this just records
    what was agreed to. Every column past `data` requires subscription_schema.sql to
    have been run; google_id additionally requires google_auth_schema.sql (password_hash
    is None for a Google-only account — that schema also drops the NOT NULL on it).
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    row = {
        "userid": userid,
        "first_name": first_name,
        "last_name": last_name,
        "email": normalize_email(email),
        "password_hash": password_hash,
        "location": location,
        "data": {},
        "subscription_status": "trial",
        "trial_ends_at": trial_ends_at_iso(),
        "subscription_end_at": None,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "promo_codes_used": [],
        "is_adult": is_adult,
        "parental_consent": parental_consent,
        "terms_accepted_at": now,
        "privacy_accepted_at": now,
        "terms_version": TERMS_VERSION,
    }
    # Only set on a Google signup — omitted (not merely None) for a plain password
    # registration, so that path keeps working even before google_auth_schema.sql has
    # been run: PostgREST 400s the whole insert on an unrecognized column, and google_id
    # would be exactly that until the migration lands.
    if google_id is not None:
        row["google_id"] = google_id
    try:
        _users_request("POST", "", data=[row])
    except urllib.error.HTTPError as e:
        # The body reads only once, so classify from a single copy of it.
        detail = _error_body(e)
        if e.code == 400 and detail.get("code") in _MISSING_COLUMN_CODES:
            raise MissingUserColumns() from e
        if e.code == 409 and _is_email_conflict(detail):
            raise DuplicateEmail() from e
        raise


# PostgREST reports an unknown column two different ways depending on the verb: a read
# gets Postgres's own 42703 (undefined_column), while a write is rejected earlier, by
# PostgREST's schema cache, as PGRST204. Both mean "run the migration".
_MISSING_COLUMN_CODES = {"42703", "PGRST204"}

# Name of the unique index on lower(email) — see users_email_unique_schema.sql. Matched
# against the constraint Postgres names in a 23505, to tell an email collision apart from
# a userid collision, which are both 409s and need different messages.
EMAIL_UNIQUE_INDEX = "users_email_lower_key"


def _error_body(http_error):
    """The JSON body of a PostgREST error, as a dict. Readable exactly once."""
    try:
        return json.loads(http_error.read().decode("utf-8", "replace"))
    except Exception:
        return {}


def _is_email_conflict(detail):
    """True if a 23505 unique violation came from the email index rather than the PK."""
    if detail.get("code") != "23505":
        return False
    blob = " ".join(str(detail.get(k) or "") for k in ("message", "details", "hint"))
    return EMAIL_UNIQUE_INDEX in blob or "email" in blob.lower()


def update_user_location(userid, location):
    record = get_user(userid)
    if not record:
        return False
    query = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}"})
    _users_request("PATCH", query, data={"location": location})
    return True


def update_password_hash(userid, password_hash):
    """Overwrite an account's stored password hash. Used by the login-time upgrade that
    rewrites a legacy client-SHA-256 row as argon2 (PLAN_2_auth.md)."""
    query = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}"})
    _users_request("PATCH", query, data={"password_hash": password_hash})
    return True


def update_user_data(userid, key, value):
    # Read-modify-write of one key inside the `data` jsonb. Only `data` is selected —
    # the account columns beside it are never looked at here.
    data = get_user_data(userid)
    if data is None:
        return False
    data[key] = value
    query = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}"})
    _users_request("PATCH", query, data={"data": data})
    return True


def update_subscription(userid, updates):
    """Update subscription fields for a user. updates is a dict of fields to update."""
    record = get_user(userid)
    if not record:
        return False
    query = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}"})
    _users_request("PATCH", query, data=updates)
    return True


def bump_token_version(userid):
    """Invalidate every outstanding refresh token for this account (PLAN_2_auth.md).

    The refresh route compares a token's `ver` claim against users.token_version, so
    incrementing that column makes all existing refresh tokens fail to renew — sessions
    then die within one access-token lifetime. This is the revocation primitive behind
    "log out everywhere" and account-kill. Returns the new version, or None on failure.

    Degrades if auth_schema.sql has not run yet: the column simply isn't there, so revocation
    is a no-op (nothing to bump), consistent with how the rest of auth treats a version of 0.
    """
    record = get_user(userid)
    if not record:
        return None
    current = int(record.get("token_version") or 0)
    new_version = current + 1
    query = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}"})
    try:
        _users_request("PATCH", query, data={"token_version": new_version})
    except urllib.error.HTTPError as e:
        if _is_missing_column_error(e):
            print("[WARN] users.token_version missing - session revocation is off. "
                  "Run auth_schema.sql in the Supabase SQL editor.")
            return None
        raise
    return new_version


def _supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }


def _supabase_request(table, method="GET", params=None, data=None, extra_headers=None):
    """Minimal service-role Supabase call for the admin tables (agent_runs, scraper_seeds).

    Kept local rather than importing supabase_common, matching the convention set at the
    top of this file: server.py keeps its own tiny helpers to hold its import surface down.
    Returns parsed JSON, or None on failure (callers decide what an empty result means).
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = dict(_supabase_headers())
    headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else []
    except Exception as e:
        print(f"[WARN] Supabase {method} {table} failed: {e}")
        return None


# Recent-runs cache. get_agent_status() used to issue one Supabase round-trip PER AGENT on
# every poll; with four agents and a 3s dashboard poll that was over a request a second,
# forever, for data that changes at most once per run. One query now covers every agent and
# is reused for a few seconds.
_runs_cache = {"at": 0.0, "rows": []}
_runs_cache_lock = threading.Lock()
RUNS_CACHE_TTL = 5
RECENT_RUNS_LIMIT = 200


def invalidate_runs_cache():
    with _runs_cache_lock:
        _runs_cache["at"] = 0.0


# ---------- user_activity: the one new write path ----------
# Counts are accumulated in memory and flushed by a background thread, rather than written
# per request. Two reasons, and the second is the load-bearing one:
#   * this table takes EVERY authenticated request, not just billed ones, so a write per
#     request would multiply the app's Supabase traffic by a large constant; and
#   * `hits` and `surfaces` need real increments, which PostgREST cannot express without
#     a stored function (DDL this repo cannot run) — so it is read-modify-write either
#     way, and batching turns a round trip per request into one per user per interval.
# A process that dies between flushes loses at most one interval of counts. DAU, WAU and
# retention only need the row to EXIST, so they are unaffected; only `hits`/`surfaces`
# are approximate, and they are colour rather than headline figures.
_activity_lock = threading.Lock()
_activity_buffer = {}          # (userid, day) -> {"hits", "surfaces", "first_at", "last_at"}
_activity_available = True     # latched off if the table isn't there
_activity_flusher = None
ACTIVITY_FLUSH_SECONDS = 30.0


def touch_user_activity(userid, surface):
    """Record that this user did something. Never raises, never blocks the request."""
    if not userid or not _activity_available:
        return
    try:
        uid = str(userid).strip().lower()
        if not uid:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (uid, now.date().isoformat())
        stamp = now.isoformat()
        with _activity_lock:
            entry = _activity_buffer.setdefault(
                key, {"hits": 0, "surfaces": {}, "first_at": stamp, "last_at": stamp})
            entry["hits"] += 1
            entry["surfaces"][surface] = entry["surfaces"].get(surface, 0) + 1
            entry["last_at"] = stamp
        _start_activity_flusher()
    except Exception as e:
        # Activity logging must never be the reason a user's request fails. Same posture
        # as log_conversation().
        print(f"[WARN] Could not buffer activity for {userid}: {e}")


def _start_activity_flusher():
    global _activity_flusher
    if _activity_flusher is not None:
        return
    with _activity_lock:
        if _activity_flusher is not None:
            return
        _activity_flusher = threading.Thread(target=_activity_flush_loop, daemon=True)
        _activity_flusher.start()


def _activity_flush_loop():
    while True:
        time.sleep(ACTIVITY_FLUSH_SECONDS)
        if not _activity_available:
            return
        flush_user_activity()


def flush_user_activity():
    """Drain the buffer into user_activity. Called on a timer and before reading metrics."""
    global _activity_available
    with _activity_lock:
        pending = dict(_activity_buffer)
        _activity_buffer.clear()
    if not pending:
        return
    for (uid, day), delta in pending.items():
        try:
            _activity_apply(uid, day, delta)
        except Exception as e:
            if _missing_table_error(e):
                _activity_available = False
                print(f"[WARN] user_activity table unavailable - DAU/WAU/retention are "
                      f"off. Run {USER_ACTIVITY_SETUP_SQL} in the Supabase SQL editor.")
                return
            # A transient failure loses this interval's counts for one user. Put nothing
            # back in the buffer: retrying forever would let one poisoned key grow without
            # bound, and the row this drops is a count, not the day's existence.
            print(f"[WARN] Could not record activity for {uid} on {day}: {e}")


def _activity_apply(uid, day, delta):
    existing = _supabase_request_strict("user_activity", params={
        "select": "hits,surfaces,first_at", "userid": f"eq.{uid}",
        "day": f"eq.{day}", "limit": "1"})
    if existing:
        row = existing[0]
        surfaces = dict(row.get("surfaces") or {})
        for k, v in delta["surfaces"].items():
            surfaces[k] = surfaces.get(k, 0) + v
        _supabase_request_strict("user_activity", method="PATCH",
                                 params={"userid": f"eq.{uid}", "day": f"eq.{day}"},
                                 data={"hits": int(row.get("hits") or 0) + delta["hits"],
                                       "surfaces": surfaces,
                                       # first_at is only ever set by the insert below;
                                       # re-sending it would walk the day's start forward.
                                       "last_at": delta["last_at"]})
    else:
        _supabase_request_strict("user_activity", method="POST", data=[{
            "userid": uid, "day": day, "hits": delta["hits"],
            "surfaces": delta["surfaces"],
            "first_at": delta["first_at"], "last_at": delta["last_at"]}])


# ---------- user_events: the append-only behavioral event log ----------
# The matcher's revealed-preference loop (recent saves up, dismisses down) and its
# "not interested -> re-rank" need per-EVENT grain that user_activity (a daily rollup)
# throws away. Capture ships EARLY even though nothing reads it for weeks, because an
# unlogged click is unrecoverable — the same logic as the metrics daily snapshot.
#
# Unlike user_activity this is APPEND-ONLY, so the flush is a single batch INSERT with no
# read-modify-write (there is no existing row to merge into). Same three postures as
# activity, for the same reasons: buffered + background flush (this table takes every
# impression, the highest-volume UI event there is); latch off on a missing table/column so
# an un-run migration is a quiet no-op, not a broken request; and fail-open — event capture
# must NEVER be the reason a student's request fails or the UI blocks.
_events_lock = threading.Lock()
_events_buffer = []            # list of ready-to-insert row dicts
_events_available = True       # latched off if the table/columns aren't there
_events_flusher = None
EVENTS_FLUSH_SECONDS = 20.0
EVENTS_MAX_BUFFER = 5000       # backstop: a wedged flush can't grow memory without bound
# Free text in the column (a new action is a code change, not a DDL migration), but the write
# path whitelists so a malformed/hostile body can't seed arbitrary rows. Keep in step with the
# action list documented in user_events_schema.sql.
_VALID_EVENT_ACTIONS = {
    "impression", "open", "save", "track", "apply_click",
    "dismiss", "untrack", "search", "tag_filter",
}


def record_user_events(userid, events):
    """Buffer a batch of behavioral events for this user. Never raises, never blocks.

    `events` is the list the client batched for one tick — each a
    {action, opportunity_id?, context?} dict. Returns the count ACCEPTED (unknown actions
    and non-dicts are dropped), or 0 when capture is off / the caller is unidentified.
    Server-stamps ts on arrival: client clocks are not trusted, and arrival order is all the
    aggregate reads need.
    """
    if not userid or not _events_available or not events:
        return 0
    accepted = 0
    try:
        uid = str(userid).strip().lower()
        if not uid:
            return 0
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rows = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            action = str(ev.get("action") or "").strip()
            if action not in _VALID_EVENT_ACTIONS:
                continue
            opp = ev.get("opportunity_id")
            opp = str(opp).strip() if opp not in (None, "") else None
            context = ev.get("context")
            if not isinstance(context, dict):
                context = {}
            rows.append({"userid": uid, "ts": stamp, "action": action,
                         "opportunity_id": opp, "context": context})
        if not rows:
            return 0
        with _events_lock:
            overflow = len(_events_buffer) + len(rows) - EVENTS_MAX_BUFFER
            if overflow > 0:
                # Drop the OLDEST to bound memory when a flush is wedged. Telemetry read
                # only in aggregate — a dropped interval is a gap, never a correctness bug.
                del _events_buffer[:overflow]
            _events_buffer.extend(rows)
            accepted = len(rows)
        _start_events_flusher()
    except Exception as e:
        # Same posture as touch_user_activity(): capture must never break a request.
        print(f"[WARN] Could not buffer events for {userid}: {e}")
    return accepted


def _start_events_flusher():
    global _events_flusher
    if _events_flusher is not None:
        return
    with _events_lock:
        if _events_flusher is not None:
            return
        _events_flusher = threading.Thread(target=_events_flush_loop, daemon=True)
        _events_flusher.start()


def _events_flush_loop():
    while True:
        time.sleep(EVENTS_FLUSH_SECONDS)
        if not _events_available:
            return
        flush_user_events()


def flush_user_events():
    """Drain the event buffer into user_events in one batch INSERT. Called on a timer (and
    available for a consumer to call before reading, the way flush_user_activity is)."""
    global _events_available
    with _events_lock:
        pending = list(_events_buffer)
        _events_buffer.clear()
    if not pending:
        return
    try:
        _supabase_request_strict("user_events", method="POST", data=pending,
                                 extra_headers={"Prefer": "return=minimal"})
    except Exception as e:
        if _missing_table_error(e) or _is_missing_column_error(e):
            _events_available = False
            print(f"[WARN] user_events table unavailable - behavioral capture is off. "
                  f"Run {USER_EVENTS_SETUP_SQL} in the Supabase SQL editor.")
            return
        # Transient failure: drop this batch rather than re-buffering it. Retrying forever
        # would let one poisoned batch grow the buffer without bound, and what this loses is
        # a slice of an aggregate stream, not a fact anyone reads on its own.
        print(f"[WARN] Could not record {len(pending)} events: {e}")


def _is_missing_column_error(exc):
    """True when PostgREST rejected a call because a migration has not been run.

    A read reports Postgres's own 42703, a write reports PGRST204 from the schema cache;
    both mean the same thing, and both are worth telling the operator apart from a real
    failure so they get pointed at the .sql file instead of at a bug.
    """
    if not isinstance(exc, urllib.error.HTTPError):
        return False
    return (_error_body(exc) or {}).get("code") in _MISSING_COLUMN_CODES


def _missing_table_error(exc):
    """True when PostgREST rejected the call because mailing_list_schema.sql has not run.

    An unknown TABLE reports PGRST205 (and 42P01 from Postgres itself), where an unknown
    COLUMN reports the codes _is_missing_column_error already knows about. Both mean "run
    the .sql file", and both must be told apart from a real failure so the console can
    show a setup step instead of an error.
    """
    if not isinstance(exc, urllib.error.HTTPError):
        return False
    code = (_error_body(exc) or {}).get("code")
    return code in ("PGRST205", "42P01") or code in _MISSING_COLUMN_CODES


def _supabase_request_strict(table, method="GET", params=None, data=None, extra_headers=None):
    """_supabase_request, but raising instead of swallowing.

    _supabase_request returns None for every failure alike, which cannot tell "the table
    does not exist yet" from "Supabase is unreachable". Those need different answers — one
    is a setup step the operator can act on, the other is an outage — so the console read
    below uses this and classifies the error itself.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL/SUPABASE_SERVICE_KEY not configured.")
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = dict(_supabase_headers())
    headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else []

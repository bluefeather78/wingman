#!/usr/bin/env python3
"""Local dev server: serves the static site and proxies /api/messages to the
Gemini API (gemini-3.5-flash-lite — see MESSAGES_MODEL below), and /api/messages-claude
to the Anthropic API (claude-haiku-4-5 — see CLAUDE_MODEL below; used only by the profile
chat's next-question/starter-question calls). If the relevant API key is not set, each
endpoint fabricates plausible mock responses instead so the app is fully click-through-able
without a real key or network access. Mock responses are pattern-matched against each
system prompt used in script.js's callGemini()/callClaude() call sites.
"""
import datetime
import http.cookies
import json
import os
import re
import random
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# gemini_common/check_deadlines are leaf modules (no argparse/CLI side effects at import
# time — check_deadlines.py's main() is guarded by if __name__ == "__main__") — importing
# their deadline-check logic here avoids re-implementing the same Gemini system prompt/
# schema/thinking-budget handling a second time for the new on-demand endpoint below, and
# call_gemini() itself is reused directly by /api/messages (see proxy_to_gemini) so the
# request/response translation and web-search-nudge/thinking-budget handling only exist
# in one place. Deliberately NOT importing supabase_common, though (see its own
# docstring): server.py keeps its own tiny Supabase GET/PATCH helpers to minimize its
# import surface for that specific piece.
from agent_common import PREVIEW_PREFIX as AGENT_PREVIEW_PREFIX
from check_deadlines import (check_one as check_deadline_one,
                             VALID_STATUS as DEADLINE_VALID_STATUS,
                             # Imported rather than re-declared: check_deadlines.py pins its own
                             # model and check_one() is what actually calls it, so a bump there
                             # must not leave the cost breakout naming a stale model here.
                             CLAUDE_MODEL as DEADLINE_CHECK_MODEL)
from gemini_common import call_gemini
from claude_common import call_claude
import url_dedupe
from subscription_common import (
    create_checkout_session, validate_promo_code, get_or_create_customer,
    get_customer_subscriptions, trial_ends_at_iso, is_trial_expired,
    days_until_trial_end, cancel_subscription, promo_kind, extend_from,
    GRANTABLE_STATUSES
)
import subprocess
import tempfile


def load_dotenv(path=".env"):
    """Minimal stdlib-only .env loader — populates os.environ from KEY=VALUE
    lines so secrets like GEMINI_API_KEY never have to be typed inline in a
    command (which is how one got leaked into shell history before)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # Only skip a key that's already set to a *non-empty* value in the
            # environment — an empty-string env var (e.g. left over from an earlier
            # inline `GEMINI_API_KEY="" python3 server.py`) should not shadow a
            # real value from .env, or the server silently stays in MOCK mode forever.
            if key and not os.environ.get(key):
                os.environ[key] = value


load_dotenv()

PORT = 8000

# ---------- /api/messages (Gemini-backed) ----------
# Interactive, in-page AI calls from script.js's callGemini() — ranking, profile chat,
# tracker extraction, etc. Pinned to gemini-3.5-flash-lite: cheaper/faster than
# gemini_common.MODEL ("gemini-3.6-flash", used by the offline batch scripts
# check_deadlines.py/check_reviews.py/scrape_opportunities.py), which matters here since
# these calls block a real page interaction instead of running unattended. Revisit
# alongside gemini_common.MODEL — see that module's docstring on model ID churn.
# NOTE: "gemini-3.6-flash-lite" (as literally requested) does not exist in the Gemini API —
# there is no lite variant of the 3.6 generation yet (confirmed via ListModels against the
# live key on 2026-08-18). Pinned to gemini-3.5-flash-lite instead, the closest existing
# lite model, following the same "pin an exact version" convention as gemini_common.py's
# MODEL constant. Swap this if/when a real gemini-3.6-flash-lite ships.
MESSAGES_MODEL = "gemini-3.5-flash-lite"
# Uniform cap across every /api/messages call site (mirrors the old Anthropic path's
# uniform max_tokens=1000) — bumped above what each system prompt's own "stay well
# within a 1000-token response" instruction asks for, to leave headroom for Gemini 3.x's
# thinking tokens, which draw from this SAME budget (see gemini_common.py's "FOURTH
# finding" docstring — at max_tokens=700 there, thinking alone consumed 673 of it).
MESSAGES_MAX_TOKENS = 2000

# ---------- /api/messages-claude (Anthropic-backed, profile chat only) ----------
# profileChatNextQuestion/profileChatStarterQuestionsFromAI (script.js's callClaude())
# deliberately stayed on Claude rather than moving to Gemini with the rest of the app on
# 2026-08-18 — a separate endpoint from /api/messages so the Gemini path above is
# untouched; client still sends the same plain {system, userContent, useWebSearch} body,
# translated into Anthropic's content-block/messages shape here rather than on the client.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_MAX_TOKENS = 1000

# ---------- Opportunities catalog (Supabase-backed) ----------
# The opportunity catalog lives in a Supabase (hosted Postgres) table rather than
# the old static opportunities.json — see migrate_to_supabase.py for the one-time
# migration and CLAUDE.md for the rationale (scalability + free tier vs local SQLite).
# The anon key is safe to hold server-side here: it's rate-limited by Supabase and
# the table's Row Level Security policy only allows reading is_active=true rows.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
OPPORTUNITIES_FIELDS = "id,name,org,summary,url,subject_tags,type,price,state,location,intl,season,review_status,review_summary,grade_min,grade_max"
OPPORTUNITIES_CACHE_TTL = 300  # seconds
_opportunities_cache = {"data": None, "fetched_at": 0.0}
_opportunities_cache_lock = threading.Lock()

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


# ---------- Signup consent & eligibility policy ----------
# The Terms of Use (legal/terms.md §2) restrict Wingman to users 13 or older, and
# require a parent/guardian's permission for anyone under 18. Registration collects
# three acknowledgements and refuses the account without them; what was agreed to is
# stamped onto the user row (see create_user) so it can be audited later.
#
# TERMS_VERSION is what gets recorded per account. It is the effective date printed at
# the top of both documents — bump it whenever legal/*.md changes materially, so rows
# accepted under the old text are distinguishable from rows accepted under the new.
TERMS_VERSION = "2026-08-21"

# ---------- Persistent user account database (Supabase-backed) ----------
# Account records live in a Supabase `users` table rather than the old flat
# users_db.json file — see migrate_users_to_supabase.py for the one-time
# migration. Unlike the opportunities table, this table has NO RLS policies at
# all, so the anon key gets zero access; every request here uses the
# service_role key, which bypasses RLS. That key must never be sent to the
# browser — it's only ever used from this server process.
# passwordHash arrives already SHA-256-hashed client-side; the server never
# sees or stores a plaintext password.
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# ---------- Google Sign-In (OAuth 2.0 authorization-code flow) ----------
# Client ID/Secret from a Google Cloud OAuth client (Web application type), configured with
# redirect URIs for both localhost and the production domain. Server-side redirect flow, not
# Google Identity Services JS — the callback exchanges a code for tokens itself, so no
# client-side Google library is needed. See google_auth_schema.sql for the users table columns
# this depends on (google_id, and password_hash made nullable for Google-only accounts).
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# One-time-use handoff tokens bridging the OAuth redirect back to the SPA, which has no
# cookie/session concept of its own (see handle_login: login is just a POST that returns
# user JSON, cached client-side). Minted in handle_google_callback, consumed exactly once
# by handle_google_session. In-process only, like _opportunities_cache — fine for a
# single-process dev/prod server, and these are short-lived by design.
_google_session_tokens = {}
GOOGLE_TOKEN_TTL_SECONDS = 5 * 60


def _prune_google_tokens():
    now = time.time()
    expired = [t for t, entry in _google_session_tokens.items() if entry["expires_at"] < now]
    for t in expired:
        del _google_session_tokens[t]


def _mint_google_token(payload):
    _prune_google_tokens()
    token = secrets.token_urlsafe(32)
    _google_session_tokens[token] = {
        **payload,
        "expires_at": time.time() + GOOGLE_TOKEN_TTL_SECONDS,
    }
    return token


def _take_google_token(token):
    """Look up and delete a token in one step — single-use, so a replayed or leaked
    URL (browser history, a referrer header) can't be reused to resolve a session twice."""
    _prune_google_tokens()
    return _google_session_tokens.pop(token, None)


# ---------- Google Calendar sync ----------
# A separate, additional OAuth grant from Google Sign-In above: sign-in only ever asks for
# "openid email profile", so an existing signed-in session (password or Google) has no
# token that can touch Calendar. Connecting Calendar is its own start/callback pair that
# requests the calendar.events scope against the already-known userid, and persists the
# resulting tokens (see google_calendar_schema.sql) rather than discarding them like the
# short-lived _google_session_tokens above — a sync can then run again later without
# re-prompting, as long as the refresh token stays valid.
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
WINGMAN_CALENDAR_NAME = "Highschool Wingman"
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

# state -> {"userid": ..., "expires_at": ...}. Mirrors _google_session_tokens: in-process,
# short-lived, fine for a single-process server. Keyed separately from the sign-in state
# cookie so a stale calendar-connect attempt can't be replayed against the sign-in flow
# or vice versa.
_google_calendar_states = {}


def _prune_google_calendar_states():
    now = time.time()
    expired = [s for s, entry in _google_calendar_states.items() if entry["expires_at"] < now]
    for s in expired:
        del _google_calendar_states[s]


# ---------- On-demand, shared/cached deadline check (Claude Haiku-backed) ----------
# Replaces check_deadlines.py's batch/cron model as the primary way status/deadlines data
# gets populated: rather than proactively scanning the whole catalog on a schedule (which,
# on 2026-08-18, burned through Gemini's daily grounding quota partway through a single
# full pass), a check now only runs when a real user actually adds an opportunity to their
# tracker, or loads the Tracker page with an already-tracked item whose cached data has
# gone stale. Uses Claude Haiku (claude-haiku-4-5-20251001) with web search enforced.
# See check_deadlines.py's docstring for the underlying Supabase columns
# (status/important_dates/was_estimated/important_date_note/last_checked_at) — this endpoint
# reads/writes the exact same columns, so the two mechanisms share one cache. important_dates
# holds EVERY pertinent date for the opportunity (registration opens/closes, event start/end,
# notifications, etc.), each tagged with a "type" — not just a single narrow "deadline"; see
# check_deadlines.py's build_system() for the full schema. The batch script still exists for
# bulk backfill/cleanup (e.g. after a big scrape), but is no longer the primary way this data
# gets kept current — see the plan doc's "On-demand deadline checking" section for the full
# rationale.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")  # Kept for other uses; not used for deadline checking
DEADLINE_STALE_DAYS = 7
DEADLINE_FIELDS = "id,name,org,url,summary,status,important_dates,was_estimated,important_date_note,last_checked_at"


def get_opportunity_for_deadline_check(opp_id):
    query = urllib.parse.urlencode({"select": DEADLINE_FIELDS, "id": f"eq.{opp_id}"})
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        rows = json.loads(resp.read())
    return rows[0] if rows else None


def patch_opportunity_deadline(opp_id, patch):
    query = urllib.parse.urlencode({"id": f"eq.{opp_id}"})
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        data=json.dumps(patch).encode(),
        method="PATCH",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def log_deadline_check(opp_id, source, status, web_searches, cost_usd, was_estimated, notes=None):
    """Log a deadline check to the deadline_check_log table (non-blocking)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return
    try:
        log_entry = {
            "opportunity_id": opp_id,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": source,
            "status": status,
            "web_searches": web_searches,
            "cost_usd": round(cost_usd, 4) if cost_usd else None,
            "was_estimated": was_estimated,
            "notes": notes,
        }
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/deadline_check_log",
            data=json.dumps(log_entry).encode(),
            method="POST",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as e:
        # Logging failure should not break the main request
        print(f"[WARN] Failed to log deadline check for {opp_id}: {e}")


def deadline_cache_is_fresh(last_checked_at):
    if not last_checked_at:
        return False
    try:
        checked = datetime.datetime.fromisoformat(last_checked_at.replace("Z", "+00:00"))
    except Exception:
        return False
    return datetime.datetime.now(datetime.timezone.utc) - checked < datetime.timedelta(days=DEADLINE_STALE_DAYS)


def cached_deadline_payload(opp, source):
    return {
        "status": opp.get("status"),
        "important_dates": opp.get("important_dates") or [],
        "was_estimated": opp.get("was_estimated"),
        "important_date_note": opp.get("important_date_note"),
        "last_checked_at": opp.get("last_checked_at"),
        "source": source,
    }


def mock_deadline_check_payload(opp):
    # MOCK mode (no GEMINI_API_KEY): fabricate a plausible response, same spirit as
    # generate_mock_text()'s GEMINI_API_KEY fallback, but deliberately does NOT write
    # to Supabase — a mock value getting cached and served to real users for 7 days would
    # be worse than just re-fabricating it every time mock mode is active.
    deadline_iso = mock_deadline_iso((opp.get("name") or "") + (opp.get("url") or ""))
    return {
        "status": "running",
        "important_dates": [{"label": "Application Deadline", "date_iso": deadline_iso, "type": "deadline"}],
        "was_estimated": True,
        "important_date_note": "Mock data — set GEMINI_API_KEY for a real, live-searched check.",
        "last_checked_at": None,
        "source": "mock",
    }

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
    "deadline_check":    "Deadline check",
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
    ("infer which subject categories",                            "infer_subjects"),
    ("Rank the best 10-12 matches",                               "ranking"),
    ("find real, current",                                        "venue_search"),
    ("maintain a single, coherent running profile",               "profile_synthesis"),
    ("decide whether a student's profile has enough detail",      "profile_readiness"),
    ("exactly THREE distinct",                                    "chat_starters"),
    ("helping a high schooler build a detailed personal profile", "profile_chat"),
    ("distill a casual chat conversation into new facts",         "chat_findings"),
    ("classify and extract structured tracking data",             "tracker_extract"),
    ("extract structured tracking data",                          "tracker_extract"),
    ("extract ONLY information that would be relevant",           "resume_import"),
    # The ranking prompt only contains "Rank the best 10-12 matches" on one of its two
    # selectionRule branches, so it also gets matched on its stable opening line.
    ("helping a student find the best-fit extracurricular",       "ranking"),
    ("interests/goals to the best opportunities",                 "tag_intent"),
    ("Write directly to them in second person",                   "tag_suggestions"),
    ("extracting specific interests, goals, and pursuits",        "infer_subjects"),
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
                existing = _supabase_request("user_costs", params=lookup)
                if existing is None and _user_costs_has_model:
                    # Could be a missing table OR a table that predates the model column —
                    # PostgREST 400s on an unknown filter column either way. Retry without
                    # the model filter before concluding attribution is off entirely.
                    lookup.pop("model")
                    existing = _supabase_request("user_costs", params=lookup)
                    if existing is not None:
                        _user_costs_has_model = False
                        print("[WARN] user_costs has no `model` column - provider/model "
                              "breakout is off. Re-run user_costs_schema.sql in the "
                              "Supabase SQL editor.")
                if existing is None:
                    # None (not []) means the request itself failed — most likely the
                    # table does not exist yet. Stop trying until the server restarts.
                    _user_costs_available = False
                    print("[WARN] user_costs table unavailable - per-user cost attribution "
                          "is off. Run user_costs_schema.sql in the Supabase SQL editor.")
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


# A deliberately permissive shape check — enough to reject junk and to guarantee the
# value is safe to put in a PostgREST `eq.` filter (no commas, quotes, or parens, which
# are that syntax's separators). Not an attempt to validate deliverability; the real
# proof an address works is mail arriving at it.
EMAIL_RE = re.compile(r"^[^\s@,()\x22\x27]+@[^\s@,()\x22\x27]+\.[^\s@,()\x22\x27]{2,}$")


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


def update_user_data(userid, key, value):
    record = get_user(userid)
    if not record:
        return False
    data = record.get("data") or {}
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

VALID_SUBJECTS = ['Mixed','STEM','Medicine','Humanities','Art','Business','Engineering',
                   'Computer Science','Mathematics','Biology','Physics','Astronomy',
                   'Chemistry','Leadership','Law','Logic','Education']
ACTIVE_KINDS = ['summer', 'internship', 'research-competition', 'pure-competition']
MOCK_REASONS = [
    "Strong overlap with the subject and skill focus you described.",
    "Matches the hands-on experience you're looking for.",
    "Good fit for your stated interests and level.",
    "Aligns with the specific project/field you mentioned.",
    "Worth a look given the breadth of your interests.",
]
MOCK_ACTION_ITEMS = [
    {"text": "Request a teacher recommendation letter", "url": None},
    {"text": "Draft your personal statement / essay", "url": None},
    {"text": "Gather transcripts and test scores", "url": None},
    {"text": "Fill out the application form", "url": None},
    {"text": "Prepare a writing sample or portfolio", "url": None},
]


def extract_ids(text):
    ids = re.findall(r'"id"\s*:\s*"([^"]+)"', text)
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def extract_profile_snippet(user_content):
    """Pulls a short snippet of the student's actual description/preferences out of the
    prompt so mock 'why it fits' reasons look grounded in what they wrote, instead of
    generic canned text."""
    m = re.search(r"passion project:\s*(.*?)\s*\n\nCandidate opportunities", user_content, re.S)
    if not m:
        return ''
    words = m.group(1).split()
    return ' '.join(words[:12])


def extract_candidates(user_content):
    m = re.search(r'Candidate opportunities \(JSON\):\s*(\[.*?\])\s*\n\nSelect', user_content, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except Exception:
        return []


def mock_rank_candidates(user_content):
    candidates = extract_candidates(user_content)[:12]
    snippet = extract_profile_snippet(user_content)
    results = []
    for i, c in enumerate(candidates):
        tier = 'strong' if i < 4 else 'look'
        if snippet:
            reason = f"Ties directly to what you wrote about {snippet}"
        else:
            reason = random.choice(MOCK_REASONS)
        results.append({"id": c.get('id'), "reason": reason, "tier": tier})
    if results:
        return json.dumps(results)
    # Fallback for older/unexpected prompt shapes — extract bare ids instead.
    ids = extract_ids(user_content)[:12]
    return json.dumps([
        {"id": cid, "reason": random.choice(MOCK_REASONS), "tier": 'strong' if i < 4 else 'look'}
        for i, cid in enumerate(ids)
    ])


def mock_infer_subjects(user_content):
    lower = user_content.lower()
    matches = [s for s in VALID_SUBJECTS if s.lower() in lower]
    if len(matches) < 2:
        matches = ['STEM', 'Mixed']
    return json.dumps(matches[:5])


def mock_synthesize_profile(user_content):
    m = re.search(r'CURRENT PROFILE:\s*(.*?)\s*NEW INFORMATION TO ADD:\s*(.*?)\s*Respond', user_content, re.S)
    if not m:
        return "(mock) profile updated."
    existing, new = m.group(1).strip(), m.group(2).strip()
    if existing.startswith('(empty'):
        return new
    return f"{existing} {new}".strip()


def mock_assess_profile_readiness():
    return json.dumps({"ready": True, "kinds": ACTIVE_KINDS})


MOCK_CHAT_QUESTIONS = [
    "If your extracurriculars had a theme song, what would it be — and why does that fit you?",
    "What's something you're weirdly good at that has nothing to do with school?",
    "Do you play any music, sport, or game seriously enough that people would be surprised how much time you put into it?",
    "If you had one free Saturday with zero obligations, what would you actually do with it?",
    "What's a small thing you've built, organized, or led that you're quietly proud of?",
]


def mock_profile_chat_starters():
    # random.sample (not a fixed [:3] slice) so clicking "Regenerate" in MOCK mode still
    # visibly swaps in a different trio instead of returning the exact same 3 every time.
    return json.dumps(random.sample(MOCK_CHAT_QUESTIONS, 3))


def mock_profile_chat_question(user_content):
    # Mock mode: cycle through a fixed bank of questions based on how long the
    # conversation-so-far is, so repeated turns don't just repeat the same question.
    m = re.search(r'CONVERSATION SO FAR:\s*(.*?)\s*Respond', user_content, re.S)
    convo = m.group(1).strip() if m else ''
    turns = 0 if convo in ('', '(nothing yet)') else convo.count('\n') + 1
    return MOCK_CHAT_QUESTIONS[turns % len(MOCK_CHAT_QUESTIONS)]


def mock_profile_chat_findings(user_content):
    m = re.search(r'CONVERSATION:\s*(.*?)\s*Respond', user_content, re.S)
    convo = m.group(1).strip() if m else ''
    lines = [l.split(':', 1)[1].strip() for l in convo.split('\n') if l.lower().startswith('student:')]
    if not lines:
        return "(mock) no new details shared."
    return "Additional details shared in chat: " + "; ".join(lines)


def mock_venues_via_web():
    next_deadline = (datetime.date.today() + datetime.timedelta(days=75)).isoformat()
    return json.dumps([
        {
            "name": "Mock Student Research Symposium 2026",
            "url": "https://example.org/symposium",
            "org": "Example Research Council",
            "summary": "Mock venue — set GEMINI_API_KEY for real, live-searched results.",
            "reason": "Placeholder result generated without live web access.",
            "tier": "strong",
            "next_deadline_iso": next_deadline,
            "was_estimated": True,
        }
    ])


SECTION_KEYWORDS = [
    ('conferences', ['conference', 'workshop', 'symposium']),
    ('journals', ['journal', 'publish', 'manuscript']),
    ('researchCompetitions', ['science fair', 'research competition', 'project competition', 'app challenge', 'hackathon']),
    ('pureCompetitions', ['olympiad', 'quiz', 'exam', 'competition']),
    ('internships', ['internship', 'intern', 'lab position', 'mentored']),
    ('summerPrograms', ['summer', 'camp', 'program', 'academy']),
]


def guess_section(text):
    lower = (text or '').lower()
    for section, keywords in SECTION_KEYWORDS:
        if any(k in lower for k in keywords):
            return section
    return 'summerPrograms'


def parse_opp_fields(user_content):
    """Pulls the real opportunity name/org/url/summary back out of the prompt
    the client sent, so mock responses reflect the actual item being tracked
    instead of generic filler text."""
    m = re.search(r'Opportunity:\s*(.+?)\s*\((.+?)\)\s*\nURL:\s*(\S+)\s*\nKnown info:\s*(.*?)(?:\n\n|$)', user_content, re.S)
    if m:
        return {"name": m.group(1).strip(), "org": m.group(2).strip(), "url": m.group(3).strip(), "summary": m.group(4).strip()}
    m2 = re.search(r'URL:\s*(\S+)', user_content)
    notes_m = re.search(r'Extra context:\s*(.*?)\n', user_content, re.S)
    return {
        "name": None,
        "org": None,
        "url": m2.group(1).strip() if m2 else '',
        "summary": notes_m.group(1).strip() if notes_m else '',
    }


def mock_deadline_iso(seed):
    days_out = 20 + (abs(hash(seed)) % 100)  # spread across ~3 months so Home/Calendar have data to show
    return (datetime.date.today() + datetime.timedelta(days=days_out)).isoformat()


def mock_tracker_extract(user_content, with_section):
    fields = parse_opp_fields(user_content)
    name = fields["name"] or "This opportunity"
    org = fields["org"]
    url = fields["url"] or "#"
    summary = fields["summary"]
    deadline_iso = mock_deadline_iso(name + url)
    meta_bits = [b for b in [org, "Mock data · set GEMINI_API_KEY for live details"] if b]
    fit = (summary[:140] + "…") if len(summary) > 140 else summary
    obj = {
        "status": "running",
        "meta": " · ".join(meta_bits),
        "fit": fit or f"Placeholder fit summary for {name} — set GEMINI_API_KEY for a real one.",
        "note": "Mock data for local testing — set GEMINI_API_KEY for real, live-searched details.",
        "noteType": "plain",
        "important_dates": [{"label": "Application Deadline", "date_iso": deadline_iso, "type": "deadline"}],
        "deadline_label": "TBA",
        "was_estimated": True,
        "requirements": [],
        "apply_url": url,
        "apply_label": "Apply now",
        "calendar_events": [{"date": deadline_iso, "text": "Deadline", "type": "deadline"}],
        "action_items": random.sample(MOCK_ACTION_ITEMS, 3),
    }
    if with_section:
        obj["section"] = guess_section(name + ' ' + summary)
        obj["category"] = "Mock category"
    return json.dumps(obj)


def generate_mock_text(system, user_content):
    if "infer which subject categories" in system:
        return mock_infer_subjects(user_content)
    if "Rank the best 10-12 matches" in system:
        return mock_rank_candidates(user_content)
    if "find real, current" in system:
        return mock_venues_via_web()
    if "maintain a single, coherent running profile" in system:
        return mock_synthesize_profile(user_content)
    if "decide whether a student's profile has enough detail" in system:
        return mock_assess_profile_readiness()
    if "exactly THREE distinct" in system:
        return mock_profile_chat_starters()
    if "helping a high schooler build a detailed personal profile" in system:
        return mock_profile_chat_question(user_content)
    if "distill a casual chat conversation into new facts" in system:
        return mock_profile_chat_findings(user_content)
    if "classify and extract structured tracking data" in system:
        return mock_tracker_extract(user_content, with_section=True)
    if "extract structured tracking data" in system:
        return mock_tracker_extract(user_content, with_section=False)
    return json.dumps({})


def fetch_opportunities():
    """Returns the cached opportunities list, refreshing from Supabase if the
    TTL has expired. Raises on the first-ever fetch failure (nothing to serve
    yet); a stale cache is served on subsequent failures rather than erroring."""
    with _opportunities_cache_lock:
        age = time.time() - _opportunities_cache["fetched_at"]
        if _opportunities_cache["data"] is not None and age < OPPORTUNITIES_CACHE_TTL:
            return _opportunities_cache["data"]

        query = urllib.parse.urlencode({
            "select": OPPORTUNITIES_FIELDS,
            "is_active": "eq.true",
            "order": "id",
        })
        page_size = 1000  # PostgREST's default max-rows cap — paginate past it via Range
        try:
            data = []
            offset = 0
            while True:
                req = urllib.request.Request(
                    f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
                    headers={
                        "apikey": SUPABASE_ANON_KEY,
                        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                        "Range": f"{offset}-{offset + page_size - 1}",
                    },
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    page = json.loads(resp.read())
                data.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size
        except Exception:
            if _opportunities_cache["data"] is not None:
                return _opportunities_cache["data"]  # serve stale on transient failure
            raise

        _opportunities_cache["data"] = data
        _opportunities_cache["fetched_at"] = time.time()
        return data


DEADLINE_PATH_RE = re.compile(r"^/api/opportunities/([^/]+)/deadline$")
SEED_PATH_RE = re.compile(r"^/api/seeds/(\d+)$")
AGENT_SETTINGS_PATH_RE = re.compile(r"^/api/agents/settings/([a-z_]+)$")

# ---------- Agent execution tracking ----------
# check_reviews.py/scrape_opportunities.py/check_deadlines.py each write a row to a
# Supabase `agent_runs` table (insert at start, patch with items/cost/errors at finish
# — see each script's own docstring for the exact CREATE TABLE). That table is the
# authoritative source for run history and cost/items data: it's accurate even for
# runs triggered outside this console (cron, manual CLI), and survives server restarts,
# unlike a plain in-memory dict. server.py only tracks "is a subprocess currently
# running right now" itself (_agent_process), since that's process-local state the DB
# has no way to know; everything else (items processed, cost, last result) is read
# straight from agent_runs after each run completes.
_agent_process = {}  # {agent_name: subprocess.Popen or None}
_agent_runs_lock = threading.Lock()
_agent_runs = {}  # {agent_name: {status: "running"|"idle", started_at}} — live-run flag only

# The four agents, keyed by the identifier the admin console uses. `db_agent` is the
# literal each script writes into agent_runs.agent — renaming one here without also
# changing the script silently detaches a card from its own history.
#
# `unit` matters: the scraper's items_processed counts SEEDS (search angles), not
# opportunity rows, so its numbers must never be summed with the other three agents'.
# `writes` distinguishes agents that PATCH existing rows from the scraper, which only
# INSERTs new ones (items_updated vs items_added in agent_runs).
# `uses_gemini_search` marks the agents that contend on gemini_common's
# .gemini_web_search.lock — only one of them can run at a time.
# `defaults` are the timing values the console prefills and the server passes through
# as --min-delay / --timeout; see gemini_common._enforce_rate_limit.
#
# `name` is the FRONT-END LABEL only — it's free to be friendlier than the script name and
# is never matched against anything. The identifiers that must not drift are the dict key
# (used by the console's API calls) and `db_agent` (written by the script itself).
#
# DICT ORDER IS THE UI ORDER. The console iterates this map as-is, so the sequence here
# drives the agent cards, the chart legend and stacking, the timing table, and the history
# filter — changing it here changes all of them together. Ordered by how often you reach
# for them: find new opportunities, then update existing ones, then the periodic checks.
AGENT_CONFIGS_SCHEMA = {
    "scraper": {
        "name": "New Opportunity Scout",
        "description": "Search for new opportunities missing from the catalog",
        "script": "scrape_opportunities.py",
        "db_agent": "scraper",
        "unit": "seeds",
        "writes": "inserts",
        "uses_gemini_search": True,
        "api": "Gemini 3.6-flash + googleSearch",
        "defaults": {"min_delay": 5, "timeout": 280},
    },
    "metadata": {
        "name": "Update Opportunity",
        "description": "Refresh name, org, eligibility, pricing and other core fields",
        "script": "refresh_opportunities.py",
        "db_agent": "metadata_refresher",
        "unit": "rows",
        "writes": "updates",
        "uses_gemini_search": False,
        "api": "Gemini 3.5-flash-lite (no web search)",
        "defaults": {"min_delay": 5, "timeout": 120},
    },
    "deadline": {
        "name": "Deadline Checker",
        "description": "Check and update application deadlines and program status",
        "script": "check_deadlines.py",
        "db_agent": "deadline_checker",
        "unit": "rows",
        "writes": "updates",
        "uses_gemini_search": False,  # uses Claude's web_search, a separate quota
        "api": "Claude Haiku + web_search",
        "defaults": {"min_delay": 5, "timeout": 120},
    },
    "reviews": {
        "name": "Review Checker",
        "description": "Verify org legitimacy and reputation from independent sources",
        "script": "check_reviews.py",
        "db_agent": "review_checker",
        "unit": "rows",
        "writes": "updates",
        "uses_gemini_search": True,
        "api": "Gemini 3.6-flash + googleSearch",
        "defaults": {"min_delay": 5, "timeout": 120},
    },
}

# ---------- Editable timing overrides ----------
# AGENT_CONFIGS_SCHEMA's `defaults` are the built-in values. Anything you change from the
# console's timing table is stored here as an OVERRIDE, so the shipped defaults stay visible
# (and restorable) rather than being edited away.
#
# A local JSON file rather than a Supabase table on purpose: these are operational knobs for
# whichever machine actually runs the agents, they must be readable before any network call,
# and a wrong value here should never be something you have to reach the database to undo.
AGENT_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_settings.json")
_settings_lock = threading.Lock()

# Bounds are deliberately permissive but not unbounded: 0 delay is a legitimate (if risky)
# choice for a 2-row test, while a 1-second timeout would just burn money on requests whose
# answers are discarded.
SETTING_BOUNDS = {"min_delay": (0, 300), "timeout": (10, 1800)}
RECOMMENDED_MIN_DELAY = 5  # the value that resolved this pipeline's HTTP 429s


def load_agent_settings():
    """Read saved timing overrides. Missing or corrupt file falls back to no overrides —
    the built-in defaults must always be reachable."""
    try:
        with open(AGENT_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[WARN] Ignoring unreadable {AGENT_SETTINGS_PATH}: {e}")
        return {}


def agent_defaults(agent_name):
    """Effective timing for an agent: built-in defaults with any saved override applied.

    Everything that needs a delay or timeout goes through this — the config endpoint, the
    argv builder and the duration estimator — so an edit takes effect everywhere at once
    instead of only in the field the console happens to prefill.
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name, {})
    merged = dict(cfg.get("defaults") or {})
    override = (load_agent_settings().get(agent_name) or {})
    for key in SETTING_BOUNDS:
        if override.get(key) is not None:
            merged[key] = override[key]
    return merged


def save_agent_settings(agent_name, patch):
    """Persist timing overrides for one agent. Returns (settings, error).

    A value equal to the built-in default is stored as None (i.e. cleared), so 'reset to
    default' and 'happens to match the default' converge on the same state.
    """
    if agent_name not in AGENT_CONFIGS_SCHEMA:
        return None, f"Unknown agent: {agent_name}"
    builtin = AGENT_CONFIGS_SCHEMA[agent_name].get("defaults") or {}
    clean = {}
    for key, (low, high) in SETTING_BOUNDS.items():
        if key not in patch:
            continue
        raw = patch[key]
        if raw in (None, ""):
            clean[key] = None            # explicit reset
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None, f"{key} must be a number"
        if not (low <= value <= high):
            return None, f"{key} must be between {low} and {high}"
        if key == "timeout":
            value = int(value)
        clean[key] = None if value == builtin.get(key) else value

    if not clean:
        return None, "Nothing to update"

    with _settings_lock:
        settings = load_agent_settings()
        entry = dict(settings.get(agent_name) or {})
        entry.update(clean)
        entry = {k: v for k, v in entry.items() if v is not None}
        if entry:
            settings[agent_name] = entry
        else:
            settings.pop(agent_name, None)
        try:
            with open(AGENT_SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
        except Exception as e:
            return None, f"Could not write settings: {e}"
    return agent_defaults(agent_name), None


def agents_config_payload():
    """The agent registry as the console consumes it: static metadata plus EFFECTIVE
    timing, with the built-in values alongside so the UI can show what's been changed."""
    out = {}
    for key, cfg in AGENT_CONFIGS_SCHEMA.items():
        entry = {k: v for k, v in cfg.items() if k != "defaults"}
        entry["defaults"] = agent_defaults(key)
        entry["builtin_defaults"] = cfg.get("defaults") or {}
        out[key] = entry
    return out


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


def fetch_agent_runs(db_agent=None, limit=20, since_iso=None):
    """GET recent rows from the agent_runs table, newest first.

    Filters to one agent if db_agent is given, and to runs started at/after since_iso if
    given (how the dashboard scopes its charts to a time range).
    """
    params = {"select": "*", "order": "started_at.desc", "limit": str(limit)}
    if db_agent:
        params["agent"] = f"eq.{db_agent}"
    if since_iso:
        params["started_at"] = f"gte.{since_iso}"
    return _supabase_request("agent_runs", params=params) or []


def _parse_iso(value):
    """Parse a Supabase timestamptz, tolerating the trailing Z form. None on anything else."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _agent_key_for(db_agent):
    """Map an agent_runs.agent literal back to its console key.

    Falls back to the raw literal so runs from scripts with no console card (e.g.
    subject_tags_backfill) still appear in the activity log rather than vanishing.
    """
    for key, cfg in AGENT_CONFIGS_SCHEMA.items():
        if cfg["db_agent"] == db_agent:
            return key
    return db_agent


def _run_status(row):
    """Derive a run's status from its agent_runs row.

    'interrupted' matters as its own state: a row with started_at but no finished_at that
    is older than the subprocess timeout means the script died mid-pass without ever
    patching its totals, so its cost and item counts are understated rather than absent.
    Folding that into 'failed' would hide the difference between "ran and reported errors"
    and "vanished halfway through".
    """
    if row.get("errors"):
        return "failed"
    if row.get("finished_at"):
        return "success"
    started = _parse_iso(row.get("started_at"))
    if not started:
        return "interrupted"
    age = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
    return "running" if age < AGENT_RUN_TIMEOUT_SECS else "interrupted"


def _shape_run(row):
    """Project one agent_runs row into the shape the console consumes.

    items_added is included deliberately: it is the scraper's "rows created" count and was
    dropped entirely by the previous version of this endpoint, which is why new-row counts
    never showed up anywhere in the UI.
    """
    key = _agent_key_for(row.get("agent"))
    cfg = AGENT_CONFIGS_SCHEMA.get(key, {})
    if not cfg and key in INTERACTIVE_AGENTS:
        cfg = {"name": INTERACTIVE_AGENTS[key], "unit": "calls"}
    started, finished = _parse_iso(row.get("started_at")), _parse_iso(row.get("finished_at"))
    duration = round((finished - started).total_seconds(), 1) if started and finished else None
    return {
        "id": row.get("id"),
        "agent": key,
        "name": cfg.get("name", row.get("agent")),
        "known_agent": key in AGENT_CONFIGS_SCHEMA,
        "unit": cfg.get("unit", "items"),
        "status": _run_status(row),
        "mode": row.get("mode"),
        # Dry runs are marked by a "-dryrun" mode suffix rather than a dedicated column,
        # so no agent_runs schema change was needed to start logging them.
        "dry_run": str(row.get("mode") or "").endswith("-dryrun"),
        "interactive": row.get("agent") in INTERACTIVE_AGENTS,
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "duration_seconds": duration,
        "items_processed": row.get("items_processed") or 0,
        "items_updated": row.get("items_updated") or 0,
        "items_added": row.get("items_added") or 0,
        "errors": row.get("errors") or 0,
        "cost_usd": row.get("cost_usd"),
        "total_web_searches": row.get("total_web_searches"),
        "silent_search_count": row.get("silent_search_count"),
        "notes": row.get("notes"),
    }


# Recent-runs cache. get_agent_status() used to issue one Supabase round-trip PER AGENT on
# every poll; with four agents and a 3s dashboard poll that was over a request a second,
# forever, for data that changes at most once per run. One query now covers every agent and
# is reused for a few seconds.
_runs_cache = {"at": 0.0, "rows": []}
_runs_cache_lock = threading.Lock()
RUNS_CACHE_TTL = 5
RECENT_RUNS_LIMIT = 200


def recent_runs(force=False):
    """Recent agent_runs rows across all agents, cached for RUNS_CACHE_TTL seconds."""
    with _runs_cache_lock:
        fresh = (time.time() - _runs_cache["at"]) < RUNS_CACHE_TTL
        if fresh and not force and _runs_cache["rows"]:
            return _runs_cache["rows"]
    rows = fetch_agent_runs(limit=RECENT_RUNS_LIMIT)
    with _runs_cache_lock:
        _runs_cache["at"] = time.time()
        _runs_cache["rows"] = rows
    return rows


def invalidate_runs_cache():
    with _runs_cache_lock:
        _runs_cache["at"] = 0.0


def get_agent_history(agent=None, limit=50, days=None):
    """Recent runs for the activity log, newest first — straight from agent_runs."""
    rows = recent_runs()
    if days:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        rows = [r for r in rows if (_parse_iso(r.get("started_at")) or cutoff) >= cutoff]
    shaped = [_shape_run(r) for r in rows]
    if agent:
        shaped = [r for r in shaped if r["agent"] == agent]
    return shaped[:limit]


import dryrun_common

USER_COSTS_SETUP_SQL = "user_costs_schema.sql"
PLAN_PRICE_USD = 9.99  # mirrors subscription_common.PLAN_PRICE_CENTS


def get_user_costs(days=30, limit=200):
    """Per-user breakdown of interactive spend, for the console's cost-per-user card.

    Reads user_costs (the attribution table) and reconciles it against the SAME window's
    interactive total, which comes from agent_runs' interactive_* rollups plus
    deadline_check_log. attributed + unattributed == that total by construction; the
    residual is spend from calls that arrived with no userid, and is reported rather than
    quietly absorbed into somebody's row.

    Returns {"table_ready": False} rather than raising when the migration has not been
    applied, so the console can show the setup step instead of an error.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff_day = (now - datetime.timedelta(days=days)).date()

    base_select = ("userid,day,surface,feature,calls,input_tokens,output_tokens,"
                   "web_searches,cost_usd,first_at,last_at")
    params = {
        "select": base_select + ",model",
        "day": f"gte.{cutoff_day.isoformat()}",
        "order": "day.desc",
        "limit": "20000",
    }
    rows = _supabase_request("user_costs", params=params)
    model_ready = True
    if rows is None:
        # Selecting a column PostgREST doesn't know 400s exactly like a missing table does,
        # so a half-migrated database is indistinguishable until the narrower select is
        # tried. Falling straight through to "table not ready" would hide every already
        # attributed dollar behind a migration that has, in fact, already been run.
        params["select"] = base_select
        rows = _supabase_request("user_costs", params=params)
        model_ready = False
    if rows is None:
        return {"table_ready": False, "model_ready": False,
                "setup_sql_file": USER_COSTS_SETUP_SQL, "days": days,
                "price_per_month_usd": PLAN_PRICE_USD, "users": [], "features": [],
                "providers": [], "models": [], "series": [], "totals": {}}

    # --- fold the rollup rows into one entry per user ---
    users = {}
    features = {}
    providers = {}
    models = {}
    per_day = {}
    for r in rows:
        uid = r.get("userid") or "(unknown)"
        cost = float(r.get("cost_usd") or 0)
        feat = r.get("feature") or "other"
        model = (r.get("model") or "").strip()
        provider = provider_for_model(model, r.get("surface"))
        u = users.setdefault(uid, {
            "userid": uid, "calls": 0, "web_searches": 0, "cost_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0,
            "features": {}, "surfaces": {}, "providers": {}, "models": {},
            "first_at": None, "last_at": None,
        })
        u["calls"] += int(r.get("calls") or 0)
        u["web_searches"] += int(r.get("web_searches") or 0)
        u["input_tokens"] += int(r.get("input_tokens") or 0)
        u["output_tokens"] += int(r.get("output_tokens") or 0)
        u["cost_usd"] += cost
        u["features"][feat] = round(u["features"].get(feat, 0.0) + cost, 6)
        surf = r.get("surface") or "gemini"
        u["surfaces"][surf] = round(u["surfaces"].get(surf, 0.0) + cost, 6)
        u["providers"][provider] = round(u["providers"].get(provider, 0.0) + cost, 6)
        # Keyed by "provider/model" so two vendors can never collide on a shared model
        # name, and so a model entry is self-describing wherever it is rendered.
        mkey = provider + "/" + model if model else provider
        mrow = u["models"].setdefault(mkey, {"provider": provider, "model": model,
                                             "cost_usd": 0.0, "calls": 0})
        mrow["cost_usd"] = round(mrow["cost_usd"] + cost, 6)
        mrow["calls"] += int(r.get("calls") or 0)

        pr = providers.setdefault(provider, {
            "key": provider, "label": PROVIDER_LABELS.get(provider, provider),
            "cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "web_searches": 0, "users": set(), "models": set()})
        pr["cost_usd"] += cost
        pr["calls"] += int(r.get("calls") or 0)
        pr["input_tokens"] += int(r.get("input_tokens") or 0)
        pr["output_tokens"] += int(r.get("output_tokens") or 0)
        pr["web_searches"] += int(r.get("web_searches") or 0)
        pr["users"].add(uid)
        if model:
            pr["models"].add(model)

        m = models.setdefault(mkey, {
            "key": mkey, "provider": provider,
            "provider_label": PROVIDER_LABELS.get(provider, provider),
            # A blank model is a row written before the breakout existed, not a model
            # literally named "" — say so rather than rendering an empty cell.
            "model": model or "(before model tracking)",
            "cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "web_searches": 0, "users": set(), "features": {}})
        m["cost_usd"] += cost
        m["calls"] += int(r.get("calls") or 0)
        m["input_tokens"] += int(r.get("input_tokens") or 0)
        m["output_tokens"] += int(r.get("output_tokens") or 0)
        m["web_searches"] += int(r.get("web_searches") or 0)
        m["users"].add(uid)
        m["features"][feat] = round(m["features"].get(feat, 0.0) + cost, 6)
        for field in ("first_at", "last_at"):
            v = r.get(field)
            if not v:
                continue
            cur = u[field]
            if cur is None or (v < cur if field == "first_at" else v > cur):
                u[field] = v

        f = features.setdefault(feat, {"key": feat, "label": FEATURE_LABELS.get(feat, feat),
                                       "cost_usd": 0.0, "calls": 0, "users": set()})
        f["cost_usd"] += cost
        f["calls"] += int(r.get("calls") or 0)
        f["users"].add(uid)

        day = r.get("day")
        if day:
            d = per_day.setdefault(day, {"cost_usd": 0.0, "users": set(), "calls": 0})
            d["cost_usd"] += cost
            d["users"].add(uid)
            d["calls"] += int(r.get("calls") or 0)

    # --- decorate with account data: a cost figure only means something next to the plan ---
    try:
        accounts = _users_request("GET", "?" + urllib.parse.urlencode({
            "select": "userid,first_name,last_name,email,subscription_status,trial_ends_at,"
                      "subscription_end_at",
            "limit": "5000",
        })) or []
    except Exception as e:
        # The spend numbers are the point; losing the name/plan decoration degrades the
        # card rather than failing it.
        print(f"[WARN] Could not load accounts for per-user cost card: {e}")
        accounts = []
    by_id = {str(a.get("userid") or "").lower(): a for a in accounts}
    for uid, u in users.items():
        a = by_id.get(uid) or {}
        name = " ".join(x for x in [a.get("first_name"), a.get("last_name")] if x).strip()
        u["name"] = name or None
        u["email"] = a.get("email")
        u["known_account"] = bool(a)
        u["subscription_status"] = a.get("subscription_status") or ("unknown" if a else "no account")
        u["trial_ends_at"] = a.get("trial_ends_at")
        u["cost_usd"] = round(u["cost_usd"], 6)
        # What this user's usage costs measured against one month of the plan. Over 100%
        # means the account loses money on inference alone, before any other cost.
        u["pct_of_plan"] = round(u["cost_usd"] / PLAN_PRICE_USD, 4) if PLAN_PRICE_USD else None
        u["margin_usd"] = round(PLAN_PRICE_USD - u["cost_usd"], 4)
        u["cost_per_call"] = round(u["cost_usd"] / u["calls"], 6) if u["calls"] else 0.0
        # Dict -> cost-sorted list at the edge, so the console renders in cost order
        # without re-sorting an object whose key order it should not have to trust.
        u["models"] = sorted(u["models"].values(), key=lambda mm: mm["cost_usd"],
                             reverse=True)

    ranked = sorted(users.values(), key=lambda x: x["cost_usd"], reverse=True)
    truncated = max(0, len(ranked) - limit)
    ranked = ranked[:limit]

    # --- reconcile against the window's interactive total ---
    cutoff = now - datetime.timedelta(days=days)
    interactive_total = 0.0
    for r in recent_runs():
        if r.get("agent") not in INTERACTIVE_AGENTS:
            continue
        started = _parse_iso(r.get("started_at"))
        if started and started >= cutoff:
            interactive_total += float(r.get("cost_usd") or 0)
    interactive_total += float(fetch_deadline_check_cost(cutoff, now).get("cost_usd") or 0)

    attributed = round(sum(u["cost_usd"] for u in users.values()), 6)
    # Clamped at zero: the two sources are written by different code paths and a rounding
    # or in-flight difference must not surface as a negative "unattributed" figure.
    unattributed = round(max(0.0, interactive_total - attributed), 6)

    series = []
    for i in range(days):
        day = (cutoff + datetime.timedelta(days=i)).date().isoformat()
        d = per_day.get(day)
        series.append({"date": day,
                       "cost_usd": round(d["cost_usd"], 6) if d else 0.0,
                       "users": len(d["users"]) if d else 0,
                       "calls": d["calls"] if d else 0})

    feature_list = sorted(
        ({"key": f["key"], "label": f["label"], "cost_usd": round(f["cost_usd"], 6),
          "calls": f["calls"], "users": len(f["users"])} for f in features.values()),
        key=lambda f: f["cost_usd"], reverse=True)

    def _shape(entry, **extra):
        """Common tail for a provider/model aggregate: round, count users, add a rate."""
        out = {k: v for k, v in entry.items() if k not in ("users", "models", "features")}
        out["cost_usd"] = round(entry["cost_usd"], 6)
        out["users"] = len(entry["users"])
        out["cost_per_call"] = (round(entry["cost_usd"] / entry["calls"], 6)
                                if entry["calls"] else 0.0)
        out.update(extra)
        return out

    provider_list = sorted(
        (_shape(pr, models=sorted(pr["models"])) for pr in providers.values()),
        key=lambda pr: pr["cost_usd"], reverse=True)
    model_list = sorted(
        (_shape(m, features=sorted(
            ({"key": k, "label": FEATURE_LABELS.get(k, k), "cost_usd": v}
             for k, v in m["features"].items()),
            key=lambda f: f["cost_usd"], reverse=True)) for m in models.values()),
        key=lambda m: m["cost_usd"], reverse=True)

    costs = [u["cost_usd"] for u in users.values()]
    return {
        "table_ready": True,
        # False means the table exists but predates the provider/model breakout. Every
        # figure is still correct; the model just reads as "(before model tracking)"
        # until user_costs_schema.sql is re-run.
        "model_ready": model_ready,
        "setup_sql_file": USER_COSTS_SETUP_SQL,
        "providers": provider_list,
        "models": model_list,
        "days": days,
        "price_per_month_usd": PLAN_PRICE_USD,
        "users": ranked,
        "users_truncated": truncated,
        "features": feature_list,
        "series": series,
        "totals": {
            "attributed_cost_usd": attributed,
            "unattributed_cost_usd": unattributed,
            "interactive_total_usd": round(interactive_total, 6),
            "attribution_rate": round(attributed / interactive_total, 4) if interactive_total else 0,
            "active_users": len(users),
            "calls": sum(u["calls"] for u in users.values()),
            "web_searches": sum(u["web_searches"] for u in users.values()),
            "avg_cost_per_user": round(attributed / len(users), 6) if users else 0.0,
            "max_cost_per_user": round(max(costs), 6) if costs else 0.0,
            "users_over_plan": sum(1 for c in costs if c > PLAN_PRICE_USD),
        },
    }


# ---------- Committing a dry-run snapshot, and activating what the scraper found ----------
# Two operations that write to the catalog but call no API and cost nothing. They are the
# other half of the three run tiers: --dry-run already pays full price for an answer, and
# a scrape already writes its rows inactive, so both of those leave work sitting on disk
# or behind a flag with no way to act on it short of paying again.

def _commit_patch(opp_id, updates):
    """PATCH one opportunity, raising on failure so commit_snapshot can count it."""
    query = urllib.parse.urlencode({"id": f"eq.{opp_id}"})
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities?{query}",
        data=json.dumps(updates).encode(), method="PATCH",
        headers={**_supabase_headers(), "Content-Type": "application/json",
                 "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def _commit_insert(rows):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/opportunities",
        data=json.dumps(rows).encode(), method="POST",
        headers={**_supabase_headers(), "Content-Type": "application/json",
                 "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def _existing_opportunity_urls():
    """Every URL already in the table, normalized — the dedupe set for snapshot inserts.

    Reads ALL rows, not just active ones: a row sitting inactive in the review queue is
    still a row, and re-inserting it would put a duplicate in front of the reviewer.
    """
    urls = set()
    offset, page_size = 0, 1000
    while True:
        page = _supabase_request("opportunities", params={
            "select": "url", "limit": str(page_size), "offset": str(offset)}) or []
        urls.update(dryrun_common.normalize_url(r.get("url")) for r in page if r.get("url"))
        if len(page) < page_size:
            break
        offset += page_size
    return urls


def commit_dryrun_snapshot(file_name, dry=False):
    """Apply a snapshot's pending writes. Free — no API call happens anywhere in here.

    A real commit is logged to agent_runs with a `-commit` mode suffix and cost_usd 0. That
    pairs correctly with the `-dryrun` row the original run already wrote: the dry run
    carries the cost and no row counts, the commit carries the row counts and no cost, and
    between them they describe one logical operation without double-counting either.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}
    result = dryrun_common.commit_snapshot(
        file_name, _commit_patch, _commit_insert, _existing_opportunity_urls, dry=dry)
    if dry or not result.get("ok") or not result.get("applied"):
        return result

    agent = result["agent"]
    db_agent = AGENT_CONFIGS_SCHEMA.get(agent, {}).get("db_agent", agent)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    inserted = result["kind"] == "insert"
    _supabase_request("agent_runs", method="POST", data=[{
        "agent": db_agent,
        "mode": "snapshot-commit",
        "started_at": now, "finished_at": now,
        "items_processed": result["entries"],
        "items_updated": 0 if inserted else result["applied"],
        "items_added": result["applied"] if inserted else 0,
        "errors": result["errors"], "cost_usd": 0,
        "total_web_searches": 0,
        "notes": f"Committed dry-run snapshot {file_name} — no API calls, cost already "
                 f"paid by the original run.",
    }])
    invalidate_runs_cache()
    # Committed rows must not wait out OPPORTUNITIES_CACHE_TTL before they are visible.
    with _opportunities_cache_lock:
        _opportunities_cache["fetched_at"] = 0.0
    return result


# The columns user_submissions_schema.sql adds. Selected separately from the base row so a
# database that has not run that migration still gets a working queue (see the ladder in
# list_pending_opportunities) rather than a blank one — PostgREST 400s the WHOLE select on
# one unknown column, so a single wide select would take the queue down entirely.
# Carries every EDITABLE_OPPORTUNITY_FIELDS column so the console's edit modal can prefill
# from the list it already has, without a per-row round-trip.
_BASE_PENDING_SELECT = ("id,name,org,type,url,summary,source,created_at,review_status,state,"
                        "price,category,cost,location,intl,season,eligibility,grade_min,"
                        "grade_max,subject_tags")
_MODERATION_SELECT = ("moderation_status,submitted_by,submitted_at,reviewed_by,reviewed_at,"
                      "duplicate_of,dup_candidates,quality_flags")

# A human's verdict on a queued row. Mirrors the CHECK constraint in
# user_submissions_schema.sql — keep the two in step or a write here 400s.
MODERATION_STATUSES = ("pending_review", "approved", "rejected", "duplicate")

# Statuses that mean "a human has already dealt with this", i.e. the rows the queue hides.
ADJUDICATED_STATUSES = ("rejected", "duplicate")


def _is_missing_column_error(exc):
    """True when PostgREST rejected a call because a migration has not been run.

    A read reports Postgres's own 42703, a write reports PGRST204 from the schema cache;
    both mean the same thing, and both are worth telling the operator apart from a real
    failure so they get pointed at the .sql file instead of at a bug.
    """
    if not isinstance(exc, urllib.error.HTTPError):
        return False
    return (_error_body(exc) or {}).get("code") in _MISSING_COLUMN_CODES


def list_pending_opportunities(limit=500, source=None, status="queue"):
    """Rows sitting at is_active=false, i.e. the review queue.

    Nothing in this repo ever flips is_active automatically — a scrape and a user
    submission both write inactive and stay inactive until a person acts here. That is
    deliberate: the scraper can and does return plausible-looking rows that are wrong, and
    the catalog is what students see.

    `status` selects which slice of the queue:
      queue     — awaiting a human (moderation_status NULL, pending_review, or approved).
                  NULL is the pre-migration/scraper case and means "never adjudicated",
                  NOT "rejected", so it must stay in the queue.
      rejected  — already adjudicated away (rejected/duplicate). Kept reachable so a
                  mistaken rejection can be put back without a database console.
      all       — everything inactive, which is what the pre-moderation queue showed.
    """
    base = {
        "is_active": "eq.false",
        "order": "created_at.desc,id.desc",
        "limit": str(max(1, min(limit, 2000))),
    }
    if source:
        base["source"] = f"eq.{source}"

    filters = {}
    if status == "queue":
        # NOT IN would drop the NULL rows too — in SQL, NULL NOT IN (...) is NULL, not
        # true. Every scraper row has a NULL moderation_status, so that would empty the
        # queue outright. Spell the null case out.
        filters["or"] = ("(moderation_status.is.null,moderation_status.in."
                         f"({','.join(s for s in MODERATION_STATUSES if s not in ADJUDICATED_STATUSES)}))")
    elif status == "rejected":
        filters["moderation_status"] = f"in.({','.join(ADJUDICATED_STATUSES)})"

    # Degrade one step rather than failing: without the migration there is no
    # moderation_status to select or filter on, and a queue with no reject button beats
    # no queue at all.
    attempts = [
        (dict(base, select=f"{_BASE_PENDING_SELECT},{_MODERATION_SELECT}", **filters), True),
        (dict(base, select=_BASE_PENDING_SELECT), False),
    ]
    rows, moderation_ready = None, False
    for params, ready in attempts:
        rows = _supabase_request("opportunities", params=params)
        if rows is not None:
            moderation_ready = ready
            break
    if rows is None:
        return {"ok": False, "error": "Could not read opportunities from Supabase."}
    if not moderation_ready and status == "rejected":
        # Nothing can have been rejected yet if the column does not exist.
        rows = []

    sources, statuses = {}, {}
    for r in rows:
        key = r.get("source") or "(no source)"
        sources[key] = sources.get(key, 0) + 1
        skey = r.get("moderation_status") or "(never reviewed)"
        statuses[skey] = statuses.get(skey, 0) + 1
    return {
        "ok": True,
        "total": len(rows),
        "opportunities": rows,
        "status": status,
        # {id: {name,url,is_active}} for the rows marked `duplicate` point at, so the queue
        # can name the survivor rather than showing a bare id.
        "duplicate_targets": _duplicate_targets(rows) if moderation_ready else {},
        # False means user_submissions_schema.sql has not been run: the console shows the
        # setup step and hides the reject controls instead of offering a button that 400s.
        "moderation_ready": moderation_ready,
        "moderation_sql": "user_submissions_schema.sql",
        "statuses": statuses,
        "sources": sorted(({"source": k, "count": v} for k, v in sources.items()),
                          key=lambda x: x["count"], reverse=True),
    }


# The 7 values `type` actually holds across the catalog. It is a clean enum (unlike
# `category`, which carries legacy junk — 'COMPETITION', 'SUMMER_PROGRAM', mixed case — and
# is therefore left as free text here). A typo'd type would make the row invisible to the
# finder's KIND_CONFIG lookup rather than merely ugly, so this one is validated.
OPPORTUNITY_TYPES = ("Program", "Internship", "Competition", "Research", "Volunteer",
                     "Conference", "Journal")

# What a reviewer may fix before a row goes in front of students, and how each value is
# coerced. Deliberately NOT editable here:
#   is_active / moderation_status  — those are the Activate and Reject buttons, which carry
#                                    their own confirmations and cache invalidation.
#   id, source, created_at         — provenance. Editing it would erase where a row came from.
#   review_status/review_summary/review_sources/last_reviewed_at — check_reviews.py owns these.
#   status/important_dates/was_estimated/dates_last_checked_at   — check_deadlines.py owns
#                                    these, and a hand-typed date would be silently
#                                    overwritten by the next deadline check anyway.
EDITABLE_OPPORTUNITY_FIELDS = {
    "name": "text", "org": "text", "summary": "text", "url": "text",
    "type": "text", "category": "text", "price": "text", "cost": "text",
    "state": "text", "location": "text", "intl": "text", "season": "text",
    "eligibility": "text",
    "grade_min": "int", "grade_max": "int",
    "subject_tags": "list",
}


def _coerce_field(key, kind, value):
    """One editable value → what the column wants, or raise ValueError with a message the
    console can show verbatim."""
    if kind == "int":
        if value in (None, "", "—"):
            return None
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number or blank.")
        if not 1 <= n <= 13:
            raise ValueError(f"{key} must be a grade between 1 and 13.")
        return n
    if kind == "list":
        if value in (None, ""):
            return None
        if isinstance(value, str):
            # The console sends a comma-separated string because that is what a text field
            # produces; the column is a Postgres array either way.
            value = [p.strip() for p in value.split(",")]
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list or a comma-separated string.")
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        return cleaned or None
    text = "" if value is None else str(value).strip()
    return text or None


def update_pending_opportunity(opp_id, fields):
    """Edit a queued row in place, before anyone activates it.

    Scoped to inactive rows on purpose. The whole point of the queue is to fix a row while
    it is still hidden — letting this endpoint touch a live row would turn it into a
    general catalog editor with no audit trail and no confirmation, which is not what the
    Activate/Reject buttons around it lead an operator to expect.
    """
    opp_id = str(opp_id or "").strip()
    if not opp_id:
        return {"ok": False, "error": "No opportunity id given."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}

    unknown = [k for k in (fields or {}) if k not in EDITABLE_OPPORTUNITY_FIELDS]
    if unknown:
        # PostgREST would 400 the whole PATCH on an unknown key anyway; say which one.
        return {"ok": False, "error": f"Not editable here: {', '.join(sorted(unknown))}."}

    existing = _supabase_request("opportunities", params={
        "select": "id,is_active,name,url", "id": f"eq.{opp_id}", "limit": "1"})
    if existing is None:
        return {"ok": False, "error": "Could not read that opportunity from Supabase."}
    if not existing:
        return {"ok": False, "error": f"No opportunity with id {opp_id}."}
    if existing[0].get("is_active"):
        return {"ok": False, "error": "That row is live in the catalog. Only rows still in "
                                      "the review queue can be edited here."}

    updates = {}
    try:
        for key, value in (fields or {}).items():
            updates[key] = _coerce_field(key, EDITABLE_OPPORTUNITY_FIELDS[key], value)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    # Name and URL are what the row IS — blanking either leaves an unreviewable stub, and
    # an empty url takes the row out of dedupe entirely.
    for required in ("name", "url"):
        if required in updates and not updates[required]:
            return {"ok": False, "error": f"{required} cannot be empty."}
    if updates.get("type") and updates["type"] not in OPPORTUNITY_TYPES:
        return {"ok": False, "error": f"type must be one of {', '.join(OPPORTUNITY_TYPES)}."}
    if updates.get("url") and not str(updates["url"]).lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http:// or https://."}
    if updates.get("state") and len(updates["state"]) != 2:
        return {"ok": False, "error": "state is the 2-letter US code (or blank)."}
    lo, hi = updates.get("grade_min"), updates.get("grade_max")
    if lo is not None and hi is not None and lo > hi:
        return {"ok": False, "error": "grade_min cannot be above grade_max."}
    if not updates:
        return {"ok": False, "error": "Nothing to change."}

    updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        _commit_patch(opp_id, updates)
    except Exception as e:
        if _is_missing_column_error(e):
            return {"ok": False, "error": f"Supabase rejected a column in {sorted(updates)} "
                                          f"— the schema has drifted from this whitelist."}
        return {"ok": False, "error": f"Update failed: {str(e)[:200]}"}

    row = _supabase_request("opportunities", params={
        "select": f"{_BASE_PENDING_SELECT},{_MODERATION_SELECT}",
        "id": f"eq.{opp_id}", "limit": "1"}) or []
    # The row is inactive, so the public cache cannot be holding it — no invalidation needed.
    return {"ok": True, "id": opp_id, "updated": sorted(k for k in updates if k != "updated_at"),
            "opportunity": row[0] if row else None}


def search_opportunities(q, limit=25):
    """Find a surviving row to point a duplicate at. Whole catalog, active or not.

    Deliberately searches inactive rows too: the survivor of two queued scrapes is another
    queued row, and restricting this to the live catalog would make that pair unresolvable.
    """
    q = str(q or "").strip()
    if len(q) < 2:
        return {"ok": True, "total": 0, "opportunities": [],
                "note": "Type at least two characters."}
    select = "id,name,org,url,type,is_active,source,moderation_status"
    limit = str(max(1, min(int(limit or 25), 100)))

    # An id is the unambiguous case and is what dup_candidates carries, so try it first
    # and exactly — an ilike on a 7-character id would also match by luck.
    exact = _supabase_request("opportunities", params={
        "select": select, "id": f"eq.{q}", "limit": "1"}) or []

    # PostgREST's or=() list is comma-separated and paren-delimited, so a comma or paren
    # inside the term would be parsed as syntax rather than as text. Strip them rather than
    # trying to quote: this is a search box, and a slightly broader match is harmless.
    safe = re.sub(r"[,()*%]", " ", q).strip()
    rows = []
    if safe:
        rows = _supabase_request("opportunities", params={
            "select": select,
            "or": f"(name.ilike.*{safe}*,org.ilike.*{safe}*,url.ilike.*{safe}*)",
            "order": "is_active.desc,name.asc",
            "limit": limit}) or []

    seen, merged = set(), []
    for row in exact + rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        merged.append(row)
    return {"ok": True, "total": len(merged), "opportunities": merged}


def _duplicate_targets(rows):
    """{id: {name, url, is_active}} for every duplicate_of a queue page points at.

    One extra request, and only when something is actually marked duplicate — the console
    otherwise has an id it cannot render as anything a reader recognises.
    """
    ids = sorted({(r.get("duplicate_of") or "").strip() for r in rows
                  if (r.get("duplicate_of") or "").strip()})
    if not ids:
        return {}
    found = _supabase_request("opportunities", params={
        "select": "id,name,url,is_active",
        "id": f"in.({','.join(ids)})", "limit": str(len(ids))}) or []
    return {row["id"]: row for row in found}


def moderate_opportunities(ids, status, reviewed_by="admin-console", duplicate_of=None):
    """Record a human verdict on an explicit list of queued rows.

    This is the counterpart to activate_opportunities: that one says "students should see
    this", this one says "a person looked and decided". They are separate because
    is_active alone cannot express "reviewed and declined" — without this, a junk row sits
    inactive forever and gets re-triaged every time the queue is opened, and the queue only
    ever grows.

    Rejecting never deletes. The row stays in the table on purpose: its URL keeps blocking
    re-submission through url_dedupe, and the decision is reversible by moderating it back
    to pending_review.
    """
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if status not in MODERATION_STATUSES:
        return {"ok": False,
                "error": f"Unknown moderation status: {status!r}. "
                         f"Expected one of {', '.join(MODERATION_STATUSES)}."}
    if not ids:
        return {"ok": False, "error": "No opportunity ids given."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}

    duplicate_of = str(duplicate_of or "").strip()
    if status == "duplicate":
        # A duplicate with no survivor names nothing — it reads as "rejected" with a
        # misleading label, and there is then no row for a reader to follow to.
        if not duplicate_of:
            return {"ok": False, "error": "Marking a row as a duplicate needs the id of the "
                                          "row it duplicates."}
        if duplicate_of in ids:
            return {"ok": False, "error": "A row cannot be a duplicate of itself."}
        target = _supabase_request("opportunities", params={
            "select": "id,name,moderation_status", "id": f"eq.{duplicate_of}", "limit": "1"})
        if target is None:
            return {"ok": False, "error": "Could not check the surviving row in Supabase."}
        if not target:
            return {"ok": False, "error": f"No opportunity with id {duplicate_of} to point at."}
        # Pointing at a row that was itself rejected or duplicated makes a chain the queue
        # has no way to follow, and quietly loses the real survivor.
        if target[0].get("moderation_status") in ADJUDICATED_STATUSES:
            return {"ok": False,
                    "error": f"{duplicate_of} is itself marked "
                             f"{target[0]['moderation_status']} — point at the row that "
                             f"survives, not at another discarded one."}
    elif duplicate_of:
        return {"ok": False, "error": "duplicate_of only applies to the 'duplicate' status."}

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    updates = {
        "moderation_status": status,
        "reviewed_by": reviewed_by,
        "reviewed_at": now,
        "updated_at": now,
        # Always written, never merely set: restoring or rejecting a row that was previously
        # marked duplicate must clear the pointer, or it keeps naming a survivor for a
        # relationship that no longer exists.
        "duplicate_of": duplicate_of if status == "duplicate" else None,
    }
    if status in ADJUDICATED_STATUSES:
        # An adjudicated-away row must not be left visible to students, whatever it was
        # before. Approving, by contrast, does NOT activate: that stays an explicit,
        # separate decision on the Activate button.
        updates["is_active"] = False

    done, errors, details = 0, 0, []
    for opp_id in ids:
        try:
            _commit_patch(opp_id, updates)
            done += 1
        except Exception as e:
            if _is_missing_column_error(e):
                return {"ok": False, "moderation_ready": False,
                        "error": "The moderation columns do not exist yet. Run "
                                 "user_submissions_schema.sql in the Supabase SQL editor "
                                 "(it is idempotent), then restart the server."}
            errors += 1
            if len(details) < 5:
                details.append(f"{opp_id}: {str(e)[:160]}")

    if done and status in ADJUDICATED_STATUSES:
        with _opportunities_cache_lock:
            _opportunities_cache["fetched_at"] = 0.0
    return {"ok": errors == 0, "moderated": done, "status": status,
            "errors": errors, "error_details": details}


def activate_opportunities(ids, active=True):
    """Flip is_active for an explicit list of ids. Never called with anything but an
    operator's explicit selection — there is no "activate all matching" path on purpose."""
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not ids:
        return {"ok": False, "error": "No opportunity ids given."}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {"ok": False, "error": "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured."}
    done, errors, details = 0, 0, []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # Activating IS a human verdict, so stamp it as one — otherwise an activated row keeps
    # a NULL moderation_status and comes back round the queue on the next pass. Dropped
    # from the payload (not the whole write) if the migration has not been run.
    stamped = {"is_active": bool(active), "updated_at": now}
    if active:
        stamped.update({"moderation_status": "approved", "reviewed_by": "admin-console",
                        "reviewed_at": now})
    plain = {"is_active": bool(active), "updated_at": now}
    for opp_id in ids:
        try:
            try:
                _commit_patch(opp_id, stamped)
            except Exception as e:
                if not _is_missing_column_error(e):
                    raise
                stamped = plain  # migration not run; stop trying for the rest of the batch
                _commit_patch(opp_id, plain)
            done += 1
        except Exception as e:
            errors += 1
            if len(details) < 5:
                details.append(f"{opp_id}: {str(e)[:160]}")
    # The public /api/opportunities response is cached for OPPORTUNITIES_CACHE_TTL seconds;
    # without this the operator activates a row and then cannot see it in the app.
    if done:
        with _opportunities_cache_lock:
            _opportunities_cache["fetched_at"] = 0.0
    return {"ok": errors == 0, "activated": done if active else 0,
            "deactivated": 0 if active else done,
            "errors": errors, "error_details": details}


def get_agents_summary(days=30):
    """Aggregates for the dashboard KPI strip and charts.

    Totals are split per agent AND kept unit-aware: the scraper's items_processed counts
    SEEDS, not opportunity rows, so summing it into a catalog-wide "rows touched" figure
    would silently inflate that number. rows_updated/rows_added therefore only accumulate
    from agents whose unit is 'rows' plus the scraper's genuine items_added.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    prev_cutoff = now - datetime.timedelta(days=days * 2)

    rows = recent_runs()
    current, previous = [], []
    for r in rows:
        started = _parse_iso(r.get("started_at"))
        if not started:
            continue
        if started >= cutoff:
            current.append(r)
        elif started >= prev_cutoff:
            previous.append(r)

    def totals(bucket, deadline_cost=0.0):
        out = {"runs": 0, "cost_usd": 0.0, "agent_cost_usd": 0.0, "app_cost_usd": 0.0,
               "rows_updated": 0, "rows_added": 0, "errors": 0, "failed_runs": 0,
               "dry_runs": 0, "unknown_cost_runs": 0}
        for r in bucket:
            key = _agent_key_for(r.get("agent"))
            cfg = AGENT_CONFIGS_SCHEMA.get(key, {})
            cost = float(r.get("cost_usd") or 0)
            interactive = r.get("agent") in INTERACTIVE_AGENTS
            out["cost_usd"] += cost
            out["app_cost_usd" if interactive else "agent_cost_usd"] += cost

            if interactive:
                continue  # rollup rows aren't "runs" and have no rows-changed semantics

            out["runs"] += 1
            out["errors"] += r.get("errors") or 0
            if r.get("errors"):
                out["failed_runs"] += 1
            # A run that ended without patching its totals spent money we can't see.
            # Counting it silently as $0 is what made the old figure look complete.
            if r.get("cost_usd") is None and not r.get("finished_at"):
                out["unknown_cost_runs"] += 1
            # Dry runs cost real money but change nothing, so their cost counts while
            # their would-have-been row counts must not.
            if str(r.get("mode") or "").endswith("-dryrun"):
                out["dry_runs"] += 1
                continue
            out["rows_added"] += r.get("items_added") or 0
            if cfg.get("unit", "rows") == "rows":
                out["rows_updated"] += r.get("items_updated") or 0

        # On-demand deadline checks live in their own table, not agent_runs.
        out["deadline_cost_usd"] = round(deadline_cost, 4)
        out["app_cost_usd"] = round(out["app_cost_usd"] + deadline_cost, 4)
        out["cost_usd"] = round(out["cost_usd"] + deadline_cost, 4)
        out["agent_cost_usd"] = round(out["agent_cost_usd"], 4)
        out["error_rate"] = round(out["failed_runs"] / out["runs"], 3) if out["runs"] else 0
        return out

    # Per-day cost series, per agent — the stacked chart's input. Days with no runs are
    # emitted as zeros so the x-axis stays evenly spaced instead of collapsing gaps.
    per_day = {}
    for r in current:
        started = _parse_iso(r.get("started_at"))
        day = started.date().isoformat()
        key = _agent_key_for(r.get("agent"))
        bucket = per_day.setdefault(day, {})
        entry = bucket.setdefault(key, {"cost_usd": 0.0, "runs": 0, "rows": 0})
        entry["cost_usd"] = round(entry["cost_usd"] + float(r.get("cost_usd") or 0), 4)
        entry["runs"] += 1
        entry["rows"] += (r.get("items_updated") or 0) + (r.get("items_added") or 0)

    series = []
    for i in range(days):
        day = (cutoff + datetime.timedelta(days=i)).date().isoformat()
        series.append({"date": day, "agents": per_day.get(day, {})})

    by_agent = {}
    for key in AGENT_CONFIGS_SCHEMA:
        db_agent = AGENT_CONFIGS_SCHEMA[key]["db_agent"]
        by_agent[key] = totals([r for r in current if r.get("agent") == db_agent])

    dl_now = fetch_deadline_check_cost(cutoff, now)
    dl_prev = fetch_deadline_check_cost(prev_cutoff, cutoff)

    return {
        "days": days,
        "current": totals(current, dl_now["cost_usd"]),
        "previous": totals(previous, dl_prev["cost_usd"]),
        "by_agent": by_agent,
        "interactive": {
            key: {
                "name": label,
                "calls": sum(r.get("items_processed") or 0
                             for r in current if r.get("agent") == key),
                "cost_usd": round(sum(float(r.get("cost_usd") or 0)
                                      for r in current if r.get("agent") == key), 4),
            } for key, label in INTERACTIVE_AGENTS.items()
        },
        "deadline_checks": dl_now,
        "series": series,
        # What the totals above still cannot see, stated rather than implied.
        "caveats": [
            "Costs are estimated locally from token counts, not read from provider billing.",
            "A client-side timeout still bills server-side; that spend is never captured.",
            "Runs that ended without reporting totals contribute $0 to these figures.",
        ],
    }


def fetch_deadline_check_cost(start, end):
    """Cost of on-demand deadline checks in a window, from deadline_check_log.

    These are user-triggered checks from the app, logged to their own table rather than
    agent_runs — so they were absent from every figure the console showed. Cached rows
    have a null cost because they made no API call; only fresh checks are billed.
    """
    rows = _supabase_request("deadline_check_log", params={
        "select": "cost_usd,source,web_searches",
        "checked_at": f"gte.{start.isoformat()}",
        "limit": "10000",
    }) or []
    rows = [r for r in rows if (r.get("cost_usd") is not None)]
    return {
        "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 4),
        "billed_checks": len(rows),
    }


# ---------- Billed spend, pulled from the providers ----------
# Everything else in this file is an ESTIMATE computed locally from token counts. This is
# the only place that reports what a provider actually billed, so the console can show the
# drift between the two.
#
# Anthropic: GET /v1/organizations/cost_report returns real billed cost per day.
#   Requires an ADMIN key (sk-ant-admin..., x-api-key) or an org:admin OAuth token — a
#   regular sk-ant-api key CANNOT read it, and the Admin API is unavailable to individual
#   accounts entirely (an organization must be set up in the Console first).
#   Amounts come back in the currency's lowest unit as a decimal string, i.e. CENTS.
#
# Google/Gemini: there is NO equivalent. The Gemini API exposes no billing endpoint at all
#   — spend is visible only in the AI Studio dashboard or the Cloud Billing console, and
#   programmatic access would need the separate Google Cloud Billing API with a GCP service
#   account (an AI Studio API key cannot authenticate to it). So Gemini gets a
#   reconciliation link rather than a live figure.
ANTHROPIC_COST_URL = "https://api.anthropic.com/v1/organizations/cost_report"
ANTHROPIC_ADMIN_KEY = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
GEMINI_BILLING_URL = "https://aistudio.google.com/spend"
ANTHROPIC_BILLING_URL = "https://platform.claude.com/cost"


def fetch_anthropic_billed_cost(days=30):
    """Real billed Anthropic spend for the last `days`, or a reason it's unavailable."""
    if not ANTHROPIC_ADMIN_KEY:
        return {
            "available": False,
            "reason": "No ANTHROPIC_ADMIN_KEY in .env. The cost API needs an Admin key "
                      "(sk-ant-admin…), which a regular API key cannot substitute for, and "
                      "which requires an organization account.",
            "dashboard_url": ANTHROPIC_BILLING_URL,
        }
    start = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)).replace(
                 hour=0, minute=0, second=0, microsecond=0)
    params = urllib.parse.urlencode({
        "starting_at": start.isoformat().replace("+00:00", "Z"),
        "bucket_width": "1d",
        "limit": min(days, 31),
    })
    req = urllib.request.Request(
        f"{ANTHROPIC_COST_URL}?{params}",
        headers={"x-api-key": ANTHROPIC_ADMIN_KEY, "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        hint = ("The key was rejected. The cost report needs an Admin key (sk-ant-admin…) "
                "from an organization account — a regular sk-ant-api key returns 401."
                if e.code in (401, 403) else detail)
        return {"available": False, "reason": f"HTTP {e.code}: {hint}",
                "dashboard_url": ANTHROPIC_BILLING_URL}
    except Exception as e:
        return {"available": False, "reason": str(e), "dashboard_url": ANTHROPIC_BILLING_URL}

    total_cents = 0.0
    series = []
    for bucket in data.get("data", []):
        # `amount` is a decimal string in the lowest currency unit (cents), not dollars.
        day_cents = sum(float(item.get("amount") or 0) for item in bucket.get("results", []))
        total_cents += day_cents
        series.append({"date": (bucket.get("starting_at") or "")[:10],
                       "cost_usd": round(day_cents / 100, 4)})
    return {
        "available": True,
        "cost_usd": round(total_cents / 100, 4),
        "currency": "USD",
        "days": days,
        "series": series,
        "dashboard_url": ANTHROPIC_BILLING_URL,
    }


def get_billed_costs(days=30):
    """Provider-billed spend beside our own estimate, with the drift between them."""
    anthropic = fetch_anthropic_billed_cost(days)
    summary = get_agents_summary(days=days)
    estimated_total = summary["current"]["cost_usd"]

    drift = None
    if anthropic.get("available") and estimated_total:
        # Anthropic-billed vs OUR TOTAL estimate is apples-to-oranges (our estimate spans
        # both providers), so this is only indicative — say so rather than implying it's
        # a clean comparison.
        drift = round(anthropic["cost_usd"] - estimated_total, 4)

    return {
        "days": days,
        "estimated_total_usd": estimated_total,
        "anthropic": anthropic,
        "gemini": {
            "available": False,
            "reason": "The Gemini API exposes no billing endpoint. Spend is only visible "
                      "in the AI Studio dashboard, or via the separate Google Cloud "
                      "Billing API, which an AI Studio key cannot authenticate to.",
            "dashboard_url": GEMINI_BILLING_URL,
        },
        "drift_usd": drift,
    }


def get_admin_console_html():
    """Returns the admin console HTML, read fresh from disk on each request so edits
    show up on reload without restarting the server."""
    admin_path = os.path.join(os.path.dirname(__file__), "admin_console.html")
    if os.path.exists(admin_path):
        with open(admin_path, "r", encoding="utf-8") as f:
            return f.read()
    return """<!DOCTYPE html><html><head><title>Admin Console</title></head>
    <body><h1>Admin Console</h1><p>admin_console.html not found. Serve from project root.</p></body></html>"""


def mark_agent_running(agent_name):
    with _agent_runs_lock:
        _agent_runs[agent_name] = {
            "status": "running",
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


def mark_agent_idle(agent_name):
    with _agent_runs_lock:
        _agent_runs.pop(agent_name, None)


def is_agent_running(agent_name):
    with _agent_runs_lock:
        return _agent_runs.get(agent_name, {}).get("status") == "running"


def running_gemini_search_agent(exclude=None):
    """Name of a currently-running agent that holds the shared Gemini web-search lock.

    check_reviews and scrape_opportunities both acquire gemini_common's
    .gemini_web_search.lock, so only one can run at a time — the second fails fast. Better
    to refuse the launch with a clear reason than to let it start and die a minute later.
    """
    with _agent_runs_lock:
        running = [k for k, v in _agent_runs.items() if v.get("status") == "running"]
    for key in running:
        if key != exclude and AGENT_CONFIGS_SCHEMA.get(key, {}).get("uses_gemini_search"):
            return key
    return None


def get_agent_status(agent_name, rows=None):
    """Status for one agent card.

    'running' is process-local state (this server's own subprocess tracking) because the
    database has no way to know it. Everything else — last run, items, cost — comes from
    the newest real agent_runs row, so it stays accurate across server restarts and for
    runs triggered outside this console. The exception is a subprocess that crashed before
    writing any row at all (a bad flag, a missing key): that error exists only in local
    memory, so it takes priority over whatever the last DB row says.
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name, {})
    with _agent_runs_lock:
        local = dict(_agent_runs.get(agent_name, {}))

    result = {
        "name": cfg.get("name", agent_name),
        "description": cfg.get("description"),
        "unit": cfg.get("unit", "items"),
        "writes": cfg.get("writes"),
        "api": cfg.get("api"),
        "uses_gemini_search": cfg.get("uses_gemini_search", False),
        "defaults": agent_defaults(agent_name),
        "status": "idle",
        "last_run": None,
        "last_mode": None,
        "items_processed": 0,
        "items_updated": 0,
        "items_added": 0,
        "cost_usd": None,
        "silent_search_count": None,
        "error": None,
    }

    if local.get("status") == "running":
        result["status"] = "running"
        result["started_at"] = local.get("started_at")
        return result

    if rows is None:
        rows = recent_runs()
    db_agent = cfg.get("db_agent")
    latest = next((r for r in rows if r.get("agent") == db_agent), None)

    if latest:
        shaped = _shape_run(latest)
        result.update({
            "last_run": shaped["finished_at"] or shaped["started_at"],
            "last_mode": shaped["mode"],
            "items_processed": shaped["items_processed"],
            "items_updated": shaped["items_updated"],
            "items_added": shaped["items_added"],
            "cost_usd": shaped["cost_usd"],
            "silent_search_count": shaped["silent_search_count"],
        })
        if shaped["status"] == "failed":
            result["status"] = "error"
            result["error"] = shaped["notes"] or f"{shaped['errors']} error(s) in last run"
        elif shaped["status"] == "interrupted":
            result["status"] = "interrupted"
            result["error"] = "Previous run ended without reporting totals"

    if local.get("status") == "error":
        result["status"] = "error"
        result["error"] = local.get("error")

    return result


def get_all_agents_status():
    """Status of all four agents from ONE cached Supabase read."""
    rows = recent_runs()
    return {name: get_agent_status(name, rows=rows) for name in AGENT_CONFIGS_SCHEMA}


# ---------- Building agent argv ----------

AGENT_RUN_TIMEOUT_SECS = 3600
LOG_BUFFER_LINES = 500
AGENT_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_logs")

# Live output per agent: {agent: {"lines": [...], "file": path, "started_at": iso}}
_agent_logs = {}
_agent_logs_lock = threading.Lock()


def _int_or_none(value):
    """Coerce a config value to int. The console posts every field as a string (DOM input
    values), and empty strings mean 'not set' rather than zero."""
    if value in (None, "", False):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_agent_args(agent_name, config, preview=False):
    """Translate a console config dict into real argv for the agent's script.

    Every flag here was verified against the script's own argparse — inventing
    plausible-looking flags previously made every run fail with an argparse error, so do
    not add one without checking `python <script>.py --help` first.
    """
    cfg = AGENT_CONFIGS_SCHEMA[agent_name]
    args = [sys.executable, "-u", cfg["script"]]  # -u: unbuffered, so live output streams
    config = config or {}
    defaults = agent_defaults(agent_name)  # built-ins with saved overrides applied

    if agent_name == "metadata":
        # refresh_opportunities.py: [--sample N | --all] [--dry-run] [--exclude-source S]
        scope = config.get("scope")
        if scope == "sample":
            args += ["--sample", str(_int_or_none(config.get("sampleSize")) or 50)]
        else:
            args.append("--all")
        if config.get("excludeSource"):
            args += ["--exclude-source", str(config["excludeSource"])]

    elif agent_name == "reviews":
        # check_reviews.py: [--sample N | --all] [--force] [--dry-run]
        scope = config.get("scope")
        if scope == "sample":
            args += ["--sample", str(_int_or_none(config.get("sampleSize")) or 50)]
        elif scope == "all":
            args.append("--all")
        # else: no flag = default (stale/unchecked rows only)
        if config.get("force"):
            args.append("--force")

    elif agent_name == "scraper":
        # scrape_opportunities.py: --mode {national,seattle} (required) [--dry-run]
        # [--seed-ids S] [--seed-indices S] [--max-searches N]
        args += ["--mode", config.get("mode") or "national"]
        if config.get("seedIds"):
            args += ["--seed-ids", str(config["seedIds"])]
        max_searches = _int_or_none(config.get("maxSearches"))
        if max_searches:
            args += ["--max-searches", str(max_searches)]

    elif agent_name == "deadline":
        # check_deadlines.py: [--sample N | --all] [--dry-run]
        scope = config.get("scope")
        if scope == "sample":
            args += ["--sample", str(_int_or_none(config.get("sampleSize")) or 50)]
        else:
            args.append("--all")

    # Timing applies to every agent identically.
    min_delay = config.get("minDelay")
    if min_delay not in (None, ""):
        args += ["--min-delay", str(min_delay)]
    elif defaults.get("min_delay") is not None:
        args += ["--min-delay", str(defaults["min_delay"])]

    timeout = _int_or_none(config.get("timeout")) or defaults.get("timeout")
    if timeout:
        args += ["--timeout", str(timeout)]

    if preview:
        args.append("--preview")
    elif config.get("dryRun"):
        args.append("--dry-run")

    return args


def preview_agent(agent_name, config):
    """Run an agent with --preview: resolves scope, makes NO API calls, writes nothing.

    Pairs the script's own row count with a per-item cost derived from that agent's real
    agent_runs history, so the estimate reflects what this agent actually costs rather
    than a hardcoded guess. Row selection stays in the script (it cannot drift from what a
    real run would do); cost math stays here (this is where the history already lives).
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name)
    if not cfg:
        return {"ok": False, "error": f"Unknown agent: {agent_name}"}
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg["script"])
    if not os.path.exists(script):
        return {"ok": False, "error": f"Script not found: {cfg['script']}"}

    args = build_agent_args(agent_name, config, preview=True)
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=120,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Preview timed out after 120s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    payload = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith(AGENT_PREVIEW_PREFIX):
            try:
                payload = json.loads(line[len(AGENT_PREVIEW_PREFIX):].strip())
            except Exception:
                payload = None
    if payload is None:
        err = (proc.stderr or proc.stdout or "").strip()[-500:]
        return {"ok": False, "error": err or "Preview produced no PREVIEW_JSON line"}

    count = payload.get("count") or 0
    est = estimate_agent_cost(agent_name, count, min_delay=(config or {}).get("minDelay"))
    est.update({
        "ok": True,
        "agent": agent_name,
        "count": count,
        "unit": payload.get("unit") or cfg.get("unit", "items"),
        "sample": payload.get("sample") or [],
        "mode": payload.get("mode"),
        "seed_ids": payload.get("seed_ids"),
        "argv": args[1:],  # minus the interpreter path; shown in the UI for transparency
    })
    return est


def estimate_agent_cost(agent_name, count, min_delay=None):
    """Estimate cost and wall time for `count` items, from this agent's own history.

    Uses completed runs only (a run that died mid-pass has a cost that doesn't match its
    item count). Returns based_on_runs=0 when there's no history to learn from, which the
    UI surfaces rather than passing off a fabricated number as an estimate.

    `min_delay` is the delay the caller is ABOUT to run with, which may differ from the
    default the historical runs used. Wall time is max(measured per-item time, delay):
    raising the delay above measured latency slows the run proportionally, while lowering
    it below cannot speed the run past how long the API itself takes to answer.
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name, {})
    db_agent = cfg.get("db_agent")
    rows = [r for r in recent_runs()
            if r.get("agent") == db_agent and r.get("finished_at")
            and (r.get("items_processed") or 0) > 0 and r.get("cost_usd") is not None]

    per_item = None
    per_item_secs = None
    if rows:
        sample = rows[:10]
        per_item = sum(float(r["cost_usd"]) / r["items_processed"] for r in sample) / len(sample)
        durations = []
        for r in sample:
            t0, t1 = _parse_iso(r.get("started_at")), _parse_iso(r.get("finished_at"))
            if t0 and t1 and r["items_processed"]:
                durations.append((t1 - t0).total_seconds() / r["items_processed"])
        if durations:
            per_item_secs = sum(durations) / len(durations)

    # Wall time is usually dominated by the inter-call delay rather than the API itself.
    try:
        delay = float(min_delay) if min_delay not in (None, "") else None
    except (TypeError, ValueError):
        delay = None
    if delay is None:
        delay = float(agent_defaults(agent_name).get("min_delay") or 0)
    secs_each = max(per_item_secs or 0, delay)

    return {
        "est_cost_usd": round(per_item * count, 4) if per_item is not None else None,
        "est_cost_per_item": round(per_item, 6) if per_item is not None else None,
        "est_seconds": round(secs_each * count) if secs_each else None,
        "based_on_runs": len(rows[:10]),
    }


# ---------- Running an agent ----------

def _append_log(agent_name, line):
    with _agent_logs_lock:
        entry = _agent_logs.get(agent_name)
        if entry is None:
            return
        entry["lines"].append(line)
        # Ring buffer: the on-disk file keeps everything, memory keeps the tail.
        if len(entry["lines"]) > LOG_BUFFER_LINES:
            entry["dropped"] += len(entry["lines"]) - LOG_BUFFER_LINES
            del entry["lines"][:-LOG_BUFFER_LINES]
        handle = entry.get("handle")
        if handle:
            try:
                handle.write(line + "\n")
                handle.flush()
            except Exception:
                pass


def start_agent_log(agent_name):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(AGENT_LOG_DIR, exist_ok=True)
    path = os.path.join(AGENT_LOG_DIR, f"{agent_name}_{stamp}.log")
    try:
        handle = open(path, "w", encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Could not open agent log {path}: {e}")
        handle, path = None, None
    with _agent_logs_lock:
        _agent_logs[agent_name] = {
            "lines": [], "dropped": 0, "handle": handle, "path": path,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    return path


def close_agent_log(agent_name):
    with _agent_logs_lock:
        entry = _agent_logs.get(agent_name) or {}
        handle = entry.pop("handle", None)
    if handle:
        try:
            handle.close()
        except Exception:
            pass


def get_agent_log(agent_name, since=0):
    """Lines after `since` for the live console, plus the running total.

    `dropped` tells the client the ring buffer discarded older lines, so it can say so
    instead of silently showing a gap.
    """
    with _agent_logs_lock:
        entry = _agent_logs.get(agent_name)
        if not entry:
            return {"lines": [], "next": 0, "dropped": 0, "running": is_agent_running(agent_name)}
        dropped = entry["dropped"]
        total = dropped + len(entry["lines"])
        start = max(0, int(since) - dropped)
        return {
            "lines": entry["lines"][start:],
            "next": total,
            "dropped": dropped,
            "path": entry.get("path"),
            "started_at": entry.get("started_at"),
            "running": is_agent_running(agent_name),
        }


def run_agent_subprocess(agent_name, config):
    """Run an agent script as a subprocess, streaming its output. Returns (ok, message).

    Output is read line by line on a reader thread rather than via communicate(), which
    blocks until the process exits — a full pass runs for over an hour, so the previous
    implementation meant the console showed nothing at all until the very end and then
    discarded the output entirely.
    """
    cfg = AGENT_CONFIGS_SCHEMA.get(agent_name, {})
    script = cfg.get("script")
    root = os.path.dirname(os.path.abspath(__file__))
    if not script or not os.path.exists(os.path.join(root, script)):
        return False, f"Script not found: {script}"

    args = build_agent_args(agent_name, config)
    proc = None
    try:
        mark_agent_running(agent_name)
        log_path = start_agent_log(agent_name)
        _append_log(agent_name, f"$ {' '.join(args[1:])}")
        print(f"[INFO] Starting {agent_name}: {' '.join(args[1:])}")

        proc = subprocess.Popen(
            args, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
        _agent_process[agent_name] = proc

        for line in proc.stdout:
            _append_log(agent_name, line.rstrip("\n"))

        try:
            proc.wait(timeout=AGENT_RUN_TIMEOUT_SECS)
        except subprocess.TimeoutExpired:
            proc.kill()
            msg = f"Execution timed out (limit {AGENT_RUN_TIMEOUT_SECS // 60} min)"
            _append_log(agent_name, f"[ERROR] {msg}")
            with _agent_runs_lock:
                _agent_runs[agent_name] = {"status": "error", "error": msg}
            return False, msg

        invalidate_runs_cache()  # the script just wrote its agent_runs row

        if proc.returncode == 0:
            mark_agent_idle(agent_name)
            _append_log(agent_name, "[DONE] Agent completed (exit 0)")
            print(f"[INFO] {agent_name} finished (exit 0)")
            return True, "Agent completed"

        # A non-zero exit usually means it died before writing any agent_runs row (bad
        # flag, missing key), so the DB has no record of the attempt — surface the tail of
        # its output as the error, since that's the only trace that exists.
        with _agent_logs_lock:
            tail = "\n".join((_agent_logs.get(agent_name) or {}).get("lines", [])[-15:])
        _append_log(agent_name, f"[ERROR] Agent exited {proc.returncode}")
        with _agent_runs_lock:
            _agent_runs[agent_name] = {"status": "error", "error": tail[-500:]}
        print(f"[WARN] {agent_name} failed (exit {proc.returncode})")
        return False, f"Agent failed (exit {proc.returncode})"

    except Exception as e:
        msg = str(e)
        _append_log(agent_name, f"[ERROR] {msg}")
        with _agent_runs_lock:
            _agent_runs[agent_name] = {"status": "error", "error": msg}
        print(f"[ERROR] Failed to run {agent_name}: {msg}")
        return False, f"Error running agent: {msg}"
    finally:
        close_agent_log(agent_name)
        _agent_process[agent_name] = None


# ---------- Scraper seeds (the editable search angles) ----------

SEED_FIELDS = ("mode", "category", "angle", "is_enabled", "sort_order")
SEED_SELECT = ("id,mode,category,angle,is_enabled,sort_order,total_runs,total_found,"
               "total_added,total_dupes,total_cost,last_run_at,created_at")


def list_seeds(mode=None):
    """All seeds (enabled and disabled) for the console's angle manager.

    Adds derived productivity figures the UI ranks by — added_per_dollar is the one that
    actually answers "is this angle worth paying for every month".
    """
    params = {"select": SEED_SELECT, "order": "mode.asc,sort_order.asc,id.asc"}
    if mode:
        params["mode"] = f"eq.{mode}"
    rows = _supabase_request("scraper_seeds", params=params)
    if rows is None:
        return None
    for r in rows:
        cost = float(r.get("total_cost") or 0)
        added = r.get("total_added") or 0
        found = r.get("total_found") or 0
        r["added_per_dollar"] = round(added / cost, 1) if cost > 0 else None
        r["dupe_rate"] = round((r.get("total_dupes") or 0) / found, 3) if found else None
    return rows


def create_seed(payload):
    row = {k: payload[k] for k in SEED_FIELDS if k in payload}
    if not row.get("mode") or not row.get("angle") or not row.get("category"):
        return None, "mode, category and angle are all required"
    if row["mode"] not in ("national", "seattle"):
        return None, "mode must be 'national' or 'seattle'"
    row.setdefault("is_enabled", True)
    if row.get("sort_order") is None:
        existing = _supabase_request("scraper_seeds", params={
            "select": "sort_order", "mode": f"eq.{row['mode']}",
            "order": "sort_order.desc", "limit": "1"}) or []
        row["sort_order"] = ((existing[0].get("sort_order") or 0) + 1) if existing else 0
    created = _supabase_request("scraper_seeds", method="POST", data=[row],
                                 extra_headers={"Prefer": "return=representation"})
    if created is None:
        return None, "Supabase insert failed"
    return (created[0] if created else row), None


def update_seed(seed_id, payload):
    patch = {k: payload[k] for k in SEED_FIELDS if k in payload}
    if not patch:
        return None, "No editable fields supplied"
    updated = _supabase_request("scraper_seeds", method="PATCH",
                                 params={"id": f"eq.{seed_id}"}, data=patch,
                                 extra_headers={"Prefer": "return=representation"})
    if updated is None:
        return None, "Supabase update failed"
    return (updated[0] if updated else {}), None


def delete_seed(seed_id):
    """Hard delete. The console prefers toggling is_enabled, since deleting also destroys
    the angle's accumulated yield history."""
    result = _supabase_request("scraper_seeds", method="DELETE",
                                params={"id": f"eq.{seed_id}"})
    return (result is not None), None if result is not None else "Supabase delete failed"


class Handler(SimpleHTTPRequestHandler):
    # Local dev server: disable HTTP caching entirely on every response (static files
    # AND API JSON). Without this, Chrome heuristically caches script.js/index.html
    # (SimpleHTTPRequestHandler sends Last-Modified but no Cache-Control, so browsers
    # apply their own freshness heuristic and can silently keep serving a stale copy
    # of script.js after an edit — even a hard reload doesn't always bust it). Bit for
    # bit this cost us a debugging session: new script.js functions were undefined in
    # the browser while the file on disk was correct, because the page's <script> tag
    # was still loading the pre-edit cached bytes.
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    # Admin routes are matched on the PATH ONLY, with the query string stripped —
    # everything else in this class compares self.path by exact string equality, which
    # silently 404s the moment a URL carries ?agent=... or ?days=...
    def do_GET(self):
        path, query = self._split_path()
        deadline_match = DEADLINE_PATH_RE.match(path)
        if deadline_match:
            self.handle_deadline_check(deadline_match.group(1), query)
        elif path == "/admin":
            if self._require_local():
                self.handle_admin_console()
        elif path.startswith("/api/agents/") or path == "/api/seeds":
            if not self._require_local():
                return
            if path == "/api/agents/status":
                self.handle_agents_status()
            elif path == "/api/agents/history":
                self.handle_agents_history(query)
            elif path == "/api/agents/summary":
                self.handle_agents_summary(query)
            elif path == "/api/agents/config":
                self.handle_agents_config()
            elif path == "/api/agents/snapshots":
                self.handle_snapshots_list()
            elif path == "/api/agents/pending":
                self.handle_pending_list(query)
            elif path == "/api/agents/opportunities/search":
                self.handle_opportunity_search(query)
            elif path == "/api/agents/user-costs":
                self.handle_agents_user_costs(query)
            elif path == "/api/agents/billed":
                self.handle_agents_billed(query)
            elif path == "/api/agents/log":
                self.handle_agents_log(query)
            elif path == "/api/seeds":
                self.handle_seeds_list(query)
            else:
                self.send_error(404)
        elif self.path.startswith("/api/opportunities"):
            self.handle_opportunities()
        elif path == "/api/auth/google/start":
            self.handle_google_start()
        elif path == "/api/auth/google/callback":
            self.handle_google_callback(query)
        elif path == "/api/auth/google/session":
            self.handle_google_session(query)
        elif path == "/api/auth/google/calendar/start":
            self.handle_google_calendar_start(query)
        elif path == "/api/auth/google/calendar/callback":
            self.handle_google_calendar_callback(query)
        else:
            super().do_GET()

    def do_PATCH(self):
        path, _ = self._split_path()
        seed = SEED_PATH_RE.match(path)
        settings = AGENT_SETTINGS_PATH_RE.match(path)
        if seed:
            if self._require_local():
                self.handle_seed_update(seed.group(1))
        elif settings:
            if self._require_local():
                self.handle_agent_settings(settings.group(1))
        else:
            self.send_error(404)

    def do_DELETE(self):
        path, _ = self._split_path()
        match = SEED_PATH_RE.match(path)
        if match:
            if self._require_local():
                self.handle_seed_delete(match.group(1))
        else:
            self.send_error(404)

    def _split_path(self):
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    def _require_local(self):
        """Admin routes are localhost-only.

        The server binds all interfaces, and these routes launch subprocesses that spend
        real money on paid APIs — so without this, anyone who can reach port 8000 could
        trigger a full catalog pass. Returns True when the request may proceed.
        """
        client = (self.client_address or ("",))[0]
        if client in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"):
            return True
        print(f"[WARN] Blocked non-local admin request from {client}: {self.path}")
        self.send_json_error(403, "Admin routes are restricted to localhost.")
        return False

    def do_POST(self):
        path, _ = self._split_path()
        if path.startswith("/api/agents/") or path == "/api/seeds":
            if not self._require_local():
                return
            if path == "/api/agents/snapshots/commit":
                self.handle_snapshot_commit()
            elif path == "/api/agents/pending/activate":
                self.handle_pending_activate()
            elif path == "/api/agents/pending/moderate":
                self.handle_pending_moderate()
            elif path == "/api/agents/pending/update":
                self.handle_pending_update()
            elif path == "/api/agents/run":
                self.handle_agents_run()
            elif path == "/api/agents/preview":
                self.handle_agents_preview()
            elif path == "/api/seeds":
                self.handle_seed_create()
            else:
                self.send_error(404)
            return
        if self.path == "/api/messages":
            self.handle_messages()
        elif self.path == "/api/messages-claude":
            self.handle_messages_claude()
        elif self.path == "/api/register":
            self.handle_register()
        elif self.path == "/api/login":
            self.handle_login()
        elif self.path == "/api/auth/google/finish":
            self.handle_google_finish()
        elif self.path == "/api/calendar/sync":
            self.handle_calendar_sync()
        elif self.path == "/api/data/save":
            self.handle_data_save()
        elif self.path == "/api/data/load":
            self.handle_data_load()
        elif self.path == "/api/account/location":
            self.handle_update_location()
        elif urllib.parse.urlparse(self.path).path == "/api/extract-from-resume":
            self.handle_extract_from_resume()
        elif self.path == "/api/extract-from-linkedin":
            self.handle_extract_from_linkedin()
        elif self.path == "/api/user-submitted-opportunities":
            self.handle_user_submitted_opportunity()
        elif self.path == "/api/subscription/status":
            self.handle_subscription_status()
        elif self.path == "/api/subscription/checkout":
            self.handle_subscription_checkout()
        elif self.path == "/api/subscription/cancel":
            self.handle_subscription_cancel()
        elif self.path == "/api/subscription/validate-promo":
            self.handle_validate_promo()
        elif self.path == "/api/subscription/redeem-promo":
            self.handle_redeem_promo()
        else:
            self.send_error(404)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _read_json_body_strict(self):
        """Read a JSON body, distinguishing 'malformed' from 'empty'. Returns (data, error).

        _read_json_body() swallows parse failures into {}, which makes a mis-encoded or
        truncated body surface as a confusing 'required field missing' error instead of the
        real cause. Content-Length counts BYTES, so a body containing any non-ASCII
        character (an em-dash in a search angle, say) fails here if the client sent
        anything other than UTF-8.
        """
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}, None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")), None
        except UnicodeDecodeError:
            return None, ("Request body is not valid UTF-8. Send the JSON as UTF-8 "
                          "(browsers do this automatically).")
        except Exception as e:
            return None, f"Malformed JSON body: {e}"

    def handle_register(self):
        body = self._read_json_body()
        first_name = (body.get("firstName") or "").strip()
        last_name = (body.get("lastName") or "").strip()
        email = (body.get("email") or "").strip()
        userid = (body.get("userid") or "").strip()
        password_hash = body.get("passwordHash") or ""
        location = (body.get("location") or "").strip()
        if not all([first_name, last_name, email, userid, password_hash, location]):
            return self.send_json_error(400, "Missing required fields.")

        # Consent gate. The browser disables the submit button until these are ticked,
        # but that is a convenience, not the control — anything can POST here directly,
        # so the same three conditions are re-checked server-side and the account is
        # simply not created if they don't hold.
        is_adult = bool(body.get("isAdult"))
        parental_consent = bool(body.get("parentalConsent"))
        accepted_terms = bool(body.get("acceptedTerms"))
        consent_error = _check_signup_consent(is_adult, parental_consent, accepted_terms)
        if consent_error:
            return self.send_json_error(400, consent_error)

        if not EMAIL_RE.match(email):
            return self.send_json_error(400, "Please enter a valid email address.")

        # Both the user ID and the email must be free. Checked up front so the user gets
        # one specific message naming the field that clashed — a bare 409 out of Postgres
        # cannot say which of the two it was without parsing the constraint name.
        #
        # Both comparisons are case-insensitive by normalization rather than by ILIKE:
        # the user ID is stored lowercased and the email goes through normalize_email().
        # There is no is_active column on `users`, so every row is a live account and any
        # hit here is a real conflict.
        key = userid.lower()
        try:
            if get_user(key):
                return self.send_json_error(409, "That user ID is already taken.")
            existing = get_user_by_email(email)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if existing:
            return self.send_json_error(409, "An account already exists with that email "
                                             "address. Sign in instead, or use a different "
                                             "email.")

        try:
            create_user(key, first_name, last_name, email, password_hash, location,
                        is_adult=is_adult, parental_consent=parental_consent)
        except MissingUserColumns:
            return self.send_json_error(503, "Accounts are temporarily unavailable: the "
                                             "database is missing the subscription and "
                                             "consent columns. Run subscription_schema.sql "
                                             "in the Supabase SQL editor, then try again.")
        except DuplicateEmail:
            # Lost a race with a simultaneous signup, between the check above and this
            # insert. Only the database can catch that, and only once the unique index
            # from users_email_unique_schema.sql exists.
            return self.send_json_error(409, "An account already exists with that email "
                                             "address. Sign in instead, or use a different "
                                             "email.")
        except urllib.error.HTTPError as e:
            if e.code == 409:
                return self.send_json_error(409, "That user ID is already taken.")
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        self._relay(200, json.dumps({"ok": True}).encode())

    def handle_login(self):
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip()
        password_hash = body.get("passwordHash") or ""
        key = userid.lower()
        try:
            record = get_user(key)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if not record:
            return self.send_json_error(404, "No account found with that user ID.")
        if record.get("password_hash") != password_hash:
            return self.send_json_error(401, "Incorrect password.")
        record = ensure_trial_started(key, record)
        self._relay(200, json.dumps(_login_payload(record)).encode())

    # ---------- Google Sign-In ----------
    # See google_auth_schema.sql and the constants/helpers near GOOGLE_CLIENT_ID above.
    # Four-step redirect flow: start -> Google -> callback -> (session | finish).

    def handle_google_start(self):
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            return self.send_json_error(503, "Google Sign-In is not configured: set "
                                             "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET "
                                             "in .env.")
        state = secrets.token_urlsafe(24)
        params = {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": self._google_redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
        self.send_response(302)
        self.send_header("Location", f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}")
        # Short-lived, HttpOnly — this is CSRF protection for the OAuth handshake only,
        # not an app session cookie (the app has none; see _login_payload/handle_login).
        cookie = http.cookies.SimpleCookie()
        cookie["google_oauth_state"] = state
        cookie["google_oauth_state"]["path"] = "/"
        cookie["google_oauth_state"]["httponly"] = True
        cookie["google_oauth_state"]["max-age"] = GOOGLE_TOKEN_TTL_SECONDS
        for line in cookie.output(header="").split("\r\n"):
            if line.strip():
                self.send_header("Set-Cookie", line.strip())
        self.end_headers()

    def _google_redirect_uri(self):
        # Must exactly match one of the Authorized redirect URIs on the OAuth client —
        # Google rejects the exchange otherwise. Derived from the request's own Host
        # header so localhost and the production domain both work off the same code.
        host = self.headers.get("Host", f"localhost:{PORT}")
        scheme = "http" if host.startswith("localhost") or host.startswith("127.0.0.1") else "https"
        return f"{scheme}://{host}/api/auth/google/callback"

    def _google_redirect_home(self, query_suffix=""):
        self.send_response(302)
        self.send_header("Location", f"/{query_suffix}")
        self.end_headers()

    def handle_google_callback(self, query):
        cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        cookie_state = cookies["google_oauth_state"].value if "google_oauth_state" in cookies else None
        req_state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        if not code or not req_state or not cookie_state or req_state != cookie_state:
            return self.send_json_error(400, "Google sign-in failed: invalid or expired "
                                             "request. Please try again.")
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            return self.send_json_error(503, "Google Sign-In is not configured.")

        try:
            token_req = urllib.request.Request(
                GOOGLE_TOKEN_URL,
                data=urllib.parse.urlencode({
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": self._google_redirect_uri(),
                    "grant_type": "authorization_code",
                }).encode(),
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(token_req, timeout=10) as resp:
                tokens = json.loads(resp.read())
            userinfo_req = urllib.request.Request(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            with urllib.request.urlopen(userinfo_req, timeout=10) as resp:
                profile = json.loads(resp.read())
        except Exception as e:
            print(f"[WARN] Google OAuth exchange failed: {e}")
            return self.send_json_error(502, "Could not verify your Google account. "
                                             "Please try again.")

        google_id = profile.get("sub")
        email = profile.get("email") or ""
        first_name = profile.get("given_name") or ""
        last_name = profile.get("family_name") or ""
        if not google_id or not email:
            return self.send_json_error(502, "Google did not return a usable profile.")

        try:
            record = get_user_by_google_id(google_id)
            if not record:
                by_email = get_user_by_email(email)
                if by_email:
                    # Google has already verified this address, so it's safe to link it
                    # to the existing password account rather than creating a duplicate.
                    record = get_user(by_email["userid"])
                    query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{record['userid']}"})
                    _users_request("PATCH", query_patch, data={"google_id": google_id})
                    record["google_id"] = google_id
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")

        if record:
            token = _mint_google_token({"kind": "login", "userid": record["userid"]})
        else:
            token = _mint_google_token({
                "kind": "pending",
                "google_id": google_id,
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
            })
        self._google_redirect_home(f"?google_token={urllib.parse.quote(token)}")

    def handle_google_session(self, query):
        token = (query.get("token") or [""])[0]
        entry = _take_google_token(token)
        if not entry:
            return self.send_json_error(400, "This sign-in link has expired. Please try "
                                             "signing in with Google again.")
        if entry["kind"] == "pending":
            return self._relay(200, json.dumps({
                "ok": True,
                "pending": True,
                "firstName": entry["first_name"],
                "lastName": entry["last_name"],
                "email": entry["email"],
            }).encode())
        try:
            record = get_user(entry["userid"])
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if not record:
            return self.send_json_error(404, "No account found.")
        record = ensure_trial_started(record["userid"], record)
        self._relay(200, json.dumps(_login_payload(record)).encode())

    def handle_google_finish(self):
        body = self._read_json_body()
        token = body.get("token") or ""
        entry = _take_google_token(token)
        if not entry or entry.get("kind") != "pending":
            return self.send_json_error(400, "This sign-in link has expired. Please try "
                                             "signing in with Google again.")

        location = (body.get("location") or "").strip()
        is_adult = bool(body.get("isAdult"))
        parental_consent = bool(body.get("parentalConsent"))
        accepted_terms = bool(body.get("acceptedTerms"))
        consent_error = _check_signup_consent(is_adult, parental_consent, accepted_terms)
        if consent_error:
            return self.send_json_error(400, consent_error)
        if not location:
            return self.send_json_error(400, "Missing required fields.")

        try:
            if get_user_by_google_id(entry["google_id"]) or get_user_by_email(entry["email"]):
                # Lost a race with another completion of the same pending signup (e.g. a
                # duplicate tab), or the email was claimed by a fresh password signup in
                # between. Either way there's now a real account for it — not an error.
                return self.send_json_error(409, "An account for this Google profile "
                                                 "already exists. Please sign in again.")
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")

        userid = _unique_userid_from_email(entry["email"])
        try:
            create_user(userid, entry["first_name"], entry["last_name"], entry["email"],
                        None, location, is_adult=is_adult,
                        parental_consent=parental_consent, google_id=entry["google_id"])
        except MissingUserColumns:
            return self.send_json_error(503, "Accounts are temporarily unavailable: the "
                                             "database is missing required columns. Run "
                                             "subscription_schema.sql and "
                                             "google_auth_schema.sql in the Supabase SQL "
                                             "editor, then try again.")
        except DuplicateEmail:
            return self.send_json_error(409, "An account already exists with that email "
                                             "address. Please sign in instead.")
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")

        record = get_user(userid)
        self._relay(200, json.dumps(_login_payload(record)).encode())

    # ---------- Google Calendar connect + sync ----------
    # See the GOOGLE_CALENDAR_SCOPE comment above and google_calendar_schema.sql.

    def handle_google_calendar_start(self, query):
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            return self.send_json_error(503, "Google Sign-In is not configured: set "
                                             "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET "
                                             "in .env.")
        userid = (query.get("userid") or [""])[0].strip().lower()
        if not userid:
            return self.send_json_error(400, "Missing userid.")
        try:
            if not get_user(userid):
                return self.send_json_error(404, "No account found.")
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")

        _prune_google_calendar_states()
        state = secrets.token_urlsafe(24)
        _google_calendar_states[state] = {"userid": userid, "expires_at": time.time() + GOOGLE_TOKEN_TTL_SECONDS}

        params = {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": self._google_calendar_redirect_uri(),
            "response_type": "code",
            "scope": GOOGLE_CALENDAR_SCOPE,
            "state": state,
            # offline + consent guarantee a refresh_token comes back even if this user
            # already granted this scope before — Google otherwise only issues one on a
            # user's *first* consent, and it's stored (not discarded) so a later sync
            # doesn't need to send the user through this screen again.
            "access_type": "offline",
            "prompt": "consent",
        }
        self.send_response(302)
        self.send_header("Location", f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}")
        cookie = http.cookies.SimpleCookie()
        cookie["google_calendar_oauth_state"] = state
        cookie["google_calendar_oauth_state"]["path"] = "/"
        cookie["google_calendar_oauth_state"]["httponly"] = True
        cookie["google_calendar_oauth_state"]["max-age"] = GOOGLE_TOKEN_TTL_SECONDS
        for line in cookie.output(header="").split("\r\n"):
            if line.strip():
                self.send_header("Set-Cookie", line.strip())
        self.end_headers()

    def _google_calendar_redirect_uri(self):
        host = self.headers.get("Host", f"localhost:{PORT}")
        scheme = "http" if host.startswith("localhost") or host.startswith("127.0.0.1") else "https"
        return f"{scheme}://{host}/api/auth/google/calendar/callback"

    def handle_google_calendar_callback(self, query):
        cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        cookie_state = cookies["google_calendar_oauth_state"].value if "google_calendar_oauth_state" in cookies else None
        req_state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        _prune_google_calendar_states()
        entry = _google_calendar_states.pop(req_state, None) if req_state else None
        if not code or not req_state or not cookie_state or req_state != cookie_state or not entry:
            return self.send_json_error(400, "Google Calendar connection failed: invalid "
                                             "or expired request. Please try again.")
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            return self.send_json_error(503, "Google Sign-In is not configured.")

        try:
            token_req = urllib.request.Request(
                GOOGLE_TOKEN_URL,
                data=urllib.parse.urlencode({
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": self._google_calendar_redirect_uri(),
                    "grant_type": "authorization_code",
                }).encode(),
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(token_req, timeout=10) as resp:
                tokens = json.loads(resp.read())
        except Exception as e:
            print(f"[WARN] Google Calendar OAuth exchange failed: {e}")
            return self.send_json_error(502, "Could not connect Google Calendar. Please "
                                             "try again.")

        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")  # absent if this scope was granted before
        expires_in = tokens.get("expires_in") or 3600
        if not access_token:
            return self.send_json_error(502, "Google did not return a usable token.")

        now = datetime.datetime.now(datetime.timezone.utc)
        expires_at = (now + datetime.timedelta(seconds=expires_in)).isoformat()
        patch = {
            "google_calendar_access_token": access_token,
            "google_calendar_token_expires_at": expires_at,
            "google_calendar_connected_at": now.isoformat(),
        }
        if refresh_token:
            patch["google_calendar_refresh_token"] = refresh_token
        try:
            query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{entry['userid']}"})
            _users_request("PATCH", query_patch, data=patch)
        except urllib.error.HTTPError as e:
            if _is_missing_column_error(e):
                return self.send_json_error(503, "Google Calendar sync is temporarily "
                                                 "unavailable: run google_calendar_schema.sql "
                                                 "in the Supabase SQL editor, then try again.")
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")

        self._google_redirect_home("?calendar_connected=1")

    def _get_google_calendar_access_token(self, userid):
        """Returns a valid access token for this user's Calendar grant, refreshing it
        first if expired. Returns None if the user has never connected Calendar, and
        raises on a Supabase/Google failure so the caller can distinguish the two."""
        record = get_user(userid)
        if not record or not record.get("google_calendar_refresh_token"):
            return None
        expires_at = record.get("google_calendar_token_expires_at")
        access_token = record.get("google_calendar_access_token")
        still_valid = False
        if expires_at and access_token:
            try:
                exp = datetime.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                still_valid = exp > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)
            except ValueError:
                still_valid = False
        if still_valid:
            return access_token

        token_req = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode({
                "refresh_token": record["google_calendar_refresh_token"],
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "grant_type": "refresh_token",
            }).encode(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            tokens = json.loads(resp.read())
        access_token = tokens.get("access_token")
        expires_in = tokens.get("expires_in") or 3600
        if not access_token:
            return None
        expires_at = (datetime.datetime.now(datetime.timezone.utc)
                      + datetime.timedelta(seconds=expires_in)).isoformat()
        query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}"})
        _users_request("PATCH", query_patch, data={
            "google_calendar_access_token": access_token,
            "google_calendar_token_expires_at": expires_at,
        })
        return access_token

    def _ensure_wingman_calendar(self, access_token, userid, record):
        """Returns the id of this user's dedicated "Highschool Wingman" calendar,
        creating it on first use. calendar.app.created only grants access to events on
        calendars the app itself created, so events can never land on the user's primary
        calendar or any other existing one — this is what makes that true."""
        calendar_id = record.get("google_calendar_id")
        if calendar_id:
            check_req = urllib.request.Request(
                f"{GOOGLE_CALENDAR_API_BASE}/calendars/{urllib.parse.quote(calendar_id)}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            try:
                with urllib.request.urlopen(check_req, timeout=10):
                    return calendar_id
            except urllib.error.HTTPError as e:
                if e.code not in (404, 403):
                    raise
                # Calendar was deleted on Google's side (or predates this grant) — fall
                # through and create a fresh one.

        create_req = urllib.request.Request(
            f"{GOOGLE_CALENDAR_API_BASE}/calendars",
            data=json.dumps({"summary": WINGMAN_CALENDAR_NAME}).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(create_req, timeout=10) as resp:
            created = json.loads(resp.read())
        calendar_id = created["id"]
        query_patch = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}"})
        _users_request("PATCH", query_patch, data={"google_calendar_id": calendar_id})
        return calendar_id

    def handle_calendar_sync(self):
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        events = body.get("events") or []
        if not userid:
            return self.send_json_error(400, "Missing userid.")
        if not isinstance(events, list) or not events:
            return self.send_json_error(400, "No events to sync.")

        try:
            access_token = self._get_google_calendar_access_token(userid)
        except Exception as e:
            return self.send_json_error(502, f"Could not refresh Google Calendar access: {e}")
        if not access_token:
            return self.send_json_error(409, "Google Calendar is not connected for this "
                                             "account. Connect it first.")

        try:
            calendar_id = self._ensure_wingman_calendar(access_token, userid, get_user(userid))
        except urllib.error.HTTPError as e:
            if _is_missing_column_error(e):
                return self.send_json_error(503, "Google Calendar sync is temporarily "
                                                 "unavailable: run google_calendar_schema.sql "
                                                 "in the Supabase SQL editor, then try again.")
            return self.send_json_error(502, f"Could not prepare your {WINGMAN_CALENDAR_NAME} calendar: {e}")
        except Exception as e:
            return self.send_json_error(502, f"Could not prepare your {WINGMAN_CALENDAR_NAME} calendar: {e}")

        results = []
        for event in events:
            item_id = event.get("id")
            title = (event.get("title") or "").strip()
            date_iso = (event.get("dateISO") or "").strip()
            description = event.get("description") or ""
            google_event_id = event.get("googleEventId")
            if not item_id or not title or not date_iso:
                results.append({"id": item_id, "status": "error", "error": "Missing id, title, or dateISO."})
                continue

            year, month, day = date_iso.split("-")
            end_obj = (datetime.date(int(year), int(month), int(day)) + datetime.timedelta(days=1))
            body_payload = {
                "summary": title,
                "description": description,
                "start": {"date": date_iso},
                "end": {"date": end_obj.isoformat()},
            }
            try:
                calendar_path = f"calendars/{urllib.parse.quote(calendar_id)}"
                if google_event_id:
                    url = f"{GOOGLE_CALENDAR_API_BASE}/{calendar_path}/events/{google_event_id}"
                    method = "PATCH"
                else:
                    url = f"{GOOGLE_CALENDAR_API_BASE}/{calendar_path}/events"
                    method = "POST"
                req = urllib.request.Request(
                    url,
                    data=json.dumps(body_payload).encode(),
                    method=method,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    created = json.loads(resp.read())
                results.append({"id": item_id, "status": "ok", "googleEventId": created.get("id")})
            except urllib.error.HTTPError as e:
                # A previously-synced event the user deleted on Google's side 404s on
                # PATCH — fall back to creating a fresh one rather than failing the sync.
                if google_event_id and e.code == 404:
                    try:
                        req = urllib.request.Request(
                            f"{GOOGLE_CALENDAR_API_BASE}/{calendar_path}/events",
                            data=json.dumps(body_payload).encode(),
                            method="POST",
                            headers={
                                "Authorization": f"Bearer {access_token}",
                                "Content-Type": "application/json",
                            },
                        )
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            created = json.loads(resp.read())
                        results.append({"id": item_id, "status": "ok", "googleEventId": created.get("id")})
                        continue
                    except Exception as e2:
                        results.append({"id": item_id, "status": "error", "error": str(e2)})
                        continue
                results.append({"id": item_id, "status": "error", "error": f"Google API error {e.code}"})
            except Exception as e:
                results.append({"id": item_id, "status": "error", "error": str(e)})

        self._relay(200, json.dumps({"ok": True, "results": results}).encode())

    def handle_update_location(self):
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        location = (body.get("location") or "").strip()
        if not userid or not location:
            return self.send_json_error(400, "Missing userid or location.")
        try:
            ok = update_user_location(userid, location)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if not ok:
            return self.send_json_error(404, "No account found with that user ID.")
        self._relay(200, json.dumps({"ok": True}).encode())

    # ---------- Per-account app data (profile, tracker, saved items) ----------
    # A generic key/value blob per user, stored in the `data` jsonb column of the
    # same Supabase row so it survives logout/login and server restarts — unlike
    # the client-only window.storage the rest of the app was built around (see
    # script.js).
    def handle_data_save(self):
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        key = body.get("key")
        if not userid or not key:
            return self.send_json_error(400, "Missing userid or key.")
        try:
            ok = update_user_data(userid, key, body.get("value"))
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if not ok:
            return self.send_json_error(404, "No account found with that user ID.")
        self._relay(200, json.dumps({"ok": True}).encode())

    def handle_data_load(self):
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        key = body.get("key")
        try:
            record = get_user(userid)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        value = (record.get("data") or {}).get(key) if record else None
        self._relay(200, json.dumps({"value": value}).encode())

    def handle_extract_from_resume(self):
        """Extract profile-relevant information from a resume (PDF or DOCX)."""
        # userid rides in on the query string here, not the body — this is a multipart
        # upload. Gate before reading the file so a lapsed account can't make us parse
        # a PDF, let alone call Claude on it.
        if self._subscription_blocks(self._qs(
                urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query), "userid")):
            return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return self.send_json_error(400, "Request must be multipart/form-data with file field.")

        boundary = content_type.split("boundary=")[-1].strip().encode()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        try:
            file_data = self._extract_multipart_file(raw, boundary)
            if not file_data:
                return self.send_json_error(400, "No file found in request.")

            filename, file_bytes = file_data

            # Extract text based on file type
            if filename.lower().endswith(".pdf"):
                text = self._extract_text_from_pdf(file_bytes)
            elif filename.lower().endswith(".docx"):
                text = self._extract_text_from_docx(file_bytes)
            else:
                return self.send_json_error(400, "Unsupported file format. Use PDF or DOCX.")

            if not text or not text.strip():
                return self._relay(200, json.dumps({"extracted_text": "", "source": "resume", "filename": filename}).encode())

            # Extract profile-relevant information. userid rides in on the query string
            # rather than the body because this is a multipart upload.
            userid = self._qs(
                urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query), "userid")
            extracted = self._extract_profile_from_text(text, "resume", userid=userid)
            if not isinstance(extracted, str):
                extracted = str(extracted) if extracted else ""

            self._relay(200, json.dumps({
                "extracted_text": extracted,
                "source": "resume",
                "filename": filename
            }).encode())

        except Exception as e:
            self.send_json_error(500, f"Failed to extract resume: {str(e)}")

    def handle_extract_from_linkedin(self):
        """Extract profile-relevant information from LinkedIn profile (text paste only)."""
        try:
            body = self._read_json_body()
        except Exception:
            return self.send_json_error(400, "Malformed JSON.")

        linkedin_text = body.get("linkedin_text", "").strip()

        if not linkedin_text:
            return self.send_json_error(400, "Please paste your LinkedIn profile text. LinkedIn blocks direct URL access, so text paste is the only supported method.")

        try:
            text = linkedin_text

            if not text or not text.strip():
                return self._relay(200, json.dumps({"extracted_text": "", "source": "linkedin"}).encode())

            extracted = self._extract_profile_from_text(
                text, "linkedin", userid=(body.get("userid") or "").strip() or None)
            if not isinstance(extracted, str):
                extracted = str(extracted) if extracted else ""

            self._relay(200, json.dumps({
                "extracted_text": extracted,
                "source": "linkedin"
            }).encode())

        except Exception as e:
            self.send_json_error(500, f"Failed to extract LinkedIn profile: {str(e)}")

    def handle_user_submitted_opportunity(self):
        """Accept user-submitted opportunity data, dedupe by URL, and insert into
        opportunities table with is_active=false. Runs asynchronously."""
        try:
            body = self._read_json_body()
        except Exception:
            return self.send_json_error(400, "Malformed JSON.")

        name = (body.get("name") or "").strip()
        # NOT lowercased. The stored URL must stay exactly as the user gave it: 100 catalog
        # URLs contain uppercase path segments (…/CNIX.html) that 404 on a case-sensitive
        # host once folded. Case-insensitive matching happens in url_dedupe.match_key(),
        # which lowercases a throwaway comparison key instead of the value we persist.
        url = (body.get("url") or "").strip()
        opp_type = (body.get("type") or "").strip()
        section = (body.get("section") or "").strip()
        meta = (body.get("meta") or "").strip()
        fit = (body.get("fit") or "").strip()
        note = (body.get("note") or "").strip()
        important_dates = body.get("important_dates") or []
        requirements = body.get("requirements") or []
        apply_url = (body.get("apply_url") or "").strip()
        category = (body.get("category") or "").strip()
        # Best-effort attribution, same residual as everywhere else: signed-out submissions
        # arrive with no userid and land in the queue anonymous. A reviewer judging whether
        # a row is real benefits a lot from knowing who sent it.
        userid = (body.get("userid") or "").strip().lower() or None

        if not url or not name:
            return self.send_json_error(400, "URL and name are required.")

        # Run insertion in background thread
        def background_insert():
            try:
                self._insert_user_opportunity(
                    name, url, opp_type, section, meta, fit, note,
                    important_dates, requirements, apply_url, category, userid
                )
            except Exception as e:
                print(f"[User Opportunity] Background insertion failed: {e}")

        thread = threading.Thread(target=background_insert, daemon=True)
        thread.start()

        self._relay(200, json.dumps({
            "status": "queued",
            "message": "Opportunity queued for addition to database"
        }).encode())

    def _insert_user_opportunity(self, name, url, opp_type, section, meta, fit, note,
                                 important_dates, requirements, apply_url, category,
                                 userid=None):
        """Insert a user-submitted opportunity, deduped against the whole catalog.

        Dedupe is tiered, and only the top tier rejects (see url_dedupe for why the data
        forbids anything stronger):
          - exact match on the normalized URL -> skip the insert entirely;
          - anything weaker (sub-page of an existing entry, similar name, shared quiet
            domain, matching apply_url) -> insert anyway, with the candidate matches
            recorded on the row so the reviewer decides.
        The previous implementation compared a lowercased URL against un-normalized stored
        values with PostgREST `eq.`, which silently failed for the ~44% of catalog rows
        holding an uppercase character or a trailing slash.
        """
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            print("[User Opportunity] Supabase credentials not configured")
            return

        existing = self._catalog_dedupe_rows()
        if existing is None:
            print("[User Opportunity] Could not read catalog for dedupe — refusing to "
                  "insert blind (would risk a duplicate).")
            return

        exact, candidates = url_dedupe.find_duplicates(
            url, name, existing, apply_url=apply_url or None)
        if exact:
            print(f"[User Opportunity] Skipped — already in catalog as "
                  f"{exact.get('id')} ({exact.get('name')}): {url}")
            return
        if candidates:
            print(f"[User Opportunity] {len(candidates)} possible duplicate(s) for {url!r}; "
                  f"inserting for review anyway: "
                  + "; ".join(f"{c['id']} ({c['confidence']}: {c['reason']})"
                              for c in candidates))

        # Map section to type if needed
        if not opp_type:
            section_to_type = {
                "summerPrograms": "Program",
                "internships": "Internship",
                "researchCompetitions": "Research",
                "pureCompetitions": "Competition",
                "conferences": "Conference",
                "journals": "Journal",
            }
            opp_type = section_to_type.get(section, "Program")

        # Generate unique ID. The random suffix matters: a bare millisecond timestamp
        # collides for two submissions landing in the same millisecond, and since this runs
        # on a background thread of a ThreadingHTTPServer that is not hypothetical — the
        # loser would fail the primary key and be dropped with only a log line.
        generated_id = f"us{int(time.time() * 1000)}{random.randint(0, 999):03d}"

        # Quality note for the reviewer: even a genuinely new opportunity should not be
        # catalogued under its FAQ/about/apply page. 35 existing rows already are.
        quality_flags = []
        if url_dedupe.is_low_value_path(url):
            quality_flags.append("submitted URL is a sub-page (faq/about/apply/etc), "
                                 "not the opportunity's main page")

        # Only columns that actually exist on `opportunities`. This list was previously
        # wrong — it also set apply_url, apply_label, meta, requirements and description,
        # none of which are columns on that table. PostgREST rejects the WHOLE insert on one
        # unknown key, so every user submission 400'd and the feature never wrote a row.
        # Do not add a key here without confirming the column exists; the catalog schema is
        # narrower than the shape the AI extraction returns.
        row = {
            "id": generated_id,
            "name": name,
            "url": url,
            "type": opp_type,
            "summary": fit or meta or note,
            "is_active": False,
            "source": "user-submitted",
            "important_dates": important_dates if important_dates else None,
            "category": category or None,
        }
        # Columns from user_submissions_schema.sql. Split out so the insert can be retried
        # without them if that migration hasn't been run yet — see _insert_opportunity_row.
        # submission_payload keeps the extracted detail the catalog has nowhere to put
        # (apply_url, requirements, meta, note) so a reviewer can still see it, without
        # widening the student-facing catalog schema to hold it.
        submission_payload = {k: v for k, v in {
            "apply_url": apply_url or url,
            "meta": meta or None,
            "note": note or None,
            "fit": fit or None,
            "requirements": requirements or None,
            "section": section or None,
        }.items() if v}
        review_fields = {
            "moderation_status": "pending_review",
            "submitted_by": userid,
            "submitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "dup_candidates": (candidates or None),
            "quality_flags": (quality_flags or None),
            "submission_payload": (submission_payload or None),
        }

        self._insert_opportunity_row(row, review_fields, generated_id, name)

    def _catalog_dedupe_rows(self):
        """Every catalog row's id/name/url/apply_url, for dedupe. None if unreadable.

        Paginated past PostgREST's 1000-row max-rows cap — the catalog is ~1330 rows, so a
        single unpaginated request silently drops the tail and lets duplicates through.
        Includes is_active=false rows: something already sitting in the review queue (or
        rejected) is still a match, and re-inserting it would put it in front of the
        reviewer twice.
        """
        rows, offset, page_size = [], 0, 1000
        while True:
            # id,name,url only — `apply_url` is NOT a column on this table, and selecting it
            # 400s the whole request. find_duplicates() treats a missing apply_url as absent,
            # so its apply-url cross-check simply doesn't fire against catalog rows.
            page = _supabase_request("opportunities", params={
                "select": "id,name,url",
                "limit": str(page_size), "offset": str(offset)})
            if page is None:
                return None
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return rows

    def _insert_opportunity_row(self, row, review_fields, generated_id, name):
        """POST the row, retrying without the review columns if the migration is pending.

        Same degrade-gracefully shape as the user_costs table: until
        user_submissions_schema.sql is run in the Supabase SQL editor, PostgREST rejects
        the whole insert with PGRST204 ("column not found") rather than ignoring the
        unknown keys. Losing the submission entirely over a missing review column would be
        worse than losing the review metadata, so we retry with the base row and say so.
        """
        present = {k: v for k, v in review_fields.items() if v is not None}
        # Degrade one step at a time rather than straight to the base row: submission_payload
        # was added to the migration after the other review columns, so a database that has
        # run the first version should still keep its moderation metadata.
        ladder = [
            dict(row, **present),
            dict(row, **{k: v for k, v in present.items() if k != "submission_payload"}),
            row,
        ]
        for attempt, payload in enumerate(ladder):
            try:
                result = _supabase_request("opportunities", method="POST", data=[payload],
                                           extra_headers={"Prefer": "return=minimal"})
                if result is None:
                    raise RuntimeError("Supabase insert returned no response")
                if attempt == 0:
                    print(f"[User Opportunity] Inserted: {generated_id} - {name} "
                          f"(pending_review)")
                elif attempt == 1:
                    print(f"[User Opportunity] Inserted WITHOUT submission_payload: "
                          f"{generated_id} - {name}. Re-run user_submissions_schema.sql to "
                          f"add that column.")
                else:
                    print(f"[User Opportunity] Inserted WITHOUT review metadata: "
                          f"{generated_id} - {name}. Run user_submissions_schema.sql in "
                          f"the Supabase SQL editor to enable the moderation queue.")
                return True
            except Exception as e:
                if attempt < len(ladder) - 1:
                    print(f"[User Opportunity] Insert failed ({e}); retrying with fewer "
                          f"optional columns.")
                    continue
                print(f"[User Opportunity] Insert failed: {e}")
        return False

    def _extract_multipart_file(self, raw, boundary):
        """Extract filename and file bytes from multipart form data."""
        parts = raw.split(b"--" + boundary)
        for part in parts:
            if b"filename=" in part:
                filename_match = re.search(rb'filename="([^"]*)"', part)
                if not filename_match:
                    filename_match = re.search(rb"filename=([^;\r\n\s]+)", part)
                if not filename_match:
                    continue
                filename = filename_match.group(1).decode("utf-8", errors="ignore").strip('"')

                file_start = part.find(b"\r\n\r\n")
                if file_start == -1:
                    file_start = part.find(b"\n\n")
                    if file_start == -1:
                        continue
                    file_data = part[file_start + 2:]
                else:
                    file_data = part[file_start + 4:]

                file_data = file_data.rstrip(b"\r\n").rstrip(b"\n").rstrip(b"\r")
                if file_data.endswith(b"--"):
                    file_data = file_data[:-2].rstrip(b"\r\n")

                return (filename, file_data)
        return None

    def _extract_text_from_pdf(self, file_bytes):
        """Extract text from PDF bytes using PyPDF2 with fallback."""
        import io
        try:
            import PyPDF2
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            text = ""
            for page in pdf_reader.pages:
                extracted = page.extract_text() or ""
                text += extracted + "\n"
            return text if text.strip() else self._fallback_extract_text(file_bytes, "pdf")
        except Exception:
            return self._fallback_extract_text(file_bytes, "pdf")

    def _extract_text_from_docx(self, file_bytes):
        """Extract text from DOCX bytes using python-docx with fallback."""
        import io
        try:
            from docx import Document
            doc = Document(io.BytesIO(file_bytes))
            text = "\n".join([p.text for p in doc.paragraphs])
            return text if text.strip() else self._fallback_extract_text(file_bytes, "docx")
        except Exception:
            return self._fallback_extract_text(file_bytes, "docx")

    def _fallback_extract_text(self, file_bytes, filename):
        """Fallback text extraction when libraries aren't available."""
        try:
            text = file_bytes.decode('utf-8', errors='ignore')
            return text[:5000] if text else ""
        except:
            return ""


    def _extract_profile_from_text(self, text, source, userid=None):
        """Use Claude to extract profile-relevant information from text.

        Costed like every other interactive call: this discarded its usage block entirely
        until per-user accounting went in, which meant resume/LinkedIn imports were real
        Anthropic spend that showed up in no figure on the console at all.
        """
        if not ANTHROPIC_API_KEY:
            return self._mock_extract_profile(source, text)

        system_prompt = f"""You are helping a high school student build their profile for finding extracurricular opportunities.
Given the following {"resume" if source == "resume" else "LinkedIn profile"} text, extract ONLY information that would be relevant for building a profile of the student's academic interests, extracurricular activities, skills, projects, work experience, and leadership roles.

Ignore: personal contact information, employment dates, salary information, company-specific jargon, or any other non-relevant details.

Output the extracted information as concise, first-person-compatible statements (e.g., "I've worked on...", "I'm skilled in...", "I led..." — not third person or bullet points).
Keep it to 2-4 short paragraphs maximum. Do NOT include markdown, quotes, or preamble."""

        user_content = f"""Extract relevant profile information from this {"resume" if source == "resume" else "LinkedIn profile"}:

{text[:2000]}"""

        try:
            result = call_claude(
                system=system_prompt,
                user_content=user_content,
                api_key=ANTHROPIC_API_KEY,
                use_web_search=False,
                max_tokens=500,
                timeout=30
            )

            if isinstance(result, tuple) and len(result) >= 1:
                extracted_text = result[0]
                usage = result[1] if len(result) > 1 and isinstance(result[1], dict) else None
            else:
                extracted_text = result
                usage = None

            if usage:
                # Same rollup row as every other interactive Claude call, so the console's
                # app-spend total and the per-user breakdown both pick it up.
                record_interactive_cost_async("interactive_claude", usage, CLAUDE_MODEL,
                                              userid=userid, system=system_prompt)

            if extracted_text and str(extracted_text).strip():
                return str(extracted_text).strip()
            return self._mock_extract_profile(source, text)
        except Exception:
            return self._mock_extract_profile(source, text)

    def _mock_extract_profile(self, source, text):
        """Generate plausible mock extracted profile information."""
        if source == "resume":
            return """I have experience with Python and JavaScript programming. I've worked on several school projects including a machine learning application and a web application. I'm interested in STEM fields and have participated in coding competitions. I've interned with a local tech company where I worked on web development projects."""
        else:
            return """I'm passionate about computer science and artificial intelligence. I've led several club initiatives and participated in hackathons. My skills include web development, data analysis, and project management. I'm active in my school community and have volunteered with local nonprofits."""

    def handle_opportunities(self):
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            return self.send_json_error(500, "SUPABASE_URL/SUPABASE_ANON_KEY not configured.")
        try:
            data = fetch_opportunities()
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        self._relay(200, json.dumps(data).encode())

    def handle_admin_console(self):
        """Serve the admin console HTML."""
        html = get_admin_console_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.end_headers()
        self.wfile.write(html.encode())

    @staticmethod
    def _qs(query, key, default=None, cast=None):
        """First value of a parsed query-string key, optionally cast, default on failure."""
        values = query.get(key) if query else None
        if not values:
            return default
        value = values[0]
        if cast is None:
            return value
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default

    def handle_agents_config(self):
        """GET /api/agents/config — the static agent registry: names, units, API used,
        timing defaults. The console prefills its run modals and renders its timing
        reference table from this, so the schema stays the single source of truth."""
        self._relay(200, json.dumps({
            "agents": agents_config_payload(),
            "run_timeout_secs": AGENT_RUN_TIMEOUT_SECS,
            "setting_bounds": SETTING_BOUNDS,
            "recommended_min_delay": RECOMMENDED_MIN_DELAY,
        }, default=str).encode())

    def handle_agent_settings(self, agent_name):
        """PATCH /api/agents/settings/<agent> — save timing overrides from the console's
        timing table. Send null for a field to restore its built-in default."""
        defaults, error = save_agent_settings(agent_name, self._read_json_body() or {})
        if error:
            return self.send_json_error(400, error)
        self._relay(200, json.dumps({
            "ok": True, "agent": agent_name, "defaults": defaults,
            "builtin_defaults": AGENT_CONFIGS_SCHEMA[agent_name].get("defaults") or {},
        }, default=str).encode())

    def handle_agents_status(self):
        """GET /api/agents/status — live status of all four agents."""
        self._relay(200, json.dumps(get_all_agents_status(), default=str).encode())

    def handle_agents_history(self, query=None):
        """GET /api/agents/history?agent=&limit=&days= — recent runs, newest first."""
        history = get_agent_history(
            agent=self._qs(query, "agent"),
            limit=self._qs(query, "limit", 50, int),
            days=self._qs(query, "days", None, int),
        )
        self._relay(200, json.dumps(history, default=str).encode())

    def handle_agents_summary(self, query=None):
        """GET /api/agents/summary?days= — KPI totals and the per-day cost series."""
        days = self._qs(query, "days", 30, int) or 30
        summary = get_agents_summary(days=max(1, min(days, 365)))
        self._relay(200, json.dumps(summary, default=str).encode())

    def handle_snapshots_list(self):
        """GET /api/agents/snapshots — dry-run snapshots on disk that could be committed."""
        self._relay(200, json.dumps(
            {"snapshots": dryrun_common.list_snapshots()}, default=str).encode())

    def handle_snapshot_commit(self):
        """POST /api/agents/snapshots/commit — replay a snapshot into the catalog.

        `preview: true` resolves and counts everything without writing, which is what the
        console shows before asking the operator to confirm. Costs nothing either way: the
        API calls were already made and paid for by the run that produced the file.
        """
        body = self._read_json_body()
        file_name = (body.get("file") or "").strip()
        preview = bool(body.get("preview"))
        result = commit_dryrun_snapshot(file_name, dry=preview)
        self._relay(200 if result.get("ok") else 400,
                    json.dumps(result, default=str).encode())

    def handle_pending_list(self, query=None):
        """GET /api/agents/pending?limit=&source=&status= — the is_active=false queue.

        status is queue (default) / rejected / all — see list_pending_opportunities.
        """
        limit = self._qs(query, "limit", 500, int) or 500
        status = self._qs(query, "status") or "queue"
        if status not in ("queue", "rejected", "all"):
            return self.send_json_error(400, f"Unknown status filter: {status}")
        result = list_pending_opportunities(limit=limit, source=self._qs(query, "source"),
                                            status=status)
        self._relay(200 if result.get("ok") else 502,
                    json.dumps(result, default=str).encode())

    def handle_opportunity_search(self, query=None):
        """GET /api/agents/opportunities/search?q=&limit= — pick a surviving row.

        Localhost-only with the rest of /api/agents/*. Read-only, and searches the whole
        catalog including inactive rows: the survivor of two queued scrapes is itself queued.
        """
        result = search_opportunities(self._qs(query, "q"),
                                      self._qs(query, "limit", 25, int) or 25)
        self._relay(200, json.dumps(result, default=str).encode())

    def handle_pending_update(self):
        """POST /api/agents/pending/update — edit one queued row's fields in place.

        Only the whitelist in EDITABLE_OPPORTUNITY_FIELDS, and only while the row is still
        inactive: this is "fix it before students see it", not a catalog editor.
        """
        body = self._read_json_body()
        result = update_pending_opportunity(body.get("id"), body.get("fields") or {})
        self._relay(200 if result.get("ok") else 400,
                    json.dumps(result, default=str).encode())

    def handle_pending_moderate(self):
        """POST /api/agents/pending/moderate — record a human verdict on an id list.

        Separate from /activate on purpose: activation says "students see this", this says
        "a person adjudicated this". Rejecting hides the row from the queue but never
        deletes it — the row keeps blocking re-submission of the same URL, and moderating
        it back to pending_review undoes the decision.
        """
        body = self._read_json_body()
        result = moderate_opportunities(
            body.get("ids") or [],
            (body.get("status") or "").strip(),
            duplicate_of=body.get("duplicate_of"))
        self._relay(200 if result.get("ok") else 400,
                    json.dumps(result, default=str).encode())

    def handle_pending_activate(self):
        """POST /api/agents/pending/activate — flip is_active on an explicit id list.

        `active: false` deactivates instead, so a row activated by mistake can be put back
        without a database console.
        """
        body = self._read_json_body()
        active = body.get("active", True)
        result = activate_opportunities(body.get("ids") or [], active=bool(active))
        self._relay(200 if result.get("ok") else 400,
                    json.dumps(result, default=str).encode())

    def handle_agents_user_costs(self, query=None):
        """GET /api/agents/user-costs?days=&limit= — per-user breakdown of app spend.

        Localhost-only along with the rest of /api/agents/*, which matters more here than
        elsewhere on this router: the response carries every account's name, email and
        subscription status alongside what they cost.
        """
        days = self._qs(query, "days", 30, int) or 30
        limit = self._qs(query, "limit", 200, int) or 200
        self._relay(200, json.dumps(
            get_user_costs(days=max(1, min(days, 365)), limit=max(1, min(limit, 1000))),
            default=str).encode())

    def handle_agents_billed(self, query=None):
        """GET /api/agents/billed?days= — provider-billed spend vs our local estimate."""
        days = self._qs(query, "days", 30, int) or 30
        self._relay(200, json.dumps(get_billed_costs(max(1, min(days, 365))),
                                     default=str).encode())

    def handle_agents_log(self, query=None):
        """GET /api/agents/log?agent=&since= — incremental live output for the console."""
        agent = self._qs(query, "agent")
        if agent not in AGENT_CONFIGS_SCHEMA:
            return self.send_json_error(400, f"Unknown agent: {agent}")
        since = self._qs(query, "since", 0, int) or 0
        self._relay(200, json.dumps(get_agent_log(agent, since), default=str).encode())

    def handle_agents_preview(self):
        """POST /api/agents/preview — resolve what a run WOULD touch. Free: the script
        exits before its first API call and writes nothing."""
        body = self._read_json_body()
        agent_name = (body.get("agent") or "").strip()
        if agent_name not in AGENT_CONFIGS_SCHEMA:
            return self.send_json_error(400, f"Unknown agent: {agent_name}")
        result = preview_agent(agent_name, body.get("config") or {})
        self._relay(200 if result.get("ok") else 400, json.dumps(result, default=str).encode())

    def handle_agents_run(self):
        """POST /api/agents/run — start an agent in the background, return immediately.

        Refuses rather than queues when the agent is already running, or when another
        agent holding the shared Gemini web-search lock is running: the second process
        would fail fast on the lockfile anyway, and saying so up front is more useful than
        a run that dies a minute in.
        """
        body = self._read_json_body()
        agent_name = (body.get("agent") or "").strip()
        if agent_name not in AGENT_CONFIGS_SCHEMA:
            return self.send_json_error(400, f"Unknown agent: {agent_name}")

        if is_agent_running(agent_name):
            return self.send_json_error(409, f"{agent_name} is already running.")

        if AGENT_CONFIGS_SCHEMA[agent_name].get("uses_gemini_search"):
            blocker = running_gemini_search_agent(exclude=agent_name)
            if blocker:
                name = AGENT_CONFIGS_SCHEMA[blocker]["name"]
                return self.send_json_error(
                    409, f"{name} is running and holds the shared Gemini web-search lock. "
                         f"Only one search-enabled agent can run at a time.")

        config = body.get("config") or {}
        argv = build_agent_args(agent_name, config)
        threading.Thread(
            target=lambda: run_agent_subprocess(agent_name, config), daemon=True).start()

        self._relay(202, json.dumps({
            "ok": True,
            "agent": agent_name,
            "dry_run": bool(config.get("dryRun")),
            "argv": argv[1:],  # echo the real flags back so the console can show them
            "message": f"{AGENT_CONFIGS_SCHEMA[agent_name]['name']} started",
        }, default=str).encode())

    # ---------- Scraper seed CRUD ----------

    def handle_seeds_list(self, query=None):
        """GET /api/seeds?mode= — every angle, enabled or not, with yield stats."""
        rows = list_seeds(mode=self._qs(query, "mode"))
        if rows is None:
            return self.send_json_error(
                502, "Could not read scraper_seeds. Has the table been created? "
                     "See scraper_seeds_schema.sql.")
        self._relay(200, json.dumps({"ok": True, "seeds": rows}, default=str).encode())

    def handle_seed_create(self):
        body, parse_error = self._read_json_body_strict()
        if parse_error:
            return self.send_json_error(400, parse_error)
        row, error = create_seed(body or {})
        if error:
            return self.send_json_error(400, error)
        self._relay(201, json.dumps({"ok": True, "seed": row}, default=str).encode())

    def handle_seed_update(self, seed_id):
        body, parse_error = self._read_json_body_strict()
        if parse_error:
            return self.send_json_error(400, parse_error)
        row, error = update_seed(seed_id, body or {})
        if error:
            return self.send_json_error(400, error)
        self._relay(200, json.dumps({"ok": True, "seed": row}, default=str).encode())

    def handle_seed_delete(self, seed_id):
        ok, error = delete_seed(seed_id)
        if not ok:
            return self.send_json_error(502, error or "Delete failed")
        self._relay(200, json.dumps({"ok": True, "deleted": seed_id}).encode())

    def handle_deadline_check(self, opp_id, query=None):
        """GET /api/opportunities/<id>/deadline — on-demand, cross-user-cached deadline
        check. See the module-level comment above GEMINI_API_KEY for the full rationale.
        Serves cached status/important_dates straight from Supabase if last_checked_at is under
        DEADLINE_STALE_DAYS old; otherwise runs a fresh Claude Haiku web_search check (reusing
        check_deadlines.py's check_one()), re-caches it, and returns the fresh result. Rejects
        silent search skips (searches == 0) and falls back to cached value if no searches occurred."""
        # Gate before any Supabase or Claude work: a fresh check is a paid web-search
        # call. userid arrives on the query string (this is a GET).
        if self._subscription_blocks(self._qs(query, "userid")):
            return
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            return self.send_json_error(500, "SUPABASE_URL/SUPABASE_SERVICE_KEY not configured.")
        try:
            opp = get_opportunity_for_deadline_check(opp_id)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if not opp:
            return self.send_json_error(404, "Opportunity not found.")

        if deadline_cache_is_fresh(opp.get("last_checked_at")):
            payload = cached_deadline_payload(opp, "cached")
            # Log the cached check (non-blocking)
            log_deadline_check(opp_id, "cached", opp.get("status"), None, None, opp.get("was_estimated"))
            self._relay(200, json.dumps(payload).encode())
            return

        if not ANTHROPIC_API_KEY:
            payload = mock_deadline_check_payload(opp)
            # Log the mock check (non-blocking)
            log_deadline_check(opp_id, "mock", payload.get("status"), 0, 0.0, payload.get("was_estimated"), "Mock mode - no API key")
            self._relay(200, json.dumps(payload).encode())
            return

        try:
            info, _cost, searches = check_deadline_one(opp, ANTHROPIC_API_KEY)

            status = info.get("status") if info.get("status") in DEADLINE_VALID_STATUS else "unknown"
            important_dates = info.get("important_dates") or []
            if not isinstance(important_dates, list):
                important_dates = []
            important_dates = [d for d in important_dates if isinstance(d, dict) and d.get("date_iso")]

            # Distinguish between real searches and silent skips
            if searches == 0:
                source_flag = "fresh, silent search"
                print(f"[WARN] Deadline check for {opp_id} had zero web searches (silent skip); returning fresh data marked as unverified.")
            else:
                source_flag = "fresh, real search"
                print(f"[INFO] Deadline check for {opp_id}: {searches} web search(es) performed.")

            patch = {
                "status": status,
                "important_dates": important_dates,
                "was_estimated": bool(info.get("was_estimated")),
                "important_date_note": info.get("important_date_note"),
                "last_checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            patch_opportunity_deadline(opp_id, patch)
            response = {**patch, "source": source_flag}
            # Log the fresh check (non-blocking)
            log_deadline_check(opp_id, source_flag, status, searches, _cost, bool(info.get("was_estimated")))
            record_user_cost_async(self._qs(query, "userid"), "deadline_check",
                                   "deadline_check", cost=_cost, searches=searches,
                                   model=DEADLINE_CHECK_MODEL)
            self._relay(200, json.dumps(response).encode())
        except Exception as e:
            # Claude API error or network hiccup: degrade to whatever was cached before, even if
            # stale, rather than failing the tracker add/load outright. A stale-but-present
            # deadline beats none when the live check can't complete right now.
            print(f"[WARN] Deadline check failed for {opp_id}: {e}")
            payload = cached_deadline_payload(opp, "stale-fallback")
            # Log the failed check (non-blocking)
            log_deadline_check(opp_id, "stale-fallback", opp.get("status"), None, None, opp.get("was_estimated"), f"Error: {str(e)[:100]}")
            self._relay(200, json.dumps(payload).encode())

    def handle_messages(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
        if self._subscription_blocks(self._userid_from_body(raw_body)):
            return
        if GEMINI_API_KEY:
            self.proxy_to_gemini(raw_body)
        else:
            self.mock_response(raw_body)

    @staticmethod
    def _userid_from_body(raw_body):
        """userid out of a raw body that's already been read off the wire."""
        try:
            return json.loads(raw_body).get("userid")
        except Exception:
            return None

    def mock_response(self, raw_body):
        try:
            payload = json.loads(raw_body)
            system = payload.get("system", "") or ""
            user_content = payload.get("userContent", "")
            userid = payload.get("userid")
        except Exception:
            system, user_content, userid = "", "", None
        text = generate_mock_text(system, user_content)
        data = json.dumps({"content": [{"type": "text", "text": text}]}).encode()
        self._relay(200, data)
        log_conversation_async(userid, self.client_address[0], "mock", system,
                                user_content if isinstance(user_content, str) else json.dumps(user_content),
                                text)

    def proxy_to_gemini(self, raw_body):
        # Client (script.js's callGemini()) sends {system, userContent, useWebSearch, userid} —
        # a plain, backend-agnostic shape rather than Anthropic's content-block/messages
        # envelope, since server.py is now the only place that needs to know the wire
        # format of whichever model API it's actually calling. Reuses gemini_common's
        # call_gemini() (same request-building, forced-search nudge, and thinking-budget
        # handling as the offline batch scripts) rather than re-implementing it here.
        try:
            payload = json.loads(raw_body)
        except Exception:
            return self.send_json_error(400, "Malformed request body.")
        system = payload.get("system", "") or ""
        user_content = payload.get("userContent", "")
        user_content = user_content if isinstance(user_content, str) else json.dumps(user_content)
        userid = payload.get("userid")
        use_web_search = bool(payload.get("useWebSearch"))
        try:
            text, usage = call_gemini(
                system, user_content, GEMINI_API_KEY,
                use_web_search=use_web_search, max_tokens=MESSAGES_MAX_TOKENS,
                model=MESSAGES_MODEL,
            )
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.read())
            return
        except Exception as e:
            self.send_json_error(502, str(e))
            return
        # Re-wrapped into the same {"content":[{"type":"text","text":...}]} envelope
        # mock_response() already produces, so callGemini()'s response parsing in
        # script.js doesn't need to branch on live vs. mock mode.
        data = json.dumps({"content": [{"type": "text", "text": text}]}).encode()
        self._relay(200, data)
        # Logging happens after the real response is already relayed to the browser,
        # and is best-effort — a parse hiccup here must never affect the actual API
        # call the user is waiting on.
        log_conversation_async(userid, self.client_address[0], "live", system, user_content, text)
        # Cost of this call, rolled into today's interactive total. Without this the
        # console's "spend" figure silently excluded all app traffic.
        record_interactive_cost_async("interactive_gemini", usage, MESSAGES_MODEL,
                                      userid=userid, system=system)

    def handle_messages_claude(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
        if self._subscription_blocks(self._userid_from_body(raw_body)):
            return
        if ANTHROPIC_API_KEY:
            self.proxy_to_anthropic(raw_body)
        else:
            self.mock_response(raw_body)

    def proxy_to_anthropic(self, raw_body):
        # Client (script.js's callClaude()) sends the same plain {system, userContent,
        # useWebSearch, userid} shape as callGemini() — translated into Anthropic's content-block/
        # messages envelope here, so the client stays backend-agnostic either way.
        try:
            payload = json.loads(raw_body)
        except Exception:
            return self.send_json_error(400, "Malformed request body.")
        system = payload.get("system", "") or ""
        user_content = payload.get("userContent", "")
        user_content = user_content if isinstance(user_content, str) else json.dumps(user_content)
        userid = payload.get("userid")
        use_web_search = bool(payload.get("useWebSearch"))
        body = {
            "model": CLAUDE_MODEL,
            "max_tokens": CLAUDE_MAX_TOKENS,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_content}],
        }
        if use_web_search:
            body["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
        req = urllib.request.Request(
            ANTHROPIC_URL,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                self._relay(resp.status, data)
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.read())
            return
        except Exception as e:
            self.send_json_error(502, str(e))
            return
        # Logging happens after the real response is already relayed to the browser, and
        # is best-effort — a parse hiccup here must never affect the actual API call the
        # user is waiting on.
        try:
            resp_json = json.loads(data)
            response_text = "\n".join(
                b.get("text", "") for b in resp_json.get("content", []) if b.get("type") == "text"
            )
            # Anthropic's usage block uses input_tokens/output_tokens; map it onto the
            # shape estimate_cost() expects so both surfaces cost the same way.
            u = resp_json.get("usage") or {}
            record_interactive_cost_async("interactive_claude", {
                "input_tokens": u.get("input_tokens", 0),
                "output_tokens": u.get("output_tokens", 0),
                "server_tool_use": u.get("server_tool_use") or {},
            }, CLAUDE_MODEL, userid=userid, system=system)
        except Exception:
            response_text = ""
        log_conversation_async(userid, self.client_address[0], "live", system, user_content, response_text)

    def handle_subscription_status(self):
        """Get current subscription status for a user."""
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        if not userid:
            return self.send_json_error(400, "Missing userid.")
        try:
            record = get_user(userid)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if not record:
            return self.send_json_error(404, "User not found.")

        self._relay(200, json.dumps(subscription_state(
            ensure_trial_started(userid, record))).encode())

    def handle_subscription_checkout(self):
        """Create a Stripe checkout session for subscription."""
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        email = (body.get("email") or "").strip()
        promo_code = (body.get("promo_code") or "").strip()
        success_url = (body.get("success_url") or "").strip()
        cancel_url = (body.get("cancel_url") or "").strip()

        if not all([userid, email, success_url, cancel_url]):
            return self.send_json_error(400, "Missing required fields: userid, email, success_url, cancel_url.")

        try:
            record = get_user(userid)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if not record:
            return self.send_json_error(404, "User not found.")

        try:
            # Get or create Stripe customer
            customer_id, error = get_or_create_customer(userid, email, f"{record.get('first_name', '')} {record.get('last_name', '')}")
            if error:
                return self.send_json_error(502, f"Failed to create Stripe customer: {error}")

            # Create checkout session
            session_id, checkout_url, error = create_checkout_session(
                customer_id, email, success_url, cancel_url, promo_code)
            if error:
                return self.send_json_error(502, f"Failed to create checkout session: {error}")
            if not checkout_url:
                return self.send_json_error(502, "Stripe did not return a checkout URL.")

            # Update user with Stripe customer ID
            update_subscription(userid, {"stripe_customer_id": customer_id})

            self._relay(200, json.dumps({
                "session_id": session_id,
                "checkout_url": checkout_url,
            }).encode())
        except Exception as e:
            return self.send_json_error(502, f"Subscription error: {str(e)}")

    def handle_subscription_cancel(self):
        """Cancel a subscription."""
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        if not userid:
            return self.send_json_error(400, "Missing userid.")

        try:
            record = get_user(userid)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if not record:
            return self.send_json_error(404, "User not found.")

        stripe_subscription_id = record.get("stripe_subscription_id")
        if not stripe_subscription_id:
            return self.send_json_error(400, "No active Stripe subscription to cancel.")

        try:
            result, error = cancel_subscription(stripe_subscription_id)
            if error:
                return self.send_json_error(502, f"Failed to cancel subscription: {error}")

            # Access runs to the end of the period already paid for. Record when that is
            # so subscription_state() can keep letting them in until then; without it a
            # canceled account would be locked out the moment it cancels.
            period_end = (result or {}).get("current_period_end")
            updates = {"subscription_status": "canceled"}
            if period_end:
                updates["subscription_end_at"] = datetime.datetime.fromtimestamp(
                    period_end, datetime.timezone.utc).isoformat()
            update_subscription(userid, updates)

            self._relay(200, json.dumps({
                "ok": True,
                "message": "Subscription canceled",
                "subscription_end_at": updates.get("subscription_end_at"),
            }).encode())
        except Exception as e:
            return self.send_json_error(502, f"Subscription error: {str(e)}")

    def handle_redeem_promo(self):
        """POST /api/subscription/redeem-promo — actually apply a 'grant' promo code.

        Distinct from validate-promo, which is a read: this one writes. It sets the
        account's status and extends its access window, and records the code in
        `promo_codes_used` so it cannot be redeemed twice.

        Only 'grant' codes go through here. A 'checkout' discount (FREEMONTH, WELCOME10)
        means nothing without Stripe in the loop — it is handed to the Checkout Session
        instead, so redeeming one here would take the code away and give nothing back.
        """
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        code = (body.get("promo_code") or "").strip().upper()
        if not userid or not code:
            return self.send_json_error(400, "Missing userid or promo_code.")

        promo_data, error = validate_promo_code(code)
        if error:
            return self.send_json_error(400, error)
        if promo_kind(promo_data) != "grant":
            return self.send_json_error(400, "That code is applied at checkout, not here.")

        status = promo_data.get("status")
        grant_days = promo_data.get("grant_days")
        if status not in GRANTABLE_STATUSES or not grant_days:
            # A malformed entry in PROMO_CODES, not anything the user did.
            return self.send_json_error(500, "That promo code is misconfigured.")

        try:
            record = get_user(userid)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        if not record:
            return self.send_json_error(404, "User not found.")

        used = list(record.get("promo_codes_used") or [])
        if code in used:
            return self.send_json_error(400, "You have already used this promo code.")

        # A paying subscriber redeeming a beta code would be *downgraded* — their paid
        # status replaced by a 7-day window. Refuse rather than take something away.
        if (record.get("subscription_status") or "trial") == "active":
            return self.send_json_error(400, "Your subscription is already active — save "
                                             "this code for later.")

        # Extend from whichever is later, now or the current window, so the grant adds to
        # whatever trial is left instead of replacing it. A lapsed account extends from
        # now, which is what un-expires it.
        current_end = (record.get("subscription_end_at")
                       if (record.get("subscription_status") or "") == "beta"
                       else record.get("trial_ends_at"))
        new_end = extend_from(current_end, grant_days)
        used.append(code)
        try:
            update_subscription(userid, {
                "subscription_status": status,
                "subscription_end_at": new_end,
                "promo_codes_used": used,
            })
            record = get_user(userid)
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")

        self._relay(200, json.dumps({
            "ok": True,
            "applied": code,
            "description": promo_data.get("description"),
            "subscription": subscription_state(record),
        }).encode())

    def handle_validate_promo(self):
        """Validate a promo code."""
        body = self._read_json_body()
        promo_code = (body.get("promo_code") or "").strip()
        userid = (body.get("userid") or "").strip().lower()

        if not promo_code:
            return self.send_json_error(400, "Missing promo_code.")

        promo_data, error = validate_promo_code(promo_code)
        if error:
            return self.send_json_error(400, error)

        # Check if user has already used this code
        if userid:
            try:
                record = get_user(userid)
                if record:
                    used_codes = record.get("promo_codes_used") or []
                    if promo_code.upper() in used_codes:
                        return self.send_json_error(400, "You have already used this promo code.")
            except Exception:
                pass  # Continue anyway

        self._relay(200, json.dumps({
            "valid": True,
            # "grant" means the client can redeem it right now against redeem-promo;
            # "checkout" means it only takes effect once the user reaches Stripe.
            "kind": promo_kind(promo_data),
            "description": promo_data.get("description"),
            "discount_months": promo_data.get("discount_months"),
            "discount_percent": promo_data.get("discount_percent"),
        }).encode())

    def _subscription_blocks(self, userid):
        """402 the request if this account's trial has lapsed with nothing paid.

        The client hides the whole app behind a paywall once has_access goes false, so
        in normal use this never fires. It exists because the client-side lock is a
        screen, not a control — these are the four endpoints that spend real money per
        call, and a lapsed account reaching one directly should not be able to bill us.

        A missing userid is NOT blocked. Signed-out calls can't be identified at all
        (the same residual /api/agents/user-costs reports as unattributed), and the app
        requires a login before any of these are reachable through the UI.
        """
        userid = (userid or "").strip().lower()
        if not userid:
            return False
        try:
            record = get_user(userid)
        except Exception:
            return False  # can't reach Supabase — fail open rather than lock everyone out
        if not record:
            return False
        state = subscription_state(record)
        if state["has_access"]:
            return False
        # Say which of the three ways it lapsed — "your free trial has ended" is actively
        # wrong for someone whose card just failed.
        if state["status"] == "past_due":
            reason = ("We could not charge your card. Update your payment details to "
                      "restore access to Wingman's AI features.")
        elif state["status"] == "canceled":
            reason = ("Your subscription has ended. Resubscribe to keep using "
                      "Wingman's AI features.")
        elif state["status"] == "beta":
            reason = ("Your beta access has ended. Subscribe to keep using Wingman's "
                      "AI features.")
        else:
            reason = ("Your free trial has ended. Subscribe to keep using Wingman's "
                      "AI features.")
        self.send_json_error(402, reason)
        return True

    def _relay(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json_error(self, code, message):
        payload = json.dumps({"error": message}).encode()
        self._relay(code, payload)


if __name__ == "__main__":
    mode = "LIVE (using GEMINI_API_KEY)" if GEMINI_API_KEY else "MOCK (no GEMINI_API_KEY set — fabricating responses)"
    claude_mode = "LIVE (using ANTHROPIC_API_KEY)" if ANTHROPIC_API_KEY else "MOCK (no ANTHROPIC_API_KEY set — fabricating responses)"
    server = ThreadingHTTPServer(("", PORT), Handler)
    print(f"Serving http://localhost:{PORT}  [messages: {mode}] [messages-claude: {claude_mode}]")
    server.serve_forever()

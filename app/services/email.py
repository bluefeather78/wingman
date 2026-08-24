"""Lifecycle email: when a message goes out, to whom, exactly once, and what happened.

Three emails only — welcome at signup, a reminder before the free trial ends, and a
cancellation confirmation. app/services/email_templates.py owns what they SAY; this module
owns whether they are sent at all.

WHY A PROVIDER RATHER THAN SMTP FROM HERE. What Resend supplies is SPF/DKIM/DMARC on the
sending domain, a warmed IP, bounce and complaint handling, and a suppression list. None of
that is rebuildable in a repo, and without it mail from a cold domain to a population living
on Gmail and school Google Workspace accounts lands in spam. What is deliberately NOT
outsourced is the user roster: nothing here syncs accounts to a third party. Resend is
handed one address at a time, at the moment of sending, which is the smallest disclosure
that gets an email delivered. Anything that maintains a contact list over there would mean
continuously exporting the names and addresses of minors, and would need a privacy-policy
edit (build_legal.py) and a TERMS_VERSION bump before it could be switched on.

THE CLAIM, WHICH IS THE WHOLE DESIGN. A row is written to email_sends BEFORE Resend is
called, and the table's unique (userid, kind, dedupe_key) is what makes a repeated sweep
safe: the second attempt loses the insert and skips. A log written AFTER the send cannot do
this — the window between "Resend accepted it" and "we recorded it" is exactly where a crash
produces a second copy in a real student's inbox. The cost is a crashed send leaving a row
stuck at 'sending' that is never retried automatically. That is the intended direction: a
stuck row is visible in the console and clearable by hand, a duplicate cannot be un-sent.

FAILING TO CLAIM MEANS NOT SENDING, INCLUDING WHEN THE TABLE IS ABSENT. Until
email_schema.sql is run every claim fails and nothing is ever sent. That reads as the
feature being switched off, which is correct — the alternative is sending with no record of
having sent, and a daily sweep that cannot remember mails the same student every morning.

MOCK MODE. With no RESEND_API_KEY the whole path runs offline, matching the convention
GEMINI_API_KEY/ANTHROPIC_API_KEY already set. A mock send writes NO claim row: a claim would
suppress the real send once a key is configured, so developing offline would silently cost
real users their welcome email.
"""
import datetime
import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from app.config import (
    RESEND_API_KEY, RESEND_URL, RESEND_USER_AGENT, EMAIL_FROM, EMAIL_REPLY_TO,
    EMAIL_APP_URL, EMAIL_SETUP_SQL, TRIAL_REMINDER_DAYS, JWT_SECRET,
)
from app.core import (
    _supabase_request_strict, _missing_table_error, _error_body, get_user_account,
    subscription_state,
)
from subscription_common import TRIAL_DAYS
from app.services import email_templates
from app.services.email_templates import EMAIL_KINDS

SENDS_TABLE = "email_sends"

# PostgREST's code for a unique-constraint violation. This is the SUCCESS path of the claim
# race, not an error: it means somebody else already owns this send.
_UNIQUE_VIOLATION = "23505"

# The codes that mean "email_schema.sql has not been run" — an unknown TABLE (PGRST205 /
# 42P01) or an unknown COLUMN (PGRST204 / 42703, i.e. a table created in an older shape,
# which is what the ALTER block in that file repairs). Mirrors _missing_table_error in
# app.core; kept as a set here because the claim path must classify from an already-read
# body rather than from the exception (see _claim).
_MISSING_SCHEMA_CODES = {"PGRST205", "42P01", "PGRST204", "42703"}

# How long a row may sit in 'sending' before the console calls it stuck rather than in
# flight. A Resend call takes under a second; ten minutes is far past any real latency.
STUCK_AFTER_SECONDS = 600


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    try:
        when = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return when.replace(tzinfo=datetime.timezone.utc) if when.tzinfo is None else when


# ---------------- Unsubscribe links ----------------
#
# An opt-out link is reachable by anyone who receives it, so the userid alone cannot be the
# credential — that would let anybody unsubscribe anybody by guessing an id. The link
# carries an HMAC of the userid under JWT_SECRET, which is the secret this deployment
# already has; a separate one would be another thing to set on Render and to fail closed on.
# It is deliberately NOT a JWT: an unsubscribe link sits in a mailbox for years and must not
# expire, and there is nothing here worth the expiry machinery.

def unsubscribe_token(userid):
    if not JWT_SECRET:
        return ""
    return hmac.new(JWT_SECRET.encode("utf-8"),
                    f"unsub:{userid}".encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def verify_unsubscribe_token(userid, token):
    expected = unsubscribe_token(userid)
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, str(token))


def unsubscribe_url(userid):
    query = urllib.parse.urlencode({"u": userid, "t": unsubscribe_token(userid)})
    return f"{EMAIL_APP_URL}/api/email/unsubscribe?{query}"


# ---------------- Context: the numbers the templates display ----------------

def _safe_display_date(value):
    """%-d is glibc-only and blows up on Windows, where this repo is developed. Fall back
    rather than letting a strftime error take out a send."""
    when = _parse_iso(value)
    if not when:
        return None
    try:
        return when.strftime("%B %-d, %Y")
    except ValueError:
        return when.strftime("%B %d, %Y").replace(" 0", " ")


def build_context(kind, record):
    """Derive every display value an email needs from a users row.

    Deliberately the ONLY place these are computed, so the console preview and the real
    send cannot disagree about the most important number in the message (how many days are
    left). Returns a plain dict — templates never see a raw users row.
    """
    state = subscription_state(record or {})
    ctx = {
        "first_name": (record or {}).get("first_name") or "",
        "userid": (record or {}).get("userid") or "",
    }
    if kind == "welcome":
        # Only stated when known: a hardcoded "7 days" is wrong for any account that
        # redeemed a grant code before opening the email.
        ctx["trial_days"] = state.get("days_left") if state.get("status") == "trial" else None
    elif kind == "trial_ending":
        ctx["days_left"] = state.get("days_left")
        ctx["trial_ends_display"] = _safe_display_date(state.get("trial_ends_at"))
    elif kind == "goodbye":
        ctx["access_ends_display"] = _safe_display_date(state.get("subscription_end_at"))
    return ctx


def render_for(kind, record):
    """(subject, html, text) for one user. Used by the send path AND by the console
    preview, so what an operator reviews is byte-identical to what is sent."""
    ctx = build_context(kind, record)
    return email_templates.render(kind, ctx, unsubscribe_url((record or {}).get("userid") or ""))


# ---------------- The claim ----------------

def _claim(userid, kind, dedupe_key, email, subject):
    """Reserve this send. Returns (row, reason).

    row is the inserted email_sends row on success. reason is why not on failure:
      'already_sent'  — the unique constraint refused it; somebody already owns this send
      'setup'         — email_schema.sql has not been run
      'error: ...'    — anything else, and it means DO NOT SEND
    """
    payload = {
        "userid": userid,
        "kind": kind,
        "dedupe_key": dedupe_key or "",
        "email": email,
        "subject": subject,
        "state": "sending",
        "provider": "resend",
        "claimed_at": _now().isoformat(),
    }
    try:
        rows = _supabase_request_strict(
            SENDS_TABLE, "POST", data=payload,
            extra_headers={"Prefer": "return=representation"})
        return (rows[0] if rows else {"userid": userid, "kind": kind}), None
    except urllib.error.HTTPError as e:
        # _error_body consumes the response stream — it is readable EXACTLY ONCE. So the
        # body is read here and both classifications are made from the parsed dict;
        # calling _missing_table_error(e) after this would re-read an exhausted stream, get
        # {}, and report a missing table as a generic error, which is precisely the case
        # that most needs to name the .sql file.
        body = _error_body(e) or {}
        code = body.get("code")
        if code == _UNIQUE_VIOLATION:
            return None, "already_sent"
        if code in _MISSING_SCHEMA_CODES:
            return None, "setup"
        return None, f"error: {body.get('message') or e}"
    except Exception as e:
        return None, f"error: {e}"


def _finish(row, state, message_id=None, error=None):
    """Close out a claimed row. Best-effort: the mail has already gone (or failed), and
    losing the bookkeeping must not raise into a signup or a cancel. It is logged loudly
    because a row left in 'sending' after a successful send looks like a crash."""
    row_id = (row or {}).get("id")
    if row_id is None:
        return
    updates = {"state": state}
    if message_id:
        updates["provider_message_id"] = message_id
    if error:
        updates["error"] = str(error)[:500]
    if state == "sent":
        updates["sent_at"] = _now().isoformat()
    try:
        _supabase_request_strict(SENDS_TABLE, "PATCH",
                                 params={"id": f"eq.{row_id}"}, data=updates)
    except Exception as e:
        print(f"[WARN] email_sends {row_id} left at 'sending' — could not record {state}: {e}")


def release_claim(row):
    """Delete a claim that was never handed to the provider.

    Only for the pre-send guards (opted out, no address): those decide NOT to send after
    the row exists, and leaving it behind would permanently suppress a legitimate later
    send — an account that opts back in would never get another trial reminder. Never call
    this after Resend has been given the message; a duplicate cannot be un-sent.
    """
    row_id = (row or {}).get("id")
    if row_id is None:
        return
    try:
        _supabase_request_strict(SENDS_TABLE, "DELETE", params={"id": f"eq.{row_id}"})
    except Exception as e:
        print(f"[WARN] Could not release email_sends claim {row_id}: {e}")


# ---------------- The provider call ----------------

def _resend_post(to_email, subject, html_body, text_body):
    """POST one email to Resend. Returns (message_id, error).

    Raw urllib rather than the SDK, matching subscription_common.py's Stripe client and
    this repo's stdlib-only convention.
    """
    payload = {
        "from": EMAIL_FROM,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    if EMAIL_REPLY_TO:
        payload["reply_to"] = EMAIL_REPLY_TO
    req = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # REQUIRED, not politeness. Resend sits behind Cloudflare, whose WAF rejects
            # urllib's default "Python-urllib/3.13" User-Agent outright: every send came
            # back 403 with a text/plain body reading "error code: 1010" (Cloudflare's
            # "client banned"), which is NOT a Resend error and carries none of Resend's
            # JSON, so it surfaced as a bare "Forbidden" naming nothing. Sending a real
            # UA makes the identical request succeed. Same class of problem as the 403s
            # check_links.py documents — the server is refusing OUR CLIENT, not the
            # request.
            "User-Agent": RESEND_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read() or b"{}")
            return body.get("id"), None
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = (e.read() or b"").decode("utf-8", "replace").strip()
        except Exception:
            pass
        detail = ""
        try:
            detail = (json.loads(raw) or {}).get("message") or ""
        except Exception:
            # NOT JSON. This is the case that mattered: a Cloudflare block answers
            # text/plain, so parsing-and-discarding left the reason as a bare "Forbidden"
            # and hid the one string ("error code: 1010") that identifies the problem.
            detail = raw[:200]
        if e.code == 403 and "1010" in raw:
            detail = (f"blocked by Cloudflare ({raw.strip()}) — the request never reached "
                      "Resend. Check the User-Agent header.")
        elif e.code == 403 and "domain" in detail.lower():
            # The other common 403, and a completely different fix.
            detail += " (verify the sending domain in the Resend dashboard)"
        return None, f"Resend {e.code}: {detail or e.reason}"
    except Exception as e:
        return None, f"Resend request failed: {e}"


# ---------------- The one public send path ----------------

def send_lifecycle_email(userid, kind, record=None, dedupe_key=None, to_email=None):
    """Send one lifecycle email, at most once per (userid, kind, dedupe_key).

    Never raises: every caller is either a signup or a cancel, and neither may fail or hang
    because a mail provider is having a bad day. Returns a result dict whose 'state' is one
    of sent | mock | skipped | failed, with 'reason' naming why on the two that did nothing.
    """
    if kind not in EMAIL_KINDS:
        return {"state": "failed", "reason": f"unknown kind {kind!r}"}

    if record is None:
        try:
            record = get_user_account(userid)
        except Exception as e:
            return {"state": "failed", "reason": f"could not read account: {e}"}
    if not record:
        return {"state": "failed", "reason": "no such account"}

    email = (to_email or record.get("email") or "").strip()
    if not email:
        return {"state": "skipped", "reason": "no email address on the account"}

    # Opt-out is honoured for ALL THREE kinds, including the two that are defensibly
    # transactional. See email_schema.sql.
    if record.get("lifecycle_email_optout"):
        return {"state": "skipped", "reason": "opted out of lifecycle email"}

    try:
        subject, html_body, text_body = render_for(kind, record)
    except Exception as e:
        return {"state": "failed", "reason": f"template error: {e}"}

    if not RESEND_API_KEY:
        # No claim row — see the module docstring. Printed rather than silent so an offline
        # signup still shows that an email would have gone out.
        print(f"[MOCK EMAIL] {kind} -> {email}: {subject}")
        return {"state": "mock", "reason": "RESEND_API_KEY not set", "subject": subject,
                "to": email}

    row, reason = _claim(userid, kind, dedupe_key, email, subject)
    if row is None:
        if reason == "setup":
            print(f"[WARN] Lifecycle email not sent: run {EMAIL_SETUP_SQL} in the "
                  f"Supabase SQL editor.")
            return {"state": "skipped", "reason": f"run {EMAIL_SETUP_SQL}",
                    "table_ready": False}
        return {"state": "skipped", "reason": reason}

    message_id, error = _resend_post(email, subject, html_body, text_body)
    if error:
        _finish(row, "failed", error=error)
        print(f"[WARN] Lifecycle email {kind} -> {email} failed: {error}")
        return {"state": "failed", "reason": error, "subject": subject, "to": email}

    _finish(row, "sent", message_id=message_id)
    return {"state": "sent", "message_id": message_id, "subject": subject, "to": email}


def send_lifecycle_email_async(userid, kind, record=None, dedupe_key=None):
    """Fire-and-forget. Registration and cancellation must not block on a provider call —
    same pattern as record_interactive_cost_async. Errors are logged inside, never raised."""
    threading.Thread(
        target=send_lifecycle_email,
        args=(userid, kind, record, dedupe_key),
        daemon=True,
    ).start()


# ---------------- The trial-ending sweep ----------------

def trial_dedupe_key(record):
    """The trial window this reminder belongs to — the DATE part of trial_ends_at.

    Date, not the full timestamp: a promo grant that pushes the end out by a few hours
    would otherwise mint a new key and re-send within the same window. A grant that adds
    days moves the date and correctly earns a second reminder.
    """
    when = _parse_iso((record or {}).get("trial_ends_at"))
    return when.date().isoformat() if when else ""


def due_trial_reminders(days=None, limit=500):
    """Accounts whose trial ends inside the window and who have not been reminded for it.

    Selection is by trial_ends_at, and the 'not yet reminded' half is left to the claim —
    reading email_sends first and filtering here would be a second source of truth for
    exactly the question the unique constraint already answers, and the two would drift.
    So this returns everyone in the window; the claim silently drops the ones already sent.
    """
    window = TRIAL_REMINDER_DAYS if days is None else int(days)
    now = _now()
    cutoff = now + datetime.timedelta(days=window)
    params = {
        "select": ("userid,first_name,last_name,email,subscription_status,trial_ends_at,"
                   "subscription_end_at,stripe_customer_id,stripe_subscription_id,"
                   "lifecycle_email_optout"),
        "subscription_status": "eq.trial",
        "trial_ends_at": f"gte.{now.isoformat()}",
        "order": "trial_ends_at.asc",
        "limit": str(limit),
    }
    rows = _supabase_request_strict("users", "GET", params=params) or []

    # The upper bound is applied here rather than as a second PostgREST filter. A dict
    # cannot carry the same key twice, and the `and=(...)` form parses commas as
    # separators, which makes an ISO timestamp an awkward thing to embed correctly. The
    # lower bound plus `order` already keeps the row count to the soonest-expiring
    # accounts, so trimming in Python costs nothing and cannot be got subtly wrong.
    rows = [r for r in rows
            if (_parse_iso(r.get("trial_ends_at")) or cutoff + datetime.timedelta(days=1))
            <= cutoff]

    # Already paying: Stripe renews them, the trial is moot, and a "your trial ends" email
    # to somebody with a card on file reads as us not knowing our own billing state.
    return [r for r in rows
            if not r.get("stripe_subscription_id")
            and not r.get("lifecycle_email_optout")]


def run_trial_sweep(days=None, dry_run=False, limit=500):
    """One pass of the trial-ending reminder. Safe to run many times a day: the claim is
    what makes a second pass a no-op, not the caller's restraint.

    dry_run resolves exactly who is due and sends nothing — the free tier this repo's
    agents all offer, for the same reason.
    """
    started = _now()
    try:
        due = due_trial_reminders(days=days, limit=limit)
    except urllib.error.HTTPError as e:
        if _missing_table_error(e):
            return {"ok": False, "table_ready": False, "setup_sql_file": EMAIL_SETUP_SQL,
                    "error": f"Run {EMAIL_SETUP_SQL} in the Supabase SQL editor."}
        return {"ok": False, "error": f"Could not read accounts: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Could not read accounts: {e}"}

    result = {
        "ok": True,
        "dry_run": bool(dry_run),
        "window_days": TRIAL_REMINDER_DAYS if days is None else int(days),
        "due": len(due),
        "sent": 0, "skipped": 0, "failed": 0, "mock": 0,
        "mode": "mock" if not RESEND_API_KEY else "live",
        "details": [],
        "started_at": started.isoformat(),
    }
    for record in due:
        userid = record.get("userid")
        entry = {"userid": userid, "email": record.get("email"),
                 "trial_ends_at": record.get("trial_ends_at")}
        if dry_run:
            entry["state"] = "would_send"
            result["details"].append(entry)
            continue
        outcome = send_lifecycle_email(userid, "trial_ending", record=record,
                                       dedupe_key=trial_dedupe_key(record))
        entry.update(outcome)
        result["details"].append(entry)
        # 'skipped' covers the common, healthy case: already claimed by an earlier run.
        result[outcome["state"]] = result.get(outcome["state"], 0) + 1

    result["finished_at"] = _now().isoformat()
    return result


# ---------------- Read side (the console) ----------------

def recent_sends(limit=100, kind=None):
    params = {"select": "*", "order": "claimed_at.desc", "limit": str(limit)}
    if kind:
        params["kind"] = f"eq.{kind}"
    return _supabase_request_strict(SENDS_TABLE, "GET", params=params) or []


def email_status(limit=100):
    """Everything the console's Emails tab needs in one call: config health, the recent
    log, per-kind counts, and who is currently due a trial reminder.

    An un-run migration degrades to a setup notice rather than an error, matching every
    other tab — and it reports `configured` separately from `table_ready`, because "no API
    key" and "no table" are different problems with different fixes and a single "not
    working" would hide which one you have.
    """
    out = {
        "configured": bool(RESEND_API_KEY),
        "mode": "live" if RESEND_API_KEY else "mock",
        "from": EMAIL_FROM,
        "reply_to": EMAIL_REPLY_TO or None,
        "app_url": EMAIL_APP_URL,
        "trial_reminder_days": TRIAL_REMINDER_DAYS,
        "kinds": list(EMAIL_KINDS),
        "table_ready": True,
        "setup_sql_file": EMAIL_SETUP_SQL,
        "sends": [],
        "counts": {},
        "stuck": 0,
        "due_now": [],
        "due_error": None,
    }
    try:
        sends = recent_sends(limit=limit)
    except urllib.error.HTTPError as e:
        if _missing_table_error(e):
            out["table_ready"] = False
            return out
        out["error"] = str(e)
        return out
    except Exception as e:
        out["error"] = str(e)
        return out

    out["sends"] = sends
    counts = {}
    stuck_before = _now() - datetime.timedelta(seconds=STUCK_AFTER_SECONDS)
    for row in sends:
        bucket = counts.setdefault(row.get("kind") or "?",
                                   {"sent": 0, "failed": 0, "sending": 0})
        state = row.get("state") or "sending"
        bucket[state] = bucket.get(state, 0) + 1
        if state == "sending":
            claimed = _parse_iso(row.get("claimed_at"))
            if claimed and claimed < stuck_before:
                out["stuck"] += 1
    out["counts"] = counts

    try:
        out["due_now"] = [
            {"userid": r.get("userid"), "email": r.get("email"),
             "first_name": r.get("first_name"), "trial_ends_at": r.get("trial_ends_at")}
            for r in due_trial_reminders()
        ]
    except Exception as e:
        # Reported, never silently zero: an empty list and a failed read look identical
        # otherwise, and one of them means the reminder is not going out.
        out["due_error"] = str(e)
    return out


def set_optout(userid, value=True):
    """Flip users.lifecycle_email_optout. Returns True on success."""
    try:
        _supabase_request_strict("users", "PATCH", params={"userid": f"eq.{userid}"},
                                 data={"lifecycle_email_optout": bool(value)})
        return True
    except urllib.error.HTTPError as e:
        if _missing_table_error(e):
            print(f"[WARN] Cannot record opt-out: run {EMAIL_SETUP_SQL}.")
        return False
    except Exception as e:
        print(f"[WARN] Could not record opt-out for {userid}: {e}")
        return False


def send_test(kind, to_email, record=None):
    """Send one template to an operator's own address, from the console.

    Deliberately does NOT claim a row and is NOT deduped: a test is something you repeat
    while editing copy, and a claim would both block the second attempt and — far worse —
    consume the real user's send, so previewing the welcome email against your own account
    would mean that account never gets one. Nothing here writes to email_sends at all.

    The trade is that this path has no protection against being pointed at a student's
    address. It is only reachable from the localhost-gated console, and the subject is
    prefixed so a copy that escapes is unmistakably a test rather than a real message.
    """
    if kind not in EMAIL_KINDS:
        return {"state": "failed", "reason": f"unknown kind {kind!r}"}
    to_email = (to_email or "").strip()
    if not to_email:
        return {"state": "failed", "reason": "no address given"}

    # Rendered against a real account when one is named, so the numbers in the test are the
    # numbers that account would actually see; otherwise against a sample row that makes
    # its own fakeness obvious.
    record = record or _sample_record(kind)
    try:
        subject, html_body, text_body = render_for(kind, record)
    except Exception as e:
        return {"state": "failed", "reason": f"template error: {e}"}

    subject = f"[TEST] {subject}"
    if not RESEND_API_KEY:
        return {"state": "mock", "reason": "RESEND_API_KEY not set", "subject": subject,
                "to": to_email}

    message_id, error = _resend_post(to_email, subject, html_body, text_body)
    if error:
        return {"state": "failed", "reason": error, "to": to_email}
    return {"state": "sent", "message_id": message_id, "subject": subject, "to": to_email}


def _sample_record(kind=None):
    """A stand-in account for previewing a template with nobody selected.

    The IDENTITY values are obviously fake on purpose — a realistic-looking sample invites
    reading a preview as a real user's email. The DATES are the opposite: they are staged
    per kind so each preview shows the number a real recipient of THAT email would see.

    This matters because every date in these templates is computed, not written. A single
    fixed trial window cannot serve all three: dated two days out it previews the
    trial-ending reminder correctly and makes the welcome email announce a "2-day trial"
    for a product whose trial is TRIAL_DAYS long — which is not a template bug but is
    indistinguishable from one, and was read as one.

      welcome       a trial that has just started  -> the full TRIAL_DAYS
      trial_ending  a trial about to expire        -> TRIAL_REMINDER_DAYS out, i.e. the
                                                      window the sweep actually fires in
      goodbye       a cancelled subscription       -> paid period still running
    """
    if kind == "trial_ending":
        trial_days = TRIAL_REMINDER_DAYS
    else:
        trial_days = TRIAL_DAYS
    return {
        "userid": "sample-student",
        "first_name": "Sample",
        "last_name": "Student",
        "email": "sample@example.com",
        "subscription_status": "trial",
        # +0.5 so days_until_trial_end, which CEILINGS, lands on the intended figure
        # rather than one above it.
        "trial_ends_at": (_now() + datetime.timedelta(days=trial_days - 0.5)).isoformat(),
        "subscription_end_at": (_now() + datetime.timedelta(days=18)).isoformat(),
    }


def preview(kind, userid=None):
    """Render a template for the console. Never sends, never writes."""
    record = None
    if userid:
        try:
            record = get_user_account(userid)
        except Exception:
            record = None
    record = record or _sample_record(kind)
    try:
        subject, html_body, text_body = render_for(kind, record)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True, "kind": kind, "subject": subject, "html": html_body, "text": text_body,
        "rendered_for": record.get("userid"),
        "is_sample": record.get("userid") == "sample-student",
    }

"""Configuration and environment for the Wingman web service.

Extracted verbatim from the former server.py monolith (docs/archive/PLAN_1_decompose.md).
Importing this module loads .env (see load_dotenv() call below), so import it
before anything that reads os.environ. Holds only shared constants; process
state and functions live in app.core and the service modules.
"""
import os
import re



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
# Ceiling on a client-supplied "maxTokens", mirroring CLAUDE_MAX_TOKENS_CEILING below.
# Callers whose answer length scales with their INPUT send their own budget rather than
# living inside the uniform default: profile-tag extraction returns one tag per thing the
# profile mentions, and tag enrichment one object per tag, so both grow with the student.
# At the flat 2000 a broad profile silently truncated — and because extractJSON repairs a
# truncated array rather than failing, the shortfall came back looking like a complete,
# shorter answer, i.e. a cap on how many interests a student is allowed to have.
# Unused budget is free (billing is on tokens produced), so asking generously costs nothing;
# this is a guard on an endpoint any signed-in browser can post to, not a model limit. Keep
# it clear of the model's own output limit, remembering that Gemini 3.x thinking tokens draw
# from this SAME budget.
MESSAGES_MAX_TOKENS_CEILING = 8000

# ---------- /api/messages-claude (Anthropic-backed, profile chat only) ----------
# profileChatNextQuestion/profileChatStarterQuestionsFromAI (script.js's callClaude())
# deliberately stayed on Claude rather than moving to Gemini with the rest of the app on
# 2026-08-18 — a separate endpoint from /api/messages so the Gemini path above is
# untouched; client still sends the same plain {system, userContent, useWebSearch} body,
# translated into Anthropic's content-block/messages shape here rather than on the client.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
# Default for the short profile-chat questions this endpoint was built for. Callers that
# produce a long answer (profile synthesis rewrites the WHOLE profile every merge, so its
# output grows with the profile) send their own "maxTokens" and are clamped to the ceiling
# below rather than to this. At the flat 1000 the synthesized profile was silently cut off
# mid-sentence once a student's story got past a few paragraphs — Anthropic returns the
# partial text with stop_reason "max_tokens", so it looked like a complete answer.
CLAUDE_MAX_TOKENS = 1000
# Ceiling on a client-supplied maxTokens. Haiku 4.5 allows far more; this is a cost guard
# on an endpoint any signed-in browser can post to, not a model limit.
CLAUDE_MAX_TOKENS_CEILING = 8000

# ---------- Opportunities catalog (Supabase-backed) ----------
# The opportunity catalog lives in a Supabase (hosted Postgres) table rather than
# the old static opportunities.json — see scripts/one-off/migrate_to_supabase.py for the one-time
# migration and CLAUDE.md for the rationale (scalability + free tier vs local SQLite).
# The anon key is safe to hold server-side here: it's rate-limited by Supabase and
# the table's Row Level Security policy only allows reading is_active=true rows.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
# `status` is check_deadlines.py's running/not_running/unknown verdict. It ships so the
# finder can drop discontinued programs from the results — it is NULL on 1195 of the
# 1239 active rows (never deadline-checked), so any consumer must treat NULL as "no
# verdict", never as "not running".
# `eligibility` joined this list 2026-08-24. It is maintained by refresh_opportunities.py
# and was the only curated record of a program's entry requirements anywhere in the repo,
# yet it never left the database — so the tracker prompt that invents prerequisites could
# not see the column that knows them. Adding a field here widens what the client receives
# AND what extractTrackerInfo can put in its prompt; keep it to columns students may see.
#
# match_vector is the ONE exception to "columns students may see": it is fetched into the
# server-side cache so the recall stage can score it (app/services/matching.py), but it is
# ~768 floats/row (~9MB across the catalog) and carries no display value, so the
# /api/opportunities client route STRIPS it before responding (see handle_opportunities). Do
# not remove the strip — shipping it to every client on every catalog load is the regression
# that made recall server-side in the first place.
OPPORTUNITIES_FIELDS = "id,name,org,summary,url,subject_tags,type,price,state,location,intl,season,review_status,review_summary,grade_min,grade_max,status,eligibility,match_vector"
# The one field fetched into the cache for server-side recall but never sent to the client.
OPPORTUNITIES_CLIENT_STRIP_FIELDS = ("match_vector",)
OPPORTUNITIES_CACHE_TTL = 300  # seconds


# ---------- Signup consent & eligibility policy ----------
# The Terms of Use (legal/terms.md §2) restrict Wingman to users 13 or older, and
# require a parent/guardian's permission for anyone under 18. Registration collects
# three acknowledgements and refuses the account without them; what was agreed to is
# stamped onto the user row (see create_user) so it can be audited later.
#
# TERMS_VERSION is what gets recorded per account. It is the effective date printed at
# the top of both documents — bump it whenever legal/*.md changes materially, so rows
# accepted under the old text are distinguishable from rows accepted under the new.
TERMS_VERSION = "2026-08-22"

# ---------- Persistent user account database (Supabase-backed) ----------
# Account records live in a Supabase `users` table rather than the old flat
# users_db.json file — see scripts/one-off/migrate_users_to_supabase.py for the one-time
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
# client-side Google library is needed. See db/google_auth_schema.sql for the users table columns
# this depends on (google_id, and password_hash made nullable for Google-only accounts).
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_TOKEN_TTL_SECONDS = 5 * 60


# ---------- Google Calendar sync ----------
# A separate, additional OAuth grant from Google Sign-In above: sign-in only ever asks for
# "openid email profile", so an existing signed-in session (password or Google) has no
# token that can touch Calendar. Connecting Calendar is its own start/callback pair that
# requests the calendar.events scope against the already-known userid, and persists the
# resulting tokens (see db/google_calendar_schema.sql) rather than discarding them like the
# short-lived _google_session_tokens above — a sync can then run again later without
# re-prompting, as long as the refresh token stays valid.
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
WINGMAN_CALENDAR_NAME = "Highschool Wingman"
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


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


# A deliberately permissive shape check — enough to reject junk and to guarantee the
# value is safe to put in a PostgREST `eq.` filter (no commas, quotes, or parens, which
# are that syntax's separators). Not an attempt to validate deliverability; the real
# proof an address works is mail arriving at it.
EMAIL_RE = re.compile(r"^[^\s@,()\x22\x27]+@[^\s@,()\x22\x27]+\.[^\s@,()\x22\x27]{2,}$")


# ---------- Session auth (Phase 2 — docs/archive/PLAN_2_auth.md) ----------
# Identity is carried in a signed JWT, never in the request body — that is what closes
# the pre-migration IDOR on /api/data/*. Two-token model:
#   * a short-lived ACCESS token, verified statelessly on every gated request (no DB hit);
#   * a longer-lived REFRESH token, presented only to /api/auth/refresh, where the
#     account's `token_version` is checked against the DB. Bumping users.token_version
#     invalidates every outstanding refresh token ("log out everywhere" / account kill),
#     so all sessions die within one access-token lifetime.
# JWT_SECRET signs both. If it is unset the auth layer fails closed (see app/auth/tokens.py)
# rather than signing with a guessable key. Set it in .env locally and as a Render env var
# (render.yaml lists it sync:false); never commit it or send it to any client.
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_SECONDS = 45 * 60           # 45 minutes
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


# ---------- User metrics: the activation funnel, retention, and the daily snapshot ----------
# The Cost per user tab measures dollars out. This measures usage and revenue in. They
# share the `users` roster and cross-link, but they stay separate views: one is a spend
# ledger, the other a product funnel, and a page that computes both computes neither well.
#
# Two migrations gate the time-series half (see the .sql files for why each exists):
#   db/user_activity_schema.sql       -> DAU/WAU/MAU, retention, "came back after signup day"
#   db/user_metrics_daily_schema.sql  -> trend lines for state metrics, which are otherwise
#                                     unrecoverable (the `data` jsonb holds one profile,
#                                     not a history of one)
# Neither is required for the funnel, plan mix, conversion or the roster. Those come from
# the `users` table as it stands today, which is why they shipped first.
USER_ACTIVITY_SETUP_SQL = "db/user_activity_schema.sql"
USER_METRICS_SETUP_SQL = "db/user_metrics_daily_schema.sql"
# The append-only behavioral event log the matcher's revealed-preference loop reads. Capture
# ships early (unlogged clicks are unrecoverable); the consumer comes later. Until this runs,
# record_user_events() latches off after one warning and the UI is unaffected (fail-open).
USER_EVENTS_SETUP_SQL = "db/user_events_schema.sql"
# The append-only log of 5xx responses / unhandled exceptions the FastAPI service produces,
# read by the admin console's API Errors tab. Capture ships with the recorder (an unlogged
# failure is unrecoverable); until this runs, record_api_error() latches off after one warning
# and no request is affected (fail-open). Lives in Supabase, not memory, because the console
# runs on a different machine from the shipped API — see db/api_errors_schema.sql.
API_ERRORS_SETUP_SQL = "db/api_errors_schema.sql"


# ---------- Lifecycle email (Resend) ----------
# Three transactional emails around the account lifecycle: a welcome at signup, a reminder
# a couple of days before the free trial ends, and a confirmation when a subscription is
# cancelled. NOT a marketing system — there is no list, no segments and no broadcast, and
# there deliberately is no path in this repo that mails everybody at once.
#
# The sending half is Resend's HTTPS API, called with raw urllib exactly as
# subscription_common.py calls Stripe (no SDK, matching the stdlib-only convention). What a
# provider buys that cannot be rebuilt here is SPF/DKIM/DMARC on the sending domain, a
# warmed IP, and bounce/complaint handling. Mail from a cold domain to a population living
# on Gmail and school Google Workspace accounts goes to spam, and school MXes are the least
# forgiving recipients there are.
#
# If RESEND_API_KEY is unset the whole path runs in MOCK mode — same convention as
# GEMINI_API_KEY/ANTHROPIC_API_KEY — so signup and cancel work offline. A mock send writes
# NO email_sends row, deliberately: a claim row would suppress the real send once a key is
# configured, i.e. developing offline would silently cost real users their welcome email.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_URL = "https://api.resend.com/emails"

# Resend is fronted by Cloudflare, which BLOCKS urllib's default "Python-urllib/x.y"
# User-Agent — every send returns 403 with a text/plain "error code: 1010" body that never
# reaches Resend at all. Identifying the client properly is what makes the same request
# work, so this is load-bearing rather than decorative. Do not drop it.
RESEND_USER_AGENT = "highschoolwingman/1.0 (+https://highschoolwingman.com)"

# Must be on a domain verified in the Resend dashboard, or every send 403s. The display
# name is part of the value ("Wingman <contactus@...>") because that is the format Resend
# takes.
EMAIL_FROM = os.environ.get(
    "EMAIL_FROM", "Highschool Wingman <contactus@highschoolwingman.com>")

# Defaults to the From address rather than to empty, because the goodbye email ends with
# "just reply to this email and tell us. It gets read." An unset reply-to would make that
# sentence false for anyone whose client honours it differently from From, and a promise
# that quietly does not work is worse than not making it.
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO", "contactus@highschoolwingman.com")

# Where links in an email point.
#
# WEB_APP_URL is inherited ONLY when it is not a loopback origin, and that guard is
# load-bearing rather than tidy. In dev WEB_APP_URL is legitimately http://localhost:8081,
# and inheriting it put "Keep my account: http://localhost:8081/subscription" into a real
# trial-ending email — a link that resolves to the RECIPIENT's own machine, so it fails for
# them and works when you test it, which is the worst possible way for this to be wrong.
# An email is read outside this process; a localhost link is never right for anybody.
#
# Set EMAIL_APP_URL explicitly to override, including to a loopback origin if you really
# are testing link routing locally — an explicit value is a decision, an inherited one is
# an accident.
_EMAIL_APP_URL_EXPLICIT = (os.environ.get("EMAIL_APP_URL") or "").strip()
_WEB_APP_URL = (os.environ.get("WEB_APP_URL") or "").strip()
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


def _is_loopback(url):
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.startswith("[::1]")


EMAIL_APP_URL = (
    _EMAIL_APP_URL_EXPLICIT
    or (_WEB_APP_URL if _WEB_APP_URL and not _is_loopback(_WEB_APP_URL) else "")
    or "https://highschoolwingman.com"
).rstrip("/")

# CAN-SPAM requires a physical postal address on commercial mail. All three of these are
# transactional and arguably exempt, but the footer carries it unconditionally — the
# exemption is a legal argument, and losing it costs more than a line of text. Set the real
# address in .env; the placeholder is deliberately obvious so it cannot ship unnoticed.
EMAIL_POSTAL_ADDRESS = os.environ.get(
    "EMAIL_POSTAL_ADDRESS", "[SET EMAIL_POSTAL_ADDRESS IN .env]")

# How many days before trial_ends_at the reminder goes out. A window, not an instant: the
# sweep runs once a day, so anything expiring inside the next N days and not yet reminded
# is due. Two days leaves a weekday to act on it without arriving so early it is forgotten.
TRIAL_REMINDER_DAYS = int(os.environ.get("TRIAL_REMINDER_DAYS", "2") or 2)

# Deadline-alert reminder rungs, in days-before-the-date. A tracked deadline is alerted once
# per rung it passes THROUGH: each sweep assigns the date to the SMALLEST rung >= days_left
# and fires that rung if it has not already been claimed. Window assignment (not day-exact
# firing) is what makes the ladder self-healing — a missed cron day still fires the item at
# T-2 under the rung-3 window, and an item tracked late lands in one rung rather than
# replaying the whole backlog. Ordered high-to-low for readability; assign_rung sorts it.
# See docs/plans/DEADLINE_EMAIL_ALERTS_PLAN.md §3. Deliberately a constant, not env-tunable: the values
# become permanent the moment they are written into email_sends dedupe keys.
DEADLINE_ALERT_RUNGS = (7, 3, 1)

# The digest lists at most this many items, soonest first, then "and N more in your Quest
# Log". An email that scrolls forever reads as noise; the app is where the full list lives.
DEADLINE_ALERT_MAX_ITEMS = 10

# Shared secret for POST /api/email/sweep, the endpoint a scheduler calls daily. It is on
# the SHIPPED app (ops/ is localhost-only and never mounted on Render, so an admin button
# cannot be what sends the trial reminder), which means it is internet-reachable and needs
# its own guard. If unset the endpoint fails CLOSED with a 503 rather than running
# unauthenticated — the same choice JWT_SECRET makes.
EMAIL_CRON_SECRET = os.environ.get("EMAIL_CRON_SECRET", "")

EMAIL_SETUP_SQL = "db/email_schema.sql"

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
# agents/check_deadlines.py/check_reviews.py/scrape_opportunities.py), which matters here since
# these calls block a real page interaction instead of running unattended. Revisit
# alongside gemini_common.MODEL — see that module's docstring on model ID churn.
# NOTE: "gemini-3.6-flash-lite" (as literally requested) does not exist in the Gemini API —
# there is no lite variant of the 3.6 generation yet (confirmed via ListModels against the
# live key on 2026-08-18). Pinned to gemini-3.5-flash-lite instead, the closest existing
# lite model, following the same "pin an exact version" convention as wingman/gemini_common.py's
# MODEL constant. Swap this if/when a real gemini-3.6-flash-lite ships.
MESSAGES_MODEL = "gemini-3.5-flash-lite"
# Uniform cap across every /api/messages call site (mirrors the old Anthropic path's
# uniform max_tokens=1000) — bumped above what each system prompt's own "stay well
# within a 1000-token response" instruction asks for, to leave headroom for Gemini 3.x's
# thinking tokens, which draw from this SAME budget (see wingman/gemini_common.py's "FOURTH
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

# ---------- Request caps on the two AI proxies (S0-2; findings D1, D4, M4) ----------
# Before these there was no limiter on either proxy route and no body-size limit anywhere in
# the app — app.deps.raw_body read the whole request into memory unbounded. Verified live
# 2026-09-03: 12 rapid POSTs -> 200 x12 with zero 429s, and a 41,040-byte body -> 200 with
# 9,703 input tokens billed. Both are direct billing levers.
#
# Size: the largest LEGITIMATE payload is rankCandidates', which sends preFilter's pool —
# capped at 100 rows compacted to 9 fields each (frontend/src/lib/ranking.ts) — so ~100 KB
# measured. 512 KB is 5x that headroom while still bounding one call to roughly a tenth of
# what SECURITY_HARDENING_PLAN.md's suggested 1 MB would allow through. Raise the env var,
# don't raise a student's floor, if a future feature genuinely needs more.
AI_MAX_BODY_BYTES = int(os.environ.get("AI_MAX_BODY_BYTES", "") or 512 * 1024)
# The resume/LinkedIn import parses an uploaded PDF or DOCX wholly in memory, so it needs its
# own, higher ceiling; it is token-gated and subscription-gated, unlike the proxies.
RESUME_MAX_BODY_BYTES = int(os.environ.get("RESUME_MAX_BODY_BYTES", "") or 10 * 1024 * 1024)

# Rate: two buckets, both checked. The PER-USER bucket is what bounds one account's spend.
# The PER-IP bucket is deliberately far looser because a school NAT puts a whole cohort
# behind one address — tightening it would lock out a classroom, not an attacker. Home Base
# opens with a burst (inferSubjects + rankCandidates + the finder's tag scorer), so the
# per-user number has to clear a normal burst with room to spare.
AI_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("AI_RATE_LIMIT_WINDOW_SECONDS", "") or 60)
AI_RATE_LIMIT_PER_USER = int(os.environ.get("AI_RATE_LIMIT_PER_USER", "") or 30)
AI_RATE_LIMIT_PER_IP = int(os.environ.get("AI_RATE_LIMIT_PER_IP", "") or 120)

# ---------- Upstream call limits on the AI proxies (S0-4; findings C1.4, M4) ----------
# The Anthropic proxy called urllib.request.urlopen(req) with NO timeout at all. FastAPI runs
# these plain-`def` handlers in the anyio threadpool, so a hung socket permanently consumes
# one of its 40 slots — capacity that never comes back until a restart. An explicit ceiling
# on both paths is what bounds that. 60s rather than the 120s that wingman/{gemini,claude}
# _common default to: those are batch agents that can afford to wait, this is a student
# watching a spinner, and a client-side timeout does not stop or refund server-side work
# already in flight — so waiting longer only costs more.
AI_UPSTREAM_TIMEOUT_SECONDS = float(os.environ.get("AI_UPSTREAM_TIMEOUT_SECONDS", "") or 60)

# Ceiling on web searches per Anthropic call. Unlike Gemini's max_searches — a number folded
# into the prompt and nothing more — Anthropic ENFORCES max_uses server-side, so this is a
# real cost ceiling ($0.01/search). It is moot while _USE_WEB_SEARCH pins search off; it
# exists as the defence-in-depth layer, so 1 is the right number: if the pin is ever lifted
# by accident, the blast radius is one search. agents/check_deadlines.py sets 1 deliberately
# for the same reason.
ANTHROPIC_MAX_WEB_SEARCH_USES = int(os.environ.get("ANTHROPIC_MAX_WEB_SEARCH_USES", "") or 1)

# ---------- Body caps on the rest of the app (S1-5; finding M4) ----------
# S0-2 capped the two AI proxies and the resume upload; every OTHER route still read its
# body with no ceiling at all, so /api/data/save, /api/events, /api/register and the rest
# each let one request buffer an arbitrary amount of memory. This is the default ceiling
# json_body applies, i.e. every JSON route that has not asked for its own.
#
# 1 MB rather than something tighter: /api/data/save carries the student's whole tracker or
# profile for one key, and a 37 KB tracker is ordinary. Tight enough to bound a request,
# loose enough that no real one hits it.
JSON_MAX_BODY_BYTES = int(os.environ.get("JSON_MAX_BODY_BYTES", "") or 1024 * 1024)
# A single users.data value. The body cap above bounds ONE request; this bounds what
# accumulates in the row, which is the thing that actually grows without limit — the row is
# read in full on every app open.
USER_DATA_MAX_VALUE_BYTES = int(
    os.environ.get("USER_DATA_MAX_VALUE_BYTES", "") or 512 * 1024)
# One event's `context` dict, which lands in user_events.context (jsonb). The events buffer
# is capped by count, so without this one caller could still push arbitrary bytes per row.
EVENT_MAX_CONTEXT_BYTES = int(os.environ.get("EVENT_MAX_CONTEXT_BYTES", "") or 4096)
# Events accepted from one request. The client batches a tick's worth — single digits.
EVENTS_MAX_PER_REQUEST = int(os.environ.get("EVENTS_MAX_PER_REQUEST", "") or 100)

# ---------- User-submitted opportunity caps (S1-4; findings M1, M10) ----------
# POST /api/user-submitted-opportunities took a row from ANYBODY with no token at all, and
# every call reads the whole catalog (~1,400 rows across two pages) for dedupe — a cheap
# amplification against a free-tier instance, and a way to bury real submissions under
# thousands of fakes. It is now require_subscription'd, so these bound an ACCOUNT rather
# than the internet: the Quest Log's custom-add is the only caller, and a student adding
# more than a handful of programs a day is not the case being served.
USER_SUBMISSION_LIMIT_PER_DAY = int(
    os.environ.get("USER_SUBMISSION_LIMIT_PER_DAY", "") or 20)
# Length ceilings on the free-text fields. These are stored on a catalog row and RENDERED
# IN THE ADMIN CONSOLE, so an unbounded `name` is both a storage lever and a thing a
# reviewer has to scroll past. Generous relative to real values (the longest catalog name
# is well under 200 characters) — this bounds abuse, it does not validate content.
USER_SUBMISSION_MAX_NAME = int(os.environ.get("USER_SUBMISSION_MAX_NAME", "") or 300)
USER_SUBMISSION_MAX_TEXT = int(os.environ.get("USER_SUBMISSION_MAX_TEXT", "") or 2000)
USER_SUBMISSION_MAX_URL = int(os.environ.get("USER_SUBMISSION_MAX_URL", "") or 2048)
# Ceiling on the two array fields, which land in jsonb.
USER_SUBMISSION_MAX_LIST = int(os.environ.get("USER_SUBMISSION_MAX_LIST", "") or 40)

# ---------- Spend caps (S0-5; finding H4) ----------
# Nothing anywhere read spend BACK to refuse a call — the rollups only recorded it. One
# 7-day trial account (which costs $0) could loop
# GET /api/opportunities/<id>/deadline?refresh=1 across the catalog: refresh=1 bypassed the
# 7-day cache unconditionally and each verified check measures ~$0.07, so ~$90 per pass over
# 1,300 rows, repeatable. /api/match is a few cents a call, also unbounded.
#
# Three independent layers, all needed — see app/services/budget.py. A value <= 0 disables
# that layer, which is the operator's off switch.
#
# 1. Per-user daily budget. $0.50 is a deliberately conservative placeholder, NOT a measured
#    number: SECURITY_HARDENING_PLAN.md asks for ~5x the median daily per-user spend read off
#    the console's Cost per user tab. Tune USER_DAILY_BUDGET_USD once that figure is known.
USER_DAILY_BUDGET_USD = float(os.environ.get("USER_DAILY_BUDGET_USD", "") or 0.50)
# Userids that bypass the per-user cap entirely — the operator override the plan asks for, for
# demos and for a support case where someone legitimately needs more. Comma-separated.
BUDGET_EXEMPT_USERIDS = frozenset(
    u.strip().lower() for u in (os.environ.get("BUDGET_EXEMPT_USERIDS") or "").split(",")
    if u.strip()
)
# 2. Per-user, per-row cooldown on a FORCED deadline re-check. The budget alone still allows a
#    fast burn, because the cache bypass is the amplifier — this caps how often any one row can
#    be forced past its 7-day cache by one student.
FORCED_RECHECK_WINDOW_SECONDS = int(os.environ.get("FORCED_RECHECK_WINDOW_SECONDS", "") or 3600)
FORCED_RECHECK_MAX_PER_WINDOW = int(os.environ.get("FORCED_RECHECK_MAX_PER_WINDOW", "") or 1)
# 3. Global daily circuit breaker. Above this, every paid branch degrades to its existing
#    cached/mock path — which turns a billing incident into a degraded app, the correct
#    failure direction. One H4 pass was ~$90, so this trips well inside a single pass; it
#    also sits far above 50 users each spending their whole per-user allowance.
GLOBAL_DAILY_BUDGET_USD = float(os.environ.get("GLOBAL_DAILY_BUDGET_USD", "") or 25.0)
# How long a spend total read out of user_costs is trusted before it is re-read. The AI
# limiter (30/min/user) bounds how far a user can overshoot inside one window.
BUDGET_CACHE_TTL_SECONDS = int(os.environ.get("BUDGET_CACHE_TTL_SECONDS", "") or 60)

# ---------- Opportunities catalog (Supabase-backed) ----------
# The opportunity catalog lives in a Supabase (hosted Postgres) table rather than
# the old static opportunities.json — see scripts/one-off/migrate_to_supabase.py for the one-time
# migration and CLAUDE.md for the rationale (scalability + free tier vs local SQLite).
# The anon key is safe to hold server-side here: it's rate-limited by Supabase and
# the table's Row Level Security policy only allows reading is_active=true rows.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
# `status` is agents/check_deadlines.py's running/not_running/unknown verdict. It ships so the
# finder can drop discontinued programs from the results — it is NULL on 1195 of the
# 1239 active rows (never deadline-checked), so any consumer must treat NULL as "no
# verdict", never as "not running".
# `eligibility` joined this list 2026-08-24. It is maintained by agents/refresh_opportunities.py
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
# Replaces agents/check_deadlines.py's batch/cron model as the primary way status/deadlines data
# gets populated: rather than proactively scanning the whole catalog on a schedule (which,
# on 2026-08-18, burned through Gemini's daily grounding quota partway through a single
# full pass), a check now only runs when a real user actually adds an opportunity to their
# tracker, or loads the Tracker page with an already-tracked item whose cached data has
# gone stale. Uses Claude Haiku (claude-haiku-4-5-20251001) with web search enforced.
# See agents/check_deadlines.py's docstring for the underlying Supabase columns
# (status/important_dates/was_estimated/important_date_note/last_checked_at) — this endpoint
# reads/writes the exact same columns, so the two mechanisms share one cache. important_dates
# holds EVERY pertinent date for the opportunity (registration opens/closes, event start/end,
# notifications, etc.), each tagged with a "type" — not just a single narrow "deadline"; see
# agents/check_deadlines.py's build_system() for the full schema. The batch script still exists for
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
# wingman/subscription_common.py calls Stripe (no SDK, matching the stdlib-only convention). What a
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

# ---------- Ops console (S1-8) ----------
# The local-only console's ONLY protection used to be `request.client.host in
# ("127.0.0.1", "::1", ...)`, which any localhost tunnel defeats — ngrok, VS Code port
# forwarding, a Cloudflare tunnel — because the tunnel's peer IS 127.0.0.1. Behind that gate
# sit subprocess launches that spend real money, a roster with the names and emails of minors,
# catalog activation, and test email sends to arbitrary addresses.
#
# So every ops API route additionally requires this token in a HEADER (X-Ops-Token) — the same
# shape EMAIL_CRON_SECRET already uses, and for the same reason: a URL carrying a credential is
# recorded by every proxy and access log between the client and here. It FAILS CLOSED when
# unset, so a misconfiguration cannot silently mean "no check". `python server.py` mints and
# prints one for local dev when it is absent.
WINGMAN_OPS_TOKEN = os.environ.get("WINGMAN_OPS_TOKEN", "")

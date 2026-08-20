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
import json
import os
import re
import random
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
from check_deadlines import check_one as check_deadline_one, VALID_STATUS as DEADLINE_VALID_STATUS
from gemini_common import call_gemini


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
OPPORTUNITIES_FIELDS = "id,name,org,summary,url,subject,type,price,state,location,intl,season,review_status,review_summary"
OPPORTUNITIES_CACHE_TTL = 300  # seconds
_opportunities_cache = {"data": None, "fetched_at": 0.0}
_opportunities_cache_lock = threading.Lock()

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


def get_user(userid):
    query = "?" + urllib.parse.urlencode({"userid": f"eq.{userid}", "select": "*"})
    rows = _users_request("GET", query)
    return rows[0] if rows else None


def create_user(userid, first_name, last_name, email, password_hash, location=""):
    _users_request("POST", "", data=[{
        "userid": userid,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "password_hash": password_hash,
        "location": location,
        "data": {},
    }])


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
    "Request a teacher recommendation letter",
    "Draft your personal statement / essay",
    "Gather transcripts and test scores",
    "Fill out the application form",
    "Prepare a writing sample or portfolio",
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


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        deadline_match = DEADLINE_PATH_RE.match(self.path)
        if deadline_match:
            self.handle_deadline_check(deadline_match.group(1))
        elif self.path.startswith("/api/opportunities"):
            self.handle_opportunities()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/messages":
            self.handle_messages()
        elif self.path == "/api/messages-claude":
            self.handle_messages_claude()
        elif self.path == "/api/register":
            self.handle_register()
        elif self.path == "/api/login":
            self.handle_login()
        elif self.path == "/api/data/save":
            self.handle_data_save()
        elif self.path == "/api/data/load":
            self.handle_data_load()
        elif self.path == "/api/account/location":
            self.handle_update_location()
        else:
            self.send_error(404)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

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
        key = userid.lower()
        try:
            create_user(key, first_name, last_name, email, password_hash, location)
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
        self._relay(200, json.dumps({
            "ok": True,
            "firstName": record["first_name"],
            "lastName": record["last_name"],
            "email": record["email"],
            "location": record.get("location") or "",
        }).encode())

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

    def handle_opportunities(self):
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            return self.send_json_error(500, "SUPABASE_URL/SUPABASE_ANON_KEY not configured.")
        try:
            data = fetch_opportunities()
        except Exception as e:
            return self.send_json_error(502, f"Could not reach Supabase: {e}")
        self._relay(200, json.dumps(data).encode())

    def handle_deadline_check(self, opp_id):
        """GET /api/opportunities/<id>/deadline — on-demand, cross-user-cached deadline
        check. See the module-level comment above GEMINI_API_KEY for the full rationale.
        Serves cached status/important_dates straight from Supabase if last_checked_at is under
        DEADLINE_STALE_DAYS old; otherwise runs a fresh Claude Haiku web_search check (reusing
        check_deadlines.py's check_one()), re-caches it, and returns the fresh result. Rejects
        silent search skips (searches == 0) and falls back to cached value if no searches occurred."""
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
        if GEMINI_API_KEY:
            self.proxy_to_gemini(raw_body)
        else:
            self.mock_response(raw_body)

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
            text, _usage = call_gemini(
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

    def handle_messages_claude(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
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
        except Exception:
            response_text = ""
        log_conversation_async(userid, self.client_address[0], "live", system, user_content, response_text)

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

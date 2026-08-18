#!/usr/bin/env python3
"""Local dev server: serves the static site and proxies /api/messages to the
Anthropic API. If ANTHROPIC_API_KEY is not set, it fabricates plausible mock
responses instead so the app is fully click-through-able without a real key
or network access. Mock responses are pattern-matched against each system
prompt used in script.js's callClaude() call sites.
"""
import datetime
import json
import os
import re
import random
import threading
import urllib.request
import urllib.error
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def load_dotenv(path=".env"):
    """Minimal stdlib-only .env loader — populates os.environ from KEY=VALUE
    lines so secrets like ANTHROPIC_API_KEY never have to be typed inline in a
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
            # Only skip a key that's already set to a *non-empty* value in the
            # environment — an empty-string env var (e.g. left over from an earlier
            # inline `ANTHROPIC_API_KEY="" python3 server.py`) should not shadow a
            # real value from .env, or the server silently stays in MOCK mode forever.
            if key and not os.environ.get(key):
                os.environ[key] = value


load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
PORT = 8000

# ---------- Persistent user account database ----------
# A plain JSON file next to this script. Not a "real" database, but it's a real
# file on disk, so accounts created via /api/register survive server restarts
# and page reloads — unlike the client-side-only storage the rest of the app
# uses. Keyed by lowercased userid -> {firstName, lastName, email, passwordHash}.
# passwordHash arrives already SHA-256-hashed client-side; the server never
# sees or stores a plaintext password.
USERS_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users_db.json")
USERS_LOCK = threading.Lock()


def load_users_db():
    if os.path.exists(USERS_DB_PATH):
        try:
            with open(USERS_DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_users_db(users):
    with open(USERS_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


USERS_DB = load_users_db()

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
            "summary": "Mock venue — set ANTHROPIC_API_KEY for real, live-searched results.",
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
    meta_bits = [b for b in [org, "Mock data · set ANTHROPIC_API_KEY for live details"] if b]
    fit = (summary[:140] + "…") if len(summary) > 140 else summary
    obj = {
        "status": "running",
        "meta": " · ".join(meta_bits),
        "fit": fit or f"Placeholder fit summary for {name} — set ANTHROPIC_API_KEY for a real one.",
        "note": "Mock data for local testing — set ANTHROPIC_API_KEY for real, live-searched details.",
        "noteType": "plain",
        "opens_iso": None,
        "deadlines": [{"label": "Application Deadline", "date_iso": deadline_iso}],
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


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/api/messages":
            self.handle_messages()
        elif self.path == "/api/register":
            self.handle_register()
        elif self.path == "/api/login":
            self.handle_login()
        elif self.path == "/api/data/save":
            self.handle_data_save()
        elif self.path == "/api/data/load":
            self.handle_data_load()
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
        if not all([first_name, last_name, email, userid, password_hash]):
            return self.send_json_error(400, "Missing required fields.")
        key = userid.lower()
        with USERS_LOCK:
            if key in USERS_DB:
                return self.send_json_error(409, "That user ID is already taken.")
            USERS_DB[key] = {
                "firstName": first_name,
                "lastName": last_name,
                "email": email,
                "passwordHash": password_hash,
            }
            save_users_db(USERS_DB)
        self._relay(200, json.dumps({"ok": True}).encode())

    def handle_login(self):
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip()
        password_hash = body.get("passwordHash") or ""
        key = userid.lower()
        with USERS_LOCK:
            record = USERS_DB.get(key)
        if not record:
            return self.send_json_error(404, "No account found with that user ID.")
        if record.get("passwordHash") != password_hash:
            return self.send_json_error(401, "Incorrect password.")
        self._relay(200, json.dumps({
            "ok": True,
            "firstName": record["firstName"],
            "lastName": record["lastName"],
            "email": record["email"],
        }).encode())

    # ---------- Per-account app data (profile, tracker, saved items) ----------
    # A generic key/value blob per user, stored alongside their account record so
    # it survives logout/login and server restarts — unlike the client-only
    # window.storage the rest of the app was built around (see script.js).
    def handle_data_save(self):
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        key = body.get("key")
        if not userid or not key:
            return self.send_json_error(400, "Missing userid or key.")
        with USERS_LOCK:
            record = USERS_DB.get(userid)
            if not record:
                return self.send_json_error(404, "No account found with that user ID.")
            record.setdefault("data", {})[key] = body.get("value")
            save_users_db(USERS_DB)
        self._relay(200, json.dumps({"ok": True}).encode())

    def handle_data_load(self):
        body = self._read_json_body()
        userid = (body.get("userid") or "").strip().lower()
        key = body.get("key")
        with USERS_LOCK:
            record = USERS_DB.get(userid)
            value = record.get("data", {}).get(key) if record else None
        self._relay(200, json.dumps({"value": value}).encode())

    def handle_messages(self):
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)
        if ANTHROPIC_API_KEY:
            self.proxy_to_anthropic(raw_body)
        else:
            self.mock_response(raw_body)

    def mock_response(self, raw_body):
        try:
            payload = json.loads(raw_body)
            system_raw = payload.get("system", "")
            # system is now sent as a list of content blocks (with cache_control)
            # rather than a plain string, to enable prompt caching — flatten it
            # back to text so the pattern-matching in generate_mock_text still works.
            if isinstance(system_raw, list):
                system = "".join(b.get("text", "") for b in system_raw if isinstance(b, dict))
            else:
                system = system_raw
            user_content = payload.get("messages", [{}])[0].get("content", "")
        except Exception:
            system, user_content = "", ""
        text = generate_mock_text(system, user_content)
        data = json.dumps({"content": [{"type": "text", "text": text}]}).encode()
        self._relay(200, data)

    def proxy_to_anthropic(self, raw_body):
        req = urllib.request.Request(
            ANTHROPIC_URL,
            data=raw_body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                self._relay(resp.status, resp.read())
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.read())
        except Exception as e:
            self.send_json_error(502, str(e))

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
    mode = "LIVE (using ANTHROPIC_API_KEY)" if ANTHROPIC_API_KEY else "MOCK (no ANTHROPIC_API_KEY set — fabricating responses)"
    server = ThreadingHTTPServer(("", PORT), Handler)
    print(f"Serving http://localhost:{PORT}  [{mode}]")
    server.serve_forever()

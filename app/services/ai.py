"""Mock AI generation and the JSON-ish extraction helpers for /api/messages.

Extracted verbatim from server.py (docs/archive/PLAN_1_decompose.md). generate_mock_text()
fabricates plausible pattern-matched responses so the app is click-through-able
with no API key; the live proxies live in app.routes.ai.
"""
import datetime
import json
import random
import re


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


def mock_profile_tag_strings(user_content):
    """Filter tags for the Fresh Finds 'Your Profile' facet. Built from the profile's own
    sentences so the mock facet reads like the student's text rather than canned strings."""
    body = user_content.split('profile:', 1)[-1].strip()
    parts = [p.strip() for p in re.split(r'[.\n]+', body) if len(p.strip().split()) >= 3]
    tags = []
    for p in parts:
        words = p.split()
        tag = ' '.join(words[:6])[:60]
        if tag and tag not in tags:
            tags.append(tag)
    # Uncapped, matching the live extractor: the facet returns as many tags as it takes to
    # cover the profile, so a mock that stopped at 10 would hide exactly the overflow
    # behaviour (batched enrichment, the scrolling dropdown) that offline mode exists to
    # exercise.
    return json.dumps(tags or ['Exploring new opportunities'])


def mock_tags_and_basics(user_content):
    """The MERGED profile-extraction pass: basics + enriched tags in one object.

    Reuses the two mocks it replaced rather than inventing a third answer, so offline mode
    keeps saying the same thing about the same profile that the separate calls used to.
    """
    body = user_content.split('STUDENT PROFILE:', 1)[-1]
    # Drop the prompt's own closing instruction, or it is read as one of the student's
    # sentences and comes back as a tag.
    body = body.split('Return the JSON object', 1)[0]
    tags = json.loads(mock_profile_tag_strings('profile: ' + body))
    return json.dumps({
        "basics": json.loads(mock_profile_basics(body)),
        "tags": [
            {
                "tag": t,
                "intent": f"Wants to go further with {t.lower()}",
                "nextSteps": ["Find a program or competition", "Build something to show for it"],
            }
            for t in tags
        ],
    })


def mock_enrich_profile_tags(user_content):
    """One enrichment object per tag, echoing the tag so the position-independent match in
    enrichProfileTags() is exercised offline too."""
    tags = re.findall(r'^\s*\d+\.\s*(.+)$', user_content, re.M)
    return json.dumps([
        {
            "tag": t.strip(),
            "intent": f"Wants to go further with {t.strip().lower()}",
            "nextSteps": ["Find a program or competition", "Build something to show for it"],
        }
        for t in tags
    ])


def mock_score_opportunities_for_tag(user_content):
    """Rank the visible results against one profile tag. Returns a genuine SUBSET (the
    real call omits poor matches), so the filter visibly filters in mock mode instead of
    looking like a no-op."""
    m = re.search(r'PROFILE TAG:\s*"([^"]*)"', user_content)
    tag = m.group(1) if m else 'your profile'
    ids = re.findall(r'^ID:\s*(\S+)\s*\|', user_content, re.M)
    keep = ids[::2][:10]  # every other one — a subset, not everything
    return json.dumps([
        {"id": cid, "rank": i + 1, "reasoning": f"This lines up with your interest in {tag.lower()}."}
        for i, cid in enumerate(keep)
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


def mock_profile_chat_starter_pool():
    # The cached 10-opener bank. Mock mode has fewer canned questions than the real pool asks
    # for, so sample what's available rather than erroring on random.sample's
    # no-replacement requirement — the client draws 3 at a time from whatever it gets back.
    return json.dumps(random.sample(MOCK_CHAT_QUESTIONS, min(10, len(MOCK_CHAT_QUESTIONS))))


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


def mock_profile_basics(user_content):
    """Regex what the real extraction infers, and leave the rest null — a mock that
    invented a grade or a gender would make the "No info" tiles untestable offline."""
    grade = re.search(r'\b(9th|10th|11th|12th|freshman|sophomore|junior|senior)\b', user_content, re.I)
    state = re.search(r'\b(?:in|from) (Washington|California|New York|Texas|Oregon)\b', user_content, re.I)
    return json.dumps({
        "grade": grade.group(1).lower() if grade else None,
        "state": state.group(1) if state else None,
        "gender": None,
    })


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
    # The ranking prompt swaps in a DIFFERENT closing rule for strict-type kinds
    # (Conference/Journal): "return EVERY candidate given", which does not contain the
    # phrase above. Without this second signature those two kinds fell through to the
    # empty `{}` at the bottom, and mock mode silently dropped them to keyword-only
    # results while every other kind worked. Matched on the prompt's stable opening line,
    # exactly as _FEATURE_SIGNATURES in app/core.py already does for the same reason.
    if "helping a student find the best-fit extracurricular" in system:
        return mock_rank_candidates(user_content)
    # The merged profile-extraction pass (basics + enriched tags in one call). MUST be
    # tested before the single-purpose branches below: it does their jobs, so its prompt
    # necessarily reads like both of them and would be captured by either.
    if "pulling out everything an opportunity-matching app needs" in system:
        return mock_tags_and_basics(user_content)
    # The "Your Profile" filter facet: tag extraction, tag enrichment, and scoring the
    # visible results against a selected tag.
    if "extracting specific interests, goals, and pursuits" in system:
        return mock_profile_tag_strings(user_content)
    if "interests/goals to the best opportunities" in system:
        return mock_enrich_profile_tags(user_content)
    if "Write directly to them in second person" in system:
        return mock_score_opportunities_for_tag(user_content)
    if "find real, current" in system:
        return mock_venues_via_web()
    if "maintain a single, coherent running profile" in system:
        return mock_synthesize_profile(user_content)
    if "decide whether a student's profile has enough detail" in system:
        return mock_assess_profile_readiness()
    if "exactly THREE distinct" in system:
        return mock_profile_chat_starters()
    if "exactly TEN distinct" in system:
        return mock_profile_chat_starter_pool()
    if "helping a high schooler build a detailed personal profile" in system:
        return mock_profile_chat_question(user_content)
    if "distill a casual chat conversation into new facts" in system:
        return mock_profile_chat_findings(user_content)
    if "pull out a small set of specific profile facts" in system:
        return mock_profile_basics(user_content)
    if "classify and extract structured tracking data" in system:
        return mock_tracker_extract(user_content, with_section=True)
    if "extract structured tracking data" in system:
        return mock_tracker_extract(user_content, with_section=False)
    return json.dumps({})

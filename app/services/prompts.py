"""Every system prompt the app sends to a model, and the call config that goes with it.

SECURITY_HARDENING_PLAN.md S1-1, finding C1.2. **MARQUEE M8** — this file IS "prompt text",
so changing anything between the triple quotes below needs Shama's approval first.

WHY THIS FILE EXISTS. Every one of these strings used to be a literal in
`frontend/src/lib/*.ts`, and therefore shipped verbatim in the web bundle (the security
review verified it by grepping four distinctive phrases out of
`dist/_expo/static/js/web/entry-*.js`). The server was a dumb pipe: `/api/messages` and
`/api/messages-claude` took `system`, `userContent`, `useWebSearch` and `maxTokens` straight
off the request body. So the client-visible contract was literally *"send any prompt, any
input, search on, 8k output"*, and every product guardrail authored in a prompt — the
profile-chat rules, "never invent a date", the eligibility guard, "keep vague statements
vague" — was one curl away from being bypassed by any account holder, on Wingman's keys and
Wingman's bill.

THE CONTRACT NOW. The client posts `{feature, inputs}`. The server owns the prompt text, the
provider, the tool config and the token budget, and REFUSES an unknown feature. There is no
way to reach a model through this app with a prompt of your own.

WHAT WAS LOST, stated honestly. `frontend/src/lib/*` was pure and dependency-injected
precisely so the prompts were testable without a server. That property is gone for the
prompt TEXT (the model-call plumbing is still injected). And prompt edits now ship with the
backend rather than with the bundle.

THE FOUR DEAD PROMPTS ARE DELETED, not ported: assessProfileReadiness, extractProfileBasics
and intakeExtractAndClassify had no callers, and the last of those advertised the exploit
shape most loudly (`useWebSearch: true` with an elaborate multi-step search plan). The
plan's inventory also listed `scoreOpportunitiesForTag` as dead; it is NOT — finder.tsx
calls it from a live effect — so it is ported here as `tag_suggestions`.

Feature ids are exact, which retires `classify_feature`'s substring guess over
`_FEATURE_SIGNATURES`: cost attribution now knows what it is attributing instead of
inferring it from a prompt's opening line, and the "trivially gamed classifier" note in C1
goes with it.
"""
import json

from app.config import (CLAUDE_MAX_TOKENS, MESSAGES_MAX_TOKENS)


class UnknownFeature(ValueError):
    """The client named a feature that does not exist. Always a 400, never a model call."""


class Feature:
    """One AI feature: which provider, what prompt, and how much output it may buy.

    `build(inputs) -> (system, user_content)`. `retry_max_tokens`, when set, means the route
    re-runs the call at that ceiling if the first answer stopped on max_tokens — the
    profile-synthesis retry, moved server-side with the budget it belongs to.

    `cost_feature` is the users_costs/FEATURE_LABELS key. It defaults to the feature id and
    only differs where two ids are genuinely the same product feature (the two chat-starter
    calls), so the console's spend breakdown reads exactly as it did before.
    """

    def __init__(self, provider, build, max_tokens=None, use_web_search=False,
                 retry_max_tokens=None, cost_feature=None):
        self.provider = provider
        self.build = build
        self.max_tokens = max_tokens
        # Every feature here is search-OFF. S0-3 pinned that server-side and this is where
        # the pin now lives: a feature that genuinely needs search has to be given it HERE,
        # by someone who has read M9, rather than by a client sending useWebSearch:true.
        self.use_web_search = use_web_search
        self.retry_max_tokens = retry_max_tokens
        self.cost_feature = cost_feature

    def budget(self, default):
        return self.max_tokens or default


def _text(inputs, key, default=""):
    value = inputs.get(key)
    return value if isinstance(value, str) else default


def _int(inputs, key, default=0):
    try:
        return int(inputs.get(key))
    except (TypeError, ValueError):
        return default


def _list(inputs, key):
    """A list input, or []. Type-checked rather than trusted: `inputs` is client JSON, and a
    builder that raises on a wrong type turns a fat-fingered request into a stack trace."""
    value = inputs.get(key)
    return value if isinstance(value, list) else []


def _dict(inputs, key):
    value = inputs.get(key)
    return value if isinstance(value, dict) else {}


# ============================ profile synthesis (Claude) ============================
#
# Output budget, not a content limit: the profile is rewritten whole on every merge, so the
# answer grows with the profile and a fixed cap eventually cuts it mid-sentence. Unused
# budget is free (billed on tokens produced), so ask generously and retry once at the
# ceiling. There is deliberately no word limit in the prompt, storage, or display.
PROFILE_SYNTH_MAX_TOKENS = 4000
PROFILE_SYNTH_MAX_TOKENS_RETRY = 8000


def _profile_synthesis(inputs):
    is_transcript = bool(inputs.get("isTranscript"))
    existing = _text(inputs, "existing")
    new_text = _text(inputs, "newText")
    transcript_clause = (
        " The NEW INFORMATION is a raw transcript of a chat between this app's bot and the "
        "student, not prose written for you. Use only what the Student lines actually say; "
        "the Bot lines are prompts, not facts about the student, and small talk should be "
        "ignored. Never quote the transcript verbatim — restate what was learned in the "
        "student's first-person voice." if is_transcript else "")
    system = (
        "You maintain a single, coherent running profile of a high school student's academic "
        "and extracurricular interests, built up over multiple sessions. You'll be given the "
        "student's CURRENT profile (may be empty) and NEW information they just added. Merge "
        "the new information in: add genuinely new details, and update or remove anything the "
        "new information supersedes or contradicts. Do not drop specific, still-relevant "
        "details from the current profile just because they weren't repeated in the new "
        "information. Record only what the student actually stated. Do not add specific "
        "details, examples, anecdotes, named topics, numbers, motivations, or reflections they "
        "did not provide — not even plausible ones that would \"fit\". Keep vague statements "
        "vague: if all they said is \"I volunteer\", write only that they volunteer — do NOT "
        "invent what they do, who they help, where they do it, or how they feel about it. If "
        "they said \"I do robotics\", do NOT add what they build or what they've won. "
        "Elaborating a bare statement into a specific scene is fabrication, even when it "
        "sounds realistic. When in doubt, say less. This rule also applies to the CURRENT "
        "profile: if it contains a specific detail the student never stated, treat it as an "
        "earlier fabrication and cut it back to what was actually said, rather than preserving "
        "or extending it. Write it as concise statements in FIRST PERSON, as if the student is "
        "describing themself (e.g. \"I'm interested in...\", \"I've been working on...\", \"My "
        "goal is...\" — not third person, not addressed to the student, not a bulleted list, no "
        "markdown). Structure the output as short paragraphs separated by a blank line (double "
        "newline). General paragraphs (no prefix) should cover academic interests, "
        "extracurriculars, and goals — 1-3 such paragraphs is typical. If the student has "
        "described any larger, longer-term \"marquee\" projects they're personally driving (as "
        "opposed to one-off activities or classes), describe EACH one in its OWN separate "
        "paragraph prefixed with the literal text \"Passion Project: \" — one such paragraph "
        "per distinct project, never combining multiple projects into one paragraph. "
        "Separately, if the student has described any independent research projects (research, "
        "papers, studies they're conducting), describe EACH one in its OWN separate paragraph "
        "prefixed with the literal text \"Research Project: \", same rule — one per project. A "
        "project that fits both categories should be listed under whichever one fits best, not "
        "both. Only include these prefixed paragraphs for projects actually described — don't "
        "fabricate any. If the CURRENT PROFILE ends mid-sentence, or contains a paragraph that "
        "is obviously an incomplete fragment, that is damage from an earlier write that was "
        "cut off short — repair it rather than preserving it verbatim: finish the thought only "
        "if the rest of the profile makes what was meant unambiguous, and otherwise drop the "
        "incomplete fragment. Never invent details to fill such a gap. Respond with ONLY the "
        "updated profile text — no preamble, no quotes around it."
        + transcript_clause)
    user_content = (
        f"CURRENT PROFILE:\n{existing or '(empty — nothing recorded yet)'}\n\n"
        f"NEW INFORMATION TO ADD{' (raw chat transcript)' if is_transcript else ''}:\n"
        f"{new_text}\n\nRespond with the updated, merged profile text only.")
    return system, user_content


# ============================ profile chat (Claude) ============================

STARTER_POOL_SIZE = 10
STARTERS_PER_OPEN = 3

_CHAT_PREAMBLE = (
    "You are a friendly, upbeat chatbot helping a high schooler build a detailed personal "
    "profile for finding extracurricular opportunities (research programs, internships, "
    "competitions, summer programs).")


def _chat_starter_pool(inputs):
    text = _text(inputs, "profileText")
    system = (
        _CHAT_PREAMBLE + " You'll be given their CURRENT PROFILE SUMMARY. Come up with "
        "exactly TEN distinct, short, fun, wacky-but-meaningful icebreaker questions, each "
        "capable of opening a chat session on its own, probing for details the profile is "
        "missing or only has shallowly — think music, sports/athletics, hobbies, what they do "
        "purely for fun, family or community involvement, leadership moments, part-time jobs, "
        "quirks of personality, or deeper specifics on things already mentioned. Every "
        "question must be ONE short, plain sentence — never a run-on, never two questions "
        "joined with \"and\"/\"or\"/a semicolon. When a question draws on the profile, pull in "
        "at most 2-3 specific details from it at a time — don't try to connect four or more "
        "dots into one elaborate question. Keep the tone playful and casual, like a clever "
        "friend riffing with them, not a form — but every question must serve a real purpose "
        "in understanding this student for extracurricular/college-application matching. "
        "These ten are shown a few at a time across several visits, so keep them varied and "
        "non-overlapping with each other. Respond with ONLY a JSON array of exactly 10 short "
        "question strings, e.g. [\"...\", ...] — no markdown, no preamble, no numbering.")
    user_content = (f"CURRENT PROFILE SUMMARY:\n{text or '(empty)'}\n\n"
                    f"Respond with a JSON array of exactly {STARTER_POOL_SIZE} questions only.")
    return system, user_content


def _chat_starters(inputs):
    profile_text = _text(inputs, "profileText")
    chat_rounds = _int(inputs, "chatRounds")
    breadth = (
        " The student explicitly asked to regenerate these — swap in a fresh set. Prioritize "
        "BREADTH over depth: favor surfacing entirely new areas of their life the profile "
        "hasn't touched at all (academics, social life, jobs, family, random obsessions, "
        "sports, art, gaming, etc.) over drilling further into what's already well-covered. "
        "Where a question does build on something they've already mentioned, use it only as a "
        "springboard to go one layer deeper on that specific thing — but most of the three "
        "should open up completely uncovered territory rather than deepen existing ones."
        if inputs.get("regenerate") else "")
    system = (
        _CHAT_PREAMBLE + " You'll be given their CURRENT PROFILE SUMMARY (may be empty). Come "
        "up with exactly THREE distinct, short, fun, wacky-but-meaningful icebreaker questions "
        "to kick off a chat session that probes for details the profile is missing or only has "
        "shallowly — think music, sports/athletics, hobbies, what they do purely for fun, "
        "leadership, part-time jobs, quirks of personality, or deeper specifics on things "
        "already mentioned." + breadth + " Every question must be ONE short, plain sentence — "
        "never a run-on, never two questions joined with \"and\"/\"or\"/a semicolon. When a "
        "question draws on the profile, pull in at most 2-3 specific details from it at a time "
        "— don't try to connect four or more dots into one elaborate question. Keep each one "
        "playful and casual, like a clever friend riffing with them, not a form — but each "
        "must serve a real purpose in understanding this student for "
        f"extracurricular/college-application matching. This is chat round {chat_rounds + 1} "
        "of them returning to this page — the higher that number, the more specific and "
        "creative the questions should get. Respond with ONLY a JSON array of exactly 3 short "
        "question strings, e.g. [\"...\", \"...\", \"...\"] — no markdown, no preamble, no "
        "numbering.")
    user_content = (f"CURRENT PROFILE SUMMARY:\n{profile_text or '(empty)'}\n\n"
                    "Respond with a JSON array of exactly 3 starter questions only.")
    return system, user_content


def _profile_chat(inputs):
    profile_text = _text(inputs, "profileText")
    chat_rounds = _int(inputs, "chatRounds")
    lines = []
    for message in _list(inputs, "history"):
        if not isinstance(message, dict):
            continue
        who = "You" if message.get("role") == "bot" else "Student"
        lines.append(f"{who}: {_text(message, 'text')}")
    transcript = "\n".join(lines) or "(nothing yet)"
    system = (
        _CHAT_PREAMBLE + " You'll be given their CURRENT PROFILE SUMMARY (may be empty) and "
        "the CONVERSATION SO FAR in this session. Ask exactly ONE short, fun, "
        "wacky-but-meaningful question. If their last answer introduced something specific — a "
        "project, a role, a place, a result — follow up on THAT rather than changing the "
        "subject: ask what exactly they did, what their part in it was, what surprised them, "
        "or what they'd change. Only open a new topic when the last answer was thin or the "
        "thread is genuinely exhausted, and then favour ground the profile hasn't covered "
        "(music, sports/athletics, hobbies, family or community involvement, leadership "
        "moments, part-time jobs, quirks of personality). Your question must be ONE short, "
        "plain sentence — never a run-on, never two questions joined with \"and\"/\"or\"/a "
        "semicolon. Draw on at most 2-3 specific details at a time — don't try to connect four "
        f"or more dots into one elaborate question. This is chat round {chat_rounds + 1} of "
        "them returning to this page — the more rounds, the more specific and creative your "
        "questions should get; don't repeat ground already covered earlier in this "
        "conversation. Keep your tone playful and casual, like a clever friend riffing with "
        "them, not a form — but every question must serve a real purpose in understanding this "
        "student for extracurricular/college-application matching. No lists, no markdown, no "
        "preamble, and no \"Great!\" acknowledgment beyond at most a few words of playful "
        "reaction folded into the same sentence.")
    user_content = (f"CURRENT PROFILE SUMMARY:\n{profile_text or '(empty)'}\n\n"
                    f"CONVERSATION SO FAR:\n{transcript}\n\n"
                    "Respond with your next single question only — no preamble, no quotes "
                    "around it.")
    return system, user_content


# ============================ profile tags + basics (Gemini) ============================

# The whole extraction runs to a size the profile decides, so it asks for the ceiling.
# Unused budget is free (billing is on tokens produced), the same reason synthesis asks
# generously.
TAG_EXTRACT_MAX_TOKENS = 8000
# An enrichment object is a tag (<= 60 chars) plus a one-sentence intent plus 2-3 short next
# steps; ~90 tokens covers one comfortably, and the overhead term leaves room for JSON
# scaffolding and for Gemini 3.x thinking tokens, which draw from this SAME budget.
ENRICH_TOKENS_PER_TAG = 90
ENRICH_TOKEN_OVERHEAD = 600

# The three "basics" fields and the never-guess rule, shared by both prompts that read them
# so neither can drift into describing the same fields differently.
PROFILE_BASICS_RULE = (
    '"grade" (their school year, e.g. "11th grade"), "state" (US state or region they live '
    'in, spelled out), "gender". Set a key to null if the student did not say it — never '
    'guess, never infer from stereotypes, and never fill a value in just to avoid a null.')


def enrich_budget_for(tag_count):
    return ENRICH_TOKEN_OVERHEAD + max(0, tag_count) * ENRICH_TOKENS_PER_TAG


def _tag_intent(inputs):
    tags = [t for t in _list(inputs, "tags") if isinstance(t, str)]
    system = """You are helping match a high school student's interests/goals to the best opportunities. You will be given a list of the student's profile themes — each one covers a whole area of what they do, not a single project. Analyze EACH theme for what it represents and what would best help them grow.

Return ONLY a JSON array with one object per tag, in the same order as given, no other text:
[{
  "tag": "the tag string exactly as given",
  "intent": "what they want out of this whole area (1 short sentence)",
  "nextSteps": ["2-3 short, logical milestones for the AREA, e.g. Master advanced techniques", "Enter competitions"]
}]

Return an object for EVERY tag listed, however many there are — do not stop early and do not summarise. Keep every field short so the whole array fits in one response."""
    listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(tags))
    user_content = (f"PROFILE THEMES:\n{listing}\n\n"
                    f"Return the JSON array of {len(tags)} enrichment objects.")
    return system, user_content


def _profile_extract(inputs):
    text = _text(inputs, "text")
    system = f"""You are reading a high school student's profile and pulling out everything an opportunity-matching app needs from it, in ONE pass. Return ONLY a raw JSON object, no markdown and no preamble, with exactly two keys: "basics" and "tags".

"basics" is an object with exactly these keys: {PROFILE_BASICS_RULE}

"tags" is the student's whole profile reduced to a small set of BROAD THEMES. This is a filter facet, not a resume: each theme becomes one row in a dropdown, and picking it searches a catalog of programs, competitions and internships for things that fit it. A theme nobody could search for is a wasted row.

First sweep the profile for raw material - current projects and research; interests they want to go deeper in; interests mentioned but never started; academic goals such as competitions, scores and certifications; career or industry aspirations; leadership and organizing; service and volunteering; hobbies and crafts. Then GROUP that material into themes. The grouping is the job; the sweep only makes sure nothing is missed.

Two rules govern the grouping:

MUTUALLY EXCLUSIVE - every item belongs to exactly ONE theme. When an item could sit in two, put it where the opportunities that would help with it live: a chatbot built to learn AI belongs with studying AI, a chatbot being sold to users belongs with building products; a physics olympiad score belongs with competitions, not with physics as a subject. If two themes would surface the same programs, they are one theme - merge them.

COLLECTIVELY EXHAUSTIVE - everything in the profile lands somewhere, and nothing is dropped for being small, old or unimpressive. A single passing mention joins the nearest theme ONLY when it genuinely fits that direction; if nothing related exists, it becomes its own theme rather than being fused onto an unrelated one.

ONE DIRECTION PER THEME - a theme covers a single searchable direction. Never join two unrelated areas with "and" or a comma just to place a stray item (WRONG: "Community service and baking" - volunteering and baking are searched for in completely different places). A conjunction is allowed only when both halves serve the SAME catalog search (OK: "Cooking and baking from scratch", where both are culinary).

Get the altitude right. A theme names a DIRECTION the student is pursuing, pitched at the level a program is described:
- TOO SPECIFIC - one project, club, role, event, organization or achievement. Never emit these: "Founded Linguistics Club", "Organized school Trivia Night", "Volunteering with Kids Coming Together", "Improving USAPhO score", "Making fresh pasta from scratch".
- RIGHT - "Organizing student clubs and enrichment events", "Volunteering with organizations that serve children", "Competing in STEM olympiads and contests", "Cooking and baking from scratch".
- TOO BROAD - a whole field of human activity: "STEM", "Science", "The arts", "Community service", "Leadership". If a theme would match most of a catalog of extracurriculars, split it.
Test each theme: it should either cover TWO OR MORE things in the profile, or be a standing interest broad enough that several different programs could serve it. If a theme covers only one line BECAUSE it is a single project, club, role, event or achievement, widen it to the area it belongs to. But if that one line is itself a distinct, searchable interest with no related sibling in the profile (e.g. baking, when nothing else culinary appears), let it stand as its own theme - do NOT fuse it onto an unrelated area just to avoid a one-line theme.

Past and present merge. Something done last year and something happening now belong to the same theme when they point the same way. Write every theme in the present, as an ongoing direction, never as a past accomplishment.

Use as many themes as there are genuinely distinct, searchable directions - a rich profile often lands around 6-12 and a thin one around 3-5, but that is a rough guide, not a target to hit: never merge distinct areas, and never split one area, just to reach a number. Never return one theme per profile line, and never invent a theme for something the profile does not say. Order them most important first: the themes carrying the most of the profile, and the ones the student says they want to go further in.

Before returning, re-check every theme that joins two things with "and" or a comma: if a single catalog search would not serve both halves, split them into separate themes.

Each entry is an object:
{{
  "tag": "the theme, 3-8 words, plain and searchable, max 60 characters",
  "intent": "what the student wants out of this whole area (1 short sentence)",
  "nextSteps": ["2-3 short milestones for the AREA, e.g. Enter a national competition"]
}}

Worked example - a profile mentioning a Linguistics Club she founded, a Math Club she co-founded, a school Trivia Night she ran, tutoring friends in chemistry, and two years volunteering at a children's outdoor program yields TWO themes, not five: "Organizing student clubs and enrichment events" and "Mentoring and volunteering with young people"."""
    user_content = (f"STUDENT PROFILE:\n\n{text}\n\n"
                    'Return the JSON object with "basics" and "tags" only.')
    return system, user_content


# ============================ ranking (Gemini) ============================

# Real headroom (clamped to MESSAGES_MAX_TOKENS_CEILING): the WOW reasons are 20-40 words x
# up to 12 items, AND Gemini 3.x thinking tokens draw from this SAME budget — at the 2000
# default the JSON truncated mid-array, the parse failed, and the WHOLE batch of reasons
# vanished. Unused budget is free (billing is on tokens produced).
RANK_MAX_TOKENS = 6000

# The subject vocabulary, verbatim from frontend/src/lib/constants.ts. It has to exist in
# both places — the model is TOLD this list here, and the client FILTERS the answer against
# it there — so a test asserts the two are identical rather than trusting that they are.
# Drift would silently drop valid subjects on the floor.
VALID_SUBJECTS = [
    "Mixed", "STEM", "Medicine", "Humanities", "Art", "Business", "Engineering",
    "Computer Science", "Mathematics", "Biology", "Physics", "Astronomy", "Chemistry",
    "Leadership", "Law", "Logic", "Education",
]


def _infer_subjects(inputs):
    system = ("You infer which subject categories from a fixed list best match a student's "
              "passion-project description. Valid categories (use these exact strings): "
              + ", ".join(VALID_SUBJECTS) +
              ". Respond with ONLY a raw JSON array of 2-5 of the most relevant category "
              "strings, no markdown, no preamble. Example: "
              "[\"Computer Science\",\"STEM\",\"Mathematics\"]")
    return system, _text(inputs, "description")


def _ranking(inputs):
    description = _text(inputs, "description")
    prefs = _text(inputs, "prefs")
    candidates = _list(inputs, "candidates")
    # requireAll (strict-type kinds like Conference/Journal Venue): rank every candidate
    # rather than omitting weak fits, or a tiny real pool zeroes out into a false "no
    # matches".
    selection_rule = (
        "Rank and return EVERY candidate given — this is an exhaustive list of the only known "
        "real options of this type, so do not omit any even if the fit is loose."
        if inputs.get("requireAll") else
        "Select ONLY the opportunities that would genuinely help them grow this specific "
        "project, build relevant skills, get recognition for it, or connect with the right "
        "community — not just anything thematically adjacent. Leave out weak or generic fits "
        "entirely; every opportunity you return must be a genuinely good match. Rank the best "
        "10-12 matches only.")
    system = (
        "You are Wingman, helping a student find the best-fit extracurricular opportunities "
        "(programs, internships, competitions, research positions) for their specific passion "
        "project, from a candidate list. Read their project description and preferences "
        f"carefully. {selection_rule} For each, write the reason as the WOW moment — it should "
        "make the student feel genuinely SEEN, like a mentor who knows both them and this "
        "opportunity picked it for them. Draw a concrete line connecting TWO specific halves: "
        "(1) a SPECIFIC thing about THEM from their description/preferences below — a project, "
        "skill, goal, or next step they stated — and (2) a SPECIFIC thing THIS opportunity "
        "actually offers, from its own name/summary — what they would build, compete in, "
        "publish, research, or walk away with. The more precisely the two halves lock "
        "together, the better. Write 1-2 sentences, roughly 20-40 words; every clause must "
        "carry real information, never filler. GOAL-FORMAT: when their stated goal or next "
        "step names an outcome, prefer AND frame opportunities whose FORMAT delivers it — "
        "\"publish\" → a journal or conference; \"compete/win\" → a competition; \"get "
        "mentorship / go deeper\" → a research program or lab; \"launch/build a product\" → a "
        "build program, accelerator, or hackathon. GOOD: \"You're taking Adio from concept to "
        "market — this accelerator gets you to your first real users and a pitch in front of "
        "investors.\" GOOD: \"Your grapheme-to-phoneme research is exactly what this olympiad "
        "rewards — you'd crack original computational-linguistics problems against the "
        "strongest students.\" BAD (thin, could be anyone): \"Great fit for your software "
        "interest.\" BAD (vague filler): \"A wonderful chance to learn and grow.\" BAD "
        "(invented — never do this): naming a mentor, prize, cohort size, or feature the "
        "opportunity's own text never states. Use ONLY real details from their description and "
        "the opportunity's own text, and write it in second person (\"you\"/\"your\"), never "
        "third person (\"the student\"/\"their\"). Assign a tier: 'strong' (excellent, highly "
        "specific fit) or 'look' (solid, worth a look). Respond with ONLY a raw JSON array, no "
        "markdown, no preamble, no text after the array, matching: "
        "[{\"id\":\"...\",\"reason\":\"...\",\"tier\":\"strong|look\"}]. Stay within a "
        "1500-token response; 10-12 items is a hard cap.")
    prefs_text = f"\n\nStudent preferences: {prefs}" if prefs else ""
    user_content = (f"Student's passion project:\n{description}{prefs_text}\n\n"
                    f"Candidate opportunities (JSON):\n{json.dumps(candidates)}\n\n"
                    "Select and rank the best matches per the schema.")
    return system, user_content


def _tag_suggestions(inputs):
    tag = _dict(inputs, "tag")
    opps = _list(inputs, "opps")
    lines = []
    for o in opps:
        if not isinstance(o, dict):
            continue
        lines.append(f"ID: {o.get('id')} | Name: {o.get('name')} | Type: {o.get('type')} | "
                     f"Summary: {o.get('summary') or '(no description)'}")
    next_steps = ", ".join(s for s in _list(tag, "nextSteps") if isinstance(s, str))
    system = ("You are helping a student find opportunities that match their interests and "
              "goals. Write directly to them in second person (using \"you\").")
    user_content = f"""STUDENT'S PROFILE TAG: "{_text(tag, 'tag')}"
INTENT: {_text(tag, 'intent') or '(no intent specified)'}
NEXT STEPS: {next_steps or '(no specific steps)'}

OPPORTUNITIES TO RANK:
{chr(10).join(lines)}

Rank these opportunities by relevance to this student's profile. Return JSON array with only genuinely relevant opportunities:
[
  {{ "id": "opp_id", "rank": 1, "reasoning": "Brief 1-sentence message directly to the student using 'you' language" }},
  ...
]

For each reasoning, write directly to the student as if you're the app speaking to them. Omit opportunities that don't align with the profile. Include only good/strong matches.
Return ONLY valid JSON, no markdown, no preamble."""
    return system, user_content


# ============================ tracker extraction (Gemini) ============================
#
# Web search is OFF, deliberately. meta/fit are descriptive — what the program is, from the
# catalog's own summary/eligibility/price/location — and a search-ON prompt demanding dates
# it can no longer emit is the exact "search theater" failure this repo documents. With no
# dates asked for, there is nothing here that needs a source.

def _tracker_extract(inputs):
    opp = _dict(inputs, "opp")
    system = """You extract structured tracking data for an extracurricular opportunity (program, internship, competition, or research position), for a high-school student's tracker.

You have NO web access in this call, and only two DESCRIPTIVE fields are wanted. Dates, deadlines, status and application tasks are researched and verified separately — NEVER include, estimate or mention any date, deadline, or application requirement here.

From the catalog details given, write:
- "meta": one short line of practical facts — location / cost / format / organizer — separated by ' · '. Use only facts stated in the given details; omit anything unknown rather than guessing. No dates.
- "fit": one sentence, under 25 words, on what this actually involves for a student.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON: {"meta":"...","fit":"..."}."""
    extra = ""
    if opp.get("eligibility"):
        extra += f"Eligibility (from catalog): {opp['eligibility']}\n"
    if opp.get("price"):
        extra += f"Cost (from catalog): {opp['price']}\n"
    if opp.get("location"):
        extra += f"Location (from catalog): {opp['location']}\n"
    user_content = (f"Opportunity: {_text(opp, 'name')} ({_text(opp, 'org')})\n"
                    f"URL: {_text(opp, 'url')}\n"
                    f"Known info: {_text(opp, 'summary')}\n"
                    f"{extra}\n"
                    "Write the two descriptive fields per the schema, from these details only.")
    return system, user_content


# ============================ the registry ============================
#
# A feature that is not in this dict cannot be reached. That IS the fix: there is no longer
# any way to send a prompt of your own through this app.
FEATURES = {
    "profile_synthesis": Feature("claude", _profile_synthesis,
                                 max_tokens=PROFILE_SYNTH_MAX_TOKENS,
                                 retry_max_tokens=PROFILE_SYNTH_MAX_TOKENS_RETRY),
    # Both chat-starter calls bill as one product feature, so the console's spend breakdown
    # reads exactly as it did when a substring guess produced it.
    "chat_starter_pool": Feature("claude", _chat_starter_pool,
                                 cost_feature="chat_starters"),
    "chat_starters":     Feature("claude", _chat_starters),
    "profile_chat":      Feature("claude", _profile_chat),
    "tag_intent":        Feature("gemini", _tag_intent),
    "profile_extract":   Feature("gemini", _profile_extract,
                                 max_tokens=TAG_EXTRACT_MAX_TOKENS),
    "infer_subjects":    Feature("gemini", _infer_subjects),
    "ranking":           Feature("gemini", _ranking, max_tokens=RANK_MAX_TOKENS),
    "tag_suggestions":   Feature("gemini", _tag_suggestions),
    "tracker_extract":   Feature("gemini", _tracker_extract),
}


def get_feature(name):
    """The Feature for `name`, or raise UnknownFeature. Never guesses, never falls back."""
    feature = FEATURES.get(name) if isinstance(name, str) else None
    if feature is None:
        raise UnknownFeature(name)
    return feature


def build(name, inputs):
    """(feature, system, user_content, max_tokens) for one request."""
    feature = get_feature(name)
    inputs = inputs if isinstance(inputs, dict) else {}
    system, user_content = feature.build(inputs)
    default = CLAUDE_MAX_TOKENS if feature.provider == "claude" else MESSAGES_MAX_TOKENS
    max_tokens = feature.budget(default)
    # tag_intent's budget is a function of how many tags were asked for, and only the
    # request knows that. Sized server-side from the input rather than sent by the client.
    if name == "tag_intent":
        max_tokens = enrich_budget_for(len(_list(inputs, "tags")))
    return feature, system, user_content, max_tokens

"""The importable core of the scraper: row building, id minting, inserting, in-run dedupe.

WHY THIS FILE EXISTS. CLAUDE.md's rule is that `agents/` is for something an operator RUNS and
`wingman/` is for something other code IMPORTS. `agents/mine_hub_pages.py`,
`agents/harvest_names.py` and `agents/refind_dead_links.py` all import build_row /
next_id_generator / insert_rows / the FLAG_* constants from `agents/scrape_opportunities.py` —
so three agents were reaching into a 1,600-line runnable agent to borrow its plumbing, dragging
its prompts, its argparse and its module state along with them. Every one of them pays the
scraper's import cost, and a change to the scraper's internals can break an agent that never
mentions it.

This is the borrowed half, moved to the layer meant for borrowing. Extracted VERBATIM — no
behaviour changed in the move — and `agents/scrape_opportunities.py` re-exports every name, so
nothing that already imported from there breaks and eval/grade_scraper_batch keeps grading the
same functions.

DELIBERATELY LEFT IN THE AGENT: the prompts (M8), reconcile_url and resolve_url_truth (M4 sits
on reconcile_url and the scraper's own loop is its only caller), and classify_same_url /
merge_row / apply_merge — the merge logic, which by operator decision 9 (2026-09-05) is not to
be touched at all, a pure move included.
"""
import datetime
import json
import urllib.error
import urllib.parse

from wingman import queue_flags
from wingman import url_dedupe
from wingman.agent_common import clean_email
from wingman.supabase_common import supabase_post

VALID_SUBJECTS = ['Mixed', 'STEM', 'Medicine', 'Humanities', 'Art', 'Business', 'Engineering',
                   'Computer Science', 'Mathematics', 'Biology', 'Physics', 'Astronomy',
                   'Chemistry', 'Leadership', 'Law', 'Logic', 'Education']
VALID_TYPES = {'Program', 'Internship', 'Competition', 'Research', 'Volunteer', 'Journal', 'Conference'}
VALID_PRICE = {'Free', 'Paid'}
VALID_LOCATION = {'In-Person', 'Remote', 'In-Person and Remote'}
VALID_INTL = {'International Students', 'Domestic Students'}
VALID_SEASON = {'Summer', 'Year-Long', 'Spring', 'Fall', 'Winter'}

# Dropped when matching a candidate's name against a grounding span: they carry no
# identifying signal and appear in almost every opportunity name, so requiring them would
# only make the match brittle.
_NAME_STOPWORDS = {"the", "a", "an", "of", "for", "and", "at", "in", "on", "to", "program",
                   "programs", "summer", "high", "school", "students", "student"}

# Review flags. The console renders each as a pill and truncates the visible text, so these
# must stay SHORT and must say what the reviewer should go and check — a flag that only
# names a symptom makes someone re-derive the diagnosis row by row.
FLAG_DEAD_LINK = "dead link (404) — program may be real; find the correct URL"
FLAG_BLOCKED_LINK = "link unverifiable ({code}) — site blocks checks; open it manually"
FLAG_UNREACHABLE = "link timed out — could not reach the site; open it manually"
FLAG_BARE_DOMAIN = "URL is a site homepage, not a program page"
FLAG_LOW_VALUE = "URL is a sub-page (faq/about/apply), not the main page"
FLAG_NOT_SEARCHED = "not search-verified — model answered from memory; check every field"
FLAG_URL_UNSOURCED = "URL not among the pages actually searched — may be from memory"
FLAG_URL_REPLACED = "URL replaced with the page the search returned — confirm it matches"
# Live, not a homepage, not a sub-page — and still the wrong page. 10 of the 166 rows on
# 2026-08-23 stored an SEO round-up ("19 Selective Internships for High School Students")
# that only mentions the program. Every other URL check passes those, so this is the one
# flag that catches them. See url_validate.domain_matches_org().
FLAG_OFFSITE = "URL is on an unrelated site — may be an article about it, not its own page"
FLAG_NO_TYPE = "no valid type returned — set one before activating"
# Phase-2 URL truth. A rescued URL was moved off a listicle/mill onto the program's own page
# (verified on-domain + title-proven); a reviewer still confirms it matches. An unproven title
# is never a rejection — false negatives like "Algebra II" vs "Algebra 2" are the accepted cost.
FLAG_URL_RESCUED = "URL rescued to the program's own page (was on {domain}) — confirm it matches"
FLAG_TITLE_UNPROVEN = "page title does not clearly name this program — confirm it's the right page"
# Stage 1b (discovery). Discovery returns a NAME with no URL; a per-name search then found and
# title-proved its own page. Same evidence bar as harvest_names (title_proves), so it is not a
# guess — but it is worth a reviewer's glance that the resolved page is the right program.
FLAG_URL_RESOLVED = "URL found by a per-name search (stage 1b) — confirm it's the right program"
# Phase-3 uniqueness. A row sharing a program's HOMEPAGE with another may be a genuinely
# distinct program that just has no page of its own (tenet 6), so it is kept + flagged rather
# than auto-merged — a person confirms it's distinct or a duplicate.
FLAG_SHARES_HOMEPAGE = "shares a homepage URL with {id} — confirm distinct program vs duplicate"

def next_id_generator(existing_ids, supabase_url=None, service_key=None, block=25):
    """Fresh `ec<n>` ids, from the DB sequence when there is one and max+1 otherwise.

    The max+1 half is correct ONLY while a single agent is writing — it reads the maximum once,
    at run start, so two overlapping runs hand out the same ids and the second insert dies on
    the primary key (audit 4.2). wingman/run_lock.py is what guarantees the single writer.

    `next_opportunity_id` (db/agent_locks_schema.sql) is the belt to that braces: nextval is
    atomic and never rolls back, so ids stay unique even if a lock is bypassed. Drawn `block`
    at a time to keep this to one round trip per 25 rows rather than one per row. Unused ids in
    a block are simply skipped — a sequence has no obligation to be gapless, and a gap costs
    nothing here.

    Falls back silently when the function is absent (a checkout where the .sql has not been
    run): the ids are still correct, they just depend on the lock rather than on Postgres.
    """
    nums = [int(i[2:]) for i in existing_ids if i.startswith("ec") and i[2:].isdigit()]
    n = (max(nums) if nums else 18220) + 1

    def local():
        nonlocal n
        while True:
            yield f"ec{n}"
            n += 1

    fallback = local()
    if not (supabase_url and service_key):
        yield from fallback
        return

    from wingman import run_lock
    sequence_ok = True
    while True:
        if sequence_ok:
            ids = run_lock.mint_ids(supabase_url, service_key, block, fallback)
            # mint_ids falls back to the local generator on any failure; detect that by the
            # ids not being ahead of the snapshot maximum, and stop paying for the round trip.
            sequence_ok = bool(ids) and all(i.startswith("ec") for i in ids)
            for i in ids:
                yield i
        else:
            yield from fallback


def clean_value(value, valid_set):
    return value if value in valid_set else None


def _netloc(url):
    try:
        return urllib.parse.urlsplit(url or "").netloc
    except ValueError:
        return ""

def build_row(candidate, mint_id, source, url, flags):
    """One catalog row, or None if it has no name/url to be identified by.

    No seed category any more: `type` is the model's answer or nothing. `category` is not
    written at all — it is nullable and already NULL on 1139 of 1440 catalog rows, and
    nothing in the student-facing app reads it.
    """
    name = (candidate.get("name") or "").strip()
    if not name or not url:
        return None
    tags = candidate.get("subject_tags") or []
    if not isinstance(tags, list):
        tags = [tags] if tags else []
    # `type` is non-null on every one of the 1440 catalog rows, so an invalid one cannot
    # simply be left empty. Park it on the most common value to get the row inserted; the
    # CALLER attaches FLAG_NO_TYPE so a reviewer sets it properly before activating. Do not
    # re-derive a type from the seed here — that fallback is exactly what was removed.
    opp_type = candidate.get("type") if candidate.get("type") in VALID_TYPES else "Program"
    return {
        "id": mint_id,
        "name": name,
        "org": (candidate.get("org") or "").strip() or None,
        "summary": candidate.get("summary"),
        "url": url,
        "type": opp_type,
        "price": clean_value(candidate.get("price"), VALID_PRICE),
        "state": (candidate.get("state") or None),
        "location": clean_value(candidate.get("location"), VALID_LOCATION),
        "intl": clean_value(candidate.get("intl"), VALID_INTL),
        "season": clean_value(candidate.get("season"), VALID_SEASON),
        "eligibility": candidate.get("eligibility"),
        "grade_min": candidate.get("grade_min") if isinstance(candidate.get("grade_min"), int) else None,
        "grade_max": candidate.get("grade_max") if isinstance(candidate.get("grade_max"), int) else None,
        "cost": candidate.get("cost_detail"),
        "subject_tags": tags or None,
        "contact_email": clean_email(candidate.get("contact_email")),
        "is_active": False,
        "source": source,
    }

def gate_dup_candidates(hint_candidates, by_id, existing_dups):
    """Turn combined_reader's dedupe hints ({id,score,reason}) into the console's dup_candidates
    shape ({id,name,url,confidence,reason,via}), enriched with the survivor's name/url from the
    catalog, MERGED with any url_dedupe candidates already on the row (this gate's own prior
    entries replaced, url_dedupe's kept). Pure. A HINT only — it never rejects.
    """
    fresh = []
    for c in (hint_candidates or []):
        survivor = by_id.get(c.get("id"), {})
        fresh.append({
            "id": c.get("id"), "name": survivor.get("name"), "url": survivor.get("url"),
            "confidence": "hint", "reason": c.get("reason") or "content similarity",
            "via": queue_flags.DEDUPE_VIA,
        })
    return queue_flags.merge_candidates(existing_dups, fresh)


ATTRIBUTION_KEYS = ("seed_id", "found_via")

# PostgREST reports an unknown column as 42703 on a read and PGRST204 on a write; an unknown
# TABLE reports 42P01/PGRST205. All four mean "a migration has not been run" and NOTHING else.
# Same list agents/check_links.py and app/core.py already use — kept spelled out here rather
# than imported so this agent stays runnable on its own.
_SCHEMA_ERROR_CODES = ("42703", "PGRST204", "PGRST205", "42P01")


def _http_detail(exc):
    """The JSON body of a PostgREST error, or {}. The body reads only once."""
    if not isinstance(exc, urllib.error.HTTPError):
        return {}
    try:
        return json.loads(exc.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _is_missing_column(exc):
    """True ONLY when PostgREST rejected the write because a migration is pending.

    Load-bearing: it is what stops the insert ladder from treating a statement timeout, a
    5xx, a PK collision or a malformed jsonb value as "that column does not exist yet".
    """
    return _http_detail(exc).get("code") in _SCHEMA_ERROR_CODES


def _without(rows, keys):
    drop = set(keys)
    return [{k: v for k, v in r.items() if k not in drop} for r in rows]


def insert_rows(supabase_url, service_key, rows, review_by_id):
    """Insert with the review + attribution columns, degrading if either migration is pending.

    PostgREST rejects an entire insert on one unknown key, so a single missing column would
    mean the whole scrape wrote NOTHING — reading as "the agent found nothing" rather than
    "every insert 400'd". Two INDEPENDENT migrations can be missing here: the review columns
    (moderation_status/dup_candidates/quality_flags — db/user_submissions_schema.sql) and the
    attribution columns (seed_id/found_via — db/scraper_attribution_schema.sql). Either can be
    present without the other, so the ladder tries all four combinations widest-first and
    keeps the maximal set the DB actually supports — a live DB with both applied always takes
    the full path. Returns the tier that succeeded. `rows` carry seed_id/found_via on the base
    dict, so the attribution-dropping tiers strip them explicitly.
    """
    full = [{**row, **review_by_id.get(row["id"], {})} for row in rows]
    attempts = [
        ("full", full),                                 # both migrations present
        ("no-attribution", _without(full, ATTRIBUTION_KEYS)),  # review present, attribution not
        ("no-review", rows),                            # attribution present, review not
        ("minimal", _without(rows, ATTRIBUTION_KEYS)),  # neither present
    ]
    for i, (tier, payload) in enumerate(attempts):
        try:
            supabase_post(supabase_url, "opportunities", payload, service_key)
            return tier
        except Exception as e:
            # ONLY a pending migration may narrow the column set. Anything else — a statement
            # timeout, a 5xx, a PK collision, a malformed jsonb value — is a real failure and
            # must surface. It used to degrade on ANY exception, and the damage was silent
            # twice over: a transient error inserted the batch stripped of moderation_status /
            # dup_candidates / quality_flags / seed_id (the whole review-queue payload, so the
            # rows landed invisible to the console), and because supabase_post batches at 500 a
            # half-written batch was re-POSTed with duplicate ids at every tier below.
            if not _is_missing_column(e):
                raise
            if i == len(attempts) - 1:
                raise  # the minimal write is base columns only — a failure here is real
            print(f"[WARN] Insert tier '{tier}' failed ({e}); trying a narrower column set. "
                  f"Run db/user_submissions_schema.sql and db/scraper_attribution_schema.sql to "
                  f"keep review flags and seed attribution.")

def _url_rank(url, flags):
    """Phase-2 URL quality, higher is better: title-proven > not-low-value > shallower path.
    Uses the flags already computed for the row (FLAG_TITLE_UNPROVEN as the proof proxy) so it
    needs no extra fetch."""
    proven = 0 if FLAG_TITLE_UNPROVEN in (flags or []) else 1
    not_low = 0 if url_dedupe.is_low_value_path(url) else 1
    try:
        depth = len([s for s in urllib.parse.urlsplit(url or "").path.split("/") if s])
    except ValueError:
        depth = 99
    return (proven, not_low, -depth)


def collapse_intra_run_twins(rows, flags_by_id):
    """Collapse rows minted THIS run that are same-registrable-domain AND name_similarity >= 0.9
    down to the copy whose URL wins the Phase-2 ranking. Returns (kept_rows, collapsed), where
    collapsed is [{"loser","winner","name"}]. This is a LOOSER rule than the catalog dedupe and
    is applied to in-run rows ONLY — it never touches the real catalog, so a false collapse
    costs at most one freshly-scraped row, not a curated one."""
    kept, collapsed = [], []
    for row in rows:
        dom = url_dedupe.registrable_domain(_netloc(row.get("url")))
        twin = None
        for k in kept:
            if (dom and url_dedupe.registrable_domain(_netloc(k.get("url"))) == dom
                    and url_dedupe.name_similarity(row.get("name") or "", k.get("name") or "") >= 0.9):
                twin = k
                break
        if not twin:
            kept.append(row)
            continue
        if _url_rank(row.get("url"), flags_by_id.get(row["id"])) > \
                _url_rank(twin.get("url"), flags_by_id.get(twin["id"])):
            kept[kept.index(twin)] = row          # new row's URL wins; it replaces the twin
            collapsed.append({"loser": twin["id"], "winner": row["id"], "name": twin.get("name")})
        else:
            collapsed.append({"loser": row["id"], "winner": twin["id"], "name": row.get("name")})
    return kept, collapsed


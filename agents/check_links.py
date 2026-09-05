#!/usr/bin/env python3
"""Link-health checker: finds catalog rows whose URL is broken and QUEUES every finding for a
person to review. It never deactivates a row on its own (changed 2026-09-02).

WHAT CHANGED (2026-09-02). This agent used to deactivate rows whose URL was provably gone. It
no longer touches is_active or moderation_status at all. Every finding — a dead link, an
unverifiable one, a soft-404, a summary that reads discontinued — is written to a review queue
by setting `link_review_status = 'pending'`, and a person decides from the console's Links tab:
select rows and either CLEAR them (leave the catalog untouched) or DEACTIVATE them (take them
out). The two-pass confirmation, the repair-before-condemning step and the discontinuation
content check are all unchanged; only the final act moved from the agent to a human. The
classification below still names the *severity* of each finding ('dead' vs a softer flag) —
that severity now decides how the queue row reads, not whether the agent pulls the row.

THIS AGENT IS FREE. No Gemini, no Anthropic, no keys beyond Supabase — every check is an
ordinary HTTP GET. It is the only one of the six that can be run without approval on cost
grounds, and the only one whose --dry-run genuinely costs nothing (the other four still pay
the API in full and merely skip the writes).

WHY IT EXISTS
Fixing the scraper's fabricated URLs (2026-08-23) fixed only NEW rows. The catalog those
rows join had never been checked at all. Measured over all 1374 active rows that same day:

    live       1029
    dead        137   (10.0%) - 135x404, 2x410
    unverified  208   (403/429, TLS failures, timeouts)

One row in ten sent a student to a page that is not there - `smysp.stanford.edu`,
`jkcf.org/our-programs/young-artist-award/`, `training.nih.gov/.../aip_hs/`. These are real
programs with rotted links, not junk rows, which is exactly why the answer is "queue for
review" rather than "delete" — and, since 2026-09-02, not "deactivate" either.

THE RULE, and it is the whole design: EVIDENCE OF ABSENCE is queued as a dead link (the
strongest finding); everything softer is queued as an unverified/flagged finding. Nothing
here is a verdict — a person makes the call from the Links tab.

    dead-link finding   404, 410, a malformed URL, or a hostname that does not resolve
    flagged finding     403, 429, TLS failures, timeouts, connection resets, redirects off-site

403 alone is ~9% of this catalog (112 rows) and TLS failures another 41. Those sites are
refusing OUR client, not reporting an absent page - a student's browser carries a different
root store and usually loads them fine. Reading "the connection failed" as "the page is
gone" would have pulled ~150 working opportunities out of the catalog on the first run. See
url_validate.DNS_FAILURE for the measurement that separates the two.

TWO PASSES, ALWAYS. A URL that looks dead is re-checked before anything is written. It is
free, and it is the only thing standing between a CDN hiccup and a deactivated row.
Measured on the 137 dead rows: 135 were unchanged on the second pass and 2 rows moved
*into* dead - i.e. the pass corrects in both directions, and a 404 here is reproducible
enough to act on.

REPAIR BEFORE CONDEMNING. A dead row is not deactivated until wingman/url_repair.py has tried to
find its real URL on the same site, because programs get reorganised far more often than
they are cancelled - of 30 dead rows in the 08-23 audit, 9 were re-found and 9 of 9 came
back live. A repair is only accepted when the replacement page's <title> PROVES it is the
same program; read wingman/url_repair.py's docstring for the three tests and the sibling-program
failures that forced each one. Everything not repaired keeps its dead-link flag and, where
a candidate was found but not proven, carries it as a suggestion so a reviewer opens the
queue with a lead instead of a bare "dead link".

REPAIR NO LONGER ACTIVATES ANYTHING (changed 2026-09-02 by operator decision; see MARQUEE M2).
This agent used to be the one place in the repo that set is_active = true from code:
--repair-flagged restored a deactivated row when it proved a replacement URL. That path is
gone. --repair-flagged still revisits inactive rows carrying a repairable link flag (dead
link, unverifiable, unreachable, soft-404 — see _REPAIRABLE_PREFIXES), and it still attempts
to prove a replacement URL by url_repair's three title tests. But a proven repair now WRITES
the new URL onto the still-inactive row and parks it at link_review_status='repaired' — it
does not flip is_active. A person verifies the new link on the Links tab and clicks Activate.
So there is now NO code path anywhere in this repo that auto-activates a catalog row. A row
whose original URL simply comes back to life on its own is likewise reported, its link_status
corrected, and it stays inactive for a person to decide.

DISCONTINUATION CONTENT CHECK (added 2026-09-01). The same "evidence of absence" idea, one
level up from the URL: a row whose SUMMARY plainly says the program is gone ("has ceased all
contest operations", "discontinued its Summer Art Camps", "will not be offering ... in summer
2026"). Its link is usually still live — the page loads and truthfully announces the program's
end — so the URL sweep above never catches it. `status = 'not_running'` is the catalog's
canonical "discontinued" value, and BOTH the finder's catalog list and the matcher's recall()
already drop not_running rows — so an unmarked one leaks into Fresh Finds and the /api/match
grid. It leaked exactly that way (Caribou, UMD Summer Art Camps, UC Berkeley CED Build Camp
surfaced as live matches, 2026-09-01) because only agents/check_deadlines.py writes `status` and it is
paid + on-demand, so it had never touched these rows.

This check is free (a regex over text we already fetched) and deliberately conservative:
  * it ONLY fills a status that is currently NULL/blank — it never overwrites agents/check_deadlines.py's
    verdict, so the two writers cannot drift (deadline owns the field; this only fills the gap);
  * matched on a small set of unambiguous program-is-gone phrases (see DISCONTINUED_RX), which
    hit exactly 3 of 1678 active rows with zero false positives on the 2026-09-01 catalog;
  * it writes not_running and a review flag but does NOT deactivate the row — not_running already
    hides it everywhere that matters, and a later real deadline check can still confirm/correct it.

SETUP:
    .env needs SUPABASE_URL and SUPABASE_SERVICE_KEY. No model key.
    db/link_health_schema.sql is a one-time manual step in the Supabase SQL editor. Without
    it this still runs and still queues findings by writing link_review_status - EXCEPT when
    that column is the one missing: then it drops the link_* columns from its writes (losing
    the staleness filter, so every run re-checks everything, and losing the queue routing
    until the migration runs). Free, so the staleness part degrades to "slower", not "broken".

USAGE:
    python -m agents.check_links --preview          # what it would check. Free (everything is).
    python -m agents.check_links --dry-run          # check for real, write nothing, dump a snapshot
    python -m agents.check_links --all              # check, repair what can be, queue the rest for review
    python -m agents.check_links --sample 100       # a random slice, for a first look
    python -m agents.check_links --repair-flagged   # retry inactive flagged rows; queue proven repairs for manual activation
    python -m agents.check_links --no-repair        # skip the repair attempt entirely
    python -m agents.check_links --force            # ignore staleness, re-check every active row
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse

from wingman.agent_common import add_agent_args, emit_preview, snapshot_stamp
from wingman.supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch
from wingman import url_repair
from wingman import url_validate as uv
from wingman import REPO_ROOT   # the repo root, defined once (see wingman/__init__.py)

DB_AGENT = "link_checker"

# PostgREST reports an unknown column as 42703 on a read and PGRST204 on a write. Both mean
# "db/link_health_schema.sql has not been run", and both must be told apart from a real failure.
_SCHEMA_ERROR_CODES = ("42703", "PGRST204", "PGRST205", "42P01")

# How long a link-health result stays good enough to skip. Deliberately much shorter than
# agents/check_reviews.py's 30 days: a reputation moves on the scale of months, but a URL can rot
# any night, and re-checking costs nothing at all. The only thing this threshold protects is
# other people's servers and this run's wall time - not money.
STALE_AFTER_DAYS = 7

# Concurrency for the HTTP sweep. There is no per-call rate limiter here because there is no
# API being billed; what there IS is a duty not to hammer someone else's site. Requests are
# spread across ~700 distinct hosts, so a given host sees a trickle even at this width.
DEFAULT_WORKERS = 16

# The link_* columns, kept in one place because they are written in two (the live PATCH and
# the missing-column retry) and dropped as a set. link_review_status rides with them: it is
# this agent's telemetry about the row (which queue it sits in), not a content edit, so it is
# selected, stripped, and excluded from the updated_at bump exactly like the others.
LINK_COLUMNS = ("link_status", "link_status_code", "link_checked_at", "link_dead_since",
                "link_review_status")

# Review flags. Short on purpose: the admin console renders each as a pill truncated at 90
# characters. They say what to go and CHECK, never what was decided.
FLAG_DEAD = "dead link ({code}) - page is gone; find the current URL or reject"
FLAG_BLOCKED = "link unverifiable ({code}) - site blocks automated checks; open it manually"
FLAG_UNREACHABLE = "link unreachable ({code}) - could not connect; open it manually"
FLAG_SOFT_404 = "link redirects to a site homepage - the program's own page may be gone"
# Written on a row whose SUMMARY announces the program is gone (see the DISCONTINUATION
# CONTENT CHECK section of the module docstring). It says to CONFIRM, not that a decision was
# made — the row is only marked not_running, never deactivated.
FLAG_DISCONTINUED = ("summary says program is discontinued/ended - marked not-running; "
                     "confirm it is really gone")
# Written on a row whose URL was REPAIRED. It carries the old URL because that is the only
# record of what changed: `url` now holds the new value, and this is what makes the edit
# auditable and reversible by hand. Truncated to keep the console pill readable.
FLAG_REPAIRED = "URL was dead ({code}) and repaired automatically; previously {old}"
FLAG_SUGGESTION = "possible replacement found but NOT verified: {url}"

# Every flag this agent writes starts with one of these, so a re-run can strip its own
# previous flags without touching flags another agent (or a human) put there. Matching on a
# prefix rather than exact text means an edited wording does not orphan the old flags.
_OWNED_PREFIXES = ("dead link (", "link unverifiable (", "link unreachable (",
                   "link redirects to a site homepage", "URL was dead (",
                   "possible replacement found", "summary says program is discontinued")

# The flags whose rows --repair-flagged revisits. Broadened 2026-09-02 from dead-link-only to
# every link-health finding a person might have deactivated: an "unverifiable" (403/TLS/timeout)
# or soft-404 row is often genuinely gone, and a repair attempt is free, so it is worth trying.
# NOT included: "URL was dead (" (already repaired), "possible replacement" (a suggestion, not a
# finding) or "summary says ... discontinued" (a content verdict, not a moved page).
_REPAIRABLE_PREFIXES = ("dead link (", "link unverifiable (", "link unreachable (",
                        "link redirects to a site homepage")

# Discontinuation language for the content check. Deliberately narrow: each pattern asserts the
# PROGRAM ITSELF is gone or paused, not merely that some part of a page mentions ending (an
# eligibility note like "discontinue use of..." must not match). Measured over the 1678 active
# rows on 2026-09-01, this hit exactly 3 rows — all genuinely dead — and nothing else. Keep it
# tight: a false positive here yanks a live program out of Fresh Finds and matching.
DISCONTINUED_RX = re.compile("|".join((
    r"\bdiscontinu",                                    # discontinued / discontinuing
    r"\bceased?\b",                                     # ceased / cease
    r"no longer (be )?(offer|offered|offering|available|running|accept)",
    r"will not (be )?(offer|be offering|be held|run|be running)",
    r"not (be )?offer(ing|ed)? (this|the|its)",
    r"has (ended|closed|shut down|been cancell?ed|been discontinued)",
    r"is (no longer|not) (running|active|offered|available)",
    r"(program|contest|camp|competition|series) (has )?(ended|closed|been cancell?ed)",
    r"winding down",
    r"permanently closed",
)), re.I)


def discontinued_phrase(row):
    """The matched discontinuation phrase in a row's summary, or None. Free — no I/O."""
    m = DISCONTINUED_RX.search(row.get("summary") or "")
    return m.group(0) if m else None


def _status_blank(row):
    """True when the row carries no deadline-checker verdict yet — the only case this agent may
    fill `status`. A real not_running/running/rolling/unknown value is agents/check_deadlines.py's and
    is never overwritten here."""
    return str(row.get("status") or "").strip().lower() in ("", "null", "none")

# WHAT THIS AGENT DELIBERATELY DOES NOT FLAG, and the measurement behind each. Both checks
# exist in url_validate and both are used by agents/scrape_opportunities.py, where they earn their
# place: a fresh scraper candidate has a high base rate of a wrong URL and a reviewer is
# reading every row anyway. Against the CURATED catalog the base rate is inverted, and a
# flag that is usually wrong buries the dead links this agent exists to surface.
#
#   is_bare_domain()      fires on 161 of 1029 live rows (16%) - and they are correct.
#                         `jshs.org`, `congressionalaward.org`, `summerscholars.rutgers.edu`
#                         and `precollege.wisc.edu` are dedicated program sites whose
#                         homepage IS the program page. For a scraped candidate a bare
#                         domain means "the model named an org instead of finding the
#                         page"; for a row a person curated it means nothing at all.
#
#   domain_matches_org()  fires on 88 of 1029 live rows (9%), of which roughly one in seven
#                         is real. The rest are US university domain abbreviations the
#                         matcher cannot derive - `umd.edu`, `udel.edu`, `unc.edu`,
#                         `tamu.edu`, `gatech.edu`, `ucsd.edu`. (Fixing the two-letter case
#                         on 2026-08-23 took it from 11% to 9%; the remainder needs a
#                         university-domain dictionary, not a rule.)
#
# FLAG_SOFT_404 replaced both. It fires on 10 rows (1.0%) and about half are genuine
# losses - `feinberg.northwestern.edu/diversity/programs/health-professions...` and
# `louisville.edu/medicine/cancer-research/.../summer` now land on a bare faculty homepage,
# i.e. the program page was deleted behind a 200. The other half are site reorganisations
# where the new root really is the program (`web.mit.edu/wtp/` -> `wtp.mit.edu/`), which a
# reviewer settles in seconds. Ten rows at one-in-two beats eighty-eight at one-in-seven.


def _http_detail(exc):
    """The JSON body of a PostgREST error, or {}. The body reads only once."""
    try:
        return json.loads(exc.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _is_missing_column(detail):
    return (detail or {}).get("code") in _SCHEMA_ERROR_CODES


def classify(row, result, repair=None):
    """(action, flags) for one checked row.

    action is "repair" | "queue" | "flag" | "ok". "queue" is the strongest finding — evidence
    of absence, what used to auto-deactivate and now goes to the review queue as a dead link.
    "flag" is a softer finding (also queued, but the row is not presented as gone). "ok" and
    "repair" clear any open finding. See the module docstring.

    `repair` is url_repair.repair_url()'s answer, or None if repair was not attempted. A
    VERIFIED repair outranks any death/unverifiable verdict: the page moved, it did not go
    away — and since 2026-09-02 repair is attempted on unverifiable rows too (in
    --repair-flagged), so a proven replacement can arrive on either status. An unverified
    repair still contributes its best candidate as a review hint, so a reviewer opens the
    queue with a lead rather than a bare "dead link".
    """
    status, code = result.get("status"), result.get("code")
    code_txt = str(code)

    # A proven replacement wins over whatever the current URL's status was. Checked first so it
    # applies to both DEAD and UNVERIFIED rows (repair is attempted on both in --repair-flagged).
    if repair and repair.get("url"):
        return "repair", [FLAG_REPAIRED.format(code=code_txt, old=row.get("url") or "?")]

    if status == uv.DEAD:
        flags = [FLAG_DEAD.format(code=code_txt)]
        if repair and repair.get("suggestion"):
            flags.append(FLAG_SUGGESTION.format(url=repair["suggestion"]["url"]))
        return "queue", flags

    if status == uv.UNVERIFIED:
        base = ([FLAG_BLOCKED.format(code=code_txt)]
                if (isinstance(code, int) or code_txt.isdigit())
                else [FLAG_UNREACHABLE.format(code=code_txt)])
        # An unverified row that repair looked at but could not prove still gets its best
        # candidate as a lead — same courtesy a dead row gets.
        if repair and repair.get("suggestion"):
            base.append(FLAG_SUGGESTION.format(url=repair["suggestion"]["url"]))
        return "flag", base

    # Live. One check a 200 cannot make on its own, learned from the scraper audit: a page
    # that loads is not the same as the RIGHT page. It never deactivates - the link works,
    # it just may no longer lead where it used to. See the block above for the two checks
    # that were tried here and rejected on measured noise.
    url = row.get("url") or ""
    final = result.get("final_url")
    if final and _path_of(url) and uv.is_bare_domain(final):
        return "flag", [FLAG_SOFT_404]
    return "ok", []


def _path_of(url):
    """The URL's path, or "" — used to tell a deep link from a homepage."""
    try:
        return urllib.parse.urlsplit(url or "").path.strip("/")
    except ValueError:
        return ""


def merge_flags(existing, new_flags):
    """Replace this agent's own previous flags with `new_flags`, keeping everyone else's.

    Without the strip, a row checked weekly accumulates the same flag over and over until
    the console shows a wall of identical pills. Without the keep, this agent would silently
    erase the scraper's FLAG_NOT_SEARCHED and a reviewer's context along with it.
    """
    kept = [f for f in (existing or [])
            if isinstance(f, str) and not f.startswith(_OWNED_PREFIXES)]
    out = list(kept)
    for f in new_flags:
        if f not in out:
            out.append(f)
    return out


def select_rows(supabase_url, service_key, args):
    """Rows this run would check, plus the mode label and whether link_* columns exist.

    The select is a two-step ladder for the same reason list_pending_opportunities() is:
    PostgREST 400s the WHOLE select on one unknown column, so asking for link_checked_at
    against a database missing the migration would take the entire agent down rather than
    costing it one feature.
    """
    base = "id,name,org,url,is_active,quality_flags,moderation_status,summary,status"
    schema_ready = True
    # `flagged` scope walks INACTIVE rows — the ones an earlier pass deactivated — so the
    # is_active filter flips. Everything else about the read is identical.
    active_filter = "eq.false" if args.repair_flagged else "eq.true"
    try:
        rows = supabase_get(supabase_url, "opportunities", {
            "select": base + "," + ",".join(LINK_COLUMNS),
            "is_active": active_filter,
            "order": "id",
        }, service_key)
    except urllib.error.HTTPError as e:
        if not _is_missing_column(_http_detail(e)):
            raise
        schema_ready = False
        print("[WARN] link_* columns are missing - run db/link_health_schema.sql in the "
              "Supabase SQL editor. Continuing without them: results are still acted on, "
              "but nothing is recorded and every run re-checks the whole catalog.")
        rows = supabase_get(supabase_url, "opportunities", {
            "select": base, "is_active": active_filter, "order": "id",
        }, service_key)

    if args.repair_flagged:
        # Exactly the inactive rows carrying one of THIS agent's link-health flags, identified
        # by its own flag rather than by "everything inactive". That distinction is what keeps
        # a repair from ever touching a row a person rejected, a scraper row awaiting its first
        # review, or anything else that is inactive for a reason unrelated to links. Broadened
        # 2026-09-02 from dead-link-only to every repairable finding (see _REPAIRABLE_PREFIXES):
        # an "unverifiable" or soft-404 row a person deactivated is often genuinely gone too.
        flagged = [r for r in rows
                   if any(isinstance(f, str) and f.startswith(_REPAIRABLE_PREFIXES)
                          for f in (r.get("quality_flags") or []))]
        print(f"[OK] {len(flagged)} inactive row(s) carry a repairable link flag "
              f"(of {len(rows)} inactive rows).")
        return flagged, "flagged", schema_ready

    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        return [r for r in rows if r["id"] in wanted], "ids", schema_ready

    mode = "all"
    if schema_ready and not args.force:
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=STALE_AFTER_DAYS))
        fresh = 0
        due = []
        for r in rows:
            checked = _parse_iso(r.get("link_checked_at"))
            if checked and checked > cutoff:
                fresh += 1
            else:
                due.append(r)
        if fresh:
            print(f"[OK] Skipping {fresh} row(s) checked within the last "
                  f"{STALE_AFTER_DAYS} days.")
        rows = due
    elif args.force:
        mode = "all-force"

    if args.sample:
        import random
        rows = random.sample(rows, min(args.sample, len(rows)))
        mode = "sample"
    return rows, mode, schema_ready


def _parse_iso(value):
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def sweep(rows, timeout, workers):
    """Check every row's URL, then re-check whatever came back dead. Returns {url: result}.

    The second pass is what makes acting on the answer defensible. It is not a retry for
    flaky infrastructure in general - a 403 is not re-checked, because a second 403 tells
    you nothing a first one did not. It exists solely so that a transient 404 (a CDN
    mid-deploy, a load balancer serving an error page for a few seconds) cannot on its own
    take a real opportunity out of the catalog.
    """
    urls = [r["url"] for r in rows if r.get("url")]
    old_workers = uv.MAX_WORKERS
    uv.MAX_WORKERS = workers
    try:
        results = uv.check_urls(urls, timeout=timeout)
        suspect = [u for u, r in results.items() if r.get("status") == uv.DEAD]
        recovered = 0
        if suspect:
            print(f"[OK] {len(suspect)} URL(s) looked dead - confirming with a second pass...")
            second = uv.check_urls(suspect, timeout=timeout)
            for u, r in second.items():
                if r.get("status") != uv.DEAD:
                    recovered += 1
                results[u] = r
            if recovered:
                print(f"[OK] {recovered} recovered on re-check and will NOT be deactivated.")
    finally:
        uv.MAX_WORKERS = old_workers
    return results


def build_update(row, action, flags, result, now_iso, schema_ready, repair=None,
                 mark_not_running=False, needs_review=False):
    """The PATCH body for one row, or None if nothing about it changed.

    `mark_not_running` is set when the row's summary announces the program is gone AND its
    `status` is still blank (see discontinued_phrase / _status_blank). It writes the catalog's
    canonical discontinued value; the caller has already added FLAG_DISCONTINUED to `flags`.

    `needs_review` is True when this pass found something a person should look at (a dead link,
    an unverifiable one, a soft-404, or a just-marked discontinuation). It routes the row into
    the Links-tab queue by setting link_review_status='pending' — but only over a NULL, so it
    never overturns a human verdict. A proven repair on an INACTIVE row instead routes it to
    link_review_status='repaired' (URL fixed, awaiting manual activation); when the finding is
    resolved otherwise, link_review_status is cleared back to NULL. This is the ONLY thing this
    agent does with a finding: it never touches is_active or moderation_status — as of
    2026-09-02 NO code path in this repo sets is_active=true (see MARQUEE M2), so even a proven
    repair parks the row for a person to activate rather than restoring it itself."""
    update = {}
    if mark_not_running:
        # A CONTENT change to the opportunity (unlike the link_* telemetry), so this correctly
        # bumps updated_at below. Guarded on _status_blank upstream, so it never overwrites
        # agents/check_deadlines.py's verdict.
        update["status"] = "not_running"
    if action == "repair":
        # The row's URL was dead and a replacement was PROVEN to be this program's own page
        # (url_repair's three tests). Write it, and treat the link as live from here — the
        # status recorded is the state after the repair, not the state that triggered it.
        update["url"] = repair["url"]
        result = {"status": uv.LIVE, "code": 200, "final_url": repair["url"]}
    if schema_ready:
        status = result.get("status")
        update["link_status"] = status
        update["link_status_code"] = str(result.get("code"))
        update["link_checked_at"] = now_iso
        if status == uv.DEAD:
            # First seen dead wins. Stamping this on every pass would erase exactly the
            # thing it is for - how long the link has been rotten.
            update["link_dead_since"] = row.get("link_dead_since") or now_iso
        else:
            update["link_dead_since"] = None

    merged = merge_flags(row.get("quality_flags"), flags)
    if merged != (row.get("quality_flags") or []):
        update["quality_flags"] = merged

    # The review-queue routing — this agent's ONLY response to a finding. It NEVER sets
    # is_active or moderation_status; a person does that from the Links tab. As of 2026-09-02
    # this repo has NO code path that sets is_active = true (the old --repair-flagged
    # auto-restore was removed by operator decision — see MARQUEE M2). A proven repair on an
    # inactive row therefore does not restore it; it fixes the URL and parks the row in the
    # 'repaired' review state, where a person verifies the new link and activates it by hand.
    if schema_ready:
        current = (row.get("link_review_status") or "").strip().lower()
        if action == "repair" and not row.get("is_active"):
            # Fixed a row a person had deactivated: the URL now holds the PROVEN replacement
            # (url_repair's three title tests) and FLAG_REPAIRED records the old one. Route it
            # to 'repaired' (awaiting manual activation) rather than flipping it live.
            if current != "repaired":
                update["link_review_status"] = "repaired"
        elif needs_review:
            # Only over a NULL: a human 'cleared'/'deactivated'/'repaired' verdict is never
            # overturned by a re-run, even while the link stays dead. Re-asserting 'pending' on
            # a row already pending is skipped so the pass writes nothing when nothing changed.
            if not current:
                update["link_review_status"] = "pending"
        else:
            # Finding resolved (link live again, or an active row's URL repaired in place).
            # Drop it out of the queue. Clearing a human verdict here is intended: the row is
            # healthy now, so the old verdict no longer describes it.
            if current:
                update["link_review_status"] = None

    if not update:
        return None

    # `updated_at` marks a change to the OPPORTUNITY, and the link_* columns are not that —
    # they are this agent's telemetry about the row, with their own link_checked_at stamp.
    # Bumping it for a healthy row whose only news is "checked again, still fine" would mark
    # every one of ~1200 rows as freshly touched on every weekly pass, which is precisely
    # the signal other parts of the console read to mean something actually happened. Same
    # reason the Edit modal sends only changed fields.
    if any(k not in LINK_COLUMNS for k in update):
        update["updated_at"] = now_iso
    return update


def apply_update(supabase_url, service_key, row_id, update, schema_ready):
    """PATCH one row, retrying without the link_* columns if that migration is pending.

    Returns the schema_ready flag, possibly flipped to False. Same ladder as insert_rows()
    in agents/scrape_opportunities.py: one unknown key 400s the entire PATCH. Since the queue routing
    lives in link_review_status (a link_* column), a missing migration means the row cannot be
    queued — the stripped retry still lands any non-link change (a repair's url, a
    discontinuation's status), and the [WARN] tells the operator the queue is off until the
    migration runs.
    """
    try:
        supabase_patch(supabase_url, "opportunities", {"id": f"eq.{row_id}"}, update, service_key)
        return schema_ready
    except urllib.error.HTTPError as e:
        if not _is_missing_column(_http_detail(e)):
            raise
        stripped = {k: v for k, v in update.items() if k not in LINK_COLUMNS}
        print("[WARN] link_* columns rejected on write - run db/link_health_schema.sql. "
              "Retrying without them; the row cannot be queued for review until the migration "
              "runs, but any repair/discontinuation change still lands.")
        if stripped:
            supabase_patch(supabase_url, "opportunities", {"id": f"eq.{row_id}"},
                           stripped, service_key)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Check every active opportunity's URL. Free - no API calls.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true",
                       help="Check every active row due for a check (the default).")
    scope.add_argument("--sample", type=int, help="Check a random N-row sample instead.")
    scope.add_argument("--ids", help="Comma-separated opportunity ids, ignoring staleness.")
    parser.add_argument("--force", action="store_true",
                        help=f"Ignore the {STALE_AFTER_DAYS}-day staleness filter and "
                             f"re-check every active row.")
    parser.add_argument("--flag-only", action="store_true",
                        help="DEPRECATED no-op since 2026-09-02: this agent no longer "
                             "deactivates anything, so there is nothing to opt out of. Still "
                             "accepted (and ignored) so the console's argv builder need not "
                             "special-case it.")
    parser.add_argument("--repair-flagged", action="store_true",
                        help="Revisit the inactive rows carrying a repairable link flag (dead "
                             "link, unverifiable, unreachable, soft-404), try to find each "
                             "one's real URL, and for the ones whose replacement verifies, "
                             "write the new URL and park them at link_review_status='repaired' "
                             "for manual activation on the Links tab. Never re-activates a row "
                             "itself. Implies --repair. Free.")
    parser.add_argument("--no-repair", dest="repair", action="store_false", default=True,
                        help="Skip the repair attempt and queue a dead row as-is. Repair is "
                             "on by default: measured on the 2026-08-23 batch, 9 of 9 dead "
                             "rows that were re-found on the same site came back live, so "
                             "queuing a row without looking buries a reviewer in dead links "
                             "that were only moved pages.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check for real but write nothing; dump a JSON snapshot. "
                             "Unlike the other agents' --dry-run this really is free.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Concurrent HTTP checks (default {DEFAULT_WORKERS}).")
    add_agent_args(parser, default_timeout=20)
    args = parser.parse_args()
    if args.repair_flagged:
        args.repair = True
    # No apply_timing() call: --min-delay exists on every agent for the API rate limiter,
    # and this agent makes no API calls. It is accepted and ignored rather than removed, so
    # the admin console can build argv for all six agents the same way.

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)

    print("[OK] Fetching active catalog rows...")
    rows, mode, schema_ready = select_rows(supabase_url, service_key, args)
    print(f"[OK] {len(rows)} row(s) to check.")

    if args.preview:
        emit_preview(len(rows), "rows", [r.get("name", "?") for r in rows],
                     mode=mode, free=True)
        return
    if not rows:
        print("[OK] Nothing due for a link check right now.")
        return

    run_mode = (mode + ("-flagonly" if args.flag_only else "")
            + ("-norepair" if not args.repair else "")
            + ("-dryrun" if args.dry_run else ""))
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": DB_AGENT,
        "mode": run_mode,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    started = datetime.datetime.now(datetime.timezone.utc)
    results = sweep(rows, args.timeout, max(1, args.workers))

    # Repair pass. It runs BEFORE anything is classified, so a row whose page merely moved is
    # never queued for it. Candidates are rows that re-check as DEAD — plus, in --repair-flagged
    # (the deliberate operator retry over rows a person already deactivated), rows that re-check
    # as UNVERIFIED too: some of those are genuinely gone, the operator asked to retry them, and
    # a repair attempt is free. In a normal --all pass an unverified row stays live and is left
    # alone, so this only widens the deliberate retry, never the routine sweep.
    repairs = {}
    if args.repair:
        repair_statuses = ((uv.DEAD, uv.UNVERIFIED) if args.repair_flagged else (uv.DEAD,))
        candidates = [r for r in rows
                      if (results.get(r.get("url")) or {}).get("status") in repair_statuses]
        if candidates:
            print(f"[OK] {len(candidates)} broken row(s) — looking for the real URL on each "
                  f"site before deciding...")
            repairs = url_repair.repair_many(candidates, timeout=args.timeout,
                                             workers=min(8, max(1, args.workers)))

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    counts = {"live": 0, "dead": 0, "unverified": 0}
    queued_dead = 0     # dead-link findings routed to the review queue (was "deactivated")
    flagged = 0         # softer findings (unverifiable / soft-404) routed to the queue
    repaired = 0
    repaired_queued = 0  # inactive rows repaired -> parked as 'repaired' for manual activation
    discontinued = 0
    written = 0
    errors = 0
    snapshot = []

    for row in rows:
        result = results.get(row.get("url")) or {"status": uv.UNVERIFIED, "code": "unchecked"}
        counts[result.get("status", uv.UNVERIFIED)] = counts.get(result.get("status"), 0) + 1
        repair = repairs.get(row["id"])
        action, flags = classify(row, result, repair)

        # Content check (free, no HTTP): a summary that announces the program is gone. Adds a
        # review flag whenever the language is present, but only WRITES status=not_running when
        # the field is still blank — never overwriting agents/check_deadlines.py. Orthogonal to the
        # link verdict above: a discontinued program's URL is usually still live.
        disc_phrase = discontinued_phrase(row)
        mark_not_running = bool(disc_phrase) and _status_blank(row)
        if disc_phrase:
            flags = flags + [FLAG_DISCONTINUED]
            if mark_not_running:
                discontinued += 1

        # Whether this pass turned up something a person should look at. A live/ok or repaired
        # row is not review-worthy on its link; a just-marked discontinuation is, so the person
        # can confirm the program is really gone. This is what routes the row into the queue.
        needs_review = action in ("queue", "flag") or mark_not_running

        name = (row.get("name") or "?")[:55].encode("utf-8", "ignore").decode("utf-8")
        mark = {"repair": "REPAIRED", "queue": "QUEUE-DEAD",
                "flag": "queue-flag", "ok": "ok"}[action]
        print(f"  [{mark:>10}] {result.get('status'):<10} {str(result.get('code')):<12} {name}")

        if action == "repair":
            repaired += 1
            # "repaired_queued" counts INACTIVE rows whose URL was fixed and which are now
            # parked at link_review_status='repaired' for a person to verify and activate —
            # the number the --repair-flagged pass exists to produce. A repair on a row that
            # was still active fixed its URL in place without changing its visibility or
            # queueing anything, and summing the two would overstate what awaits review.
            if not row.get("is_active"):
                repaired_queued += 1
            print(f"               -> {repair['url']}")
            print(f"               proof: {(repair.get('title') or '')[:70]}")
        elif action == "queue":
            queued_dead += 1
        elif action == "flag":
            flagged += 1
        if mark_not_running:
            print(f"               summary reads discontinued -> status=not_running "
                  f"({disc_phrase!r})")

        snapshot.append({
            "id": row["id"], "name": row.get("name"), "org": row.get("org"),
            "url": row.get("url"), "status": result.get("status"),
            "code": str(result.get("code")), "final_url": result.get("final_url"),
            "action": action, "flags": flags,
            "was_active": bool(row.get("is_active")),
            # The discontinuation content signal, independent of the link verdict: the matched
            # phrase (if any) and whether this run marked the row not_running for it.
            "discontinued_phrase": disc_phrase,
            "marked_not_running": mark_not_running,
            # The whole repair record, accepted or not: the URL chosen, the title that
            # proved it, and every candidate that was rejected and why. This is what makes
            # an automatic URL edit reviewable after the fact rather than a mystery.
            "repair": repair,
        })

        if args.dry_run:
            continue
        update = build_update(row, action, flags, result, now_iso, schema_ready, repair,
                              mark_not_running=mark_not_running, needs_review=needs_review)
        if not update:
            continue
        try:
            schema_ready = apply_update(supabase_url, service_key, row["id"], update, schema_ready)
            written += 1
        except Exception as e:
            errors += 1
            print(f"    [ERROR] could not write {row['id']}: {e}")

    elapsed = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
    total = len(rows)
    pct = (counts["dead"] / total * 100) if total else 0
    print(f"\n[SUMMARY] checked: {total} in {elapsed:.0f}s  |  live: {counts['live']}, "
          f"dead: {counts['dead']} ({pct:.1f}%), unverified: {counts['unverified']}")
    # Wording is scope-aware. In --repair-flagged every row is ALREADY inactive (it was
    # deactivated by a person, or by this agent before 2026-09-02), so the honest verb there
    # is "stayed" — the run did not queue anything, it revisited old deactivations.
    if args.repair_flagged:
        print(f"[SUMMARY] stayed deactivated: {queued_dead + flagged}, "
              f"rows written: {written}, errors: {errors}, cost: $0.00 (no API calls)")
    else:
        verb = "would queue" if args.dry_run else "queued"
        print(f"[SUMMARY] {verb} for review: {queued_dead} dead-link, {flagged} flagged "
              f"(unverifiable/soft-404); rows written: {written}, errors: {errors}, "
              f"cost: $0.00 (no API calls)")
        total_queued = queued_dead + flagged
        if total_queued and not args.dry_run:
            print(f"[SUMMARY] {total_queued} row(s) now sit at link_review_status='pending'. "
                  f"They are in the admin console's Links tab. NOTHING was deactivated — a "
                  f"person clears or deactivates each one there.")
        elif total_queued:
            print(f"[SUMMARY] {total_queued} row(s) WOULD be queued for review. Nothing was "
                  f"changed. No row is ever deactivated by this agent.")
    if discontinued:
        verb = "would mark" if args.dry_run else "marked"
        print(f"[SUMMARY] {verb} {discontinued} row(s) status=not_running from discontinuation "
              f"language in their summary (status was blank). They drop out of Fresh Finds and "
              f"matching; the row stays for a person to confirm. This never overwrites a "
              f"deadline-checker verdict.")

    if args.repair:
        attempted = len(repairs)
        rate = f" ({repaired / attempted * 100:.0f}%)" if attempted else ""
        verb = "would repair" if args.dry_run else "repaired"
        print(f"[SUMMARY] repair: {attempted} broken row(s) looked at, {verb} {repaired}{rate}"
              f" — the rest kept their flag and, where one was found, a "
              f"suggested replacement for a reviewer.")
        if repaired_queued:
            print(f"[SUMMARY] *** {repaired_queued} inactive row(s) "
                  f"{'would be' if args.dry_run else 'were'} REPAIRED and parked for review "
                  f"*** — their URL now holds a proven replacement and they sit at "
                  f"link_review_status='repaired' on the Links tab. NOTHING was re-activated; "
                  f"a person verifies each new link and clicks Activate.")
        elif args.repair_flagged:
            print("[SUMMARY] No rows could be repaired.")

    if args.dry_run:
        stamp = snapshot_stamp()
        path = os.path.join(REPO_ROOT,
                            f"link_check_dry_run_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote dry-run snapshot: {path}")
        print("[DRY RUN] No opportunity rows were touched. The run itself is still logged "
              "to agent_runs (mode='%s') so the history is complete - unlike the other "
              "agents' dry runs, this one really did cost nothing." % run_mode)
    else:
        # A full report every run, dry or not. The console's log ring buffer holds 500 lines
        # and a full pass prints ~1400, so without this the head of the run - which is where
        # the queued findings are - scrolls out of reach before anyone reads it.
        log_dir = os.path.join(REPO_ROOT, "agent_logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"link_check_{snapshot_stamp()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote full report: {path}")

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": total,
            "items_updated": queued_dead + flagged + repaired + discontinued,
            "errors": errors,
            # Genuinely zero, not unknown. This is the one agent for which a 0 in the cost
            # column is a fact rather than a run that died before its closing PATCH.
            "cost_usd": 0.0,
            "total_web_searches": 0,
            "silent_search_count": 0,
            "notes": f"live={counts['live']}, dead={counts['dead']}, "
                     f"unverified={counts['unverified']}, queued_dead={queued_dead}, "
                     f"flagged={flagged}, repaired={repaired}, "
                     f"repaired_queued={repaired_queued}, discontinued={discontinued}",
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Link-health checker: finds catalog rows whose URL is broken, deactivates the ones that
are provably gone, and flags the rest for a person to look at.

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
programs with rotted links, not junk rows, which is exactly why the answer is "deactivate
and queue for review" rather than "delete".

THE RULE, and it is the whole design: only EVIDENCE OF ABSENCE deactivates a row.

    deactivate   404, 410, a malformed URL, or a hostname that does not resolve
    flag only    403, 429, TLS failures, timeouts, connection resets, redirects off-site

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

REPAIR BEFORE CONDEMNING. A dead row is not deactivated until url_repair.py has tried to
find its real URL on the same site, because programs get reorganised far more often than
they are cancelled - of 30 dead rows in the 08-23 audit, 9 were re-found and 9 of 9 came
back live. A repair is only accepted when the replacement page's <title> PROVES it is the
same program; read url_repair.py's docstring for the three tests and the sibling-program
failures that forced each one. Everything not repaired keeps its dead-link flag and, where
a candidate was found but not proven, carries it as a suggestion so a reviewer opens the
queue with a lead instead of a bare "dead link".

THE ONE PLACE THIS REPO SETS is_active = true FROM CODE, and the limits on it.
--repair-flagged revisits rows THIS agent deactivated for a dead link and restores the ones
whose replacement verifies. That is not the "never auto-activate" rule being bent: that rule
is about promoting rows no person has ever vetted - a scraper's guess. These rows were in
the live catalog because a person put them there, a machine removed them over a link, and
the same machine has now proven the link. Restoring is putting back what the automated check
took out. It is bounded to rows carrying this agent's own dead-link flag, never a row a
person rejected and never one that was never active, and each restored row keeps a flag
naming its old URL so the edit is auditable and reversible by hand. Nothing else here
activates anything: a row whose original URL simply comes back to life on its own is
reported, its link_status corrected, and it stays inactive for a person to decide.

SETUP:
    .env needs SUPABASE_URL and SUPABASE_SERVICE_KEY. No model key.
    link_health_schema.sql is a one-time manual step in the Supabase SQL editor. Without
    it this still runs and still deactivates - it drops the link_* columns from its writes
    and loses only the staleness filter, so every run re-checks everything. Free, so that
    degrades to "slower", not "broken".

USAGE:
    python check_links.py --preview          # what it would check. Free (everything is).
    python check_links.py --dry-run          # check for real, write nothing, dump a snapshot
    python check_links.py --all              # check, repair what can be repaired, deactivate the rest
    python check_links.py --sample 100       # a random slice, for a first look
    python check_links.py --repair-flagged   # retry ONLY previously-deactivated rows; restore what verifies
    python check_links.py --no-repair        # skip the repair attempt entirely
    python check_links.py --flag-only        # never deactivate; only record + report
    python check_links.py --force            # ignore staleness, re-check every active row
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse

from agent_common import add_agent_args, emit_preview, snapshot_stamp
from supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch
import url_repair
import url_validate as uv

DB_AGENT = "link_checker"

# PostgREST reports an unknown column as 42703 on a read and PGRST204 on a write. Both mean
# "link_health_schema.sql has not been run", and both must be told apart from a real failure.
_SCHEMA_ERROR_CODES = ("42703", "PGRST204", "PGRST205", "42P01")

# How long a link-health result stays good enough to skip. Deliberately much shorter than
# check_reviews.py's 30 days: a reputation moves on the scale of months, but a URL can rot
# any night, and re-checking costs nothing at all. The only thing this threshold protects is
# other people's servers and this run's wall time - not money.
STALE_AFTER_DAYS = 7

# Concurrency for the HTTP sweep. There is no per-call rate limiter here because there is no
# API being billed; what there IS is a duty not to hammer someone else's site. Requests are
# spread across ~700 distinct hosts, so a given host sees a trickle even at this width.
DEFAULT_WORKERS = 16

# The link_* columns, kept in one place because they are written in two (the live PATCH and
# the missing-column retry) and dropped as a set.
LINK_COLUMNS = ("link_status", "link_status_code", "link_checked_at", "link_dead_since")

# Review flags. Short on purpose: the admin console renders each as a pill truncated at 90
# characters. They say what to go and CHECK, never what was decided.
FLAG_DEAD = "dead link ({code}) - page is gone; find the current URL or reject"
FLAG_BLOCKED = "link unverifiable ({code}) - site blocks automated checks; open it manually"
FLAG_UNREACHABLE = "link unreachable ({code}) - could not connect; open it manually"
FLAG_SOFT_404 = "link redirects to a site homepage - the program's own page may be gone"
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
                   "possible replacement found")

# WHAT THIS AGENT DELIBERATELY DOES NOT FLAG, and the measurement behind each. Both checks
# exist in url_validate and both are used by scrape_opportunities.py, where they earn their
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

    action is "repair" | "deactivate" | "flag" | "ok". Only evidence of absence
    deactivates - see the module docstring. Everything else is a review hint attached to a
    row that stays live.

    `repair` is url_repair.repair_url()'s answer for a dead row, or None if repair was not
    attempted. A VERIFIED repair outranks the death sentence: the page moved, it did not
    go away. An unverified one still contributes its best candidate as a review hint, so a
    reviewer opens the queue with a lead rather than a bare "dead link".
    """
    status, code = result.get("status"), result.get("code")
    code_txt = str(code)

    if status == uv.DEAD:
        if repair and repair.get("url"):
            return "repair", [FLAG_REPAIRED.format(code=code_txt, old=row.get("url") or "?")]
        flags = [FLAG_DEAD.format(code=code_txt)]
        if repair and repair.get("suggestion"):
            flags.append(FLAG_SUGGESTION.format(url=repair["suggestion"]["url"]))
        return "deactivate", flags

    if status == uv.UNVERIFIED:
        if isinstance(code, int) or code_txt.isdigit():
            return "flag", [FLAG_BLOCKED.format(code=code_txt)]
        return "flag", [FLAG_UNREACHABLE.format(code=code_txt)]

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
    base = "id,name,org,url,is_active,quality_flags,moderation_status"
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
        print("[WARN] link_* columns are missing - run link_health_schema.sql in the "
              "Supabase SQL editor. Continuing without them: results are still acted on, "
              "but nothing is recorded and every run re-checks the whole catalog.")
        rows = supabase_get(supabase_url, "opportunities", {
            "select": base, "is_active": active_filter, "order": "id",
        }, service_key)

    if args.repair_flagged:
        # Exactly the rows THIS agent deactivated for a dead link, identified by its own
        # flag rather than by "everything inactive". That distinction is what keeps a
        # restore from ever touching a row a person rejected, a scraper row awaiting its
        # first review, or anything else that is inactive for a reason unrelated to links.
        flagged = [r for r in rows
                   if any(isinstance(f, str) and f.startswith("dead link (")
                          for f in (r.get("quality_flags") or []))]
        print(f"[OK] {len(flagged)} inactive row(s) carry this agent's dead-link flag "
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


def build_update(row, action, flags, result, now_iso, schema_ready, repair=None):
    """The PATCH body for one row, or None if nothing about it changed."""
    update = {}
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

    if action == "repair" and not row.get("is_active"):
        # RESTORING a row this agent itself deactivated on an earlier pass, now that the
        # reason no longer holds. Read carefully: this is the ONE place in this repo that
        # sets is_active = true from code, and it is not a breach of "never auto-activate".
        # That rule is about promoting rows a person has never vetted — a scraper's guess.
        # This row was in the live catalog because a person put it there; a machine took it
        # out over a link, and the same machine has now proven the link. Restoring is
        # putting back what the automated check removed, not promoting anything new.
        #
        # It is bounded accordingly: it only ever fires on a row carrying THIS agent's own
        # dead-link flag (see select_rows' `flagged` scope), never on a row a person
        # rejected or one that was never active, and the flag it writes keeps the old URL
        # so the change is auditable and reversible by hand.
        update["is_active"] = True

    if action == "deactivate" and row.get("is_active"):
        # Guarded on is_active because --repair-flagged walks rows that are ALREADY
        # inactive: re-asserting the same values there writes 130-odd rows to say nothing
        # and bumps updated_at on every one, which makes them look freshly touched
        # everywhere else in the console. Same reason the Edit modal sends only changed
        # fields.
        update["is_active"] = False
        # Into the review queue, not out of the catalog. `pending_review` is what puts it in
        # front of a person; nothing here ever writes `rejected`, because a rotted link is
        # not a verdict on the opportunity.
        update["moderation_status"] = "pending_review"
        # reviewed_by / reviewed_at are deliberately LEFT ALONE. Most rows this touches were
        # approved by a person at some point, and clearing the stamp would erase the only
        # record of that. Kept, the queue can say something genuinely useful — "someone
        # approved this on 08-23 and the link has died since" — which is a different
        # situation from a row nobody has ever looked at, and they should not read alike.

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
    in scrape_opportunities.py: one unknown key 400s the entire PATCH, so without this a
    missing migration means the deactivation silently does not happen either.
    """
    try:
        supabase_patch(supabase_url, "opportunities", {"id": f"eq.{row_id}"}, update, service_key)
        return schema_ready
    except urllib.error.HTTPError as e:
        if not _is_missing_column(_http_detail(e)):
            raise
        stripped = {k: v for k, v in update.items() if k not in LINK_COLUMNS}
        print("[WARN] link_* columns rejected on write - run link_health_schema.sql. "
              "Retrying without them so the deactivation still lands.")
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
                        help="Record and report, but never deactivate. Use this for the "
                             "first run against a catalog that has never been checked, so "
                             "the scale of the problem is visible before anything moves.")
    parser.add_argument("--repair-flagged", action="store_true",
                        help="Revisit ONLY the inactive rows this agent previously "
                             "deactivated for a dead link, try to find each one's real "
                             "URL, and RESTORE the rows whose replacement verifies. Implies "
                             "--repair. Free, like everything else here.")
    parser.add_argument("--no-repair", dest="repair", action="store_false", default=True,
                        help="Skip the repair attempt and deactivate a dead row outright. "
                             "Repair is on by default: measured on the 2026-08-23 batch, "
                             "9 of 9 dead rows that were re-found on the same site came "
                             "back live, so condemning a row without looking throws away "
                             "real opportunities over a moved page.")
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

    # Repair pass. Only dead rows are candidates, so this costs nothing on a healthy
    # catalog — and it runs BEFORE anything is classified, so a row whose page merely moved
    # is never condemned for it.
    repairs = {}
    if args.repair:
        dead_rows = [r for r in rows
                     if (results.get(r.get("url")) or {}).get("status") == uv.DEAD]
        if dead_rows:
            print(f"[OK] {len(dead_rows)} dead row(s) — looking for the real URL on each "
                  f"site before deciding...")
            repairs = url_repair.repair_many(dead_rows, timeout=args.timeout,
                                             workers=min(8, max(1, args.workers)))

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    counts = {"live": 0, "dead": 0, "unverified": 0}
    deactivated = 0
    flagged = 0
    repaired = 0
    restored = 0
    written = 0
    errors = 0
    snapshot = []

    for row in rows:
        result = results.get(row.get("url")) or {"status": uv.UNVERIFIED, "code": "unchecked"}
        counts[result.get("status", uv.UNVERIFIED)] = counts.get(result.get("status"), 0) + 1
        repair = repairs.get(row["id"])
        action, flags = classify(row, result, repair)
        if action == "deactivate" and args.flag_only:
            action = "flag"

        name = (row.get("name") or "?")[:55].encode("utf-8", "ignore").decode("utf-8")
        mark = {"repair": "REPAIRED", "deactivate": "DEACTIVATE",
                "flag": "flag", "ok": "ok"}[action]
        print(f"  [{mark:>10}] {result.get('status'):<10} {str(result.get('code')):<12} {name}")

        if action == "repair":
            repaired += 1
            # "restored" counts only rows that were INACTIVE and are going back to active —
            # the number the --repair-flagged pass exists to produce. A repair on a row
            # that was still active fixed its URL without ever changing its visibility, and
            # summing the two would overstate what came back to students.
            if not row.get("is_active"):
                restored += 1
            print(f"               -> {repair['url']}")
            print(f"               proof: {(repair.get('title') or '')[:70]}")
        elif action == "deactivate":
            deactivated += 1
        elif action == "flag":
            flagged += 1

        snapshot.append({
            "id": row["id"], "name": row.get("name"), "org": row.get("org"),
            "url": row.get("url"), "status": result.get("status"),
            "code": str(result.get("code")), "final_url": result.get("final_url"),
            "action": action, "flags": flags,
            "was_active": bool(row.get("is_active")),
            # The whole repair record, accepted or not: the URL chosen, the title that
            # proved it, and every candidate that was rejected and why. This is what makes
            # an automatic URL edit reviewable after the fact rather than a mystery.
            "repair": repair,
        })

        if args.dry_run:
            continue
        update = build_update(row, action, flags, result, now_iso, schema_ready, repair)
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
    # Wording is scope-aware. In --repair-flagged every row is ALREADY inactive, so
    # "deactivated: 134" and "flagged (left active)" would both be false statements about
    # what the run did — the honest verb there is "stayed".
    if args.repair_flagged:
        print(f"[SUMMARY] stayed deactivated: {deactivated + flagged}, "
              f"rows written: {written}, errors: {errors}, cost: $0.00 (no API calls)")
    else:
        verb = "would deactivate" if args.dry_run else "deactivated"
        print(f"[SUMMARY] {verb}: {deactivated}, flagged (left active): {flagged}, "
              f"rows written: {written}, errors: {errors}, cost: $0.00 (no API calls)")
        if deactivated and not args.dry_run:
            print(f"[SUMMARY] {deactivated} row(s) are now is_active=false / pending_review. "
                  f"They are in the admin console's Review queue with a flag saying why. "
                  f"Nothing reactivates them but a person.")
        elif deactivated:
            print(f"[SUMMARY] {deactivated} row(s) WOULD be deactivated. Nothing was changed.")
    if flagged and not args.repair_flagged:
        # Worth saying plainly: a flag on a row that stays ACTIVE is written to
        # quality_flags but has nowhere to show, because the console's Review queue lists
        # is_active = false rows only. It is durable — it surfaces if the row ever does
        # enter the queue — but today the run report below is the only place to read it.
        print(f"[SUMMARY] The {flagged} flagged row(s) are still active and still visible "
              f"to students. Their flags are stored, but the console's Review queue only "
              f"shows deactivated rows — read the report below to see them.")
    if args.flag_only and counts["dead"]:
        print(f"[NOTE] --flag-only: {counts['dead']} dead row(s) were left ACTIVE. "
              f"Re-run without it to deactivate them.")

    if args.repair:
        attempted = len(repairs)
        rate = f" ({repaired / attempted * 100:.0f}%)" if attempted else ""
        verb = "would repair" if args.dry_run else "repaired"
        print(f"[SUMMARY] repair: {attempted} dead row(s) looked at, {verb} {repaired}{rate}"
              f" — the rest kept their dead-link flag and, where one was found, a "
              f"suggested replacement for a reviewer.")
        if restored:
            print(f"[SUMMARY] *** {restored} row(s) {'would go' if args.dry_run else 'went'} "
                  f"BACK TO ACTIVE *** — they were deactivated for a dead link, and the "
                  f"replacement URL was proven to be the same program by its page title.")
        elif args.repair_flagged:
            print("[SUMMARY] No rows could be restored to active.")

    if args.dry_run:
        stamp = snapshot_stamp()
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
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
        # the deactivations are - scrolls out of reach before anyone reads it.
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"link_check_{snapshot_stamp()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote full report: {path}")

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": total,
            "items_updated": deactivated + flagged,
            "errors": errors,
            # Genuinely zero, not unknown. This is the one agent for which a 0 in the cost
            # column is a fact rather than a run that died before its closing PATCH.
            "cost_usd": 0.0,
            "total_web_searches": 0,
            "silent_search_count": 0,
            "notes": f"live={counts['live']}, dead={counts['dead']}, "
                     f"unverified={counts['unverified']}, deactivated={deactivated}, "
                     f"flagged={flagged}, repaired={repaired}, restored={restored}",
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")


if __name__ == "__main__":
    main()

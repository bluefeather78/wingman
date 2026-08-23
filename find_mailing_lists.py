#!/usr/bin/env python3
"""Mailing-list discovery: for each active opportunity, work out whether the program has
a mailing list we can subscribe a student to unattended, and store a *recipe* for it.

This is the fifth background agent (console key `mailinglist`,
agent_runs.agent = "mailing_list_finder"). Like the other four it never activates its own
output: every recipe it writes lands `status = 'pending_review'` and only a person in the
admin console can promote one to `verified`. Nothing but a verified recipe is ever
executed against a real student's email address.

WHY THIS AGENT IS MOSTLY NOT AI, AND WHY IT IS SO CHEAP

Accuracy is the metric here, so the expensive, hallucination-prone step is deleted rather
than prompted around. Per opportunity:

  1. Fetch the row's URL with urllib (plus a few conventional signup subpages).      free
  2. Regex out any supported provider's embed form.                                  free
  3. If none: write `method = 'none'` and stop.  NO MODEL CALL AT ALL.               free
  4. If some: ONE Haiku call, no web search, to answer the single question a regex
     cannot - is this form THIS program's list, or the host institution's?           ~$0.002

Step 4 is the whole reason a model is involved. CLAUDE.md records that 969 of 1330
catalog rows (73%) sit on a domain shared with a different opportunity, nyu.edu alone
hosts 36, and one SMApply portal backs six distinct Stanford/Korea programs. On those
pages "there is a newsletter form here" is nearly worthless; "this form is captioned
'Stanford e-Japan mailing list'" is the finding. The model must quote its evidence, and
that quote (`scope_evidence`) is what the human reviewer actually reads.

Because most rows never reach step 4, a full catalog pass costs far less than the other
agents - order $1 rather than the deadline checker's ~$0.70 with search, and much of the
catalog resolves for free.

SETUP:
    .env needs SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY.
    Run mailing_list_schema.sql once in the Supabase SQL editor first.

USAGE:
    python find_mailing_lists.py --preview --limit 10   # free: what would it touch
    python find_mailing_lists.py --limit 10             # the pilot pass
    python find_mailing_lists.py --all                  # every unchecked active row
    python find_mailing_lists.py --ids ec17081,ab12cd3  # named rows, e.g. a re-check
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error

from agent_common import add_agent_args, apply_timing, emit_preview, snapshot_stamp
from claude_common import call_claude, estimate_cost, extract_json
from mailing_list_common import candidate_urls, fetch_page, find_candidates
from supabase_common import load_dotenv, supabase_get, supabase_insert_one, supabase_patch, supabase_post

DB_AGENT = "mailing_list_finder"

# PostgREST reports an unknown column as 42703 on a read and PGRST204 on a write; an
# unknown table is PGRST205. All three mean "the schema file has not been run (or not
# re-run)", and all three must be told apart from a genuine failure.
_SCHEMA_ERROR_CODES = ("42703", "PGRST204", "PGRST205", "42P01")


def _http_detail(exc):
    """The JSON body of a PostgREST error, or {}. The body reads only once."""
    try:
        return json.loads(exc.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _is_missing_column(detail):
    return (detail or {}).get("code") in _SCHEMA_ERROR_CODES

# How many pages we are willing to fetch per opportunity before giving up. The landing
# page plus a handful of /newsletter-style guesses; each one is a real request against
# someone else's server, so this stays small.
MAX_PAGES_PER_OPP = 4

# How much of a candidate form's surrounding text the model sees. Enough to carry a
# heading and a caption, short enough that a page full of forms cannot blow the budget.
FORM_TEXT_CHARS = 600


def build_system():
    return """You judge whether a mailing-list signup form belongs to a SPECIFIC \
extracurricular program, or merely to the organization/institution hosting that program's page.

You are given one opportunity (a summer program, internship, competition, conference or \
journal for high school students) and one or more signup forms found on its page. The forms \
have already been located and parsed - do not look for more, and do not invent endpoints, \
URLs or field names. Your ONLY job is attribution.

This matters because most program pages live on a shared institutional site. A university \
domain may host dozens of unrelated programs, and a single application portal can serve six \
different programs. A generic "subscribe to our newsletter" form in a university footer is \
NOT the mailing list for the specific program on that page - subscribing a student to it \
sends them the wrong mail and they will never hear about the program they asked about.

Decide, for each form, whether a reasonable person would say "this form signs me up for news \
about THIS program".

Signals it IS the program's list: the form's own text names the program or its abbreviation; \
it sits inside a section about this program ("Stay informed about E-Japan", "Join the PRIMES \
mailing list", "Get notified when applications open"); the page is dedicated to this one program \
and the form is its only signup.

Signals it is NOT: the text is generic ("Sign up for our newsletter", "Subscribe for updates") \
on a site that clearly hosts many programs; the form is in a site-wide footer or header; the \
caption names the institution, department or a different program; it is an alumni, donor, \
events, or general-marketing list.

When the evidence is thin, say so with a LOW confidence rather than guessing high. A \
wrongly-flagged form costs a reviewer ten seconds; a wrongly-approved one silently sends a \
student the wrong mail forever, and they blame the program, not us.

`scope_evidence` must be a short QUOTE or close paraphrase of the actual text that decided it \
- never a restatement of your conclusion. If you cannot point at any text, that itself is \
evidence the form is generic, and confidence must be low.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON:
{"choice": <0-based index of the best form, or null if none of them is this program's list>, \
"is_program_list": true or false, "confidence": 0.0 to 1.0, "scope_evidence": "short quote from \
the form or page text that decided this, under 30 words", "reason": "one sentence, under 25 words"}"""


def build_user_content(opp, candidates):
    lines = [
        f"Opportunity: {opp['name']}",
        f"Organization: {opp.get('org') or 'unknown'}",
        f"Page: {opp['url']}",
        f"About: {(opp.get('summary') or '')[:400]}",
        "",
        f"{len(candidates)} signup form(s) found:",
    ]
    for i, cand in enumerate(candidates):
        lines += [
            f"[{i}] provider={cand['method']} found_on={cand.get('source_url')}",
            f"    form text: {(cand.get('form_text') or '')[:FORM_TEXT_CHARS]}",
        ]
    lines += ["", "Which of these, if any, is the mailing list for THIS program? Quote the "
                  "text that decided it."]
    return "\n".join(lines)


def scan_pages(opp):
    """Fetch this opportunity's page (and a few likely subpages) and return every
    supported-provider form found, plus which URLs were actually reachable.

    Stops at the first page that yields a candidate: the landing page's own form is
    always the better attributed one, and a /newsletter page found afterwards is far
    more likely to be the institution's general list.
    """
    candidates, fetched = [], []
    for url in candidate_urls(opp["url"])[:MAX_PAGES_PER_OPP]:
        final_url, html = fetch_page(url)
        if not html:
            continue
        fetched.append(final_url)
        found = find_candidates(final_url, html)
        if found:
            candidates = found
            break
    return candidates, fetched


def check_one(opp, api_key):
    """Resolve one opportunity into a recipe row. Returns (row, cost, used_model).

    `used_model` is what separates a free row (no form on the page, no call made) from a
    paid one, so the run summary can report the split honestly.
    """
    candidates, fetched = scan_pages(opp)
    base = {
        "opportunity_id": opp["id"],
        "discovered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pages_checked": fetched,
    }
    if not candidates:
        # No supported provider embed anywhere we looked. That is a real answer, not a
        # failure: it stops the next pass from re-fetching these pages, and it is the
        # honest input to the recall number.
        return dict(base, method="none", status="none", confidence=0.0,
                    scope_evidence="", reason="No supported signup provider found on the page."), 0.0, False

    text, usage = call_claude(build_system(), build_user_content(opp, candidates), api_key,
                              use_web_search=False, max_tokens=600)
    cost = estimate_cost(usage)
    verdict = extract_json(text) or {}

    choice = verdict.get("choice")
    is_list = bool(verdict.get("is_program_list"))
    if not isinstance(choice, int) or not (0 <= choice < len(candidates)) or not is_list:
        # The model looked and said none of these is this program's list. Recorded as
        # `none` with its reasoning, so a reviewer can see it was considered and why.
        return dict(base, method="none", status="none",
                    confidence=float(verdict.get("confidence") or 0.0),
                    scope_evidence=(verdict.get("scope_evidence") or "")[:500],
                    reason=(verdict.get("reason") or "Forms found, but none specific to this program.")[:300],
                    ), cost, True

    picked = candidates[choice]
    return dict(base,
                method=picked["method"],
                endpoint=picked["endpoint"],
                params=picked.get("params") or {},
                field_map=picked.get("field_map") or {},
                # Every provider we support double opt-ins. Stored per-recipe anyway so a
                # future single-opt-in provider does not silently inherit the wrong copy.
                double_optin=True,
                source_url=picked.get("source_url"),
                form_text=(picked.get("form_text") or "")[:1000],
                # pending_review, always. Discovery cannot verify its own work; see the
                # module docstring.
                status="pending_review",
                confidence=float(verdict.get("confidence") or 0.0),
                scope_evidence=(verdict.get("scope_evidence") or "")[:500],
                reason=(verdict.get("reason") or "")[:300],
                ), cost, True


def select_rows(supabase_url, service_key, args):
    """Which opportunities this run would process.

    Default is "active rows with no recipe yet" - so a plain re-run is idempotent and does
    not re-fetch pages already resolved. --force re-checks rows that already have one.
    """
    active = supabase_get(supabase_url, "opportunities", {
        "select": "id,name,org,url,summary",
        "is_active": "eq.true",
    }, service_key)

    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        return [o for o in active if o["id"] in wanted], "ids"

    if not args.force:
        try:
            done = supabase_get(supabase_url, "opportunity_signups",
                                {"select": "opportunity_id"}, service_key)
            seen = {r["opportunity_id"] for r in done}
        except Exception as e:
            print(f"[ERROR] Could not read opportunity_signups: {e}")
            print("[ERROR] Has mailing_list_schema.sql been run in the Supabase SQL editor?")
            sys.exit(1)
        active = [o for o in active if o["id"] not in seen]

    mode = "force" if args.force else "new"
    if args.limit:
        # Deterministic, not random: a pilot you can re-run and compare against needs the
        # same rows twice. Catalog order is stable.
        active = active[:args.limit]
        mode += f"-limit{args.limit}"
    return active, mode


def main():
    parser = argparse.ArgumentParser()
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true",
                       help="Check every active row that has no recipe yet (the default).")
    scope.add_argument("--ids", help="Comma-separated opportunity ids to check, ignoring "
                                     "whether they already have a recipe.")
    parser.add_argument("--limit", type=int,
                        help="Stop after N rows. This is how the pilot pass is run.")
    parser.add_argument("--force", action="store_true",
                        help="Re-check rows that already have a recipe. Overwrites their "
                             "recipe and resets it to pending_review - a verified recipe "
                             "goes back into the review queue.")
    parser.add_argument("--dry-run", action="store_true",
                        help="No writes (opportunity_signups or agent_runs) - still fetches "
                             "pages and still calls the API at full cost, but dumps results "
                             "to a local JSON snapshot instead.")
    add_agent_args(parser, default_timeout=120, default_min_delay=5)
    args = parser.parse_args()
    apply_timing(args, claude=True)

    load_dotenv()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not supabase_url or not service_key:
        print("[ERROR] SUPABASE_URL / SUPABASE_SERVICE_KEY not set in .env.")
        sys.exit(1)
    if not anthropic_key:
        print("[ERROR] ANTHROPIC_API_KEY not set in .env. This agent needs it to attribute "
              "a form to a program; there is no mock mode for a paid catalog write.")
        sys.exit(1)

    print("[OK] Fetching active catalog from Supabase...")
    items, mode = select_rows(supabase_url, service_key, args)
    print(f"[OK] {len(items)} row(s) to check ({mode}).")

    if args.preview:
        emit_preview(len(items), "rows", [o.get("name", "?") for o in items], mode=mode)
        return

    if not items:
        print("[OK] Nothing to do.")
        return

    # A dry run is logged too, with a -dryrun mode suffix: it skips DATABASE writes, not
    # API calls, so it costs the same as a live run and its spend must not be invisible.
    run_mode = mode + ("-dryrun" if args.dry_run else "")
    run_row = supabase_insert_one(supabase_url, "agent_runs", {
        "agent": DB_AGENT,
        "mode": run_mode,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, service_key)
    run_id = run_row["id"] if run_row else None

    total_cost, found, none_rows, errors, model_calls = 0.0, 0, 0, 0, 0
    snapshot = []

    for i, opp in enumerate(items):
        print(f"[{i + 1}/{len(items)}] {opp['name'][:55]}...", end=" ")
        try:
            row, cost, used_model = check_one(opp, anthropic_key)
            total_cost += cost
            model_calls += 1 if used_model else 0
            if row.get("method") == "none":
                none_rows += 1
                print(f"no list ({'model' if used_model else 'no form found'}), ${cost:.4f}")
            else:
                found += 1
                print(f"{row['method']} conf={row.get('confidence'):.2f}, ${cost:.4f}")
            snapshot.append(row)
            if not args.dry_run:
                # Upsert on opportunity_id: --force re-checking a row must replace its
                # recipe, not raise a primary-key conflict.
                supabase_post(supabase_url, "opportunity_signups", [row], service_key,
                              on_conflict="opportunity_id")
        except urllib.error.HTTPError as e:
            errors += 1
            detail = _http_detail(e)
            # PostgREST rejects an ENTIRE insert on one unrecognized key, so a table
            # created from an older version of the schema fails on every single row while
            # the pass appears to run normally. Stop and name the file rather than
            # printing 1300 identical 400s and finishing with an empty queue.
            if _is_missing_column(detail):
                print(f"\n[ERROR] {detail.get('message')}")
                print("[ERROR] The opportunity_signups table is out of date. Re-run "
                      "mailing_list_schema.sql in the Supabase SQL editor — it is "
                      "idempotent and its ALTER block adds whatever is missing.")
                print(f"[ERROR] Stopped after {i} row(s); API spend so far ${total_cost:.4f}.")
                sys.exit(1)
            print(f"[ERROR] HTTP {e.code} {detail.get('message') or ''}")
        except Exception as e:
            errors += 1
            print(f"[ERROR] {e}")

    print(f"\n[SUMMARY] checked: {len(items)}, recipes found: {found}, no list: {none_rows}, "
          f"errors: {errors}, model calls: {model_calls}/{len(items)}, cost: ${total_cost:.4f}")
    if found:
        print(f"[NEXT] {found} recipe(s) are pending review. Nothing will be executed for a "
              f"real user until they are verified at /admin -> Mailing lists.")

    if args.dry_run:
        stamp = snapshot_stamp()
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f"mailing_list_dry_run_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote dry-run snapshot: {path}")
        print("[DRY RUN] No writes performed.")

    if run_id is not None:
        supabase_patch(supabase_url, "agent_runs", {"id": f"eq.{run_id}"}, {
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items_processed": len(items),
            # items_updated, not items_added: these are recipes attached to existing
            # catalog rows, and the scraper is still the only agent that INSERTs
            # opportunities. Summing this with its seed counts remains wrong.
            "items_updated": found + none_rows,
            "errors": errors,
            "cost_usd": round(total_cost, 4),
        }, service_key)
        print(f"[OK] Logged agent_runs id={run_id}.")


if __name__ == "__main__":
    main()

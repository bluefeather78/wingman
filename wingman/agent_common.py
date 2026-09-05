#!/usr/bin/env python3
"""Shared CLI plumbing for wingman's four offline agent scripts —
agents/refresh_opportunities.py, agents/check_reviews.py, agents/scrape_opportunities.py, agents/check_deadlines.py.

Three things they all need, kept here so the flags behave identically across agents and
server.py's admin console can rely on one contract:

1. --preview   Resolve which rows/seeds a run WOULD process, print one machine-readable
               line, and exit before the first paid API call. Zero cost, zero writes,
               zero agent_runs rows. This is what the admin console's "Preview" button
               calls to show scope and a cost estimate before you commit to a real run.

2. --min-delay Minimum seconds between API calls. 5s is the value that resolved this
               pipeline's repeated HTTP 429s; the flag exists so a small sample run can
               be sped up deliberately, not so the floor can be forgotten. Warns below 5.

3. --timeout   Per-request HTTP read timeout. A client-side timeout does NOT stop or
               refund server-side work already in flight, so a too-short timeout means
               paying for answers you never receive — the exact failure that cost ~$30
               on the first national scrape.

Plus snapshot_stamp(), the naming of the JSON snapshot every agent dumps. It lives here
for the same reason the flags do: all four write one, dryrun_common reads all four, and
the format is a contract between them.

The preview contract: exactly one line on stdout, prefixed PREVIEW_JSON:, e.g.

    PREVIEW_JSON: {"count": 412, "unit": "rows", "sample": ["MIT PRIMES", "..."]}

server.py's preview_agent() parses that line and pairs `count` with a per-item cost
derived from the agent's own real agent_runs history. Row-selection logic stays in the
scripts (so it cannot drift from what a real run would do); cost math stays in the
server (which is where the history already lives).
"""
import datetime
import json
import re
import sys

PREVIEW_PREFIX = "PREVIEW_JSON:"
SAMPLE_LIMIT = 12  # how many example names to include; enough to eyeball, not to flood

# Loose shape check only — not the RFC. Shared by the scraper and metadata refresher so a
# model hallucinating "contact us via our website" or similar prose doesn't get written to
# opportunities.contact_email as if it were a real address.
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def safe_console():
    """Stop a non-ASCII character in MODEL OUTPUT from killing an agent mid-run. Idempotent.

    Windows consoles default to cp1252, which cannot encode most of what a web page contains.
    Measured live 2026-08-27: `agents/harvest_names.py` crashed on a program name carrying a
    non-breaking hyphen (U+2011) while printing its own drop-list — AFTER the paid naming call
    had returned, so the run lost work it had already been billed for. Anything that prints
    model output or page text should call this first; it degrades an unprintable character to a
    replacement glyph instead of raising.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass       # already wrapped, or not a real stream (pytest capture) — nothing to do


def clean_email(value):
    """A model-returned contact_email candidate, or None if it doesn't look like one."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if EMAIL_RE.match(value) else None


def add_agent_args(parser, default_timeout=120, default_min_delay=5):
    """Add --preview / --min-delay / --timeout to an agent's parser.

    Callers pass their own default_timeout because it is genuinely agent-specific:
    the scraper's broad national seeds need 280s where a single-row metadata lookup
    is fine at 120s.
    """
    parser.add_argument(
        "--preview", action="store_true",
        help="Resolve scope and print a PREVIEW_JSON summary, then exit. Makes NO API "
             "calls and NO writes — free. Use this to check what a run would touch.")
    parser.add_argument(
        "--min-delay", type=float, default=default_min_delay,
        help=f"Minimum seconds between API calls (default {default_min_delay}). Values "
             f"below 5 risk the HTTP 429 rate limiting this delay was added to fix.")
    parser.add_argument(
        "--timeout", type=int, default=default_timeout,
        help=f"Per-request HTTP read timeout in seconds (default {default_timeout}). A "
             f"client-side timeout does not stop or refund server-side work in flight, "
             f"so setting this too low risks paying for results you never see.")
    return parser


def apply_timing(args, gemini=False, claude=False):
    """Push --min-delay / --timeout into whichever API module this agent actually uses.

    Import the modules lazily so a script that only talks to one provider doesn't pay
    for importing the other (and doesn't fail if the unused one is mid-refactor).
    """
    applied = {}
    if gemini:
        from wingman import gemini_common
        applied["gemini_min_delay"] = gemini_common.set_min_delay(getattr(args, "min_delay", None))
        applied["gemini_timeout"] = gemini_common.set_default_timeout(getattr(args, "timeout", None))
    if claude:
        from wingman import claude_common
        applied["claude_min_delay"] = claude_common.set_min_delay(getattr(args, "min_delay", None))
        applied["claude_timeout"] = claude_common.set_default_timeout(getattr(args, "timeout", None))
    return applied


def emit_preview(count, unit, sample=None, **extra):
    """Print the single machine-readable preview line server.py parses.

    Also prints a human-readable summary first — these scripts are run by hand from a
    terminal at least as often as they're run from the console, and a bare JSON blob is
    a poor answer to "what would this do?".
    """
    sample = list(sample or [])
    shown = sample[:SAMPLE_LIMIT]
    print(f"[PREVIEW] Would process {count} {unit}. No API calls made, nothing written.")
    for name in shown:
        print(f"  - {name}")
    if len(sample) > len(shown):
        print(f"  ... and {len(sample) - len(shown)} more")
    payload = {"count": count, "unit": unit, "sample": shown}
    payload.update(extra)
    print(f"{PREVIEW_PREFIX} {json.dumps(payload, ensure_ascii=False)}")
    return payload


def snapshot_stamp(when=None):
    """The `YYYYMMDD-HHMMSS` a snapshot filename carries.

    Seconds, not just the date. Snapshots used to be named by date alone, so a second run
    on the same day silently overwrote the first one's file — and since a --dry-run still
    pays the API in full, that overwrote work that had already cost real money, with no
    warning and no way to get it back. It also made the admin console's snapshot list a
    lie: the file said 20260819 and there was no way to tell which of that day's runs it
    held, or that two had happened at all.

    Local time deliberately, matching agent_logs/<agent>_<stamp>.log — an operator reading
    a filename is placing it against their own clock, not UTC. The date half stays first so
    dryrun_common's existing 8-digit date parse keeps working on both shapes, and plain
    lexicographic sorting of filenames stays chronological.
    """
    return (when or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")

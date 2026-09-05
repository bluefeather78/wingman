#!/usr/bin/env python3
"""Fail CI when a commit edits a MARQUEE-protected site without naming the entry.

WHY THIS EXISTS. MARQUEE_DECISIONS.md exists because a load-bearing decision was reversed
SILENTLY, inside a commit titled "Implement resume/LinkedIn profile import feature": the
metadata agent's live web fetch was switched off with a one-line aside, and a later cleanup
rewrote its prompt to match the broken state rather than restoring it. Rule 2 of that file —
"a marquee change is always its own dedicated commit whose message names the entry" — is the
rule that would have caught it, and until now nothing enforced it. A convention that depends on
the author remembering it is exactly the convention that failed the first time.

WHAT IT CHECKS. Every protected site carries a `MARQUEE M<n>:` sentinel comment (rule 3). For
each commit in the range, for each file it touches that contains sentinels, the check asks
whether the commit's changed lines land in a sentinel's REGION. If they do, the commit message
must name that entry — "MARQUEE M9" or "MARQUEE M8", in any casing.

A region is the sentinel line plus the code it guards, up to the next sentinel or REGION_LINES
(60) lines, whichever comes first. Whole-file matching would flag every typo in app/routes/ai.py
and a check that cries wolf gets switched off; line-exact matching would miss the one-line
`use_web_search=False` two lines under the comment, which is the actual historical failure.

DELIBERATELY NOT A SUBSTITUTE FOR APPROVAL. This cannot tell an approved marquee change from an
unapproved one — only that the author declared it. Rule 1 (ask Shama first) is still a human
gate. What this removes is the SILENT case.

    python scripts/ci/check_marquee_commits.py                 # HEAD against its parent
    python scripts/ci/check_marquee_commits.py --range A..B    # an explicit range
    python scripts/ci/check_marquee_commits.py --files a.py b.py --message "..."   # pre-commit
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The sentinel, per MARQUEE_DECISIONS.md rule 3. Matched anywhere on the line so it works in
# a `#` comment, a `//` one, a docstring or an HTML comment.
SENTINEL_RX = re.compile(r"MARQUEE\s+(M\d+)", re.I)

# Backstop only. A region normally ends at the blank line that closes the block the sentinel
# introduces (see sentinel_regions); this caps a sentinel inside a very long unbroken block.
REGION_LINES = 60

# Files whose ENTIRE contents are protected, whatever the line. app/services/prompts.py IS
# prompt text (M8) top to bottom, so a diff anywhere in it is a marquee change.
WHOLE_FILE_PROTECTED = {
    "app/services/prompts.py": "M8",
}

# Suffixes worth scanning. A .md edit is documentation ABOUT a decision, not the decision.
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".html", ".yaml", ".yml", ".sql")

# Paths that legitimately CONTAIN the sentinel text without being a protected site: the tests
# that assert marquee coverage, and this checker itself. Without the exclusion, writing a test
# that names an entry counts as changing that entry — which would make the guard fire on the
# very thing that verifies it.
SKIP_PREFIXES = ("tests/", "scripts/ci/check_marquee_commits.py")


def _git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=False).stdout


def _is_comment(stripped_line):
    """Comment/docstring line in any language this repo uses."""
    for opener in ("#", "//", "*", "<!--", chr(34) * 3, chr(39) * 3):
        if stripped_line.startswith(opener):
            return True
    return False


def sentinel_regions(text):
    """{entry -> set(line numbers it protects)} for one file's contents, 1-indexed.

    A region is the sentinel's own comment block PLUS the code block it introduces, ending at
    the first blank line after that code. This repo writes marquee comments immediately above
    the thing they guard and separates blocks with a blank line, so the shape is reliable —
    and the precision is the whole point.

    Too WIDE and the check flags an unrelated counter sixty lines below a sentinel; a check
    that cries wolf gets switched off, which is worse than no check. Too NARROW (the sentinel
    line alone) and it misses `use_web_search=False` sitting two lines under the comment, which
    is the actual historical failure this exists to catch.
    """
    lines = text.splitlines()
    marks = [(i + 1, m.group(1).upper())
             for i, line in enumerate(lines) if (m := SENTINEL_RX.search(line))]
    regions = {}
    for idx, (line_no, entry) in enumerate(marks):
        next_mark = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines) + 1
        hard_stop = min(line_no + REGION_LINES, next_mark - 1, len(lines))
        end, seen_code = line_no, False
        for n in range(line_no, hard_stop + 1):
            body = lines[n - 1].strip()
            if not body:
                if seen_code:
                    break          # blank line after the guarded code closes the region
                continue           # blank line still inside the comment block
            if not _is_comment(body):
                seen_code = True
            end = n
        regions.setdefault(entry, set()).update(range(line_no, end + 1))
    return regions


def changed_line_numbers(diff_text):
    """{path -> set(new-file line numbers touched)} from a unified diff."""
    out, path, new_line = {}, None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            out.setdefault(path, set())
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_line = int(m.group(1)) if m else 0
        elif path and line.startswith("+") and not line.startswith("+++"):
            out[path].add(new_line)
            new_line += 1
        elif path and not line.startswith("-") and not line.startswith("\\"):
            new_line += 1
    return out


def entries_touched(path, lines_touched, file_text):
    """Which marquee entries this file's changed lines land inside."""
    if path.startswith(SKIP_PREFIXES):
        return set()
    hit = {WHOLE_FILE_PROTECTED[path]} if path in WHOLE_FILE_PROTECTED else set()
    if not path.endswith(SOURCE_SUFFIXES):
        return hit
    for entry, protected in sentinel_regions(file_text).items():
        if lines_touched & protected:
            hit.add(entry)
    return hit


# A commit message declaring several entries writes them naturally — "MARQUEE M4 + M6 + M7" —
# so requiring the word MARQUEE before EACH one would reject the honest form and push authors
# toward splitting a single coherent change into three commits for the checker's benefit.
_ENTRY_RX = re.compile(r"\bM(\d+)\b", re.I)


def declared_entries(message):
    """Every entry a commit message declares.

    The word MARQUEE must appear somewhere — that is what makes the declaration deliberate
    rather than an accident of prose — and then every M<n> token in the message counts. A
    message that never says MARQUEE declares nothing, so a passing reference to "M9" in an
    unrelated commit cannot silently authorise a change to it.
    """
    text = message or ""
    if not re.search(r"MARQUEE", text, re.I):
        return set()
    return {f"M{m.group(1)}" for m in _ENTRY_RX.finditer(text)}


def check_commit(sha):
    """[] when the commit is fine, else a list of problem strings."""
    message = _git("log", "-1", "--format=%B", sha)
    diff = _git("show", "--format=", "--unified=0", sha)
    declared = declared_entries(message)
    problems = []
    for path, lines in changed_line_numbers(diff).items():
        if not lines:
            continue
        text = _git("show", f"{sha}:{path}")
        missing = entries_touched(path, lines, text) - declared
        if missing:
            subject = message.strip().splitlines()[0] if message.strip() else "(no message)"
            problems.append(
                f"{sha[:9]} {subject!r}\n"
                f"    touches {path} inside {', '.join(sorted(missing))} but the message "
                f"names {', '.join(sorted(declared)) or 'no entry'}.\n"
                f"    MARQUEE_DECISIONS.md rule 2: a marquee change is its own dedicated "
                f"commit whose message names the entry.")
    return problems


def check_working_files(paths, message):
    """The pre-commit / --files form: check paths as they stand on disk."""
    declared = declared_entries(message)
    problems = []
    for path in paths:
        full = os.path.join(ROOT, path)
        if not os.path.isfile(full):
            continue
        with open(full, encoding="utf-8", errors="replace") as f:
            text = f.read()
        touched = set(range(1, len(text.splitlines()) + 1))
        missing = entries_touched(path, touched, text) - declared
        if missing:
            problems.append(f"{path} is inside {', '.join(sorted(missing))} but the message "
                            f"names {', '.join(sorted(declared)) or 'no entry'}.")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--range", dest="rng",
                    help="Commit range, e.g. origin/main..HEAD. Default: HEAD's own diff.")
    ap.add_argument("--files", nargs="*", help="Check these paths as they are on disk instead.")
    ap.add_argument("--message", default="", help="Commit message to check --files against.")
    args = ap.parse_args()

    if args.files is not None:
        problems = check_working_files(args.files, args.message)
    else:
        rng = args.rng or "HEAD~1..HEAD"
        shas = [s for s in _git("rev-list", "--no-merges", rng).split() if s]
        if not shas:
            print("[OK] No commits to check.")
            return 0
        problems = [p for sha in shas for p in check_commit(sha)]

    if problems:
        print("[FAIL] Marquee-protected code changed without the commit naming the entry.\n")
        for p in problems:
            print("  " + p.replace("\n", "\n  ") + "\n")
        print("Fix by splitting the marquee change into its own commit whose message names "
              "the entry (e.g. 'MARQUEE M9 (…): …'), after getting approval in chat. "
              "See MARQUEE_DECISIONS.md.")
        return 1
    print("[OK] No undeclared marquee changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

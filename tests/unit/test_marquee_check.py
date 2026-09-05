"""The CI guard on MARQUEE_DECISIONS.md rule 2.

That file exists because a load-bearing decision was reversed SILENTLY, inside a commit titled
"Implement resume/LinkedIn profile import feature": refresh_opportunities' live web fetch was
switched off with a one-line aside, and a later cleanup rewrote its prompt to match the broken
state rather than restoring it. Rule 2 — a marquee change is its own dedicated commit whose
message names the entry — is what would have caught it, and nothing enforced it.

The check cannot tell an approved marquee change from an unapproved one. Rule 1 (ask first) is
still a human gate. What it removes is the silent case.
"""
import importlib.util
import os

import pytest

from wingman import REPO_ROOT

_PATH = os.path.join(REPO_ROOT, "scripts", "ci", "check_marquee_commits.py")


def _load():
    spec = importlib.util.spec_from_file_location("marquee_check", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mc = _load()


# The exact historical shape: the flip two lines under the sentinel comment.
_M1_FILE = '''def check_one(opp, key):
    # MARQUEE M1: fills metadata by READING THE LIVE PAGE, never from memory.
    # A fetch failure means SKIP the row, not fall back to the model's recall.
    text, usage = call_gemini(system, user, key, use_web_search=False)
    return text


def unrelated_helper():
    return 42
'''


def test_it_catches_the_failure_it_exists_for():
    """The M1 reversal: `use_web_search=False` on line 4, inside the sentinel's block."""
    assert mc.entries_touched("agents/refresh_opportunities.py", {4}, _M1_FILE) == {"M1"}


def test_declaring_the_entry_satisfies_it():
    touched = mc.entries_touched("agents/refresh_opportunities.py", {4}, _M1_FILE)
    assert not touched - mc.declared_entries("MARQUEE M1: restore the live fetch")
    assert not touched - mc.declared_entries("marquee m1 — lowercase still counts")


def test_unrelated_code_in_the_same_file_is_not_flagged():
    """A check that cries wolf gets switched off. Line 8 is outside the region."""
    assert mc.entries_touched("agents/refresh_opportunities.py", {8}, _M1_FILE) == set()


def test_the_region_ends_at_the_blank_line_after_the_guarded_code():
    regions = mc.sentinel_regions(_M1_FILE)
    assert regions == {"M1": {2, 3, 4, 5}}


def test_a_later_sentinel_closes_the_previous_region():
    text = "\n".join([
        "a = 1",                    # 1
        "# MARQUEE M8: prompt",     # 2
        "P = 'text'",               # 3
        "# MARQUEE M9: paid call",  # 4
        "call(paid=True)",          # 5
    ])
    regions = mc.sentinel_regions(text)
    assert regions["M8"] == {2, 3}
    assert regions["M9"] == {4, 5}


def test_whole_file_protection_for_the_prompts_module():
    """app/services/prompts.py IS prompt text top to bottom, so any diff in it is M8."""
    assert mc.entries_touched("app/services/prompts.py", {1}, "anything at all") == {"M8"}


def test_a_markdown_edit_is_not_a_marquee_change():
    """Documentation ABOUT a decision is not the decision."""
    assert mc.entries_touched("docs/CLAUDE-ops.md", {2}, _M1_FILE) == set()


def test_diff_line_numbers_are_parsed_from_a_unified_diff():
    diff = ("+++ b/a.py\n"
            "@@ -10,0 +11,2 @@\n"
            "+first\n"
            "+second\n"
            "+++ b/b.py\n"
            "@@ -1,0 +5 @@\n"
            "+only\n")
    assert mc.changed_line_numbers(diff) == {"a.py": {11, 12}, "b.py": {5}}


def test_declared_entries_reads_every_entry_named():
    assert mc.declared_entries("MARQUEE M8 and MARQUEE M9 both") == {"M8", "M9"}
    assert mc.declared_entries("no entry here") == set()
    assert mc.declared_entries(None) == set()


# --------------------------------------------------------------------------------------
# the guard is wired up, and the repo currently passes it
# --------------------------------------------------------------------------------------

def test_the_check_is_wired_into_ci():
    with open(os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
        ci = f.read()
    assert "scripts/ci/check_marquee_commits.py" in ci
    assert "fetch-depth: 0" in ci, "a depth-1 clone cannot resolve a commit range"


def _ratified_entries():
    """The M<n> entries under '## Ratified' in MARQUEE_DECISIONS.md."""
    import re
    with open(os.path.join(REPO_ROOT, "MARQUEE_DECISIONS.md"), encoding="utf-8") as f:
        text = f.read()
    body = text.split("## Ratified", 1)[1].split("## Proposed", 1)[0]
    return {m.group(1) for m in re.finditer(r"^### (M\d+)", body, re.M)}


def _sentinels_in_tree():
    """{entry -> [files carrying a parsable sentinel]} across the whole source tree."""
    found = {}
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs
                   if d not in {"node_modules", ".git", "dist", "__pycache__", ".venv"}]
        for f in files:
            if not f.endswith(mc.SOURCE_SUFFIXES) or f.startswith("test_"):
                continue
            path = os.path.join(root, f)
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = mc.SENTINEL_RX.search(line)
                    if m:
                        found.setdefault(m.group(1).upper(), set()).add(
                            os.path.relpath(path, REPO_ROOT))
    return found


# M3 (no paid agent runs without approval) and M10 (the match scorecard mirrors production) are
# process commitments, not single code sites, so neither has a sentinel to find. Every OTHER
# ratified entry must be reachable from the code, or rule 3 has quietly lapsed for it — and an
# entry with no sentinel is one this check can never enforce.
_PROCESS_ONLY_ENTRIES = {"M3", "M10"}


def test_every_ratified_entry_is_reachable_from_a_sentinel():
    expected = _ratified_entries() - _PROCESS_ONLY_ENTRIES
    assert expected, "parsed no ratified entries — the MARQUEE_DECISIONS.md headings moved"
    missing = sorted(expected - set(_sentinels_in_tree()))
    assert not missing, (
        f"ratified but carrying no parsable 'MARQUEE M<n>' sentinel anywhere in the source, "
        f"so the CI check can never enforce them: {missing}")


def test_the_m1_file_is_sentinel_marked():
    """M1 is the entry the whole system exists for. Its file must be findable."""
    assert "agents/refresh_opportunities.py" in _sentinels_in_tree().get("M1", set())


def test_no_sentinel_is_written_in_an_unparsable_form():
    """A typo like 'MARQUEE-M9' or 'MARQUEE  M 9' protects nothing at all. Only lines that
    clearly INTEND to be a sentinel are checked — prose like 'See MARQUEE_DECISIONS.md M9' is a
    cross-reference, not a sentinel, and is left alone."""
    import re
    intent = re.compile(r"MARQUEE[\s_-]+M(?!\d)|MARQUEE-M\d", re.I)
    bad = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs
                   if d not in {"node_modules", ".git", "dist", "__pycache__", ".venv"}]
        for f in files:
            # The checker's own docs necessarily spell the pattern out.
            if (not f.endswith(mc.SOURCE_SUFFIXES) or f.startswith("test_")
                    or f == "check_marquee_commits.py"):
                continue
            path = os.path.join(root, f)
            with open(path, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if intent.search(line) and not mc.SENTINEL_RX.search(line):
                        bad.append(f"{os.path.relpath(path, REPO_ROOT)}:{n}: {line.strip()[:70]}")
    assert not bad, f"sentinel-shaped but unparsable: {bad}"


def test_tests_and_the_checker_itself_are_not_protected_sites():
    """A test that asserts marquee coverage necessarily contains the sentinel text. Without
    this exclusion the guard would fire on the very file that verifies it."""
    text = "# MARQUEE M4: something"
    assert mc.entries_touched("tests/unit/test_marquee_check.py", {1}, text) == set()
    assert mc.entries_touched("scripts/ci/check_marquee_commits.py", {1}, text) == set()
    # ...but a real source file with the same content still is.
    assert mc.entries_touched("agents/scrape_opportunities.py", {1}, text) == {"M4"}


def test_a_message_can_declare_several_entries_naturally():
    """"MARQUEE M4 + M6 + M7" is how a coherent multi-entry change is actually written.
    Requiring the word before each one would push authors to split a single change into three
    commits for the checker's benefit."""
    assert mc.declared_entries("MARQUEE M4 + M6 + M7: add the missing sentinels") == {
        "M4", "M6", "M7"}
    assert mc.declared_entries("MARQUEE M9 (Phase 2 item 1): bound the AI lane") == {"M9"}


def test_a_message_that_never_says_marquee_declares_nothing():
    """Otherwise a passing 'M9' in unrelated prose would silently authorise a change to it."""
    assert mc.declared_entries("Fix the M9 highway routing test") == set()
    assert mc.declared_entries("bumped to M2 of the rollout") == set()

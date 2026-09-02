#!/usr/bin/env python3
"""Persist the discovery-gate verdicts onto a queue row so the review console can SURFACE them.

The page classifier (`classify_page`) and the content-embedding dedupe (`dedupe_confidence`) each
produce a verdict per pending row, but `classify_queue.py` / `dedupe_queue.py` are read-only
dry-runs -- the verdict is printed and thrown away. This module is the thin, PURE bridge that turns
a verdict into the row edit the review queue ALREADY knows how to render:

    classifier verdict -> a `classify: ...` entry in `quality_flags`  (rendered as a class pill)
    dedupe verdict     -> a `dup_candidates` entry naming the survivor (rendered as a back-link)

Both channels already flow to the review-queue UI and are already the home of "why look at this
row" hints -- the merge audit writes `merged ...` into `quality_flags`, and `url_dedupe` writes
`dup_candidates` at submission. Reusing them means NO schema migration and NO console data-plumbing,
only a render tweak. Everything here is pure and free; the WRITE is opt-in (`--write`) on the two
dry-run scripts, so their read-only default is unchanged.
"""

# quality_flags entries the classifier owns. `Classification.flag()` already returns a string that
# starts with this, so the prefix is the whole contract between the writer and the console renderer.
CLASSIFY_PREFIX = "classify:"

# Marks a `dup_candidates` entry THIS module wrote, so a re-run replaces only its own entries and
# never touches `url_dedupe`'s submission-time candidates sitting in the same column.
DEDUPE_VIA = "content-embedding"


def upsert_flag(flags, prefix, text):
    """Return `flags` with the single entry starting `prefix` set to `text` -- replaced if one is
    already there, else appended -- every other entry preserved. Pure.

    Re-running the classifier must not STACK `classify: ...` entries: the newest verdict replaces
    the stale one in place of piling up. `text` is expected to itself start with `prefix`.
    """
    kept = [str(f) for f in (flags or []) if not str(f).startswith(prefix)]
    return kept + [str(text)]


def flag_class(flags):
    """The class token carried by a `classify:` flag, or None if the row has none. Pure.

    'classify: program (high); STALE latest year 2012' -> 'program'
    'classify: unreadable (blocked)'                    -> 'unreadable'
    'classify: no verdict (unparsed)'                   -> 'no verdict'
    """
    for f in (flags or []):
        s = str(f)
        if s.startswith(CLASSIFY_PREFIX):
            return s[len(CLASSIFY_PREFIX):].split("(")[0].strip() or None
    return None


def dedupe_candidate(survivor, tier, cosine):
    """A `dup_candidates` entry (the shape `url_dedupe` writes) for a content-embedding match. Pure.

    `confidence` carries the TIER verbatim (proof/confident/adjudicate/hint) so the console colours
    it and the operator sees exactly what the dedupe logic judged. `via` tags it as ours.
    """
    return {
        "id": survivor.get("id"),
        "name": survivor.get("name"),
        "url": survivor.get("url"),
        "confidence": tier,
        "reason": f"content match cos={cosine:.3f}",
        "via": DEDUPE_VIA,
    }


def merge_candidates(existing, new_entries):
    """Return `dup_candidates` with this module's prior content-embedding entries replaced by
    `new_entries`, and `url_dedupe`'s submission-time entries preserved untouched. Pure.

    Keyed on the `via` marker rather than on id, so a survivor that CHANGED between runs does not
    leave a stale twin behind.
    """
    kept = [c for c in (existing or [])
            if not (isinstance(c, dict) and c.get("via") == DEDUPE_VIA)]
    return kept + list(new_entries)


# --- discontinuation gate (pure, free) ----------------------------------------------------------
# A model that READ a program's own page (mine_hub_pages today; any future page-extractor) can
# report the program as no longer running. The review-queue contract is: DROP it before insert. A
# discontinued program should never reach the reviewer at all -- keeping it out of the queue IS the
# point, not inserting it and asking a human to re-confirm what the page already stated. This is the
# SINGLE place the signal is interpreted, so every writer drops identically -- the alignment point:
# an extractor only has to surface `running`, then hand the dropped candidate to its OWN
# rejected-snapshot so nothing vanishes silently ("discard almost nothing, explain everything": we
# discard it from the QUEUE but keep the full record on disk).
#
# Only a POSITIVE signal drops: `running is False`. True / None / missing -> keep. Absence of a
# discontinuation signal is NEVER read as one, mirroring the deliberately narrow check in
# check_links.py: act on real evidence the program is over, never on its silence. (This is why the
# extract prompt must DEFAULT running to true and set it false only on explicit page evidence -- a
# false positive here silently deletes a live program from discovery, with no reviewer to catch it.)


def is_not_running(running):
    """True when an extractor's page-read says the program is no longer offered -> DROP before
    insert. Pure. Only `running is False` drops; True / None / missing keeps (never inferred from
    silence)."""
    return running is False


def not_running_reason(reason):
    """A short human explanation for the rejected-snapshot entry of a dropped not-running row. Pure."""
    why = str(reason).strip()[:200] if (reason and str(reason).strip()) else "no reason given"
    return f"page reads the program as no longer offered - {why}"

#!/usr/bin/env python3
"""Dedupe CONFIDENCE — the signals that turn a similarity HINT into an auto-actionable verdict.

The eval (2026-08-30) proved a single cosine cannot separate a DUPLICATE from a same-institution
SIBLING: the embedding sees shared boilerplate, so "Badger Music Clinic" and "Badger Arts Clinic",
and the YoungArts category competitions, all sit in the true-dup band. A score alone will always
need a human.

Confidence comes from INDEPENDENT signals AGREEING. This module carries the cheap, free ones — the
discriminators that see the difference the embedding misses, plus the deterministic proofs — and
combines them (with the cosine) into a TIER. Auto-action stays OFF until each tier's false-positive
rate is measured against accumulated operator verdicts; today the tier just LABELS a hint and routes
the ambiguous band toward the paid adjudicator.

    redirect / canonical equal ...................... PROOF      certain, auto-merge
    high cosine + name SAME + no field conflict ..... CONFIDENT  auto-merge once FP measured 0
    high cosine + name/field CONFLICT ............... SIBLING    NOT a dup (discriminator overrides)
    high cosine + name SUBSET/UNKNOWN ............... ADJUDICATE  send to the LLM judge
    moderate cosine, no conflict .................... HINT        human (small, ~constant tail)
    low cosine ...................................... NONE

All pure and FREE. The adjudicator (paid) and auto-merge live elsewhere; this decides which pairs
reach them. See SCRAPER_IMPROVEMENT_PLAN.md "Dedupe CONFIDENCE".
"""
import dataclasses
import re

import url_dedupe

# --- name-identity discriminator ------------------------------------------------------
# Structure/connector words that do NOT distinguish one program from its sibling. Season and
# location words are stripped here because they are checked as HARD FIELDS instead (defence in
# depth: if a name difference IS a season/location difference, field_relation catches it). Program
# TYPE words (camp, clinic, academy, workshop, institute…) are deliberately KEPT — "Music Clinic"
# vs "Music Camp" is a real distinction — as are all subject words (music, arts, robotics, design).
_STRUCTURE_WORDS = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "at", "to", "with", "by",
    "program", "programs", "high", "school", "students", "student", "youth", "teen", "teens",
    "summer", "winter", "spring", "fall", "autumn", "yearlong", "year",
    "online", "virtual", "person", "remote", "hybrid", "national", "annual",
}

NAME_SAME = "same"          # identical program identity
NAME_SUBSET = "subset"      # one name adds a qualifier the other lacks (rename OR sub-program)
NAME_CONFLICT = "conflict"  # each name carries a distinctive token the other lacks -> sibling
NAME_UNKNOWN = "unknown"    # too few identity tokens to judge


def _singular(tok):
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok


# A program often prints its own acronym in parentheses — "(CSSI)", "(SHIP)", "(CNI-X)". A SHARED
# one is strong evidence of the same program even when the spelled-out names differ ("Computer
# Science" vs "CS"), which is the abbreviation case the queue dry-run exposed. Only PARENTHETICAL
# acronyms count (a deliberate label), and a small generic set is excluded so "(STEM)" or "(USA)"
# cannot manufacture a match between different programs.
_GENERIC_ACRONYMS = {"USA", "US", "STEM", "STEAM", "AP", "SAT", "ACT", "GPA", "NYC", "USA", "HS"}
_ACRONYM_RE = re.compile(r"\(([A-Za-z0-9][A-Za-z0-9\-.]{1,})\)")


def extract_acronyms(name):
    """The distinctive parenthetical acronyms in a name, e.g. {'CSSI'}. Pure."""
    out = set()
    for m in _ACRONYM_RE.finditer(name or ""):
        tok = m.group(1).upper().replace(".", "")
        if len(tok.replace("-", "")) >= 3 and any(c.isalpha() for c in tok) and tok not in _GENERIC_ACRONYMS:
            out.add(tok)
    return out


def shared_acronym(a_name, b_name):
    """True when two names share a distinctive parenthetical acronym. Pure."""
    return bool(extract_acronyms(a_name) & extract_acronyms(b_name))


def identity_tokens(name, org=""):
    """The tokens that IDENTIFY this program, with org + structure words removed. Pure.

    Uses url_dedupe.normalize_name so this matches the rest of the dedupe stack. Org tokens are
    subtracted so a match on the institution cannot stand in for a match on the program — the same
    subtraction url_repair makes for exactly this reason.
    """
    org_toks = {_singular(t) for t in url_dedupe.normalize_name(org or "").split()}
    out = []
    for t in url_dedupe.normalize_name(name or "").split():
        s = _singular(t)
        if s and s not in _STRUCTURE_WORDS and s not in org_toks:
            out.append(s)
    return out


def name_relation(a_name, a_org, b_name, b_org):
    """How the two program identities relate: SAME / SUBSET / CONFLICT / UNKNOWN. Pure.

    A shared parenthetical acronym softens a CONFLICT to SUBSET — "Google Computer Science Summer
    Institute (CSSI)" and "Google CS Summer Institute (CSSI)" are the same program, and without this
    the CS/Computer-Science spelling difference read as a conflict and kept them wrongly distinct.
    Softened to SUBSET (not SAME) on purpose: an acronym alone routes to the judge, it never
    auto-merges on an abbreviation.
    """
    a, b = set(identity_tokens(a_name, a_org)), set(identity_tokens(b_name, b_org))
    if not a or not b:
        return NAME_UNKNOWN
    if a == b:
        return NAME_SAME
    if a <= b or b <= a:
        return NAME_SUBSET
    return NAME_SUBSET if shared_acronym(a_name, b_name) else NAME_CONFLICT


# --- hard-field discriminator ---------------------------------------------------------
# A genuine duplicate does not differ on these. A mismatch on any is a strong SIBLING tell — it is
# what separates two real programs the embedding calls the same. subject_tags are deliberately NOT
# here: they come from a small fixed list, so a Music vs Arts clinic can share the "Art" tag — the
# NAME discriminator catches that, not this.
_HARD_FIELDS = ("type", "season", "grade_min", "grade_max", "price")

FIELD_AGREE = "agree"
FIELD_CONFLICT = "conflict"
FIELD_UNKNOWN = "unknown"


def field_relation(a_row, b_row):
    """(relation, conflicting_fields). AGREE / CONFLICT / UNKNOWN over the hard fields. Pure."""
    conflicts, compared = [], 0
    for f in _HARD_FIELDS:
        va, vb = a_row.get(f), b_row.get(f)
        if va in (None, "") or vb in (None, ""):
            continue
        compared += 1
        if va != vb:
            conflicts.append(f)
    if conflicts:
        return FIELD_CONFLICT, conflicts
    return (FIELD_AGREE, []) if compared else (FIELD_UNKNOWN, [])


# --- deterministic proofs (auto-merge, certain) ---------------------------------------

_CANONICAL_RE = re.compile(
    r"""<link[^>]+rel=["']canonical["'][^>]+href=["']([^"']+)["']""", re.I)


def _key(url):
    try:
        return url_dedupe.match_key(url or "")
    except ValueError:
        return ""


def same_final_url(final_a, final_b):
    """True when two URLs resolve (after redirects) to the same page. Certain duplicate. Pure.

    `final_*` are the post-redirect URLs (page_text.fetch_page_text_resolved returns them). This is
    the `/alp/` -> `/accelerated-learning-program/` case: not similarity, identity.
    """
    ka, kb = _key(final_a), _key(final_b)
    return bool(ka) and ka == kb


def extract_canonical(html):
    """The normalized <link rel=canonical> key on a page, or "". Pure."""
    m = _CANONICAL_RE.search(html or "")
    return _key(m.group(1)) if m else ""


def same_canonical(canon_a, canon_b):
    """True when two pages declare the same canonical URL — the site itself says they are one. Pure."""
    return bool(canon_a) and canon_a == canon_b


# --- context guard: same institution? (protects auto-merge from generic-name collisions) -----
# Two DIFFERENT programs can share a generic name ("Youth Leadership Program") at different orgs.
# name=SAME + high cosine would auto-merge them. The guard: a CONFIDENT auto-merge must have
# positive evidence of the SAME institution — same registrable domain OR an agreeing org. Org, not
# only domain, because a real dup often arrives on a third-party/social URL (a Facebook or Empowerly
# page for a Stanford program) whose domain differs while the org plainly matches.
_ORG_GENERIC = {"university", "college", "institute", "school", "of", "the", "at", "for", "and",
                "program", "programs", "center", "centre", "summer", "national", "academy",
                "department", "dept", "foundation", "association", "associates", "society"}


def _org_tokens(org):
    return {_singular(t) for t in url_dedupe.normalize_name(org or "").split()
            if _singular(t) and _singular(t) not in _ORG_GENERIC}


def org_agrees(a_org, b_org):
    """True/False if the two orgs share a distinctive token (or one contains the other); None if
    either org is missing/only-generic so we cannot tell. Pure."""
    a, b = _org_tokens(a_org), _org_tokens(b_org)
    if not a or not b:
        return None
    return bool(a & b) or a <= b or b <= a


def _reg_domain(url):
    from urllib.parse import urlsplit
    try:
        return url_dedupe.registrable_domain(urlsplit(url or "").hostname or "")
    except ValueError:
        return ""


def same_registrable_domain(url_a, url_b):
    """True/False if two URLs share a registrable domain; None if either is missing. Pure."""
    da, db = _reg_domain(url_a), _reg_domain(url_b)
    if not da or not db:
        return None
    return da == db


def same_context(a_row, b_row):
    """True if the rows are plainly the same institution (same domain OR agreeing org), False if we
    have evidence they are not, None if we cannot tell. Pure."""
    dom = same_registrable_domain(a_row.get("url"), b_row.get("url"))
    org = org_agrees(a_row.get("org"), b_row.get("org"))
    if dom is True or org is True:
        return True
    if dom is None and org is None:
        return None
    return False


# --- the tier combiner ----------------------------------------------------------------

TIER_PROOF = "proof"            # redirect/canonical equal -> auto-merge (certain)
TIER_CONFIDENT = "confident"    # high sim + name SAME + no field conflict -> auto-merge (after FP=0)
TIER_ADJUDICATE = "adjudicate"  # high sim + name SUBSET/UNKNOWN, no conflict -> LLM judge
TIER_SIBLING = "sibling"        # high sim but a discriminator says different -> NOT a dup
TIER_HINT = "hint"              # moderate sim, no conflict -> human hint
TIER_NONE = "none"              # not similar enough to consider

# From the eval: real duplicates cluster at fields-cosine >= ~0.95; 0.90-0.93 is a mixed band.
CONFIDENT_COS = 0.95
HINT_FLOOR = 0.90


@dataclasses.dataclass
class PairVerdict:
    tier: str
    reasons: list = dataclasses.field(default_factory=list)

    @property
    def auto_mergeable(self):
        """Tiers eligible for AUTO action. Still gated on a measured FP rate before it is turned on."""
        return self.tier in (TIER_PROOF, TIER_CONFIDENT)


def classify_pair(cosine, name_rel, field_rel, *, proof=False, context_ok=None,
                  confident_cos=CONFIDENT_COS, hint_floor=HINT_FLOOR):
    """Combine the signals into a tier. Pure.

    `cosine` may be None when only the free discriminators are available (a conflict still resolves
    to SIBLING/NONE without it). `proof` is True when a deterministic proof fired. `context_ok` is
    the same-institution guard (see `same_context`): a CONFIDENT auto-merge is downgraded to
    ADJUDICATE when it is explicitly False, so a generic name matched ACROSS institutions never
    auto-merges. None (unknown) does not downgrade — same-domain callers like the pair eval pass it.
    """
    if proof:
        return PairVerdict(TIER_PROOF, ["redirect or canonical equal"])

    reasons = [f"name={name_rel}", f"fields={field_rel}"]
    if cosine is not None:
        reasons.insert(0, f"cos={cosine:.3f}")
    low = cosine is not None and cosine < hint_floor  # cosine None => discriminators decide

    # A NAME conflict is decisive: each name carries a distinctive token the other lacks — a
    # different program, however alike the pages look.
    if name_rel == NAME_CONFLICT:
        return PairVerdict(TIER_NONE if low else TIER_SIBLING, reasons)

    if name_rel == NAME_SAME:
        # Identical identity but a hard field disagrees is far likelier a DUPLICATE with a data
        # discrepancy than a true sibling — do NOT call it a sibling; let the judge reconcile it.
        if field_rel == FIELD_CONFLICT:
            return PairVerdict(TIER_NONE if low else TIER_ADJUDICATE, reasons)
        if cosine is None:
            return PairVerdict(TIER_HINT, reasons)   # names identical, no similarity signal yet
        if cosine >= confident_cos:
            if context_ok is False:
                # same name, high similarity, but a DIFFERENT institution — a generic-name
                # collision, not a duplicate to auto-merge. Let the judge decide.
                return PairVerdict(TIER_ADJUDICATE, reasons + ["cross-institution"])
            return PairVerdict(TIER_CONFIDENT, reasons)
        return PairVerdict(TIER_HINT if cosine >= hint_floor else TIER_NONE, reasons)

    # SUBSET or UNKNOWN identity. Here a hard-field conflict DOES mean a different program (a
    # qualifier plus a differing type/season is not one row's typo).
    if field_rel == FIELD_CONFLICT:
        return PairVerdict(TIER_NONE if low else TIER_SIBLING, reasons)
    if cosine is None:
        return PairVerdict(TIER_NONE, reasons)  # can't confirm subset/unknown from names alone
    if cosine >= confident_cos:
        return PairVerdict(TIER_ADJUDICATE, reasons)  # let the judge settle the qualifier
    return PairVerdict(TIER_HINT if cosine >= hint_floor else TIER_NONE, reasons)


def classify_rows(a_row, b_row, cosine=None, *, final_a=None, final_b=None,
                  canon_a=None, canon_b=None):
    """Convenience: run every free signal over two catalog rows and return the tier. Pure.

    Proofs use the optional resolved/canonical inputs when supplied (the caller fetched them);
    absent, only similarity + discriminators decide.
    """
    proof = (same_final_url(final_a or a_row.get("url"), final_b or b_row.get("url"))
             or same_canonical(canon_a or "", canon_b or ""))
    nr = name_relation(a_row.get("name"), a_row.get("org"), b_row.get("name"), b_row.get("org"))
    fr, _fields = field_relation(a_row, b_row)
    return classify_pair(cosine, nr, fr, proof=proof, context_ok=same_context(a_row, b_row))

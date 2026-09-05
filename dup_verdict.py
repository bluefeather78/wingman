"""Resolve ONE dedupe verdict per row: {confidence, best-guess duplicate}. Pure, free.

Phase 1 of docs/plans/DEDUPE_SIMPLIFICATION_PLAN.md. This is the SINGLE place the three detection logics
collapse into one label the reviewer acts on, replacing the four-writer `dup_candidates` pile:

    url_dedupe (URL/name)  ─┐
    dedupe_confidence       ├─►  resolve_dup_verdict()  ─►  one Verdict {confidence, duplicate_of}
    (name/field/acronym +   │
     embedding cosine)     ─┘

`dedupe_confidence.classify_rows` already FUSES every free signal (URL proof, name relation,
hard-field conflict, shared acronym, same-institution guard) with the embedding cosine into one
TIER per pair. All this module adds is: judge each candidate, keep the single strongest, and map
the engine's six tiers onto the three labels the console shows. It writes nothing and fetches
nothing — the caller supplies the candidate rows and a cosine lookup, so this stays pure and
offline-testable.

Decisions locked 2026-09-02 (plan §6): siblings shown only above a cosine floor (as 'possible');
auto-merge and the paid LLM adjudicator both OFF — every tier only LABELS.
"""
import dataclasses

import dedupe_confidence as dc

# --- the three operator-facing labels (what the console renders) --------------------------------
CONFIDENCE_CERTAIN = "certain"    # engine tier: proof (redirect/canonical equal)
CONFIDENCE_LIKELY = "likely"      # confident (high cosine + name same + no field conflict)
CONFIDENCE_POSSIBLE = "possible"  # adjudicate / hint / a shown high-cosine sibling

_TIER_TO_CONFIDENCE = {
    dc.TIER_PROOF: CONFIDENCE_CERTAIN,
    dc.TIER_CONFIDENT: CONFIDENCE_LIKELY,
    dc.TIER_ADJUDICATE: CONFIDENCE_POSSIBLE,
    dc.TIER_HINT: CONFIDENCE_POSSIBLE,
}

# Rank for picking the single strongest pair when a row has several candidates. Higher wins; ties
# break on cosine. A surfaced sibling ranks at the HINT floor so a real proof/confident always
# beats it.
_TIER_RANK = {
    dc.TIER_PROOF: 4,
    dc.TIER_CONFIDENT: 3,
    dc.TIER_ADJUDICATE: 2,
    dc.TIER_HINT: 1,
}

# Decision 2: a SIBLING is a discriminator-confirmed DIFFERENT program, so it is normally hidden.
# But a VERY high-cosine sibling is where the discriminators are most likely to be wrong, so above
# this cosine it still surfaces as a low-confidence 'possible' tagged as a sibling — a real dup
# mislabelled sibling gets a glance rather than vanishing. Below it, siblings never reach the queue.
SIBLING_SHOW_FLOOR = 0.93


@dataclasses.dataclass
class Verdict:
    """The single resolved dedupe verdict for one row. Serialized into the `dup_verdict` column."""
    confidence: str          # certain | likely | possible
    duplicate_of: str        # the survivor (best-guess) row id
    name: str                # survivor name (so the console needn't re-look-up)
    url: str                 # survivor url
    tier: str                # raw engine tier, for audit
    cosine: float | None     # dedupe_vector cosine, when available
    reasons: list            # the engine's reasons (+ a sibling note when applicable)
    sibling: bool = False    # True when surfaced from a high-cosine sibling

    def as_dict(self):
        return {
            "confidence": self.confidence,
            "duplicate_of": self.duplicate_of,
            "name": self.name,
            "url": self.url,
            "tier": self.tier,
            "cosine": self.cosine,
            "reasons": list(self.reasons),
            "sibling": self.sibling,
        }


def resolve_dup_verdict(row, candidate_rows, cosine_of, *, sibling_floor=SIBLING_SHOW_FLOOR):
    """Return the single strongest dedupe `Verdict` for `row`, or None. Pure.

    row           : the catalog row being judged (dict with id/name/org/url + the hard fields).
    candidate_rows: OTHER catalog rows to consider — the UNION of url_dedupe's Track-A hits and
                    the embedding Track-B nearest-neighbours. The caller generates them; this
                    function only judges and picks. Self and empty ids are skipped.
    cosine_of     : callable(other_id) -> float | None, the stored dedupe_vector cosine.

    Each candidate is judged by dedupe_confidence.classify_rows (all free signals + the cosine).
    `none` is never surfaced; `sibling` only above `sibling_floor` (as 'possible'); everything
    else maps through _TIER_TO_CONFIDENCE. The winner is the highest (tier rank, cosine).
    """
    rid = row.get("id")
    best = None
    best_key = None
    for other in candidate_rows:
        oid = other.get("id")
        if not oid or oid == rid:
            continue
        cos = cosine_of(oid)
        verdict = dc.classify_rows(row, other, cosine=cos)
        tier = verdict.tier
        if tier == dc.TIER_NONE:
            continue
        if tier == dc.TIER_SIBLING:
            if cos is None or cos < sibling_floor:
                continue  # a low/mid-cosine sibling is genuinely a different program — hide it
            confidence = CONFIDENCE_POSSIBLE
            rank = _TIER_RANK[dc.TIER_HINT]
            reasons = list(verdict.reasons) + [
                "looks similar but a field/name discriminator says a different program"
            ]
            is_sibling = True
        else:
            confidence = _TIER_TO_CONFIDENCE[tier]
            rank = _TIER_RANK[tier]
            reasons = list(verdict.reasons)
            is_sibling = False
        key = (rank, cos if cos is not None else -1.0)
        if best_key is None or key > best_key:
            best_key = key
            best = Verdict(
                confidence=confidence,
                duplicate_of=oid,
                name=other.get("name") or "",
                url=other.get("url") or "",
                tier=tier,
                cosine=cos,
                reasons=reasons,
                sibling=is_sibling,
            )
    return best

# Copyright 2026 lets-innovate.ch (Michael Hug)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""MPCO claim-type classifier — greenfield, deterministic, rule-based.

Maps a free-text MPCO claim (typically the concatenation of Property +
Outcome + any context text from the comparison-table row) to one of the
five canonical :class:`ClaimType` labels defined in Handoff 2026-06-26 §12
hybrid evidence hierarchy:

    REGULATORY_COMPLIANCE
    BIOCHEMISTRY_MATERIAL_PROPERTY
    SAFETY_ALLERGENICITY
    CLINICAL_PERFORMANCE
    HISTORICAL_MARKET_USE

The classifier uses keyword anchors of two strengths:

* **Strong anchors** — highly specific terms whose presence is on its own
  evidence for a claim type (e.g. ``"alpha-gal"`` → SAFETY_ALLERGENICITY,
  ``"722/2012"`` → REGULATORY_COMPLIANCE). Anchor lists were reviewed and
  approved as U-1.6-A in the Stufe 1.6 planning checkpoint.
* **Supporting anchors** — contextual terms whose presence is suggestive
  but not by itself sufficient (e.g. ``"degradation"``,
  ``"allergic"``). They only push a type above the review threshold when
  multiple co-occur, and they always set ``requires_review=True``.

Confidence semantics mirror :mod:`ring2.core.study_classifier`:

* :data:`CONFIDENCE_AUTHORITATIVE` = 0.85 — at or above this, a strong
  anchor has been matched. ``requires_review`` may still be ``True`` if a
  second type also scored highly (cross-type conflict).
* :data:`REVIEW_THRESHOLD` = 0.5 — below this, the result is forced to
  :attr:`ClaimType.UNKNOWN` with ``requires_review=True``.

Cross-type conflicts (e.g. ``"722/2012"`` + ``"alpha-gal"`` both fire)
are resolved by :data:`CLAIM_TYPE_PRIORITY` when two types score equally:
regulatory framing dominates, then safety, clinical, historical use, and
finally pure material biochemistry. The losing types are preserved in
:attr:`ClaimTypeClassification.alternative_types` and
``requires_review`` is set to ``True`` so the human reviewer can confirm.

Matching is case-insensitive. Anchor boundaries are alphanumeric-aware
(implemented via lookaround) so that:

* ``"MDR"`` matches ``"MDR-compliant"`` but **not** ``"medroxyprogesterone"``
  or ``"MDR1"``.
* ``"RCT"`` matches ``"RCT design"`` but **not** ``"PRCT"`` or ``"RCTs"``.
* ``"510(k)"`` matches as a literal phrase regardless of surrounding
  punctuation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "CLAIM_TYPE_PRIORITY",
    "CONFIDENCE_AUTHORITATIVE",
    "REVIEW_THRESHOLD",
    "ClaimType",
    "ClaimTypeClassification",
    "classify",
]


# ---------------------------------------------------------------------------
# Canonical claim types
# ---------------------------------------------------------------------------


class ClaimType(StrEnum):
    """The five hybrid-hierarchy claim types (Handoff 26-06-26 §12), plus UNKNOWN."""

    REGULATORY_COMPLIANCE = "regulatory_compliance"
    BIOCHEMISTRY_MATERIAL_PROPERTY = "biochemistry_material_property"
    SAFETY_ALLERGENICITY = "safety_allergenicity"
    CLINICAL_PERFORMANCE = "clinical_performance"
    HISTORICAL_MARKET_USE = "historical_market_use"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimTypeClassification:
    """Result of :func:`classify` for one claim-text input.

    Fields:
        claim_type: chosen :class:`ClaimType` label. Use ``UNKNOWN`` when
            no anchor scored above :data:`REVIEW_THRESHOLD`.
        confidence: 0.0-1.0; see module docstring for bands.
        evidence: ordered tuple of short strings describing which anchors
            triggered the classification. The first entry is the primary
            trigger; subsequent entries are corroborating anchors and
            cross-type conflicts worth recording for audit.
        requires_review: ``True`` when (a) confidence is below
            :data:`CONFIDENCE_AUTHORITATIVE`, or (b) more than one type
            scored above :data:`REVIEW_THRESHOLD` (cross-type conflict),
            or (c) only supporting anchors matched.
        alternative_types: cross-type runners-up that also scored above
            :data:`REVIEW_THRESHOLD`. Empty unless a conflict was
            detected. Order: descending by score, ties broken by
            :data:`CLAIM_TYPE_PRIORITY`.
    """

    claim_type: ClaimType
    confidence: float
    evidence: tuple[str, ...]
    requires_review: bool
    alternative_types: tuple[ClaimType, ...] = field(default=())

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"ClaimTypeClassification: confidence must be in [0, 1], got {self.confidence}"
            )
        if self.claim_type in self.alternative_types:
            raise ValueError(
                f"ClaimTypeClassification: primary type {self.claim_type!r} "
                "must not appear in alternative_types"
            )


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


CONFIDENCE_AUTHORITATIVE: float = 0.85
"""Minimum confidence considered authoritative (strong anchor match)."""

REVIEW_THRESHOLD: float = 0.5
"""Confidence below this triggers UNKNOWN + requires_review=True."""


# ---------------------------------------------------------------------------
# Tie-break priority — Handoff 26-06-27 conflict-zone resolution
# ---------------------------------------------------------------------------


#: When two claim types score equally, the one earlier in this tuple wins.
#: Rationale (per U-1.6-A planning checkpoint): regulatory framing dominates,
#: then safety, clinical, historical use, with pure material biochemistry
#: last because it is the most likely to be merely descriptive rather than
#: a substantive evidence claim.
CLAIM_TYPE_PRIORITY: tuple[ClaimType, ...] = (
    ClaimType.REGULATORY_COMPLIANCE,
    ClaimType.SAFETY_ALLERGENICITY,
    ClaimType.CLINICAL_PERFORMANCE,
    ClaimType.HISTORICAL_MARKET_USE,
    ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY,
)


# ---------------------------------------------------------------------------
# Keyword anchor tables — U-1.6-A approved
# ---------------------------------------------------------------------------


_STRONG_ANCHORS: dict[ClaimType, tuple[str, ...]] = {
    ClaimType.REGULATORY_COMPLIANCE: (
        "MDR",
        "MDD",
        "CE mark",
        "notified body",
        "MEDDEV",
        "MDCG",
        "722/2012",
        "ISO 10993",
        "ISO 13485",
        "Annex I",
        "GSPR",
        "510(k)",
        "PMA",
        "EMA/410/01",
        "Decision 2007/453",
    ),
    ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY: (
        "denaturation temperature",
        "triple helix",
        "telopeptide",
        "hydroxyproline",
        "crosslink",
        "porosity",
        "tensile strength",
        "pepsin digestion",
        "extraction yield",
        "molecular weight distribution",
        "amino acid composition",
    ),
    ClaimType.SAFETY_ALLERGENICITY: (
        "alpha-gal",
        "anaphylaxis",
        "IgE-mediated",
        "WHO-IUIS",
        "allergen nomenclature",
        "hypersensitivity reaction",
        "cross-reactivity",
        "sensitization",
    ),
    ClaimType.CLINICAL_PERFORMANCE: (
        "randomized controlled trial",
        "clinical trial",
        "cohort study",
        "case-control",
        "RCT",
        "patient outcome",
        "clinical efficacy",
        "in vivo human",
        "clinical follow-up",
        "bone regeneration",
    ),
    ClaimType.HISTORICAL_MARKET_USE: (
        "CE mark since",
        "marketed since",
        "post-market surveillance",
        "PMS report",
        "PMCF",
        "device registry",
        "real-world evidence",
        "long-term clinical experience",
    ),
}

_SUPPORTING_ANCHORS: dict[ClaimType, tuple[str, ...]] = {
    ClaimType.REGULATORY_COMPLIANCE: (
        "regulation",
        "directive",
        "guidance document",
        "conformity assessment",
        "regulatory framework",
    ),
    ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY: (
        "biocompatibility",
        "degradation",
        "resorption",
        "hydrolysis",
        "swelling",
        "pH stability",
        "mechanical properties",
    ),
    ClaimType.SAFETY_ALLERGENICITY: (
        "allergen",
        "allergic",
        "immunogenicity",
        "adverse event",
        "safety profile",
    ),
    ClaimType.CLINICAL_PERFORMANCE: (
        "implant",
        "graft",
        "healing",
        "endpoint",
        "clinical study",
        "subjects",
        "patients",
    ),
    ClaimType.HISTORICAL_MARKET_USE: (
        "historical use",
        "established use",
        "years of marketing",
        "marketed device",
    ),
}


# ---------------------------------------------------------------------------
# Compiled anchor patterns
# ---------------------------------------------------------------------------


def _compile_anchor(anchor: str) -> re.Pattern[str]:
    """Compile an anchor into a case-insensitive, alphanumeric-boundary-safe pattern.

    Uses lookaround instead of ``\\b`` because some anchors contain
    non-word characters (slashes, parens, hyphens) that break the
    standard ``\\b`` semantics.
    """
    escaped = re.escape(anchor)
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


_STRONG_PATTERNS: dict[ClaimType, tuple[tuple[str, re.Pattern[str]], ...]] = {
    ct: tuple((a, _compile_anchor(a)) for a in anchors) for ct, anchors in _STRONG_ANCHORS.items()
}

_SUPPORTING_PATTERNS: dict[ClaimType, tuple[tuple[str, re.Pattern[str]], ...]] = {
    ct: tuple((a, _compile_anchor(a)) for a in anchors)
    for ct, anchors in _SUPPORTING_ANCHORS.items()
}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


# Per Handoff 26-06-26 study-classifier convention: strong-anchor matches
# put the type "authoritative" at 0.85+; each additional strong anchor
# nudges confidence up but does not exceed 0.95. Supporting-only matches
# stay below CONFIDENCE_AUTHORITATIVE so requires_review is always set.
_STRONG_BASE_CONFIDENCE: float = 0.85
_STRONG_INCREMENT: float = 0.03
_STRONG_CAP: float = 0.95
_SUPPORTING_PER_HIT: float = 0.15  # 1 hit → 0.65 with base 0.5; 2 hits → 0.8 still below cap
_SUPPORTING_BASE_CONFIDENCE: float = 0.5
_SUPPORTING_CAP: float = 0.8


@dataclass(frozen=True, slots=True)
class _TypeScore:
    """Internal: per-type accumulator during scoring."""

    claim_type: ClaimType
    strong_hits: tuple[str, ...]
    supporting_hits: tuple[str, ...]

    @property
    def score(self) -> float:
        """Confidence score for this type given its hit counts.

        Strong hits dominate: any strong hit → base 0.85, capped at 0.95.
        Supporting-only hits stay in [0.5, 0.8] so :attr:`requires_review`
        remains True for the caller.
        """
        if self.strong_hits:
            return min(
                _STRONG_BASE_CONFIDENCE + _STRONG_INCREMENT * (len(self.strong_hits) - 1),
                _STRONG_CAP,
            )
        if self.supporting_hits:
            return min(
                _SUPPORTING_BASE_CONFIDENCE + _SUPPORTING_PER_HIT * (len(self.supporting_hits) - 1),
                _SUPPORTING_CAP,
            )
        return 0.0


def _score_type(claim_type: ClaimType, text: str) -> _TypeScore:
    """Score one :class:`ClaimType` against ``text``."""
    strong = tuple(
        anchor for anchor, pat in _STRONG_PATTERNS[claim_type] if pat.search(text) is not None
    )
    supporting = tuple(
        anchor for anchor, pat in _SUPPORTING_PATTERNS[claim_type] if pat.search(text) is not None
    )
    return _TypeScore(claim_type=claim_type, strong_hits=strong, supporting_hits=supporting)


def _priority_rank(claim_type: ClaimType) -> int:
    """Return the position of ``claim_type`` in :data:`CLAIM_TYPE_PRIORITY`. Lower wins."""
    return CLAIM_TYPE_PRIORITY.index(claim_type)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify(claim_text: str) -> ClaimTypeClassification:
    """Classify a free-text MPCO claim into one of five canonical types.

    Args:
        claim_text: the text to classify. Typically the concatenation of
            the claim's Property + Outcome fields and any contextual row
            text from the comparison table. An empty string is permitted
            and yields :attr:`ClaimType.UNKNOWN` with ``confidence=0.0``.

    Returns:
        a :class:`ClaimTypeClassification` with the chosen primary type,
        confidence, evidence trail, review flag, and any cross-type
        alternatives.
    """
    if not claim_text or not claim_text.strip():
        return ClaimTypeClassification(
            claim_type=ClaimType.UNKNOWN,
            confidence=0.0,
            evidence=("empty claim text",),
            requires_review=True,
        )

    # Score every type. Excluding UNKNOWN since it has no anchors.
    scored = [_score_type(ct, claim_text) for ct in ClaimType if ct is not ClaimType.UNKNOWN]

    # Only types with non-zero score matter.
    above_zero = [s for s in scored if s.score > 0.0]

    if not above_zero:
        return ClaimTypeClassification(
            claim_type=ClaimType.UNKNOWN,
            confidence=0.0,
            evidence=("no anchors matched",),
            requires_review=True,
        )

    # Pick primary: highest score, tie-broken by CLAIM_TYPE_PRIORITY.
    above_zero.sort(key=lambda s: (-s.score, _priority_rank(s.claim_type)))
    primary = above_zero[0]

    # Build evidence: primary's anchors first, then a note for each
    # alternative type that also scored above REVIEW_THRESHOLD.
    evidence_parts: list[str] = []
    evidence_parts.extend(f"strong: {a}" for a in primary.strong_hits)
    evidence_parts.extend(f"supporting: {a}" for a in primary.supporting_hits)

    # Alternatives: types other than the primary whose score is above the
    # review threshold. These flag a cross-type conflict.
    alternatives_above_threshold = [s for s in above_zero[1:] if s.score >= REVIEW_THRESHOLD]
    alternative_types = tuple(s.claim_type for s in alternatives_above_threshold)
    for s in alternatives_above_threshold:
        ev_anchors = ", ".join(s.strong_hits + s.supporting_hits)
        evidence_parts.append(f"alternative {s.claim_type.value}: {ev_anchors}")

    # If the primary's score is below REVIEW_THRESHOLD, force UNKNOWN.
    # This guards against pathological cases where a single supporting hit
    # would otherwise return a low-confidence type label.
    if primary.score < REVIEW_THRESHOLD:
        return ClaimTypeClassification(
            claim_type=ClaimType.UNKNOWN,
            confidence=primary.score,
            evidence=tuple(evidence_parts) or ("below review threshold",),
            requires_review=True,
        )

    requires_review = (
        primary.score < CONFIDENCE_AUTHORITATIVE
        or not primary.strong_hits  # supporting-only is always reviewable
        or bool(alternative_types)  # cross-type conflict
    )

    return ClaimTypeClassification(
        claim_type=primary.claim_type,
        confidence=primary.score,
        evidence=tuple(evidence_parts),
        requires_review=requires_review,
        alternative_types=alternative_types,
    )

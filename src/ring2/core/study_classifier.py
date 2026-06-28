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
"""Study design classifier — greenfield, deterministic, rule-based.

Maps a :class:`PubMedRecord` to a canonical :class:`StudyDesign` label using
a priority-ordered rule cascade:

    1. PubMed ``publication_types`` (authoritative; high confidence)
    2. Journal-name hint (e.g. Cochrane Database of Systematic Reviews → SR)
    3. Title / abstract keyword heuristics (lower confidence)

The classifier does **not** assign Oxford CEBM levels. Mapping a study
design to an evidence level is *claim-type dependent* (per Handoff
2026-06-26 §12 hybrid hierarchy: regulatory_compliance,
biochemistry_material_property, safety_allergenicity, clinical_performance,
historical_market_use) and is therefore the responsibility of the adapter,
not of the core.

Confidence semantics
--------------------
* ``confidence >= 0.85`` — authoritative match (PubMed pubtype hit).
* ``0.5 <= confidence < 0.85`` — heuristic match (keyword / journal).
* ``confidence < 0.5`` — design forced to ``UNKNOWN`` and
  ``requires_review=True``.

Conflicts between signals (e.g. ``Meta-Analysis`` pubtype + "case report"
in title) set ``requires_review=True`` regardless of confidence — the
classification stands, but a human should confirm it.

The module is import-cheap (stdlib only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .adapter_base import PubMedRecord

# ---------------------------------------------------------------------------
# Canonical study-design labels
# ---------------------------------------------------------------------------


class StudyDesign(StrEnum):
    """Canonical study-design labels.

    Ordered roughly from highest to lowest internal validity within each
    family, but the order is *not* an evidence ranking — that is
    claim-type dependent and lives in the adapter.
    """

    META_ANALYSIS = "meta_analysis"
    SYSTEMATIC_REVIEW = "systematic_review"
    RCT = "rct"
    COHORT = "cohort"
    CASE_CONTROL = "case_control"
    CASE_SERIES = "case_series"
    CASE_REPORT = "case_report"
    NARRATIVE_REVIEW = "narrative_review"
    GUIDELINE = "guideline"
    IN_VITRO = "in_vitro"
    ANIMAL_STUDY = "animal_study"
    EXPERT_OPINION = "expert_opinion"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StudyDesignClassification:
    """Result of :func:`classify` for one :class:`PubMedRecord`.

    Fields:
        pmid: identifier of the classified record.
        design: chosen :class:`StudyDesign` label.
        confidence: 0.0-1.0; see module docstring for bands.
        evidence: ordered tuple of short strings explaining which features
            triggered the classification. The first entry is the *primary*
            trigger; subsequent entries are corroborating or conflicting
            signals worth recording for audit.
        requires_review: ``True`` when (a) confidence is below the review
            threshold, or (b) conflicting signals were detected even if
            confidence is high.
    """

    pmid: str
    design: StudyDesign
    confidence: float
    evidence: tuple[str, ...]
    requires_review: bool

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"StudyDesignClassification for pmid={self.pmid!r}: "
                f"confidence must be in [0, 1], got {self.confidence}"
            )


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


CONFIDENCE_AUTHORITATIVE: float = 0.85
"""Minimum confidence considered 'authoritative' (PubMed pubtype match)."""

REVIEW_THRESHOLD: float = 0.5
"""Confidence below this triggers ``UNKNOWN`` + ``requires_review=True``."""


# ---------------------------------------------------------------------------
# Rule tables
# ---------------------------------------------------------------------------

# PubMed publication-type strings (case-insensitive match) → canonical
# design. Order within the dict does not matter; priority is enforced by
# the priority list below.
_PUBTYPE_MAP: dict[str, StudyDesign] = {
    "meta-analysis": StudyDesign.META_ANALYSIS,
    "systematic review": StudyDesign.SYSTEMATIC_REVIEW,
    "randomized controlled trial": StudyDesign.RCT,
    "clinical trial": StudyDesign.RCT,  # broad; review if no RCT signal
    "controlled clinical trial": StudyDesign.RCT,
    "observational study": StudyDesign.COHORT,
    "case reports": StudyDesign.CASE_REPORT,
    "case report": StudyDesign.CASE_REPORT,
    "practice guideline": StudyDesign.GUIDELINE,
    "guideline": StudyDesign.GUIDELINE,
    "consensus development conference": StudyDesign.GUIDELINE,
    "review": StudyDesign.NARRATIVE_REVIEW,  # downgraded if no SR signal
    "comparative study": StudyDesign.COHORT,
    "editorial": StudyDesign.EXPERT_OPINION,
    "letter": StudyDesign.EXPERT_OPINION,
    "comment": StudyDesign.EXPERT_OPINION,
    "news": StudyDesign.EXPERT_OPINION,
}

# Priority order: when multiple pubtypes match, the earlier entry wins.
# Meta-analysis beats systematic review beats RCT, etc.
_PUBTYPE_PRIORITY: tuple[StudyDesign, ...] = (
    StudyDesign.META_ANALYSIS,
    StudyDesign.SYSTEMATIC_REVIEW,
    StudyDesign.RCT,
    StudyDesign.GUIDELINE,
    StudyDesign.COHORT,
    StudyDesign.CASE_REPORT,
    StudyDesign.NARRATIVE_REVIEW,
    StudyDesign.EXPERT_OPINION,
)

# Journal-name hints. Used both as confidence-boost when pubtype agrees
# and as a primary signal when pubtypes are empty. Case-insensitive
# substring match.
_JOURNAL_HINTS: dict[str, StudyDesign] = {
    "cochrane database of systematic reviews": StudyDesign.SYSTEMATIC_REVIEW,
}

# Title/abstract keyword cues. Patterns are compiled once at import time.
# Each cue maps to a (design, weight) pair. Weights are heuristic and
# sum to a confidence score (capped at the authoritative threshold so
# heuristics never claim authoritative status).
_KEYWORD_RULES: tuple[tuple[re.Pattern[str], StudyDesign, float], ...] = (
    (re.compile(r"\bmeta-?analys[ie]s\b", re.IGNORECASE), StudyDesign.META_ANALYSIS, 0.75),
    (re.compile(r"\bsystematic review\b", re.IGNORECASE), StudyDesign.SYSTEMATIC_REVIEW, 0.75),
    (re.compile(r"\bnetwork meta-?analys[ie]s\b", re.IGNORECASE), StudyDesign.META_ANALYSIS, 0.80),
    (
        re.compile(r"\brandomi[sz]ed controlled trial\b", re.IGNORECASE),
        StudyDesign.RCT,
        0.75,
    ),
    (re.compile(r"\bdouble-?blind\b.*\btrial\b", re.IGNORECASE), StudyDesign.RCT, 0.65),
    (re.compile(r"\bcohort study\b", re.IGNORECASE), StudyDesign.COHORT, 0.70),
    (re.compile(r"\bprospective cohort\b", re.IGNORECASE), StudyDesign.COHORT, 0.70),
    (re.compile(r"\bretrospective cohort\b", re.IGNORECASE), StudyDesign.COHORT, 0.70),
    (re.compile(r"\bcase-?control study\b", re.IGNORECASE), StudyDesign.CASE_CONTROL, 0.75),
    (re.compile(r"\bcase series\b", re.IGNORECASE), StudyDesign.CASE_SERIES, 0.70),
    (re.compile(r"\bcase report\b", re.IGNORECASE), StudyDesign.CASE_REPORT, 0.70),
    (re.compile(r"\bin vitro\b", re.IGNORECASE), StudyDesign.IN_VITRO, 0.70),
    (re.compile(r"\bbench(?: |-)?test\b", re.IGNORECASE), StudyDesign.IN_VITRO, 0.60),
    (re.compile(r"\banimal (?:study|model)\b", re.IGNORECASE), StudyDesign.ANIMAL_STUDY, 0.70),
    (
        re.compile(r"\brat(?:s)?\b|\bmouse\b|\bmice\b|\brabbit(?:s)?\b", re.IGNORECASE),
        StudyDesign.ANIMAL_STUDY,
        0.55,
    ),
    (re.compile(r"\bnarrative review\b", re.IGNORECASE), StudyDesign.NARRATIVE_REVIEW, 0.70),
    (re.compile(r"\bguideline\b", re.IGNORECASE), StudyDesign.GUIDELINE, 0.55),
    (re.compile(r"\bconsensus statement\b", re.IGNORECASE), StudyDesign.GUIDELINE, 0.70),
    (re.compile(r"\bexpert opinion\b", re.IGNORECASE), StudyDesign.EXPERT_OPINION, 0.65),
)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(record: PubMedRecord) -> StudyDesignClassification:
    """Classify ``record`` into a canonical :class:`StudyDesign`.

    Algorithm:

        1. Match each ``publication_types`` entry against :data:`_PUBTYPE_MAP`.
           If any matches, pick the highest-priority design and assign an
           authoritative confidence (0.90).
        2. Independently scan the journal name against :data:`_JOURNAL_HINTS`.
           Record the hit as evidence; if step 1 produced no design, use
           the journal hint as the primary signal (confidence 0.80).
        3. Scan title + abstract against :data:`_KEYWORD_RULES`. Aggregate
           hits per design (max-weight per design).
        4. Pick the final design:

           * If step 1 produced a design, keep it; corroborate or conflict
             with steps 2-3 (conflicts set ``requires_review=True``).
           * Else pick the keyword/journal design with the highest weight.

        5. If the chosen confidence is below :data:`REVIEW_THRESHOLD`,
           downgrade the design to :data:`StudyDesign.UNKNOWN` and set
           ``requires_review=True``.

    The function is deterministic and idempotent: classifying the same
    record twice yields equal :class:`StudyDesignClassification` instances.
    """
    evidence: list[str] = []
    requires_review = False

    # -- Step 1: PubMed pubtype matching ------------------------------------
    pubtype_design: StudyDesign | None = None
    pubtype_matched_strings: list[str] = []
    candidates: set[StudyDesign] = set()
    for pt in record.publication_types:
        key = pt.strip().lower()
        if key in _PUBTYPE_MAP:
            candidates.add(_PUBTYPE_MAP[key])
            pubtype_matched_strings.append(pt)

    if candidates:
        for design in _PUBTYPE_PRIORITY:
            if design in candidates:
                pubtype_design = design
                break
        # If no priority match (e.g. only IN_VITRO/ANIMAL_STUDY signals from
        # pubtypes, which don't exist in the map currently), fall back to
        # the first candidate deterministically.
        if pubtype_design is None:
            pubtype_design = sorted(candidates, key=lambda d: d.value)[0]
        evidence.append(f"pubtype:{','.join(sorted(pubtype_matched_strings))}")

    # -- Step 2: Journal-name hint -----------------------------------------
    journal_design: StudyDesign | None = None
    if record.journal:
        journal_lower = record.journal.lower()
        for needle, design in _JOURNAL_HINTS.items():
            if needle in journal_lower:
                journal_design = design
                evidence.append(f"journal:{record.journal!r}->{design.value}")
                break

    # -- Step 3: Keyword scan over title + abstract -------------------------
    haystack = " ".join(filter(None, (record.title, record.abstract or "")))
    keyword_scores: dict[StudyDesign, float] = {}
    keyword_evidence: dict[StudyDesign, list[str]] = {}
    for pattern, design, weight in _KEYWORD_RULES:
        if pattern.search(haystack):
            prior = keyword_scores.get(design, 0.0)
            if weight > prior:
                keyword_scores[design] = weight
            keyword_evidence.setdefault(design, []).append(pattern.pattern)

    for design, patterns in keyword_evidence.items():
        evidence.append(f"keyword[{design.value}]:{'|'.join(patterns)}")

    # -- Step 4: Resolve final design + confidence --------------------------
    design: StudyDesign
    confidence: float

    if pubtype_design is not None:
        design = pubtype_design
        confidence = 0.90

        # Special-case: "Review" pubtype is ambiguous. Promote to SR if
        # title/journal corroborate; otherwise narrative.
        if design is StudyDesign.NARRATIVE_REVIEW and (
            StudyDesign.SYSTEMATIC_REVIEW in keyword_scores
            or journal_design is StudyDesign.SYSTEMATIC_REVIEW
        ):
            design = StudyDesign.SYSTEMATIC_REVIEW
            evidence.append("promoted:Review->SystematicReview by title/journal")
            confidence = 0.92

        # Conflict detection: a strongly weighted keyword for a different
        # *family* (e.g. case_report keyword vs RCT pubtype) flags review.
        for kw_design, weight in keyword_scores.items():
            if kw_design is design:
                continue
            if weight >= 0.70 and _families_conflict(design, kw_design):
                requires_review = True
                evidence.append(
                    f"conflict:pubtype={design.value} vs keyword={kw_design.value}@{weight:.2f}"
                )

        # Journal corroboration boost
        if journal_design is design:
            confidence = min(0.95, confidence + 0.03)

    elif journal_design is not None:
        design = journal_design
        confidence = 0.80
        # Keywords may still corroborate
        if keyword_scores.get(design, 0.0) >= 0.70:
            confidence = 0.83

    elif keyword_scores:
        design, weight = max(keyword_scores.items(), key=lambda kv: kv[1])
        # Cap heuristic confidence below the authoritative threshold.
        confidence = min(weight, CONFIDENCE_AUTHORITATIVE - 0.05)
    else:
        design = StudyDesign.UNKNOWN
        confidence = 0.0
        evidence.append("no_signals")

    # -- Step 5: Downgrade if below review threshold ------------------------
    if design is not StudyDesign.UNKNOWN and confidence < REVIEW_THRESHOLD:
        evidence.append(f"downgrade:{design.value}@{confidence:.2f}<{REVIEW_THRESHOLD}->UNKNOWN")
        design = StudyDesign.UNKNOWN
        requires_review = True
    elif design is StudyDesign.UNKNOWN:
        requires_review = True

    return StudyDesignClassification(
        pmid=record.pmid,
        design=design,
        confidence=round(confidence, 4),
        evidence=tuple(evidence),
        requires_review=requires_review,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Families for conflict detection. Same family = ok to disagree on
# sub-type; different family = flag for review.
_FAMILY: dict[StudyDesign, str] = {
    StudyDesign.META_ANALYSIS: "synthesis",
    StudyDesign.SYSTEMATIC_REVIEW: "synthesis",
    StudyDesign.NARRATIVE_REVIEW: "synthesis",
    StudyDesign.RCT: "primary_clinical",
    StudyDesign.COHORT: "primary_clinical",
    StudyDesign.CASE_CONTROL: "primary_clinical",
    StudyDesign.CASE_SERIES: "primary_clinical_descriptive",
    StudyDesign.CASE_REPORT: "primary_clinical_descriptive",
    StudyDesign.IN_VITRO: "preclinical",
    StudyDesign.ANIMAL_STUDY: "preclinical",
    StudyDesign.GUIDELINE: "normative",
    StudyDesign.EXPERT_OPINION: "normative",
    StudyDesign.UNKNOWN: "unknown",
}


def _families_conflict(a: StudyDesign, b: StudyDesign) -> bool:
    """``True`` if ``a`` and ``b`` belong to different design families.

    Family membership is defined in :data:`_FAMILY`. Two designs in the
    same family (e.g. RCT and Cohort, both 'primary_clinical') do not
    conflict; cross-family disagreement is flagged for review.
    """
    return _FAMILY.get(a, "unknown") != _FAMILY.get(b, "unknown")


__all__ = [
    "CONFIDENCE_AUTHORITATIVE",
    "REVIEW_THRESHOLD",
    "StudyDesign",
    "StudyDesignClassification",
    "classify",
]

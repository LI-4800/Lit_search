# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.core.study_classifier."""

from __future__ import annotations

import pytest

from ring2.core.adapter_base import PubMedRecord
from ring2.core.study_classifier import (
    CONFIDENCE_AUTHORITATIVE,
    REVIEW_THRESHOLD,
    StudyDesign,
    StudyDesignClassification,
    classify,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rec(
    *,
    pmid: str = "1",
    title: str = "",
    abstract: str | None = None,
    journal: str | None = None,
    publication_types: tuple[str, ...] = (),
) -> PubMedRecord:
    return PubMedRecord(
        pmid=pmid,
        title=title,
        abstract=abstract,
        journal=journal,
        publication_types=publication_types,
    )


# ---------------------------------------------------------------------------
# Dataclass validation
# ---------------------------------------------------------------------------


def test_classification_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValueError, match="confidence must be in"):
        StudyDesignClassification(
            pmid="1",
            design=StudyDesign.RCT,
            confidence=1.5,
            evidence=(),
            requires_review=False,
        )


def test_classification_rejects_negative_confidence() -> None:
    with pytest.raises(ValueError, match="confidence must be in"):
        StudyDesignClassification(
            pmid="1",
            design=StudyDesign.RCT,
            confidence=-0.1,
            evidence=(),
            requires_review=False,
        )


# ---------------------------------------------------------------------------
# Authoritative pubtype matches
# ---------------------------------------------------------------------------


def test_pubtype_meta_analysis_authoritative() -> None:
    r = _rec(title="Bovine collagen safety", publication_types=("Meta-Analysis",))
    c = classify(r)
    assert c.design is StudyDesign.META_ANALYSIS
    assert c.confidence >= CONFIDENCE_AUTHORITATIVE
    assert not c.requires_review
    assert any("pubtype" in e for e in c.evidence)


def test_pubtype_rct_authoritative() -> None:
    r = _rec(
        title="A trial of pepsin-extracted collagen",
        publication_types=("Randomized Controlled Trial",),
    )
    c = classify(r)
    assert c.design is StudyDesign.RCT
    assert c.confidence >= CONFIDENCE_AUTHORITATIVE


def test_pubtype_priority_meta_beats_systematic_review() -> None:
    r = _rec(publication_types=("Systematic Review", "Meta-Analysis"))
    c = classify(r)
    assert c.design is StudyDesign.META_ANALYSIS


def test_pubtype_priority_review_demoted_to_narrative() -> None:
    """'Review' pubtype with no SR signal -> narrative review."""
    r = _rec(title="Recent advances in dental biomaterials", publication_types=("Review",))
    c = classify(r)
    assert c.design is StudyDesign.NARRATIVE_REVIEW


def test_pubtype_review_promoted_to_sr_by_title() -> None:
    """'Review' pubtype + 'systematic review' in title -> SR (not narrative)."""
    r = _rec(
        title="A systematic review of bovine collagen safety",
        publication_types=("Review",),
    )
    c = classify(r)
    assert c.design is StudyDesign.SYSTEMATIC_REVIEW
    assert any("promoted" in e for e in c.evidence)


# ---------------------------------------------------------------------------
# Journal hint (Cochrane lesson)
# ---------------------------------------------------------------------------


def test_journal_cochrane_drives_sr_when_pubtypes_empty() -> None:
    """OsteoGen lesson: Cochrane reviews must not be missed."""
    r = _rec(
        title="Interventions for alveolar ridge preservation",
        journal="Cochrane Database of Systematic Reviews",
    )
    c = classify(r)
    assert c.design is StudyDesign.SYSTEMATIC_REVIEW
    assert any("journal" in e and "Cochrane" in e for e in c.evidence)
    assert not c.requires_review


def test_journal_cochrane_corroborates_pubtype_boost() -> None:
    r = _rec(
        publication_types=("Systematic Review",),
        journal="Cochrane Database of Systematic Reviews",
    )
    c = classify(r)
    assert c.design is StudyDesign.SYSTEMATIC_REVIEW
    # Corroboration bumps confidence above the bare pubtype baseline.
    assert c.confidence > 0.90


# ---------------------------------------------------------------------------
# Keyword fallback (no pubtype, no journal hint)
# ---------------------------------------------------------------------------


def test_keyword_rct_from_title_alone() -> None:
    r = _rec(title="Randomized controlled trial of bovine bone substitute")
    c = classify(r)
    assert c.design is StudyDesign.RCT
    # Heuristic — must stay below authoritative threshold.
    assert c.confidence < CONFIDENCE_AUTHORITATIVE
    assert c.confidence >= REVIEW_THRESHOLD


def test_keyword_in_vitro_from_abstract() -> None:
    r = _rec(
        title="Mechanical properties of a synthetic graft",
        abstract="In vitro analysis of compressive strength.",
    )
    c = classify(r)
    assert c.design is StudyDesign.IN_VITRO


def test_keyword_animal_study_from_title() -> None:
    r = _rec(title="Bone regeneration in a rabbit calvarial model")
    c = classify(r)
    assert c.design is StudyDesign.ANIMAL_STUDY


def test_keyword_case_report_from_title() -> None:
    r = _rec(title="Severe allergic reaction to bovine collagen: a case report")
    c = classify(r)
    assert c.design is StudyDesign.CASE_REPORT


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


def test_conflict_pubtype_rct_vs_keyword_case_report_flags_review() -> None:
    """Cross-family disagreement triggers review even at authoritative confidence."""
    r = _rec(
        title="Case report of failure during a randomized trial follow-up",
        publication_types=("Randomized Controlled Trial",),
    )
    c = classify(r)
    assert c.design is StudyDesign.RCT  # pubtype wins design
    assert c.requires_review
    assert any("conflict" in e for e in c.evidence)


def test_no_conflict_within_same_family() -> None:
    """RCT pubtype + 'cohort' keyword -> same family -> no review flag."""
    r = _rec(
        title="A randomized prospective cohort comparison",
        publication_types=("Randomized Controlled Trial",),
    )
    c = classify(r)
    assert c.design is StudyDesign.RCT
    assert not c.requires_review


# ---------------------------------------------------------------------------
# Unknown / low-confidence
# ---------------------------------------------------------------------------


def test_empty_record_yields_unknown_with_review() -> None:
    r = _rec(title="")
    c = classify(r)
    assert c.design is StudyDesign.UNKNOWN
    assert c.requires_review
    assert c.confidence == 0.0


def test_irrelevant_title_yields_unknown() -> None:
    r = _rec(title="Hello world, miscellaneous notes on something")
    c = classify(r)
    assert c.design is StudyDesign.UNKNOWN
    assert c.requires_review


# ---------------------------------------------------------------------------
# Determinism & idempotence
# ---------------------------------------------------------------------------


def test_classification_is_deterministic() -> None:
    r = _rec(
        pmid="33899930",
        title="Interventions for replacing missing teeth: alveolar ridge preservation",
        journal="Cochrane Database of Systematic Reviews",
        publication_types=("Meta-Analysis", "Systematic Review"),
    )
    a = classify(r)
    b = classify(r)
    assert a == b


def test_evidence_field_is_tuple_immutable() -> None:
    r = _rec(publication_types=("Meta-Analysis",))
    c = classify(r)
    assert isinstance(c.evidence, tuple)


# ---------------------------------------------------------------------------
# Realistic compound case — DEV-008 / Atieh Cochrane review
# ---------------------------------------------------------------------------


def test_atieh_cochrane_review_realistic() -> None:
    """Realistic shape of the late-identified Atieh 2021 review (PMID 33899930)."""
    r = _rec(
        pmid="33899930",
        title=(
            "Interventions for replacing missing teeth: alveolar ridge "
            "preservation techniques for dental implant site development"
        ),
        abstract=(
            "Background: Various materials have been investigated as graft "
            "materials. This systematic review evaluates the effects of "
            "alveolar ridge preservation techniques."
        ),
        journal="The Cochrane Database of Systematic Reviews",
        publication_types=("Systematic Review", "Meta-Analysis", "Review"),
    )
    c = classify(r)
    assert c.design is StudyDesign.META_ANALYSIS
    assert c.confidence >= CONFIDENCE_AUTHORITATIVE
    assert not c.requires_review

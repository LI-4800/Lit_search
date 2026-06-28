# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.appraisal.meddev_a6 — Stufe 1.8 Inkrement 7.

Coverage:
    * A6Category enum — 7 members, hyphen-canonical values, declaration order
    * A6_CATEGORY_TITLES — 7 verbatim titles
    * NullA6Classifier — raises on use
    * MeddevA6Result — schema + cross-validators V1 (findings-keys ⊆ categories)
      and V2 (qualifies ⇔ empty categories)
    * MeddevA6Lens — registry registration, applicable_claim_types, default
      classifier, claim-type guard, classifier delegation, qualify threshold
    * render_summary — empty input, mixed qualifying/non-qualifying, category
      coverage, type-narrowing rejection
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from ring2.adapters.mpco.appraisal import get_lens, names
from ring2.adapters.mpco.appraisal.base import AppraisalResult
from ring2.adapters.mpco.appraisal.meddev_a6 import (
    A6_CATEGORY_TITLES,
    A6Category,
    A6Classification,
    MeddevA6Classifier,
    MeddevA6Lens,
    MeddevA6Result,
    NullA6Classifier,
)
from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import (
    Comparator,
    Material,
    MPCOClaim,
    Outcome,
    Property,
)
from ring2.adapters.mpco.table_mapping import CellRef
from ring2.core.adapter_base import PubMedRecord

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_claim(claim_type: ClaimType = ClaimType.CLINICAL_PERFORMANCE) -> MPCOClaim:
    return MPCOClaim(
        claim_id="TEST-001",
        source_table_cell=CellRef(workbook="t.xlsx", sheet="S", row=1, column_label="A"),
        material=Material(description="m"),
        property=Property(description="p"),
        comparator=Comparator(description="c"),
        outcome=Outcome(description="o"),
        applicable_regulation="722_2012",
        claim_type=claim_type,
    )


def _make_record(pmid: str = "12345") -> PubMedRecord:
    return PubMedRecord(pmid=pmid, title="Some title", abstract="Some abstract.")


@dataclass(frozen=True, slots=True)
class _FakeClassifier:
    """Test double — returns a pre-set classification on every call."""

    outcome: A6Classification

    def classify(self, *, record: PubMedRecord, claim: MPCOClaim) -> A6Classification:
        return self.outcome


# ---------------------------------------------------------------------------
# A6Category enum
# ---------------------------------------------------------------------------


def test_a6_category_has_seven_members() -> None:
    assert len(list(A6Category)) == 7


def test_a6_category_values_are_hyphen_canonical() -> None:
    """Every value matches '<letter>-<words-with-hyphens>' format."""
    for cat in A6Category:
        # Must start with a single lowercase letter, then hyphen.
        assert cat.value[0].islower()
        assert cat.value[1] == "-"
        # No underscores anywhere in the value.
        assert "_" not in cat.value


def test_a6_category_declaration_order_a_to_g() -> None:
    """Declaration order is a → g; iteration must follow."""
    leading_letters = [cat.value[0] for cat in A6Category]
    assert leading_letters == ["a", "b", "c", "d", "e", "f", "g"]


def test_a6_category_titles_complete_and_verbatim() -> None:
    """A6_CATEGORY_TITLES has all 7 entries with the verbatim MEDDEV wording."""
    assert len(A6_CATEGORY_TITLES) == 7
    # Spot-check verbatim titles against MEDDEV 2.7/1 Rev. 4 §A6.
    assert (
        A6_CATEGORY_TITLES[A6Category.A_LACK_OF_INFORMATION]
        == "Lack of information on elementary aspects"
    )
    assert (
        A6_CATEGORY_TITLES[A6Category.E_IMPROPER_MORTALITY_DATA]
        == "Improper collection of mortality and serious adverse events data"
    )
    assert A6_CATEGORY_TITLES[A6Category.G_ILLEGAL_ACTIVITIES] == "Illegal activities"


# ---------------------------------------------------------------------------
# NullA6Classifier
# ---------------------------------------------------------------------------


def test_null_classifier_raises_on_use() -> None:
    """NullA6Classifier raises ValueError when classify() is invoked."""
    clf = NullA6Classifier()
    with pytest.raises(ValueError, match="without MeddevA6Classifier"):
        clf.classify(record=_make_record(), claim=_make_claim())


def test_null_classifier_satisfies_protocol() -> None:
    """NullA6Classifier structurally satisfies the MeddevA6Classifier Protocol."""
    clf: MeddevA6Classifier = NullA6Classifier()  # type-checker assertion
    # Use it once to silence "unused" lint, expecting the raise.
    with pytest.raises(ValueError):
        clf.classify(record=_make_record(), claim=_make_claim())


# ---------------------------------------------------------------------------
# MeddevA6Result — schema + validators
# ---------------------------------------------------------------------------


def test_result_empty_categories_qualifies_true() -> None:
    """Empty applicable_categories + qualifies=True is valid."""
    r = MeddevA6Result(
        pmid="1",
        lens_name="meddev_a6",
        rationale="No §A6 deficiency detected.",
        qualifies=True,
        applicable_categories=frozenset(),
        category_findings={},
    )
    assert r.qualifies is True
    assert r.applicable_categories == frozenset()


def test_result_one_category_qualifies_false() -> None:
    """One applicable category + qualifies=False is valid."""
    r = MeddevA6Result(
        pmid="1",
        lens_name="meddev_a6",
        rationale="Falls under §A6(b).",
        qualifies=False,
        applicable_categories=frozenset({A6Category.B_NUMBERS_TOO_SMALL}),
        category_findings={A6Category.B_NUMBERS_TOO_SMALL: "n=4 — preliminary data"},
    )
    assert r.qualifies is False


def test_result_v1_findings_keys_must_be_subset() -> None:
    """V1: category_findings keys must all appear in applicable_categories."""
    with pytest.raises(ValidationError, match="not in applicable_categories"):
        MeddevA6Result(
            pmid="1",
            lens_name="meddev_a6",
            rationale="r",
            qualifies=False,
            applicable_categories=frozenset({A6Category.B_NUMBERS_TOO_SMALL}),
            category_findings={
                A6Category.B_NUMBERS_TOO_SMALL: "ok",
                A6Category.C_IMPROPER_STATISTICAL_METHODS: "stray",
            },
        )


def test_result_v2_qualifies_must_match_categories() -> None:
    """V2: qualifies=True with non-empty categories is rejected."""
    with pytest.raises(ValidationError, match="contradicts"):
        MeddevA6Result(
            pmid="1",
            lens_name="meddev_a6",
            rationale="r",
            qualifies=True,
            applicable_categories=frozenset({A6Category.A_LACK_OF_INFORMATION}),
            category_findings={A6Category.A_LACK_OF_INFORMATION: "x"},
        )


def test_result_v2_qualifies_false_with_empty_categories_rejected() -> None:
    """V2 (reverse): qualifies=False with empty categories is rejected."""
    with pytest.raises(ValidationError, match="contradicts"):
        MeddevA6Result(
            pmid="1",
            lens_name="meddev_a6",
            rationale="r",
            qualifies=False,
            applicable_categories=frozenset(),
            category_findings={},
        )


# ---------------------------------------------------------------------------
# Registry / lens metadata
# ---------------------------------------------------------------------------


def test_meddev_a6_registered_under_correct_name() -> None:
    assert "meddev_a6" in names()
    assert get_lens("meddev_a6") is MeddevA6Lens


def test_meddev_a6_applicable_claim_types() -> None:
    """Per appraisal matrix: clinical_performance + safety_allergenicity."""
    assert MeddevA6Lens.applicable_claim_types == frozenset(
        {ClaimType.CLINICAL_PERFORMANCE, ClaimType.SAFETY_ALLERGENICITY}
    )


def test_meddev_a6_default_classifier_is_null() -> None:
    """Zero-arg constructor installs NullA6Classifier (must raise on appraise)."""
    lens = MeddevA6Lens()
    with pytest.raises(ValueError, match="without MeddevA6Classifier"):
        lens.appraise(_make_record(), _make_claim())


# ---------------------------------------------------------------------------
# Lens.appraise — delegation + threshold + guard
# ---------------------------------------------------------------------------


def test_appraise_rejects_inapplicable_claim_type() -> None:
    """Claim type outside applicable_claim_types raises ValueError."""
    lens = MeddevA6Lens(classifier=NullA6Classifier())
    claim = _make_claim(claim_type=ClaimType.HISTORICAL_MARKET_USE)
    with pytest.raises(ValueError, match="does not apply to claim_type"):
        lens.appraise(_make_record(), claim)


def test_appraise_qualify_when_classifier_reports_no_categories() -> None:
    """Lens threshold: empty applicable_categories → qualifies=True."""
    outcome = A6Classification(
        applicable_categories=frozenset(),
        category_findings={},
        rationale="No §A6 deficiency detected.",
    )
    lens = MeddevA6Lens(classifier=_FakeClassifier(outcome))
    result = lens.appraise(_make_record("11111"), _make_claim())
    assert isinstance(result, MeddevA6Result)
    assert result.pmid == "11111"
    assert result.lens_name == "meddev_a6"
    assert result.qualifies is True
    assert result.applicable_categories == frozenset()


def test_appraise_non_qualify_when_classifier_reports_categories() -> None:
    """Lens threshold: ≥ 1 applicable_categories → qualifies=False."""
    outcome = A6Classification(
        applicable_categories=frozenset(
            {A6Category.B_NUMBERS_TOO_SMALL, A6Category.D_LACK_OF_ADEQUATE_CONTROLS}
        ),
        category_findings={
            A6Category.B_NUMBERS_TOO_SMALL: "n=4 — preliminary",
            A6Category.D_LACK_OF_ADEQUATE_CONTROLS: "single-arm design",
        },
        rationale="Falls under §A6(b) and §A6(d).",
    )
    lens = MeddevA6Lens(classifier=_FakeClassifier(outcome))
    result = lens.appraise(_make_record("22222"), _make_claim())
    assert result.qualifies is False
    assert len(result.applicable_categories) == 2


def test_appraise_classifier_inconsistency_raises() -> None:
    """If classifier returns findings for a non-applicable category, V1 fires."""
    outcome = A6Classification(
        applicable_categories=frozenset({A6Category.A_LACK_OF_INFORMATION}),
        category_findings={
            A6Category.A_LACK_OF_INFORMATION: "ok",
            A6Category.G_ILLEGAL_ACTIVITIES: "stray finding",
        },
        rationale="r",
    )
    lens = MeddevA6Lens(classifier=_FakeClassifier(outcome))
    with pytest.raises(ValidationError, match="not in applicable_categories"):
        lens.appraise(_make_record(), _make_claim())


# ---------------------------------------------------------------------------
# render_summary
# ---------------------------------------------------------------------------


def test_render_summary_empty_results() -> None:
    """Empty input: headline 0/0/0, all categories at 0, 'None' for both lists."""
    summary = MeddevA6Lens().render_summary(())
    assert "### Lens: MEDDEV 2.7/1 Rev. 4 §A6" in summary
    assert "Records appraised: 0" in summary
    assert "Qualifying (no §A6 deficiency): 0" in summary
    assert "Non-qualifying (≥ 1 §A6 deficiency): 0" in summary
    # All 7 categories listed, each at "0 record(s)".
    for cat in A6Category:
        assert f"`{cat.value}`" in summary
    # Both lists are empty.
    assert summary.count("_None._") == 2


def test_render_summary_mixed_results() -> None:
    """Mixed qualifying/non-qualifying input is summarised correctly."""
    qualifying = MeddevA6Result(
        pmid="11111",
        lens_name="meddev_a6",
        rationale="ok",
        qualifies=True,
        applicable_categories=frozenset(),
        category_findings={},
    )
    non_q_b = MeddevA6Result(
        pmid="22222",
        lens_name="meddev_a6",
        rationale="b",
        qualifies=False,
        applicable_categories=frozenset({A6Category.B_NUMBERS_TOO_SMALL}),
        category_findings={A6Category.B_NUMBERS_TOO_SMALL: "n=4"},
    )
    non_q_bd = MeddevA6Result(
        pmid="33333",
        lens_name="meddev_a6",
        rationale="b+d",
        qualifies=False,
        applicable_categories=frozenset(
            {A6Category.B_NUMBERS_TOO_SMALL, A6Category.D_LACK_OF_ADEQUATE_CONTROLS}
        ),
        category_findings={
            A6Category.B_NUMBERS_TOO_SMALL: "n=2",
            A6Category.D_LACK_OF_ADEQUATE_CONTROLS: "single-arm",
        },
    )
    summary = MeddevA6Lens().render_summary((qualifying, non_q_b, non_q_bd))
    assert "Records appraised: 3" in summary
    assert "Qualifying (no §A6 deficiency): 1" in summary
    assert "Non-qualifying (≥ 1 §A6 deficiency): 2" in summary
    # Category B has 2 records, D has 1, the rest have 0.
    assert (
        "`b-numbers-too-small` — Numbers too small for statistical significance: 2 record(s)"
        in summary
    )
    assert "`d-lack-of-adequate-controls` — Lack of adequate controls: 1 record(s)" in summary
    assert (
        "`a-lack-of-information` — Lack of information on elementary aspects: 0 record(s)"
        in summary
    )
    # Qualifying section lists 11111.
    assert "`11111`" in summary
    # Non-qualifying section lists 22222 and 33333 with their categories.
    assert "`22222` — categories: ['b-numbers-too-small']" in summary
    assert "`33333` — categories: ['b-numbers-too-small', 'd-lack-of-adequate-controls']" in summary


def test_render_summary_rejects_foreign_result_subclass() -> None:
    """A bare AppraisalResult (not MeddevA6Result) triggers TypeError."""
    foreign = AppraisalResult(pmid="1", lens_name="rob2", rationale="r", qualifies=True)
    with pytest.raises(TypeError, match="expected MeddevA6Result"):
        MeddevA6Lens().render_summary((foreign,))

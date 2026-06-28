# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for :mod:`ring2.adapters.mpco.appraisal.rule_based_a6`."""

from __future__ import annotations

from ring2.adapters.mpco.appraisal.meddev_a6 import (
    A6Category,
    A6Classification,
    MeddevA6Classifier,
)
from ring2.adapters.mpco.appraisal.rule_based_a6 import RuleBasedA6Classifier
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
# Fixtures
# ---------------------------------------------------------------------------


def _claim() -> MPCOClaim:
    return MPCOClaim(
        claim_id="CB-bov-01",
        source_table_cell=CellRef(
            workbook="Comparator-Tables.xlsx",
            sheet="Bovine-Collagen",
            row=4,
            column_label="Pepsin",
        ),
        material=Material(description="Bovine-derived collagen"),
        property=Property(description="Biocompatibility"),
        comparator=Comparator(description="Porcine collagen"),
        outcome=Outcome(description="Inflammatory response"),
        applicable_regulation="722_2012",
        claim_type=ClaimType.CLINICAL_PERFORMANCE,
    )


def _record(
    pmid: str = "12345678",
    title: str = "A randomized controlled trial",
    abstract: str = "A standard abstract with no specific n value.",
) -> PubMedRecord:
    return PubMedRecord(pmid=pmid, title=title, abstract=abstract)


# ---------------------------------------------------------------------------
# Protocol conformance + construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_no_args_construction(self) -> None:
        clf = RuleBasedA6Classifier()
        assert clf is not None

    def test_implements_classifier_protocol(self) -> None:
        # Structural check: has .classify() with the right shape.
        clf = RuleBasedA6Classifier()
        result = clf.classify(record=_record(), claim=_claim())
        assert isinstance(result, A6Classification)

    def test_can_be_assigned_to_protocol_typed_slot(self) -> None:
        # Mypy-style: the instance is assignable to the Protocol.
        clf: MeddevA6Classifier = RuleBasedA6Classifier()
        out = clf.classify(record=_record(), claim=_claim())
        assert isinstance(out, A6Classification)

    def test_is_hashable(self) -> None:
        # Frozen dataclass → hashable, safe in sets / dict keys.
        clf = RuleBasedA6Classifier()
        s = {clf, RuleBasedA6Classifier()}
        assert len(s) == 1


# ---------------------------------------------------------------------------
# Category b — numbers too small
# ---------------------------------------------------------------------------


class TestCategoryB:
    def test_n_less_than_threshold_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="We enrolled n=4 patients with bovine collagen implants.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.B_NUMBERS_TOO_SMALL in out.applicable_categories
        finding = out.category_findings[A6Category.B_NUMBERS_TOO_SMALL]
        assert "n=4" in finding

    def test_n_at_threshold_not_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="A pilot study with n=10 participants.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.B_NUMBERS_TOO_SMALL not in out.applicable_categories

    def test_n_above_threshold_not_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="We randomized n=124 patients into two arms.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.B_NUMBERS_TOO_SMALL not in out.applicable_categories

    def test_smallest_n_chosen_when_multiple_present(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="The treatment arm (n=120) was matched to controls (n=5).")
        out = clf.classify(record=record, claim=_claim())
        # n=5 is below threshold → b flagged. Despite the fact that
        # there is also a healthy n=120 group.
        assert A6Category.B_NUMBERS_TOO_SMALL in out.applicable_categories

    def test_n_with_spaces_around_equals(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="The cohort (n = 3) showed a strong response.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.B_NUMBERS_TOO_SMALL in out.applicable_categories

    def test_title_case_report_flags_b(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(
            title="A case report of bovine collagen-induced anaphylaxis",
            abstract="Standard abstract.",
        )
        out = clf.classify(record=record, claim=_claim())
        # 'case report' is a small-n cue too.
        assert A6Category.B_NUMBERS_TOO_SMALL in out.applicable_categories


# ---------------------------------------------------------------------------
# Category d — lack of adequate controls
# ---------------------------------------------------------------------------


class TestCategoryD:
    def test_single_arm_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="This single-arm prospective study evaluated 80 patients.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.D_LACK_OF_ADEQUATE_CONTROLS in out.applicable_categories

    def test_uncontrolled_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="An uncontrolled prospective observational study.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.D_LACK_OF_ADEQUATE_CONTROLS in out.applicable_categories

    def test_case_series_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(title="A retrospective case series of 30 patients")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.D_LACK_OF_ADEQUATE_CONTROLS in out.applicable_categories

    def test_no_control_group_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="The study had no control group due to ethical concerns.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.D_LACK_OF_ADEQUATE_CONTROLS in out.applicable_categories

    def test_case_insensitive(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="The CASE SERIES enrolled 30 patients.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.D_LACK_OF_ADEQUATE_CONTROLS in out.applicable_categories


# ---------------------------------------------------------------------------
# Combined / multi-category
# ---------------------------------------------------------------------------


class TestCombined:
    def test_case_report_triggers_both_b_and_d(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(title="A case report of immediate hypersensitivity")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.B_NUMBERS_TOO_SMALL in out.applicable_categories
        assert A6Category.D_LACK_OF_ADEQUATE_CONTROLS in out.applicable_categories

    def test_findings_keys_subset_of_applicable(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(title="Case report of one patient")
        out = clf.classify(record=record, claim=_claim())
        assert set(out.category_findings.keys()).issubset(out.applicable_categories)


# ---------------------------------------------------------------------------
# Negative case (no deficiency detected)
# ---------------------------------------------------------------------------


class TestNegative:
    def test_well_powered_rct_no_flag(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(
            title="A randomized controlled trial of bovine collagen membranes",
            abstract="We enrolled 124 patients in a parallel-group design with two arms.",
        )
        out = clf.classify(record=record, claim=_claim())
        assert out.applicable_categories == frozenset()
        assert out.category_findings == {}
        assert "no" in out.rationale.lower() and "deficiency" in out.rationale.lower()

    def test_categories_not_evaluated_mentioned_in_rationale(self) -> None:
        # The classifier should be transparent that 5 categories are unchecked.
        clf = RuleBasedA6Classifier()
        record = _record(abstract="Standard abstract.")
        out = clf.classify(record=record, claim=_claim())
        # Rationale mentions the un-checked categories explicitly.
        assert "a, c, e, f, g" in out.rationale.lower() or "Stufe 1.10" in out.rationale


# ---------------------------------------------------------------------------
# Categories not evaluated — c, e, f, g, a
# ---------------------------------------------------------------------------


class TestUncheckedCategories:
    def test_category_a_never_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(
            abstract="Lack of methodological detail on inclusion criteria and statistical handling."
        )
        out = clf.classify(record=record, claim=_claim())
        # The classifier does not flag a (would require full-text scrutiny).
        assert A6Category.A_LACK_OF_INFORMATION not in out.applicable_categories

    def test_category_c_never_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="The Chi-square test was applied to ordinal data.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.C_IMPROPER_STATISTICAL_METHODS not in out.applicable_categories

    def test_category_e_never_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="No serious adverse events were systematically recorded.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.E_IMPROPER_MORTALITY_DATA not in out.applicable_categories

    def test_category_f_never_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="The authors conclude that bovine collagen is universally safe.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.F_MISINTERPRETATION not in out.applicable_categories

    def test_category_g_never_flagged(self) -> None:
        clf = RuleBasedA6Classifier()
        record = _record(abstract="The study was conducted off-label and without ethics approval.")
        out = clf.classify(record=record, claim=_claim())
        assert A6Category.G_ILLEGAL_ACTIVITIES not in out.applicable_categories


# ---------------------------------------------------------------------------
# Integration: classifier → MeddevA6Lens
# ---------------------------------------------------------------------------


class TestLensIntegration:
    def test_lens_with_rule_based_classifier_is_operational(self) -> None:
        from ring2.adapters.mpco.appraisal.meddev_a6 import MeddevA6Lens

        lens = MeddevA6Lens(classifier=RuleBasedA6Classifier())
        assert lens.is_operational() is True

    def test_lens_appraises_case_report_as_non_qualifying(self) -> None:
        from ring2.adapters.mpco.appraisal.meddev_a6 import MeddevA6Lens

        lens = MeddevA6Lens(classifier=RuleBasedA6Classifier())
        record = _record(title="A case report of bovine collagen hypersensitivity")
        result = lens.appraise(record, _claim())
        assert result.qualifies is False
        assert A6Category.B_NUMBERS_TOO_SMALL in result.applicable_categories
        assert A6Category.D_LACK_OF_ADEQUATE_CONTROLS in result.applicable_categories

    def test_lens_appraises_well_powered_rct_as_qualifying(self) -> None:
        from ring2.adapters.mpco.appraisal.meddev_a6 import MeddevA6Lens

        lens = MeddevA6Lens(classifier=RuleBasedA6Classifier())
        record = _record(
            title="A randomized controlled trial of bovine collagen",
            abstract="124 patients in two arms with primary endpoint analysis.",
        )
        result = lens.appraise(record, _claim())
        assert result.qualifies is True
        assert result.applicable_categories == frozenset()

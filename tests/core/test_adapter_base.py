# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.core.adapter_base."""

from dataclasses import FrozenInstanceError, dataclass

import pytest

from ring2.core.adapter_base import (
    Adapter,
    AppraisalDecision,
    AppraisalOutcome,
    ExclusionCriteria,
    ExclusionCriterion,
    InclusionCriteria,
    InclusionCriterion,
    PubMedRecord,
    ReportArtefact,
    clear,
    get,
    names,
    register,
)

# ---------------------------------------------------------------------------
# Dataclasses are frozen and validate themselves
# ---------------------------------------------------------------------------


def test_pubmed_record_is_frozen() -> None:
    r = PubMedRecord(pmid="1", title="t")
    with pytest.raises(FrozenInstanceError):
        r.pmid = "2"  # type: ignore[misc]


def test_pubmed_record_defaults() -> None:
    r = PubMedRecord(pmid="1", title="t")
    assert r.doi is None
    assert r.abstract is None
    assert r.authors == ()
    assert r.publication_types == ()
    assert r.raw == {}


def test_appraisal_outcome_values() -> None:
    assert AppraisalOutcome.INCLUDE.value == "include"
    assert AppraisalOutcome.EXCLUDE.value == "exclude"
    assert AppraisalOutcome.REVIEW.value == "requires_review"
    # StrEnum equality with str:
    assert AppraisalOutcome.INCLUDE == "include"


def test_appraisal_decision_minimal_include() -> None:
    d = AppraisalDecision(
        pmid="X",
        outcome=AppraisalOutcome.INCLUDE,
        exclusion_code=None,
        rationale="meets criteria",
    )
    assert d.confidence is None
    assert d.requires_review is False


def test_appraisal_decision_exclude_requires_code() -> None:
    with pytest.raises(ValueError, match="exclusion_code is required"):
        AppraisalDecision(
            pmid="X",
            outcome=AppraisalOutcome.EXCLUDE,
            exclusion_code=None,
            rationale="r",
        )


def test_appraisal_decision_include_forbids_code() -> None:
    with pytest.raises(ValueError, match="must be None"):
        AppraisalDecision(
            pmid="X",
            outcome=AppraisalOutcome.INCLUDE,
            exclusion_code="EX-DESIGN",
            rationale="r",
        )


def test_appraisal_decision_review_forbids_code() -> None:
    with pytest.raises(ValueError, match="must be None"):
        AppraisalDecision(
            pmid="X",
            outcome=AppraisalOutcome.REVIEW,
            exclusion_code="EX-DESIGN",
            rationale="r",
        )


def test_appraisal_decision_exclude_with_code_ok() -> None:
    d = AppraisalDecision(
        pmid="X",
        outcome=AppraisalOutcome.EXCLUDE,
        exclusion_code="EX-DESIGN",
        rationale="case report",
    )
    assert d.exclusion_code == "EX-DESIGN"


def test_appraisal_decision_confidence_bounds() -> None:
    # in-bounds OK
    AppraisalDecision(
        pmid="X",
        outcome=AppraisalOutcome.INCLUDE,
        exclusion_code=None,
        rationale="r",
        confidence=0.0,
    )
    AppraisalDecision(
        pmid="X",
        outcome=AppraisalOutcome.INCLUDE,
        exclusion_code=None,
        rationale="r",
        confidence=1.0,
    )
    # out-of-bounds raise
    with pytest.raises(ValueError, match=r"confidence must be in \[0, 1\]"):
        AppraisalDecision(
            pmid="X",
            outcome=AppraisalOutcome.INCLUDE,
            exclusion_code=None,
            rationale="r",
            confidence=1.1,
        )
    with pytest.raises(ValueError, match=r"confidence must be in \[0, 1\]"):
        AppraisalDecision(
            pmid="X",
            outcome=AppraisalOutcome.INCLUDE,
            exclusion_code=None,
            rationale="r",
            confidence=-0.1,
        )


def test_report_artefact_requires_path_or_content() -> None:
    with pytest.raises(ValueError, match="path or content"):
        ReportArtefact(format="markdown")


def test_report_artefact_with_content_ok() -> None:
    a = ReportArtefact(format="markdown", content="# Hello")
    assert a.path is None
    assert a.content == "# Hello"


# ---------------------------------------------------------------------------
# Criteria collections
# ---------------------------------------------------------------------------


def test_inclusion_criteria_holds_ordered_tuple() -> None:
    a = InclusionCriterion(id="INC-001", description="human in-vivo")
    b = InclusionCriterion(id="INC-002", description="full text retrievable")
    c = InclusionCriteria(criteria=(a, b))
    assert c.criteria == (a, b)


def test_exclusion_criteria_holds_ordered_tuple() -> None:
    a = ExclusionCriterion(code="EX-DESIGN", description="case report")
    b = ExclusionCriterion(code="EX-INVITRO", description="cell culture")
    c = ExclusionCriteria(criteria=(a, b))
    assert c.criteria == (a, b)


# ---------------------------------------------------------------------------
# Adapter ABC
# ---------------------------------------------------------------------------


def test_adapter_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Adapter()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeSchema:
    name: str = "FAKE"
    fields: tuple[str, ...] = ("a", "b")


@dataclass(frozen=True)
class _FakeQuestion:
    claim_id: str = "x"


def _make_fake_adapter(adapter_name: str) -> type[Adapter]:
    """Build a minimal concrete Adapter subclass for registry tests."""

    class FakeAdapter(Adapter):
        name = adapter_name

        @property
        def schema(self) -> _FakeSchema:
            return _FakeSchema()

        def inclusion_criteria(self, question):
            return InclusionCriteria(criteria=())

        def exclusion_criteria(self, question):
            return ExclusionCriteria(criteria=())

        def appraise(self, record, question):
            return AppraisalDecision(
                pmid=record.pmid,
                outcome=AppraisalOutcome.INCLUDE,
                exclusion_code=None,
                rationale="-",
            )

        def render_report(self, state):
            return ReportArtefact(format="markdown", content="-")

    return FakeAdapter


@pytest.fixture
def clean_registry():
    clear()
    yield
    clear()


def test_register_and_get(clean_registry: None) -> None:
    cls = _make_fake_adapter("Alpha")
    register(cls)
    assert get("Alpha") is cls
    assert "Alpha" in names()


def test_register_returns_class_for_decorator_use(clean_registry: None) -> None:
    cls = _make_fake_adapter("Beta")
    assert register(cls) is cls


def test_register_is_idempotent(clean_registry: None) -> None:
    cls = _make_fake_adapter("Gamma")
    register(cls)
    register(cls)  # no error — same class re-registered
    assert get("Gamma") is cls


def test_register_duplicate_name_different_class_raises(clean_registry: None) -> None:
    cls1 = _make_fake_adapter("Delta")
    cls2 = _make_fake_adapter("Delta")
    register(cls1)
    with pytest.raises(ValueError, match="already registered"):
        register(cls2)


def test_register_empty_name_raises(clean_registry: None) -> None:
    class Anonymous(Adapter):
        # name not set
        @property
        def schema(self):  # pragma: no cover
            ...

        def inclusion_criteria(self, q):  # pragma: no cover
            ...

        def exclusion_criteria(self, q):  # pragma: no cover
            ...

        def appraise(self, r, q):  # pragma: no cover
            ...

        def render_report(self, s):  # pragma: no cover
            ...

    with pytest.raises(ValueError, match=r"empty \.name"):
        register(Anonymous)


def test_get_unknown_raises(clean_registry: None) -> None:
    with pytest.raises(KeyError, match="No adapter registered"):
        get("nonexistent")


def test_names_returns_sorted_tuple(clean_registry: None) -> None:
    register(_make_fake_adapter("Zeta"))
    register(_make_fake_adapter("Alpha"))
    register(_make_fake_adapter("Mu"))
    assert names() == ("Alpha", "Mu", "Zeta")

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
    RenderContext,
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

        def render_report(self, state, context=None):
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

        def render_report(self, s, context=None):  # pragma: no cover
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


# ---------------------------------------------------------------------------
# RenderContext Protocol marker + Adapter.render_report(state, context=...) —
# Stufe-1.8 Inkrement 1 (U-1.8-B / Weg B)
# ---------------------------------------------------------------------------


def test_render_context_is_runtime_checkable_protocol() -> None:
    """``isinstance(obj, RenderContext)`` must work (decorated runtime_checkable)."""

    class Anything:
        pass

    # Does not raise TypeError — Protocol is runtime-checkable.
    assert isinstance(Anything(), RenderContext) is True


def test_render_context_is_empty_marker() -> None:
    """RenderContext has no required members — any object satisfies it.

    The marker exists solely as a typed slot in the ``render_report``
    signature; concrete adapters define their own context types
    (e.g. ``MPCORenderContext``) without inheriting from this Protocol.
    """

    class TotallyUnrelated:
        x = 1

    class AnotherShape:
        def foo(self) -> None: ...

    assert isinstance(TotallyUnrelated(), RenderContext) is True
    assert isinstance(AnotherShape(), RenderContext) is True
    assert isinstance(object(), RenderContext) is True


def test_adapter_render_report_default_context_is_none(clean_registry: None) -> None:
    """Calling ``render_report(state)`` without context must work (default ``None``).

    Backward-compat guarantee for Stufe-1.7 callers: the ABC's new
    ``context`` parameter has a default of ``None``, so any code that
    still calls the one-arg form continues to function.
    """
    cls = _make_fake_adapter("WithDefault")
    adapter = cls()
    # SessionState is a Protocol; the fake stub ignores it.
    artefact = adapter.render_report(state=object())  # type: ignore[arg-type]
    assert isinstance(artefact, ReportArtefact)
    assert artefact.format == "markdown"


def test_adapter_render_report_accepts_explicit_context(clean_registry: None) -> None:
    """Adapters that ignore ``context`` must still accept it (Liskov).

    The MPCO Stufe-1.7 renderer ignores the context; later increments
    will consume it. The ABC signature with ``context: RenderContext |
    None = None`` allows callers to pass any context object without
    breaking adapters that don't care.
    """

    class _Ctx:
        """Anything goes — RenderContext is an empty marker."""

    cls = _make_fake_adapter("AcceptsCtx")
    adapter = cls()
    artefact = adapter.render_report(
        state=object(),  # type: ignore[arg-type]
        context=_Ctx(),
    )
    assert isinstance(artefact, ReportArtefact)

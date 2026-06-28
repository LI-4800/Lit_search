# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.adapter (MPCOAdapter)."""

from __future__ import annotations

from typing import Any

import pytest

from ring2.adapters.mpco.adapter import MPCOAdapter
from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.exclusion_codes import ExclusionCode
from ring2.adapters.mpco.schema import (
    ApplicableRegulation,
    Comparator,
    Material,
    MPCOClaim,
    MPCOSchemaDefinition,
    Outcome,
    Property,
)
from ring2.adapters.mpco.table_mapping import CellRef
from ring2.core.adapter_base import (
    AppraisalDecision,
    AppraisalOutcome,
    PubMedRecord,
    ReportArtefact,
    Schema,
    get,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_claim(
    *,
    applicable_regulation: ApplicableRegulation = "none",
    claim_type: ClaimType = ClaimType.UNKNOWN,
    claim_id: str = "TEST-001",
) -> MPCOClaim:
    """Build a minimally valid MPCOClaim for tests."""
    return MPCOClaim(
        claim_id=claim_id,
        source_table_cell=CellRef(workbook="test.xlsx", sheet="Test", row=1, column_label="A"),
        material=Material(description="test material"),
        property=Property(description="test property"),
        comparator=Comparator(description="test comparator"),
        outcome=Outcome(description="test outcome"),
        applicable_regulation=applicable_regulation,
        claim_type=claim_type,
    )


def _make_record(*, with_abstract: bool = True) -> PubMedRecord:
    return PubMedRecord(
        pmid="12345",
        title="A study of bovine collagen biocompatibility",
        abstract="Abstract body." if with_abstract else None,
    )


class _FakeScreenerCaller:
    """Fake ScreenerCaller — returns pre-baked responses, records calls."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def assess(
        self,
        *,
        record_view: dict[str, Any],
        inclusion: list[dict[str, str]],
        exclusion: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.calls.append(
            {"record_view": record_view, "inclusion": inclusion, "exclusion": exclusion}
        )
        if not self._responses:
            raise AssertionError("FakeScreenerCaller out of pre-baked responses")
        return self._responses.pop(0)


# ---------------------------------------------------------------------------
# Class-level contract
# ---------------------------------------------------------------------------


def test_name_is_mpco() -> None:
    assert MPCOAdapter.name == "MPCO"


def test_schema_returns_mpco_schema_definition() -> None:
    adapter = MPCOAdapter()
    assert isinstance(adapter.schema, MPCOSchemaDefinition)


def test_schema_satisfies_schema_protocol() -> None:
    """The adapter's schema must satisfy the core Schema Protocol."""
    adapter = MPCOAdapter()
    assert isinstance(adapter.schema, Schema)


# ---------------------------------------------------------------------------
# Registry round-trip — @register decorator was applied at module import
# ---------------------------------------------------------------------------


def test_registry_resolves_mpco_to_class() -> None:
    """get('MPCO') must return the MPCOAdapter class."""
    assert get("MPCO") is MPCOAdapter


def test_registered_class_instantiable_zero_arg() -> None:
    """The registered class must be instantiable with no args (default caller)."""
    cls = get("MPCO")
    instance = cls()
    assert isinstance(instance, MPCOAdapter)


# ---------------------------------------------------------------------------
# inclusion_criteria — Decision #32 gating
# ---------------------------------------------------------------------------


def test_inclusion_none_regulation_returns_base_only() -> None:
    adapter = MPCOAdapter()
    claim = _make_claim(applicable_regulation="none", claim_type=ClaimType.UNKNOWN)
    inc = adapter.inclusion_criteria(claim)
    assert len(inc.criteria) == 1
    assert inc.criteria[0].id == "INC-001"


def test_inclusion_722_regulatory_compliance_has_four_annex_i() -> None:
    """REGULATORY_COMPLIANCE under 722/2012: base + all 4 Annex-I criteria."""
    adapter = MPCOAdapter()
    claim = _make_claim(
        applicable_regulation="722_2012",
        claim_type=ClaimType.REGULATORY_COMPLIANCE,
    )
    inc = adapter.inclusion_criteria(claim)
    ids = [c.id for c in inc.criteria]
    assert ids == [
        "INC-001",
        "INC-722-GEOGRAPHIC-ORIGIN",
        "INC-722-TSE-RISK-ASSESSMENT",
        "INC-722-INACTIVATION-PROCEDURE",
        "INC-722-TRACEABILITY",
    ]


def test_inclusion_722_biochemistry_has_two_annex_i() -> None:
    """BIOCHEMISTRY under 722/2012: base + TSE + inactivation only."""
    adapter = MPCOAdapter()
    claim = _make_claim(
        applicable_regulation="722_2012",
        claim_type=ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY,
    )
    inc = adapter.inclusion_criteria(claim)
    ids = [c.id for c in inc.criteria]
    assert ids == ["INC-001", "INC-722-TSE-RISK-ASSESSMENT", "INC-722-INACTIVATION-PROCEDURE"]


def test_inclusion_722_clinical_performance_has_base_only() -> None:
    """CLINICAL_PERFORMANCE under 722/2012: empty Annex-I scope → base only."""
    adapter = MPCOAdapter()
    claim = _make_claim(
        applicable_regulation="722_2012",
        claim_type=ClaimType.CLINICAL_PERFORMANCE,
    )
    inc = adapter.inclusion_criteria(claim)
    assert len(inc.criteria) == 1
    assert inc.criteria[0].id == "INC-001"


def test_inclusion_other_regulation_no_annex_enrichment() -> None:
    """Decision #32: applicable_regulation='other' must NOT trigger Annex-I enrichment."""
    adapter = MPCOAdapter()
    claim = _make_claim(
        applicable_regulation="other",
        claim_type=ClaimType.REGULATORY_COMPLIANCE,  # would max out under 722/2012
    )
    inc = adapter.inclusion_criteria(claim)
    assert len(inc.criteria) == 1
    assert inc.criteria[0].id == "INC-001"


def test_inclusion_annex_i_descriptions_contain_verbatim_anchor() -> None:
    """Inclusion descriptions must carry the verbatim 722/2012 anchor string."""
    adapter = MPCOAdapter()
    claim = _make_claim(
        applicable_regulation="722_2012",
        claim_type=ClaimType.REGULATORY_COMPLIANCE,
    )
    inc = adapter.inclusion_criteria(claim)
    annex_criteria = [c for c in inc.criteria if c.id.startswith("INC-722-")]
    assert annex_criteria, "Expected at least one INC-722-* criterion"
    for c in annex_criteria:
        assert "Regulation (EU) No 722/2012, Annex I" in c.description, (
            f"{c.id} missing verbatim anchor"
        )


def test_inclusion_raises_on_non_mpco_claim() -> None:
    """A non-MPCOClaim must be rejected with TypeError."""

    class _BogusQuestion:
        @property
        def claim_id(self) -> str:
            return "BOGUS"

    adapter = MPCOAdapter()
    with pytest.raises(TypeError, match=r"MPCOAdapter\.inclusion_criteria"):
        adapter.inclusion_criteria(_BogusQuestion())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# exclusion_criteria
# ---------------------------------------------------------------------------


def test_exclusion_returns_all_five_codes() -> None:
    adapter = MPCOAdapter()
    claim = _make_claim()
    exc = adapter.exclusion_criteria(claim)
    codes = {c.code for c in exc.criteria}
    assert codes == {code.value for code in ExclusionCode}


def test_exclusion_descriptions_non_empty() -> None:
    adapter = MPCOAdapter()
    claim = _make_claim()
    exc = adapter.exclusion_criteria(claim)
    for c in exc.criteria:
        assert c.description.strip(), f"{c.code} description is empty"


def test_exclusion_raises_on_non_mpco_claim() -> None:
    class _BogusQuestion:
        @property
        def claim_id(self) -> str:
            return "BOGUS"

    adapter = MPCOAdapter()
    with pytest.raises(TypeError, match=r"MPCOAdapter\.exclusion_criteria"):
        adapter.exclusion_criteria(_BogusQuestion())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# appraise — delegating to screen_record with screening-phase filter
# ---------------------------------------------------------------------------


def test_appraise_include_happy_path() -> None:
    """A clean INCLUDE flows through both passes and returns AppraisalDecision."""
    caller = _FakeScreenerCaller(
        [
            {"outcome": "include", "rationale": "ok title", "confidence": 0.95},
            {"outcome": "include", "rationale": "ok abstract", "confidence": 0.95},
        ]
    )
    adapter = MPCOAdapter(caller=caller)
    claim = _make_claim()
    record = _make_record(with_abstract=True)

    decision = adapter.appraise(record, claim)

    assert isinstance(decision, AppraisalDecision)
    assert decision.outcome is AppraisalOutcome.INCLUDE
    assert decision.exclusion_code is None
    assert decision.pmid == record.pmid


def test_appraise_exclude_with_screening_code() -> None:
    """High-confidence EXCLUDE at title pass with screening code short-circuits Pass 2."""
    caller = _FakeScreenerCaller(
        [
            {
                "outcome": "exclude",
                "exclusion_code": "EX-LANGUAGE",
                "rationale": "non-English",
                "confidence": 0.95,
            }
        ]
    )
    adapter = MPCOAdapter(caller=caller)
    claim = _make_claim()
    record = _make_record(with_abstract=True)

    decision = adapter.appraise(record, claim)

    assert decision.outcome is AppraisalOutcome.EXCLUDE
    assert decision.exclusion_code == "EX-LANGUAGE"
    # Only Pass 1 should have run (high-confidence title-only exclude).
    assert len(caller.calls) == 1


def test_appraise_passes_only_screening_codes_to_caller() -> None:
    """The exclusion payload visible to the LLM must contain only screening-phase codes.

    Verifies the Decision #29/#32 filter: deduplication and eligibility
    codes (EX-DUPLICATE, EX-NO-FULLTEXT, EX-A6-CATALOG) must be
    suppressed from the caller's payload at the screening step.
    """
    caller = _FakeScreenerCaller(
        [
            {"outcome": "include", "rationale": "ok", "confidence": 0.95},
            {"outcome": "include", "rationale": "ok", "confidence": 0.95},
        ]
    )
    adapter = MPCOAdapter(caller=caller)
    claim = _make_claim()
    record = _make_record(with_abstract=True)

    adapter.appraise(record, claim)

    assert caller.calls, "Caller was never invoked"
    exclusion_codes_seen = {entry["code"] for entry in caller.calls[0]["exclusion"]}
    assert exclusion_codes_seen == {"EX-LANGUAGE", "EX-IRRELEVANT"}, (
        f"Expected only screening codes; got {exclusion_codes_seen}"
    )


def test_appraise_caller_emitting_eligibility_code_is_rejected() -> None:
    """If the screener tries to emit EX-A6-CATALOG (eligibility-phase), the
    screen_record validation must reject it as an unknown code at this
    phase. This is the safety property the screening-phase filter buys."""
    caller = _FakeScreenerCaller(
        [
            {
                "outcome": "exclude",
                "exclusion_code": "EX-A6-CATALOG",
                "rationale": "§A6 violation",
                "confidence": 0.95,
            }
        ]
    )
    adapter = MPCOAdapter(caller=caller)
    claim = _make_claim()
    record = _make_record(with_abstract=True)

    with pytest.raises(ValueError, match="unknown exclusion_code"):
        adapter.appraise(record, claim)


def test_appraise_zero_arg_adapter_raises_on_use() -> None:
    """An adapter constructed with no caller must fail loudly when appraise is called."""
    adapter = MPCOAdapter()  # NullScreenerCaller default
    claim = _make_claim()
    record = _make_record(with_abstract=True)

    with pytest.raises(RuntimeError, match="MPCOAdapter constructed without ScreenerCaller"):
        adapter.appraise(record, claim)


def test_appraise_raises_on_non_mpco_claim() -> None:
    class _BogusQuestion:
        @property
        def claim_id(self) -> str:
            return "BOGUS"

    caller = _FakeScreenerCaller([])
    adapter = MPCOAdapter(caller=caller)
    record = _make_record()

    with pytest.raises(TypeError, match=r"MPCOAdapter\.appraise"):
        adapter.appraise(record, _BogusQuestion())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# render_report — Stufe 1.7 interim renderer (delegates to report_renderer)
# ---------------------------------------------------------------------------


def test_render_report_delegates_to_renderer(tmp_path: Any) -> None:
    """Stufe 1.7 contract: render_report returns the interim markdown report.

    The full content/section behaviour is covered by the renderer's own
    test module; this test only asserts the adapter delegation contract
    (format, key markers present).
    """
    from ring2.adapters.mpco.report_renderer import STATUS_BANNER
    from ring2.core.session import SessionStateImpl

    adapter = MPCOAdapter()
    state = SessionStateImpl(
        project_id="TEST-PROJ",
        claim_id="TEST-001",
        session_dir=tmp_path,
    )
    artefact = adapter.render_report(state)
    assert isinstance(artefact, ReportArtefact)
    assert artefact.format == "markdown"
    assert artefact.content is not None
    # Status banner present — confirms the interim contract is in force.
    assert STATUS_BANNER in artefact.content
    # Identifiers from state flow through.
    assert "TEST-PROJ" in artefact.content
    assert "TEST-001" in artefact.content


def test_render_report_accepts_optional_context_param(tmp_path: Any) -> None:
    """Stufe-1.8 Inkrement 1 contract: the new ``context`` parameter is accepted.

    The MPCOAdapter currently ignores ``context`` (full pass-through to
    the renderer follows in Stufe-1.8 Inkrement 5/6). Until then, the
    signature must still accept both ``context=None`` and an arbitrary
    object, and produce the same interim report in both cases — same
    output as the no-context call.
    """
    from ring2.core.session import SessionStateImpl

    adapter = MPCOAdapter()
    state = SessionStateImpl(
        project_id="TEST-PROJ",
        claim_id="TEST-001",
        session_dir=tmp_path,
    )

    class _DummyContext:
        """Stand-in for a future MPCORenderContext — empty marker satisfies the Protocol."""

    baseline = adapter.render_report(state)
    with_none = adapter.render_report(state, context=None)
    with_object = adapter.render_report(state, context=_DummyContext())

    # All three calls must succeed and produce equivalent content.
    assert with_none.content == baseline.content
    assert with_object.content == baseline.content

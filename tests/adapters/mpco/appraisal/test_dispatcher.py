# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for :mod:`ring2.adapters.mpco.appraisal.dispatcher`."""

from __future__ import annotations

from typing import ClassVar

import pytest

from ring2.adapters.mpco.appraisal.base import AppraisalLens, AppraisalResult
from ring2.adapters.mpco.appraisal.dispatcher import (
    AppraisalDispatcher,
    PendingAppraisalResult,
)
from ring2.adapters.mpco.appraisal.meddev_a6 import MeddevA6Lens
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
from ring2.core.project_config import AppraisalConfig, AppraisalLensSelection

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cell_ref() -> CellRef:
    return CellRef(
        workbook="Comparator-Tables.xlsx",
        sheet="Bovine-Collagen",
        row=4,
        column_label="Pepsin",
    )


def _make_claim(
    claim_type: ClaimType = ClaimType.CLINICAL_PERFORMANCE,
) -> MPCOClaim:
    return MPCOClaim(
        claim_id="CB-bov-01",
        source_table_cell=_cell_ref(),
        material=Material(description="Bovine-derived collagen"),
        property=Property(description="Biocompatibility"),
        comparator=Comparator(description="Porcine collagen"),
        outcome=Outcome(description="Inflammatory response"),
        applicable_regulation="722_2012",
        claim_type=claim_type,
    )


def _make_record(pmid: str = "12345") -> PubMedRecord:
    return PubMedRecord(pmid=pmid, title="Some title", abstract="An abstract.")


def _default_appraisal_config() -> AppraisalConfig:
    return AppraisalConfig(
        biochemistry_material_property=AppraisalLensSelection(lens="glp_oecd"),
        safety_allergenicity=AppraisalLensSelection(lens="care_caseseries"),
        clinical_performance=AppraisalLensSelection(lens="meddev_a6"),
        historical_market_use=AppraisalLensSelection(lens="registry_authoritativeness"),
    )


class _OperationalFakeLens(AppraisalLens):
    """Test-only lens that is genuinely operational and produces real results."""

    name: ClassVar[str] = "operational_fake"
    applicable_claim_types: ClassVar[frozenset[ClaimType]] = frozenset(
        {ClaimType.CLINICAL_PERFORMANCE}
    )

    def appraise(self, record: PubMedRecord, claim: MPCOClaim) -> AppraisalResult:
        return AppraisalResult(
            pmid=record.pmid,
            lens_name=self.name,
            rationale="fake operational lens — always qualifies for tests",
            qualifies=True,
        )

    def render_summary(self, results: tuple[AppraisalResult, ...]) -> str:
        return f"## operational_fake — {len(results)} record(s)\n"

    def is_operational(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# PendingAppraisalResult schema
# ---------------------------------------------------------------------------


class TestPendingAppraisalResult:
    def test_is_appraisal_result_subclass(self) -> None:
        assert issubclass(PendingAppraisalResult, AppraisalResult)

    def test_minimal_construction(self) -> None:
        r = PendingAppraisalResult(
            pmid="123",
            lens_name="meddev_a6",
            rationale="awaiting classifier",
            qualifies=False,
        )
        assert isinstance(r, AppraisalResult)
        assert isinstance(r, PendingAppraisalResult)
        assert r.pmid == "123"
        assert r.qualifies is False

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        r = PendingAppraisalResult(pmid="1", lens_name="x", rationale="r", qualifies=False)
        with pytest.raises(ValidationError):
            r.pmid = "2"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AppraisalDispatcher — construction
# ---------------------------------------------------------------------------


class TestDispatcherConstruction:
    def test_stores_config(self) -> None:
        cfg = _default_appraisal_config()
        d = AppraisalDispatcher(cfg)
        assert d.config is cfg

    def test_default_lens_factory_resolves_via_registry(self) -> None:
        d = AppraisalDispatcher(_default_appraisal_config())
        # Default factory should yield a MeddevA6Lens when asked for "meddev_a6".
        lens = d.lens_factory("meddev_a6")
        assert isinstance(lens, MeddevA6Lens)

    def test_custom_lens_factory_overrides_default(self) -> None:
        captured: list[str] = []

        def custom_factory(name: str) -> AppraisalLens:
            captured.append(name)
            return _OperationalFakeLens()

        d = AppraisalDispatcher(_default_appraisal_config(), lens_factory=custom_factory)
        assert d.lens_factory is custom_factory
        # Sanity: factory really intercepts the call.
        lens = d.lens_factory("anything")
        assert isinstance(lens, _OperationalFakeLens)
        assert captured == ["anything"]


# ---------------------------------------------------------------------------
# AppraisalDispatcher.dispatch — claim-type routing
# ---------------------------------------------------------------------------


class TestDispatcherRouting:
    def test_regulatory_compliance_returns_empty(self) -> None:
        d = AppraisalDispatcher(_default_appraisal_config())
        out = d.dispatch(
            _make_claim(ClaimType.REGULATORY_COMPLIANCE),
            [_make_record("1"), _make_record("2")],
        )
        assert out == {}

    def test_unknown_returns_empty(self) -> None:
        d = AppraisalDispatcher(_default_appraisal_config())
        out = d.dispatch(_make_claim(ClaimType.UNKNOWN), [_make_record("1")])
        assert out == {}

    def test_clinical_performance_routes_to_meddev_a6(self) -> None:
        captured: list[str] = []

        def custom_factory(name: str) -> AppraisalLens:
            captured.append(name)
            return _OperationalFakeLens()

        d = AppraisalDispatcher(_default_appraisal_config(), lens_factory=custom_factory)
        d.dispatch(_make_claim(ClaimType.CLINICAL_PERFORMANCE), [_make_record("1")])

        assert captured == ["meddev_a6"]

    def test_biochemistry_material_property_routes_to_glp_oecd(self) -> None:
        captured: list[str] = []

        def custom_factory(name: str) -> AppraisalLens:
            captured.append(name)
            return _OperationalFakeLens()

        d = AppraisalDispatcher(_default_appraisal_config(), lens_factory=custom_factory)
        d.dispatch(
            _make_claim(ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY),
            [_make_record("1")],
        )

        assert captured == ["glp_oecd"]


# ---------------------------------------------------------------------------
# AppraisalDispatcher.dispatch — operational lens
# ---------------------------------------------------------------------------


class TestDispatcherOperationalLens:
    def test_operational_lens_returns_real_results(self) -> None:
        d = AppraisalDispatcher(
            _default_appraisal_config(),
            lens_factory=lambda _name: _OperationalFakeLens(),
        )
        records = [_make_record("11"), _make_record("22"), _make_record("33")]

        out = d.dispatch(_make_claim(ClaimType.CLINICAL_PERFORMANCE), records)

        assert set(out.keys()) == {ClaimType.CLINICAL_PERFORMANCE}
        results = out[ClaimType.CLINICAL_PERFORMANCE]
        assert len(results) == 3
        assert all(not isinstance(r, PendingAppraisalResult) for r in results)
        assert [r.pmid for r in results] == ["11", "22", "33"]
        assert all(r.qualifies for r in results)
        assert all(r.lens_name == "operational_fake" for r in results)

    def test_operational_lens_with_no_records(self) -> None:
        d = AppraisalDispatcher(
            _default_appraisal_config(),
            lens_factory=lambda _name: _OperationalFakeLens(),
        )
        out = d.dispatch(_make_claim(ClaimType.CLINICAL_PERFORMANCE), [])

        assert out == {ClaimType.CLINICAL_PERFORMANCE: []}


# ---------------------------------------------------------------------------
# AppraisalDispatcher.dispatch — non-operational lens
# ---------------------------------------------------------------------------


class TestDispatcherNonOperationalLens:
    def test_non_operational_lens_emits_pending_per_record(self) -> None:
        # Default factory + meddev_a6 (default classifier = NullA6Classifier
        # → not operational).
        d = AppraisalDispatcher(_default_appraisal_config())
        records = [_make_record("11"), _make_record("22"), _make_record("33")]

        out = d.dispatch(_make_claim(ClaimType.CLINICAL_PERFORMANCE), records)

        results = out[ClaimType.CLINICAL_PERFORMANCE]
        assert len(results) == 3
        assert all(isinstance(r, PendingAppraisalResult) for r in results)
        assert [r.pmid for r in results] == ["11", "22", "33"]
        assert all(r.lens_name == "meddev_a6" for r in results)
        assert all(r.qualifies is False for r in results)
        # The rationale must convey "not operational" so the renderer
        # can detect / display the pending state.
        for r in results:
            assert "not operational" in r.rationale.lower()

    def test_non_operational_lens_with_no_records(self) -> None:
        d = AppraisalDispatcher(_default_appraisal_config())
        out = d.dispatch(_make_claim(ClaimType.CLINICAL_PERFORMANCE), [])

        assert out == {ClaimType.CLINICAL_PERFORMANCE: []}

    def test_stub_lens_is_non_operational_and_emits_pending(self) -> None:
        # historical_market_use is configured to registry_authoritativeness,
        # which is a stub → default is_operational() = False → pending results.
        d = AppraisalDispatcher(_default_appraisal_config())
        out = d.dispatch(
            _make_claim(ClaimType.HISTORICAL_MARKET_USE),
            [_make_record("99")],
        )

        results = out[ClaimType.HISTORICAL_MARKET_USE]
        assert len(results) == 1
        assert isinstance(results[0], PendingAppraisalResult)
        assert results[0].lens_name == "registry_authoritativeness"


# ---------------------------------------------------------------------------
# AppraisalDispatcher.dispatch — exception propagation
# ---------------------------------------------------------------------------


class TestDispatcherExceptionPropagation:
    def test_factory_exception_propagates(self) -> None:
        def bad_factory(name: str) -> AppraisalLens:
            raise RuntimeError(f"factory boom for {name}")

        d = AppraisalDispatcher(_default_appraisal_config(), lens_factory=bad_factory)
        with pytest.raises(RuntimeError, match="factory boom"):
            d.dispatch(_make_claim(ClaimType.CLINICAL_PERFORMANCE), [_make_record()])

    def test_operational_lens_appraise_exception_propagates(self) -> None:
        class _BrokenLens(_OperationalFakeLens):
            def appraise(self, record: PubMedRecord, claim: MPCOClaim) -> AppraisalResult:
                raise ValueError("appraise boom")

        d = AppraisalDispatcher(
            _default_appraisal_config(),
            lens_factory=lambda _name: _BrokenLens(),
        )
        with pytest.raises(ValueError, match="appraise boom"):
            d.dispatch(_make_claim(ClaimType.CLINICAL_PERFORMANCE), [_make_record()])

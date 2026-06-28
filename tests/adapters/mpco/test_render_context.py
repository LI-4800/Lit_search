# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.render_context."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from ring2.adapters.mpco.decision_persistence import ScreeningDecision
from ring2.adapters.mpco.exclusion_codes import ExclusionCode, PrismaPhase
from ring2.adapters.mpco.render_context import MPCORenderContext
from ring2.adapters.mpco.schema import (
    Comparator,
    Material,
    MPCOClaim,
    Outcome,
    Property,
)
from ring2.adapters.mpco.table_mapping import CellRef
from ring2.core.adapter_base import RenderContext
from ring2.core.prisma import PrismaFlow, PrismaPhaseCounts

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


_UTC_NOW = datetime(2026, 6, 27, 14, 23, 0, tzinfo=UTC)


def _cell_ref() -> CellRef:
    return CellRef(
        workbook="Comparator-Tables.xlsx",
        sheet="Bovine-Collagen",
        row=4,
        column_label="Pepsin",
    )


def _claim(claim_id: str = "CB-bov-01") -> MPCOClaim:
    return MPCOClaim(
        claim_id=claim_id,
        source_table_cell=_cell_ref(),
        material=Material(description="Bovine-derived collagen extracted via porcine pepsin"),
        property=Property(description="Biocompatibility and resorption kinetics"),
        comparator=Comparator(description="Porcine-derived collagen; synthetic PLGA"),
        outcome=Outcome(description="Inflammatory response; resorption time"),
        applicable_regulation="722_2012",
    )


def _flow(claim_id: str = "CB-bov-01", project_id: str = "722-Retro") -> PrismaFlow:
    counts = PrismaPhaseCounts(
        identified_database=100,
        identified_other=0,
        duplicates_removed=5,
        excluded_screening={"EX-IRRELEVANT": 60, "EX-LANGUAGE": 5},
        excluded_eligibility={"EX-A6-CATALOG": 20, "EX-NO-FULLTEXT": 2},
    )
    return PrismaFlow(
        counts=counts,
        project_id=project_id,
        claim_id=claim_id,
        generated_at="2026-06-27T14:23:00Z",
    )


def _decision(
    pmid: str = "12345678",
    phase: PrismaPhase = PrismaPhase.SCREENING,
) -> ScreeningDecision:
    return ScreeningDecision(
        pmid=pmid,
        phase=phase,
        outcome="include",
        exclusion_code=None,
        rationale="subject device class; on topic",
        decided_at=_UTC_NOW,
        decided_by="screener:claude-sonnet-4-6",
    )


# ---------------------------------------------------------------------------
# 1-3: Construction and basic Pydantic config
# ---------------------------------------------------------------------------


def test_mpco_render_context_construction_valid() -> None:
    """Happy path: all three fields wire together, attributes readable."""
    ctx = MPCORenderContext(
        claim=_claim(),
        decisions=(_decision(),),
        flow=_flow(),
    )
    assert ctx.claim.claim_id == "CB-bov-01"
    assert len(ctx.decisions) == 1
    assert ctx.decisions[0].pmid == "12345678"
    assert ctx.flow.claim_id == "CB-bov-01"
    assert ctx.flow.counts.total_identified == 100


def test_mpco_render_context_is_frozen() -> None:
    """frozen=True — field assignment raises ValidationError."""
    ctx = MPCORenderContext(claim=_claim(), flow=_flow())
    with pytest.raises(ValidationError):
        ctx.claim = _claim(claim_id="OTHER")  # type: ignore[misc]


def test_mpco_render_context_extra_fields_forbidden() -> None:
    """extra='forbid' — unknown field raises ValidationError."""
    with pytest.raises(ValidationError):
        MPCORenderContext(
            claim=_claim(),
            flow=_flow(),
            unexpected_field="boom",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# 4: Cross-validator C1
# ---------------------------------------------------------------------------


def test_mpco_render_context_claim_id_mismatch_raises() -> None:
    """C1: claim.claim_id and flow.claim_id must match."""
    with pytest.raises(ValidationError, match=r"inconsistent claim/flow"):
        MPCORenderContext(
            claim=_claim(claim_id="CB-bov-01"),
            flow=_flow(claim_id="OTHER-CLAIM"),
        )


# ---------------------------------------------------------------------------
# 5-6: decisions field defaults and ordering
# ---------------------------------------------------------------------------


def test_mpco_render_context_empty_decisions_default_ok() -> None:
    """decisions defaults to empty tuple — valid initial state."""
    ctx = MPCORenderContext(claim=_claim(), flow=_flow())
    assert ctx.decisions == ()


def test_mpco_render_context_preserves_decisions_order_and_type() -> None:
    """An input list is coerced to a tuple; order is preserved."""
    d1 = _decision(pmid="11111111", phase=PrismaPhase.SCREENING)
    d2 = _decision(pmid="22222222", phase=PrismaPhase.SCREENING)
    d3 = ScreeningDecision(
        pmid="33333333",
        phase=PrismaPhase.ELIGIBILITY,
        outcome="exclude",
        exclusion_code=ExclusionCode.A6_CATALOG,
        rationale="§A6(a): elementary aspects omitted",
        decided_at=_UTC_NOW,
        decided_by="reviewer:michael",
    )
    ctx = MPCORenderContext(
        claim=_claim(),
        decisions=[d1, d2, d3],  # type: ignore[arg-type]  # list → tuple coercion
        flow=_flow(),
    )
    assert isinstance(ctx.decisions, tuple)
    pmids = tuple(d.pmid for d in ctx.decisions)
    assert pmids == ("11111111", "22222222", "33333333")


# ---------------------------------------------------------------------------
# 7: Structural conformance to core RenderContext Protocol
# ---------------------------------------------------------------------------


def test_mpco_render_context_satisfies_render_context_protocol() -> None:
    """MPCORenderContext satisfies the empty core RenderContext Protocol.

    The core marker is intentionally empty + runtime_checkable, so any
    object satisfies it. The explicit assertion here guards the contract:
    if a future change adds required members to RenderContext, this
    test catches the breakage at the right layer.
    """
    ctx: Any = MPCORenderContext(claim=_claim(), flow=_flow())
    assert isinstance(ctx, RenderContext) is True

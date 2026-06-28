# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.decision_persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from ring2.adapters.mpco.decision_persistence import (
    DecisionFile,
    ScreeningDecision,
    load_latest_decision_file,
    write_decision_file,
)
from ring2.adapters.mpco.exclusion_codes import ExclusionCode, PrismaPhase

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


_UTC_NOW = datetime(2026, 6, 27, 14, 23, 0, tzinfo=UTC)


def _include(
    pmid: str = "12345678", phase: PrismaPhase = PrismaPhase.SCREENING
) -> ScreeningDecision:
    """Build a valid include-decision."""
    return ScreeningDecision(
        pmid=pmid,
        phase=phase,
        outcome="include",
        exclusion_code=None,
        rationale="subject device class; on topic",
        decided_at=_UTC_NOW,
        decided_by="screener:claude-sonnet-4-6",
    )


def _exclude(
    pmid: str = "98765432",
    code: ExclusionCode = ExclusionCode.IRRELEVANT,
    phase: PrismaPhase = PrismaPhase.SCREENING,
) -> ScreeningDecision:
    """Build a valid exclude-decision with a phase-coherent code."""
    return ScreeningDecision(
        pmid=pmid,
        phase=phase,
        outcome="exclude",
        exclusion_code=code,
        rationale="off-topic per title/abstract",
        decided_at=_UTC_NOW,
        decided_by="screener:claude-sonnet-4-6",
    )


# ---------------------------------------------------------------------------
# ScreeningDecision — validators V1..V4
# ---------------------------------------------------------------------------


def test_screening_decision_minimal_include() -> None:
    """outcome=include with exclusion_code=None is valid."""
    d = _include()
    assert d.outcome == "include"
    assert d.exclusion_code is None
    assert d.phase is PrismaPhase.SCREENING


def test_screening_decision_minimal_exclude() -> None:
    """outcome=exclude with a phase-coherent code is valid."""
    d = _exclude(code=ExclusionCode.IRRELEVANT, phase=PrismaPhase.SCREENING)
    assert d.outcome == "exclude"
    assert d.exclusion_code is ExclusionCode.IRRELEVANT


def test_screening_decision_exclude_without_code_raises() -> None:
    """V1: outcome=exclude requires exclusion_code."""
    with pytest.raises(ValidationError, match="exclusion_code"):
        ScreeningDecision(
            pmid="1",
            phase=PrismaPhase.SCREENING,
            outcome="exclude",
            exclusion_code=None,
            rationale="...",
            decided_at=_UTC_NOW,
            decided_by="x",
        )


def test_screening_decision_include_with_code_raises() -> None:
    """V2: outcome=include forbids exclusion_code."""
    with pytest.raises(ValidationError, match="forbids exclusion_code"):
        ScreeningDecision(
            pmid="1",
            phase=PrismaPhase.SCREENING,
            outcome="include",
            exclusion_code=ExclusionCode.IRRELEVANT,
            rationale="...",
            decided_at=_UTC_NOW,
            decided_by="x",
        )


def test_screening_decision_phase_code_mismatch_raises() -> None:
    """V3: A6_CATALOG is routed to ELIGIBILITY, not SCREENING."""
    with pytest.raises(ValidationError, match=r"routed to phase 'eligibility'"):
        ScreeningDecision(
            pmid="1",
            phase=PrismaPhase.SCREENING,  # wrong — A6 belongs at eligibility
            outcome="exclude",
            exclusion_code=ExclusionCode.A6_CATALOG,
            rationale="...",
            decided_at=_UTC_NOW,
            decided_by="x",
        )


def test_screening_decision_is_frozen() -> None:
    """frozen=True — assignment raises ValidationError."""
    d = _include()
    with pytest.raises(ValidationError):
        d.pmid = "other"  # type: ignore[misc]


def test_screening_decision_naive_datetime_raises() -> None:
    """V4: naive datetime is rejected."""
    naive = datetime(2026, 6, 27, 14, 23, 0)  # no tzinfo
    with pytest.raises(ValidationError, match="naive datetime"):
        ScreeningDecision(
            pmid="1",
            phase=PrismaPhase.SCREENING,
            outcome="include",
            exclusion_code=None,
            rationale="...",
            decided_at=naive,
            decided_by="x",
        )


def test_screening_decision_non_utc_tz_raises() -> None:
    """V4: tz-aware but non-UTC is rejected."""
    plus_two = timezone(timedelta(hours=2))
    not_utc = datetime(2026, 6, 27, 14, 23, 0, tzinfo=plus_two)
    with pytest.raises(ValidationError, match=r"must be UTC"):
        ScreeningDecision(
            pmid="1",
            phase=PrismaPhase.SCREENING,
            outcome="include",
            exclusion_code=None,
            rationale="...",
            decided_at=not_utc,
            decided_by="x",
        )


# ---------------------------------------------------------------------------
# DecisionFile — validator V5 and schema_version lock
# ---------------------------------------------------------------------------


def test_decision_file_minimal_valid() -> None:
    """schema_version, claim_id, empty decisions tuple — valid."""
    df = DecisionFile(schema_version="1.0", claim_id="CB-bov-01", decisions=())
    assert df.claim_id == "CB-bov-01"
    assert df.decisions == ()


def test_decision_file_duplicate_pmid_phase_raises() -> None:
    """V5: same (pmid, phase) twice is forbidden."""
    d1 = _exclude(pmid="42", code=ExclusionCode.IRRELEVANT, phase=PrismaPhase.SCREENING)
    d2 = _exclude(pmid="42", code=ExclusionCode.LANGUAGE, phase=PrismaPhase.SCREENING)
    with pytest.raises(ValidationError, match=r"duplicate \(pmid, phase\)"):
        DecisionFile(schema_version="1.0", claim_id="C", decisions=(d1, d2))


def test_decision_file_same_pmid_different_phases_ok() -> None:
    """Same PMID at SCREENING and ELIGIBILITY is allowed (real lifecycle)."""
    d_screen = _include(pmid="42", phase=PrismaPhase.SCREENING)
    d_elig = _exclude(
        pmid="42",
        code=ExclusionCode.A6_CATALOG,
        phase=PrismaPhase.ELIGIBILITY,
    )
    df = DecisionFile(schema_version="1.0", claim_id="C", decisions=(d_screen, d_elig))
    assert len(df.decisions) == 2


def test_decision_file_schema_version_locked_to_1_0() -> None:
    """Only schema_version='1.0' is currently accepted."""
    with pytest.raises(ValidationError):
        DecisionFile(schema_version="2.0", claim_id="C", decisions=())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# YAML I/O — write_decision_file + load_latest_decision_file
# ---------------------------------------------------------------------------


def test_write_creates_v1_then_v2(tmp_path: Path) -> None:
    """First write produces _v1.yaml; second produces _v2.yaml."""
    p1 = write_decision_file(tmp_path, "CB-bov-01", (_include(),))
    p2 = write_decision_file(tmp_path, "CB-bov-01", (_include(),))
    assert p1.name == "CB-bov-01_v1.yaml"
    assert p2.name == "CB-bov-01_v2.yaml"
    assert p1.exists()
    assert p2.exists()


def test_write_creates_decisions_subdir(tmp_path: Path) -> None:
    """The `decisions/` subdirectory is auto-created."""
    assert not (tmp_path / "decisions").exists()
    write_decision_file(tmp_path, "C", (_include(),))
    assert (tmp_path / "decisions").is_dir()


def test_write_does_not_collide_across_claim_ids(tmp_path: Path) -> None:
    """Version counter is per-claim, not per-directory."""
    p_a1 = write_decision_file(tmp_path, "CLAIM-A", (_include(),))
    p_b1 = write_decision_file(tmp_path, "CLAIM-B", (_include(),))
    p_a2 = write_decision_file(tmp_path, "CLAIM-A", (_include(),))
    assert p_a1.name == "CLAIM-A_v1.yaml"
    assert p_b1.name == "CLAIM-B_v1.yaml"
    assert p_a2.name == "CLAIM-A_v2.yaml"


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    """write → load yields a DecisionFile equal in content to the original."""
    decisions = (
        _include(pmid="11111111", phase=PrismaPhase.SCREENING),
        _exclude(
            pmid="22222222",
            code=ExclusionCode.A6_CATALOG,
            phase=PrismaPhase.ELIGIBILITY,
        ),
    )
    written = write_decision_file(tmp_path, "CB-bov-01", decisions)
    assert written.exists()
    loaded = load_latest_decision_file(tmp_path, "CB-bov-01")
    assert loaded is not None
    assert loaded.schema_version == "1.0"
    assert loaded.claim_id == "CB-bov-01"
    assert len(loaded.decisions) == 2
    # Field-by-field equality on the round-tripped decisions
    assert loaded.decisions[0].pmid == "11111111"
    assert loaded.decisions[0].outcome == "include"
    assert loaded.decisions[0].exclusion_code is None
    assert loaded.decisions[1].pmid == "22222222"
    assert loaded.decisions[1].outcome == "exclude"
    assert loaded.decisions[1].exclusion_code is ExclusionCode.A6_CATALOG
    assert loaded.decisions[1].phase is PrismaPhase.ELIGIBILITY
    assert loaded.decisions[1].decided_at == _UTC_NOW


def test_load_latest_returns_none_when_missing(tmp_path: Path) -> None:
    """No decisions/ dir at all → None."""
    assert load_latest_decision_file(tmp_path, "CB-bov-01") is None


def test_load_latest_returns_none_when_claim_absent(tmp_path: Path) -> None:
    """decisions/ exists but no file matches claim_id → None."""
    write_decision_file(tmp_path, "OTHER-CLAIM", (_include(),))
    assert load_latest_decision_file(tmp_path, "CB-bov-01") is None


def test_load_latest_returns_highest_version(tmp_path: Path) -> None:
    """With v1, v2, v3 on disk, v3 is returned."""
    write_decision_file(tmp_path, "C", (_include(pmid="A"),))
    write_decision_file(tmp_path, "C", (_include(pmid="B"),))
    write_decision_file(tmp_path, "C", (_include(pmid="C-LATEST"),))
    loaded = load_latest_decision_file(tmp_path, "C")
    assert loaded is not None
    assert len(loaded.decisions) == 1
    assert loaded.decisions[0].pmid == "C-LATEST"

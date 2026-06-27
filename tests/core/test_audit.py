# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.core.audit."""

from pathlib import Path

import pytest

from ring2.core.audit import (
    DEVIATIONS_FILENAME,
    EVENT_LOG_FILENAME,
    STRATEGY_LOG_FILENAME,
    AuditLog,
    DeviationEntry,
    EventLogEntry,
    StrategyProbeEntry,
    probe_entry_from_hit_count,
)
from ring2.core.persistence import load
from ring2.core.pubmed_client import HitCountResult

# ---------------------------------------------------------------------------
# Entry types
# ---------------------------------------------------------------------------


def test_strategy_probe_entry_roundtrip() -> None:
    entry = StrategyProbeEntry(
        timestamp="2026-06-27T10:00:00Z",
        claim_id="CB-bov-01",
        query="Collagen[Mesh]",
        total_count=141813,
        query_translation='"collagen"[MeSH Terms]',
        heat_bar="red",
        rationale="seed term too broad",
    )
    d = entry.to_yaml_dict()
    restored = StrategyProbeEntry.from_yaml_dict(d)
    assert restored == entry


def test_strategy_probe_omits_none_rationale_from_yaml() -> None:
    entry = StrategyProbeEntry(
        timestamp="t",
        claim_id="c",
        query="q",
        total_count=0,
        query_translation="",
        heat_bar="green",
    )
    d = entry.to_yaml_dict()
    assert "rationale" not in d


def test_deviation_entry_roundtrip() -> None:
    dev = DeviationEntry(
        id="DEV-722-001",
        title="Single-database search (PubMed only)",
        rationale="PubMed indexes MEDLINE...",
        mitigation="Forward + backward citation chasing",
        affects="MEDDEV §A4 expectation of multiple databases",
    )
    d = dev.to_yaml_dict()
    restored = DeviationEntry.from_yaml_dict(d)
    assert restored == dev


def test_event_log_entry_roundtrip_minimal() -> None:
    e = EventLogEntry(timestamp="t", event_type="search_started", claim_id=None)
    d = e.to_yaml_dict()
    restored = EventLogEntry.from_yaml_dict(d)
    assert restored == e


def test_event_log_entry_with_details() -> None:
    e = EventLogEntry(
        timestamp="t",
        event_type="batch_persisted",
        claim_id="X",
        details={"batch_num": 0, "record_count": 10},
    )
    d = e.to_yaml_dict()
    assert d["details"] == {"batch_num": 0, "record_count": 10}


# ---------------------------------------------------------------------------
# AuditLog — strategy probes
# ---------------------------------------------------------------------------


def test_audit_log_creates_session_dir(tmp_path: Path) -> None:
    target = tmp_path / "sessions" / "proj-X"
    AuditLog(target)
    assert target.exists() and target.is_dir()


def test_log_strategy_probe_writes_yaml(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    entry = StrategyProbeEntry(
        timestamp="2026-06-27T10:00:00Z",
        claim_id="CB-bov-01",
        query="Collagen[Mesh]",
        total_count=141813,
        query_translation='"collagen"[MeSH Terms]',
        heat_bar="red",
    )
    audit.log_strategy_probe(entry)
    assert audit.strategy_log_path.name == STRATEGY_LOG_FILENAME
    assert audit.strategy_log_path.exists()
    data = load(audit.strategy_log_path)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["claim_id"] == "CB-bov-01"
    assert data[0]["total_count"] == 141813


def test_log_strategy_probe_appends(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    for i, q in enumerate(["A", "B", "C"]):
        audit.log_strategy_probe(
            StrategyProbeEntry(
                timestamp=f"t{i}",
                claim_id="X",
                query=q,
                total_count=i,
                query_translation="",
                heat_bar="green",
            )
        )
    probes = audit.load_strategy_probes()
    assert [p.query for p in probes] == ["A", "B", "C"]


def test_load_strategy_probes_filter_by_claim(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    for cid in ["A", "B", "A", "C"]:
        audit.log_strategy_probe(
            StrategyProbeEntry(
                timestamp="t",
                claim_id=cid,
                query="q",
                total_count=0,
                query_translation="",
                heat_bar="green",
            )
        )
    a_only = audit.load_strategy_probes(claim_id="A")
    assert len(a_only) == 2
    assert all(p.claim_id == "A" for p in a_only)


def test_load_strategy_probes_empty_when_no_file(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    assert audit.load_strategy_probes() == []


# ---------------------------------------------------------------------------
# AuditLog — deviations
# ---------------------------------------------------------------------------


def test_register_deviation_writes_yaml(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    dev = DeviationEntry(
        id="DEV-722-001",
        title="PubMed only",
        rationale="r",
        mitigation="m",
    )
    audit.register_deviation(dev)
    assert audit.deviations_path.name == DEVIATIONS_FILENAME
    loaded = audit.load_deviations()
    assert loaded == [dev]


def test_register_deviation_duplicate_raises(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    dev = DeviationEntry(id="DEV-X", title="t", rationale="r")
    audit.register_deviation(dev)
    with pytest.raises(ValueError, match="already registered"):
        audit.register_deviation(dev)


def test_register_deviations_bulk_skips_existing(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    audit.register_deviation(DeviationEntry(id="A", title="t1", rationale="r1"))
    audit.register_deviations(
        [
            DeviationEntry(id="A", title="t1-different", rationale="r1-different"),
            DeviationEntry(id="B", title="t2", rationale="r2"),
        ]
    )
    loaded = audit.load_deviations()
    ids = {d.id for d in loaded}
    assert ids == {"A", "B"}
    # The first 'A' should be retained (no overwrite of existing id)
    a_entry = next(d for d in loaded if d.id == "A")
    assert a_entry.title == "t1"


def test_register_deviations_bulk_first_time(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    audit.register_deviations(
        [
            DeviationEntry(id="DEV-722-001", title="t1", rationale="r1"),
            DeviationEntry(id="DEV-722-002", title="t2", rationale="r2"),
        ]
    )
    assert len(audit.load_deviations()) == 2


# ---------------------------------------------------------------------------
# AuditLog — events
# ---------------------------------------------------------------------------


def test_log_event_writes_yaml(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    entry = audit.log_event("search_started", claim_id="CB-bov-01", query="X")
    assert audit.event_log_path.name == EVENT_LOG_FILENAME
    assert entry.event_type == "search_started"
    assert entry.claim_id == "CB-bov-01"
    assert entry.details == {"query": "X"}
    events = audit.load_events()
    assert len(events) == 1
    assert events[0].details == {"query": "X"}


def test_load_events_filter_by_type(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    audit.log_event("search_started")
    audit.log_event("batch_persisted", batch_num=0)
    audit.log_event("batch_persisted", batch_num=1)
    audit.log_event("search_completed")
    batches = audit.load_events(event_type="batch_persisted")
    assert len(batches) == 2
    assert all(e.event_type == "batch_persisted" for e in batches)


def test_load_events_empty_when_no_file(tmp_path: Path) -> None:
    assert AuditLog(tmp_path).load_events() == []


# ---------------------------------------------------------------------------
# Persistence preserves comments — regression
# ---------------------------------------------------------------------------


def test_strategy_log_preserves_manually_added_comments(tmp_path: Path) -> None:
    """A user (or future Claude) can hand-edit the YAML to add # UNKLAR
    notes; subsequent programmatic appends must preserve those comments.
    """
    audit = AuditLog(tmp_path)
    # First entry written programmatically
    audit.log_strategy_probe(
        StrategyProbeEntry(
            timestamp="t1",
            claim_id="X",
            query="q1",
            total_count=10,
            query_translation="",
            heat_bar="green",
            rationale="initial",
        )
    )
    # User edits the file to add a comment
    raw = audit.strategy_log_path.read_text(encoding="utf-8")
    audit.strategy_log_path.write_text(
        "# UNKLAR-X: this probe needs reviewer confirmation\n" + raw, encoding="utf-8"
    )
    # Programmatic append again
    audit.log_strategy_probe(
        StrategyProbeEntry(
            timestamp="t2",
            claim_id="X",
            query="q2",
            total_count=20,
            query_translation="",
            heat_bar="green",
        )
    )
    final = audit.strategy_log_path.read_text(encoding="utf-8")
    assert "# UNKLAR-X" in final
    assert "q1" in final
    assert "q2" in final


# ---------------------------------------------------------------------------
# probe_entry_from_hit_count — the bridge from pubmed_client to audit
# ---------------------------------------------------------------------------


def test_probe_entry_from_hit_count_default_heat_bar() -> None:
    hit = HitCountResult(
        timestamp="t",
        query="Collagen[Mesh]",
        total_count=141813,
        query_translation='"collagen"[MeSH Terms]',
        returned_count=1,
        has_more=True,
    )
    entry = probe_entry_from_hit_count("CB-bov-01", hit, rationale="too broad")
    assert entry.heat_bar == "red"
    assert entry.rationale == "too broad"
    assert entry.claim_id == "CB-bov-01"
    assert entry.total_count == 141813


def test_probe_entry_from_hit_count_explicit_heat_bar() -> None:
    hit = HitCountResult(
        timestamp="t",
        query="q",
        total_count=1_000_000,
        query_translation="",
        returned_count=1,
        has_more=True,
    )
    entry = probe_entry_from_hit_count("X", hit, heat_bar_value="custom")
    assert entry.heat_bar == "custom"

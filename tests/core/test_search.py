# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.core.search."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ring2.core.audit import AuditLog
from ring2.core.persistence import find_batches, load, save_batch
from ring2.core.pubmed_client import PubMedClient
from ring2.core.search import SearchOrchestrator, SearchRunResult
from ring2.core.session import RecordStatus

# ---------------------------------------------------------------------------
# Fake MCP caller
# ---------------------------------------------------------------------------


def _record(pmid: str) -> dict[str, Any]:
    return {
        "pmid": pmid,
        "title": f"Title {pmid}",
        "doi": f"10.0/{pmid}",
        "abstract": f"Abstract {pmid}",
        "journal": "Test Journal",
        "year": 2024,
        "authors": ["A. Author"],
        "publication_types": ["Journal Article"],
    }


@dataclass
class _FakeMCPCaller:
    """MCP caller that returns scripted pages keyed by retstart.

    Pages must be supplied as a list ordered by ``retstart``. Calls with
    a ``retstart`` past the end of the script return an empty page.
    The ``probe`` page (``max_results=1``) is taken from the first
    scripted page's ``total_count`` and ``query_translation``.
    """

    pages: list[dict[str, Any]]
    raise_at_retstart: int | None = None
    raise_exc: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def search_articles(
        self,
        query: str,
        max_results: int = 10,
        retstart: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append({"query": query, "max_results": max_results, "retstart": retstart})
        if (
            self.raise_at_retstart is not None
            and retstart == self.raise_at_retstart
            and max_results > 1
        ):
            assert self.raise_exc is not None
            raise self.raise_exc

        # Probe call (max_results=1): return total_count from first page.
        if max_results == 1:
            base = (
                self.pages[0]
                if self.pages
                else {
                    "total_count": 0,
                    "query_translation": "",
                    "articles": [],
                    "has_more": False,
                    "returned_count": 0,
                }
            )
            return {
                "total_count": base["total_count"],
                "query_translation": base["query_translation"],
                "articles": (base["articles"][:1] if base["articles"] else []),
                "has_more": base["total_count"] > 1,
                "returned_count": 1 if base["articles"] else 0,
            }

        # Full search call: find the page covering this retstart.
        for page in self.pages:
            if page["retstart"] == retstart:
                return page
        # Past the end of the script.
        return {
            "total_count": (self.pages[0]["total_count"] if self.pages else 0),
            "query_translation": "",
            "articles": [],
            "has_more": False,
            "returned_count": 0,
        }

    def get_article_metadata(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def get_full_text_article(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def convert_article_ids(
        self, ids: list[str], from_type: str, to_type: str, **kwargs: Any
    ) -> dict[str, Any]:
        raise NotImplementedError

    def find_related_articles(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError


def _page(retstart: int, pmids: list[str], total: int, *, qt: str = "X[Mesh]") -> dict[str, Any]:
    return {
        "retstart": retstart,
        "total_count": total,
        "query_translation": qt,
        "articles": [_record(p) for p in pmids],
        "returned_count": len(pmids),
        "has_more": retstart + len(pmids) < total,
    }


def _orchestrator(caller: _FakeMCPCaller, session_dir: Path) -> SearchOrchestrator:
    return SearchOrchestrator(PubMedClient(caller), AuditLog(session_dir))


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_run_rejects_invalid_batch_size(tmp_path: Path) -> None:
    orch = _orchestrator(_FakeMCPCaller(pages=[]), tmp_path)
    with pytest.raises(ValueError, match="batch_size"):
        orch.run("q", project_id="P", claim_id="C", session_dir=tmp_path, batch_size=0)


def test_run_rejects_invalid_max_batches(tmp_path: Path) -> None:
    orch = _orchestrator(_FakeMCPCaller(pages=[]), tmp_path)
    with pytest.raises(ValueError, match="max_batches"):
        orch.run(
            "q",
            project_id="P",
            claim_id="C",
            session_dir=tmp_path,
            max_batches=-1,
        )


def test_run_rejects_missing_session_dir(tmp_path: Path) -> None:
    orch = _orchestrator(_FakeMCPCaller(pages=[]), tmp_path)
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError, match="session_dir"):
        orch.run("q", project_id="P", claim_id="C", session_dir=missing)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_happy_path_three_batches(tmp_path: Path) -> None:
    pages = [
        _page(0, ["1", "2", "3"], total=7),
        _page(3, ["4", "5", "6"], total=7),
        _page(6, ["7"], total=7),
    ]
    caller = _FakeMCPCaller(pages=pages)
    orch = _orchestrator(caller, tmp_path)
    result = orch.run(
        "Collagen[Mesh]",
        project_id="722-Retro",
        claim_id="CB-bov-01",
        session_dir=tmp_path,
        batch_size=3,
    )
    assert isinstance(result, SearchRunResult)
    assert result.batches_written == 3
    assert result.records_persisted == 7
    assert result.resumed_from_batch == 0
    assert result.probe is not None
    assert result.probe.total_count == 7

    # State must have all 7 records, all marked retrieved.
    assert result.state.total_records == 7
    for info in result.state.status_map.values():
        assert info.retrieved
        assert not info.screened
        assert not info.classified
        assert not info.extracted


def test_persisted_records_have_status_flags(tmp_path: Path) -> None:
    caller = _FakeMCPCaller(pages=[_page(0, ["1", "2"], total=2)])
    orch = _orchestrator(caller, tmp_path)
    orch.run("q", project_id="P", claim_id="C", session_dir=tmp_path, batch_size=10)
    files = find_batches(tmp_path, "C")
    assert len(files) == 1
    records = load(files[0])
    assert isinstance(records, list)
    for record in records:
        assert record[RecordStatus.RETRIEVED.value] is True
        assert record[RecordStatus.SCREENED.value] is False
        assert record[RecordStatus.CLASSIFIED.value] is False
        assert record[RecordStatus.EXTRACTED.value] is False


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def test_strategy_probe_logged_to_audit(tmp_path: Path) -> None:
    pages = [_page(0, ["1"], total=1)]
    caller = _FakeMCPCaller(pages=pages)
    audit = AuditLog(tmp_path)
    orch = SearchOrchestrator(PubMedClient(caller), audit)
    orch.run(
        "Collagen[Mesh] AND Cattle[Mesh]",
        project_id="722-Retro",
        claim_id="CB-bov-01",
        session_dir=tmp_path,
        probe_rationale="final query for CB-bov-01",
    )
    probes = audit.load_strategy_probes(claim_id="CB-bov-01")
    assert len(probes) == 1
    assert probes[0].query == "Collagen[Mesh] AND Cattle[Mesh]"
    assert probes[0].total_count == 1
    assert probes[0].rationale == "final query for CB-bov-01"
    assert probes[0].heat_bar == "green"


def test_disabling_probe_skips_strategy_log(tmp_path: Path) -> None:
    pages = [_page(0, ["1"], total=1)]
    caller = _FakeMCPCaller(pages=pages)
    audit = AuditLog(tmp_path)
    orch = SearchOrchestrator(PubMedClient(caller), audit)
    result = orch.run(
        "q",
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        capture_strategy_probe=False,
    )
    assert result.probe is None
    assert audit.load_strategy_probes(claim_id="C") == []


# ---------------------------------------------------------------------------
# Resume / skip_existing
# ---------------------------------------------------------------------------


def test_resume_skips_existing_batches_and_continues(tmp_path: Path) -> None:
    """Pre-existing batches 0..1 -> run starts at batch 2 with retstart=20."""
    # Pre-seed two batches the orchestrator should not touch.
    save_batch(
        tmp_path,
        "C",
        0,
        [_record("preexisting-0-1"), _record("preexisting-0-2")],
    )
    save_batch(
        tmp_path,
        "C",
        1,
        [_record("preexisting-1-1")],
    )
    # The caller has a page only at retstart=20.
    pages = [_page(20, ["new-1", "new-2"], total=22)]
    caller = _FakeMCPCaller(pages=pages)
    orch = _orchestrator(caller, tmp_path)
    result = orch.run(
        "q",
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        batch_size=10,
    )
    assert result.resumed_from_batch == 2
    assert result.batches_written == 1
    # Inspect calls: probe (max_results=1) + one search at retstart=20.
    search_calls = [c for c in caller.calls if c["max_results"] > 1]
    assert len(search_calls) == 1
    assert search_calls[0]["retstart"] == 20

    # Pre-existing batches still intact (lengths preserved).
    files = find_batches(tmp_path, "C")
    assert len(files) == 3
    batch_0 = load(files[0])
    assert isinstance(batch_0, list)
    assert batch_0[0]["pmid"] == "preexisting-0-1"


def test_skip_existing_false_starts_at_zero_overwriting(tmp_path: Path) -> None:
    """skip_existing=False resets to batch 0 and overwrites in place."""
    save_batch(tmp_path, "C", 0, [{"pmid": "stale"}])
    pages = [_page(0, ["fresh-1"], total=1)]
    caller = _FakeMCPCaller(pages=pages)
    orch = _orchestrator(caller, tmp_path)
    result = orch.run(
        "q",
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        skip_existing=False,
    )
    assert result.resumed_from_batch == 0
    files = find_batches(tmp_path, "C")
    assert len(files) == 1
    refreshed = load(files[0])
    assert isinstance(refreshed, list)
    assert refreshed[0]["pmid"] == "fresh-1"


def test_resume_with_no_existing_batches_starts_at_zero(tmp_path: Path) -> None:
    pages = [_page(0, ["1"], total=1)]
    caller = _FakeMCPCaller(pages=pages)
    orch = _orchestrator(caller, tmp_path)
    result = orch.run(
        "q",
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
    )
    assert result.resumed_from_batch == 0


def test_resume_finds_highest_batch_number_with_gaps(tmp_path: Path) -> None:
    """Batches 0 and 3 exist (gap at 1, 2): resume picks max+1 = 4."""
    save_batch(tmp_path, "C", 0, [{"pmid": "a"}])
    save_batch(tmp_path, "C", 3, [{"pmid": "b"}])
    pages = [_page(40, [], total=2)]  # past the end
    caller = _FakeMCPCaller(pages=pages)
    orch = _orchestrator(caller, tmp_path)
    result = orch.run(
        "q",
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        batch_size=10,
    )
    assert result.resumed_from_batch == 4


# ---------------------------------------------------------------------------
# max_batches cap
# ---------------------------------------------------------------------------


def test_max_batches_caps_run(tmp_path: Path) -> None:
    pages = [
        _page(0, ["a", "b"], total=6),
        _page(2, ["c", "d"], total=6),
        _page(4, ["e", "f"], total=6),
    ]
    caller = _FakeMCPCaller(pages=pages)
    audit = AuditLog(tmp_path)
    orch = SearchOrchestrator(PubMedClient(caller), audit)
    result = orch.run(
        "q",
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        batch_size=2,
        max_batches=2,
    )
    assert result.batches_written == 2
    events = audit.load_events(event_type="search_capped")
    assert len(events) == 1
    assert events[0].details["max_batches"] == 2


def test_max_batches_zero_writes_nothing(tmp_path: Path) -> None:
    pages = [_page(0, ["a"], total=1)]
    caller = _FakeMCPCaller(pages=pages)
    audit = AuditLog(tmp_path)
    orch = SearchOrchestrator(PubMedClient(caller), audit)
    result = orch.run(
        "q",
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        max_batches=0,
    )
    assert result.batches_written == 0
    assert result.records_persisted == 0


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


def test_empty_search_results_logs_search_empty(tmp_path: Path) -> None:
    pages = [_page(0, [], total=0)]
    caller = _FakeMCPCaller(pages=pages)
    audit = AuditLog(tmp_path)
    orch = SearchOrchestrator(PubMedClient(caller), audit)
    result = orch.run("q", project_id="P", claim_id="C", session_dir=tmp_path)
    assert result.batches_written == 0
    empty_events = audit.load_events(event_type="search_empty")
    assert len(empty_events) == 1


# ---------------------------------------------------------------------------
# Mid-batch error
# ---------------------------------------------------------------------------


def test_mid_batch_error_persists_prior_batches_and_logs(tmp_path: Path) -> None:
    """Error during batch 2: batches 0 and 1 still on disk; error logged."""
    pages = [
        _page(0, ["1", "2"], total=10),
        _page(2, ["3", "4"], total=10),
    ]
    caller = _FakeMCPCaller(
        pages=pages,
        raise_at_retstart=4,
        raise_exc=RuntimeError("MCP timeout"),
    )
    audit = AuditLog(tmp_path)
    orch = SearchOrchestrator(PubMedClient(caller), audit)
    with pytest.raises(RuntimeError, match="MCP timeout"):
        orch.run(
            "q",
            project_id="P",
            claim_id="C",
            session_dir=tmp_path,
            batch_size=2,
        )
    # Batches 0 and 1 are persisted.
    files = find_batches(tmp_path, "C")
    assert len(files) == 2
    # The error event is logged with the failing batch number (2).
    err_events = audit.load_events(event_type="search_error")
    assert len(err_events) == 1
    assert err_events[0].details["batch_num"] == 2
    assert err_events[0].details["batches_written_before_error"] == 2
    assert err_events[0].details["error_type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Audit event sequence
# ---------------------------------------------------------------------------


def test_event_sequence_on_clean_run(tmp_path: Path) -> None:
    pages = [_page(0, ["1", "2"], total=2)]
    caller = _FakeMCPCaller(pages=pages)
    audit = AuditLog(tmp_path)
    orch = SearchOrchestrator(PubMedClient(caller), audit)
    orch.run("q", project_id="P", claim_id="C", session_dir=tmp_path, batch_size=10)
    events = audit.load_events()
    types = [e.event_type for e in events]
    assert types == ["search_started", "batch_saved", "search_completed"]
    completed = events[-1]
    assert completed.details["batches_written"] == 1
    assert completed.details["records_persisted"] == 2


def test_resume_records_resumed_from_batch_in_started_event(tmp_path: Path) -> None:
    save_batch(tmp_path, "C", 0, [_record("old-1"), _record("old-2")])
    pages = [_page(10, ["new-1"], total=11)]
    caller = _FakeMCPCaller(pages=pages)
    audit = AuditLog(tmp_path)
    orch = SearchOrchestrator(PubMedClient(caller), audit)
    orch.run(
        "q",
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        batch_size=10,
    )
    started = audit.load_events(event_type="search_started")
    assert len(started) == 1
    assert started[0].details["resumed_from_batch"] == 1
    assert started[0].details["retstart"] == 10


# ---------------------------------------------------------------------------
# Pagination termination
# ---------------------------------------------------------------------------


def test_run_stops_when_has_more_is_false(tmp_path: Path) -> None:
    """Single page with has_more=False -> only one search call besides probe."""
    pages = [_page(0, ["a", "b"], total=2)]
    caller = _FakeMCPCaller(pages=pages)
    orch = _orchestrator(caller, tmp_path)
    orch.run("q", project_id="P", claim_id="C", session_dir=tmp_path, batch_size=10)
    # probe + 1 search
    search_calls = [c for c in caller.calls if c["max_results"] > 1]
    assert len(search_calls) == 1


def test_run_stops_on_empty_records_even_if_has_more_true(tmp_path: Path) -> None:
    """A page with no records and has_more=True still terminates the loop."""
    pages = [
        {
            "retstart": 0,
            "total_count": 5,
            "query_translation": "",
            "articles": [],
            "returned_count": 0,
            "has_more": True,  # liar; we still must stop
        }
    ]
    caller = _FakeMCPCaller(pages=pages)
    orch = _orchestrator(caller, tmp_path)
    result = orch.run("q", project_id="P", claim_id="C", session_dir=tmp_path)
    assert result.batches_written == 0

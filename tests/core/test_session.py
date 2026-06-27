# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.core.session."""

from pathlib import Path
from typing import Any

import pytest

from ring2.core.persistence import save_batch
from ring2.core.session import (
    RecordStatus,
    RecordStatusInfo,
    SessionStateImpl,
    resume_state,
)

# ---------------------------------------------------------------------------
# RecordStatusInfo
# ---------------------------------------------------------------------------


def test_status_info_defaults() -> None:
    s = RecordStatusInfo(pmid="1")
    assert not s.retrieved
    assert not s.is_complete
    assert s.next_step is RecordStatus.RETRIEVED


def test_status_info_progression() -> None:
    s = RecordStatusInfo(pmid="1", retrieved=True)
    assert s.next_step is RecordStatus.SCREENED

    s = RecordStatusInfo(pmid="1", retrieved=True, screened=True)
    assert s.next_step is RecordStatus.CLASSIFIED

    s = RecordStatusInfo(pmid="1", retrieved=True, screened=True, classified=True)
    assert s.next_step is RecordStatus.EXTRACTED

    s = RecordStatusInfo(pmid="1", retrieved=True, screened=True, classified=True, extracted=True)
    assert s.next_step is None
    assert s.is_complete


def test_status_info_from_record_minimal() -> None:
    s = RecordStatusInfo.from_record({"pmid": "42"})
    assert s.pmid == "42"
    assert not s.retrieved


def test_status_info_from_record_full() -> None:
    s = RecordStatusInfo.from_record(
        {
            "pmid": "42",
            "retrieved": True,
            "screened": True,
            "classified": False,
            "extracted": False,
        }
    )
    assert s.next_step is RecordStatus.CLASSIFIED


def test_status_info_from_record_missing_pmid_raises() -> None:
    with pytest.raises(KeyError, match="pmid"):
        RecordStatusInfo.from_record({"retrieved": True})


def test_status_info_pmid_coerced_to_str() -> None:
    s = RecordStatusInfo.from_record({"pmid": 42})
    assert s.pmid == "42"


# ---------------------------------------------------------------------------
# resume_state
# ---------------------------------------------------------------------------


def _record(pmid: str, **flags: Any) -> dict[str, Any]:
    """Tiny helper to build a record dict with explicit status flags."""
    return {"pmid": pmid, **flags}


def test_resume_state_empty_dir(tmp_path: Path) -> None:
    state = resume_state(tmp_path, "proj", "X")
    assert isinstance(state, SessionStateImpl)
    assert state.project_id == "proj"
    assert state.claim_id == "X"
    assert state.total_records == 0
    assert state.first_incomplete() is None
    assert state.batch_files == ()


def test_resume_state_single_batch(tmp_path: Path) -> None:
    save_batch(
        tmp_path,
        "X",
        0,
        [
            _record("1", retrieved=True, screened=True),
            _record("2", retrieved=True),
            _record("3"),
        ],
    )
    state = resume_state(tmp_path, "proj", "X")
    assert state.total_records == 3
    assert state.status_map["1"].next_step is RecordStatus.CLASSIFIED
    assert state.status_map["2"].next_step is RecordStatus.SCREENED
    assert state.status_map["3"].next_step is RecordStatus.RETRIEVED


def test_resume_state_multiple_batches_assembles_in_order(tmp_path: Path) -> None:
    save_batch(tmp_path, "X", 0, [_record(str(i)) for i in range(3)])
    save_batch(tmp_path, "X", 1, [_record(str(i)) for i in range(3, 6)])
    save_batch(tmp_path, "X", 2, [_record(str(i)) for i in range(6, 9)])

    state = resume_state(tmp_path, "proj", "X")
    assert state.total_records == 9
    assert list(state.status_map.keys()) == [str(i) for i in range(9)]


def test_resume_state_later_batch_overrides_status_for_same_pmid(tmp_path: Path) -> None:
    """If the same PMID appears in batch_00 and batch_01, the later one wins."""
    save_batch(tmp_path, "X", 0, [_record("1", retrieved=True)])
    save_batch(
        tmp_path,
        "X",
        1,
        [_record("1", retrieved=True, screened=True, classified=True, extracted=True)],
    )
    state = resume_state(tmp_path, "proj", "X")
    assert state.status_map["1"].is_complete


def test_resume_state_first_incomplete_finds_in_batch_order(tmp_path: Path) -> None:
    save_batch(
        tmp_path,
        "X",
        0,
        [
            _record("1", retrieved=True, screened=True, classified=True, extracted=True),
            _record("2", retrieved=True, screened=True),
            _record("3"),
        ],
    )
    state = resume_state(tmp_path, "proj", "X")
    first = state.first_incomplete()
    assert first is not None
    assert first.pmid == "2"


def test_resume_state_complete_count(tmp_path: Path) -> None:
    save_batch(
        tmp_path,
        "X",
        0,
        [
            _record("1", retrieved=True, screened=True, classified=True, extracted=True),
            _record("2", retrieved=True, screened=True, classified=True, extracted=True),
            _record("3"),
        ],
    )
    state = resume_state(tmp_path, "proj", "X")
    assert state.complete_count == 2
    assert state.incomplete_count == 1


def test_resume_state_records_pending_by_step(tmp_path: Path) -> None:
    save_batch(
        tmp_path,
        "X",
        0,
        [
            _record("a"),  # next: retrieved
            _record("b", retrieved=True),  # next: screened
            _record("c", retrieved=True, screened=True),  # next: classified
            _record("d", retrieved=True, screened=True, classified=True),  # next: extracted
        ],
    )
    state = resume_state(tmp_path, "proj", "X")
    assert [r.pmid for r in state.records_pending(RecordStatus.RETRIEVED)] == ["a"]
    assert [r.pmid for r in state.records_pending(RecordStatus.SCREENED)] == ["b"]
    assert [r.pmid for r in state.records_pending(RecordStatus.CLASSIFIED)] == ["c"]
    assert [r.pmid for r in state.records_pending(RecordStatus.EXTRACTED)] == ["d"]


def test_resume_state_mixed_yaml_json_batches(tmp_path: Path) -> None:
    """Dual-format read: a session with mixed legacy JSON + YAML batches."""
    save_batch(tmp_path, "X", 0, [_record("1")], format="json")
    save_batch(tmp_path, "X", 1, [_record("2")], format="yaml")
    state = resume_state(tmp_path, "proj", "X")
    assert set(state.status_map) == {"1", "2"}


def test_resume_state_batch_files_in_order(tmp_path: Path) -> None:
    save_batch(tmp_path, "X", 2, [_record("c")])
    save_batch(tmp_path, "X", 0, [_record("a")])
    save_batch(tmp_path, "X", 1, [_record("b")])
    state = resume_state(tmp_path, "proj", "X")
    names = [p.stem for p in state.batch_files]
    assert names == [
        "search_X_batch_00",
        "search_X_batch_01",
        "search_X_batch_02",
    ]


# ---------------------------------------------------------------------------
# resume_state — defensive: alternate batch shape ({records: [...]})
# ---------------------------------------------------------------------------


def test_resume_state_records_wrapped_in_records_key(tmp_path: Path) -> None:
    from ring2.core.persistence import save

    save(
        tmp_path / "search_X_batch_00.yaml",
        {"records": [_record("1", retrieved=True)]},
    )
    state = resume_state(tmp_path, "proj", "X")
    assert "1" in state.status_map


def test_resume_state_unrecognised_shape_raises(tmp_path: Path) -> None:
    from ring2.core.persistence import save

    save(tmp_path / "search_X_batch_00.yaml", {"unexpected": "shape"})
    with pytest.raises(ValueError, match="Unrecognised batch shape"):
        resume_state(tmp_path, "proj", "X")


# ---------------------------------------------------------------------------
# SessionStateImpl conforms to the SessionState Protocol
# ---------------------------------------------------------------------------


def test_session_state_conforms_to_protocol() -> None:
    from ring2.core.adapter_base import SessionState

    state = SessionStateImpl(project_id="p", claim_id="c", session_dir=Path("/tmp"))
    assert isinstance(state, SessionState)

# Copyright 2026 lets-innovate.ch (Michael Hug)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Session state — scan batches, reconstruct per-PMID status, resume.

Per the prompt v3 §Stage 3.2 save/resume requirement:

    "Resume logic must scan all batch files belonging to a claim,
    reconstruct the per-PMID status map, and continue from the first
    incomplete record."

The four status flags on each record (``retrieved``, ``screened``,
``classified``, ``extracted``) progress in that order. A record is
*complete* when all four are true.

This module is intentionally adapter-agnostic — adapters write the
flags into batch records; ``SessionStateImpl`` only reads them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .persistence import find_batches, load

# ---------------------------------------------------------------------------
# Status model
# ---------------------------------------------------------------------------


class RecordStatus(StrEnum):
    """The four lifecycle stages a record passes through, in order."""

    RETRIEVED = "retrieved"
    SCREENED = "screened"
    CLASSIFIED = "classified"
    EXTRACTED = "extracted"


# Ordered tuple for sequencing checks.
_STATUS_ORDER: tuple[RecordStatus, ...] = (
    RecordStatus.RETRIEVED,
    RecordStatus.SCREENED,
    RecordStatus.CLASSIFIED,
    RecordStatus.EXTRACTED,
)


@dataclass(frozen=True, slots=True)
class RecordStatusInfo:
    """Per-PMID lifecycle state."""

    pmid: str
    retrieved: bool = False
    screened: bool = False
    classified: bool = False
    extracted: bool = False
    source_batch: Path | None = None

    @property
    def is_complete(self) -> bool:
        """True if all four stages are done."""
        return self.retrieved and self.screened and self.classified and self.extracted

    @property
    def next_step(self) -> RecordStatus | None:
        """The next stage to execute, or ``None`` if complete."""
        for status in _STATUS_ORDER:
            if not getattr(self, status.value):
                return status
        return None

    @classmethod
    def from_record(
        cls, record: Mapping[str, Any], source_batch: Path | None = None
    ) -> RecordStatusInfo:
        """Build status info from one batch record dict.

        Required key: ``pmid``. Status flags default to ``False`` if absent.
        """
        if "pmid" not in record:
            raise KeyError(f"Record missing required 'pmid' key. Available keys: {list(record)}")
        return cls(
            pmid=str(record["pmid"]),
            retrieved=bool(record.get(RecordStatus.RETRIEVED.value, False)),
            screened=bool(record.get(RecordStatus.SCREENED.value, False)),
            classified=bool(record.get(RecordStatus.CLASSIFIED.value, False)),
            extracted=bool(record.get(RecordStatus.EXTRACTED.value, False)),
            source_batch=source_batch,
        )


# ---------------------------------------------------------------------------
# Session state — implements the SessionState Protocol from adapter_base
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionStateImpl:
    """Concrete per-claim session state.

    Carries the directory the session lives in, the project + claim
    identifiers, and the assembled per-PMID status map.

    Implements the :class:`ring2.core.adapter_base.SessionState` Protocol
    by virtue of exposing ``project_id`` and ``claim_id`` attributes.
    """

    project_id: str
    claim_id: str
    session_dir: Path
    status_map: dict[str, RecordStatusInfo] = field(default_factory=dict)
    batch_files: tuple[Path, ...] = ()

    # -- queries on the status map -----------------------------------------

    @property
    def total_records(self) -> int:
        return len(self.status_map)

    @property
    def complete_count(self) -> int:
        return sum(1 for s in self.status_map.values() if s.is_complete)

    @property
    def incomplete_count(self) -> int:
        return self.total_records - self.complete_count

    def records_pending(self, step: RecordStatus) -> list[RecordStatusInfo]:
        """Records whose next-step is ``step``."""
        return [info for info in self.status_map.values() if info.next_step is step]

    def first_incomplete(self) -> RecordStatusInfo | None:
        """The first record (in batch-file order) that is not complete.

        Iteration order: the dict insertion order matches the order in
        which records were encountered during :func:`resume_state`'s
        batch scan — i.e. batch 00 record 0, batch 00 record 1, ...,
        batch 01 record 0, ...
        """
        for info in self.status_map.values():
            if not info.is_complete:
                return info
        return None


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def resume_state(
    session_dir: Path,
    project_id: str,
    claim_id: str,
) -> SessionStateImpl:
    """Reconstruct session state by scanning all batch files for ``claim_id``.

    Logic:

        1. Enumerate batch files via :func:`persistence.find_batches`
           (yields YAML and JSON, sorted by batch number).
        2. For each batch, load its records (expected: a YAML list of
           per-record dicts).
        3. For each record, build a :class:`RecordStatusInfo` keyed by
           ``pmid``. Later occurrences of the same PMID *overwrite*
           earlier ones — this allows a re-run of the same batch
           (e.g. after a network failure) to refresh the status flags
           without leaving stale state.

    Returns:
        A :class:`SessionStateImpl` snapshot. Mutating it does not
        rewrite the batch files; callers re-save batches explicitly
        through the persistence module.
    """
    batches = find_batches(session_dir, claim_id)
    status_map: dict[str, RecordStatusInfo] = {}

    for batch_path in batches:
        records = _load_batch_records(batch_path)
        for record in records:
            info = RecordStatusInfo.from_record(record, source_batch=batch_path)
            status_map[info.pmid] = info

    return SessionStateImpl(
        project_id=project_id,
        claim_id=claim_id,
        session_dir=session_dir,
        status_map=status_map,
        batch_files=tuple(batches),
    )


def _load_batch_records(batch_path: Path) -> Iterable[Mapping[str, Any]]:
    """Load one batch file, returning the contained record list.

    Tolerates the two reasonable on-disk shapes:

        (a) a flat YAML/JSON list of record dicts (canonical)
        (b) a dict with a ``records`` key wrapping the list (legacy)
    """
    data = load(batch_path)
    if isinstance(data, list):
        return data
    if isinstance(data, Mapping):
        inner = data.get("records")
        if isinstance(inner, list):
            return inner
    raise ValueError(
        f"Unrecognised batch shape in {batch_path}: expected list of records "
        f"or {{'records': [...]}}, got {type(data).__name__}"
    )


__all__ = [
    "RecordStatus",
    "RecordStatusInfo",
    "SessionStateImpl",
    "resume_state",
]

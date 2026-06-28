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
"""Search orchestration — Stage 3 in the prompt-v3 pipeline.

Wires together :class:`PubMedClient`, persistence, audit and session
modules into a single ``run()`` entry point. Adapter-agnostic: this
module only retrieves records and stamps them with ``retrieved=True``.
Downstream stages (screening, classification, extraction) flip the
remaining :class:`RecordStatus` flags.

Workflow
--------
1. Optionally capture a hit-count strategy probe for the final query
   (rationale and heat-bar bucket logged to the audit trail).
2. If ``skip_existing=True`` (default) and prior batches exist for
   ``claim_id``, resume from the next batch number with a matching
   ``retstart`` offset. Existing files are NOT touched.
3. Iterate paginated searches via :meth:`PubMedClient.search`; for each
   batch, serialise records as dicts with status flags (``retrieved=True``,
   the rest ``False``) and persist via :func:`save_batch`.
4. Log a ``batch_saved`` event after every successful persist. On
   exception, log ``search_error`` with the failing batch number and
   re-raise.
5. On normal completion, log ``search_completed`` and return a freshly
   resumed :class:`SessionStateImpl`.

The orchestrator does not retry, does not throttle, and does not
debounce — all of that is the responsibility of upstream layers (UI
strategy builder for debouncing; future Stage 1.8 work for retries).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapter_base import PubMedRecord
from .audit import AuditLog, probe_entry_from_hit_count
from .persistence import find_batches, save_batch
from .pubmed_client import HitCountResult, PubMedClient, heat_bar
from .session import RecordStatus, SessionStateImpl, resume_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BATCH_NUM_PATTERN = re.compile(r"_batch_(\d+)$")


def _parse_batch_num(path: Path) -> int:
    """Extract the batch number from a canonical batch-file path.

    The canonical stem is ``search_<claim_id>_batch_<NN>``; we match the
    trailing ``_batch_NN`` to remain robust against claim_ids that
    contain underscores or digits.

    Raises:
        ValueError: if the stem does not match the canonical pattern.
    """
    match = _BATCH_NUM_PATTERN.search(path.stem)
    if not match:
        raise ValueError(f"Cannot parse batch number from path {path!s}")
    return int(match.group(1))


def _record_to_persistable_dict(record: PubMedRecord) -> dict[str, Any]:
    """Serialise a :class:`PubMedRecord` to a dict with status flags.

    The four :class:`RecordStatus` flags are written explicitly so a
    later :func:`resume_state` scan finds the per-PMID status without
    further metadata reads.
    """
    return {
        "pmid": record.pmid,
        "title": record.title,
        "doi": record.doi,
        "abstract": record.abstract,
        "journal": record.journal,
        "year": record.year,
        "authors": list(record.authors),
        "publication_types": list(record.publication_types),
        "raw": dict(record.raw) if record.raw else {},
        RecordStatus.RETRIEVED.value: True,
        RecordStatus.SCREENED.value: False,
        RecordStatus.CLASSIFIED.value: False,
        RecordStatus.EXTRACTED.value: False,
    }


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchRunResult:
    """Summary of a completed (or partially completed) orchestrator run.

    Fields:
        state: the resumed :class:`SessionStateImpl` after the run.
        batches_written: number of new batch files written in this run.
        records_persisted: number of new records persisted in this run.
        resumed_from_batch: batch number the run started at
            (``0`` if no prior batches existed).
        probe: the :class:`HitCountResult` captured at the start of the
            run, or ``None`` if probing was disabled.
    """

    state: SessionStateImpl
    batches_written: int
    records_persisted: int
    resumed_from_batch: int
    probe: HitCountResult | None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class SearchOrchestrator:
    """Coordinates a single literature-search run for one claim.

    A single instance is bound to a :class:`PubMedClient` and an
    :class:`AuditLog`; the audit log already knows its ``session_dir``,
    so :meth:`run` only needs the ``session_dir`` for batch persistence
    (in practice both are usually the same directory).
    """

    def __init__(self, client: PubMedClient, audit: AuditLog) -> None:
        self._client = client
        self._audit = audit

    def run(
        self,
        query: str,
        *,
        project_id: str,
        claim_id: str,
        session_dir: Path,
        batch_size: int = 10,
        max_batches: int | None = None,
        skip_existing: bool = True,
        capture_strategy_probe: bool = True,
        probe_rationale: str | None = None,
    ) -> SearchRunResult:
        """Run a paginated PubMed search and persist results in batches.

        Args:
            query: final PubMed search string (caller is responsible for
                its construction; this method does not modify or refine
                the query).
            project_id: the project identifier (e.g. ``"722-Retro"``).
            claim_id: the per-claim identifier (e.g. ``"CB-bov-01"``).
            session_dir: directory for batch files. Must already exist
                or be creatable by the caller; the orchestrator does
                NOT create it implicitly to avoid masking path mistakes.
            batch_size: records per batch (default 10, matching the
                project-wide persistence convention).
            max_batches: if set, cap the number of *new* batches written
                in this run. Useful for dev/test or staged retrieval.
            skip_existing: when ``True`` (default), prior batches for
                ``claim_id`` are detected and the run resumes at the
                next batch number with a matching ``retstart`` offset.
                When ``False``, the run starts at batch 0 and overwrites
                any existing batch files with the same number.
            capture_strategy_probe: when ``True`` (default), a
                hit-count probe is performed at the start of the run
                and logged to ``strategy_build_log.yaml`` via the audit
                log. The cached :class:`PubMedClient` probe cache means
                a subsequent retrieval does not pay for it twice.
            probe_rationale: optional free-text rationale attached to
                the strategy probe entry.

        Returns:
            A :class:`SearchRunResult` summarising the run.

        Raises:
            FileNotFoundError: if ``session_dir`` does not exist.
            ValueError: on invalid arguments.
            Exception: any exception raised by the PubMed MCP caller is
                re-raised after the partial state is persisted and a
                ``search_error`` audit event is logged.
        """
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        if max_batches is not None and max_batches < 0:
            raise ValueError(f"max_batches must be >= 0, got {max_batches}")
        if not session_dir.exists():
            raise FileNotFoundError(
                f"session_dir does not exist: {session_dir!s}. "
                "Create it before calling SearchOrchestrator.run() "
                "to avoid masking path mistakes."
            )

        # -- 1. Strategy probe -------------------------------------------------
        probe: HitCountResult | None = None
        if capture_strategy_probe:
            probe = self._client.probe_hit_count(query)
            self._audit.log_strategy_probe(
                probe_entry_from_hit_count(
                    claim_id=claim_id,
                    hit=probe,
                    rationale=probe_rationale,
                    heat_bar_value=heat_bar(probe.total_count),
                )
            )

        # -- 2. Resume detection ----------------------------------------------
        start_batch_num, retstart = self._compute_resume_offset(
            session_dir=session_dir,
            claim_id=claim_id,
            batch_size=batch_size,
            skip_existing=skip_existing,
        )

        self._audit.log_event(
            "search_started",
            claim_id=claim_id,
            project_id=project_id,
            query=query,
            batch_size=batch_size,
            max_batches=max_batches,
            resumed_from_batch=start_batch_num,
            retstart=retstart,
            skip_existing=skip_existing,
        )

        # -- 3. Retrieval loop -------------------------------------------------
        batches_written = 0
        records_persisted = 0
        current_batch_num = start_batch_num
        current_retstart = retstart

        try:
            while True:
                if max_batches is not None and batches_written >= max_batches:
                    self._audit.log_event(
                        "search_capped",
                        claim_id=claim_id,
                        max_batches=max_batches,
                        batches_written=batches_written,
                    )
                    break

                page = self._client.search(query, max_results=batch_size, retstart=current_retstart)
                if not page.records:
                    if batches_written == 0 and start_batch_num == 0:
                        self._audit.log_event(
                            "search_empty",
                            claim_id=claim_id,
                            query=query,
                            total_count=page.total_count,
                        )
                    break

                # Persist this batch.
                serialised = [_record_to_persistable_dict(r) for r in page.records]
                written_path = save_batch(session_dir, claim_id, current_batch_num, serialised)
                self._audit.log_event(
                    "batch_saved",
                    claim_id=claim_id,
                    batch_num=current_batch_num,
                    record_count=len(page.records),
                    path=str(written_path),
                    retstart=current_retstart,
                    total_count=page.total_count,
                )
                batches_written += 1
                records_persisted += len(page.records)
                current_batch_num += 1
                current_retstart += len(page.records)

                if not page.has_more:
                    break

        except Exception as exc:
            self._audit.log_event(
                "search_error",
                claim_id=claim_id,
                batch_num=current_batch_num,
                retstart=current_retstart,
                batches_written_before_error=batches_written,
                records_persisted_before_error=records_persisted,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

        self._audit.log_event(
            "search_completed",
            claim_id=claim_id,
            batches_written=batches_written,
            records_persisted=records_persisted,
            final_batch_num=current_batch_num - 1 if batches_written else None,
        )

        state = resume_state(session_dir, project_id, claim_id)
        return SearchRunResult(
            state=state,
            batches_written=batches_written,
            records_persisted=records_persisted,
            resumed_from_batch=start_batch_num,
            probe=probe,
        )

    # -- internals --------------------------------------------------------------

    @staticmethod
    def _compute_resume_offset(
        *,
        session_dir: Path,
        claim_id: str,
        batch_size: int,
        skip_existing: bool,
    ) -> tuple[int, int]:
        """Determine the ``(batch_num, retstart)`` pair the loop should start at.

        With ``skip_existing=False`` the run always starts at ``(0, 0)``
        and will overwrite any colliding batch file (last-write-wins).

        With ``skip_existing=True`` the highest existing batch number is
        found via :func:`find_batches`. The next batch number is
        ``max + 1`` and ``retstart = (max + 1) * batch_size``. Gaps in
        the batch numbering are not filled — the caller is expected to
        keep the batch sequence dense.
        """
        if not skip_existing:
            return 0, 0
        existing = find_batches(session_dir, claim_id)
        if not existing:
            return 0, 0
        max_existing = max(_parse_batch_num(p) for p in existing)
        next_num = max_existing + 1
        return next_num, next_num * batch_size


__all__ = [
    "SearchOrchestrator",
    "SearchRunResult",
]

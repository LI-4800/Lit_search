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
"""End-to-end orchestrator — Stufe 1.9a Inkrement 4.

Wires the full RING2 pipeline for one project YAML:

    load project config → resolve claim → (search) → load records →
    screen records → persist decisions → build PRISMA flow →
    appraise → assemble render context → render report → write report

The orchestrator is a thin coordinator. All real work happens in the
modules it calls; this module knits them together and handles the
adapter / shape conversions in the seams (AppraisalDecision →
ScreeningDecision, persisted dict → PubMedRecord, etc.).

External dependencies (MCP for PubMed, screening LLM) are injected via
:func:`run` keyword arguments. Defaults are the loud-failing null
callers (:class:`NullMCPCaller`, :class:`NullScreenerCaller`) — fine
for tests that bypass those phases via custom config or doubles, but a
production run requires real callers to be passed in.

CLI entry point: ``ring2 run <project.yaml>`` (see :mod:`ring2.cli.run`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ring2.adapters.mpco.adapter import MPCOAdapter
from ring2.adapters.mpco.appraisal.dispatcher import AppraisalDispatcher
from ring2.adapters.mpco.criteria_factory import (
    make_exclusion_criteria,
    make_inclusion_criteria,
)
from ring2.adapters.mpco.decision_persistence import (
    ScreeningDecision,
    write_decision_file,
)
from ring2.adapters.mpco.exclusion_codes import ExclusionCode, PrismaPhase, phase_for
from ring2.adapters.mpco.render_context import MPCORenderContext
from ring2.core.adapter_base import AppraisalDecision, AppraisalOutcome, PubMedRecord
from ring2.core.audit import AuditLog
from ring2.core.persistence import load as _persistence_load
from ring2.core.prisma import build_flow
from ring2.core.project_config_loader import load_project_config, resolve_claim
from ring2.core.pubmed_client import MCPCaller, NullMCPCaller, PubMedClient
from ring2.core.screening import NullScreenerCaller, ScreenerCaller, screen_record
from ring2.core.search import SearchOrchestrator
from ring2.core.session import SessionStateImpl, resume_state

if TYPE_CHECKING:
    from ring2.adapters.mpco.schema import MPCOClaim
    from ring2.core.project_config import ProjectConfig

__all__ = [
    "OrchestratorError",
    "OrchestratorRunResult",
    "build_render_context",
    "convert_decisions",
    "load_records_from_state",
    "run",
    "run_appraisal",
    "run_screening",
    "run_search",
    "write_report",
]


class OrchestratorError(Exception):
    """Raised on orchestrator-level errors (missing inputs, etc.)."""


@dataclass(frozen=True, slots=True)
class OrchestratorRunResult:
    """Summary of a completed orchestrator run.

    Attributes:
        report_path: path to the written markdown report.
        state: the final :class:`SessionStateImpl`.
        screening_decisions: tuple of :class:`ScreeningDecision`
            produced this run.
        eligible_records_count: number of records that passed all
            screening / eligibility gates and were dispatched to
            appraisal.
    """

    report_path: Path
    state: SessionStateImpl
    screening_decisions: tuple[ScreeningDecision, ...]
    eligible_records_count: int


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------


def run_search(
    config: ProjectConfig,
    claim: MPCOClaim,
    session_dir: Path,
    *,
    mcp_caller: MCPCaller,
) -> SessionStateImpl:
    """Run the search phase (if ``config.search`` is set) or resume state.

    Args:
        config: validated :class:`ProjectConfig`.
        claim: the claim under evaluation.
        session_dir: target directory for batch files and audit log.
        mcp_caller: PubMed MCP wrapper. Must be a real caller when
            ``config.search`` is set; a :class:`NullMCPCaller` raises
            on first MCP invocation.

    Returns:
        The :class:`SessionStateImpl` after the search (or resumed
        from existing batches when ``config.search`` is ``None``).
    """
    if config.search is None:
        return resume_state(session_dir, project_id=config.name, claim_id=claim.claim_id)

    client = PubMedClient(caller=mcp_caller)
    audit = AuditLog(session_dir)
    search_orch = SearchOrchestrator(client=client, audit=audit)
    result = search_orch.run(
        query=config.search.query,
        project_id=config.name,
        claim_id=claim.claim_id,
        session_dir=session_dir,
        batch_size=config.search.batch_size,
        max_batches=config.search.max_batches,
    )
    return result.state


def load_records_from_state(state: SessionStateImpl) -> list[PubMedRecord]:
    """Reconstruct :class:`PubMedRecord` instances from all batch files in ``state``.

    Batch files are sorted by batch number (already enforced by
    :attr:`SessionStateImpl.batch_files` ordering). Within a batch, records
    are returned in file order. Duplicates (same PMID across batches) are
    deduplicated keeping the last occurrence — consistent with the
    last-write-wins semantics in :func:`resume_state`.
    """
    by_pmid: dict[str, PubMedRecord] = {}
    for batch_path in state.batch_files:
        data = _persistence_load(batch_path)
        if isinstance(data, list):
            records_raw = data
        elif isinstance(data, dict) and isinstance(data.get("records"), list):
            records_raw = data["records"]
        else:
            raise OrchestratorError(
                f"Batch file {batch_path} has unsupported shape "
                f"(expected list or dict with 'records' key)"
            )
        for raw in records_raw:
            if not isinstance(raw, dict):
                continue
            pmid = raw.get("pmid")
            if not pmid:
                continue
            by_pmid[str(pmid)] = PubMedRecord(
                pmid=str(pmid),
                title=str(raw.get("title", "")),
                doi=raw.get("doi"),
                abstract=raw.get("abstract"),
                journal=raw.get("journal"),
                year=raw.get("year"),
                authors=tuple(raw.get("authors", ()) or ()),
                publication_types=tuple(raw.get("publication_types", ()) or ()),
                raw=dict(raw.get("raw", {}) or {}),
            )
    return list(by_pmid.values())


def run_screening(
    records: Iterable[PubMedRecord],
    claim: MPCOClaim,
    *,
    screener_caller: ScreenerCaller,
) -> list[AppraisalDecision]:
    """Screen each record against the claim's inclusion/exclusion criteria.

    Returns one :class:`AppraisalDecision` per input record, in input
    order. Conversion to :class:`ScreeningDecision` happens in
    :func:`convert_decisions`.
    """
    inclusion = make_inclusion_criteria(claim)
    exclusion = make_exclusion_criteria(claim)
    return [screen_record(r, inclusion, exclusion, caller=screener_caller) for r in records]


def convert_decisions(
    records: list[PubMedRecord],
    appraisal_decisions: list[AppraisalDecision],
    *,
    decided_at: datetime | None = None,
    decided_by: str = "orchestrator:ring2",
) -> tuple[tuple[ScreeningDecision, ...], list[PubMedRecord]]:
    """Convert :class:`AppraisalDecision` list to :class:`ScreeningDecision`s.

    Drops decisions with ``requires_review=True`` (they belong to a
    pending-review pile, not the final decision file). Returns the
    persisted decisions and the records that were included
    (``outcome == AppraisalOutcome.INCLUDE`` and not requires_review).

    Args:
        records: the records in the same order as ``appraisal_decisions``.
        appraisal_decisions: per-record decisions from
            :func:`run_screening`.
        decided_at: timestamp stamped onto each decision. Default
            ``datetime.now(UTC)``.
        decided_by: identifier stamped onto each decision.

    Returns:
        A pair ``(screening_decisions, eligible_records)``.
    """
    if len(records) != len(appraisal_decisions):
        raise OrchestratorError(
            f"records ({len(records)}) and appraisal_decisions "
            f"({len(appraisal_decisions)}) must have the same length"
        )
    if decided_at is None:
        decided_at = datetime.now(tz=UTC)

    screening_decisions: list[ScreeningDecision] = []
    eligible_records: list[PubMedRecord] = []

    for record, decision in zip(records, appraisal_decisions, strict=True):
        if decision.requires_review:
            continue
        if decision.outcome == AppraisalOutcome.INCLUDE:
            sd = ScreeningDecision(
                pmid=decision.pmid,
                phase=PrismaPhase.SCREENING,
                outcome="include",
                exclusion_code=None,
                rationale=decision.rationale,
                decided_at=decided_at,
                decided_by=decided_by,
            )
            screening_decisions.append(sd)
            eligible_records.append(record)
        else:  # EXCLUDE
            if decision.exclusion_code is None:
                raise OrchestratorError(
                    f"AppraisalDecision pmid={decision.pmid!r} has "
                    f"outcome=EXCLUDE but exclusion_code is None"
                )
            try:
                code = ExclusionCode(decision.exclusion_code)
            except ValueError as e:
                raise OrchestratorError(
                    f"AppraisalDecision pmid={decision.pmid!r} has unknown "
                    f"exclusion_code {decision.exclusion_code!r}"
                ) from e
            sd = ScreeningDecision(
                pmid=decision.pmid,
                phase=phase_for(code),
                outcome="exclude",
                exclusion_code=code,
                rationale=decision.rationale,
                decided_at=decided_at,
                decided_by=decided_by,
            )
            screening_decisions.append(sd)

    return tuple(screening_decisions), eligible_records


def run_appraisal(
    claim: MPCOClaim,
    eligible_records: list[PubMedRecord],
    config: ProjectConfig,
    *,
    a6_classifier: object | None = None,
) -> dict:
    """Dispatch eligible records to the configured appraisal lens.

    Returns ``dict[ClaimType, tuple[AppraisalResult, ...]]``. Values
    are tuples for :class:`MPCORenderContext` compatibility.

    Args:
        claim: claim under appraisal.
        eligible_records: records past screening + eligibility gates.
        config: project config (provides ``config.appraisal``).
        a6_classifier: optional :class:`MeddevA6Classifier` instance to
            inject into the MeddevA6Lens when it is constructed by the
            dispatcher's lens factory. When ``None``, the lens falls
            back to its :class:`NullA6Classifier` default and reports
            ``is_operational() == False`` — the dispatcher then emits
            :class:`PendingAppraisalResult` for each eligible record.
    """
    from ring2.adapters.mpco.appraisal.dispatcher import LensFactory
    from ring2.adapters.mpco.appraisal.meddev_a6 import MeddevA6Lens
    from ring2.adapters.mpco.appraisal.registry import get_lens

    factory: LensFactory | None = None
    if a6_classifier is not None:

        def factory_with_a6(name: str):  # type: ignore[no-untyped-def]
            cls = get_lens(name)
            if cls is MeddevA6Lens:
                return cls(classifier=a6_classifier)  # type: ignore[call-arg]
            return cls()

        factory = factory_with_a6

    dispatcher = AppraisalDispatcher(config.appraisal, lens_factory=factory)
    raw = dispatcher.dispatch(claim, eligible_records)
    return {ct: tuple(results) for ct, results in raw.items()}


def build_render_context(
    claim: MPCOClaim,
    state: SessionStateImpl,
    screening_decisions: tuple[ScreeningDecision, ...],
    appraisals: dict,
    *,
    identified_database: int,
) -> MPCORenderContext:
    """Assemble the :class:`MPCORenderContext` from pipeline outputs.

    Computes the PRISMA exclusion-count maps from the screening
    decisions, builds a balanced :class:`PrismaFlow`, and wires
    everything into a :class:`MPCORenderContext`.
    """
    excluded_screening: dict[str, int] = {}
    excluded_eligibility: dict[str, int] = {}
    for sd in screening_decisions:
        if sd.outcome != "exclude" or sd.exclusion_code is None:
            continue
        code_value = sd.exclusion_code.value
        if sd.phase == PrismaPhase.SCREENING:
            excluded_screening[code_value] = excluded_screening.get(code_value, 0) + 1
        elif sd.phase == PrismaPhase.ELIGIBILITY:
            excluded_eligibility[code_value] = excluded_eligibility.get(code_value, 0) + 1

    flow = build_flow(
        state,
        identified_database=identified_database,
        excluded_screening=excluded_screening,
        excluded_eligibility=excluded_eligibility,
    )

    return MPCORenderContext(
        claim=claim,
        decisions=screening_decisions,
        flow=flow,
        appraisals=appraisals,
    )


def write_report(
    artefact_content: str,
    output_dir: Path,
    claim_id: str,
) -> Path:
    """Write the rendered markdown report to ``output_dir``.

    Returns the path of the written file
    (``<output_dir>/<claim_id>_report.md``).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{claim_id}_report.md"
    report_path.write_text(artefact_content, encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run(
    project_config_path: Path,
    *,
    mcp_caller: MCPCaller | None = None,
    screener_caller: ScreenerCaller | None = None,
    a6_classifier: object | None = None,
    claude_client: object | None = None,
) -> OrchestratorRunResult:
    """Run the full end-to-end pipeline for one project YAML.

    Args:
        project_config_path: path to a project YAML.
        mcp_caller: PubMed MCP caller. Default :class:`NullMCPCaller`
            — fine when ``config.search`` is ``None``; required (real
            caller) when ``config.search`` is set.
        screener_caller: screening LLM caller. Default
            :class:`NullScreenerCaller` — fine for runs that have no
            records to screen; required (real caller) when records
            need screening. If ``None`` and ``claude_client`` is
            provided, a :class:`~ring2.llm.ClaudeScreener` is built
            automatically from the resolved claim.
        a6_classifier: optional :class:`MeddevA6Classifier`
            implementation to inject into the MeddevA6Lens. If ``None``
            and ``claude_client`` is provided, a
            :class:`~ring2.llm.ClaudeA6Classifier` is built
            automatically.
        claude_client: optional Claude client. When set, the
            orchestrator auto-wires :class:`ClaudeScreener` and
            :class:`ClaudeA6Classifier` for any slots that are not
            explicitly provided. Pass a :class:`ClaudeClient` for real
            API calls or a test fake for offline tests.

    Returns:
        An :class:`OrchestratorRunResult` summarising the run.

    Raises:
        OrchestratorError: on orchestrator-level errors.
        FileNotFoundError: missing files.
        pydantic.ValidationError: schema violations.
    """
    project_config_path = Path(project_config_path).resolve()
    base_dir = project_config_path.parent

    config = load_project_config(project_config_path)
    claim = resolve_claim(config, base_dir)

    # Resolve output_dir relative to project.yaml's parent.
    output_dir = config.output_dir
    if not output_dir.is_absolute():
        output_dir = base_dir / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Auto-wire Claude-based callers if a client is provided and the
    # corresponding caller slot is empty.
    if claude_client is not None:
        if screener_caller is None:
            from ring2.llm.claude_screener import ClaudeScreener

            screener_caller = ClaudeScreener(client=claude_client, claim=claim)  # type: ignore[arg-type]
        if a6_classifier is None:
            from ring2.llm.claude_a6_classifier import ClaudeA6Classifier

            a6_classifier = ClaudeA6Classifier(client=claude_client)  # type: ignore[arg-type]

    actual_mcp = mcp_caller or NullMCPCaller()
    actual_screener = screener_caller or NullScreenerCaller()

    # 1. Search (or resume).
    state = run_search(config, claim, output_dir, mcp_caller=actual_mcp)

    # 2. Load records from session batches.
    records = load_records_from_state(state)

    # 3. Screen each record.
    appraisal_decisions = run_screening(records, claim, screener_caller=actual_screener)

    # 4. Convert + split into persisted decisions + eligible records.
    screening_decisions, eligible_records = convert_decisions(records, appraisal_decisions)

    # 5. Persist decisions as a versioned sidecar YAML.
    if screening_decisions:
        write_decision_file(output_dir, claim.claim_id, screening_decisions)

    # 6. Dispatch eligible records to the configured appraisal lens.
    appraisals = run_appraisal(claim, eligible_records, config, a6_classifier=a6_classifier)

    # 7. Assemble render context.
    context = build_render_context(
        claim,
        state,
        screening_decisions,
        appraisals,
        identified_database=len(records),
    )

    # 8. Render the report.
    adapter = MPCOAdapter(caller=actual_screener)
    artefact = adapter.render_report(state, context=context)
    if artefact.content is None:
        raise OrchestratorError("Adapter render_report returned a ReportArtefact with no content")

    # 9. Write the report.
    report_path = write_report(artefact.content, output_dir, claim.claim_id)

    return OrchestratorRunResult(
        report_path=report_path,
        state=state,
        screening_decisions=screening_decisions,
        eligible_records_count=len(eligible_records),
    )

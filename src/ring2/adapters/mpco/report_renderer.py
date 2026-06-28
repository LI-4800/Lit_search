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
"""MPCO interim report renderer — Stufe 1.7.

Renders a markdown report from a :class:`SessionStateImpl`. The report is
**interim by design**: only state-derived sections are populated.
Claim-aware sections (regulatory anchors, inclusion/exclusion criteria,
PRISMA flow, per-record decisions, appraisal log, evidence synthesis)
are listed as explicit pending placeholders with the reason for each
deferral, awaiting orchestrator wire-up in Stufe 1.8.

Per the Weg-3 decision recorded at session start: rather than mutate the
:class:`Adapter.render_report` ABC signature or fabricate data, the
renderer is honest about the lifecycle stage it can reach from
:class:`SessionState` alone. Inputs:

    * :attr:`SessionStateImpl.project_id` — string id
    * :attr:`SessionStateImpl.claim_id` — string id (not the full claim)
    * :attr:`SessionStateImpl.session_dir` — for the audit footer
    * :attr:`SessionStateImpl.status_map` — per-PMID lifecycle flags
    * :attr:`SessionStateImpl.batch_files` — for the batch-files listing

The renderer performs **no I/O** (the batch files are listed by name,
not opened) and is deterministic given fixed inputs apart from the
generation timestamp.

Section layout (forward-compatible with Stufe 1.8+):

    §0  Status banner + intro
    §1  Session
    §2  Regulatory anchors                 [filled if context given]
    §3  Inclusion criteria                 [filled if context given]
    §4  Exclusion criteria                 [filled if context given]
    §5  PRISMA flow                        [filled if context given]
    §6  Records passed screening           [filled if context given]
    §7  Excluded records                   [filled if context given]
    §8  Appraisal log                      [PENDING — requires Stufe 1.8+ appraisal]
    §9  Evidence synthesis                 [PENDING — requires Stufe 1.8+ synthesis]
    §10 Lifecycle counts                   [RENDERED — from status_map]
    §11 Batch files                        [RENDERED — from batch_files]
    §12 Audit                              [RENDERED]

When §2-§9 are wired up in later stages, the section numbers remain
stable; the placeholders in this module are replaced one by one without
renumbering the tail (§10-§12).
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import metadata
from typing import TYPE_CHECKING

from ring2.adapters.mpco.appraisal.dispatcher import PendingAppraisalResult
from ring2.adapters.mpco.appraisal.registry import get_lens
from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.criteria_factory import (
    make_exclusion_criteria,
    make_inclusion_criteria,
)
from ring2.adapters.mpco.exclusion_codes import ExclusionCode, PrismaPhase, phase_for
from ring2.adapters.mpco.reg_722_2012 import elements_in_scope, regulatory_anchors
from ring2.core.adapter_base import ReportArtefact

if TYPE_CHECKING:
    from ring2.adapters.mpco.render_context import MPCORenderContext
    from ring2.core.session import SessionStateImpl

__all__ = [
    "STATUS_BANNER",
    "render_mpco_report",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Verbatim status banner used in §0. Stable string; tests assert on it.
STATUS_BANNER: str = "INTERIM — POST-SCREENING, PRE-APPRAISAL"


#: Intro paragraph rendered immediately under the status banner. Verbatim.
_INTRO: str = (
    "This report reflects the session lifecycle only. Claim-aware sections "
    "(inclusion/exclusion criteria, regulatory anchors, per-record "
    "decisions, PRISMA flow) are pending orchestrator wire-up in "
    "Stufe 1.8."
)


#: Pending-section placeholder text per section. Each value is the
#: verbatim reason shown to the report reader, so the reader understands
#: *why* the section is empty without consulting the handoff documents.
#:
#: After Stufe-1.8 Inkrement 5b, §2 / §3 / §4 / §5 / §6 / §7 are all
#: filled when an MPCORenderContext is provided; only §8 / §9 remain
#: pending (awaiting the appraisal modules introduced in later
#: increments).
_PENDING_SECTIONS_NO_CONTEXT: tuple[tuple[int, str, str], ...] = (
    (2, "Regulatory anchors", "requires claim pass-through"),
    (3, "Inclusion criteria", "requires claim pass-through"),
    (
        4,
        "Exclusion criteria",
        "claim-agnostic in practice, but ABC requires claim — deferred for consistency",
    ),
    (5, "PRISMA flow", "requires orchestrator wire-up"),
    (6, "Records passed screening", "requires decision persistence"),
    (7, "Excluded records", "requires decision persistence"),
    (8, "Appraisal log", "requires Stufe 1.8+ appraisal modules"),
    (9, "Evidence synthesis", "requires Stufe 1.8+ synthesis"),
)

#: Pending sections when a context IS provided. After Inkrement 3, §8
#: is filled from ``context.appraisals``; only §9 remains pending
#: (awaiting evidence synthesis, Stufe 1.10+).
_PENDING_SECTIONS_WITH_CONTEXT: tuple[tuple[int, str, str], ...] = (
    (9, "Evidence synthesis", "requires Stufe 1.8+ synthesis"),
)


#: Adapter identifier shown in §1. Matches :attr:`MPCOAdapter.name`.
_ADAPTER_NAME: str = "MPCO"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """ISO-8601 UTC timestamp with second resolution.

    Same shape as :func:`ring2.core.prisma._now_iso` for cross-artefact
    consistency (e.g. ``"2026-06-27T14:32:11Z"``).
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ring2_version() -> str:
    """Return the installed ``ring2`` package version, or ``"unknown"``.

    Single source of truth: ``pyproject.toml`` (read via
    :func:`importlib.metadata.version`). When the package is not
    installed (e.g. raw source-tree invocation), returns ``"unknown"``
    rather than raising — the renderer is non-critical infrastructure
    and should not fail because of a missing install.
    """
    try:
        return metadata.version("ring2")
    except metadata.PackageNotFoundError:
        return "unknown"


def _lifecycle_counts(state: SessionStateImpl) -> dict[str, int]:
    """Aggregate per-flag counts across all records in ``state.status_map``.

    Returns a dict with keys ``retrieved``, ``screened``, ``classified``,
    ``extracted``, ``complete``, ``incomplete``, ``total`` — each
    counting the records whose corresponding flag (or computed property)
    is true. Counts are inclusive: a fully-extracted record contributes
    1 to all of ``retrieved``, ``screened``, ``classified``,
    ``extracted``, ``complete`` and ``total``.
    """
    retrieved = screened = classified = extracted = complete = 0
    for info in state.status_map.values():
        if info.retrieved:
            retrieved += 1
        if info.screened:
            screened += 1
        if info.classified:
            classified += 1
        if info.extracted:
            extracted += 1
        if info.is_complete:
            complete += 1
    total = state.total_records
    return {
        "retrieved": retrieved,
        "screened": screened,
        "classified": classified,
        "extracted": extracted,
        "complete": complete,
        "incomplete": total - complete,
        "total": total,
    }


# ---------------------------------------------------------------------------
# Section renderers — Stufe-1.8 Inkrement 4
#
# Each _render_section_*() function returns a list of markdown lines
# (without a trailing blank line — the caller separates sections).
# All four are invoked only when an MPCORenderContext is provided.
# ---------------------------------------------------------------------------


def _render_section_2_regulatory_anchors(context: MPCORenderContext) -> list[str]:
    """§2 Regulatory anchors — verbatim citation strings + in-scope Annex-I elements.

    Only renders 722/2012 content when ``claim.applicable_regulation ==
    "722_2012"``. For other regulations (or ``"none"``), emits a short
    "Not applicable" line — the Annex-I machinery is regulation-specific
    by design.

    All citation strings are reproduced **verbatim** from
    :func:`regulatory_anchors`; order is part of the contract.
    """
    lines = ["## §2 Regulatory anchors", ""]
    claim = context.claim
    if claim.applicable_regulation != "722_2012":
        lines.append(
            f"_Not applicable — claim has `applicable_regulation = "
            f'"{claim.applicable_regulation}"`; the Annex-I anchors below '
            f"are 722/2012-specific._"
        )
        return lines

    in_scope = elements_in_scope(claim.claim_type)
    lines.append(
        f"In scope for claim type `{claim.claim_type.value}`: "
        f"{len(in_scope)} of 4 Annex-I element(s)."
    )
    lines.append("")
    if in_scope:
        lines.append("**Annex-I elements in scope** (declaration order):")
        lines.append("")
        # Sort for stable output (elements_in_scope returns a frozenset).
        for element in sorted(in_scope, key=lambda e: e.value):
            lines.append(f"- `{element.value}`")
        lines.append("")
    lines.append("**Verbatim regulatory anchors** (EU Regulation 722/2012):")
    lines.append("")
    for anchor in regulatory_anchors():
        # Anchors are pre-formatted citation strings; emit each as a
        # blockquote line so quoted markup in the anchor renders cleanly.
        lines.append(f"> {anchor}")
        lines.append(">")
    # Strip the trailing standalone "> " line for a clean section end.
    if lines and lines[-1] == ">":
        lines.pop()
    return lines


def _render_section_3_inclusion_criteria(context: MPCORenderContext) -> list[str]:
    """§3 Inclusion criteria — universal baseline + Annex-I criteria.

    Delegates to :func:`make_inclusion_criteria` (the pure factory) for
    the criterion set. Emits criteria flat in declaration order: the
    universal ``INC-001`` baseline first, then any
    ``INC-722-*`` criteria when ``applicable_regulation == "722_2012"``
    in :class:`AnnexIElement` declaration order.

    Descriptions are reproduced **verbatim** from the factory's
    :data:`ANNEX_I_DESCRIPTIONS` table.
    """
    lines = ["## §3 Inclusion criteria", ""]
    criteria = make_inclusion_criteria(context.claim)
    lines.append(f"Total: {len(criteria.criteria)} criterion(a).")
    lines.append("")
    for criterion in criteria.criteria:
        lines.append(f"- `{criterion.id}` — {criterion.description}")
    lines.append("")
    return lines


def _render_section_4_exclusion_criteria(context: MPCORenderContext) -> list[str]:
    """§4 Exclusion criteria — full 5-code set, grouped by PRISMA phase.

    Delegates to :func:`make_exclusion_criteria` (the pure factory) for
    the criterion set, then groups the resulting criteria by their
    :func:`phase_for` routing tag. Phase groups emit in
    :class:`PrismaPhase` declaration order (deduplication → screening →
    eligibility), matching the §5 PRISMA-flow layout for audit
    consistency.

    Descriptions are reproduced **verbatim** from the factory's
    :data:`EXCLUSION_DESCRIPTIONS` table.
    """
    lines = ["## §4 Exclusion criteria", ""]
    criteria = make_exclusion_criteria(context.claim)

    # Group emitted criteria by their PRISMA phase. Iterating once over
    # the factory output keeps the grouping consistent with whatever the
    # factory decides to emit (in case of future per-claim variation).
    grouped: dict[PrismaPhase, list] = {phase: [] for phase in PrismaPhase}
    for criterion in criteria.criteria:
        # criterion.code is the canonical hyphenated string ("EX-LANGUAGE"
        # etc.); convert back to the enum to look up the phase routing.
        code_enum = ExclusionCode(criterion.code)
        grouped[phase_for(code_enum)].append(criterion)

    # Emit phases in declaration order; skip phases with no criteria
    # (preserves clean output if a future variant drops a code).
    for phase in PrismaPhase:
        phase_criteria = grouped[phase]
        if not phase_criteria:
            continue
        lines.append(f"**{phase.value.capitalize()} phase**:")
        lines.append("")
        for criterion in phase_criteria:
            lines.append(f"- `{criterion.code}` — {criterion.description}")
        lines.append("")
    return lines


def _render_section_5_prisma_flow(context: MPCORenderContext) -> list[str]:
    """§5 PRISMA flow — counts per phase + per-code exclusion breakdown.

    Uses :class:`PrismaPhaseCounts` derived properties
    (``total_identified``, ``screened``, ``assessed_eligibility``,
    ``included``) for the headline tally, then breaks down the
    per-phase exclusion-code maps verbatim.
    """
    flow = context.flow
    counts = flow.counts
    lines = ["## §5 PRISMA flow", ""]
    lines.append(f"- Generated: {flow.generated_at}")
    lines.append(f"- Total identified (database + other): {counts.total_identified}")
    lines.append(f"  - Identified (database): {counts.identified_database}")
    lines.append(f"  - Identified (other sources): {counts.identified_other}")
    lines.append(f"- Duplicates removed: {counts.duplicates_removed}")
    lines.append(f"- Records screened: {counts.screened}")
    lines.append(f"- Records assessed for eligibility: {counts.assessed_eligibility}")
    lines.append(f"- Records included: {counts.included}")
    lines.append("")

    def _emit_breakdown(label: str, code_map: dict[str, int]) -> None:
        lines.append(f"**Excluded at {label}**:")
        lines.append("")
        if not code_map:
            lines.append("_None._")
            lines.append("")
            return
        # Sort by code for deterministic output.
        for code in sorted(code_map):
            lines.append(f"- `{code}`: {code_map[code]}")
        lines.append("")

    _emit_breakdown("screening", dict(counts.excluded_screening))
    _emit_breakdown("eligibility", dict(counts.excluded_eligibility))

    if flow.notes:
        lines.append("**Notes:**")
        lines.append("")
        for note in flow.notes:
            lines.append(f"- {note}")
        lines.append("")
    return lines


def _render_section_6_passed(context: MPCORenderContext) -> list[str]:
    """§6 Records passed screening — PMIDs of include-decisions, grouped by phase.

    Phases appear in canonical PRISMA order (deduplication →
    screening → eligibility). Within each phase, PMIDs are sorted
    lexicographically for deterministic output.
    """
    lines = ["## §6 Records passed screening", ""]
    includes = [d for d in context.decisions if d.outcome == "include"]
    if not includes:
        lines.append("_No records passed screening yet._")
        return lines

    by_phase: dict[PrismaPhase, list[str]] = {}
    for d in includes:
        by_phase.setdefault(d.phase, []).append(d.pmid)

    # Iterate phases in enum declaration order for determinism.
    for phase in PrismaPhase:
        pmids = by_phase.get(phase)
        if not pmids:
            continue
        lines.append(f"**Phase: {phase.value}** ({len(pmids)} record(s))")
        lines.append("")
        for pmid in sorted(pmids):
            lines.append(f"- `{pmid}`")
        lines.append("")
    return lines


def _render_section_7_excluded(context: MPCORenderContext) -> list[str]:
    """§7 Excluded records — PMIDs grouped by phase, then by exclusion code.

    Within each (phase, code) group, PMIDs are sorted lexicographically.
    The verbatim rationale is included beneath each PMID — these are
    audit-trail-critical and must not be paraphrased.
    """
    lines = ["## §7 Excluded records", ""]
    excludes = [d for d in context.decisions if d.outcome == "exclude"]
    if not excludes:
        lines.append("_No records excluded yet._")
        return lines

    # Group: phase -> exclusion_code.value -> list[ScreeningDecision]
    grouped: dict[PrismaPhase, dict[str, list]] = {}
    for d in excludes:
        # exclusion_code is guaranteed non-None by ScreeningDecision V1.
        assert d.exclusion_code is not None
        grouped.setdefault(d.phase, {}).setdefault(d.exclusion_code.value, []).append(d)

    for phase in PrismaPhase:
        by_code = grouped.get(phase)
        if not by_code:
            continue
        phase_count = sum(len(v) for v in by_code.values())
        lines.append(f"**Phase: {phase.value}** ({phase_count} record(s))")
        lines.append("")
        for code in sorted(by_code):
            decisions = sorted(by_code[code], key=lambda d: d.pmid)
            lines.append(f"_Code: `{code}`_ ({len(decisions)} record(s))")
            lines.append("")
            for d in decisions:
                lines.append(f"- `{d.pmid}` — {d.rationale}")
            lines.append("")
    return lines


def _render_section_8_appraisal_log(context: MPCORenderContext) -> list[str]:
    """§8 Appraisal log — render appraisal results per claim type.

    Behaviour:

    * Empty :attr:`MPCORenderContext.appraisals` → pending placeholder
      (matches the Stufe-1.8 §8 wording — context provided but
      appraisal phase has not been wired up yet).
    * Non-empty → one sub-section per claim type, ordered by the
      ClaimType enum's declaration order for determinism. Within each
      sub-section:

      - if **all** results in that sub-section are
        :class:`PendingAppraisalResult` instances (non-operational
        lens) → render the *"awaiting classifier/implementation"*
        block with the eligible-record PMIDs;
      - otherwise → delegate to ``lens.render_summary(results)`` via a
        registry-lookup + no-arg lens instantiation. The lens itself
        sets its own sub-section header (e.g. ``MeddevA6Lens`` emits
        ``### Lens: MEDDEV 2.7/1 Rev. 4 §A6``), so the renderer does
        not add an extra ``###`` line here.

    The mixed case (some results pending, others real, under the same
    claim type) should not be produced by
    :class:`~ring2.adapters.mpco.appraisal.dispatcher.AppraisalDispatcher`,
    which is all-or-nothing per dispatch. If it does occur (e.g. via
    hand-crafted context in tests), the renderer falls back to the
    real-results path — ``lens.render_summary`` will surface the
    inconsistency through its own type checks.
    """
    lines = ["## §8 Appraisal log", ""]

    if not context.appraisals:
        lines.append(
            "_Pending — requires the orchestrator to wire the appraisal "
            "dispatcher into context.appraisals (Stufe 1.9a Inkrement 4)._"
        )
        return lines

    # Iterate by ClaimType declaration order for determinism.
    for claim_type in ClaimType:
        results = context.appraisals.get(claim_type)
        if not results:
            continue

        all_pending = all(isinstance(r, PendingAppraisalResult) for r in results)
        # All results share the same lens_name when produced by the
        # dispatcher; fall back to "(mixed)" if hand-crafted tests
        # break that invariant.
        lens_names = {r.lens_name for r in results}
        lens_label = lens_names.pop() if len(lens_names) == 1 else "(mixed)"

        if all_pending:
            n = len(results)
            lines.append(f"### Lens: {lens_label} (claim type: {claim_type.value})")
            lines.append("")
            lines.append(
                f"_**Awaiting classifier/implementation** — {n} eligible "
                f"record(s) cannot be appraised yet. Scheduled: Stufe "
                f"1.9b / 1.10+._"
            )
            lines.append("")
            for pmid in sorted(r.pmid for r in results):
                lines.append(f"- `{pmid}`")
            lines.append("")
            continue

        # Real-results path: delegate to the lens's own summary
        # renderer. The lens supplies its own sub-section header.
        try:
            lens_cls = get_lens(lens_label)
        except KeyError:
            # Defensive: results carry a lens_name not in the registry.
            # Should not happen in production; surface clearly.
            lines.append(f"### Lens: {lens_label} (claim type: {claim_type.value})")
            lines.append("")
            lines.append(
                f"_Lens {lens_label!r} is not registered; cannot render "
                f"summary. {len(results)} result(s) suppressed._"
            )
            lines.append("")
            continue

        lens_instance = lens_cls()
        summary = lens_instance.render_summary(tuple(results))
        lines.extend(summary.splitlines())
        # Ensure a blank separator after the lens-supplied block, in case
        # render_summary did not append one.
        if lines and lines[-1] != "":
            lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_mpco_report(
    state: SessionStateImpl,
    context: MPCORenderContext | None = None,
) -> ReportArtefact:
    """Render the interim MPCO report for one session.

    Args:
        state: the per-claim session state to render. Only its public
            attributes (``project_id``, ``claim_id``, ``session_dir``,
            ``status_map``, ``batch_files``) are read.
        context: optional :class:`MPCORenderContext` carrying the
            ``MPCOClaim``, screening decisions, and PRISMA flow. When
            ``None``, all of §2-§9 render as PENDING (Stufe-1.7
            behaviour preserved verbatim). When provided, §2 / §3 / §4
            / §5 / §6 / §7 are filled from the context; only §8 / §9
            remain PENDING (awaiting the appraisal modules in later
            Stufe-1.8 increments).

    Returns:
        A :class:`ReportArtefact` with ``format="markdown"`` and
        ``content`` set to the rendered markdown string. ``path`` is
        not used — the renderer never writes to disk; serialising the
        artefact is the orchestrator's responsibility.
    """
    generated_at = _now_iso()
    version = _ring2_version()
    counts = _lifecycle_counts(state)

    lines: list[str] = []

    # ---- §0 Status banner + intro --------------------------------------
    lines.append("# RING2 MPCO Report — INTERIM")
    lines.append("")
    lines.append(f"**STATUS:** {STATUS_BANNER}")
    lines.append("")
    lines.append(_INTRO)
    lines.append("")

    # ---- §1 Session ----------------------------------------------------
    lines.append("## §1 Session")
    lines.append("")
    lines.append(f"- Project: `{state.project_id}`")
    lines.append(f"- Claim ID: `{state.claim_id}`")
    lines.append(f"- Adapter: {_ADAPTER_NAME}")
    lines.append(f"- Generated: {generated_at}")
    lines.append("")

    # ---- §2-§9 ---------------------------------------------------------
    # Two paths: with-context fills §2/§5/§6/§7; without keeps the
    # Stufe-1.7 pure-PENDING block. The two pending tuples encode the
    # difference in displayed reasons.
    if context is None:
        for num, title, reason in _PENDING_SECTIONS_NO_CONTEXT:
            lines.append(f"## §{num} {title}")
            lines.append("")
            lines.append(f"_Pending — {reason}._")
            lines.append("")
    else:
        # Filled sections.
        lines.extend(_render_section_2_regulatory_anchors(context))
        lines.append("")
        lines.extend(_render_section_3_inclusion_criteria(context))
        lines.append("")
        lines.extend(_render_section_4_exclusion_criteria(context))
        lines.append("")
        lines.extend(_render_section_5_prisma_flow(context))
        lines.append("")
        lines.extend(_render_section_6_passed(context))
        lines.append("")
        lines.extend(_render_section_7_excluded(context))
        lines.append("")
        lines.extend(_render_section_8_appraisal_log(context))
        lines.append("")
        # §9 — still pending.
        for num, title, reason in _PENDING_SECTIONS_WITH_CONTEXT:
            lines.append(f"## §{num} {title}")
            lines.append("")
            lines.append(f"_Pending — {reason}._")
            lines.append("")

    # ---- §10 Lifecycle counts ------------------------------------------
    lines.append("## §10 Lifecycle counts")
    lines.append("")
    lines.append(f"- Records retrieved: {counts['retrieved']}")
    lines.append(f"- Records screened: {counts['screened']}")
    lines.append(f"- Records classified: {counts['classified']}")
    lines.append(f"- Records extracted: {counts['extracted']}")
    lines.append(f"- Records complete: {counts['complete']}")
    lines.append(f"- Records incomplete: {counts['incomplete']}")
    lines.append(f"- Total: {counts['total']}")
    lines.append("")

    # ---- §11 Batch files ----------------------------------------------
    lines.append("## §11 Batch files")
    lines.append("")
    if state.batch_files:
        for path in state.batch_files:
            lines.append(f"- `{path.name}`")
    else:
        lines.append("_No batch files in session._")
    lines.append("")

    # ---- §12 Audit -----------------------------------------------------
    lines.append("## §12 Audit")
    lines.append("")
    lines.append(f"- RING2 version: {version}")
    lines.append(f"- MPCO adapter: {_ADAPTER_NAME}")
    lines.append(f"- Generated: {generated_at}")
    lines.append(f"- Session dir: `{state.session_dir}`")
    lines.append("")

    return ReportArtefact(format="markdown", content="\n".join(lines))

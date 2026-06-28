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
    §2  Regulatory anchors                 [PENDING — requires claim]
    §3  Inclusion criteria                 [PENDING — requires claim]
    §4  Exclusion criteria                 [PENDING — requires claim]
    §5  PRISMA flow                        [PENDING — requires orchestrator]
    §6  Records passed screening           [PENDING — requires decision persistence]
    §7  Excluded records                   [PENDING — requires decision persistence]
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

from ring2.core.adapter_base import ReportArtefact

if TYPE_CHECKING:
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
_PENDING_SECTIONS: tuple[tuple[int, str, str], ...] = (
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
# Public API
# ---------------------------------------------------------------------------


def render_mpco_report(state: SessionStateImpl) -> ReportArtefact:
    """Render the interim MPCO report for one session.

    Args:
        state: the per-claim session state to render. Only its public
            attributes (``project_id``, ``claim_id``, ``session_dir``,
            ``status_map``, ``batch_files``) are read.

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

    # ---- §2-§9 Pending sections ---------------------------------------
    for num, title, reason in _PENDING_SECTIONS:
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

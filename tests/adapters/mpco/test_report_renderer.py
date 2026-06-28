# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.report_renderer.

The Stufe-1.7 interim report renderer must:

    * produce a markdown :class:`ReportArtefact` with no path,
    * stamp the verbatim :data:`STATUS_BANNER` in §0,
    * carry ``project_id``, ``claim_id`` and ``session_dir`` through to
      the rendered sections,
    * render numbered pending placeholders for §2-§9 with the verbatim
      reason text,
    * aggregate lifecycle counts correctly from ``status_map``,
    * list batch files by name in insertion order,
    * stamp a ``ring2`` version derived from the installed package
      (or ``"unknown"`` if not installed),
    * be deterministic apart from the generation timestamp.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

from ring2.adapters.mpco.report_renderer import (
    STATUS_BANNER,
    render_mpco_report,
)
from ring2.core.adapter_base import ReportArtefact
from ring2.core.session import RecordStatusInfo, SessionStateImpl

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _empty_state(tmp_path: Path) -> SessionStateImpl:
    """A session state with zero records and zero batches."""
    return SessionStateImpl(
        project_id="OsteoGen-CER",
        claim_id="CB-bov-01",
        session_dir=tmp_path,
    )


def _populated_state(tmp_path: Path) -> SessionStateImpl:
    """A session state with a mix of lifecycle progress."""
    # 3 fully retrieved, 2 also screened, 1 also classified, 0 extracted.
    status_map = {
        "100": RecordStatusInfo(pmid="100", retrieved=True),
        "200": RecordStatusInfo(pmid="200", retrieved=True, screened=True),
        "300": RecordStatusInfo(pmid="300", retrieved=True, screened=True, classified=True),
    }
    return SessionStateImpl(
        project_id="OsteoGen-CER",
        claim_id="CB-bov-01",
        session_dir=tmp_path,
        status_map=status_map,
        batch_files=(
            tmp_path / "search_CB-bov-01_batch_00.yaml",
            tmp_path / "search_CB-bov-01_batch_01.yaml",
        ),
    )


# ---------------------------------------------------------------------------
# Artefact contract
# ---------------------------------------------------------------------------


def test_returns_report_artefact_with_markdown_content(tmp_path: Path) -> None:
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert isinstance(artefact, ReportArtefact)
    assert artefact.format == "markdown"
    assert artefact.content is not None
    assert artefact.path is None


def test_content_is_non_empty(tmp_path: Path) -> None:
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content
    assert len(artefact.content.splitlines()) > 20


# ---------------------------------------------------------------------------
# §0 Status banner + intro
# ---------------------------------------------------------------------------


def test_status_banner_is_verbatim_constant(tmp_path: Path) -> None:
    """Status banner string is the documented verbatim form."""
    assert STATUS_BANNER == "INTERIM — POST-SCREENING, PRE-APPRAISAL"
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert STATUS_BANNER in artefact.content


def test_top_level_heading_includes_interim_marker(tmp_path: Path) -> None:
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert artefact.content.startswith("# RING2 MPCO Report — INTERIM")


def test_intro_paragraph_mentions_stufe_18(tmp_path: Path) -> None:
    """The intro names the next stage so the reader knows when sections fill in."""
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert "Stufe 1.8" in artefact.content


# ---------------------------------------------------------------------------
# §1 Session
# ---------------------------------------------------------------------------


def test_session_section_contains_identifiers(tmp_path: Path) -> None:
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert "## §1 Session" in artefact.content
    assert "OsteoGen-CER" in artefact.content
    assert "CB-bov-01" in artefact.content


def test_session_section_names_mpco_adapter(tmp_path: Path) -> None:
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert "Adapter: MPCO" in artefact.content


def test_session_section_has_iso_timestamp(tmp_path: Path) -> None:
    """Generated timestamp matches the ISO-8601 UTC second-resolution pattern."""
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    # Pattern: 2026-06-27T14:32:11Z
    assert re.search(r"Generated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", artefact.content)


# ---------------------------------------------------------------------------
# §2-§9 Pending sections
# ---------------------------------------------------------------------------


def test_all_pending_section_headers_present(tmp_path: Path) -> None:
    """Sections §2-§9 are present in order, with their canonical titles."""
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    expected_headers = [
        "## §2 Regulatory anchors",
        "## §3 Inclusion criteria",
        "## §4 Exclusion criteria",
        "## §5 PRISMA flow",
        "## §6 Records passed screening",
        "## §7 Excluded records",
        "## §8 Appraisal log",
        "## §9 Evidence synthesis",
    ]
    last_index = -1
    for header in expected_headers:
        assert header in artefact.content, f"missing header: {header}"
        index = artefact.content.index(header)
        assert index > last_index, f"out-of-order header: {header}"
        last_index = index


def test_pending_sections_each_have_a_reason(tmp_path: Path) -> None:
    """Every pending section body starts with the verbatim reason marker."""
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    pending_section_count = artefact.content.count("_Pending — ")
    # 8 pending sections (§2 through §9)
    assert pending_section_count == 8


def test_pending_section_reasons_are_verbatim(tmp_path: Path) -> None:
    """Specific reason strings appear, matching the renderer's _PENDING_SECTIONS table."""
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert "_Pending — requires claim pass-through._" in artefact.content
    assert "_Pending — requires orchestrator wire-up._" in artefact.content
    assert "_Pending — requires decision persistence._" in artefact.content
    assert "_Pending — requires Stufe 1.8+ appraisal modules._" in artefact.content
    assert "_Pending — requires Stufe 1.8+ synthesis._" in artefact.content


# ---------------------------------------------------------------------------
# §10 Lifecycle counts
# ---------------------------------------------------------------------------


def test_lifecycle_counts_empty_state(tmp_path: Path) -> None:
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert "## §10 Lifecycle counts" in artefact.content
    assert "Records retrieved: 0" in artefact.content
    assert "Records screened: 0" in artefact.content
    assert "Records classified: 0" in artefact.content
    assert "Records extracted: 0" in artefact.content
    assert "Records complete: 0" in artefact.content
    assert "Records incomplete: 0" in artefact.content
    assert "Total: 0" in artefact.content


def test_lifecycle_counts_populated_state(tmp_path: Path) -> None:
    """Aggregation matches expected per-flag counts.

    Three records in the fixture:
        - 100: retrieved only            → contributes to retrieved
        - 200: retrieved + screened      → retrieved, screened
        - 300: retrieved + screened + classified
                                         → retrieved, screened, classified
    None are fully complete (none has extracted=True).
    """
    artefact = render_mpco_report(_populated_state(tmp_path))
    assert artefact.content is not None
    assert "Records retrieved: 3" in artefact.content
    assert "Records screened: 2" in artefact.content
    assert "Records classified: 1" in artefact.content
    assert "Records extracted: 0" in artefact.content
    assert "Records complete: 0" in artefact.content
    assert "Records incomplete: 3" in artefact.content
    assert "Total: 3" in artefact.content


def test_lifecycle_counts_with_one_complete_record(tmp_path: Path) -> None:
    """A fully extracted record counts as complete."""
    state = SessionStateImpl(
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        status_map={
            "1": RecordStatusInfo(
                pmid="1",
                retrieved=True,
                screened=True,
                classified=True,
                extracted=True,
            )
        },
    )
    artefact = render_mpco_report(state)
    assert artefact.content is not None
    assert "Records complete: 1" in artefact.content
    assert "Records incomplete: 0" in artefact.content


# ---------------------------------------------------------------------------
# §11 Batch files
# ---------------------------------------------------------------------------


def test_batch_files_section_empty_when_no_batches(tmp_path: Path) -> None:
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert "## §11 Batch files" in artefact.content
    assert "_No batch files in session._" in artefact.content


def test_batch_files_section_lists_filenames_only(tmp_path: Path) -> None:
    """Batch files are rendered by their basename, not absolute path."""
    artefact = render_mpco_report(_populated_state(tmp_path))
    assert artefact.content is not None
    assert "- `search_CB-bov-01_batch_00.yaml`" in artefact.content
    assert "- `search_CB-bov-01_batch_01.yaml`" in artefact.content
    # No absolute paths leaked.
    assert (
        str(tmp_path)
        not in (
            # Strip the audit footer's session-dir line out before checking; the
            # audit footer is allowed to show the full path.
            artefact.content.split("## §12 Audit")[0]
        )
    )


def test_batch_files_preserve_insertion_order(tmp_path: Path) -> None:
    """Batch files are emitted in the same order as the tuple."""
    state = SessionStateImpl(
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        batch_files=(
            tmp_path / "search_C_batch_02.yaml",
            tmp_path / "search_C_batch_00.yaml",
            tmp_path / "search_C_batch_01.yaml",
        ),
    )
    artefact = render_mpco_report(state)
    assert artefact.content is not None
    section = artefact.content.split("## §11 Batch files")[1].split("## §12 Audit")[0]
    idx_02 = section.index("batch_02")
    idx_00 = section.index("batch_00")
    idx_01 = section.index("batch_01")
    assert idx_02 < idx_00 < idx_01


# ---------------------------------------------------------------------------
# §12 Audit
# ---------------------------------------------------------------------------


def test_audit_section_present(tmp_path: Path) -> None:
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert "## §12 Audit" in artefact.content
    assert "MPCO adapter: MPCO" in artefact.content


def test_audit_section_includes_session_dir(tmp_path: Path) -> None:
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert f"Session dir: `{tmp_path}`" in artefact.content


def test_audit_section_shows_ring2_version(tmp_path: Path) -> None:
    """When ring2 is installed, its version appears verbatim.

    The string ``"RING2 version: "`` is followed either by a real
    version (e.g. ``0.0.1``) or the fallback ``"unknown"``.
    """
    artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    match = re.search(r"RING2 version: (\S+)", artefact.content)
    assert match is not None
    version = match.group(1)
    # Either a PEP-440 plausible version, or the unknown fallback.
    assert version == "unknown" or re.match(r"\d+\.\d+", version)


def test_audit_section_falls_back_to_unknown_when_package_missing(
    tmp_path: Path,
) -> None:
    """Missing installed metadata yields ``RING2 version: unknown`` — no exception."""
    import importlib.metadata as md

    with mock.patch.object(md, "version", side_effect=md.PackageNotFoundError("ring2")):
        artefact = render_mpco_report(_empty_state(tmp_path))
    assert artefact.content is not None
    assert "RING2 version: unknown" in artefact.content


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_renderer_is_deterministic_apart_from_timestamp(tmp_path: Path) -> None:
    """Two renders with the same state differ only in the timestamp lines."""
    state = _populated_state(tmp_path)
    a = render_mpco_report(state).content
    b = render_mpco_report(state).content
    assert a is not None and b is not None

    def _strip_timestamps(text: str) -> str:
        return re.sub(
            r"Generated: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            "Generated: <TIMESTAMP>",
            text,
        )

    assert _strip_timestamps(a) == _strip_timestamps(b)


def test_renderer_performs_no_io(tmp_path: Path) -> None:
    """The renderer must not open the batch files (they're listed by name only).

    We point ``batch_files`` at non-existent paths; if the renderer
    opens them, it would raise. If it merely lists their basenames, the
    call succeeds.
    """
    state = SessionStateImpl(
        project_id="P",
        claim_id="C",
        session_dir=tmp_path,
        batch_files=(
            tmp_path / "does_not_exist_batch_00.yaml",
            tmp_path / "does_not_exist_batch_01.yaml",
        ),
    )
    artefact = render_mpco_report(state)
    assert artefact.content is not None
    assert "does_not_exist_batch_00.yaml" in artefact.content

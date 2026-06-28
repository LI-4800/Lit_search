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


# ===========================================================================
# Stufe-1.8 Inkrement 4 — context-aware rendering
#
# When an MPCORenderContext is provided, §2 / §5 / §6 / §7 are filled.
# §3 / §4 remain PENDING with a revised reason; §8 / §9 unchanged.
# ===========================================================================

from datetime import UTC, datetime  # noqa: E402

from ring2.adapters.mpco.claim_type_classifier import ClaimType  # noqa: E402
from ring2.adapters.mpco.decision_persistence import ScreeningDecision  # noqa: E402
from ring2.adapters.mpco.exclusion_codes import ExclusionCode, PrismaPhase  # noqa: E402
from ring2.adapters.mpco.render_context import MPCORenderContext  # noqa: E402
from ring2.adapters.mpco.schema import (  # noqa: E402
    Comparator,
    Material,
    MPCOClaim,
    Outcome,
    Property,
)
from ring2.adapters.mpco.table_mapping import CellRef  # noqa: E402
from ring2.core.prisma import PrismaFlow, PrismaPhaseCounts  # noqa: E402

_UTC_NOW = datetime(2026, 6, 27, 14, 23, 0, tzinfo=UTC)


def _ctx(
    claim_id: str = "CB-bov-01",
    applicable_regulation: str = "722_2012",
    claim_type: ClaimType = ClaimType.UNKNOWN,
    decisions: tuple[ScreeningDecision, ...] = (),
    excluded_screening: dict[str, int] | None = None,
    excluded_eligibility: dict[str, int] | None = None,
    notes: tuple[str, ...] = (),
) -> MPCORenderContext:
    claim = MPCOClaim(
        claim_id=claim_id,
        source_table_cell=CellRef(
            workbook="Comparator-Tables.xlsx",
            sheet="Bovine-Collagen",
            row=4,
            column_label="Pepsin",
        ),
        material=Material(description="Bovine-derived collagen"),
        property=Property(description="Biocompatibility"),
        comparator=Comparator(description="Porcine-derived collagen"),
        outcome=Outcome(description="Inflammatory response"),
        applicable_regulation=applicable_regulation,  # type: ignore[arg-type]
        claim_type=claim_type,
    )
    counts = PrismaPhaseCounts(
        identified_database=100,
        identified_other=0,
        duplicates_removed=5,
        excluded_screening=excluded_screening or {},
        excluded_eligibility=excluded_eligibility or {},
    )
    flow = PrismaFlow(
        counts=counts,
        project_id="722-Retro",
        claim_id=claim_id,
        generated_at="2026-06-27T14:23:00Z",
        notes=notes,
    )
    return MPCORenderContext(claim=claim, decisions=decisions, flow=flow)


def _state(tmp_path: Path) -> SessionStateImpl:
    return SessionStateImpl(
        project_id="722-Retro",
        claim_id="CB-bov-01",
        session_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# §2 Regulatory anchors
# ---------------------------------------------------------------------------


def test_context_none_preserves_stufe_1_7_pending(tmp_path: Path) -> None:
    """Context=None: §2-§9 all PENDING with original reasons (Stufe-1.7 invariant)."""
    artefact = render_mpco_report(_state(tmp_path))
    assert artefact.content is not None
    # Spot-check verbatim Stufe-1.7 pending reasons preserved.
    assert "_Pending — requires claim pass-through._" in artefact.content
    assert "_Pending — requires orchestrator wire-up._" in artefact.content
    assert "_Pending — requires decision persistence._" in artefact.content


def test_context_given_section_2_renders_722_anchors(tmp_path: Path) -> None:
    """§2 renders verbatim 722/2012 anchors when applicable_regulation matches."""
    artefact = render_mpco_report(_state(tmp_path), context=_ctx())
    assert artefact.content is not None
    # The verbatim anchor strings come from regulatory_anchors() —
    # we assert one stable fragment to confirm verbatim emission.
    assert "722/2012" in artefact.content
    assert "## §2 Regulatory anchors" in artefact.content
    # In-scope Annex-I header present.
    assert "Annex-I element" in artefact.content
    # §2 is no longer PENDING.
    assert "## §2" in artefact.content
    assert "_Pending — requires claim pass-through._" not in artefact.content


def test_context_given_section_2_non_722_emits_not_applicable(tmp_path: Path) -> None:
    """When applicable_regulation != '722_2012', §2 emits a 'Not applicable' line."""
    ctx = _ctx(applicable_regulation="none")
    artefact = render_mpco_report(_state(tmp_path), context=ctx)
    assert artefact.content is not None
    assert "Not applicable" in artefact.content


# ---------------------------------------------------------------------------
# §3 Inclusion criteria — filled when context given
# ---------------------------------------------------------------------------


def test_context_given_section_3_renders_universal_baseline(tmp_path: Path) -> None:
    """§3 always includes the universal `INC-001` baseline, regardless of regulation."""
    # claim_type=UNKNOWN means elements_in_scope is empty even under 722/2012,
    # so the only inclusion criterion is the baseline.
    artefact = render_mpco_report(_state(tmp_path), context=_ctx(claim_type=ClaimType.UNKNOWN))
    assert artefact.content is not None
    c = artefact.content
    assert "## §3 Inclusion criteria" in c
    assert "Total: 1 criterion(a)." in c
    assert "`INC-001` — Evidence is relevant to the MPCO claim under appraisal." in c


def test_context_given_section_3_renders_annex_i_criteria_for_722_claim(tmp_path: Path) -> None:
    """§3 emits Annex-I criteria when applicable_regulation is 722_2012 + in-scope claim_type."""
    ctx = _ctx(applicable_regulation="722_2012", claim_type=ClaimType.SAFETY_ALLERGENICITY)
    artefact = render_mpco_report(_state(tmp_path), context=ctx)
    assert artefact.content is not None
    c = artefact.content
    assert "## §3 Inclusion criteria" in c
    # SAFETY_ALLERGENICITY has 2 Annex-I elements in scope (geographic-origin,
    # tse-risk-assessment), so total = 1 baseline + 2 Annex-I = 3.
    assert "Total: 3 criterion(a)." in c
    assert "`INC-001`" in c
    assert "`INC-722-GEOGRAPHIC-ORIGIN` — Evidence addresses geographic origin" in c
    assert "`INC-722-TSE-RISK-ASSESSMENT` — Evidence addresses TSE risk assessment" in c
    # Verbatim regulatory anchor on each Annex-I criterion.
    assert "per Regulation (EU) No 722/2012, Annex I." in c


def test_context_given_section_3_non_722_only_baseline(tmp_path: Path) -> None:
    """§3 for non-722 claim: only the baseline (no Annex-I machinery)."""
    artefact = render_mpco_report(
        _state(tmp_path),
        context=_ctx(applicable_regulation="none", claim_type=ClaimType.SAFETY_ALLERGENICITY),
    )
    assert artefact.content is not None
    c = artefact.content
    assert "## §3 Inclusion criteria" in c
    assert "Total: 1 criterion(a)." in c
    assert "`INC-001`" in c
    assert "INC-722-" not in c


# ---------------------------------------------------------------------------
# §4 Exclusion criteria — filled, grouped by PRISMA phase
# ---------------------------------------------------------------------------


def test_context_given_section_4_renders_all_five_codes(tmp_path: Path) -> None:
    """§4 emits all 5 exclusion codes with their verbatim descriptions."""
    artefact = render_mpco_report(_state(tmp_path), context=_ctx())
    assert artefact.content is not None
    c = artefact.content
    assert "## §4 Exclusion criteria" in c
    # All five canonical code strings appear in the section.
    for code in ExclusionCode:
        assert f"`{code.value}`" in c


def test_context_given_section_4_grouped_by_prisma_phase(tmp_path: Path) -> None:
    """§4 groups exclusion criteria by PRISMA phase in declaration order."""
    artefact = render_mpco_report(_state(tmp_path), context=_ctx())
    assert artefact.content is not None
    c = artefact.content
    # Three phase headers must be present.
    assert "**Deduplication phase**:" in c
    assert "**Screening phase**:" in c
    assert "**Eligibility phase**:" in c
    # Phases appear in PrismaPhase declaration order.
    idx_dedup = c.index("**Deduplication phase**:")
    idx_screen = c.index("**Screening phase**:")
    idx_elig = c.index("**Eligibility phase**:")
    assert idx_dedup < idx_screen < idx_elig
    # Spot-check: EX-DUPLICATE lives under the deduplication phase header,
    # not under another. Slice from "Deduplication phase" up to "Screening
    # phase" and assert EX-DUPLICATE is in that window.
    dedup_section = c[idx_dedup:idx_screen]
    assert "`EX-DUPLICATE`" in dedup_section
    screening_section = c[idx_screen:idx_elig]
    assert "`EX-LANGUAGE`" in screening_section
    assert "`EX-IRRELEVANT`" in screening_section
    # Eligibility section continues to end of §4 — check both eligibility codes there.
    eligibility_section = c[idx_elig:]
    assert "`EX-NO-FULLTEXT`" in eligibility_section
    assert "`EX-A6-CATALOG`" in eligibility_section


# ---------------------------------------------------------------------------
# §3 / §4 no-longer-PENDING regression
# ---------------------------------------------------------------------------


def test_context_given_section_3_4_no_longer_pending(tmp_path: Path) -> None:
    """After Inkrement 5b, §3 / §4 are no longer PENDING when a context is given.

    Regression guard: the old "criteria-factory extraction deferred" reason
    must not appear when the context is provided.
    """
    artefact = render_mpco_report(_state(tmp_path), context=_ctx())
    assert artefact.content is not None
    c = artefact.content
    assert "criteria-factory extraction deferred" not in c
    # And neither §3 nor §4 emits a "_Pending — …_" line when context is given.
    # Slice out the §3 and §4 sections and confirm they contain no pending marker.
    idx_3 = c.index("## §3 Inclusion criteria")
    idx_4 = c.index("## §4 Exclusion criteria")
    idx_5 = c.index("## §5 PRISMA flow")
    assert "_Pending —" not in c[idx_3:idx_4]
    assert "_Pending —" not in c[idx_4:idx_5]


# ---------------------------------------------------------------------------
# §5 PRISMA flow
# ---------------------------------------------------------------------------


def test_context_given_section_5_renders_counts(tmp_path: Path) -> None:
    """§5 renders headline tally + per-code breakdowns."""
    ctx = _ctx(
        excluded_screening={"EX-IRRELEVANT": 60, "EX-LANGUAGE": 5},
        excluded_eligibility={"EX-A6-CATALOG": 20},
    )
    artefact = render_mpco_report(_state(tmp_path), context=ctx)
    assert artefact.content is not None
    c = artefact.content
    assert "## §5 PRISMA flow" in c
    assert "Total identified (database + other): 100" in c
    assert "Duplicates removed: 5" in c
    # screened = 100 - 5 = 95
    assert "Records screened: 95" in c
    # assessed_eligibility = 95 - 60 - 5 = 30
    assert "Records assessed for eligibility: 30" in c
    # included = 30 - 20 = 10
    assert "Records included: 10" in c
    # Per-code breakdowns
    assert "`EX-IRRELEVANT`: 60" in c
    assert "`EX-LANGUAGE`: 5" in c
    assert "`EX-A6-CATALOG`: 20" in c


def test_context_given_section_5_emits_notes_when_present(tmp_path: Path) -> None:
    ctx = _ctx(notes=("UNKLAR-C3: Pepsin sheet count discrepancy",))
    artefact = render_mpco_report(_state(tmp_path), context=ctx)
    assert artefact.content is not None
    assert "**Notes:**" in artefact.content
    assert "UNKLAR-C3" in artefact.content


def test_context_given_section_5_no_exclusions_emits_none(tmp_path: Path) -> None:
    artefact = render_mpco_report(_state(tmp_path), context=_ctx())
    assert artefact.content is not None
    # With empty excluded_screening/eligibility, _None._ placeholder shows.
    assert "**Excluded at screening**:" in artefact.content
    assert "_None._" in artefact.content


# ---------------------------------------------------------------------------
# §6 PASSED records
# ---------------------------------------------------------------------------


def _inc(pmid: str, phase: PrismaPhase = PrismaPhase.SCREENING) -> ScreeningDecision:
    return ScreeningDecision(
        pmid=pmid,
        phase=phase,
        outcome="include",
        exclusion_code=None,
        rationale="on topic",
        decided_at=_UTC_NOW,
        decided_by="screener:test",
    )


def _exc(
    pmid: str,
    code: ExclusionCode = ExclusionCode.IRRELEVANT,
    phase: PrismaPhase = PrismaPhase.SCREENING,
    rationale: str = "off topic",
) -> ScreeningDecision:
    return ScreeningDecision(
        pmid=pmid,
        phase=phase,
        outcome="exclude",
        exclusion_code=code,
        rationale=rationale,
        decided_at=_UTC_NOW,
        decided_by="screener:test",
    )


def test_context_given_section_6_lists_pmids_grouped_by_phase(tmp_path: Path) -> None:
    decisions = (
        _inc("11111111", PrismaPhase.SCREENING),
        _inc("22222222", PrismaPhase.ELIGIBILITY),
        _inc("33333333", PrismaPhase.SCREENING),
    )
    artefact = render_mpco_report(_state(tmp_path), context=_ctx(decisions=decisions))
    assert artefact.content is not None
    c = artefact.content
    assert "## §6 Records passed screening" in c
    assert "**Phase: screening** (2 record(s))" in c
    assert "**Phase: eligibility** (1 record(s))" in c
    # PMIDs sorted lexicographically within phase.
    screening_block = c.split("**Phase: screening**")[1].split("**Phase:")[0]
    assert screening_block.index("11111111") < screening_block.index("33333333")


def test_context_given_section_6_empty_emits_none(tmp_path: Path) -> None:
    artefact = render_mpco_report(_state(tmp_path), context=_ctx())
    assert artefact.content is not None
    assert "_No records passed screening yet._" in artefact.content


# ---------------------------------------------------------------------------
# §7 EXCLUDED records
# ---------------------------------------------------------------------------


def test_context_given_section_7_groups_by_phase_then_code(tmp_path: Path) -> None:
    decisions = (
        _exc("AA", ExclusionCode.IRRELEVANT, PrismaPhase.SCREENING, "off topic A"),
        _exc("BB", ExclusionCode.LANGUAGE, PrismaPhase.SCREENING, "non-English B"),
        _exc("CC", ExclusionCode.IRRELEVANT, PrismaPhase.SCREENING, "off topic C"),
        _exc("DD", ExclusionCode.A6_CATALOG, PrismaPhase.ELIGIBILITY, "A6 cat-a missing"),
    )
    artefact = render_mpco_report(_state(tmp_path), context=_ctx(decisions=decisions))
    assert artefact.content is not None
    c = artefact.content
    assert "## §7 Excluded records" in c
    assert "**Phase: screening** (3 record(s))" in c
    assert "**Phase: eligibility** (1 record(s))" in c
    assert "_Code: `EX-IRRELEVANT`_ (2 record(s))" in c
    assert "_Code: `EX-LANGUAGE`_ (1 record(s))" in c
    assert "_Code: `EX-A6-CATALOG`_ (1 record(s))" in c
    # Verbatim rationale present (not paraphrased).
    assert "A6 cat-a missing" in c


def test_context_given_section_7_empty_emits_none(tmp_path: Path) -> None:
    artefact = render_mpco_report(_state(tmp_path), context=_ctx())
    assert artefact.content is not None
    assert "_No records excluded yet._" in artefact.content


# ---------------------------------------------------------------------------
# §8 / §9 + section ordering invariants
# ---------------------------------------------------------------------------


def test_context_given_sections_8_9_still_pending(tmp_path: Path) -> None:
    artefact = render_mpco_report(_state(tmp_path), context=_ctx())
    assert artefact.content is not None
    assert "## §8 Appraisal log" in artefact.content
    assert "## §9 Evidence synthesis" in artefact.content
    assert "requires Stufe 1.8+ appraisal modules" in artefact.content


def test_context_given_section_order_is_numeric(tmp_path: Path) -> None:
    """All sections appear in §1, §2, §3, §4, §5, §6, §7, §8, §9, §10, §11, §12 order."""
    artefact = render_mpco_report(_state(tmp_path), context=_ctx())
    assert artefact.content is not None
    c = artefact.content
    positions = [c.index(f"## §{n} ") for n in range(1, 13)]
    assert positions == sorted(positions)

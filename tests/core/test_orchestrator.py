# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for :mod:`ring2.core.orchestrator` — step helpers + end-to-end."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.decision_persistence import ScreeningDecision
from ring2.adapters.mpco.exclusion_codes import ExclusionCode, PrismaPhase
from ring2.adapters.mpco.schema import (
    Comparator,
    Material,
    MPCOClaim,
    Outcome,
    Property,
)
from ring2.adapters.mpco.table_mapping import CellRef
from ring2.core.adapter_base import AppraisalDecision, AppraisalOutcome, PubMedRecord
from ring2.core.orchestrator import (
    OrchestratorError,
    OrchestratorRunResult,
    build_render_context,
    convert_decisions,
    load_records_from_state,
    run,
    run_appraisal,
    run_screening,
    run_search,
    write_report,
)
from ring2.core.session import resume_state

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeScreenerCaller:
    """Deterministic screener — returns pre-baked decisions per PMID.

    The dict maps each PMID to a dict shaped like the production
    screener's reply: ``{outcome, exclusion_code?, rationale, confidence}``.
    Unknown PMIDs default to ``include`` so a test that only enumerates
    EXCLUDE cases need not enumerate every PMID.
    """

    replies: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def assess(
        self,
        *,
        record_view: dict[str, Any],
        inclusion: list[dict[str, str]],
        exclusion: list[dict[str, str]],
    ) -> dict[str, Any]:
        pmid = record_view.get("pmid", "?")
        self.calls.append(str(pmid))
        reply = self.replies.get(
            str(pmid),
            {
                "outcome": "include",
                "rationale": "fake screener: default include",
                "confidence": 0.9,
            },
        )
        # Defensive copy so the test fixture doesn't get mutated.
        return dict(reply)


# ---------------------------------------------------------------------------
# Common fixtures
# ---------------------------------------------------------------------------


def _claim(
    claim_id: str = "CB-bov-01",
    claim_type: ClaimType = ClaimType.CLINICAL_PERFORMANCE,
) -> MPCOClaim:
    return MPCOClaim(
        claim_id=claim_id,
        source_table_cell=CellRef(
            workbook="Comparator-Tables.xlsx",
            sheet="Bovine-Collagen",
            row=4,
            column_label="Pepsin",
        ),
        material=Material(description="Bovine-derived collagen"),
        property=Property(description="Biocompatibility"),
        comparator=Comparator(description="Porcine collagen"),
        outcome=Outcome(description="Inflammatory response"),
        applicable_regulation="722_2012",
        claim_type=claim_type,
    )


def _record(pmid: str, title: str = "A title", abstract: str | None = None) -> PubMedRecord:
    return PubMedRecord(pmid=pmid, title=title, abstract=abstract or "")


def _record_dict(pmid: str, title: str = "A title") -> dict[str, Any]:
    return {
        "pmid": pmid,
        "title": title,
        "doi": None,
        "abstract": "Some abstract text.",
        "journal": "J. Test",
        "year": 2024,
        "authors": ["Author A", "Author B"],
        "publication_types": [],
        "raw": {},
        "retrieved": True,
        "screened": False,
        "classified": False,
        "extracted": False,
    }


def _write_batch(session_dir: Path, batch_num: int, claim_id: str, records: list[dict]) -> Path:
    """Write a batch file under the canonical name so resume_state finds it."""
    from ring2.core.persistence import save_batch

    session_dir.mkdir(parents=True, exist_ok=True)
    return save_batch(session_dir, claim_id, batch_num, records)


def _project_yaml_inline(
    output_dir_str: str,
    claim_type: str = "clinical_performance",
    applicable_regulation: str = "722_2012",
) -> str:
    return f"""\
name: CB-bov-01
output_dir: {output_dir_str}
claim:
  claim_id: CB-bov-01
  source_table_cell:
    workbook: Comparator-Tables.xlsx
    sheet: Bovine-Collagen
    row: 4
    column_label: Pepsin
  material:
    description: Bovine-derived collagen
  property:
    description: Biocompatibility
  comparator:
    description: Porcine collagen
  outcome:
    description: Inflammatory response
  applicable_regulation: "{applicable_regulation}"
  claim_type: {claim_type}
appraisal:
  biochemistry_material_property:
    lens: glp_oecd
  safety_allergenicity:
    lens: care_caseseries
  clinical_performance:
    lens: meddev_a6
  historical_market_use:
    lens: registry_authoritativeness
"""


# ===========================================================================
# Step helpers — unit tests
# ===========================================================================


# ---- run_search ------------------------------------------------------------


class TestRunSearch:
    def test_skips_search_when_no_search_config(self, tmp_path: Path) -> None:
        """config.search=None → resume_state path, no MCP calls."""
        from ring2.core.project_config import (
            AppraisalConfig,
            AppraisalLensSelection,
            ProjectConfig,
        )
        from ring2.core.pubmed_client import NullMCPCaller

        config = ProjectConfig(
            name="P1",
            claim=_claim(),
            appraisal=AppraisalConfig(
                biochemistry_material_property=AppraisalLensSelection(lens="glp_oecd"),
                safety_allergenicity=AppraisalLensSelection(lens="care_caseseries"),
                clinical_performance=AppraisalLensSelection(lens="meddev_a6"),
                historical_market_use=AppraisalLensSelection(lens="registry_authoritativeness"),
            ),
            output_dir=tmp_path,
        )
        # No batches → empty status_map but valid state.
        state = run_search(config, _claim(), tmp_path, mcp_caller=NullMCPCaller())
        assert state.project_id == "P1"
        assert state.claim_id == "CB-bov-01"
        assert state.total_records == 0


# ---- load_records_from_state ----------------------------------------------


class TestLoadRecordsFromState:
    def test_loads_records_from_one_batch(self, tmp_path: Path) -> None:
        _write_batch(
            tmp_path,
            1,
            "CB-bov-01",
            [_record_dict("111"), _record_dict("222")],
        )
        state = resume_state(tmp_path, project_id="P1", claim_id="CB-bov-01")
        records = load_records_from_state(state)
        assert {r.pmid for r in records} == {"111", "222"}

    def test_loads_records_across_multiple_batches(self, tmp_path: Path) -> None:
        _write_batch(tmp_path, 1, "CB-bov-01", [_record_dict("111")])
        _write_batch(tmp_path, 2, "CB-bov-01", [_record_dict("222")])
        state = resume_state(tmp_path, project_id="P1", claim_id="CB-bov-01")
        records = load_records_from_state(state)
        assert {r.pmid for r in records} == {"111", "222"}

    def test_dedup_keeps_last_occurrence(self, tmp_path: Path) -> None:
        _write_batch(tmp_path, 1, "CB-bov-01", [_record_dict("111", "Old title")])
        _write_batch(tmp_path, 2, "CB-bov-01", [_record_dict("111", "New title")])
        state = resume_state(tmp_path, project_id="P1", claim_id="CB-bov-01")
        records = load_records_from_state(state)
        assert len(records) == 1
        assert records[0].title == "New title"

    def test_empty_session_returns_empty_list(self, tmp_path: Path) -> None:
        state = resume_state(tmp_path, project_id="P1", claim_id="CB-bov-01")
        records = load_records_from_state(state)
        assert records == []


# ---- run_screening ---------------------------------------------------------


class TestRunScreening:
    def test_screens_each_record(self) -> None:
        records = [_record("111", abstract="x"), _record("222", abstract="x")]
        caller = FakeScreenerCaller()
        decisions = run_screening(records, _claim(), screener_caller=caller)
        assert len(decisions) == 2
        # All defaults → include.
        assert all(d.outcome == AppraisalOutcome.INCLUDE for d in decisions)
        # screen_record performs up to two passes per record (title-only +
        # title+abstract) — verify each PMID was visited at least once.
        assert set(caller.calls) == {"111", "222"}


# ---- convert_decisions -----------------------------------------------------


class TestConvertDecisions:
    def test_include_becomes_screening_include(self) -> None:
        records = [_record("111")]
        decisions = [
            AppraisalDecision(
                pmid="111",
                outcome=AppraisalOutcome.INCLUDE,
                exclusion_code=None,
                rationale="ok",
            )
        ]
        screening, eligible = convert_decisions(records, decisions)
        assert len(screening) == 1
        assert screening[0].outcome == "include"
        assert screening[0].phase == PrismaPhase.SCREENING
        assert eligible == records

    def test_exclude_becomes_screening_exclude_with_routed_phase(self) -> None:
        records = [_record("111"), _record("222")]
        decisions = [
            AppraisalDecision(
                pmid="111",
                outcome=AppraisalOutcome.EXCLUDE,
                exclusion_code=ExclusionCode.IRRELEVANT.value,
                rationale="off-topic",
            ),
            AppraisalDecision(
                pmid="222",
                outcome=AppraisalOutcome.EXCLUDE,
                exclusion_code=ExclusionCode.NO_FULLTEXT.value,
                rationale="abstract only",
            ),
        ]
        screening, eligible = convert_decisions(records, decisions)
        assert len(screening) == 2
        assert eligible == []
        # IRRELEVANT routes to SCREENING; NO_FULLTEXT routes to ELIGIBILITY.
        by_pmid = {sd.pmid: sd for sd in screening}
        assert by_pmid["111"].phase == PrismaPhase.SCREENING
        assert by_pmid["111"].exclusion_code == ExclusionCode.IRRELEVANT
        assert by_pmid["222"].phase == PrismaPhase.ELIGIBILITY
        assert by_pmid["222"].exclusion_code == ExclusionCode.NO_FULLTEXT

    def test_requires_review_is_dropped(self) -> None:
        records = [_record("111")]
        decisions = [
            AppraisalDecision(
                pmid="111",
                outcome=AppraisalOutcome.INCLUDE,
                exclusion_code=None,
                rationale="needs human",
                requires_review=True,
            )
        ]
        screening, eligible = convert_decisions(records, decisions)
        assert screening == ()
        assert eligible == []

    def test_unknown_exclusion_code_raises(self) -> None:
        records = [_record("111")]
        decisions = [
            AppraisalDecision(
                pmid="111",
                outcome=AppraisalOutcome.EXCLUDE,
                exclusion_code="EX-MADE-UP",
                rationale="?",
            )
        ]
        with pytest.raises(OrchestratorError, match="unknown exclusion_code"):
            convert_decisions(records, decisions)

    # Note: an AppraisalDecision with outcome=EXCLUDE and exclusion_code=None
    # is rejected by AppraisalDecision's own __post_init__ — the defensive
    # check in convert_decisions cannot be reached via a valid input. The
    # defensive branch remains for protection against bypassed __post_init__
    # (e.g. dataclass replace tricks) and ruff/coverage tooling.

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(OrchestratorError, match="same length"):
            convert_decisions([_record("111")], [])

    def test_explicit_decided_at_and_by(self) -> None:
        records = [_record("111")]
        decisions = [
            AppraisalDecision(
                pmid="111",
                outcome=AppraisalOutcome.INCLUDE,
                exclusion_code=None,
                rationale="ok",
            )
        ]
        ts = datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC)
        screening, _ = convert_decisions(
            records, decisions, decided_at=ts, decided_by="reviewer:michael"
        )
        assert screening[0].decided_at == ts
        assert screening[0].decided_by == "reviewer:michael"


# ---- run_appraisal --------------------------------------------------------


class TestRunAppraisal:
    def test_routes_to_configured_lens_for_claim_type(self, tmp_path: Path) -> None:
        from ring2.core.project_config import (
            AppraisalConfig,
            AppraisalLensSelection,
            ProjectConfig,
        )

        config = ProjectConfig(
            name="P1",
            claim=_claim(claim_type=ClaimType.CLINICAL_PERFORMANCE),
            appraisal=AppraisalConfig(
                biochemistry_material_property=AppraisalLensSelection(lens="glp_oecd"),
                safety_allergenicity=AppraisalLensSelection(lens="care_caseseries"),
                clinical_performance=AppraisalLensSelection(lens="meddev_a6"),
                historical_market_use=AppraisalLensSelection(lens="registry_authoritativeness"),
            ),
            output_dir=tmp_path,
        )
        records = [_record("111"), _record("222")]
        out = run_appraisal(_claim(claim_type=ClaimType.CLINICAL_PERFORMANCE), records, config)
        # meddev_a6 with default classifier is non-operational → pending results.
        assert ClaimType.CLINICAL_PERFORMANCE in out
        assert len(out[ClaimType.CLINICAL_PERFORMANCE]) == 2
        # All values are tuples for MPCORenderContext compatibility.
        assert isinstance(out[ClaimType.CLINICAL_PERFORMANCE], tuple)


# ---- build_render_context -------------------------------------------------


class TestBuildRenderContext:
    def test_assembles_context_with_balanced_flow(self, tmp_path: Path) -> None:
        _write_batch(
            tmp_path,
            1,
            "CB-bov-01",
            [_record_dict("111"), _record_dict("222")],
        )
        state = resume_state(tmp_path, project_id="P1", claim_id="CB-bov-01")
        ts = datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC)
        decisions = (
            ScreeningDecision(
                pmid="111",
                phase=PrismaPhase.SCREENING,
                outcome="include",
                exclusion_code=None,
                rationale="ok",
                decided_at=ts,
                decided_by="x",
            ),
            ScreeningDecision(
                pmid="222",
                phase=PrismaPhase.SCREENING,
                outcome="exclude",
                exclusion_code=ExclusionCode.IRRELEVANT,
                rationale="off-topic",
                decided_at=ts,
                decided_by="x",
            ),
        )
        ctx = build_render_context(
            _claim(),
            state,
            decisions,
            appraisals={},
            identified_database=2,
        )
        assert ctx.claim.claim_id == "CB-bov-01"
        # Flow exists and the exclusion was routed to screening phase.
        assert "EX-IRRELEVANT" in ctx.flow.counts.excluded_screening


# ---- write_report ---------------------------------------------------------


class TestWriteReport:
    def test_writes_markdown_to_expected_path(self, tmp_path: Path) -> None:
        out = write_report("# Report\n\nhello\n", tmp_path, "CB-bov-01")
        assert out == tmp_path / "CB-bov-01_report.md"
        assert out.read_text(encoding="utf-8") == "# Report\n\nhello\n"

    def test_creates_output_dir_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested"
        out = write_report("body", target, "CID")
        assert out.exists()
        assert out.parent == target


# ===========================================================================
# End-to-end test (no search)
# ===========================================================================


class TestEndToEndNoSearch:
    def test_pipeline_runs_against_pre_seeded_batches(self, tmp_path: Path) -> None:
        # Set up the output dir + a pre-seeded batch.
        output_dir = tmp_path / "out"
        _write_batch(
            output_dir,
            1,
            "CB-bov-01",
            [
                _record_dict("111", "Clinical study A"),
                _record_dict("222", "Clinical study B"),
                _record_dict("333", "Off-topic paper"),
            ],
        )

        # Project YAML: no search block → orchestrator resumes state.
        project_yaml = _project_yaml_inline(output_dir_str=str(output_dir))
        project_path = tmp_path / "project.yaml"
        project_path.write_text(project_yaml, encoding="utf-8")

        # Screener excludes pmid 333; includes the rest.
        screener = FakeScreenerCaller(
            replies={
                "333": {
                    "outcome": "exclude",
                    "exclusion_code": "EX-IRRELEVANT",
                    "rationale": "off-topic per fake screener",
                    "confidence": 0.95,
                },
            }
        )

        result = run(project_path, screener_caller=screener)

        assert isinstance(result, OrchestratorRunResult)
        assert result.report_path.exists()
        assert result.report_path == output_dir / "CB-bov-01_report.md"
        assert len(result.screening_decisions) == 3
        # Two includes, one exclude.
        outcomes = [sd.outcome for sd in result.screening_decisions]
        assert outcomes.count("include") == 2
        assert outcomes.count("exclude") == 1
        assert result.eligible_records_count == 2

        # Inspect the report content for the key sections.
        body = result.report_path.read_text(encoding="utf-8")
        assert "# RING2 MPCO Report" in body
        assert "## §5 PRISMA flow" in body
        assert "## §8 Appraisal log" in body
        # clinical_performance → meddev_a6 (non-operational classifier).
        # Both eligible records produce PendingAppraisalResult.
        assert "Awaiting classifier/implementation" in body
        assert "2 eligible record(s)" in body

        # A decisions sidecar was written.
        decisions_dir = output_dir / "decisions"
        assert decisions_dir.exists()
        assert any(decisions_dir.iterdir())

    def test_pipeline_with_empty_session_produces_minimal_report(self, tmp_path: Path) -> None:
        # No batches, no search → no records, no screening, empty appraisal.
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        project_yaml = _project_yaml_inline(output_dir_str=str(output_dir))
        project_path = tmp_path / "project.yaml"
        project_path.write_text(project_yaml, encoding="utf-8")

        # NullScreener is safe because no records → no calls.
        result = run(project_path)

        assert result.report_path.exists()
        assert len(result.screening_decisions) == 0
        assert result.eligible_records_count == 0


# ===========================================================================
# CLI
# ===========================================================================


class TestCli:
    def test_build_parser_has_run_subcommand(self) -> None:
        from ring2.cli.run import build_parser

        parser = build_parser()
        ns = parser.parse_args(["run", "fake.yaml"])
        assert ns.project_yaml == Path("fake.yaml")
        assert ns.command == "run"

    def test_main_missing_file_returns_exit_2(self, tmp_path: Path) -> None:
        from ring2.cli.run import main

        exit_code = main(["run", str(tmp_path / "nonexistent.yaml")])
        assert exit_code == 2

    def test_main_runs_smoke(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        from ring2.cli.run import main

        output_dir = tmp_path / "out"
        output_dir.mkdir()
        project_yaml = _project_yaml_inline(output_dir_str=str(output_dir))
        project_path = tmp_path / "project.yaml"
        project_path.write_text(project_yaml, encoding="utf-8")

        exit_code = main(["run", str(project_path)])
        assert exit_code == 0

        captured = capsys.readouterr()
        assert "Report written to" in captured.out
        assert (output_dir / "CB-bov-01_report.md").exists()


# ===========================================================================
# End-to-end with rule-based A6 classifier (Stufe 1.9b)
# ===========================================================================


class TestEndToEndWithRuleBasedA6:
    def test_pipeline_with_a6_classifier_produces_real_meddev_a6_results(
        self, tmp_path: Path
    ) -> None:
        from ring2.adapters.mpco.appraisal.rule_based_a6 import RuleBasedA6Classifier

        output_dir = tmp_path / "out"
        # Two records: one well-powered RCT (qualifies), one case report
        # (flagged by both b and d → does not qualify).
        _write_batch(
            output_dir,
            1,
            "CB-bov-01",
            [
                {
                    **_record_dict(
                        "11111111",
                        "A randomized controlled trial of bovine collagen in 124 patients",
                    ),
                    "abstract": "Two-arm RCT with primary outcome.",
                },
                {
                    **_record_dict("22222222", "A case report of bovine collagen anaphylaxis"),
                    "abstract": "Single patient outcome.",
                },
            ],
        )

        project_yaml = _project_yaml_inline(output_dir_str=str(output_dir))
        project_path = tmp_path / "project.yaml"
        project_path.write_text(project_yaml, encoding="utf-8")

        screener = FakeScreenerCaller()  # default: include all
        result = run(
            project_path,
            screener_caller=screener,
            a6_classifier=RuleBasedA6Classifier(),
        )

        body = result.report_path.read_text(encoding="utf-8")
        # MeddevA6Lens.render_summary supplies its own header → real results path.
        assert "### Lens: MEDDEV 2.7/1 Rev. 4 §A6" in body
        # Headline tally: 2 appraised, 1 qualifying (the RCT), 1 non-qualifying
        # (the case report).
        assert "Records appraised: 2" in body
        assert "Qualifying (no §A6 deficiency): 1" in body
        assert "Non-qualifying (≥ 1 §A6 deficiency): 1" in body
        # No "awaiting" block when classifier is operational.
        assert "Awaiting classifier" not in body
        # Per-category tally lines present.
        assert "b-numbers-too-small" in body
        assert "d-lack-of-adequate-controls" in body

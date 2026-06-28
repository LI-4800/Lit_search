# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.core.prisma."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ring2.core.persistence import load
from ring2.core.prisma import (
    PrismaConsistencyError,
    PrismaPhaseCounts,
    build_flow,
    to_svg,
    to_yaml,
)
from ring2.core.session import SessionStateImpl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(
    tmp_path: Path, *, project_id: str = "722-Retro", claim_id: str = "CB-bov-01"
) -> SessionStateImpl:
    return SessionStateImpl(
        project_id=project_id,
        claim_id=claim_id,
        session_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# PrismaPhaseCounts: non-negativity
# ---------------------------------------------------------------------------


def test_counts_rejects_negative_identified_database() -> None:
    with pytest.raises(PrismaConsistencyError, match="identified_database"):
        PrismaPhaseCounts(identified_database=-1)


def test_counts_rejects_negative_identified_other() -> None:
    with pytest.raises(PrismaConsistencyError, match="identified_other"):
        PrismaPhaseCounts(identified_database=10, identified_other=-1)


def test_counts_rejects_negative_duplicates() -> None:
    with pytest.raises(PrismaConsistencyError, match="duplicates_removed"):
        PrismaPhaseCounts(identified_database=10, duplicates_removed=-1)


def test_counts_rejects_negative_screening_exclusion_count() -> None:
    with pytest.raises(PrismaConsistencyError, match="excluded_screening"):
        PrismaPhaseCounts(
            identified_database=10,
            excluded_screening={"EX-LANGUAGE": -1},
        )


def test_counts_rejects_negative_eligibility_exclusion_count() -> None:
    with pytest.raises(PrismaConsistencyError, match="excluded_eligibility"):
        PrismaPhaseCounts(
            identified_database=10,
            excluded_eligibility={"EX-A6-CATALOG": -1},
        )


# ---------------------------------------------------------------------------
# PrismaPhaseCounts: balance equations
# ---------------------------------------------------------------------------


def test_counts_rejects_duplicates_greater_than_identified() -> None:
    with pytest.raises(PrismaConsistencyError, match=r"duplicates_removed.*exceeds"):
        PrismaPhaseCounts(
            identified_database=5,
            identified_other=2,
            duplicates_removed=10,  # > total_identified = 7
        )


def test_counts_rejects_screening_exclusions_greater_than_screened() -> None:
    with pytest.raises(PrismaConsistencyError, match=r"excluded_screening.*exceeds"):
        PrismaPhaseCounts(
            identified_database=10,
            duplicates_removed=2,  # screened = 8
            excluded_screening={"EX-LANGUAGE": 5, "EX-IRRELEVANT": 5},  # sum = 10 > 8
        )


def test_counts_rejects_eligibility_exclusions_greater_than_assessed() -> None:
    with pytest.raises(PrismaConsistencyError, match=r"excluded_eligibility.*exceeds"):
        PrismaPhaseCounts(
            identified_database=10,
            excluded_screening={"EX-IRRELEVANT": 6},  # assessed = 4
            excluded_eligibility={"EX-A6-CATALOG": 5},  # > 4
        )


# ---------------------------------------------------------------------------
# PrismaPhaseCounts: derived counts
# ---------------------------------------------------------------------------


def test_counts_derived_happy_path() -> None:
    c = PrismaPhaseCounts(
        identified_database=100,
        identified_other=5,
        duplicates_removed=10,
        excluded_screening={"EX-LANGUAGE": 20, "EX-IRRELEVANT": 30},
        excluded_eligibility={"EX-A6-CATALOG": 15, "EX-NO-FULLTEXT": 5},
    )
    assert c.total_identified == 105
    assert c.screened == 95
    assert c.assessed_eligibility == 45  # 95 - 50
    assert c.included == 25  # 45 - 20


def test_counts_minimal_zero_path() -> None:
    """Edge case: nothing found, everything zero."""
    c = PrismaPhaseCounts(identified_database=0)
    assert c.total_identified == 0
    assert c.screened == 0
    assert c.assessed_eligibility == 0
    assert c.included == 0


def test_counts_all_excluded_yields_zero_included() -> None:
    c = PrismaPhaseCounts(
        identified_database=10,
        excluded_screening={"EX-IRRELEVANT": 5},
        excluded_eligibility={"EX-A6-CATALOG": 5},
    )
    assert c.included == 0


def test_counts_excluded_mappings_are_immutable() -> None:
    src = {"EX-LANGUAGE": 3}
    c = PrismaPhaseCounts(identified_database=10, excluded_screening=src)
    # mutating the original input must not affect the stored mapping
    src["EX-LANGUAGE"] = 999
    assert c.excluded_screening["EX-LANGUAGE"] == 3
    # the stored mapping itself must reject mutation
    with pytest.raises(TypeError):
        c.excluded_screening["EX-LANGUAGE"] = 999  # type: ignore[index]


# ---------------------------------------------------------------------------
# build_flow
# ---------------------------------------------------------------------------


def test_build_flow_stamps_state_identifiers(tmp_path: Path) -> None:
    state = _state(tmp_path, project_id="OsteoGen-CER", claim_id="P1-PICO-01")
    flow = build_flow(
        state,
        identified_database=50,
        excluded_screening={"EX-IRRELEVANT": 10},
        excluded_eligibility={"EX-A6-CATALOG": 5},
    )
    assert flow.project_id == "OsteoGen-CER"
    assert flow.claim_id == "P1-PICO-01"
    assert flow.counts.included == 35
    # ISO-8601 UTC timestamp ending in Z
    assert flow.generated_at.endswith("Z")


def test_build_flow_passes_notes(tmp_path: Path) -> None:
    state = _state(tmp_path)
    flow = build_flow(
        state,
        identified_database=10,
        excluded_screening={},
        excluded_eligibility={},
        notes=("UNKLAR-B4: NB scope not yet confirmed",),
    )
    assert flow.notes == ("UNKLAR-B4: NB scope not yet confirmed",)


def test_build_flow_propagates_consistency_error(tmp_path: Path) -> None:
    state = _state(tmp_path)
    with pytest.raises(PrismaConsistencyError):
        build_flow(
            state,
            identified_database=5,
            excluded_screening={"EX-IRRELEVANT": 100},
            excluded_eligibility={},
        )


# ---------------------------------------------------------------------------
# YAML serialisation
# ---------------------------------------------------------------------------


def test_to_yaml_roundtrip(tmp_path: Path) -> None:
    state = _state(tmp_path, project_id="722-Retro", claim_id="CB-bov-01")
    flow = build_flow(
        state,
        identified_database=136,
        identified_other=3,
        duplicates_removed=8,
        excluded_screening={"EX-LANGUAGE": 12, "EX-IRRELEVANT": 60},
        excluded_eligibility={"EX-A6-CATALOG": 30, "EX-NO-FULLTEXT": 4},
    )
    target = tmp_path / "prisma_CB-bov-01.yaml"
    written = to_yaml(flow, target)
    assert written == target
    assert target.exists()

    loaded = load(target)
    section = loaded["prisma_2020"]
    assert section["project_id"] == "722-Retro"
    assert section["claim_id"] == "CB-bov-01"
    assert section["identification"]["identified_database"] == 136
    assert section["identification"]["identified_other"] == 3
    assert section["identification"]["total_identified"] == 139
    assert section["screening"]["duplicates_removed"] == 8
    assert section["screening"]["screened"] == 131
    assert section["screening"]["excluded"]["EX-LANGUAGE"] == 12
    assert section["screening"]["excluded_total"] == 72
    assert section["eligibility"]["assessed"] == 59
    assert section["eligibility"]["excluded"]["EX-A6-CATALOG"] == 30
    assert section["included"] == 25


def test_to_yaml_writes_notes(tmp_path: Path) -> None:
    state = _state(tmp_path)
    flow = build_flow(
        state,
        identified_database=10,
        excluded_screening={},
        excluded_eligibility={},
        notes=("note A", "note B"),
    )
    target = tmp_path / "prisma.yaml"
    to_yaml(flow, target)
    loaded = load(target)
    assert list(loaded["prisma_2020"]["notes"]) == ["note A", "note B"]


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------


def test_to_svg_returns_well_formed_xml(tmp_path: Path) -> None:
    state = _state(tmp_path)
    flow = build_flow(
        state,
        identified_database=50,
        excluded_screening={"EX-IRRELEVANT": 10, "EX-LANGUAGE": 5},
        excluded_eligibility={"EX-A6-CATALOG": 3},
    )
    svg = to_svg(flow)
    # Must parse as XML.
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    # Must declare a viewBox.
    assert "viewBox" in root.attrib


def test_to_svg_contains_key_counts(tmp_path: Path) -> None:
    state = _state(tmp_path, project_id="722-Retro", claim_id="CB-bov-01")
    flow = build_flow(
        state,
        identified_database=136,
        duplicates_removed=8,
        excluded_screening={"EX-IRRELEVANT": 60},
        excluded_eligibility={"EX-A6-CATALOG": 30},
    )
    svg = to_svg(flow)
    # Header (project + claim)
    assert "722-Retro" in svg
    assert "CB-bov-01" in svg
    # Counts the diagram must surface
    assert "136" in svg  # identified
    assert "128" in svg  # screened (136 - 8)
    assert "68" in svg  # assessed (128 - 60)
    assert "38" in svg  # included (68 - 30)
    # Exclusion-code labels
    assert "EX-IRRELEVANT" in svg
    assert "EX-A6-CATALOG" in svg


def test_to_svg_handles_zero_exclusions(tmp_path: Path) -> None:
    state = _state(tmp_path)
    flow = build_flow(
        state,
        identified_database=5,
        excluded_screening={},
        excluded_eligibility={},
    )
    svg = to_svg(flow)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    # Must visibly indicate empty exclusion lists.
    assert "(none)" in svg


def test_to_svg_escapes_xml_special_chars_in_codes(tmp_path: Path) -> None:
    """Exclusion codes that contain XML-meaningful chars must be escaped."""
    state = _state(tmp_path, project_id="P&D", claim_id="C<1>")
    flow = build_flow(
        state,
        identified_database=10,
        excluded_screening={"EX-A&B": 2},
        excluded_eligibility={},
    )
    svg = to_svg(flow)
    # XML well-formedness implies escapes are correct.
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    # raw ampersand must be encoded
    assert "P&D" not in svg
    assert "P&amp;D" in svg


def test_to_svg_all_rectangles_fit_inside_viewbox(tmp_path: Path) -> None:
    """Regression: every <rect> must lie fully within the declared viewBox.

    Earlier layout placed side boxes at x=600 width=280 with viewBox
    width 800 - boxes were clipped or rendered outside the canvas
    depending on the viewer.
    """
    state = _state(tmp_path)
    flow = build_flow(
        state,
        identified_database=100,
        duplicates_removed=5,
        excluded_screening={"EX-LANGUAGE": 10, "EX-IRRELEVANT": 20, "EX-DUPLICATE": 3},
        excluded_eligibility={"EX-A6-CATALOG": 15, "EX-NO-FULLTEXT": 4},
    )
    svg = to_svg(flow)
    root = ET.fromstring(svg)
    vb = root.attrib["viewBox"].split()
    vb_x, vb_y, vb_w, vb_h = (float(v) for v in vb)

    ns = "{http://www.w3.org/2000/svg}"
    for rect in root.iter(f"{ns}rect"):
        x = float(rect.attrib["x"])
        y = float(rect.attrib["y"])
        w = float(rect.attrib["width"])
        h = float(rect.attrib["height"])
        assert x >= vb_x, f"rect x={x} < viewBox x={vb_x}"
        assert y >= vb_y, f"rect y={y} < viewBox y={vb_y}"
        assert x + w <= vb_x + vb_w, f"rect right edge {x + w} exceeds viewBox right {vb_x + vb_w}"
        assert y + h <= vb_y + vb_h, (
            f"rect bottom edge {y + h} exceeds viewBox bottom {vb_y + vb_h}"
        )

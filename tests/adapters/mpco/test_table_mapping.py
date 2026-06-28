# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.table_mapping."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ring2.adapters.mpco.table_mapping import CellRef

# ---------------------------------------------------------------------------
# Construction — happy path
# ---------------------------------------------------------------------------


def test_construct_valid() -> None:
    ref = CellRef(
        workbook="Material_Comparison.xlsx",
        sheet="Polymer",
        row=8,
        column_label="bovine Collagen",
    )
    assert ref.workbook == "Material_Comparison.xlsx"
    assert ref.sheet == "Polymer"
    assert ref.row == 8
    assert ref.column_label == "bovine Collagen"


def test_construct_row_1_ok() -> None:
    """Row 1 is the minimum valid Excel row."""
    ref = CellRef(workbook="w.xlsx", sheet="S", row=1, column_label="C")
    assert ref.row == 1


# ---------------------------------------------------------------------------
# Construction — validation errors
# ---------------------------------------------------------------------------


def test_construct_empty_workbook_rejected() -> None:
    with pytest.raises(ValueError, match="workbook must be non-empty"):
        CellRef(workbook="", sheet="S", row=1, column_label="C")


def test_construct_empty_sheet_rejected() -> None:
    with pytest.raises(ValueError, match="sheet must be non-empty"):
        CellRef(workbook="w.xlsx", sheet="", row=1, column_label="C")


@pytest.mark.parametrize("bad_row", [0, -1, -100])
def test_construct_row_below_one_rejected(bad_row: int) -> None:
    with pytest.raises(ValueError, match="row must be >= 1"):
        CellRef(workbook="w.xlsx", sheet="S", row=bad_row, column_label="C")


def test_construct_empty_column_label_rejected() -> None:
    with pytest.raises(ValueError, match="column_label must be non-empty"):
        CellRef(workbook="w.xlsx", sheet="S", row=1, column_label="")


def test_construct_column_label_with_single_quote_rejected() -> None:
    with pytest.raises(ValueError, match="must not contain single quotes"):
        CellRef(workbook="w.xlsx", sheet="S", row=1, column_label="bovine's collagen")


def test_construct_separator_in_workbook_rejected() -> None:
    with pytest.raises(ValueError, match="must not contain the separator"):
        CellRef(workbook="bad · name.xlsx", sheet="S", row=1, column_label="C")


def test_construct_separator_in_sheet_rejected() -> None:
    with pytest.raises(ValueError, match="must not contain the separator"):
        CellRef(workbook="w.xlsx", sheet="bad · sheet", row=1, column_label="C")


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_frozen() -> None:
    ref = CellRef(workbook="w.xlsx", sheet="S", row=1, column_label="C")
    with pytest.raises(FrozenInstanceError):
        ref.row = 2  # type: ignore[misc]


def test_equality_by_value() -> None:
    a = CellRef(workbook="w.xlsx", sheet="S", row=1, column_label="C")
    b = CellRef(workbook="w.xlsx", sheet="S", row=1, column_label="C")
    c = CellRef(workbook="w.xlsx", sheet="S", row=2, column_label="C")
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


# ---------------------------------------------------------------------------
# to_string
# ---------------------------------------------------------------------------


def test_to_string_matches_architecture_v1_format() -> None:
    ref = CellRef(
        workbook="Material_Comparison.xlsx",
        sheet="Polymer",
        row=8,
        column_label="bovine Collagen",
    )
    expected = "Material_Comparison.xlsx · Polymer · Row 8 · Column 'bovine Collagen'"
    assert ref.to_string() == expected


def test_to_string_with_double_quotes_in_label() -> None:
    """Double quotes in label are permitted (only single quotes break round-trip)."""
    ref = CellRef(workbook="w.xlsx", sheet="S", row=3, column_label='label "x"')
    assert ref.to_string() == "w.xlsx · S · Row 3 · Column 'label \"x\"'"


# ---------------------------------------------------------------------------
# from_string — happy path and round-trip
# ---------------------------------------------------------------------------


def test_from_string_round_trip() -> None:
    ref = CellRef(
        workbook="26-04-01_Material_Comparison.xlsx",
        sheet="Polymer",
        row=8,
        column_label="bovine Collagen",
    )
    parsed = CellRef.from_string(ref.to_string())
    assert parsed == ref


def test_from_string_accepts_double_quoted_label() -> None:
    """Defensive: hand-edited audit files may use double quotes."""
    parsed = CellRef.from_string('w.xlsx · S · Row 5 · Column "bovine Collagen"')
    assert parsed == CellRef(workbook="w.xlsx", sheet="S", row=5, column_label="bovine Collagen")


# ---------------------------------------------------------------------------
# from_string — error cases
# ---------------------------------------------------------------------------


def test_from_string_wrong_part_count() -> None:
    with pytest.raises(ValueError, match="expected 4 parts"):
        CellRef.from_string("only · two")


def test_from_string_missing_row_prefix() -> None:
    with pytest.raises(ValueError, match="must start with 'Row '"):
        CellRef.from_string("w.xlsx · S · 8 · Column 'C'")


def test_from_string_non_integer_row() -> None:
    with pytest.raises(ValueError, match="is not an integer"):
        CellRef.from_string("w.xlsx · S · Row eight · Column 'C'")


def test_from_string_missing_column_prefix() -> None:
    with pytest.raises(ValueError, match="must start with 'Column '"):
        CellRef.from_string("w.xlsx · S · Row 8 · 'C'")


def test_from_string_unquoted_label() -> None:
    with pytest.raises(ValueError, match="must be quoted"):
        CellRef.from_string("w.xlsx · S · Row 8 · Column bovine")


def test_from_string_empty_quoted_label() -> None:
    with pytest.raises(ValueError, match="empty column label"):
        CellRef.from_string("w.xlsx · S · Row 8 · Column ''")


def test_from_string_mismatched_quotes() -> None:
    with pytest.raises(ValueError, match="must be quoted"):
        CellRef.from_string("w.xlsx · S · Row 8 · Column 'C\"")


def test_from_string_row_too_small_rejected_at_construction() -> None:
    """from_string passes parsed row through __post_init__ validation."""
    with pytest.raises(ValueError, match="row must be >= 1"):
        CellRef.from_string("w.xlsx · S · Row 0 · Column 'C'")

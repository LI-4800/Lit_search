# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.schema."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import (
    Comparator,
    Material,
    MPCOClaim,
    MPCOSchemaDefinition,
    Outcome,
    Property,
)
from ring2.adapters.mpco.table_mapping import CellRef
from ring2.core.adapter_base import Schema

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_cell_ref() -> CellRef:
    return CellRef(
        workbook="26-04-01_Material_Comparison.xlsx",
        sheet="Polymer",
        row=8,
        column_label="bovine Collagen",
    )


def _valid_claim_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "claim_id": "CB-bov-01",
        "source_table_cell": _valid_cell_ref(),
        "material": Material(description="Bovine-derived collagen extracted via porcine pepsin"),
        "property": Property(description="Biocompatibility and resorption kinetics"),
        "comparator": Comparator(description="Porcine-derived collagen; synthetic PLGA"),
        "outcome": Outcome(description="Inflammatory response; resorption time"),
        "applicable_regulation": "722_2012",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# MPCO components — Material / Property / Comparator / Outcome
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [Material, Property, Comparator, Outcome])
def test_component_constructs_with_description(cls: type) -> None:
    obj = cls(description="A free-text description.")
    assert obj.description == "A free-text description."
    assert obj.notes is None


@pytest.mark.parametrize("cls", [Material, Property, Comparator, Outcome])
def test_component_accepts_notes(cls: type) -> None:
    obj = cls(description="Desc.", notes="Adapter-side elaboration.")
    assert obj.notes == "Adapter-side elaboration."


@pytest.mark.parametrize("cls", [Material, Property, Comparator, Outcome])
def test_component_rejects_empty_description(cls: type) -> None:
    with pytest.raises(ValidationError):
        cls(description="")


@pytest.mark.parametrize("cls", [Material, Property, Comparator, Outcome])
def test_component_rejects_extra_fields(cls: type) -> None:
    with pytest.raises(ValidationError):
        cls(description="d", unexpected_field="x")


@pytest.mark.parametrize("cls", [Material, Property, Comparator, Outcome])
def test_component_frozen(cls: type) -> None:
    obj = cls(description="d")
    with pytest.raises(ValidationError):
        obj.description = "new"  # type: ignore[misc]


@pytest.mark.parametrize("cls", [Material, Property, Comparator, Outcome])
def test_component_strips_whitespace(cls: type) -> None:
    obj = cls(description="  hello  ")
    assert obj.description == "hello"


# ---------------------------------------------------------------------------
# MPCOClaim — construction
# ---------------------------------------------------------------------------


def test_mpco_claim_constructs_with_valid_data() -> None:
    claim = MPCOClaim(**_valid_claim_kwargs())
    assert claim.claim_id == "CB-bov-01"
    assert claim.material.description.startswith("Bovine-derived")
    assert claim.applicable_regulation == "722_2012"
    assert claim.claim_type is ClaimType.UNKNOWN


def test_mpco_claim_type_defaults_to_unknown() -> None:
    """Claim constructed before classification has UNKNOWN type."""
    claim = MPCOClaim(**_valid_claim_kwargs())
    assert claim.claim_type is ClaimType.UNKNOWN


def test_mpco_claim_accepts_explicit_claim_type() -> None:
    claim = MPCOClaim(**_valid_claim_kwargs(claim_type=ClaimType.REGULATORY_COMPLIANCE))
    assert claim.claim_type is ClaimType.REGULATORY_COMPLIANCE


def test_mpco_claim_frozen() -> None:
    claim = MPCOClaim(**_valid_claim_kwargs())
    with pytest.raises(ValidationError):
        claim.claim_id = "CB-bov-02"  # type: ignore[misc]


def test_mpco_claim_rejects_empty_claim_id() -> None:
    with pytest.raises(ValidationError):
        MPCOClaim(**_valid_claim_kwargs(claim_id=""))


def test_mpco_claim_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        MPCOClaim(**_valid_claim_kwargs(unexpected="x"))


@pytest.mark.parametrize("bad_reg", ["", "2017_745", "TBD", "MDR"])
def test_mpco_claim_rejects_invalid_applicable_regulation(bad_reg: str) -> None:
    with pytest.raises(ValidationError):
        MPCOClaim(**_valid_claim_kwargs(applicable_regulation=bad_reg))


@pytest.mark.parametrize("good_reg", ["722_2012", "none", "other"])
def test_mpco_claim_accepts_all_applicable_regulation_values(good_reg: str) -> None:
    claim = MPCOClaim(**_valid_claim_kwargs(applicable_regulation=good_reg))
    assert claim.applicable_regulation == good_reg


# ---------------------------------------------------------------------------
# MPCOClaim — CellRef coercion
# ---------------------------------------------------------------------------


def test_mpco_claim_accepts_cell_ref_instance() -> None:
    claim = MPCOClaim(**_valid_claim_kwargs(source_table_cell=_valid_cell_ref()))
    assert isinstance(claim.source_table_cell, CellRef)
    assert claim.source_table_cell.row == 8


def test_mpco_claim_accepts_cell_ref_as_dict() -> None:
    claim = MPCOClaim(
        **_valid_claim_kwargs(
            source_table_cell={
                "workbook": "w.xlsx",
                "sheet": "S",
                "row": 5,
                "column_label": "C",
            }
        )
    )
    assert isinstance(claim.source_table_cell, CellRef)
    assert claim.source_table_cell.workbook == "w.xlsx"
    assert claim.source_table_cell.row == 5


def test_mpco_claim_accepts_cell_ref_as_legacy_string() -> None:
    """Legacy single-string form (Architecture v1 example) is accepted on read."""
    legacy = "Material_Comparison.xlsx · Polymer · Row 8 · Column 'bovine Collagen'"
    claim = MPCOClaim(**_valid_claim_kwargs(source_table_cell=legacy))
    assert isinstance(claim.source_table_cell, CellRef)
    assert claim.source_table_cell.sheet == "Polymer"
    assert claim.source_table_cell.column_label == "bovine Collagen"


def test_mpco_claim_rejects_invalid_cell_ref_type() -> None:
    with pytest.raises(ValidationError):
        MPCOClaim(**_valid_claim_kwargs(source_table_cell=42))


def test_mpco_claim_propagates_cell_ref_validation_errors() -> None:
    """Bad row in the dict form must surface as a validation error (row >= 1)."""
    with pytest.raises(ValidationError):
        MPCOClaim(
            **_valid_claim_kwargs(
                source_table_cell={
                    "workbook": "w.xlsx",
                    "sheet": "S",
                    "row": 0,
                    "column_label": "C",
                }
            )
        )


# ---------------------------------------------------------------------------
# MPCOClaim — serialisation
# ---------------------------------------------------------------------------


def test_mpco_claim_serialises_cell_ref_as_structured_dict() -> None:
    """source_table_cell must always serialise as the structured dict form."""
    claim = MPCOClaim(**_valid_claim_kwargs())
    dumped = claim.model_dump()
    assert isinstance(dumped["source_table_cell"], dict)
    assert dumped["source_table_cell"] == {
        "workbook": "26-04-01_Material_Comparison.xlsx",
        "sheet": "Polymer",
        "row": 8,
        "column_label": "bovine Collagen",
    }


def test_mpco_claim_round_trip_via_dict() -> None:
    """A claim dumped to dict and re-validated must equal the original."""
    original = MPCOClaim(**_valid_claim_kwargs(claim_type=ClaimType.CLINICAL_PERFORMANCE))
    dumped = original.model_dump()
    restored = MPCOClaim.model_validate(dumped)
    assert restored == original


def test_mpco_claim_serialises_claim_type_as_string_value() -> None:
    """ClaimType is a StrEnum, so model_dump should output the string value."""
    claim = MPCOClaim(**_valid_claim_kwargs(claim_type=ClaimType.REGULATORY_COMPLIANCE))
    dumped = claim.model_dump()
    assert dumped["claim_type"] == "regulatory_compliance"


# ---------------------------------------------------------------------------
# MPCOSchemaDefinition — implements Schema Protocol from core
# ---------------------------------------------------------------------------


def test_schema_definition_name_is_mpco() -> None:
    assert MPCOSchemaDefinition().name == "MPCO"


def test_schema_definition_fields_are_mpco_canonical_order() -> None:
    assert MPCOSchemaDefinition().fields == ("material", "property", "comparator", "outcome")


def test_schema_definition_satisfies_core_schema_protocol() -> None:
    """Runtime-checkable Protocol compliance — adapter glue depends on this."""
    schema = MPCOSchemaDefinition()
    assert isinstance(schema, Schema)


def test_schema_definition_has_no_mutable_state() -> None:
    """The descriptor has only @property accessors and uses frozen=True, slots=True;
    it has no dataclass fields and therefore no mutable state to test."""
    schema = MPCOSchemaDefinition()
    # Two equivalent instances are equal — no hidden state distinguishes them.
    assert schema == MPCOSchemaDefinition()
    assert hash(schema) == hash(MPCOSchemaDefinition())

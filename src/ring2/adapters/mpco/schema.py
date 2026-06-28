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
"""MPCO claim schema — Pydantic v2 models.

Defines the four MPCO components (:class:`Material`, :class:`Property`,
:class:`Comparator`, :class:`Outcome`) and the composite :class:`MPCOClaim`
that bundles them with their claim identifier, comparison-table back-
reference (:class:`~ring2.adapters.mpco.table_mapping.CellRef`), applicable-
regulation toggle, and classifier output (:class:`~ring2.adapters.mpco.
claim_type_classifier.ClaimType`).

Per Prompt v3 §Example, the canonical YAML shape is::

    claim_id: CB-bov-01
    source_table_cell:
      workbook: 26-04-01_Material_Comparison.xlsx
      sheet: Polymer
      row: 8
      column_label: bovine Collagen
    material: {description: "Bovine-derived collagen ..."}
    property: {description: "Biocompatibility and resorption ..."}
    comparator: {description: "Porcine-derived collagen; PLGA"}
    outcome: {description: "Inflammatory response; resorption time"}
    applicable_regulation: 722_2012
    claim_type: regulatory_compliance

The CellRef back-reference is stored structurally (four keys), not as a
flat string — see U-1.6-A discussion. The schema accepts the structured
dict form, a CellRef instance, or the legacy single-string form, but
always serialises to the structured dict for forward compatibility.

The :class:`MPCOSchemaDefinition` adapter-side schema descriptor satisfies
the :class:`ring2.core.adapter_base.Schema` Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.table_mapping import CellRef

__all__ = [
    "ApplicableRegulation",
    "Comparator",
    "MPCOClaim",
    "MPCOSchemaDefinition",
    "Material",
    "Outcome",
    "Property",
]


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------


ApplicableRegulation = Literal["722_2012", "none", "other"]
"""Allowed values for the MPCO applicable-regulation toggle (Prompt v3 §Architecture)."""


# ---------------------------------------------------------------------------
# MPCO components — Material / Property / Comparator / Outcome
# ---------------------------------------------------------------------------


_COMPONENT_CONFIG = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class _MPCOComponent(BaseModel):
    """Shared base for the four MPCO components. Each carries a free-text
    description plus an optional ``notes`` field for adapter-side
    elaboration. Frozen, ``extra='forbid'`` to catch typos early."""

    model_config = _COMPONENT_CONFIG

    description: str = Field(min_length=1, description="Free-text description of the component.")
    notes: str | None = Field(default=None, description="Optional adapter-side elaboration.")


class Material(_MPCOComponent):
    """The material under evaluation (M of MPCO).

    Example: ``"Bovine-derived collagen extracted via porcine pepsin"``.
    """


class Property(_MPCOComponent):
    """The property being claimed (P of MPCO).

    Example: ``"Biocompatibility and resorption kinetics in oral surgery"``.
    """


class Comparator(_MPCOComponent):
    """The comparator material(s) or reference (C of MPCO).

    Example: ``"Porcine-derived collagen; synthetic PLGA"``.
    """


class Outcome(_MPCOComponent):
    """The measured outcome(s) (O of MPCO).

    Example: ``"Inflammatory response (histology); resorption time (weeks)"``.
    """


# ---------------------------------------------------------------------------
# MPCOClaim — composite claim record
# ---------------------------------------------------------------------------


class MPCOClaim(BaseModel):
    """One MPCO claim — the central data record of the MPCO adapter.

    Bundles the four MPCO components, the claim's stable identifier, the
    back-reference to its originating comparison-table cell, the applicable-
    regulation toggle, and the classifier-assigned :class:`ClaimType`.

    Attributes:
        claim_id: stable identifier (e.g. ``"CB-bov-01"``). Non-empty,
            whitespace stripped.
        source_table_cell: back-reference to the comparison-table cell
            this claim was derived from. Accepts a :class:`CellRef`
            instance, the structured dict form
            (``{"workbook":..., "sheet":..., "row":..., "column_label":...}``),
            or the legacy single-string form
            (``"workbook · sheet · Row N · Column 'label'"``). Always
            serialised back as the structured dict.
        material: the M of MPCO.
        property: the P of MPCO. (Field name shadows the builtin
            ``property`` deliberately to match Prompt v3 §Example YAML.)
        comparator: the C of MPCO.
        outcome: the O of MPCO.
        applicable_regulation: regulatory toggle. ``"722_2012"`` activates
            the animal-tissue regulatory block (Annex I element gating in
            ``reg_722_2012.py``); ``"none"`` skips it; ``"other"`` is
            reserved for caller-supplied alternative frameworks.
        claim_type: classifier-assigned :class:`ClaimType`. Defaults to
            :attr:`ClaimType.UNKNOWN` for claims constructed before
            classification has run.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        arbitrary_types_allowed=True,  # CellRef is a stdlib dataclass
    )

    claim_id: str = Field(min_length=1)
    source_table_cell: CellRef
    material: Material
    property: Property
    comparator: Comparator
    outcome: Outcome
    applicable_regulation: ApplicableRegulation
    claim_type: ClaimType = Field(default=ClaimType.UNKNOWN)

    @field_validator("source_table_cell", mode="before")
    @classmethod
    def _coerce_cell_ref(cls, v: Any) -> CellRef:
        """Accept CellRef, structured dict, or legacy string form."""
        if isinstance(v, CellRef):
            return v
        if isinstance(v, dict):
            return CellRef(**v)
        if isinstance(v, str):
            return CellRef.from_string(v)
        raise ValueError(f"source_table_cell must be CellRef, dict, or str; got {type(v).__name__}")

    @field_serializer("source_table_cell")
    def _serialize_cell_ref(self, ref: CellRef) -> dict[str, Any]:
        """Serialise CellRef as the structured dict (forward-compatible YAML)."""
        return {
            "workbook": ref.workbook,
            "sheet": ref.sheet,
            "row": ref.row,
            "column_label": ref.column_label,
        }


# ---------------------------------------------------------------------------
# Schema descriptor — implements ring2.core.adapter_base.Schema Protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MPCOSchemaDefinition:
    """Concrete :class:`ring2.core.adapter_base.Schema` for the MPCO adapter.

    The ``fields`` tuple lists the four canonical MPCO components in
    canonical order (M-P-C-O). Claim-level metadata (``claim_id``,
    ``source_table_cell``, ``applicable_regulation``, ``claim_type``) is
    not part of the schema fields proper — those are envelope/context.
    """

    @property
    def name(self) -> str:
        return "MPCO"

    @property
    def fields(self) -> tuple[str, ...]:
        return ("material", "property", "comparator", "outcome")

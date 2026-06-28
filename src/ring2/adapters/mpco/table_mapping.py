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
"""Excel back-reference for MPCO claims.

Every MPCO claim derived from a comparison-table cell carries a
:class:`CellRef` metadatum identifying its source: workbook, sheet, row,
and column label. This module is schema-only — no ``openpyxl`` dependency,
no spreadsheet I/O. The downstream verification module (planned post-1.9)
will use these references to confirm that tool-derived claims map back to
the originating spreadsheet cell.

String form (per Architecture v1 §1.3)::

    Material_Comparison.xlsx · Polymer · Row 8 · Column 'bovine Collagen'

Round-trip guarantee: :meth:`CellRef.to_string` followed by
:meth:`CellRef.from_string` returns an equal :class:`CellRef`. To guarantee
this, the constructor rejects column labels containing single quotes.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CellRef"]


_SEPARATOR = " · "
_ROW_PREFIX = "Row "
_COLUMN_PREFIX = "Column "


@dataclass(frozen=True, slots=True)
class CellRef:
    """Reference to a single cell in a comparison-table workbook.

    Used as ``MPCOClaim.source_table_cell`` to enable retrospective
    verification that each claim's evidence trace maps back to the
    originating spreadsheet cell.

    Fields:
        workbook: filename of the source workbook, e.g.
            ``"26-04-01_Material_Comparison.xlsx"``.
        sheet: sheet name within the workbook.
        row: 1-indexed row number (matching Excel's row numbering).
        column_label: human-readable column header (not Excel column
            letter), e.g. ``"bovine Collagen"``. Column letters are
            unstable across edits; labels are the canonical identifier.
            Must not contain single quotes (would break round-trip).
    """

    workbook: str
    sheet: str
    row: int
    column_label: str

    def __post_init__(self) -> None:
        if not self.workbook:
            raise ValueError("CellRef.workbook must be non-empty")
        if not self.sheet:
            raise ValueError("CellRef.sheet must be non-empty")
        if self.row < 1:
            raise ValueError(f"CellRef.row must be >= 1 (Excel rows are 1-indexed), got {self.row}")
        if not self.column_label:
            raise ValueError("CellRef.column_label must be non-empty")
        if "'" in self.column_label:
            raise ValueError(
                "CellRef.column_label must not contain single quotes "
                "(would break to_string/from_string round-trip); "
                f"got {self.column_label!r}"
            )
        if _SEPARATOR in self.workbook or _SEPARATOR in self.sheet:
            raise ValueError(
                f"CellRef fields must not contain the separator {_SEPARATOR!r}; "
                "would break to_string/from_string round-trip"
            )

    def to_string(self) -> str:
        """Render as audit-string in the Architecture-v1 format."""
        return (
            f"{self.workbook}{_SEPARATOR}{self.sheet}{_SEPARATOR}"
            f"{_ROW_PREFIX}{self.row}{_SEPARATOR}{_COLUMN_PREFIX}'{self.column_label}'"
        )

    @classmethod
    def from_string(cls, s: str) -> CellRef:
        """Parse a CellRef from its string form.

        Defensive inverse of :meth:`to_string`. Accepts either single-quoted
        or double-quoted column labels for forgiveness in hand-edited audit
        files; :meth:`to_string` always emits single quotes.

        Raises:
            ValueError: if the input cannot be parsed.
        """
        parts = [p.strip() for p in s.split(_SEPARATOR)]
        if len(parts) != 4:
            raise ValueError(
                f"Cannot parse CellRef from {s!r}: "
                f"expected 4 parts separated by {_SEPARATOR!r}, got {len(parts)}"
            )
        workbook, sheet, row_part, col_part = parts

        if not row_part.startswith(_ROW_PREFIX):
            raise ValueError(
                f"Cannot parse CellRef from {s!r}: third part must start with {_ROW_PREFIX!r}"
            )
        row_str = row_part[len(_ROW_PREFIX) :].strip()
        try:
            row = int(row_str)
        except ValueError as e:
            raise ValueError(
                f"Cannot parse CellRef from {s!r}: row number {row_str!r} is not an integer"
            ) from e

        if not col_part.startswith(_COLUMN_PREFIX):
            raise ValueError(
                f"Cannot parse CellRef from {s!r}: fourth part must start with {_COLUMN_PREFIX!r}"
            )
        label_part = col_part[len(_COLUMN_PREFIX) :].strip()
        if (
            len(label_part) < 2
            or label_part[0] != label_part[-1]
            or label_part[0] not in ("'", '"')
        ):
            raise ValueError(
                f"Cannot parse CellRef from {s!r}: column label must be quoted "
                "with matching single or double quotes"
            )
        label = label_part[1:-1]
        if not label:
            raise ValueError(f"Cannot parse CellRef from {s!r}: empty column label")

        return cls(workbook=workbook, sheet=sheet, row=row, column_label=label)

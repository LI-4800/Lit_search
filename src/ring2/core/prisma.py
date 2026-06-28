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
"""PRISMA 2020 flow generator.

Produces both a YAML data artefact (audit-trail) and an SVG flow diagram
(reporting) from per-claim exclusion counts and a :class:`SessionStateImpl`.

The four PRISMA 2020 phases:

    1. Identification
       - records identified via database searching (e.g. PubMed)
       - records identified via other sources (manual, citation searching)
    2. Screening
       - duplicates removed
       - title/abstract reviewed
       - records excluded at title/abstract with reasons (e.g. EX-LANGUAGE,
         EX-IRRELEVANT)
    3. Eligibility (full-text)
       - full-text reports assessed
       - reports excluded with reasons (incl. §A6 catalog codes such as
         EX-A6-CATALOG, and EX-NO-FULLTEXT for irretrievable full texts)
    4. Included
       - studies included in the synthesis

Per the 2026-06-27 design decision, the §A6 catalog is applied **only in
the eligibility (full-text) phase**, never at screening. Adapters must
route their codes accordingly when calling :func:`build_flow`.

Balance equations enforced by :class:`PrismaPhaseCounts`:

    total_identified  = identified_database + identified_other
    screened          = total_identified - duplicates_removed
    assessed          = screened - sum(excluded_screening.values())
    included          = assessed - sum(excluded_eligibility.values())

A consistency failure (e.g. more screening exclusions than records
screened) raises :class:`PrismaConsistencyError`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from .persistence import save

if TYPE_CHECKING:
    from .session import SessionStateImpl

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PrismaConsistencyError(ValueError):
    """Raised when PRISMA phase counts violate a balance equation."""


# ---------------------------------------------------------------------------
# Phase counts
# ---------------------------------------------------------------------------


def _freeze(m: Mapping[str, int]) -> Mapping[str, int]:
    """Return an immutable view over a mapping copy."""
    return MappingProxyType(dict(m))


@dataclass(frozen=True, slots=True)
class PrismaPhaseCounts:
    """Leaf counts for one PRISMA 2020 flow.

    Intermediate counts (``screened``, ``assessed_eligibility``,
    ``included``) are derived properties so callers cannot accidentally
    pass inconsistent intermediates.

    All counts must be non-negative; sums of excluded counts must not
    exceed the records available at that phase. Violations raise
    :class:`PrismaConsistencyError` during construction.

    Fields:
        identified_database: records found via database searching (PubMed).
        identified_other: records found via other methods (manual /
            citation searching). Default 0.
        duplicates_removed: records removed after deduplication. Default 0.
        excluded_screening: title/abstract-level exclusions, mapping
            exclusion code (e.g. ``EX-LANGUAGE``, ``EX-IRRELEVANT``) to
            count. Must NOT contain §A6 codes - those belong in
            ``excluded_eligibility``.
        excluded_eligibility: full-text-level exclusions, mapping
            exclusion code (e.g. ``EX-A6-CATALOG``, ``EX-NO-FULLTEXT``)
            to count.
    """

    identified_database: int
    identified_other: int = 0
    duplicates_removed: int = 0
    excluded_screening: Mapping[str, int] = field(default_factory=dict)
    excluded_eligibility: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Coerce mapping fields to immutable views (defensive copy).
        object.__setattr__(self, "excluded_screening", _freeze(self.excluded_screening))
        object.__setattr__(self, "excluded_eligibility", _freeze(self.excluded_eligibility))

        # Non-negativity
        for name, value in (
            ("identified_database", self.identified_database),
            ("identified_other", self.identified_other),
            ("duplicates_removed", self.duplicates_removed),
        ):
            if value < 0:
                raise PrismaConsistencyError(f"{name} must be non-negative, got {value}")
        for name, mapping in (
            ("excluded_screening", self.excluded_screening),
            ("excluded_eligibility", self.excluded_eligibility),
        ):
            for code, count in mapping.items():
                if count < 0:
                    raise PrismaConsistencyError(
                        f"{name}[{code!r}] must be non-negative, got {count}"
                    )

        # Balance: duplicates_removed <= total_identified
        if self.duplicates_removed > self.total_identified:
            raise PrismaConsistencyError(
                f"duplicates_removed ({self.duplicates_removed}) exceeds "
                f"total_identified ({self.total_identified})"
            )

        # Balance: screening exclusions <= screened
        screen_excl_total = sum(self.excluded_screening.values())
        if screen_excl_total > self.screened:
            raise PrismaConsistencyError(
                f"sum(excluded_screening) ({screen_excl_total}) exceeds screened ({self.screened})"
            )

        # Balance: eligibility exclusions <= assessed
        elig_excl_total = sum(self.excluded_eligibility.values())
        if elig_excl_total > self.assessed_eligibility:
            raise PrismaConsistencyError(
                f"sum(excluded_eligibility) ({elig_excl_total}) exceeds "
                f"assessed_eligibility ({self.assessed_eligibility})"
            )

    # -- derived counts -----------------------------------------------------

    @property
    def total_identified(self) -> int:
        """``identified_database + identified_other``."""
        return self.identified_database + self.identified_other

    @property
    def screened(self) -> int:
        """Records remaining after deduplication; entered into screening."""
        return self.total_identified - self.duplicates_removed

    @property
    def assessed_eligibility(self) -> int:
        """Records remaining after title/abstract screening; entered into full-text review."""
        return self.screened - sum(self.excluded_screening.values())

    @property
    def included(self) -> int:
        """Studies included in the final synthesis."""
        return self.assessed_eligibility - sum(self.excluded_eligibility.values())


# ---------------------------------------------------------------------------
# Flow (counts + metadata)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrismaFlow:
    """A balanced PRISMA 2020 flow ready for serialisation.

    Fields:
        counts: a validated :class:`PrismaPhaseCounts` instance.
        project_id: project identifier (e.g. ``"OsteoGen-CER"``,
            ``"722-Retro"``).
        claim_id: per-claim identifier (e.g. ``"CB-bov-01"``).
        generated_at: ISO-8601 UTC timestamp at flow creation.
        notes: optional free-text notes (e.g. UNKLAR markers carried
            forward into the report).
    """

    counts: PrismaPhaseCounts
    project_id: str
    claim_id: str
    generated_at: str
    notes: tuple[str, ...] = ()


def _now_iso() -> str:
    """ISO-8601 UTC timestamp with second resolution."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_flow(
    state: SessionStateImpl,
    *,
    identified_database: int,
    excluded_screening: Mapping[str, int],
    excluded_eligibility: Mapping[str, int],
    identified_other: int = 0,
    duplicates_removed: int = 0,
    notes: tuple[str, ...] = (),
) -> PrismaFlow:
    """Build a balanced :class:`PrismaFlow` for one claim.

    The caller (adapter) supplies the leaf counts; this function
    validates the balance equations (via :class:`PrismaPhaseCounts`)
    and stamps the project/claim identifiers from ``state``.

    Per the 2026-06-27 design decision, §A6-catalog codes
    (e.g. ``EX-A6-CATALOG``) belong in ``excluded_eligibility``, never
    in ``excluded_screening``. This invariant is the caller's
    responsibility; the PRISMA module does not police adapter codes.

    Raises:
        PrismaConsistencyError: if any balance equation fails.
    """
    counts = PrismaPhaseCounts(
        identified_database=identified_database,
        identified_other=identified_other,
        duplicates_removed=duplicates_removed,
        excluded_screening=excluded_screening,
        excluded_eligibility=excluded_eligibility,
    )
    return PrismaFlow(
        counts=counts,
        project_id=state.project_id,
        claim_id=state.claim_id,
        generated_at=_now_iso(),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# YAML serialisation
# ---------------------------------------------------------------------------


def to_yaml(flow: PrismaFlow, path: Path) -> Path:
    """Serialise ``flow`` to YAML at ``path``.

    The on-disk schema:

    .. code-block:: yaml

        prisma_2020:
          generated_at: "..."
          project_id: ...
          claim_id: ...
          identification:
            identified_database: N
            identified_other: N
            total_identified: N
          screening:
            duplicates_removed: N
            screened: N
            excluded:
              EX-LANGUAGE: n1
              EX-IRRELEVANT: n2
            excluded_total: N
          eligibility:
            assessed: N
            excluded:
              EX-A6-CATALOG: n1
              EX-NO-FULLTEXT: n2
            excluded_total: N
          included: N
          notes: [...]

    Derived counts (``total_identified``, ``screened``, ``assessed``,
    ``included``, ``excluded_total``) are written out explicitly even
    though they are computed from leaves; this makes the YAML
    human-readable as an audit artefact without requiring readers to
    re-derive intermediate counts.
    """
    c = flow.counts
    data = {
        "prisma_2020": {
            "generated_at": flow.generated_at,
            "project_id": flow.project_id,
            "claim_id": flow.claim_id,
            "identification": {
                "identified_database": c.identified_database,
                "identified_other": c.identified_other,
                "total_identified": c.total_identified,
            },
            "screening": {
                "duplicates_removed": c.duplicates_removed,
                "screened": c.screened,
                "excluded": dict(c.excluded_screening),
                "excluded_total": sum(c.excluded_screening.values()),
            },
            "eligibility": {
                "assessed": c.assessed_eligibility,
                "excluded": dict(c.excluded_eligibility),
                "excluded_total": sum(c.excluded_eligibility.values()),
            },
            "included": c.included,
            "notes": list(flow.notes),
        }
    }
    return save(path, data)


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

# Layout constants - all in user-space units (SVG viewBox).
_SVG_WIDTH: int = 1000
_BOX_WIDTH: int = 280
_BOX_HEIGHT_MIN: int = 70
_BOX_LINE_HEIGHT: int = 16
_BOX_PADDING: int = 12
_BOX_X_MAIN: int = (_SVG_WIDTH - _BOX_WIDTH) // 2  # centred main column
_BOX_X_SIDE: int = _BOX_X_MAIN + _BOX_WIDTH + 60  # exclusion side boxes
_ARROW_GAP: int = 30
_TOP_MARGIN: int = 20
_BOTTOM_MARGIN: int = 20


def _svg_box(
    x: int, y: int, width: int, height: int, lines: list[str], *, fill: str = "white"
) -> str:
    """Render a labelled rectangle as SVG."""
    rect = (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'fill="{fill}" stroke="black" stroke-width="1.5" rx="4"/>'
    )
    text_parts: list[str] = []
    text_x = x + width // 2
    text_y = y + _BOX_PADDING + 12  # baseline of first line
    for line in lines:
        text_parts.append(
            f'<text x="{text_x}" y="{text_y}" '
            f'text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="13">{escape(line)}</text>'
        )
        text_y += _BOX_LINE_HEIGHT
    return rect + "".join(text_parts)


def _svg_arrow(x1: int, y1: int, x2: int, y2: int) -> str:
    """Render a vertical or horizontal arrow as SVG (uses marker-end)."""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
        f'stroke="black" stroke-width="1.5" marker-end="url(#arrow)"/>'
    )


def _box_height(num_lines: int) -> int:
    """Compute box height given the number of text lines."""
    needed = _BOX_PADDING * 2 + num_lines * _BOX_LINE_HEIGHT
    return max(_BOX_HEIGHT_MIN, needed)


def to_svg(flow: PrismaFlow) -> str:
    """Render ``flow`` as a PRISMA 2020 flow diagram (SVG, string).

    Layout: 4 main boxes in a vertical column (Identification, Screening,
    Eligibility, Included) with two side boxes for the screening and
    eligibility exclusions.

    The returned string is well-formed XML, parseable by
    ``xml.etree.ElementTree.fromstring``. It contains no external
    references and is fully self-contained.
    """
    c = flow.counts

    # -- assemble per-box text content -------------------------------------
    id_lines = [
        "Identification",
        f"Records identified (database): {c.identified_database}",
        f"Records identified (other): {c.identified_other}",
        f"Total: {c.total_identified}",
    ]
    screen_lines = [
        "Screening",
        f"Duplicates removed: {c.duplicates_removed}",
        f"Records screened: {c.screened}",
    ]
    excl_screen_lines = ["Excluded at screening:"]
    if c.excluded_screening:
        for code in sorted(c.excluded_screening):
            excl_screen_lines.append(f"  {code}: {c.excluded_screening[code]}")
        excl_screen_lines.append(f"Total: {sum(c.excluded_screening.values())}")
    else:
        excl_screen_lines.append("  (none)")
    elig_lines = [
        "Eligibility (full-text)",
        f"Reports assessed: {c.assessed_eligibility}",
    ]
    excl_elig_lines = ["Excluded at eligibility:"]
    if c.excluded_eligibility:
        for code in sorted(c.excluded_eligibility):
            excl_elig_lines.append(f"  {code}: {c.excluded_eligibility[code]}")
        excl_elig_lines.append(f"Total: {sum(c.excluded_eligibility.values())}")
    else:
        excl_elig_lines.append("  (none)")
    incl_lines = [
        "Included",
        f"Studies in synthesis: {c.included}",
    ]

    # -- compute y-positions -----------------------------------------------
    id_h = _box_height(len(id_lines))
    screen_h = _box_height(len(screen_lines))
    excl_screen_h = _box_height(len(excl_screen_lines))
    elig_h = _box_height(len(elig_lines))
    excl_elig_h = _box_height(len(excl_elig_lines))
    incl_h = _box_height(len(incl_lines))

    y = _TOP_MARGIN
    id_y = y
    y += id_h + _ARROW_GAP
    screen_y = y
    excl_screen_y = y + (screen_h - excl_screen_h) // 2
    y += screen_h + _ARROW_GAP
    elig_y = y
    excl_elig_y = y + (elig_h - excl_elig_h) // 2
    y += elig_h + _ARROW_GAP
    incl_y = y
    y += incl_h + _BOTTOM_MARGIN

    svg_height = y

    # -- assemble SVG ------------------------------------------------------
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_SVG_WIDTH} {svg_height}" '
        f'width="{_SVG_WIDTH}" height="{svg_height}">'
    )
    # arrow marker
    parts.append(
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="8" markerHeight="8" orient="auto">'
        '<path d="M0,0 L10,5 L0,10 z" fill="black"/></marker></defs>'
    )
    # title
    parts.append(
        f'<text x="{_SVG_WIDTH // 2}" y="{_TOP_MARGIN - 5}" '
        f'text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="14" font-weight="bold">'
        f"PRISMA 2020 - {escape(flow.project_id)} / {escape(flow.claim_id)}</text>"
    )
    # main column boxes
    parts.append(_svg_box(_BOX_X_MAIN, id_y, _BOX_WIDTH, id_h, id_lines, fill="#f4f7fb"))
    parts.append(
        _svg_box(_BOX_X_MAIN, screen_y, _BOX_WIDTH, screen_h, screen_lines, fill="#f4f7fb")
    )
    parts.append(_svg_box(_BOX_X_MAIN, elig_y, _BOX_WIDTH, elig_h, elig_lines, fill="#f4f7fb"))
    parts.append(_svg_box(_BOX_X_MAIN, incl_y, _BOX_WIDTH, incl_h, incl_lines, fill="#e8f0e8"))
    # side exclusion boxes
    parts.append(
        _svg_box(
            _BOX_X_SIDE,
            excl_screen_y,
            _BOX_WIDTH,
            excl_screen_h,
            excl_screen_lines,
            fill="#fbf4f4",
        )
    )
    parts.append(
        _svg_box(
            _BOX_X_SIDE,
            excl_elig_y,
            _BOX_WIDTH,
            excl_elig_h,
            excl_elig_lines,
            fill="#fbf4f4",
        )
    )
    # arrows: main column vertical
    centre_x = _BOX_X_MAIN + _BOX_WIDTH // 2
    parts.append(_svg_arrow(centre_x, id_y + id_h, centre_x, screen_y))
    parts.append(_svg_arrow(centre_x, screen_y + screen_h, centre_x, elig_y))
    parts.append(_svg_arrow(centre_x, elig_y + elig_h, centre_x, incl_y))
    # arrows: horizontal to side boxes
    parts.append(
        _svg_arrow(
            _BOX_X_MAIN + _BOX_WIDTH,
            screen_y + screen_h // 2,
            _BOX_X_SIDE,
            excl_screen_y + excl_screen_h // 2,
        )
    )
    parts.append(
        _svg_arrow(
            _BOX_X_MAIN + _BOX_WIDTH,
            elig_y + elig_h // 2,
            _BOX_X_SIDE,
            excl_elig_y + excl_elig_h // 2,
        )
    )
    parts.append("</svg>")
    return "".join(parts)


__all__ = [
    "PrismaConsistencyError",
    "PrismaFlow",
    "PrismaPhaseCounts",
    "build_flow",
    "to_svg",
    "to_yaml",
]

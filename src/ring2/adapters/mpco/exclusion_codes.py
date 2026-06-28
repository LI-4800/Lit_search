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
"""MPCO-adapter-specific exclusion codes with PRISMA-phase routing.

The MPCO adapter extends the core's base exclusion set (``EX-DESIGN``,
``EX-INVITRO``, ``EX-ANIMAL``; see ``core/adapter_base.py``) with five
adapter-specific codes:

    EX-LANGUAGE      Title/abstract not in an accepted language.
    EX-IRRELEVANT    Topic mismatch detectable from title/abstract alone.
    EX-DUPLICATE     Same PMID or DOI seen earlier in the deduplication step.
    EX-NO-FULLTEXT   Full text could not be obtained for eligibility check.
    EX-A6-CATALOG    Falls under a MEDDEV 2.7/1 Rev. 4 §A6 exclusion category
                     (only assigned at the eligibility phase — per Handoff
                     26-06-27 Decision #20, §A6 is never applied at screening).

Each code carries a fixed :class:`PrismaPhase` routing tag indicating which
stage of the PRISMA 2020 flow may legitimately raise it. Routing is
enforced by the adapter's screening pipeline; this module only declares
the contract.

Per Architecture v1 §1.3, the canonical code string form uses hyphens
(``EX-LANGUAGE``). Enum *names* are Python identifiers (``LANGUAGE``);
enum *values* are the canonical hyphenated strings.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

__all__ = [
    "EXCLUSION_PHASE_ROUTING",
    "ExclusionCode",
    "PrismaPhase",
    "codes_for_phase",
    "phase_for",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ExclusionCode(StrEnum):
    """Adapter-specific MPCO exclusion codes.

    Values are the canonical hyphenated form used in audit artefacts and
    cross-referenced from regulatory documents.
    """

    LANGUAGE = "EX-LANGUAGE"
    IRRELEVANT = "EX-IRRELEVANT"
    DUPLICATE = "EX-DUPLICATE"
    NO_FULLTEXT = "EX-NO-FULLTEXT"
    A6_CATALOG = "EX-A6-CATALOG"


class PrismaPhase(StrEnum):
    """PRISMA 2020 flow phase at which an exclusion may be legitimately raised.

    Ordered conceptually as records flow through the pipeline:

        DEDUPLICATION → SCREENING → ELIGIBILITY → (Included)

    A given :class:`ExclusionCode` is routed to exactly one phase; raising
    it in any other phase is a contract violation enforceable by the
    adapter's screening pipeline.
    """

    DEDUPLICATION = "deduplication"
    SCREENING = "screening"
    ELIGIBILITY = "eligibility"


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


#: Authoritative mapping ``ExclusionCode → PrismaPhase``.
#:
#: Exposed read-only via :data:`MappingProxyType`. The mapping is
#: exhaustive over :class:`ExclusionCode`; the exhaustiveness contract is
#: enforced by tests.
EXCLUSION_PHASE_ROUTING: Mapping[ExclusionCode, PrismaPhase] = MappingProxyType(
    {
        # Pre-screening: PMID/DOI overlap with prior batches.
        ExclusionCode.DUPLICATE: PrismaPhase.DEDUPLICATION,
        # Title/abstract level: detectable without full text.
        ExclusionCode.LANGUAGE: PrismaPhase.SCREENING,
        ExclusionCode.IRRELEVANT: PrismaPhase.SCREENING,
        # Full-text level: only knowable after retrieval.
        ExclusionCode.NO_FULLTEXT: PrismaPhase.ELIGIBILITY,
        # §A6 catalog: per Handoff 26-06-27 Decision #20, only at eligibility,
        # never at screening.
        ExclusionCode.A6_CATALOG: PrismaPhase.ELIGIBILITY,
    }
)


def phase_for(code: ExclusionCode) -> PrismaPhase:
    """Return the PRISMA phase at which ``code`` is permitted.

    Args:
        code: an :class:`ExclusionCode` member.

    Returns:
        the :class:`PrismaPhase` that ``code`` is routed to.

    Raises:
        ValueError: if ``code`` is not a known :class:`ExclusionCode`. This
            should be impossible at runtime given the type signature, but
            is guarded for defensive use against e.g. deserialised data.
    """
    try:
        return EXCLUSION_PHASE_ROUTING[code]
    except KeyError as e:  # pragma: no cover — exhaustiveness enforced by tests
        raise ValueError(
            f"ExclusionCode {code!r} has no phase mapping; routing table is incomplete"
        ) from e


def codes_for_phase(phase: PrismaPhase) -> frozenset[ExclusionCode]:
    """Return all exclusion codes legitimate at ``phase``.

    Useful for the screening pipeline to validate that decisions raised at
    a given phase carry an acceptable code.
    """
    return frozenset(code for code, p in EXCLUSION_PHASE_ROUTING.items() if p is phase)

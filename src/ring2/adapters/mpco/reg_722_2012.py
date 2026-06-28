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
"""EU Regulation 722/2012 — Annex-I element gating for MPCO claims.

EU Regulation 722/2012 supplements MDR for medical devices manufactured
utilising tissues or cells of animal origin. Its Annex I specifies four
element domains whose adequate treatment a Notified Body will assess
when reviewing a 722/2012-applicable submission:

    GEOGRAPHIC_ORIGIN       Source country / herd / animal-origin geography.
    TSE_RISK_ASSESSMENT     Transmissible spongiform encephalopathy risk.
    INACTIVATION_PROCEDURE  Pathogen-inactivation / -elimination process.
    TRACEABILITY            End-to-end batch / supplier traceability.

This module is the *contract* layer: it defines the canonical element
identifiers, the conservative-defensive mapping of which elements are
potentially in scope for a given :class:`ClaimType`, and the verbatim
list of regulatory anchor strings cited whenever an element is invoked.

The mapping is **conservative-defensive**: it lists elements
*potentially relevant* to a claim type, not elements *necessarily
required*. The Stufe 1.7 appraisal step (`meddev_a6_appraisal.py`)
reduces the potential set to the elements actually required by the
specific claim's evidence — per Handoff 26-06-27 Decision #29, this
module's responsibility ends at the mapping; sufficiency validation is
explicitly out of scope.

Per Decision #32, the MPCO adapter must gate on
``MPCOClaim.applicable_regulation == "722_2012"`` *before* invoking
:func:`elements_in_scope`. Generic (non-722/2012) projects must remain
free of Annex-I enrichment. This module does not enforce the gate; it
trusts the adapter to do so.

The mapping (`U-1.6-B`) was approved by Michael at the Stufe 1.6
mid-session checkpoint on 2026-06-27.

The regulatory anchor strings (`REGULATORY_ANCHORS`) are reproduced
**verbatim** from the cited regulations and must never be paraphrased,
abbreviated, or reordered. This is a hard project convention (see
Handoff Decision #16; the "Key learnings" log: *regulatory language
must be reproduced verbatim*).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from ring2.adapters.mpco.claim_type_classifier import ClaimType

__all__ = [
    "ANNEX_I_SCOPE_BY_CLAIM_TYPE",
    "REGULATORY_ANCHORS",
    "AnnexIElement",
    "elements_in_scope",
    "regulatory_anchors",
]


# ---------------------------------------------------------------------------
# Annex-I element enum
# ---------------------------------------------------------------------------


class AnnexIElement(StrEnum):
    """The four Annex-I element domains of EU Regulation 722/2012.

    Values are hyphen-canonical strings consistent with the project's
    enum-value convention (see also :class:`ExclusionCode`,
    :class:`PrismaPhase`). They are stable identifiers used in audit
    artefacts and YAML serialisation; do not change them without a
    schema-versioning decision.
    """

    GEOGRAPHIC_ORIGIN = "geographic-origin"
    TSE_RISK_ASSESSMENT = "tse-risk-assessment"
    INACTIVATION_PROCEDURE = "inactivation-procedure"
    TRACEABILITY = "traceability"


# ---------------------------------------------------------------------------
# Verbatim regulatory anchors
# ---------------------------------------------------------------------------


#: Verbatim citation strings for every regulation, MDR clause, and
#: agency document cited by this module. Reproduced exactly as in the
#: source regulation; **never paraphrase, abbreviate, or reorder**.
#:
#: Order is part of the contract: the operative regulation first
#: (722/2012 Annex I), then MDR clauses that scope or anchor it, then
#: flanking guidance (EMA, Commission Decision).
REGULATORY_ANCHORS: tuple[str, ...] = (
    "Regulation (EU) No 722/2012, Annex I",
    "MDR Rule 18 (Annex VIII)",
    "MDR Annex I, GSPR 13.2(c)",
    "MDR Annex VII, Section 4.5.6",
    "EMA/410/01 Rev. 3",
    "Commission Decision 2007/453/EC",
)


# ---------------------------------------------------------------------------
# Claim-type → Annex-I scope mapping (U-1.6-B)
# ---------------------------------------------------------------------------


#: Conservative-defensive mapping ``ClaimType → frozenset[AnnexIElement]``.
#:
#: Approved as U-1.6-B at the Stufe 1.6 mid-session checkpoint
#: (2026-06-27). Exhaustive over :class:`ClaimType`; exhaustiveness
#: is enforced by tests.
#:
#: Rationale per claim type:
#:   * ``REGULATORY_COMPLIANCE`` — all four elements may be invoked;
#:     a regulatory-framed claim about animal-origin material can
#:     touch any Annex-I domain.
#:   * ``BIOCHEMISTRY_MATERIAL_PROPERTY`` — TSE risk and pathogen
#:     inactivation are the material/process-level elements; geographic
#:     origin and traceability are upstream and downstream of the
#:     biochemistry itself.
#:   * ``SAFETY_ALLERGENICITY`` — geographic origin (herd-level
#:     antigenic variation) and TSE risk are the safety-relevant
#:     source-side elements.
#:   * ``CLINICAL_PERFORMANCE`` — empty: clinical performance claims
#:     are device-level, not source- or process-level, and therefore
#:     do not engage Annex-I directly.
#:   * ``HISTORICAL_MARKET_USE`` — traceability anchors any historical
#:     claim; TSE risk because historical exposure horizons are part
#:     of the EMA/410/01 framework.
#:   * ``UNKNOWN`` — empty: an unclassified claim cannot be assumed
#:     to engage Annex-I.
ANNEX_I_SCOPE_BY_CLAIM_TYPE: Mapping[ClaimType, frozenset[AnnexIElement]] = MappingProxyType(
    {
        ClaimType.REGULATORY_COMPLIANCE: frozenset(
            {
                AnnexIElement.GEOGRAPHIC_ORIGIN,
                AnnexIElement.TSE_RISK_ASSESSMENT,
                AnnexIElement.INACTIVATION_PROCEDURE,
                AnnexIElement.TRACEABILITY,
            }
        ),
        ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY: frozenset(
            {
                AnnexIElement.TSE_RISK_ASSESSMENT,
                AnnexIElement.INACTIVATION_PROCEDURE,
            }
        ),
        ClaimType.SAFETY_ALLERGENICITY: frozenset(
            {
                AnnexIElement.GEOGRAPHIC_ORIGIN,
                AnnexIElement.TSE_RISK_ASSESSMENT,
            }
        ),
        ClaimType.CLINICAL_PERFORMANCE: frozenset(),
        ClaimType.HISTORICAL_MARKET_USE: frozenset(
            {
                AnnexIElement.TRACEABILITY,
                AnnexIElement.TSE_RISK_ASSESSMENT,
            }
        ),
        ClaimType.UNKNOWN: frozenset(),
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def elements_in_scope(claim_type: ClaimType) -> frozenset[AnnexIElement]:
    """Return the Annex-I elements potentially in scope for ``claim_type``.

    The returned set is a **superset** of what may actually be required
    by any specific claim's evidence. The Stufe 1.7 appraisal step
    reduces this potential set to the elements actually engaged by the
    claim's evidence; this module does not perform that reduction.

    Per Handoff Decision #32, callers must gate on
    ``MPCOClaim.applicable_regulation == "722_2012"`` *before* invoking
    this function — generic (non-722/2012) projects must not see
    Annex-I enrichment. This function does not enforce the gate; the
    adapter is responsible for it.

    Args:
        claim_type: a :class:`ClaimType` member.

    Returns:
        the :class:`frozenset` of :class:`AnnexIElement` values
        potentially in scope. May be empty (e.g. ``CLINICAL_PERFORMANCE``,
        ``UNKNOWN``).

    Raises:
        ValueError: if ``claim_type`` is not a known :class:`ClaimType`.
            Should be unreachable at runtime given the type signature;
            guarded for defensive use against e.g. deserialised data.
    """
    try:
        return ANNEX_I_SCOPE_BY_CLAIM_TYPE[claim_type]
    except KeyError as e:  # pragma: no cover — exhaustiveness enforced by tests
        raise ValueError(
            f"ClaimType {claim_type!r} has no Annex-I scope mapping; "
            "ANNEX_I_SCOPE_BY_CLAIM_TYPE is incomplete"
        ) from e


def regulatory_anchors() -> tuple[str, ...]:
    """Return the verbatim regulatory anchor strings for 722/2012 element invocation.

    These are the exact, never-paraphrased citation strings to be
    embedded in audit artefacts whenever an Annex-I element is invoked
    in the appraisal of an MPCO claim. The order is part of the
    contract (see :data:`REGULATORY_ANCHORS`).

    Returns:
        an immutable :class:`tuple` of verbatim citation strings.
    """
    return REGULATORY_ANCHORS

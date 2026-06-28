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
"""Pure-function factory for MPCO inclusion/exclusion criteria (Stufe 1.8 Inkrement 5b).

Extracted from :mod:`ring2.adapters.mpco.adapter` so that the report
renderer can construct §3 (inclusion criteria) and §4 (exclusion
criteria) directly from an :class:`MPCOClaim` without needing an
:class:`MPCOAdapter` instance.

Per ``U-1.8`` design decision (Option A — module-level functions): the
factory functions take :class:`MPCOClaim` directly and are pure (no
side effects, no I/O, deterministic). The adapter's
:meth:`MPCOAdapter.inclusion_criteria` and
:meth:`MPCOAdapter.exclusion_criteria` delegate to these functions
after type-narrowing the core :class:`Question` Protocol to
:class:`MPCOClaim`.

Verbatim language convention:
    All description strings in :data:`ANNEX_I_DESCRIPTIONS` and
    :data:`EXCLUSION_DESCRIPTIONS` are reproduced verbatim and must not
    be paraphrased. The Annex-I descriptions in particular end with the
    canonical regulatory anchor
    ``"per Regulation (EU) No 722/2012, Annex I."`` which downstream
    audit artefacts pick up unchanged.

Public surface:
    * :data:`BASE_INCLUSION` — universal MPCO inclusion criterion.
    * :data:`ANNEX_I_DESCRIPTIONS` — verbatim descriptions per
      :class:`AnnexIElement`.
    * :data:`EXCLUSION_DESCRIPTIONS` — verbatim descriptions per
      :class:`ExclusionCode`.
    * :func:`annex_i_criterion_id` — build the canonical
      ``INC-722-*`` criterion id for an Annex-I element.
    * :func:`make_inclusion_criteria` — build the
      :class:`InclusionCriteria` set for a claim.
    * :func:`make_exclusion_criteria` — build the full
      :class:`ExclusionCriteria` set (all five codes spanning all three
      PRISMA phases). Callers that need a phase-restricted subset must
      filter using :func:`codes_for_phase` themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ring2.adapters.mpco.exclusion_codes import ExclusionCode
from ring2.adapters.mpco.reg_722_2012 import AnnexIElement, elements_in_scope
from ring2.adapters.mpco.schema import MPCOClaim
from ring2.core.adapter_base import (
    ExclusionCriteria,
    ExclusionCriterion,
    InclusionCriteria,
    InclusionCriterion,
)

__all__ = [
    "ANNEX_I_DESCRIPTIONS",
    "BASE_INCLUSION",
    "EXCLUSION_DESCRIPTIONS",
    "annex_i_criterion_id",
    "make_exclusion_criteria",
    "make_inclusion_criteria",
]


# ---------------------------------------------------------------------------
# Static description tables (verbatim)
# ---------------------------------------------------------------------------


#: Human-readable descriptions for every :class:`ExclusionCode`. Wording
#: tracks the module docstring of :mod:`ring2.adapters.mpco.exclusion_codes`
#: so that the description seen by the screening LLM is the same wording
#: the audit trail and any reviewer documentation use.
EXCLUSION_DESCRIPTIONS: Mapping[ExclusionCode, str] = MappingProxyType(
    {
        ExclusionCode.LANGUAGE: "Title/abstract not in an accepted language.",
        ExclusionCode.IRRELEVANT: "Topic mismatch detectable from title/abstract alone.",
        ExclusionCode.DUPLICATE: "Same PMID or DOI seen earlier in the deduplication step.",
        ExclusionCode.NO_FULLTEXT: "Full text could not be obtained for eligibility check.",
        ExclusionCode.A6_CATALOG: (
            "Falls under a MEDDEV 2.7/1 Rev. 4 §A6 exclusion category (eligibility-phase only)."
        ),
    }
)


#: Human-readable descriptions for every :class:`AnnexIElement`. Each
#: ends with the verbatim regulatory anchor ``"per Regulation (EU) No
#: 722/2012, Annex I."`` so that the audit trail picks up the citation
#: directly from the inclusion criterion. Per the project's verbatim-
#: language convention, the trailing citation string must be reproduced
#: exactly as written here.
ANNEX_I_DESCRIPTIONS: Mapping[AnnexIElement, str] = MappingProxyType(
    {
        AnnexIElement.GEOGRAPHIC_ORIGIN: (
            "Evidence addresses geographic origin per Regulation (EU) No 722/2012, Annex I."
        ),
        AnnexIElement.TSE_RISK_ASSESSMENT: (
            "Evidence addresses TSE risk assessment per Regulation (EU) No 722/2012, Annex I."
        ),
        AnnexIElement.INACTIVATION_PROCEDURE: (
            "Evidence addresses pathogen inactivation procedure per "
            "Regulation (EU) No 722/2012, Annex I."
        ),
        AnnexIElement.TRACEABILITY: (
            "Evidence addresses traceability per Regulation (EU) No 722/2012, Annex I."
        ),
    }
)


#: The universal MPCO inclusion criterion, applied to every claim
#: regardless of ``applicable_regulation``.
BASE_INCLUSION = InclusionCriterion(
    id="INC-001",
    description="Evidence is relevant to the MPCO claim under appraisal.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def annex_i_criterion_id(element: AnnexIElement) -> str:
    """Build the canonical ``INC-722-*`` criterion id for an Annex-I element.

    Example: ``AnnexIElement.TSE_RISK_ASSESSMENT`` →
    ``"INC-722-TSE-RISK-ASSESSMENT"``.
    """
    return f"INC-722-{element.value.upper()}"


# ---------------------------------------------------------------------------
# Pure factories
# ---------------------------------------------------------------------------


def make_inclusion_criteria(claim: MPCOClaim) -> InclusionCriteria:
    """Return the inclusion-criteria set for an MPCO claim.

    Always includes the universal :data:`BASE_INCLUSION`. When the
    claim's ``applicable_regulation == "722_2012"``, additionally
    emits one criterion per Annex-I element in
    :func:`elements_in_scope` for the claim's ``claim_type``.

    Annex-I criteria are emitted in :class:`AnnexIElement` declaration
    order (upstream-to-downstream: origin → TSE risk → inactivation →
    traceability) so output ordering is deterministic and
    regulation-aligned.

    Pure: identical input claim → identical output. No side effects, no
    I/O.

    Args:
        claim: the :class:`MPCOClaim` for which to build inclusion
            criteria.

    Returns:
        an immutable :class:`InclusionCriteria` set.
    """
    criteria: list[InclusionCriterion] = [BASE_INCLUSION]

    if claim.applicable_regulation == "722_2012":
        in_scope = elements_in_scope(claim.claim_type)
        # Iterate enum in declaration order, filter to in-scope set, so
        # output ordering is deterministic and regulation-aligned.
        for element in AnnexIElement:
            if element in in_scope:
                criteria.append(
                    InclusionCriterion(
                        id=annex_i_criterion_id(element),
                        description=ANNEX_I_DESCRIPTIONS[element],
                    )
                )

    return InclusionCriteria(criteria=tuple(criteria))


def make_exclusion_criteria(claim: MPCOClaim) -> ExclusionCriteria:
    """Return the full MPCO exclusion-criteria set (all 5 codes).

    The full set spans all three PRISMA phases — deduplication
    (``EX-DUPLICATE``), screening (``EX-LANGUAGE``, ``EX-IRRELEVANT``),
    and eligibility (``EX-NO-FULLTEXT``, ``EX-A6-CATALOG``). Callers
    that need only one phase's subset (e.g. ``MPCOAdapter.appraise``)
    must filter using :func:`codes_for_phase` themselves.

    ``claim`` is currently unused — all MPCO claims share the same
    exclusion set in Stufe 1.8 — but is kept in the signature for
    symmetry with :func:`make_inclusion_criteria` and reserved for
    per-claim variation in later stages.

    Pure: deterministic, no side effects, no I/O. Criteria are emitted
    in :class:`ExclusionCode` enum-declaration order.

    Args:
        claim: the :class:`MPCOClaim` (reserved for future per-claim
            variation; currently unused).

    Returns:
        an immutable :class:`ExclusionCriteria` set containing all five
        codes.
    """
    return ExclusionCriteria(
        criteria=tuple(
            ExclusionCriterion(code=code.value, description=EXCLUSION_DESCRIPTIONS[code])
            for code in ExclusionCode
        )
    )

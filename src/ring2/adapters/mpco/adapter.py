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
"""MPCO adapter — concrete :class:`Adapter` binding the foundation modules.

The MPCO adapter is the integration layer of Stufe 1.6. It wires together
the foundation modules built in this stage:

    * :mod:`ring2.adapters.mpco.schema` — :class:`MPCOClaim`,
      :class:`MPCOSchemaDefinition`.
    * :mod:`ring2.adapters.mpco.claim_type_classifier` — :class:`ClaimType`.
    * :mod:`ring2.adapters.mpco.exclusion_codes` — :class:`ExclusionCode`,
      :class:`PrismaPhase`, :func:`codes_for_phase`.
    * :mod:`ring2.adapters.mpco.reg_722_2012` — :class:`AnnexIElement`,
      :func:`elements_in_scope`.

…and binds them into the core's :class:`Adapter` ABC.

The screening LLM enters through the constructor as a
:class:`ScreenerCaller` so the :class:`Adapter.appraise` signature stays
canonical ``(record, question)``. When no caller is supplied, the
adapter holds a :class:`NullScreenerCaller` that fails loudly on first
use — never silently degrading to a placeholder decision. This default
allows zero-arg instantiation (useful for registry round-trip checks)
without weakening production safety.

Per Handoff Decision #29, full §A6-catalog appraisal lives in Stufe 1.7
(``meddev_a6_appraisal.py``). :meth:`MPCOAdapter.appraise` therefore
filters the exclusion set down to the **screening-phase** codes
(``EX-LANGUAGE``, ``EX-IRRELEVANT``) before delegating to
:func:`screen_record` — eligibility-phase codes (``EX-NO-FULLTEXT``,
``EX-A6-CATALOG``) are not raisable from the screening step, and
deduplication codes (``EX-DUPLICATE``) are the upstream pipeline's
concern. This filter is the contract that prevents a screening LLM
from labelling a record with a code it cannot legitimately apply at
this phase.

Per Handoff Decision #32, the ``applicable_regulation`` toggle on
:class:`MPCOClaim` is the gate for 722/2012 Annex-I enrichment of the
inclusion criteria; the gating happens here at the adapter layer so
generic (non-722/2012) projects remain clean of regulation-specific
artefacts.

Reporting (:meth:`render_report`) delegates to
:mod:`ring2.adapters.mpco.report_renderer`. Per the Weg-3 decision
(Stufe 1.7), the report is interim by design: only state-derived
sections are populated, with claim- and decision-aware sections held as
numbered pending placeholders awaiting orchestrator wire-up in
Stufe 1.8.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ring2.adapters.mpco.exclusion_codes import (
    ExclusionCode,
    PrismaPhase,
    codes_for_phase,
)
from ring2.adapters.mpco.reg_722_2012 import AnnexIElement, elements_in_scope
from ring2.adapters.mpco.schema import MPCOClaim, MPCOSchemaDefinition
from ring2.core.adapter_base import (
    Adapter,
    AppraisalDecision,
    ExclusionCriteria,
    ExclusionCriterion,
    InclusionCriteria,
    InclusionCriterion,
    PubMedRecord,
    Question,
    RenderContext,
    ReportArtefact,
    Schema,
    SessionState,
    register,
)
from ring2.core.screening import NullScreenerCaller, ScreenerCaller, screen_record

__all__ = ["MPCOAdapter"]


# ---------------------------------------------------------------------------
# Static description tables
# ---------------------------------------------------------------------------


#: Human-readable descriptions for every :class:`ExclusionCode`. Wording
#: tracks the module docstring of :mod:`ring2.adapters.mpco.exclusion_codes`
#: so that the description seen by the screening LLM is the same wording
#: the audit trail and any reviewer documentation use.
_EXCLUSION_DESCRIPTIONS: Mapping[ExclusionCode, str] = MappingProxyType(
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
_ANNEX_I_DESCRIPTIONS: Mapping[AnnexIElement, str] = MappingProxyType(
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
_BASE_INCLUSION = InclusionCriterion(
    id="INC-001",
    description="Evidence is relevant to the MPCO claim under appraisal.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _annex_i_criterion_id(element: AnnexIElement) -> str:
    """Build the canonical ``INC-722-*`` criterion id for an Annex-I element.

    Example: ``AnnexIElement.TSE_RISK_ASSESSMENT`` →
    ``"INC-722-TSE-RISK-ASSESSMENT"``.
    """
    return f"INC-722-{element.value.upper()}"


def _require_mpco_claim(question: Question, method: str) -> MPCOClaim:
    """Type-narrow ``question`` to :class:`MPCOClaim` or raise.

    The :class:`Question` Protocol is intentionally minimal at the core
    layer (only ``claim_id``). At the adapter layer we need access to
    the full :class:`MPCOClaim` fields (``applicable_regulation``,
    ``claim_type``, …), so a hard type-check is appropriate here.
    """
    if not isinstance(question, MPCOClaim):
        raise TypeError(
            f"MPCOAdapter.{method} requires an MPCOClaim instance; got {type(question).__name__}"
        )
    return question


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


@register
class MPCOAdapter(Adapter):
    """Concrete :class:`Adapter` for material-evidence (MPCO) projects.

    Construction:

        >>> adapter = MPCOAdapter()                      # registry round-trip OK
        >>> adapter = MPCOAdapter(caller=production_llm)  # production usage

    The default no-arg constructor installs a :class:`NullScreenerCaller`
    that raises on use — meaning :meth:`appraise` will fail loudly if
    invoked without an explicit caller, never returning a silent
    placeholder decision. This is intentional safety.

    Per the project's architecture and Handoff Decisions:

        * **Decision #29** — §A6 catalog application is Stufe 1.7's
          responsibility; this adapter's :meth:`appraise` is the
          screening step only and operates with the screening-phase
          subset of exclusion codes.
        * **Decision #32** — the ``applicable_regulation`` toggle gates
          722/2012 Annex-I enrichment of the inclusion criteria; the
          gating happens here at the adapter layer.
    """

    name: str = "MPCO"

    def __init__(self, caller: ScreenerCaller | None = None) -> None:
        """Construct the MPCO adapter.

        Args:
            caller: the screening LLM bridge implementing
                :class:`ScreenerCaller`. If ``None``, a
                :class:`NullScreenerCaller` is installed that raises on
                first :meth:`appraise` call.
        """
        self._caller: ScreenerCaller = caller or NullScreenerCaller(
            "MPCOAdapter constructed without ScreenerCaller; inject one before calling appraise()."
        )

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @property
    def schema(self) -> Schema:
        """Return the MPCO schema descriptor."""
        return MPCOSchemaDefinition()

    # ------------------------------------------------------------------
    # Criteria
    # ------------------------------------------------------------------

    def inclusion_criteria(self, question: Question) -> InclusionCriteria:
        """Return inclusion criteria for an MPCO claim.

        Always includes the universal :data:`_BASE_INCLUSION`. When the
        claim's ``applicable_regulation == "722_2012"``, additionally
        emits one criterion per Annex-I element in
        :func:`elements_in_scope` for the claim's :class:`ClaimType`.

        Annex-I criteria are emitted in :class:`AnnexIElement`
        declaration order (upstream-to-downstream: origin → TSE risk
        → inactivation → traceability) for deterministic output.

        Raises:
            TypeError: if ``question`` is not an :class:`MPCOClaim`.
        """
        claim = _require_mpco_claim(question, "inclusion_criteria")
        criteria: list[InclusionCriterion] = [_BASE_INCLUSION]

        if claim.applicable_regulation == "722_2012":
            in_scope = elements_in_scope(claim.claim_type)
            # Iterate enum in declaration order, filter to in-scope set,
            # so output ordering is deterministic and regulation-aligned.
            for element in AnnexIElement:
                if element in in_scope:
                    criteria.append(
                        InclusionCriterion(
                            id=_annex_i_criterion_id(element),
                            description=_ANNEX_I_DESCRIPTIONS[element],
                        )
                    )

        return InclusionCriteria(criteria=tuple(criteria))

    def exclusion_criteria(self, question: Question) -> ExclusionCriteria:
        """Return the full MPCO exclusion-criteria set (all 5 codes).

        The full set spans all three PRISMA phases — deduplication
        (``EX-DUPLICATE``), screening (``EX-LANGUAGE``,
        ``EX-IRRELEVANT``), and eligibility (``EX-NO-FULLTEXT``,
        ``EX-A6-CATALOG``). Callers that need only the screening-phase
        subset (e.g. :meth:`appraise`) must filter using
        :func:`codes_for_phase` themselves.

        ``question`` is currently unused — all MPCO claims share the
        same exclusion set in Stufe 1.6 — but is kept in the signature
        per the :class:`Adapter` ABC and reserved for per-claim
        variation in later stages.

        Raises:
            TypeError: if ``question`` is not an :class:`MPCOClaim`.
        """
        _require_mpco_claim(question, "exclusion_criteria")
        return ExclusionCriteria(
            criteria=tuple(
                ExclusionCriterion(code=code.value, description=_EXCLUSION_DESCRIPTIONS[code])
                for code in ExclusionCode
            )
        )

    # ------------------------------------------------------------------
    # Appraisal — screening-phase only in Stufe 1.6
    # ------------------------------------------------------------------

    def appraise(self, record: PubMedRecord, question: Question) -> AppraisalDecision:
        """Screen one record against one MPCO claim.

        Delegates to :func:`screen_record` with the injected
        :class:`ScreenerCaller`. Before delegating, the exclusion-
        criterion set is **filtered to the screening phase only** —
        :func:`codes_for_phase(PrismaPhase.SCREENING) <codes_for_phase>`
        — so that the LLM cannot legitimately emit an eligibility- or
        deduplication-phase code at this step. Any such emission is
        caught and converted to a :class:`ValueError` by
        :func:`screen_record`'s validation.

        Raises:
            TypeError: if ``question`` is not an :class:`MPCOClaim`.
            ValueError: propagated from :func:`screen_record` when the
                caller returns a malformed or out-of-phase response.
            RuntimeError: propagated when the adapter is using a
                :class:`NullScreenerCaller` (the default).
        """
        _require_mpco_claim(question, "appraise")

        inclusion = self.inclusion_criteria(question)
        full_exclusion = self.exclusion_criteria(question)

        # Filter to screening-phase codes only. The screening LLM must
        # not be invited to apply EX-DUPLICATE (deduplication) or
        # EX-NO-FULLTEXT / EX-A6-CATALOG (eligibility) at this phase.
        screening_code_values: frozenset[str] = frozenset(
            c.value for c in codes_for_phase(PrismaPhase.SCREENING)
        )
        screening_exclusion = ExclusionCriteria(
            criteria=tuple(c for c in full_exclusion.criteria if c.code in screening_code_values)
        )

        return screen_record(record, inclusion, screening_exclusion, caller=self._caller)

    # ------------------------------------------------------------------
    # Reporting — interim renderer (Stufe 1.7)
    # ------------------------------------------------------------------

    def render_report(
        self,
        state: SessionState,
        context: RenderContext | None = None,
    ) -> ReportArtefact:
        """Render the interim MPCO markdown report for one session.

        Delegates to :func:`render_mpco_report`. When ``context`` is
        ``None``, the renderer produces the Stufe-1.7 interim report
        (all §2-§9 PENDING). When ``context`` is an
        :class:`MPCORenderContext`, the renderer fills §2 / §5 / §6 / §7
        from the context (Stufe-1.8 Inkrement 4); §3 / §4 / §8 / §9
        remain PENDING with their own deferral reasons.

        Args:
            state: a :class:`SessionState` Protocol instance. In
                production this is :class:`SessionStateImpl`; the
                renderer reads only its public attributes
                (``project_id``, ``claim_id``, ``session_dir``,
                ``status_map``, ``batch_files``).
            context: optional render context. The ABC types this as the
                empty :class:`RenderContext` Protocol marker for
                adapter-agnosticism; the MPCO renderer accepts a
                concrete :class:`MPCORenderContext` (any other concrete
                shape is undefined behaviour, surfaced as a downstream
                AttributeError if the renderer reaches for fields the
                object does not carry).
        """
        # Defer the import so this module's import graph stays minimal
        # at the ABC level (renderer pulls in stdlib ``importlib.metadata``
        # which we don't need just to construct an MPCOAdapter).
        from ring2.adapters.mpco.report_renderer import render_mpco_report

        # SessionState is a Protocol; render_mpco_report is typed against
        # SessionStateImpl but only reads attributes that the Protocol
        # itself promises plus three further attributes that any real
        # implementation (including SessionStateImpl) provides. The
        # context, when set, is structurally an MPCORenderContext (which
        # satisfies the empty core RenderContext Protocol).
        return render_mpco_report(state, context)  # type: ignore[arg-type]

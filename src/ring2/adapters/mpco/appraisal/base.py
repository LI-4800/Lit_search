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
"""Appraisal base — :class:`AppraisalResult` and :class:`AppraisalLens` ABC.

An *appraisal lens* is one claim-type-appropriate methodology for
appraising the literature evidence supporting an :class:`MPCOClaim`.
Different claim types call for different lenses — see the appraisal
matrix in Handoff v6 (Stufe 1.8):

    ====================================  ===========================
    Claim type                            Default lens (others reg.)
    ====================================  ===========================
    BIOCHEMISTRY_MATERIAL_PROPERTY        glp_oecd
    SAFETY_ALLERGENICITY                  care_caseseries
    CLINICAL_PERFORMANCE                  rob2
    HISTORICAL_MARKET_USE                 registry_authoritativeness
    ====================================  ===========================

The ``REGULATORY_COMPLIANCE`` claim type is intentionally **not** in
the matrix — regulatory claims are settled by citation resolution, not
literature appraisal, so the adapter forks them into a separate
non-lens path (Stufe 1.10+).

Per-claim lens selection is by project YAML (option (b) from
Handoff v6) — defaults above, overridable per claim type. For
CB-bov-01 the project YAML pins ``clinical_performance.lens =
meddev_a6`` (Inkrement 7) because the MDR context demands it; RING2
itself stays generic with ``rob2`` as the default.

Schema:
    :class:`AppraisalResult` is the base Pydantic v2 frozen model
    returned by :meth:`AppraisalLens.appraise`. Lens-specific data
    (e.g. MEDDEV §A6 categories, RoB2 domain judgements) lives in
    lens-specific subclasses defined in their own modules, with
    typed fields. Generic consumers — e.g. the report renderer's §8
    headline counts — use only the base fields.

Verbatim-language convention applies to :attr:`AppraisalResult.rationale`:
    rationale strings should reproduce the controlling regulatory or
    methodological text verbatim where one exists, with at most a
    short application note. Paraphrasing the regulatory portion is
    not permitted.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import MPCOClaim
from ring2.core.adapter_base import PubMedRecord

__all__ = [
    "AppraisalLens",
    "AppraisalResult",
]


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


class AppraisalResult(BaseModel):
    """Result of appraising one record with one lens.

    This is the base class. Lens-specific subclasses (e.g.
    ``MeddevA6Result``, ``Rob2Result``) extend it with typed fields
    carrying the lens-specific findings. Generic consumers must rely
    on the base fields only; lens-specific consumers cast to the
    expected subclass.

    Attributes:
        pmid: PubMed identifier of the record being appraised.
        lens_name: registry key of the lens that produced this
            result. Audit-trail required — multiple lenses may
            appraise the same record in later stages, and the
            artefacts must remain disambiguated.
        rationale: verbatim narrative justification for the verdict.
            For lenses driven by a regulatory or normative text
            (e.g. MEDDEV §A6), this should reproduce the controlling
            text verbatim plus a short application note.
        qualifies: did this record pass the lens's threshold for
            "qualifies as supporting evidence under this lens"?
            Each lens defines its own threshold. Generic consumers
            (e.g. §8 count of "appraised: N, qualifying: M") read
            this flag. Lens-specific reasoning lives in the subclass.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pmid: str
    lens_name: str
    rationale: str
    qualifies: bool


# ---------------------------------------------------------------------------
# Lens ABC
# ---------------------------------------------------------------------------


class AppraisalLens(ABC):
    """Abstract base class for one appraisal lens.

    Subclasses must set :attr:`name` and :attr:`applicable_claim_types`
    as :class:`ClassVar` and implement :meth:`appraise` and
    :meth:`render_summary`.

    Registration:
        Subclasses are typically registered with the lens registry via
        the :func:`register_lens` decorator. Registration is the only
        way an orchestrator or test can discover lenses by name.

    Attributes:
        name: registry key for this lens. Convention: lowercase with
            underscores, matching the method/standard name (e.g.
            ``"rob2"``, ``"meddev_a6"``, ``"glp_oecd"``).
        applicable_claim_types: the set of :class:`ClaimType` values
            this lens is methodologically appropriate for. Used by
            the project-YAML loader to validate that a configured
            lens-claim-type binding is sensible — e.g. assigning
            ``rob2`` to ``HISTORICAL_MARKET_USE`` would be flagged
            because ``CLINICAL_PERFORMANCE`` is not in that lens's
            applicable set.
    """

    name: ClassVar[str]
    applicable_claim_types: ClassVar[frozenset[ClaimType]]

    @abstractmethod
    def appraise(self, record: PubMedRecord, claim: MPCOClaim) -> AppraisalResult:
        """Appraise one record against one MPCO claim under this lens.

        Args:
            record: the :class:`PubMedRecord` to appraise. Lenses that
                need full-text access beyond the abstract are
                responsible for fetching it themselves; the screening
                step has already ensured the record is past the
                full-text-availability gate where required.
            claim: the :class:`MPCOClaim` providing context (material,
                property, comparator, outcome, claim_type, etc.).

        Returns:
            An :class:`AppraisalResult` (or lens-specific subclass).
        """

    @abstractmethod
    def render_summary(self, results: tuple[AppraisalResult, ...]) -> str:
        """Render a markdown summary of appraisal results for §8 of the report.

        Args:
            results: tuple of :class:`AppraisalResult` produced by
                this lens. Implementations may need to cast to their
                own subclass for lens-specific aggregation
                (e.g. category coverage for MEDDEV §A6).

        Returns:
            A markdown string forming the body of the §8 sub-section
            for this lens. The section header is the renderer's
            responsibility.
        """

    def is_operational(self) -> bool:
        """Whether this lens instance is ready to produce real appraisal results.

        The default implementation returns ``False`` — a fail-safe
        choice so that registry stubs (e.g. ``Rob2Lens``,
        ``GlpOecdLens``) and lenses constructed with a null dependency
        (e.g. :class:`~ring2.adapters.mpco.appraisal.meddev_a6.MeddevA6Lens`
        with :class:`~ring2.adapters.mpco.appraisal.meddev_a6.NullA6Classifier`)
        all report ``False`` without further action. A lens that is
        truly ready to appraise records must override this and return
        ``True`` (or compute readiness from its injected dependencies,
        as ``MeddevA6Lens`` does for its classifier slot).

        The orchestrator's appraisal dispatcher consults this flag
        *before* calling :meth:`appraise`. When ``False``, the
        dispatcher does not invoke ``appraise`` (avoiding the
        ``NotImplementedError`` / ``ValueError`` that stubs and null-
        dependency lenses would raise) and emits a pending marker for
        each eligible record instead. The §8 report renderer turns
        those markers into an *"awaiting classifier/implementation"*
        sub-section per the Stufe-1.9a NullClassifier-aware-bypass
        decision.

        Returns:
            ``True`` if :meth:`appraise` is safe to invoke on this
            instance; ``False`` otherwise.
        """
        return False

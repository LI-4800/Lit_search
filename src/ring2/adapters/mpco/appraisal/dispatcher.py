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
"""Appraisal dispatcher — claim → configured lens → results.

Stufe 1.9a Inkrement 2. Connects the project-level
:class:`~ring2.core.project_config.AppraisalConfig` (lens selection per
claim type) to the appraisal subpackage's lens implementations, and to
the orchestrator-supplied list of eligible records.

Behaviour:
    1. Resolve the claim's :class:`ClaimType` to a configured lens name
       via :meth:`AppraisalConfig.lens_for`.
    2. Instantiate the lens via the lens factory (default: instantiate
       the registry-resolved class with no arguments). Tests may inject
       a custom factory to bypass the registry.
    3. Check :meth:`AppraisalLens.is_operational`:

       * Operational → call ``lens.appraise(record, claim)`` for every
         eligible record and collect the results.
       * Not operational → emit one :class:`PendingAppraisalResult` per
         eligible record. The renderer (Inkrement 3) turns these into
         the *"awaiting classifier"* §8 sub-section per the
         Stufe-1.9a NullClassifier-aware-bypass decision.

    4. Return ``{claim.claim_type: [results...]}`` — a single-entry
       dict for Stufe-1.9a (one claim per run); the dict shape is
       carried forward for future multi-claim runs.

Special cases:
    * Claim type ``REGULATORY_COMPLIANCE``: dispatcher returns an
      empty mapping. Regulatory claims do not pass through an
      appraisal lens; they will be resolved by a separate
      reference-resolution path (Stufe 1.10+).
    * Claim type ``UNKNOWN``: dispatcher returns an empty mapping.
      A classified-as-UNKNOWN claim is a workflow error upstream;
      the dispatcher does not silently fabricate a verdict.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from ring2.adapters.mpco.appraisal.base import AppraisalLens, AppraisalResult
from ring2.adapters.mpco.appraisal.registry import get_lens
from ring2.adapters.mpco.claim_type_classifier import ClaimType

if TYPE_CHECKING:
    from ring2.adapters.mpco.schema import MPCOClaim
    from ring2.core.adapter_base import PubMedRecord
    from ring2.core.project_config import AppraisalConfig


__all__ = [
    "AppraisalDispatcher",
    "LensFactory",
    "PendingAppraisalResult",
]


# ---------------------------------------------------------------------------
# Pending-marker result
# ---------------------------------------------------------------------------


_PENDING_RATIONALE = (
    "Lens is not operational — appraise() was not invoked. "
    "Record is eligible for appraisal, but the configured lens "
    "requires further implementation (classifier injection or full "
    "methodology) before a verdict can be produced. "
    "Scheduled: Stufe 1.9b / 1.10+."
)


class PendingAppraisalResult(AppraisalResult):
    """Marker subclass emitted by the dispatcher when a lens is non-operational.

    Carries the eligible-record's PMID, the configured lens name (so
    the §8 renderer can attribute the pending state to the right
    lens), a fixed rationale string, and ``qualifies=False`` (a record
    that has not been appraised cannot be claimed as supporting
    evidence).

    The §8 renderer detects pending results via ``isinstance`` checks
    and switches its output mode for that lens to the
    *"awaiting classifier/implementation"* sub-section.
    """

    # Same model_config as the parent; declared explicitly for clarity.
    # (frozen=True, extra="forbid" — inherited.)


# ---------------------------------------------------------------------------
# Lens factory
# ---------------------------------------------------------------------------


LensFactory = Callable[[str], AppraisalLens]
"""Callable: lens name → :class:`AppraisalLens` instance.

The default factory resolves the name in the registry and instantiates
the resulting class with no constructor arguments. Tests may inject a
custom factory to (a) return mock lens instances, or (b) instantiate
real lenses with dependencies pre-wired (e.g.
``MeddevA6Lens(classifier=real_clf)``).
"""


def _default_lens_factory(name: str) -> AppraisalLens:
    """Resolve ``name`` in the registry and instantiate with no arguments.

    Raises:
        KeyError: if no lens is registered under ``name``. The
            registry's error message lists currently registered
            names.
    """
    lens_cls = get_lens(name)
    return lens_cls()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class AppraisalDispatcher:
    """Dispatch eligible records to the configured appraisal lens.

    Stateless per-instance — the dispatcher carries only its
    :class:`~ring2.core.project_config.AppraisalConfig` and lens
    factory. All per-run state (claim, records, results) flows through
    :meth:`dispatch`.

    Attributes:
        config: the :class:`~ring2.core.project_config.AppraisalConfig`
            that maps claim types to lens names.
        lens_factory: callable that instantiates a lens from its name.
            Defaults to :func:`_default_lens_factory` (registry
            lookup + no-arg instantiation).
    """

    def __init__(
        self,
        config: AppraisalConfig,
        lens_factory: LensFactory | None = None,
    ) -> None:
        self._config = config
        self._lens_factory: LensFactory = lens_factory or _default_lens_factory

    @property
    def config(self) -> AppraisalConfig:
        return self._config

    @property
    def lens_factory(self) -> LensFactory:
        return self._lens_factory

    def dispatch(
        self,
        claim: MPCOClaim,
        eligible_records: Iterable[PubMedRecord],
    ) -> dict[ClaimType, list[AppraisalResult]]:
        """Dispatch ``eligible_records`` against the lens for ``claim.claim_type``.

        Args:
            claim: the :class:`MPCOClaim` providing the claim type that
                drives lens selection and the per-record context for
                ``lens.appraise(record, claim)``.
            eligible_records: records that have passed the screening +
                eligibility gates and are ready for methodological
                appraisal.

        Returns:
            A mapping ``{claim.claim_type: [results...]}``. The list
            contains one entry per eligible record — either a real
            :class:`AppraisalResult` subclass (when the lens is
            operational) or a :class:`PendingAppraisalResult` (when
            the lens is not). For claim types that are not appraised
            (``REGULATORY_COMPLIANCE``, ``UNKNOWN``), returns ``{}``.
        """
        # Claim types that do not go through an appraisal lens.
        if claim.claim_type in (ClaimType.REGULATORY_COMPLIANCE, ClaimType.UNKNOWN):
            return {}

        lens_name = self._config.lens_for(claim.claim_type)
        lens = self._lens_factory(lens_name)
        records = list(eligible_records)

        if not lens.is_operational():
            pending_results: list[AppraisalResult] = [
                PendingAppraisalResult(
                    pmid=record.pmid,
                    lens_name=lens_name,
                    rationale=_PENDING_RATIONALE,
                    qualifies=False,
                )
                for record in records
            ]
            return {claim.claim_type: pending_results}

        real_results: list[AppraisalResult] = [lens.appraise(record, claim) for record in records]
        return {claim.claim_type: real_results}

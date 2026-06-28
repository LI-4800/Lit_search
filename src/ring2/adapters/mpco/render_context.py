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
"""Adapter-specific render-time context for the MPCO adapter (Stufe-1.8 Inkrement 3).

Bundles the three pieces of information the renderer needs beyond the
adapter-agnostic :class:`SessionState`:

    1. the :class:`MPCOClaim` (so the report can quote M/P/C/O fields
       and the claim_id banner)
    2. the screening decisions for this claim (so PASSED and EXCLUDED
       record listings can be rendered)
    3. the :class:`PrismaFlow` (so the §5 PRISMA flow section can be
       rendered with balanced counts)

Resolves ``U-1.8-B`` (``Weg B``): rather than persisting these to disk
in a location the renderer would have to read, the orchestrator
(Stufe 1.9+) constructs an :class:`MPCORenderContext` and passes it
to :meth:`MPCOAdapter.render_report` via the new ``context`` parameter.
The renderer remains pure — no I/O.

Structural conformance to the core ``RenderContext`` Protocol:
    The core marker (:class:`ring2.core.adapter_base.RenderContext`) is
    intentionally empty — no methods or properties required. Any object
    satisfies it via :func:`isinstance` when the Protocol is
    ``@runtime_checkable``. :class:`MPCORenderContext` therefore needs
    no explicit inheritance; a regression test in this module's test
    suite guards that conformance.

Cross-validator:
    ``C1`` enforces that ``claim.claim_id == flow.claim_id`` — prevents
    the orchestrator from wiring a claim with a flow that belongs to a
    different claim. Other cross-checks (decisions ↔ flow.counts
    aggregation, batch ↔ decisions PMID consistency) are deferred:
    decisions↔counts duplicates orchestrator aggregation logic and is
    premature; batch↔decisions belongs in the renderer (Inkrement 4)
    where ``SessionState.batch_files`` is in scope.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from ring2.adapters.mpco.decision_persistence import ScreeningDecision
from ring2.adapters.mpco.schema import MPCOClaim
from ring2.core.prisma import PrismaFlow

__all__ = ["MPCORenderContext"]


class MPCORenderContext(BaseModel):
    """Adapter-specific render-time context for the MPCO adapter.

    Constructed by the orchestrator and passed via
    :meth:`MPCOAdapter.render_report(state, context=...)`.

    Attributes:
        claim: the :class:`MPCOClaim` whose evidence is being reported on.
        decisions: tuple of :class:`ScreeningDecision` for this claim.
            Default empty — a context for a session where no screening
            decisions have been recorded yet is still valid (the
            renderer simply produces an empty PASSED/EXCLUDED listing).
        flow: the balanced :class:`PrismaFlow` for this claim. Must
            agree with ``claim.claim_id`` via validator ``C1``.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        # PrismaFlow is a stdlib @dataclass(frozen=True, slots=True) in
        # ring2.core.prisma — not a Pydantic model. arbitrary_types_allowed
        # delegates field-type validation to isinstance().
        arbitrary_types_allowed=True,
    )

    claim: MPCOClaim
    decisions: tuple[ScreeningDecision, ...] = ()
    flow: PrismaFlow

    @model_validator(mode="after")
    def _claim_flow_claim_id_consistent(self) -> MPCORenderContext:
        """C1: ``claim.claim_id`` must equal ``flow.claim_id``.

        Prevents wiring a claim with a flow that was built for a
        different claim — a common orchestrator bug class.
        """
        if self.claim.claim_id != self.flow.claim_id:
            raise ValueError(
                f"claim.claim_id={self.claim.claim_id!r} but "
                f"flow.claim_id={self.flow.claim_id!r}; "
                f"MPCORenderContext wires inconsistent claim/flow"
            )
        return self

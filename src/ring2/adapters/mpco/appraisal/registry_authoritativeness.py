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
"""Registry-source authoritativeness appraisal lens (STUB — Inkrement 6 skeleton).

Appraises historical-market-use evidence by evaluating the authoritativeness of the registry or database from which the evidence is drawn (EUDAMED, FDA databases, national medical-device registries). The default lens for ``HISTORICAL_MARKET_USE`` claims.

NOT IMPLEMENTED:
    This is a registry-only stub. Both :meth:`RegistryAuthoritativenessLens.appraise` and
    :meth:`RegistryAuthoritativenessLens.render_summary` raise :class:`NotImplementedError`.
    The class is registered so that project-YAML lens resolution can
    detect the name and so that the registry inventory is complete
    for the appraisal matrix; full methodology implementation is
    scheduled for Stufe 1.10+ (later than Inkrement 7's MEDDEV §A6
    full implementation).
"""

from __future__ import annotations

from typing import ClassVar

from ring2.adapters.mpco.appraisal.base import AppraisalLens, AppraisalResult
from ring2.adapters.mpco.appraisal.registry import register_lens
from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import MPCOClaim
from ring2.core.adapter_base import PubMedRecord

__all__ = ["RegistryAuthoritativenessLens"]


_NOT_IMPL_MSG = (
    "RegistryAuthoritativenessLens.{method} is not implemented — Inkrement-6 stub; "
    "full implementation scheduled for Stufe 1.10+."
)


@register_lens
class RegistryAuthoritativenessLens(AppraisalLens):
    """Registry-source authoritativeness appraisal lens (STUB)."""

    name: ClassVar[str] = "registry_authoritativeness"
    applicable_claim_types: ClassVar[frozenset[ClaimType]] = frozenset(
        {ClaimType.HISTORICAL_MARKET_USE}
    )

    def appraise(self, record: PubMedRecord, claim: MPCOClaim) -> AppraisalResult:
        raise NotImplementedError(_NOT_IMPL_MSG.format(method="appraise"))

    def render_summary(self, results: tuple[AppraisalResult, ...]) -> str:
        raise NotImplementedError(_NOT_IMPL_MSG.format(method="render_summary"))

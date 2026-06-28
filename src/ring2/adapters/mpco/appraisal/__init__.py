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
"""Appraisal subpackage — claim-type-aware evidence-quality lenses.

Public surface:
    * :class:`AppraisalResult`, :class:`AppraisalLens` — base classes.
    * :func:`register_lens`, :func:`get_lens`, :func:`names`,
      :func:`clear` — registry API.

Auto-registration:
    Importing this subpackage triggers side-effect imports of every
    lens module below, populating the registry. After import,
    :func:`names` returns the full inventory of registered lenses —
    after Inkrement 7 this is 8 stubs plus ``meddev_a6`` as the first
    fully-implemented lens (9 total).
"""

from __future__ import annotations

# Side-effect imports — populate the lens registry via @register_lens
# decorators at module load. Order is alphabetical for determinism;
# registry ordering itself is independent of import order (names() is
# always sorted).
from ring2.adapters.mpco.appraisal import arrive as _arrive  # noqa: F401
from ring2.adapters.mpco.appraisal import (
    astm_iso_material as _astm_iso_material,  # noqa: F401
)
from ring2.adapters.mpco.appraisal import care_caseseries as _care_caseseries  # noqa: F401
from ring2.adapters.mpco.appraisal import glp_oecd as _glp_oecd  # noqa: F401
from ring2.adapters.mpco.appraisal import grade as _grade  # noqa: F401
from ring2.adapters.mpco.appraisal import meddev_a6 as _meddev_a6  # noqa: F401
from ring2.adapters.mpco.appraisal import (
    registry_authoritativeness as _registry_authoritativeness,  # noqa: F401
)
from ring2.adapters.mpco.appraisal import rob2 as _rob2  # noqa: F401
from ring2.adapters.mpco.appraisal import robins_i as _robins_i  # noqa: F401
from ring2.adapters.mpco.appraisal.base import AppraisalLens, AppraisalResult
from ring2.adapters.mpco.appraisal.registry import (
    clear,
    get_lens,
    names,
    register_lens,
)

__all__ = [
    "AppraisalLens",
    "AppraisalResult",
    "clear",
    "get_lens",
    "names",
    "register_lens",
]

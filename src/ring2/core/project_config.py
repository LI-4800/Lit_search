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
"""Project-config schema — Pydantic v2 models for ``project.yaml``.

A project YAML drives an end-to-end RING2 run. It declares:

* the project's name,
* the claim to evaluate — either inline (``claim:``) or referenced by
  path (``claim_file:``); exactly one of the two must be supplied,
* the appraisal-lens selection per appraisable claim type (the four
  non-regulatory-compliance types from the MPCO matrix),
* the output directory for the generated report and audit artefacts.

The canonical YAML shape is::

    name: CB-bov-01
    claim_file: claims/cb-bov-01.yaml
    output_dir: reports/cb-bov-01
    appraisal:
      biochemistry_material_property:
        lens: glp_oecd
      safety_allergenicity:
        lens: care_caseseries
      clinical_performance:
        lens: meddev_a6
      historical_market_use:
        lens: registry_authoritativeness

The ``regulatory_compliance`` claim type is deliberately absent from the
appraisal matrix — it is handled by a separate reference-resolution path
(planned for Stufe 1.10+), not by an appraisal lens.

Lens names are validated against the MPCO appraisal-lens registry at
schema-validation time (per U-1.9a-A: validation lives in the schema,
not in the loader, so the failure surfaces close to its cause).

Path fields (``claim_file``, ``output_dir``) hold the raw paths as
declared in the YAML. Resolution to absolute paths — relative to the
project-YAML's parent directory — is the loader's responsibility, not
the schema's.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import MPCOClaim

if TYPE_CHECKING:
    from typing import Self

__all__ = [
    "AppraisalConfig",
    "AppraisalLensSelection",
    "ProjectConfig",
    "SearchConfig",
]


# ---------------------------------------------------------------------------
# Appraisal-config sub-models
# ---------------------------------------------------------------------------


_FROZEN_CONFIG = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class SearchConfig(BaseModel):
    """Search-phase configuration block.

    Optional under :class:`ProjectConfig`. When absent, the orchestrator
    skips the search phase and expects pre-existing batch files in the
    session directory (for resume / replay scenarios).

    Attributes:
        query: final PubMed search string. The orchestrator passes
            this verbatim to
            :meth:`~ring2.core.search.SearchOrchestrator.run`; query
            construction / refinement is upstream.
        batch_size: records per batch (default 10).
        max_batches: hard ceiling on the number of batches retrieved.
            ``None`` = no ceiling (default).
    """

    model_config = _FROZEN_CONFIG

    query: str = Field(min_length=1)
    batch_size: int = Field(default=10, gt=0)
    max_batches: int | None = Field(default=None)

    @field_validator("max_batches")
    @classmethod
    def _max_batches_positive_if_set(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("max_batches must be positive when set")
        return v


class AppraisalLensSelection(BaseModel):
    """Selection of a single appraisal lens for one claim type.

    Attributes:
        lens: registered lens name (must exist in the MPCO appraisal-lens
            registry). Validated at schema-validation time.
    """

    model_config = _FROZEN_CONFIG

    lens: str = Field(min_length=1)

    @field_validator("lens")
    @classmethod
    def _lens_must_be_registered(cls, v: str) -> str:
        # Local import to populate the lens registry via side-effect
        # imports in the appraisal subpackage's __init__. Avoids
        # ordering issues if this module is imported before the
        # appraisal subpackage has been touched.
        from ring2.adapters.mpco.appraisal import names as registered_lens_names

        registered = registered_lens_names()
        if v not in registered:
            raise ValueError(
                f"Lens {v!r} is not registered in the MPCO appraisal-lens "
                f"registry. Available lenses: {list(registered)}"
            )
        return v


class AppraisalConfig(BaseModel):
    """Per-claim-type appraisal-lens selection.

    Covers the four appraisable claim types from the MPCO matrix
    (per Handoff v7 §"Appraisal-Matrix (final)"):

    * ``biochemistry_material_property``,
    * ``safety_allergenicity``,
    * ``clinical_performance``,
    * ``historical_market_use``.

    The ``regulatory_compliance`` claim type is intentionally excluded —
    it is a reference-resolution path, not an appraisal lens (Stufe 1.10+).

    All four fields are required; there are no schema-level defaults so
    that each project explicitly declares its appraisal choices.
    """

    model_config = _FROZEN_CONFIG

    biochemistry_material_property: AppraisalLensSelection
    safety_allergenicity: AppraisalLensSelection
    clinical_performance: AppraisalLensSelection
    historical_market_use: AppraisalLensSelection

    def lens_for(self, claim_type: ClaimType) -> str:
        """Return the configured lens name for ``claim_type``.

        Args:
            claim_type: the claim type to look up.

        Returns:
            The registered lens name configured for this claim type.

        Raises:
            KeyError: if ``claim_type`` is ``REGULATORY_COMPLIANCE``
                (handled by reference resolution, not an appraisal lens),
                or ``UNKNOWN`` (no lens applicable until classification
                has run).
        """
        mapping = {
            ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY: self.biochemistry_material_property.lens,
            ClaimType.SAFETY_ALLERGENICITY: self.safety_allergenicity.lens,
            ClaimType.CLINICAL_PERFORMANCE: self.clinical_performance.lens,
            ClaimType.HISTORICAL_MARKET_USE: self.historical_market_use.lens,
        }
        if claim_type in mapping:
            return mapping[claim_type]
        if claim_type == ClaimType.REGULATORY_COMPLIANCE:
            raise KeyError(
                "REGULATORY_COMPLIANCE is handled by reference resolution, "
                "not appraisal lenses (Stufe 1.10+)."
            )
        if claim_type == ClaimType.UNKNOWN:
            raise KeyError("Claim type UNKNOWN has no appraisal lens; classify the claim first.")
        # Defensive: future claim-type members would land here.
        raise KeyError(f"No lens configured for claim type {claim_type!r}.")


# ---------------------------------------------------------------------------
# ProjectConfig — top-level project YAML
# ---------------------------------------------------------------------------


class ProjectConfig(BaseModel):
    """Top-level project-YAML schema driving an end-to-end RING2 run.

    Attributes:
        name: project name (non-empty, whitespace-stripped). Used in
            report filenames and audit-trail metadata.
        claim: inline :class:`MPCOClaim` instance. Mutually exclusive
            with ``claim_file``; exactly one of the two must be set.
        claim_file: path to a separate YAML file containing the
            :class:`MPCOClaim`. Interpreted relative to the project-YAML's
            parent directory by the loader (the schema itself does not
            resolve paths). Mutually exclusive with ``claim``.
        appraisal: per-claim-type lens selection.
        output_dir: target directory for generated report and audit
            artefacts. Interpreted relative to the project-YAML's parent
            directory by the loader. Need not exist at validation time;
            it is created by the orchestrator if missing.
        search: optional :class:`SearchConfig`. When set, the
            orchestrator runs a PubMed search at the start of the
            pipeline. When ``None``, the orchestrator skips the search
            phase and expects pre-existing batch files in the session
            directory (resume / replay scenarios).
    """

    model_config = _FROZEN_CONFIG

    name: str = Field(min_length=1)
    claim: MPCOClaim | None = None
    claim_file: Path | None = None
    appraisal: AppraisalConfig
    output_dir: Path
    search: SearchConfig | None = None

    @model_validator(mode="after")
    def _exactly_one_claim_source(self) -> Self:
        has_inline = self.claim is not None
        has_file = self.claim_file is not None
        if has_inline and has_file:
            raise ValueError(
                "ProjectConfig: ``claim`` and ``claim_file`` are mutually "
                "exclusive — set exactly one."
            )
        if not has_inline and not has_file:
            raise ValueError(
                "ProjectConfig: one of ``claim`` (inline) or "
                "``claim_file`` (path reference) must be supplied."
            )
        return self

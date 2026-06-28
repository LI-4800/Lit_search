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
"""Project-config loader — reads ``project.yaml``, resolves paths, builds claim.

This module is the I/O complement to :mod:`ring2.core.project_config`.
The schema module is pure; this module touches the filesystem.

Two public functions:

* :func:`load_project_config` — read and validate a project YAML.
* :func:`resolve_claim` — return the effective :class:`MPCOClaim`, loading
  from ``claim_file`` if needed.

Paths declared in the project YAML (``claim_file``, ``output_dir``) are
interpreted relative to the project-YAML's parent directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ring2.adapters.mpco.schema import MPCOClaim
from ring2.core.persistence import load as _persistence_load
from ring2.core.project_config import ProjectConfig

if TYPE_CHECKING:
    pass

__all__ = [
    "ProjectConfigLoaderError",
    "load_project_config",
    "resolve_claim",
]


class ProjectConfigLoaderError(Exception):
    """Raised on loader errors (missing file, malformed claim file, etc.)."""


def load_project_config(path: Path) -> ProjectConfig:
    """Load and validate a project YAML from disk.

    Args:
        path: path to the project YAML file.

    Returns:
        A validated :class:`ProjectConfig`.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ProjectConfigLoaderError: if the file is empty or its top-level
            structure is not a mapping.
        pydantic.ValidationError: if the YAML content fails schema
            validation (lens names, exactly-one-claim-source, etc.).
        ring2.core.persistence.PersistenceError: if the file extension
            is not recognised.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Project config not found: {path}")

    raw = _persistence_load(path)
    if raw is None:
        raise ProjectConfigLoaderError(
            f"Project config {path} is empty. Expected a YAML mapping with "
            f"keys: name, claim/claim_file, appraisal, output_dir."
        )
    if not isinstance(raw, dict):
        raise ProjectConfigLoaderError(
            f"Project config {path} must be a YAML mapping at the top level; "
            f"got {type(raw).__name__}."
        )

    return ProjectConfig.model_validate(raw)


def resolve_claim(config: ProjectConfig, base_dir: Path) -> MPCOClaim:
    """Return the effective :class:`MPCOClaim` for ``config``.

    If ``config.claim`` is set (inline), it is returned directly.
    Otherwise ``config.claim_file`` is resolved relative to ``base_dir``,
    the referenced YAML is loaded, and an :class:`MPCOClaim` is built
    from it.

    Args:
        config: the project config (already schema-validated, so exactly
            one of ``claim``/``claim_file`` is guaranteed to be set).
        base_dir: directory against which ``claim_file`` is resolved
            (typically the parent directory of the project YAML).

    Returns:
        The effective :class:`MPCOClaim`.

    Raises:
        FileNotFoundError: if ``claim_file`` is set but the referenced
            file does not exist.
        ProjectConfigLoaderError: if the claim file is empty or its
            top-level structure is not a mapping.
        pydantic.ValidationError: if the claim YAML fails
            :class:`MPCOClaim` validation.
    """
    if config.claim is not None:
        return config.claim

    # Schema's model-validator guarantees claim_file is set when claim is None.
    assert config.claim_file is not None, (
        "ProjectConfig invariant violated: neither claim nor claim_file set"
    )

    claim_path = config.claim_file
    if not claim_path.is_absolute():
        claim_path = base_dir / claim_path

    if not claim_path.exists():
        raise FileNotFoundError(
            f"claim_file references missing file: {claim_path} "
            f"(declared as {config.claim_file} relative to {base_dir})"
        )

    raw = _persistence_load(claim_path)
    if raw is None:
        raise ProjectConfigLoaderError(
            f"Claim file {claim_path} is empty. Expected a YAML mapping "
            f"matching the MPCOClaim schema."
        )
    if not isinstance(raw, dict):
        raise ProjectConfigLoaderError(
            f"Claim file {claim_path} must be a YAML mapping at the top "
            f"level; got {type(raw).__name__}."
        )

    return MPCOClaim.model_validate(raw)

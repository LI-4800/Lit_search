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
"""Decision persistence for the MPCO adapter (Stufe-1.8 Inkrement 2).

Implements ``U-1.8-C`` resolution (``Weg B``): screening decisions live in
sidecar YAML files under ``<session_dir>/decisions/`` and are versioned
per claim. Batch files (the on-disk record of what the search found)
remain immutable — separation of "what we found" from "what we
decided".

On-disk layout::

    <session_dir>/
      search_<claim_id>_batch_01.yaml      (existing, immutable)
      search_<claim_id>_batch_02.yaml
      decisions/
        <claim_id>_v1.yaml                 (first screening pass)
        <claim_id>_v2.yaml                 (re-screening after full-text)
        ...

Auto-versioning: :func:`write_decision_file` finds the highest existing
``_v<n>.yaml`` for ``claim_id`` and writes ``_v<n+1>.yaml``. Previous
versions are never overwritten — they remain as part of the audit trail
required by MDR/MEDDEV review.

The YAML wraps a single top-level ``decisions:`` mapping (mirroring
:mod:`ring2.core.prisma`'s ``prisma_2020:`` top-level key), and serialises
datetimes as ISO-8601 strings via Pydantic's JSON mode for deterministic
round-tripping through :mod:`ring2.core.persistence`.

Cross-field invariants enforced by :class:`ScreeningDecision`:

    V1: outcome=exclude  ⇒  exclusion_code is not None
    V2: outcome=include  ⇒  exclusion_code is None
    V3: phase matches the canonical phase for exclusion_code
        (per :func:`ring2.adapters.mpco.exclusion_codes.phase_for`)
    V4: decided_at must be tz-aware and equivalent to UTC

Invariant enforced by :class:`DecisionFile`:

    V5: all (pmid, phase) pairs in ``decisions`` are unique. The same
        pmid may legitimately appear at two different phases (e.g.
        passed SCREENING, then excluded at ELIGIBILITY), but not twice
        at the same phase within a single file version.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ring2.adapters.mpco.exclusion_codes import ExclusionCode, PrismaPhase, phase_for
from ring2.core.persistence import load, save

__all__ = [
    "DecisionFile",
    "ScreeningDecision",
    "load_latest_decision_file",
    "write_decision_file",
]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Subdirectory under ``session_dir`` where versioned decision files live.
_DECISIONS_SUBDIR: str = "decisions"

#: Pattern for ``<claim_id>_v<n>.yaml`` files. ``claim_id`` matches lazily
#: against any character — the version-suffix anchor at the end resolves
#: ambiguity for claim_ids that themselves contain ``_v\d+``.
_FILE_PATTERN: re.Pattern[str] = re.compile(r"^(?P<claim_id>.+)_v(?P<version>\d+)\.yaml$")

#: Current on-disk schema version. Bump on breaking changes.
_SCHEMA_VERSION: Literal["1.0"] = "1.0"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ScreeningDecision(BaseModel):
    """One per-record screening decision.

    Attributes:
        pmid: PMID of the record being decided on. Non-empty.
        phase: PRISMA phase at which the decision was raised. Must agree
            with the canonical phase for ``exclusion_code`` when set
            (validator V3).
        outcome: ``"include"`` or ``"exclude"``.
        exclusion_code: stable :class:`ExclusionCode` member; ``None``
            iff ``outcome == "include"`` (validators V1, V2).
        rationale: verbatim human/screener justification. Non-empty.
        decided_at: timestamp at which the decision was recorded. Must be
            tz-aware and represent UTC (utcoffset == 0); validator V4.
        decided_by: identifier of the decider. Free-text in v1.
            Convention: ``"screener:<model-id>"`` for AI calls,
            ``"reviewer:<email>"`` for human decisions.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    pmid: str = Field(min_length=1)
    phase: PrismaPhase
    outcome: Literal["include", "exclude"]
    exclusion_code: ExclusionCode | None = None
    rationale: str = Field(min_length=1)
    decided_at: datetime
    decided_by: str = Field(min_length=1)

    @field_validator("decided_at")
    @classmethod
    def _decided_at_must_be_utc(cls, v: datetime) -> datetime:
        """V4: decided_at must be tz-aware and equivalent to UTC."""
        if v.tzinfo is None:
            raise ValueError("decided_at must be tz-aware (UTC); got naive datetime")
        offset = v.utcoffset()
        if offset is None or offset != timedelta(0):
            raise ValueError(f"decided_at must be UTC (offset 0); got offset {offset}")
        return v

    @model_validator(mode="after")
    def _outcome_code_phase_coherence(self) -> ScreeningDecision:
        """V1+V2+V3: outcome ↔ exclusion_code, code ↔ phase routing."""
        if self.outcome == "exclude" and self.exclusion_code is None:
            raise ValueError("outcome=exclude requires exclusion_code; got None")
        if self.outcome == "include" and self.exclusion_code is not None:
            raise ValueError(
                f"outcome=include forbids exclusion_code; got {self.exclusion_code.value!r}"
            )
        if self.exclusion_code is not None:
            expected_phase = phase_for(self.exclusion_code)
            if self.phase is not expected_phase:
                raise ValueError(
                    f"exclusion_code {self.exclusion_code.value!r} is routed to "
                    f"phase {expected_phase.value!r}, but decision phase is "
                    f"{self.phase.value!r}"
                )
        return self


class DecisionFile(BaseModel):
    """A versioned collection of screening decisions for one claim.

    Attributes:
        schema_version: on-disk schema version. Locked to ``"1.0"`` in
            this stage; future migrations bump this.
        claim_id: the claim these decisions belong to. Non-empty.
        decisions: tuple of :class:`ScreeningDecision`. Empty is valid
            (a "no decisions yet" file). All ``(pmid, phase)`` pairs
            within the tuple must be unique (validator V5).
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    schema_version: Literal["1.0"]
    claim_id: str = Field(min_length=1)
    decisions: tuple[ScreeningDecision, ...] = ()

    @model_validator(mode="after")
    def _unique_pmid_phase(self) -> DecisionFile:
        """V5: each (pmid, phase) pair appears at most once."""
        seen: set[tuple[str, PrismaPhase]] = set()
        for d in self.decisions:
            key = (d.pmid, d.phase)
            if key in seen:
                raise ValueError(
                    f"duplicate (pmid, phase) in decisions: ({d.pmid!r}, {d.phase.value!r})"
                )
            seen.add(key)
        return self


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _decisions_dir(session_dir: Path) -> Path:
    """Return the canonical ``<session_dir>/decisions/`` path."""
    return session_dir / _DECISIONS_SUBDIR


def _next_version(session_dir: Path, claim_id: str) -> int:
    """Find the next available version number for ``claim_id``.

    Returns 1 if no versions exist yet (incl. when ``decisions/`` does
    not yet exist); otherwise ``max(existing) + 1``.
    """
    decisions_dir = _decisions_dir(session_dir)
    if not decisions_dir.exists():
        return 1
    versions: list[int] = []
    for f in decisions_dir.iterdir():
        if not f.is_file():
            continue
        m = _FILE_PATTERN.match(f.name)
        if m is not None and m.group("claim_id") == claim_id:
            versions.append(int(m.group("version")))
    return max(versions, default=0) + 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_decision_file(
    session_dir: Path,
    claim_id: str,
    decisions: tuple[ScreeningDecision, ...],
) -> Path:
    """Write a new versioned DecisionFile.

    The decisions are wrapped in a fresh :class:`DecisionFile`
    (schema_version locked to ``"1.0"``) and validated; only on
    successful validation is the file written. The destination is
    ``<session_dir>/decisions/<claim_id>_v<n>.yaml`` where ``n`` is one
    more than the highest existing version (or 1 if none exist).

    Args:
        session_dir: project session directory; will be created if absent.
        claim_id: the claim these decisions belong to.
        decisions: tuple of validated :class:`ScreeningDecision`.

    Returns:
        The path of the file written.

    Raises:
        pydantic.ValidationError: if ``DecisionFile`` construction fails
            (e.g. duplicate ``(pmid, phase)`` pair, empty ``claim_id``).
    """
    decision_file = DecisionFile(
        schema_version=_SCHEMA_VERSION,
        claim_id=claim_id,
        decisions=decisions,
    )
    version = _next_version(session_dir, claim_id)
    target = _decisions_dir(session_dir) / f"{claim_id}_v{version}.yaml"
    payload = {"decisions": decision_file.model_dump(mode="json")}
    save(target, payload)
    return target


def load_latest_decision_file(
    session_dir: Path,
    claim_id: str,
) -> DecisionFile | None:
    """Load the highest-version :class:`DecisionFile` for ``claim_id``.

    Args:
        session_dir: project session directory.
        claim_id: the claim whose decisions are sought.

    Returns:
        The latest :class:`DecisionFile`, or ``None`` if no decision
        files for this claim exist (including when ``decisions/`` itself
        does not exist).

    Raises:
        pydantic.ValidationError: if the on-disk file fails to round-trip
            through :class:`DecisionFile` validation (corruption, manual
            edits violating invariants, schema-version mismatch).
        KeyError: if the on-disk file is missing the top-level
            ``decisions:`` wrapper.
    """
    decisions_dir = _decisions_dir(session_dir)
    if not decisions_dir.exists():
        return None
    candidates: list[tuple[int, Path]] = []
    for f in decisions_dir.iterdir():
        if not f.is_file():
            continue
        m = _FILE_PATTERN.match(f.name)
        if m is not None and m.group("claim_id") == claim_id:
            candidates.append((int(m.group("version")), f))
    if not candidates:
        return None
    _, latest_path = max(candidates, key=lambda x: x[0])
    raw = load(latest_path)
    return DecisionFile.model_validate(raw["decisions"])

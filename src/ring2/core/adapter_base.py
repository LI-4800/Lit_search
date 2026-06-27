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
"""Adapter base class.

The shared core of RING2 is regulation- and domain-agnostic. Each domain
(PICO for clinical evaluation, MPCO for material evidence, ...) provides a
concrete :class:`Adapter` subclass that plugs in three things:

    1. A *schema* describing the question/claim structure.
    2. *Inclusion* and *exclusion* criteria for screening.
    3. An *appraisal* function that decides include / exclude / review
       for each retrieved record.
    4. A *report renderer* that turns session state into a final artefact.

Adapters are registered by name (``register``) and retrieved via :func:`get`.

This module deliberately depends on the stdlib only — adapters may pull in
heavier libraries (e.g. ``pydantic``) for their concrete schemas, but the
core abstraction stays import-cheap.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Records (PubMed-shaped; produced by core/pubmed_client.py, consumed here)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PubMedRecord:
    """A single bibliographic record. Regulation-agnostic shape.

    All fields are optional except ``pmid`` and ``title`` — the search may
    return records with missing metadata that still need to be displayed.
    The ``raw`` dict carries the full upstream payload for adapter-specific
    field extraction without re-defining the dataclass.
    """

    pmid: str
    title: str
    doi: str | None = None
    abstract: str | None = None
    journal: str | None = None
    year: int | None = None
    authors: tuple[str, ...] = ()
    publication_types: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Criteria and decisions
# ---------------------------------------------------------------------------


class AppraisalOutcome(StrEnum):
    """Three-valued outcome for an appraisal decision."""

    INCLUDE = "include"
    EXCLUDE = "exclude"
    REVIEW = "requires_review"


@dataclass(frozen=True, slots=True)
class InclusionCriterion:
    """A single positive inclusion rule."""

    id: str  # short stable id, e.g. "INC-001"
    description: str


@dataclass(frozen=True, slots=True)
class ExclusionCriterion:
    """A single negative exclusion rule, identified by a stable code.

    Codes are partitioned between *core* (reused across adapters — e.g.
    ``EX-DESIGN``, ``EX-INVITRO``, ``EX-ANIMAL``) and *adapter-specific*
    (e.g. ``EX-LANGUAGE``, ``EX-A6-CATALOG`` for MPCO).
    """

    code: str
    description: str


@dataclass(frozen=True, slots=True)
class InclusionCriteria:
    """Ordered collection of inclusion rules for one question."""

    criteria: tuple[InclusionCriterion, ...]


@dataclass(frozen=True, slots=True)
class ExclusionCriteria:
    """Ordered collection of exclusion rules for one question."""

    criteria: tuple[ExclusionCriterion, ...]


@dataclass(frozen=True, slots=True)
class AppraisalDecision:
    """The result of appraising one record against one question.

    Fields:
        pmid: identifier of the appraised record.
        outcome: include / exclude / requires_review.
        exclusion_code: stable code from :class:`ExclusionCriterion`,
            required when ``outcome == EXCLUDE``, ``None`` otherwise.
        rationale: short human-readable justification.
        confidence: optional 0..1 score from automated appraisal.
        requires_review: convenience flag — set ``True`` when an automated
            decision should be confirmed by a human reviewer (e.g. low
            confidence or §A6 borderline case, per UNKLAR-A3 resolution).
    """

    pmid: str
    outcome: AppraisalOutcome
    exclusion_code: str | None
    rationale: str
    confidence: float | None = None
    requires_review: bool = False

    def __post_init__(self) -> None:
        if self.outcome is AppraisalOutcome.EXCLUDE and not self.exclusion_code:
            raise ValueError(
                f"AppraisalDecision for pmid={self.pmid!r}: "
                "exclusion_code is required when outcome=EXCLUDE"
            )
        if self.outcome is not AppraisalOutcome.EXCLUDE and self.exclusion_code:
            raise ValueError(
                f"AppraisalDecision for pmid={self.pmid!r}: "
                "exclusion_code must be None unless outcome=EXCLUDE"
            )
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"AppraisalDecision for pmid={self.pmid!r}: "
                f"confidence must be in [0, 1], got {self.confidence}"
            )


# ---------------------------------------------------------------------------
# Protocols (adapter-specific concrete types implement these)
# ---------------------------------------------------------------------------


@runtime_checkable
class Question(Protocol):
    """A per-claim question or specification.

    PICO adapter: a PICO question (P/I/C/O).
    MPCO adapter: an MPCO claim (M/P/C/O).
    """

    @property
    def claim_id(self) -> str: ...


@runtime_checkable
class Schema(Protocol):
    """Adapter schema metadata."""

    @property
    def name(self) -> str:  # "PICO" | "MPCO" | ...
        ...

    @property
    def fields(self) -> tuple[str, ...]: ...


@runtime_checkable
class SessionState(Protocol):
    """Per-claim session state. Real implementation lives in core/session.py."""

    @property
    def project_id(self) -> str: ...

    @property
    def claim_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ReportArtefact:
    """Result of :meth:`Adapter.render_report`.

    Either ``path`` (file written to disk) or ``content`` (inline) — at least
    one must be set.
    """

    format: str  # "markdown" | "yaml" | "pdf" | ...
    path: Path | None = None
    content: str | None = None

    def __post_init__(self) -> None:
        if self.path is None and self.content is None:
            raise ValueError("ReportArtefact must have either path or content set")


# ---------------------------------------------------------------------------
# The Adapter ABC
# ---------------------------------------------------------------------------


class Adapter(ABC):
    """Abstract base for all domain adapters.

    Subclasses must set the class attribute :attr:`name` (e.g. ``"PICO"``,
    ``"MPCO"``) before registering via :func:`register`.
    """

    #: Adapter name — must be set by subclass; used as registry key.
    name: str = ""

    @property
    @abstractmethod
    def schema(self) -> Schema:
        """The adapter's question/claim schema."""

    @abstractmethod
    def inclusion_criteria(self, question: Question) -> InclusionCriteria:
        """Return the inclusion criteria for the given question/claim."""

    @abstractmethod
    def exclusion_criteria(self, question: Question) -> ExclusionCriteria:
        """Return the exclusion criteria for the given question/claim.

        Adapters may extend the core base set (``EX-DESIGN``, ``EX-INVITRO``,
        ``EX-ANIMAL``) with adapter-specific codes.
        """

    @abstractmethod
    def appraise(self, record: PubMedRecord, question: Question) -> AppraisalDecision:
        """Decide include / exclude / review for one record."""

    @abstractmethod
    def render_report(self, state: SessionState) -> ReportArtefact:
        """Render the final report for one claim's session state."""


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


_REGISTRY: dict[str, type[Adapter]] = {}


def register(adapter_cls: type[Adapter]) -> type[Adapter]:
    """Register an :class:`Adapter` subclass under its :attr:`name`.

    Usable as a decorator::

        @register
        class MPCOAdapter(Adapter):
            name = "MPCO"
            ...

    Raises:
        ValueError: if ``name`` is empty or already registered.
    """
    name = adapter_cls.name
    if not name:
        raise ValueError(
            f"Adapter {adapter_cls.__name__} has an empty .name; set it before registering"
        )
    if name in _REGISTRY:
        existing = _REGISTRY[name]
        if existing is adapter_cls:
            return adapter_cls  # idempotent
        raise ValueError(
            f"Adapter name {name!r} already registered to "
            f"{existing.__module__}.{existing.__name__}; "
            f"cannot re-register {adapter_cls.__module__}.{adapter_cls.__name__}"
        )
    _REGISTRY[name] = adapter_cls
    return adapter_cls


def get(name: str) -> type[Adapter]:
    """Return the adapter class registered under ``name``.

    Raises:
        KeyError: if no adapter is registered under that name.
    """
    try:
        return _REGISTRY[name]
    except KeyError as e:
        available = sorted(_REGISTRY)
        raise KeyError(
            f"No adapter registered as {name!r}. Available: {available or '(none)'}"
        ) from e


def names() -> tuple[str, ...]:
    """Return all currently registered adapter names, sorted."""
    return tuple(sorted(_REGISTRY))


def clear() -> None:
    """Empty the registry — intended for test isolation only."""
    _REGISTRY.clear()

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
"""Title/abstract screening — Stage 4 in the prompt-v3 pipeline.

Screening is the **cheap pre-filter** that removes obvious mismatches based
on title + abstract alone. It is distinct from full-text appraisal
(Stage 5, ``Adapter.appraise``), which applies the section A6 catalog
against the full paper. Per the 2026-06-27 design decision, the section
A6 catalog is never applied here.

The screening LLM call lives behind the :class:`ScreenerCaller` Protocol,
mirroring the :class:`~ring2.core.pubmed_client.MCPCaller` pattern.
The core module ships:

    * the Protocol itself (no SDK dependencies),
    * a :class:`NullScreenerCaller` sentinel that fails loudly on use,
    * the orchestration logic in :func:`screen_record`.

A production caller bridging to an actual LLM SDK lives outside core
(per agreed Option A on 2026-06-27).

Two-pass logic
--------------
Pass 1 calls the screener with **title only**. If the LLM is highly
confident the record should be excluded (``confidence >=
TITLE_ONLY_EXCLUDE_THRESHOLD``), Pass 2 is skipped - this saves an LLM
round-trip on obvious off-topic hits. Otherwise Pass 2 is called with
**title + abstract**, and its decision is final.

Records without an abstract degrade gracefully: Pass 2 is skipped and
Pass 1's decision stands (with ``requires_review=True`` if Pass 1's
confidence is below :data:`REVIEW_THRESHOLD`).

Validation
----------
:func:`screen_record` validates the caller's response against the
passed criteria *before* building the :class:`AppraisalDecision`:

    * ``outcome`` must be one of ``include``, ``exclude``,
      ``requires_review``.
    * If ``outcome == "exclude"``, ``exclusion_code`` must be one of
      the codes listed in the passed :class:`ExclusionCriteria`.
    * ``confidence`` must be in ``[0.0, 1.0]``.

A confidence below :data:`REVIEW_THRESHOLD` sets the decision's
``requires_review`` flag regardless of the LLM's own assessment - the
human reviewer gets the final say in low-confidence cases.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .adapter_base import (
    AppraisalDecision,
    AppraisalOutcome,
    ExclusionCriteria,
    InclusionCriteria,
    PubMedRecord,
)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


TITLE_ONLY_EXCLUDE_THRESHOLD: float = 0.9
"""Pass 1 short-circuit: title-only EXCLUDE with confidence >= this skips Pass 2."""

REVIEW_THRESHOLD: float = 0.7
"""Decisions below this confidence trigger ``requires_review=True``."""


# ---------------------------------------------------------------------------
# Caller protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ScreenerCaller(Protocol):
    """Abstraction over the screening LLM.

    Production implementations bridge to a real LLM SDK (kept outside
    ``core/`` so the core stays SDK-agnostic). Tests inject fakes that
    return pre-baked dicts.

    The method signature uses plain Python types (dicts, lists, strs)
    so callers can serialise the prompt context directly into a JSON
    request without an intermediate conversion step.
    """

    def assess(
        self,
        *,
        record_view: dict[str, Any],
        inclusion: list[dict[str, str]],
        exclusion: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Assess one record against the given criteria.

        Args:
            record_view: a dict describing the record under screening.
                Always contains ``pmid`` and ``title``; may contain
                ``abstract``, ``journal``, ``year`` depending on which
                pass is calling.
            inclusion: ``[{"id": ..., "description": ...}, ...]``.
            exclusion: ``[{"code": ..., "description": ...}, ...]``.

        Returns:
            A dict with keys ``outcome`` (one of ``"include"``,
            ``"exclude"``, ``"requires_review"``), optionally
            ``exclusion_code`` (required when ``outcome == "exclude"``),
            ``rationale`` (short string), ``confidence`` (float in
            ``[0.0, 1.0]``).
        """
        ...


# ---------------------------------------------------------------------------
# NullScreenerCaller — sentinel that fails loudly
# ---------------------------------------------------------------------------


class NullScreenerCaller:
    """A :class:`ScreenerCaller` that raises on every call.

    Useful as a default in code paths that should not reach the
    screening LLM (e.g. when the calling adapter expects all records to
    have been pre-screened). Fails loudly rather than silently
    returning a placeholder decision.
    """

    def __init__(self, message: str = "Screening LLM not available in this context") -> None:
        self._message = message
        self._calls: list[str] = []

    @property
    def calls(self) -> tuple[str, ...]:
        return tuple(self._calls)

    def assess(
        self,
        *,
        record_view: dict[str, Any],
        inclusion: list[dict[str, str]],
        exclusion: list[dict[str, str]],
    ) -> dict[str, Any]:
        pmid = record_view.get("pmid", "?")
        self._calls.append(f"assess(pmid={pmid!r})")
        raise RuntimeError(f"{self._message} (attempted assess for pmid={pmid!r})")


# ---------------------------------------------------------------------------
# Screening orchestration
# ---------------------------------------------------------------------------


def _record_view(record: PubMedRecord, *, include_abstract: bool) -> dict[str, Any]:
    """Build the dict view of a record passed to the screener caller."""
    view: dict[str, Any] = {"pmid": record.pmid, "title": record.title}
    if record.journal:
        view["journal"] = record.journal
    if record.year is not None:
        view["year"] = record.year
    if include_abstract and record.abstract:
        view["abstract"] = record.abstract
    return view


def _build_decision(
    pmid: str,
    response: dict[str, Any],
    valid_exclusion_codes: frozenset[str],
    review_threshold: float,
) -> AppraisalDecision:
    """Validate a screener response and convert it to an :class:`AppraisalDecision`.

    Validation steps:

        * ``outcome`` must be one of the canonical strings.
        * ``confidence``, if present, must be in ``[0.0, 1.0]``.
        * If ``outcome == "exclude"``, ``exclusion_code`` must be set
          and must be a member of ``valid_exclusion_codes``.

    A confidence below ``review_threshold`` flips ``requires_review``
    to ``True`` even when the caller reported a definitive outcome.
    """
    raw_outcome = response.get("outcome")
    if not isinstance(raw_outcome, str):
        raise ValueError(
            f"Screener response for pmid={pmid!r}: 'outcome' missing or non-string "
            f"(got {raw_outcome!r})"
        )
    try:
        outcome = AppraisalOutcome(raw_outcome)
    except ValueError as e:
        valid = sorted(o.value for o in AppraisalOutcome)
        raise ValueError(
            f"Screener response for pmid={pmid!r}: unknown outcome {raw_outcome!r}. "
            f"Valid outcomes: {valid}"
        ) from e

    confidence_raw = response.get("confidence")
    confidence: float | None
    if confidence_raw is None:
        confidence = None
    elif isinstance(confidence_raw, int | float) and not isinstance(confidence_raw, bool):
        confidence = float(confidence_raw)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f"Screener response for pmid={pmid!r}: confidence must be in [0, 1], "
                f"got {confidence}"
            )
    else:
        raise ValueError(
            f"Screener response for pmid={pmid!r}: confidence must be a number or None, "
            f"got {type(confidence_raw).__name__}"
        )

    exclusion_code_raw = response.get("exclusion_code")
    exclusion_code: str | None
    if exclusion_code_raw is None:
        exclusion_code = None
    elif isinstance(exclusion_code_raw, str):
        exclusion_code = exclusion_code_raw
    else:
        raise ValueError(
            f"Screener response for pmid={pmid!r}: exclusion_code must be a string or None, "
            f"got {type(exclusion_code_raw).__name__}"
        )

    if outcome is AppraisalOutcome.EXCLUDE:
        if not exclusion_code:
            raise ValueError(
                f"Screener response for pmid={pmid!r}: outcome=exclude requires exclusion_code"
            )
        if exclusion_code not in valid_exclusion_codes:
            valid = sorted(valid_exclusion_codes)
            raise ValueError(
                f"Screener response for pmid={pmid!r}: unknown exclusion_code "
                f"{exclusion_code!r}. Valid codes: {valid or '(none)'}"
            )
    elif exclusion_code is not None:
        # AppraisalDecision would reject this too, but we surface a
        # clearer error message here.
        raise ValueError(
            f"Screener response for pmid={pmid!r}: exclusion_code must be None unless "
            f"outcome=exclude (got outcome={outcome.value!r}, exclusion_code={exclusion_code!r})"
        )

    rationale_raw = response.get("rationale", "")
    rationale = str(rationale_raw) if rationale_raw is not None else ""

    # Low-confidence floor: even a definitive outcome from the LLM is
    # flagged for human review when below threshold.
    requires_review = outcome is AppraisalOutcome.REVIEW or (
        confidence is not None and confidence < review_threshold
    )

    return AppraisalDecision(
        pmid=pmid,
        outcome=outcome,
        exclusion_code=exclusion_code,
        rationale=rationale,
        confidence=confidence,
        requires_review=requires_review,
    )


def screen_record(
    record: PubMedRecord,
    inclusion: InclusionCriteria,
    exclusion: ExclusionCriteria,
    *,
    caller: ScreenerCaller,
    title_only_exclude_threshold: float = TITLE_ONLY_EXCLUDE_THRESHOLD,
    review_threshold: float = REVIEW_THRESHOLD,
) -> AppraisalDecision:
    """Screen one ``record`` against ``inclusion``/``exclusion`` criteria.

    Workflow:

        1. **Pass 1 (title only)**: build a ``record_view`` containing
           only title (plus journal/year if present) and call
           ``caller.assess``. If Pass 1 returns ``EXCLUDE`` with
           confidence at or above ``title_only_exclude_threshold``,
           return that decision and skip Pass 2.
        2. **Pass 2 (title + abstract)**: only runs if (a) the record
           has an abstract, and (b) Pass 1 did not short-circuit. The
           Pass 2 decision is final.
        3. If the record has no abstract, Pass 2 is skipped and the
           Pass 1 decision stands.

    Args:
        record: the :class:`PubMedRecord` to screen.
        inclusion: the inclusion criteria for the active question.
        exclusion: the exclusion criteria for the active question.
            Codes in this set are the only legal values for
            ``AppraisalDecision.exclusion_code``.
        caller: the screening LLM bridge.
        title_only_exclude_threshold: Pass 1 EXCLUDE confidence required
            to skip Pass 2. Defaults to
            :data:`TITLE_ONLY_EXCLUDE_THRESHOLD`.
        review_threshold: confidence below which ``requires_review`` is
            forced true. Defaults to :data:`REVIEW_THRESHOLD`.

    Returns:
        A :class:`AppraisalDecision` with the screening outcome.

    Raises:
        ValueError: if the caller returns a malformed or invalid
            response (unknown outcome string, unknown exclusion code,
            confidence out of range, etc.).
        RuntimeError: propagated from a :class:`NullScreenerCaller`.
    """
    if not 0.0 <= title_only_exclude_threshold <= 1.0:
        raise ValueError(
            f"title_only_exclude_threshold must be in [0, 1], got {title_only_exclude_threshold}"
        )
    if not 0.0 <= review_threshold <= 1.0:
        raise ValueError(f"review_threshold must be in [0, 1], got {review_threshold}")

    valid_codes = frozenset(c.code for c in exclusion.criteria)
    inclusion_payload = [{"id": c.id, "description": c.description} for c in inclusion.criteria]
    exclusion_payload = [{"code": c.code, "description": c.description} for c in exclusion.criteria]

    # -- Pass 1: title only -------------------------------------------------
    pass1_view = _record_view(record, include_abstract=False)
    pass1_raw = caller.assess(
        record_view=pass1_view,
        inclusion=inclusion_payload,
        exclusion=exclusion_payload,
    )
    pass1_decision = _build_decision(record.pmid, pass1_raw, valid_codes, review_threshold)

    short_circuit = (
        pass1_decision.outcome is AppraisalOutcome.EXCLUDE
        and pass1_decision.confidence is not None
        and pass1_decision.confidence >= title_only_exclude_threshold
    )
    if short_circuit:
        return pass1_decision

    if not record.abstract:
        # No abstract available — Pass 1 is the best we can do.
        return pass1_decision

    # -- Pass 2: title + abstract ------------------------------------------
    pass2_view = _record_view(record, include_abstract=True)
    pass2_raw = caller.assess(
        record_view=pass2_view,
        inclusion=inclusion_payload,
        exclusion=exclusion_payload,
    )
    return _build_decision(record.pmid, pass2_raw, valid_codes, review_threshold)


__all__ = [
    "REVIEW_THRESHOLD",
    "TITLE_ONLY_EXCLUDE_THRESHOLD",
    "NullScreenerCaller",
    "ScreenerCaller",
    "screen_record",
]

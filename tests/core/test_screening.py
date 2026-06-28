# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.core.screening."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ring2.core.adapter_base import (
    AppraisalOutcome,
    ExclusionCriteria,
    ExclusionCriterion,
    InclusionCriteria,
    InclusionCriterion,
    PubMedRecord,
)
from ring2.core.screening import (
    REVIEW_THRESHOLD,
    TITLE_ONLY_EXCLUDE_THRESHOLD,
    NullScreenerCaller,
    ScreenerCaller,
    screen_record,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeScreener:
    """Programmable screener: returns scripted responses, records calls."""

    responses: list[dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def assess(
        self,
        *,
        record_view: dict[str, Any],
        inclusion: list[dict[str, str]],
        exclusion: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "record_view": dict(record_view),
                "inclusion": list(inclusion),
                "exclusion": list(exclusion),
            }
        )
        response = self.responses[self._index]
        self._index += 1
        return response


def _rec(
    *,
    pmid: str = "1",
    title: str = "Title",
    abstract: str | None = "Abstract text.",
    journal: str | None = None,
    year: int | None = None,
) -> PubMedRecord:
    return PubMedRecord(
        pmid=pmid,
        title=title,
        abstract=abstract,
        journal=journal,
        year=year,
    )


def _inc(*ids_and_descs: tuple[str, str]) -> InclusionCriteria:
    return InclusionCriteria(
        criteria=tuple(InclusionCriterion(id=i, description=d) for i, d in ids_and_descs)
    )


def _exc(*codes_and_descs: tuple[str, str]) -> ExclusionCriteria:
    return ExclusionCriteria(
        criteria=tuple(ExclusionCriterion(code=c, description=d) for c, d in codes_and_descs)
    )


_DEFAULT_INC = _inc(("INC-001", "Relevant to bovine collagen"))
_DEFAULT_EXC = _exc(
    ("EX-LANGUAGE", "Not in English/German"),
    ("EX-IRRELEVANT", "Off-topic"),
)


# ---------------------------------------------------------------------------
# Protocol satisfaction
# ---------------------------------------------------------------------------


def test_null_screener_caller_satisfies_protocol() -> None:
    assert isinstance(NullScreenerCaller(), ScreenerCaller)


def test_fake_screener_satisfies_protocol() -> None:
    assert isinstance(_FakeScreener(responses=[]), ScreenerCaller)


# ---------------------------------------------------------------------------
# NullScreenerCaller behaviour
# ---------------------------------------------------------------------------


def test_null_caller_raises_with_pmid_in_message() -> None:
    caller = NullScreenerCaller()
    with pytest.raises(RuntimeError, match="pmid='42'"):
        screen_record(_rec(pmid="42"), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)


def test_null_caller_records_attempted_calls() -> None:
    caller = NullScreenerCaller()
    with pytest.raises(RuntimeError):
        screen_record(_rec(pmid="42"), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert caller.calls == ("assess(pmid='42')",)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_include_high_confidence_no_review_flag() -> None:
    """Pass 1 INCLUDE leads to Pass 2; Pass 2 INCLUDE @ 0.95 -> no review."""
    caller = _FakeScreener(
        responses=[
            {"outcome": "include", "rationale": "title looks on-topic", "confidence": 0.6},
            {"outcome": "include", "rationale": "abstract confirms", "confidence": 0.95},
        ]
    )
    d = screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert d.outcome is AppraisalOutcome.INCLUDE
    assert d.confidence == 0.95
    assert d.exclusion_code is None
    assert not d.requires_review
    assert len(caller.calls) == 2


def test_exclude_with_valid_code() -> None:
    """Pass 1 INCLUDE -> Pass 2 EXCLUDE with valid code is honoured."""
    caller = _FakeScreener(
        responses=[
            {"outcome": "include", "confidence": 0.75},
            {
                "outcome": "exclude",
                "exclusion_code": "EX-IRRELEVANT",
                "rationale": "off-topic on full abstract",
                "confidence": 0.92,
            },
        ]
    )
    d = screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert d.outcome is AppraisalOutcome.EXCLUDE
    assert d.exclusion_code == "EX-IRRELEVANT"
    assert d.rationale == "off-topic on full abstract"
    assert not d.requires_review


def test_review_outcome_propagates_review_flag() -> None:
    """LLM returning outcome=requires_review forces requires_review=True."""
    caller = _FakeScreener(
        responses=[
            {"outcome": "include", "confidence": 0.8},
            {"outcome": "requires_review", "rationale": "ambiguous", "confidence": 0.85},
        ]
    )
    d = screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert d.outcome is AppraisalOutcome.REVIEW
    assert d.requires_review
    assert d.exclusion_code is None


# ---------------------------------------------------------------------------
# Two-pass logic
# ---------------------------------------------------------------------------


def test_pass1_exclude_high_confidence_short_circuits() -> None:
    """Pass 1 EXCLUDE at >= TITLE_ONLY_EXCLUDE_THRESHOLD: Pass 2 is NOT called."""
    caller = _FakeScreener(
        responses=[
            {
                "outcome": "exclude",
                "exclusion_code": "EX-IRRELEVANT",
                "rationale": "title is clearly off-topic",
                "confidence": TITLE_ONLY_EXCLUDE_THRESHOLD,
            }
        ]
    )
    d = screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert d.outcome is AppraisalOutcome.EXCLUDE
    assert d.exclusion_code == "EX-IRRELEVANT"
    assert len(caller.calls) == 1


def test_pass1_exclude_low_confidence_does_not_short_circuit() -> None:
    """Pass 1 EXCLUDE just under the threshold: Pass 2 IS called."""
    caller = _FakeScreener(
        responses=[
            {
                "outcome": "exclude",
                "exclusion_code": "EX-IRRELEVANT",
                "confidence": TITLE_ONLY_EXCLUDE_THRESHOLD - 0.01,
            },
            {"outcome": "include", "confidence": 0.9},
        ]
    )
    d = screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert d.outcome is AppraisalOutcome.INCLUDE
    assert len(caller.calls) == 2


def test_pass1_include_does_not_short_circuit() -> None:
    """A high-confidence Pass 1 INCLUDE still triggers Pass 2."""
    caller = _FakeScreener(
        responses=[
            {"outcome": "include", "confidence": 0.99},
            {"outcome": "include", "confidence": 0.9},
        ]
    )
    screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert len(caller.calls) == 2


def test_pass1_view_excludes_abstract_pass2_includes_it() -> None:
    """Pass 1 sees title only; Pass 2 sees abstract too."""
    caller = _FakeScreener(
        responses=[
            {"outcome": "include", "confidence": 0.7},
            {"outcome": "include", "confidence": 0.9},
        ]
    )
    record = _rec(abstract="The full abstract goes here.")
    screen_record(record, _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert "abstract" not in caller.calls[0]["record_view"]
    assert caller.calls[1]["record_view"]["abstract"] == "The full abstract goes here."


def test_record_without_abstract_skips_pass2() -> None:
    """No abstract -> only Pass 1 called, Pass 1 decision stands."""
    caller = _FakeScreener(
        responses=[
            {
                "outcome": "include",
                "rationale": "title only",
                "confidence": 0.8,
            }
        ]
    )
    d = screen_record(_rec(abstract=None), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert d.outcome is AppraisalOutcome.INCLUDE
    assert d.rationale == "title only"
    assert len(caller.calls) == 1


def test_record_with_empty_abstract_skips_pass2() -> None:
    """Empty-string abstract is treated as 'no abstract'."""
    caller = _FakeScreener(responses=[{"outcome": "include", "confidence": 0.8}])
    screen_record(_rec(abstract=""), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert len(caller.calls) == 1


# ---------------------------------------------------------------------------
# Validation: malformed responses
# ---------------------------------------------------------------------------


def test_unknown_outcome_string_raises() -> None:
    caller = _FakeScreener(responses=[{"outcome": "maybe", "confidence": 0.9}])
    with pytest.raises(ValueError, match="unknown outcome"):
        screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)


def test_missing_outcome_raises() -> None:
    caller = _FakeScreener(responses=[{"confidence": 0.9}])
    with pytest.raises(ValueError, match=r"outcome.*missing"):
        screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)


def test_exclude_with_unknown_code_raises() -> None:
    caller = _FakeScreener(
        responses=[
            {
                "outcome": "exclude",
                "exclusion_code": "EX-NONSENSE",
                "confidence": 0.95,
            }
        ]
    )
    with pytest.raises(ValueError, match="unknown exclusion_code"):
        screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)


def test_exclude_without_code_raises() -> None:
    caller = _FakeScreener(responses=[{"outcome": "exclude", "confidence": 0.95}])
    with pytest.raises(ValueError, match="requires exclusion_code"):
        screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)


def test_include_with_exclusion_code_raises() -> None:
    caller = _FakeScreener(
        responses=[{"outcome": "include", "exclusion_code": "EX-IRRELEVANT", "confidence": 0.9}]
    )
    with pytest.raises(ValueError, match="exclusion_code must be None"):
        screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)


def test_confidence_out_of_range_raises() -> None:
    caller = _FakeScreener(responses=[{"outcome": "include", "confidence": 1.5}])
    with pytest.raises(ValueError, match=r"confidence must be in \[0, 1\]"):
        screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)


def test_confidence_wrong_type_raises() -> None:
    caller = _FakeScreener(responses=[{"outcome": "include", "confidence": "high"}])
    with pytest.raises(ValueError, match="confidence must be a number"):
        screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)


def test_exclusion_code_wrong_type_raises() -> None:
    caller = _FakeScreener(
        responses=[{"outcome": "exclude", "exclusion_code": 42, "confidence": 0.9}]
    )
    with pytest.raises(ValueError, match="exclusion_code must be a string"):
        screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)


# ---------------------------------------------------------------------------
# Low-confidence -> requires_review
# ---------------------------------------------------------------------------


def test_low_confidence_include_flagged_for_review() -> None:
    """Definitive INCLUDE but confidence below threshold -> review flag set."""
    caller = _FakeScreener(
        responses=[
            {"outcome": "include", "confidence": 0.8},
            {
                "outcome": "include",
                "confidence": REVIEW_THRESHOLD - 0.05,
            },
        ]
    )
    d = screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert d.outcome is AppraisalOutcome.INCLUDE
    assert d.requires_review


def test_at_review_threshold_not_flagged() -> None:
    """Exactly at REVIEW_THRESHOLD: not flagged (boundary is strict <)."""
    caller = _FakeScreener(
        responses=[
            {"outcome": "include", "confidence": 0.8},
            {"outcome": "include", "confidence": REVIEW_THRESHOLD},
        ]
    )
    d = screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert not d.requires_review


def test_no_confidence_provided_does_not_trigger_review_flag() -> None:
    """A response without 'confidence' is allowed; no review flag from threshold."""
    caller = _FakeScreener(
        responses=[
            {"outcome": "include"},
            {"outcome": "include", "rationale": "ok"},
        ]
    )
    d = screen_record(_rec(), _DEFAULT_INC, _DEFAULT_EXC, caller=caller)
    assert d.confidence is None
    assert not d.requires_review


# ---------------------------------------------------------------------------
# Threshold arg validation
# ---------------------------------------------------------------------------


def test_invalid_title_only_threshold_raises() -> None:
    caller = _FakeScreener(responses=[])
    with pytest.raises(ValueError, match="title_only_exclude_threshold"):
        screen_record(
            _rec(),
            _DEFAULT_INC,
            _DEFAULT_EXC,
            caller=caller,
            title_only_exclude_threshold=1.5,
        )


def test_invalid_review_threshold_raises() -> None:
    caller = _FakeScreener(responses=[])
    with pytest.raises(ValueError, match="review_threshold"):
        screen_record(
            _rec(),
            _DEFAULT_INC,
            _DEFAULT_EXC,
            caller=caller,
            review_threshold=-0.1,
        )


# ---------------------------------------------------------------------------
# Caller payload shape
# ---------------------------------------------------------------------------


def test_inclusion_and_exclusion_payloads_match_passed_criteria() -> None:
    inc = _inc(("INC-A", "alpha"), ("INC-B", "beta"))
    exc = _exc(("EX-A", "a desc"), ("EX-B", "b desc"))
    caller = _FakeScreener(
        responses=[
            {"outcome": "include", "confidence": 0.8},
            {"outcome": "include", "confidence": 0.9},
        ]
    )
    screen_record(_rec(), inc, exc, caller=caller)
    # Pass 1 + Pass 2 both receive the same payloads.
    for call in caller.calls:
        assert call["inclusion"] == [
            {"id": "INC-A", "description": "alpha"},
            {"id": "INC-B", "description": "beta"},
        ]
        assert call["exclusion"] == [
            {"code": "EX-A", "description": "a desc"},
            {"code": "EX-B", "description": "b desc"},
        ]


def test_record_view_carries_journal_and_year_when_present() -> None:
    caller = _FakeScreener(responses=[{"outcome": "include", "confidence": 0.9}])
    screen_record(
        _rec(abstract=None, journal="Cochrane DSR", year=2021),
        _DEFAULT_INC,
        _DEFAULT_EXC,
        caller=caller,
    )
    view = caller.calls[0]["record_view"]
    assert view["journal"] == "Cochrane DSR"
    assert view["year"] == 2021

# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.exclusion_codes."""

from __future__ import annotations

import pytest

from ring2.adapters.mpco.exclusion_codes import (
    EXCLUSION_PHASE_ROUTING,
    ExclusionCode,
    PrismaPhase,
    codes_for_phase,
    phase_for,
)

# ---------------------------------------------------------------------------
# Enum value contracts (canonical hyphenated strings, per Architecture v1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected_value"),
    [
        (ExclusionCode.LANGUAGE, "EX-LANGUAGE"),
        (ExclusionCode.IRRELEVANT, "EX-IRRELEVANT"),
        (ExclusionCode.DUPLICATE, "EX-DUPLICATE"),
        (ExclusionCode.NO_FULLTEXT, "EX-NO-FULLTEXT"),
        (ExclusionCode.A6_CATALOG, "EX-A6-CATALOG"),
    ],
)
def test_exclusion_code_values(code: ExclusionCode, expected_value: str) -> None:
    assert code.value == expected_value
    # StrEnum: should compare equal to its string value.
    assert code == expected_value


def test_exclusion_code_is_str_enum() -> None:
    """ExclusionCode values must be usable wherever a plain str is expected
    (e.g. AppraisalDecision.exclusion_code: str | None)."""
    code = ExclusionCode.LANGUAGE
    assert isinstance(code, str)


def test_prisma_phase_values() -> None:
    assert PrismaPhase.DEDUPLICATION == "deduplication"
    assert PrismaPhase.SCREENING == "screening"
    assert PrismaPhase.ELIGIBILITY == "eligibility"


# ---------------------------------------------------------------------------
# Routing — Handoff 26-06-27 Decision #20 and architectural intent
# ---------------------------------------------------------------------------


def test_routing_exhaustive_over_all_codes() -> None:
    """Every ExclusionCode must have exactly one PrismaPhase mapping."""
    assert set(EXCLUSION_PHASE_ROUTING) == set(ExclusionCode)


def test_routing_decision_20_a6_never_at_screening() -> None:
    """Per Handoff 26-06-27 Decision #20: §A6 only at eligibility, never at screening."""
    assert EXCLUSION_PHASE_ROUTING[ExclusionCode.A6_CATALOG] is PrismaPhase.ELIGIBILITY
    assert EXCLUSION_PHASE_ROUTING[ExclusionCode.A6_CATALOG] is not PrismaPhase.SCREENING


def test_routing_duplicate_at_deduplication() -> None:
    """Duplicates are caught before screening, by PMID/DOI overlap."""
    assert EXCLUSION_PHASE_ROUTING[ExclusionCode.DUPLICATE] is PrismaPhase.DEDUPLICATION


def test_routing_no_fulltext_at_eligibility() -> None:
    """Full-text absence is only knowable after retrieval, i.e. at the eligibility phase."""
    assert EXCLUSION_PHASE_ROUTING[ExclusionCode.NO_FULLTEXT] is PrismaPhase.ELIGIBILITY


@pytest.mark.parametrize(
    "code",
    [ExclusionCode.LANGUAGE, ExclusionCode.IRRELEVANT],
)
def test_routing_title_abstract_codes_at_screening(code: ExclusionCode) -> None:
    """LANGUAGE and IRRELEVANT are detectable from title/abstract → screening phase."""
    assert EXCLUSION_PHASE_ROUTING[code] is PrismaPhase.SCREENING


def test_routing_is_read_only() -> None:
    """MappingProxyType blocks mutation of the routing table."""
    with pytest.raises(TypeError):
        EXCLUSION_PHASE_ROUTING[ExclusionCode.LANGUAGE] = PrismaPhase.ELIGIBILITY  # type: ignore[index]


# ---------------------------------------------------------------------------
# phase_for()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected_phase"),
    [
        (ExclusionCode.LANGUAGE, PrismaPhase.SCREENING),
        (ExclusionCode.IRRELEVANT, PrismaPhase.SCREENING),
        (ExclusionCode.DUPLICATE, PrismaPhase.DEDUPLICATION),
        (ExclusionCode.NO_FULLTEXT, PrismaPhase.ELIGIBILITY),
        (ExclusionCode.A6_CATALOG, PrismaPhase.ELIGIBILITY),
    ],
)
def test_phase_for_matches_routing_table(code: ExclusionCode, expected_phase: PrismaPhase) -> None:
    assert phase_for(code) is expected_phase


# ---------------------------------------------------------------------------
# codes_for_phase() — reverse lookup
# ---------------------------------------------------------------------------


def test_codes_for_phase_screening() -> None:
    assert codes_for_phase(PrismaPhase.SCREENING) == frozenset(
        {ExclusionCode.LANGUAGE, ExclusionCode.IRRELEVANT}
    )


def test_codes_for_phase_eligibility() -> None:
    assert codes_for_phase(PrismaPhase.ELIGIBILITY) == frozenset(
        {ExclusionCode.NO_FULLTEXT, ExclusionCode.A6_CATALOG}
    )


def test_codes_for_phase_deduplication() -> None:
    assert codes_for_phase(PrismaPhase.DEDUPLICATION) == frozenset({ExclusionCode.DUPLICATE})


def test_codes_for_phase_partitions_all_codes() -> None:
    """Union of codes_for_phase across all phases must equal all ExclusionCodes,
    with no overlap (each code routed to exactly one phase)."""
    all_phases = list(PrismaPhase)
    partitions = [codes_for_phase(p) for p in all_phases]

    union: set[ExclusionCode] = set()
    for part in partitions:
        union |= part
    assert union == set(ExclusionCode)

    # No overlap between any two phases
    for i, a in enumerate(partitions):
        for b in partitions[i + 1 :]:
            assert a.isdisjoint(b)


def test_codes_for_phase_returns_frozen_set() -> None:
    """Returned set must be frozen so callers cannot mutate the routing implicitly."""
    result = codes_for_phase(PrismaPhase.SCREENING)
    assert isinstance(result, frozenset)

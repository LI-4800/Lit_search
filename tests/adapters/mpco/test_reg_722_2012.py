# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.reg_722_2012."""

from __future__ import annotations

import pytest

from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.reg_722_2012 import (
    ANNEX_I_SCOPE_BY_CLAIM_TYPE,
    REGULATORY_ANCHORS,
    AnnexIElement,
    elements_in_scope,
    regulatory_anchors,
)

# ---------------------------------------------------------------------------
# AnnexIElement enum — value contracts (hyphen-canonical per project convention)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("element", "expected_value"),
    [
        (AnnexIElement.GEOGRAPHIC_ORIGIN, "geographic-origin"),
        (AnnexIElement.TSE_RISK_ASSESSMENT, "tse-risk-assessment"),
        (AnnexIElement.INACTIVATION_PROCEDURE, "inactivation-procedure"),
        (AnnexIElement.TRACEABILITY, "traceability"),
    ],
)
def test_annex_i_element_values(element: AnnexIElement, expected_value: str) -> None:
    assert element.value == expected_value
    # StrEnum: should compare equal to its string value.
    assert element == expected_value


def test_annex_i_element_is_str_enum() -> None:
    """AnnexIElement values must be usable wherever a plain str is expected
    (e.g. audit-log entries, YAML serialisation)."""
    assert isinstance(AnnexIElement.GEOGRAPHIC_ORIGIN, str)


def test_annex_i_element_has_exactly_four_members() -> None:
    """Annex I of EU Regulation 722/2012 defines exactly four element domains."""
    assert len(AnnexIElement) == 4


# ---------------------------------------------------------------------------
# Mapping — exhaustiveness and structural contracts (U-1.6-B)
# ---------------------------------------------------------------------------


def test_mapping_exhaustive_over_all_claim_types() -> None:
    """Every ClaimType must have an Annex-I scope mapping entry."""
    assert set(ANNEX_I_SCOPE_BY_CLAIM_TYPE) == set(ClaimType)


def test_mapping_values_are_frozensets() -> None:
    """Returned scope sets must be frozen (immutable) for safe caching/sharing."""
    for scope in ANNEX_I_SCOPE_BY_CLAIM_TYPE.values():
        assert isinstance(scope, frozenset)


def test_mapping_is_read_only() -> None:
    """The mapping itself is wrapped in MappingProxyType — assignment must fail."""
    with pytest.raises(TypeError):
        ANNEX_I_SCOPE_BY_CLAIM_TYPE[ClaimType.UNKNOWN] = frozenset(  # type: ignore[index]
            {AnnexIElement.TRACEABILITY}
        )


def test_mapping_scope_members_are_annex_i_elements() -> None:
    """No claim-type scope may contain a non-AnnexIElement value."""
    for claim_type, scope in ANNEX_I_SCOPE_BY_CLAIM_TYPE.items():
        for element in scope:
            assert isinstance(element, AnnexIElement), (
                f"{claim_type!r} scope contains non-AnnexIElement member {element!r}"
            )


# ---------------------------------------------------------------------------
# elements_in_scope() — U-1.6-B confirmed mapping per claim type
# ---------------------------------------------------------------------------


def test_elements_in_scope_regulatory_compliance_returns_all_four() -> None:
    """REGULATORY_COMPLIANCE: full set — a regulatory-framed claim may touch any element."""
    assert elements_in_scope(ClaimType.REGULATORY_COMPLIANCE) == frozenset(AnnexIElement)


def test_elements_in_scope_biochemistry_returns_tse_and_inactivation() -> None:
    """BIOCHEMISTRY_MATERIAL_PROPERTY: process- and material-level elements only."""
    assert elements_in_scope(ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY) == frozenset(
        {AnnexIElement.TSE_RISK_ASSESSMENT, AnnexIElement.INACTIVATION_PROCEDURE}
    )


def test_elements_in_scope_safety_allergenicity_returns_origin_and_tse() -> None:
    """SAFETY_ALLERGENICITY: safety-relevant source-side elements."""
    assert elements_in_scope(ClaimType.SAFETY_ALLERGENICITY) == frozenset(
        {AnnexIElement.GEOGRAPHIC_ORIGIN, AnnexIElement.TSE_RISK_ASSESSMENT}
    )


def test_elements_in_scope_clinical_performance_is_empty() -> None:
    """CLINICAL_PERFORMANCE: empty — device-level claims do not engage Annex-I directly."""
    assert elements_in_scope(ClaimType.CLINICAL_PERFORMANCE) == frozenset()


def test_elements_in_scope_historical_market_use_returns_traceability_and_tse() -> None:
    """HISTORICAL_MARKET_USE: traceability + TSE per EMA/410/01 exposure-horizon framework."""
    assert elements_in_scope(ClaimType.HISTORICAL_MARKET_USE) == frozenset(
        {AnnexIElement.TRACEABILITY, AnnexIElement.TSE_RISK_ASSESSMENT}
    )


def test_elements_in_scope_unknown_is_empty() -> None:
    """UNKNOWN: empty — an unclassified claim cannot be assumed to engage Annex-I."""
    assert elements_in_scope(ClaimType.UNKNOWN) == frozenset()


def test_elements_in_scope_returns_same_object_on_repeat_call() -> None:
    """The mapping is static; repeat calls return identical frozenset instances
    (frozenset is hashable so identity sharing is safe and efficient)."""
    first = elements_in_scope(ClaimType.REGULATORY_COMPLIANCE)
    second = elements_in_scope(ClaimType.REGULATORY_COMPLIANCE)
    assert first is second


# ---------------------------------------------------------------------------
# regulatory_anchors() — VERBATIM strings, order is part of contract
# ---------------------------------------------------------------------------


def test_regulatory_anchors_verbatim_and_ordered() -> None:
    """Anchor strings MUST be reproduced verbatim, in the contractual order.

    These strings are reproduced exactly from the source regulations and
    must never be paraphrased, abbreviated, or reordered. The test
    pins both content and order character-for-character.
    """
    assert regulatory_anchors() == (
        "Regulation (EU) No 722/2012, Annex I",
        "MDR Rule 18 (Annex VIII)",
        "MDR Annex I, GSPR 13.2(c)",
        "MDR Annex VII, Section 4.5.6",
        "EMA/410/01 Rev. 3",
        "Commission Decision 2007/453/EC",
    )


def test_regulatory_anchors_returns_tuple_for_immutability() -> None:
    """Tuple, not list — anchors are part of the regulatory contract."""
    assert isinstance(regulatory_anchors(), tuple)


def test_regulatory_anchors_module_constant_matches_function() -> None:
    """The function is a thin accessor over the module-level constant."""
    assert regulatory_anchors() is REGULATORY_ANCHORS


def test_regulatory_anchors_first_is_operative_regulation() -> None:
    """Contractual ordering: the operative regulation (722/2012) comes first."""
    anchors = regulatory_anchors()
    assert anchors[0].startswith("Regulation (EU) No 722/2012")

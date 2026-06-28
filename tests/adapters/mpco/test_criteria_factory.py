# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.criteria_factory.

Verifies that the pure factory functions extracted from
:class:`MPCOAdapter` retain the full behavioural contract:
    * universal ``INC-001`` baseline always present;
    * 722/2012 Annex-I criteria emitted when
      ``applicable_regulation == "722_2012"`` and the claim type has
      Annex-I scope, in declaration order;
    * full 5-code exclusion set (all PRISMA phases) emitted in
      :class:`ExclusionCode` enum-declaration order;
    * descriptions verbatim;
    * determinism (pure function — identical input → identical output).
"""

from __future__ import annotations

from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.criteria_factory import (
    ANNEX_I_DESCRIPTIONS,
    BASE_INCLUSION,
    EXCLUSION_DESCRIPTIONS,
    annex_i_criterion_id,
    make_exclusion_criteria,
    make_inclusion_criteria,
)
from ring2.adapters.mpco.exclusion_codes import ExclusionCode
from ring2.adapters.mpco.reg_722_2012 import AnnexIElement, elements_in_scope
from ring2.adapters.mpco.schema import (
    ApplicableRegulation,
    Comparator,
    Material,
    MPCOClaim,
    Outcome,
    Property,
)
from ring2.adapters.mpco.table_mapping import CellRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_claim(
    *,
    applicable_regulation: ApplicableRegulation = "none",
    claim_type: ClaimType = ClaimType.UNKNOWN,
    claim_id: str = "TEST-001",
) -> MPCOClaim:
    """Build a minimally valid MPCOClaim for tests."""
    return MPCOClaim(
        claim_id=claim_id,
        source_table_cell=CellRef(workbook="test.xlsx", sheet="Test", row=1, column_label="A"),
        material=Material(description="test material"),
        property=Property(description="test property"),
        comparator=Comparator(description="test comparator"),
        outcome=Outcome(description="test outcome"),
        applicable_regulation=applicable_regulation,
        claim_type=claim_type,
    )


# ---------------------------------------------------------------------------
# annex_i_criterion_id
# ---------------------------------------------------------------------------


def test_annex_i_criterion_id_format() -> None:
    """Canonical form: ``INC-722-<UPPER hyphenated>``."""
    assert annex_i_criterion_id(AnnexIElement.GEOGRAPHIC_ORIGIN) == "INC-722-GEOGRAPHIC-ORIGIN"
    assert annex_i_criterion_id(AnnexIElement.TSE_RISK_ASSESSMENT) == "INC-722-TSE-RISK-ASSESSMENT"
    assert (
        annex_i_criterion_id(AnnexIElement.INACTIVATION_PROCEDURE)
        == "INC-722-INACTIVATION-PROCEDURE"
    )
    assert annex_i_criterion_id(AnnexIElement.TRACEABILITY) == "INC-722-TRACEABILITY"


# ---------------------------------------------------------------------------
# make_inclusion_criteria
# ---------------------------------------------------------------------------


def test_make_inclusion_criteria_always_includes_base_first() -> None:
    """The universal ``INC-001`` baseline is always the first criterion."""
    claim = _make_claim(applicable_regulation="none", claim_type=ClaimType.UNKNOWN)
    criteria = make_inclusion_criteria(claim)
    assert criteria.criteria[0] == BASE_INCLUSION
    assert criteria.criteria[0].id == "INC-001"


def test_make_inclusion_criteria_non_722_returns_only_base() -> None:
    """For non-722/2012 claims, only the universal baseline is emitted."""
    claim = _make_claim(applicable_regulation="none", claim_type=ClaimType.SAFETY_ALLERGENICITY)
    criteria = make_inclusion_criteria(claim)
    assert len(criteria.criteria) == 1
    assert criteria.criteria[0] == BASE_INCLUSION


def test_make_inclusion_criteria_722_safety_includes_all_annex_i() -> None:
    """For SAFETY_ALLERGENICITY under 722/2012, all four Annex-I elements apply."""
    claim = _make_claim(applicable_regulation="722_2012", claim_type=ClaimType.SAFETY_ALLERGENICITY)
    criteria = make_inclusion_criteria(claim)
    expected_in_scope = elements_in_scope(ClaimType.SAFETY_ALLERGENICITY)
    # 1 baseline + N Annex-I criteria
    assert len(criteria.criteria) == 1 + len(expected_in_scope)
    # Baseline first
    assert criteria.criteria[0] == BASE_INCLUSION
    # Annex-I criterion ids cover the in-scope set exactly
    annex_ids = {c.id for c in criteria.criteria[1:]}
    expected_ids = {annex_i_criterion_id(e) for e in expected_in_scope}
    assert annex_ids == expected_ids


def test_make_inclusion_criteria_annex_i_declaration_order() -> None:
    """Annex-I criteria emit in :class:`AnnexIElement` declaration order."""
    claim = _make_claim(applicable_regulation="722_2012", claim_type=ClaimType.SAFETY_ALLERGENICITY)
    criteria = make_inclusion_criteria(claim)
    # Strip baseline; remaining must follow AnnexIElement declaration order
    annex_criteria_ids = [c.id for c in criteria.criteria[1:]]
    in_scope = elements_in_scope(ClaimType.SAFETY_ALLERGENICITY)
    expected_ordered_ids = [annex_i_criterion_id(e) for e in AnnexIElement if e in in_scope]
    assert annex_criteria_ids == expected_ordered_ids


def test_make_inclusion_criteria_descriptions_are_verbatim() -> None:
    """Annex-I criterion descriptions equal the verbatim ANNEX_I_DESCRIPTIONS entries."""
    claim = _make_claim(applicable_regulation="722_2012", claim_type=ClaimType.SAFETY_ALLERGENICITY)
    criteria = make_inclusion_criteria(claim)
    # Build a {id -> description} map from the emitted criteria, then check
    # each against the verbatim ANNEX_I_DESCRIPTIONS table.
    description_by_id = {c.id: c.description for c in criteria.criteria[1:]}
    in_scope = elements_in_scope(ClaimType.SAFETY_ALLERGENICITY)
    for element in in_scope:
        cid = annex_i_criterion_id(element)
        assert description_by_id[cid] == ANNEX_I_DESCRIPTIONS[element]


def test_make_inclusion_criteria_deterministic() -> None:
    """Two calls with the same claim produce equal results (pure function)."""
    claim = _make_claim(applicable_regulation="722_2012", claim_type=ClaimType.SAFETY_ALLERGENICITY)
    a = make_inclusion_criteria(claim)
    b = make_inclusion_criteria(claim)
    assert a.criteria == b.criteria


# ---------------------------------------------------------------------------
# make_exclusion_criteria
# ---------------------------------------------------------------------------


def test_make_exclusion_criteria_contains_all_five_codes() -> None:
    """All five :class:`ExclusionCode` members are present."""
    claim = _make_claim()
    criteria = make_exclusion_criteria(claim)
    emitted_codes = {c.code for c in criteria.criteria}
    expected_codes = {code.value for code in ExclusionCode}
    assert emitted_codes == expected_codes
    assert len(criteria.criteria) == 5


def test_make_exclusion_criteria_enum_declaration_order() -> None:
    """Criteria emit in :class:`ExclusionCode` declaration order."""
    claim = _make_claim()
    criteria = make_exclusion_criteria(claim)
    emitted_order = [c.code for c in criteria.criteria]
    expected_order = [code.value for code in ExclusionCode]
    assert emitted_order == expected_order


def test_make_exclusion_criteria_descriptions_are_verbatim() -> None:
    """Each criterion's description equals the verbatim EXCLUSION_DESCRIPTIONS entry."""
    claim = _make_claim()
    criteria = make_exclusion_criteria(claim)
    description_by_code = {c.code: c.description for c in criteria.criteria}
    for code in ExclusionCode:
        assert description_by_code[code.value] == EXCLUSION_DESCRIPTIONS[code]


def test_make_exclusion_criteria_deterministic() -> None:
    """Two calls with the same claim produce equal results (pure function)."""
    claim = _make_claim()
    a = make_exclusion_criteria(claim)
    b = make_exclusion_criteria(claim)
    assert a.criteria == b.criteria


def test_make_exclusion_criteria_independent_of_claim_fields() -> None:
    """Per current contract, exclusion set is identical regardless of claim fields."""
    claim_none = _make_claim(applicable_regulation="none", claim_type=ClaimType.UNKNOWN)
    claim_722 = _make_claim(
        applicable_regulation="722_2012", claim_type=ClaimType.SAFETY_ALLERGENICITY
    )
    a = make_exclusion_criteria(claim_none)
    b = make_exclusion_criteria(claim_722)
    assert a.criteria == b.criteria

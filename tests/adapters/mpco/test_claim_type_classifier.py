# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.claim_type_classifier."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ring2.adapters.mpco.claim_type_classifier import (
    CLAIM_TYPE_PRIORITY,
    CONFIDENCE_AUTHORITATIVE,
    REVIEW_THRESHOLD,
    ClaimType,
    ClaimTypeClassification,
    classify,
)

# ---------------------------------------------------------------------------
# Enum + dataclass contracts
# ---------------------------------------------------------------------------


def test_claim_type_enum_has_five_business_types_plus_unknown() -> None:
    """Handoff 26-06-26 §12 hybrid hierarchy specifies exactly five claim types."""
    business_types = {ct for ct in ClaimType if ct is not ClaimType.UNKNOWN}
    assert len(business_types) == 5
    assert ClaimType.REGULATORY_COMPLIANCE in business_types
    assert ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY in business_types
    assert ClaimType.SAFETY_ALLERGENICITY in business_types
    assert ClaimType.CLINICAL_PERFORMANCE in business_types
    assert ClaimType.HISTORICAL_MARKET_USE in business_types


def test_claim_type_priority_covers_all_business_types() -> None:
    """Tie-break priority must rank every business type exactly once."""
    business_types = {ct for ct in ClaimType if ct is not ClaimType.UNKNOWN}
    assert set(CLAIM_TYPE_PRIORITY) == business_types
    assert len(CLAIM_TYPE_PRIORITY) == 5


def test_claim_type_priority_regulatory_first() -> None:
    """Per U-1.6-A, regulatory framing dominates the tie-break order."""
    assert CLAIM_TYPE_PRIORITY[0] is ClaimType.REGULATORY_COMPLIANCE


def test_classification_frozen() -> None:
    c = ClaimTypeClassification(
        claim_type=ClaimType.UNKNOWN, confidence=0.0, evidence=(), requires_review=True
    )
    with pytest.raises(FrozenInstanceError):
        c.confidence = 0.9  # type: ignore[misc]


def test_classification_rejects_confidence_outside_unit_interval() -> None:
    with pytest.raises(ValueError, match="confidence must be in"):
        ClaimTypeClassification(
            claim_type=ClaimType.UNKNOWN, confidence=1.5, evidence=(), requires_review=True
        )


def test_classification_rejects_primary_in_alternative_types() -> None:
    with pytest.raises(ValueError, match="must not appear in alternative_types"):
        ClaimTypeClassification(
            claim_type=ClaimType.REGULATORY_COMPLIANCE,
            confidence=0.9,
            evidence=(),
            requires_review=False,
            alternative_types=(ClaimType.REGULATORY_COMPLIANCE,),
        )


# ---------------------------------------------------------------------------
# Empty / no-match cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\t  \n"])
def test_classify_empty_text_returns_unknown(text: str) -> None:
    result = classify(text)
    assert result.claim_type is ClaimType.UNKNOWN
    assert result.confidence == 0.0
    assert result.requires_review is True


def test_classify_no_matches_returns_unknown() -> None:
    result = classify("the quick brown fox jumps over the lazy dog")
    assert result.claim_type is ClaimType.UNKNOWN
    assert result.confidence == 0.0
    assert result.requires_review is True


# ---------------------------------------------------------------------------
# Strong-anchor hits — one anchor per type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("This device complies with MDR Annex I.", ClaimType.REGULATORY_COMPLIANCE),
        (
            "Pepsin-aided extraction preserves the triple helix.",
            ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY,
        ),
        ("Risk of alpha-gal sensitisation noted.", ClaimType.SAFETY_ALLERGENICITY),
        (
            "Randomized controlled trial showed superior bone regeneration.",
            ClaimType.CLINICAL_PERFORMANCE,
        ),
        (
            "Long-term clinical experience documented in post-market surveillance.",
            ClaimType.HISTORICAL_MARKET_USE,
        ),
    ],
)
def test_classify_strong_anchor_picks_correct_type(text: str, expected_type: ClaimType) -> None:
    result = classify(text)
    assert result.claim_type is expected_type
    assert result.confidence >= CONFIDENCE_AUTHORITATIVE


def test_classify_strong_anchor_requires_review_false_when_solo() -> None:
    """A single strong-anchor hit with no cross-type conflict needs no review."""
    result = classify("This device complies with MDR Annex I.")
    assert result.requires_review is False
    assert result.alternative_types == ()


def test_classify_multiple_strong_anchors_increase_confidence() -> None:
    """Each additional strong anchor nudges confidence up (capped at 0.95)."""
    one = classify("Pepsin-aided extraction preserves the triple helix.")
    many = classify(
        "Pepsin digestion preserves the triple helix without disturbing telopeptide "
        "structure; hydroxyproline content remains stable."
    )
    assert many.confidence > one.confidence
    assert many.confidence <= 0.95


# ---------------------------------------------------------------------------
# Supporting-only matches — always requires_review
# ---------------------------------------------------------------------------


def test_classify_supporting_only_below_authoritative_and_requires_review() -> None:
    """A claim with only supporting anchors falls below CONFIDENCE_AUTHORITATIVE."""
    result = classify("The biocompatibility and resorption profile are favourable.")
    assert result.claim_type is ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY
    assert result.confidence < CONFIDENCE_AUTHORITATIVE
    assert result.confidence >= REVIEW_THRESHOLD
    assert result.requires_review is True


def test_classify_single_supporting_anchor_at_review_threshold() -> None:
    """A single supporting hit sits exactly at REVIEW_THRESHOLD."""
    result = classify("Material shows good biocompatibility.")
    assert result.claim_type is ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY
    assert result.confidence == REVIEW_THRESHOLD
    assert result.requires_review is True


# ---------------------------------------------------------------------------
# Cross-type conflict — Conflict Zone 1 (722/2012 + alpha-gal)
# ---------------------------------------------------------------------------


def test_classify_conflict_zone_1_regulatory_dominates_safety() -> None:
    """Tie between REGULATORY and SAFETY -> primary REGULATORY (CLAIM_TYPE_PRIORITY),
    secondary SAFETY, requires_review. The test text has exactly one strong anchor
    per type to force the tie-break; anchor counts otherwise dominate."""
    result = classify("Per 722/2012, the risk of alpha-gal must be assessed.")
    assert result.claim_type is ClaimType.REGULATORY_COMPLIANCE
    assert ClaimType.SAFETY_ALLERGENICITY in result.alternative_types
    assert result.requires_review is True


def test_classify_more_anchors_beat_priority_tiebreak() -> None:
    """Documented intentional behaviour: anchor counts dominate over tie-break priority.
    Two strong SAFETY anchors outweigh one strong REGULATORY anchor even though
    REGULATORY has tie-break priority. The tie-break only resolves equal scores."""
    result = classify("Regulation 722/2012 covers alpha-gal sensitisation and cross-reactivity.")
    assert result.claim_type is ClaimType.SAFETY_ALLERGENICITY
    assert ClaimType.REGULATORY_COMPLIANCE in result.alternative_types
    assert result.requires_review is True


def test_classify_tie_break_uses_priority_order() -> None:
    """When two types tie on score, CLAIM_TYPE_PRIORITY decides the primary."""
    # One strong anchor for each of REGULATORY and CLINICAL_PERFORMANCE.
    result = classify("The MDR governs the conduct of any randomized controlled trial.")
    assert result.claim_type is ClaimType.REGULATORY_COMPLIANCE
    assert ClaimType.CLINICAL_PERFORMANCE in result.alternative_types


# ---------------------------------------------------------------------------
# Boundary handling — alphanumeric lookaround
# ---------------------------------------------------------------------------


def test_classify_mdr_matches_with_hyphen_boundary() -> None:
    """'MDR' anchored term must match 'MDR-compliant' (hyphen is a boundary)."""
    result = classify("The device is MDR-compliant under Annex VIII.")
    assert result.claim_type is ClaimType.REGULATORY_COMPLIANCE


def test_classify_mdr_does_not_match_inside_word() -> None:
    """'MDR' must not match inside 'medroxyprogesterone' or 'MDR1'."""
    result = classify("Treatment with medroxyprogesterone showed MDR1 expression.")
    # Neither plausible match should fire the REGULATORY type.
    assert result.claim_type is not ClaimType.REGULATORY_COMPLIANCE


def test_classify_rct_does_not_match_inside_word() -> None:
    """'RCT' must not match 'PRCT' or 'RCTs' (no boundary)."""
    result = classify("Authors referenced PRCT methodology and earlier RCTs only.")
    # CLINICAL_PERFORMANCE strong anchor 'RCT' should not have fired.
    assert result.claim_type is not ClaimType.CLINICAL_PERFORMANCE


def test_classify_rct_matches_standalone() -> None:
    """'RCT' must match as a standalone token."""
    result = classify("This RCT enrolled 120 subjects.")
    assert result.claim_type is ClaimType.CLINICAL_PERFORMANCE


def test_classify_510k_special_chars_match_literally() -> None:
    """Anchors with parens must match as literals despite regex-special chars."""
    result = classify("The product cleared 510(k) review in 2018.")
    assert result.claim_type is ClaimType.REGULATORY_COMPLIANCE


def test_classify_722_2012_slash_anchor_matches() -> None:
    """Anchors with slashes must match (722/2012, EMA/410/01, ISO 10993)."""
    for text in (
        "Per EU Regulation 722/2012 the manufacturer must document TSE risk.",
        "Compliance with EMA/410/01 was demonstrated.",
        "Cytotoxicity per ISO 10993 was acceptable.",
    ):
        result = classify(text)
        assert result.claim_type is ClaimType.REGULATORY_COMPLIANCE, (
            f"Anchor regex failure for: {text!r}"
        )


# ---------------------------------------------------------------------------
# Case-insensitivity
# ---------------------------------------------------------------------------


def test_classify_is_case_insensitive() -> None:
    upper = classify("THE MDR APPLIES TO THIS DEVICE.")
    lower = classify("the mdr applies to this device.")
    assert upper.claim_type is ClaimType.REGULATORY_COMPLIANCE
    assert lower.claim_type is ClaimType.REGULATORY_COMPLIANCE


# ---------------------------------------------------------------------------
# Evidence trail
# ---------------------------------------------------------------------------


def test_classify_evidence_contains_strong_anchor_label() -> None:
    result = classify("Compliance with MDR Annex I is documented.")
    assert any("strong:" in e for e in result.evidence)
    assert any("MDR" in e for e in result.evidence)


def test_classify_evidence_records_alternative_types_on_conflict() -> None:
    """One anchor per type to force tie-break, then verify SAFETY appears as alternative."""
    result = classify("Per 722/2012, the risk of alpha-gal must be assessed.")
    # The secondary type should be mentioned in evidence with its anchors.
    alt_evidence = [e for e in result.evidence if e.startswith("alternative")]
    assert len(alt_evidence) >= 1
    assert any("safety_allergenicity" in e for e in alt_evidence)


# ---------------------------------------------------------------------------
# StrEnum-ness — ClaimType values usable as strings
# ---------------------------------------------------------------------------


def test_claim_type_is_str_enum() -> None:
    assert isinstance(ClaimType.REGULATORY_COMPLIANCE, str)
    assert ClaimType.REGULATORY_COMPLIANCE == "regulatory_compliance"

# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for the eight Stufe-1.8 Inkrement-6 appraisal-lens stub modules.

Verifies:
    * each stub registers under its expected name on subpackage import;
    * each stub's ``applicable_claim_types`` matches the appraisal
      matrix in Handoff v6;
    * each stub's :meth:`appraise` and :meth:`render_summary` raise
      :class:`NotImplementedError` with a message naming the class and
      method.

The subpackage import (top of the file) triggers all @register_lens
decorators; no further fixture setup is needed.
"""

from __future__ import annotations

import pytest

from ring2.adapters.mpco.appraisal import get_lens, names
from ring2.adapters.mpco.appraisal.arrive import ArriveLens
from ring2.adapters.mpco.appraisal.astm_iso_material import AstmIsoMaterialLens
from ring2.adapters.mpco.appraisal.care_caseseries import CareCaseseriesLens
from ring2.adapters.mpco.appraisal.glp_oecd import GlpOecdLens
from ring2.adapters.mpco.appraisal.grade import GradeLens
from ring2.adapters.mpco.appraisal.registry_authoritativeness import (
    RegistryAuthoritativenessLens,
)
from ring2.adapters.mpco.appraisal.rob2 import Rob2Lens
from ring2.adapters.mpco.appraisal.robins_i import RobinsILens
from ring2.adapters.mpco.claim_type_classifier import ClaimType

# Appraisal-matrix expectation per Handoff v6.
# (registry_name, lens_class, expected applicable_claim_types)
EXPECTED_STUBS = (
    ("rob2", Rob2Lens, frozenset({ClaimType.CLINICAL_PERFORMANCE, ClaimType.SAFETY_ALLERGENICITY})),
    ("grade", GradeLens, frozenset({ClaimType.CLINICAL_PERFORMANCE})),
    ("robins_i", RobinsILens, frozenset({ClaimType.CLINICAL_PERFORMANCE})),
    ("glp_oecd", GlpOecdLens, frozenset({ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY})),
    ("arrive", ArriveLens, frozenset({ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY})),
    (
        "astm_iso_material",
        AstmIsoMaterialLens,
        frozenset({ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY}),
    ),
    ("care_caseseries", CareCaseseriesLens, frozenset({ClaimType.SAFETY_ALLERGENICITY})),
    (
        "registry_authoritativeness",
        RegistryAuthoritativenessLens,
        frozenset({ClaimType.HISTORICAL_MARKET_USE}),
    ),
)


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------


def test_all_eight_stub_lenses_registered() -> None:
    """All 8 stubs from the appraisal matrix appear in names() after import."""
    registered = set(names())
    expected = {n for n, _, _ in EXPECTED_STUBS}
    # Allow for additional non-stub lenses registered by other modules
    # (e.g. meddev_a6 in later increments).
    assert expected.issubset(registered)


def test_meddev_a6_registered_at_inkrement_7() -> None:
    """meddev_a6 is added in Inkrement 7 — it MUST be registered.

    Regression guard: protects the Inkrement-7 boundary. If this fails,
    the meddev_a6 side-effect import or @register_lens decorator broke.
    """
    assert "meddev_a6" in names()


# ---------------------------------------------------------------------------
# Per-lens metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lens_name", "lens_cls", "expected_claim_types"),
    EXPECTED_STUBS,
)
def test_stub_registers_under_expected_name(
    lens_name: str,
    lens_cls: type,
    expected_claim_types: frozenset[ClaimType],
) -> None:
    """get_lens(name) returns exactly the class declared in that module."""
    assert get_lens(lens_name) is lens_cls


@pytest.mark.parametrize(
    ("lens_name", "lens_cls", "expected_claim_types"),
    EXPECTED_STUBS,
)
def test_stub_class_name_attribute_matches_registry_key(
    lens_name: str,
    lens_cls: type,
    expected_claim_types: frozenset[ClaimType],
) -> None:
    """The class's `name` ClassVar equals the registry key."""
    assert lens_cls.name == lens_name


@pytest.mark.parametrize(
    ("lens_name", "lens_cls", "expected_claim_types"),
    EXPECTED_STUBS,
)
def test_stub_applicable_claim_types_match_matrix(
    lens_name: str,
    lens_cls: type,
    expected_claim_types: frozenset[ClaimType],
) -> None:
    """applicable_claim_types matches the Handoff-v6 appraisal matrix."""
    assert lens_cls.applicable_claim_types == expected_claim_types


# ---------------------------------------------------------------------------
# NotImplementedError raising
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lens_name", "lens_cls", "expected_claim_types"),
    EXPECTED_STUBS,
)
def test_stub_appraise_raises_not_implemented(
    lens_name: str,
    lens_cls: type,
    expected_claim_types: frozenset[ClaimType],
) -> None:
    """Each stub's appraise() raises NotImplementedError with class+method name."""
    lens = lens_cls()
    with pytest.raises(NotImplementedError) as exc_info:
        lens.appraise(record=None, claim=None)  # type: ignore[arg-type]
    msg = str(exc_info.value)
    assert lens_cls.__name__ in msg
    assert "appraise" in msg


@pytest.mark.parametrize(
    ("lens_name", "lens_cls", "expected_claim_types"),
    EXPECTED_STUBS,
)
def test_stub_render_summary_raises_not_implemented(
    lens_name: str,
    lens_cls: type,
    expected_claim_types: frozenset[ClaimType],
) -> None:
    """Each stub's render_summary() raises NotImplementedError with class+method name."""
    lens = lens_cls()
    with pytest.raises(NotImplementedError) as exc_info:
        lens.render_summary(())
    msg = str(exc_info.value)
    assert lens_cls.__name__ in msg
    assert "render_summary" in msg

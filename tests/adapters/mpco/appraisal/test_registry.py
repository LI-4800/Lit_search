# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.appraisal.registry.

Covers register_lens / get_lens / names / clear contract.

Test isolation:
    The registry is module-global. Each test that mutates it uses a
    save-and-restore pattern via the ``isolated_registry`` fixture so
    bleed across tests (and back to the subpackage's auto-registered
    stubs) is impossible.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

import pytest

from ring2.adapters.mpco.appraisal import (
    AppraisalLens,
    AppraisalResult,
    clear,
    get_lens,
    names,
    register_lens,
)
from ring2.adapters.mpco.appraisal.registry import _REGISTRY
from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import MPCOClaim
from ring2.core.adapter_base import PubMedRecord

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_registry() -> Iterator[None]:
    """Snapshot the registry, let the test mutate it, restore afterwards."""
    snapshot = dict(_REGISTRY)
    try:
        clear()
        yield
    finally:
        clear()
        _REGISTRY.update(snapshot)


def _make_lens_class(class_name: str, lens_name: str) -> type[AppraisalLens]:
    """Build a minimal concrete AppraisalLens subclass with the given name."""

    def appraise(self: AppraisalLens, record: PubMedRecord, claim: MPCOClaim) -> AppraisalResult:
        return AppraisalResult(
            pmid=record.pmid, lens_name=self.name, rationale="ok", qualifies=True
        )

    def render_summary(self: AppraisalLens, results: tuple[AppraisalResult, ...]) -> str:
        return f"## {self.name} — {len(results)}"

    return type(
        class_name,
        (AppraisalLens,),
        {
            "name": lens_name,
            "applicable_claim_types": frozenset({ClaimType.CLINICAL_PERFORMANCE}),
            "appraise": appraise,
            "render_summary": render_summary,
            "__annotations__": {
                "name": ClassVar[str],
                "applicable_claim_types": ClassVar[frozenset[ClaimType]],
            },
        },
    )


# ---------------------------------------------------------------------------
# register_lens
# ---------------------------------------------------------------------------


def test_register_lens_adds_to_registry(isolated_registry: None) -> None:
    cls = _make_lens_class("FooLens", "foo")
    register_lens(cls)
    assert "foo" in names()
    assert get_lens("foo") is cls


def test_register_lens_returns_class_unchanged(isolated_registry: None) -> None:
    """Decorator semantics: returns the class identically for chaining."""
    cls = _make_lens_class("BarLens", "bar")
    returned = register_lens(cls)
    assert returned is cls


def test_register_lens_idempotent_for_same_class(isolated_registry: None) -> None:
    """Re-registering the same class under its own name is a no-op."""
    cls = _make_lens_class("BazLens", "baz")
    register_lens(cls)
    register_lens(cls)  # second call must not raise
    assert names().count("baz") == 1


def test_register_lens_duplicate_name_different_class_raises(
    isolated_registry: None,
) -> None:
    """Two different classes with the same name → ValueError."""
    cls1 = _make_lens_class("Quux1", "quux")
    cls2 = _make_lens_class("Quux2", "quux")
    register_lens(cls1)
    with pytest.raises(ValueError, match="already registered"):
        register_lens(cls2)


def test_register_lens_empty_name_raises(isolated_registry: None) -> None:
    cls = _make_lens_class("EmptyName", "")
    with pytest.raises(ValueError, match=r"empty \.name"):
        register_lens(cls)


# ---------------------------------------------------------------------------
# get_lens
# ---------------------------------------------------------------------------


def test_get_lens_unknown_raises_keyerror(isolated_registry: None) -> None:
    with pytest.raises(KeyError, match="No AppraisalLens registered"):
        get_lens("does_not_exist")


def test_get_lens_keyerror_lists_available_names(isolated_registry: None) -> None:
    """The KeyError message includes the sorted list of available names."""
    register_lens(_make_lens_class("AAALens", "aaa"))
    register_lens(_make_lens_class("BBBLens", "bbb"))
    with pytest.raises(KeyError) as exc_info:
        get_lens("missing")
    msg = str(exc_info.value)
    assert "aaa" in msg
    assert "bbb" in msg


# ---------------------------------------------------------------------------
# names / clear
# ---------------------------------------------------------------------------


def test_names_returns_sorted_tuple(isolated_registry: None) -> None:
    register_lens(_make_lens_class("Z", "zeta"))
    register_lens(_make_lens_class("A", "alpha"))
    register_lens(_make_lens_class("M", "mu"))
    assert names() == ("alpha", "mu", "zeta")


def test_names_empty_when_registry_empty(isolated_registry: None) -> None:
    assert names() == ()


def test_clear_empties_registry(isolated_registry: None) -> None:
    register_lens(_make_lens_class("X", "x"))
    assert len(names()) == 1
    clear()
    assert names() == ()

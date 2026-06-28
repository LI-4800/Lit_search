# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.adapters.mpco.appraisal.base.

Covers the :class:`AppraisalResult` Pydantic schema and the
:class:`AppraisalLens` ABC contract.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ValidationError

from ring2.adapters.mpco.appraisal.base import AppraisalLens, AppraisalResult
from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import MPCOClaim
from ring2.core.adapter_base import PubMedRecord

# ---------------------------------------------------------------------------
# AppraisalResult — schema contract
# ---------------------------------------------------------------------------


def test_appraisal_result_minimal_construction() -> None:
    """A valid AppraisalResult builds from the four base fields."""
    r = AppraisalResult(
        pmid="12345678",
        lens_name="rob2",
        rationale="verbatim methodology text",
        qualifies=True,
    )
    assert r.pmid == "12345678"
    assert r.lens_name == "rob2"
    assert r.rationale == "verbatim methodology text"
    assert r.qualifies is True


def test_appraisal_result_is_frozen() -> None:
    """AppraisalResult instances are immutable (Pydantic frozen=True)."""
    r = AppraisalResult(pmid="1", lens_name="x", rationale="r", qualifies=False)
    with pytest.raises(ValidationError):
        r.pmid = "2"  # type: ignore[misc]


def test_appraisal_result_forbids_extra_fields() -> None:
    """Unknown fields raise — guards against typos in lens subclasses."""
    with pytest.raises(ValidationError):
        AppraisalResult(
            pmid="1",
            lens_name="x",
            rationale="r",
            qualifies=True,
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_appraisal_result_requires_all_fields() -> None:
    """All four base fields are required (no defaults)."""
    with pytest.raises(ValidationError):
        AppraisalResult(pmid="1", lens_name="x", rationale="r")  # type: ignore[call-arg]


def test_appraisal_result_qualifies_required_no_none() -> None:
    """`qualifies` cannot be None — it is a required bool field."""
    with pytest.raises(ValidationError):
        AppraisalResult(
            pmid="1",
            lens_name="x",
            rationale="r",
            qualifies=None,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# AppraisalLens — ABC contract
# ---------------------------------------------------------------------------


def test_appraisal_lens_cannot_instantiate_directly() -> None:
    """AppraisalLens is abstract — direct instantiation raises TypeError."""
    with pytest.raises(TypeError):
        AppraisalLens()  # type: ignore[abstract]


def test_appraisal_lens_subclass_missing_methods_cannot_instantiate() -> None:
    """A subclass that omits abstract methods cannot be instantiated."""

    class _Incomplete(AppraisalLens):
        name: ClassVar[str] = "incomplete"
        applicable_claim_types: ClassVar[frozenset[ClaimType]] = frozenset()

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_appraisal_lens_concrete_subclass_works() -> None:
    """A fully concrete subclass instantiates and its methods are callable."""

    class _Fake(AppraisalLens):
        name: ClassVar[str] = "fake"
        applicable_claim_types: ClassVar[frozenset[ClaimType]] = frozenset(
            {ClaimType.CLINICAL_PERFORMANCE}
        )

        def appraise(self, record: PubMedRecord, claim: MPCOClaim) -> AppraisalResult:
            return AppraisalResult(
                pmid=record.pmid, lens_name=self.name, rationale="ok", qualifies=True
            )

        def render_summary(self, results: tuple[AppraisalResult, ...]) -> str:
            return f"## fake — {len(results)} record(s)\n"

    lens = _Fake()
    assert lens.name == "fake"
    assert ClaimType.CLINICAL_PERFORMANCE in lens.applicable_claim_types
    assert lens.render_summary(()) == "## fake — 0 record(s)\n"

# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for :mod:`ring2.llm` — Claude-based callers with mock client.

No real API calls — all tests inject a :class:`FakeClaudeClient` that
returns pre-baked :class:`ClaudeResponse` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ring2.adapters.mpco.appraisal.meddev_a6 import A6Category, A6Classification
from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import (
    Comparator,
    Material,
    MPCOClaim,
    Outcome,
    Property,
)
from ring2.adapters.mpco.table_mapping import CellRef
from ring2.core.adapter_base import PubMedRecord
from ring2.llm import (
    DEFAULT_MODEL,
    ClaudeA6Classifier,
    ClaudeClientProtocol,
    ClaudeResponse,
    ClaudeScreener,
)
from ring2.llm.json_response import (
    JsonExtractionError,
    extract_json_from_text,
    extract_json_from_tool_use,
)

# ---------------------------------------------------------------------------
# Fakes & fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakeClaudeClient:
    """In-memory ClaudeClient — returns pre-baked responses by call order."""

    responses: list[ClaudeResponse] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    _idx: int = 0

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ClaudeResponse:
        self.requests.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "tools": tools,
            }
        )
        if self._idx >= len(self.responses):
            raise AssertionError(f"FakeClaudeClient ran out of responses at call {self._idx}")
        out = self.responses[self._idx]
        self._idx += 1
        return out


def _resp_text(text: str, *, tool_use: dict[str, Any] | None = None) -> ClaudeResponse:
    return ClaudeResponse(
        text=text,
        tool_use=tool_use,
        stop_reason="end_turn" if tool_use is None else "tool_use",
        model=DEFAULT_MODEL,
        input_tokens=100,
        output_tokens=50,
    )


def _claim(claim_type: ClaimType = ClaimType.CLINICAL_PERFORMANCE) -> MPCOClaim:
    return MPCOClaim(
        claim_id="CB-bov-01",
        source_table_cell=CellRef(
            workbook="Comparator-Tables.xlsx",
            sheet="Bovine-Collagen",
            row=4,
            column_label="Pepsin",
        ),
        material=Material(description="Bovine-derived collagen"),
        property=Property(description="Biocompatibility"),
        comparator=Comparator(description="Porcine collagen"),
        outcome=Outcome(description="Inflammatory response"),
        applicable_regulation="722_2012",
        claim_type=claim_type,
    )


def _record(
    pmid: str = "11111111", title: str = "Test", abstract: str = "abstract"
) -> PubMedRecord:
    return PubMedRecord(pmid=pmid, title=title, abstract=abstract)


# ===========================================================================
# json_response extraction
# ===========================================================================


class TestJsonExtractionText:
    def test_whole_text_is_json(self) -> None:
        out = extract_json_from_text('{"a": 1, "b": "x"}')
        assert out == {"a": 1, "b": "x"}

    def test_wrapped_in_json_fence(self) -> None:
        out = extract_json_from_text(
            'Here is the result:\n```json\n{"outcome": "include"}\n```\nDone.'
        )
        assert out == {"outcome": "include"}

    def test_wrapped_in_generic_fence(self) -> None:
        out = extract_json_from_text('Result:\n```\n{"k": 42}\n```')
        assert out == {"k": 42}

    def test_inline_with_prose(self) -> None:
        out = extract_json_from_text(
            'Sure, here you go: {"outcome": "exclude", "code": "x"} — that is final.'
        )
        assert out == {"outcome": "exclude", "code": "x"}

    def test_empty_raises(self) -> None:
        with pytest.raises(JsonExtractionError):
            extract_json_from_text("")

    def test_no_json_raises(self) -> None:
        with pytest.raises(JsonExtractionError):
            extract_json_from_text("This is just prose without any JSON.")

    def test_nested_object_extracted(self) -> None:
        out = extract_json_from_text('{"x": {"y": 1}}')
        assert out == {"x": {"y": 1}}


class TestJsonExtractionToolUse:
    def test_non_empty_dict_returned(self) -> None:
        out = extract_json_from_tool_use({"a": 1})
        assert out == {"a": 1}

    def test_none_raises(self) -> None:
        with pytest.raises(JsonExtractionError):
            extract_json_from_tool_use(None)

    def test_empty_dict_raises(self) -> None:
        with pytest.raises(JsonExtractionError):
            extract_json_from_tool_use({})


# ===========================================================================
# ClaudeScreener
# ===========================================================================


class TestClaudeScreenerJsonMode:
    def test_include_decision_parsed(self) -> None:
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    '{"outcome": "include", "rationale": "Relevant to claim", "confidence": 0.9}'
                )
            ]
        )
        screener = ClaudeScreener(client=fake, claim=_claim())
        result = screener.assess(
            record_view={"pmid": "111", "title": "T", "abstract": "A"},
            inclusion=[{"code": "INC-001", "description": "relevant"}],
            exclusion=[{"code": "EX-IRRELEVANT", "description": "off-topic"}],
        )
        assert result["outcome"] == "include"
        assert result["confidence"] == 0.9
        assert screener.calls == ("111",)

    def test_exclude_with_code_parsed(self) -> None:
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    '{"outcome": "exclude", '
                    '"exclusion_code": "EX-IRRELEVANT", '
                    '"rationale": "off-topic", '
                    '"confidence": 0.95}'
                )
            ]
        )
        screener = ClaudeScreener(client=fake, claim=_claim())
        result = screener.assess(
            record_view={"pmid": "222", "title": "T", "abstract": "A"},
            inclusion=[],
            exclusion=[{"code": "EX-IRRELEVANT", "description": "x"}],
        )
        assert result["outcome"] == "exclude"
        assert result["exclusion_code"] == "EX-IRRELEVANT"

    def test_invalid_outcome_raises(self) -> None:
        fake = FakeClaudeClient(
            responses=[_resp_text('{"outcome": "maybe", "rationale": "?", "confidence": 0.5}')]
        )
        screener = ClaudeScreener(client=fake, claim=_claim())
        with pytest.raises(RuntimeError, match="invalid outcome"):
            screener.assess(
                record_view={"pmid": "333"},
                inclusion=[],
                exclusion=[],
            )

    def test_exclude_without_code_raises(self) -> None:
        fake = FakeClaudeClient(
            responses=[_resp_text('{"outcome": "exclude", "rationale": "?", "confidence": 0.8}')]
        )
        screener = ClaudeScreener(client=fake, claim=_claim())
        with pytest.raises(RuntimeError, match="without exclusion_code"):
            screener.assess(
                record_view={"pmid": "444"},
                inclusion=[],
                exclusion=[],
            )

    def test_malformed_response_raises(self) -> None:
        fake = FakeClaudeClient(responses=[_resp_text("I cannot respond in JSON.")])
        screener = ClaudeScreener(client=fake, claim=_claim())
        with pytest.raises(RuntimeError, match="could not parse"):
            screener.assess(
                record_view={"pmid": "555"},
                inclusion=[],
                exclusion=[],
            )

    def test_prompt_includes_claim_context(self) -> None:
        fake = FakeClaudeClient(
            responses=[_resp_text('{"outcome": "include", "rationale": "x", "confidence": 0.7}')]
        )
        screener = ClaudeScreener(client=fake, claim=_claim())
        screener.assess(
            record_view={"pmid": "111", "title": "T"},
            inclusion=[],
            exclusion=[],
        )
        prompt = fake.requests[0]["user"]
        assert "CB-bov-01" in prompt
        assert "Bovine-derived collagen" in prompt
        assert "Porcine collagen" in prompt


class TestClaudeScreenerToolMode:
    def test_tool_use_response_parsed(self) -> None:
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    "",
                    tool_use={
                        "outcome": "include",
                        "rationale": "fine",
                        "confidence": 0.85,
                    },
                )
            ]
        )
        screener = ClaudeScreener(client=fake, claim=_claim(), use_tools=True)
        result = screener.assess(
            record_view={"pmid": "111", "title": "T"},
            inclusion=[],
            exclusion=[],
        )
        assert result["outcome"] == "include"
        # Tool was passed to the client.
        assert fake.requests[0]["tools"] is not None
        assert fake.requests[0]["tools"][0]["name"] == "submit_screening_decision"

    def test_falls_back_to_text_when_tool_use_missing(self) -> None:
        # use_tools=True but Claude returns text → screener still parses.
        fake = FakeClaudeClient(
            responses=[_resp_text('{"outcome": "include", "rationale": "x", "confidence": 0.7}')]
        )
        screener = ClaudeScreener(client=fake, claim=_claim(), use_tools=True)
        result = screener.assess(
            record_view={"pmid": "111"},
            inclusion=[],
            exclusion=[],
        )
        assert result["outcome"] == "include"


# ===========================================================================
# ClaudeA6Classifier
# ===========================================================================


class TestClaudeA6ClassifierJsonMode:
    def test_no_deficiency_parsed(self) -> None:
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    '{"applicable_categories": [], '
                    '"category_findings": {}, '
                    '"rationale": "Adequate methodology"}'
                )
            ]
        )
        clf = ClaudeA6Classifier(client=fake)
        result = clf.classify(record=_record(), claim=_claim())
        assert isinstance(result, A6Classification)
        assert result.applicable_categories == frozenset()
        assert result.category_findings == {}

    def test_one_category_parsed(self) -> None:
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    '{"applicable_categories": ["b-numbers-too-small"], '
                    '"category_findings": {"b-numbers-too-small": "n=4 reported"}, '
                    '"rationale": "Small n"}'
                )
            ]
        )
        clf = ClaudeA6Classifier(client=fake)
        result = clf.classify(record=_record(), claim=_claim())
        assert A6Category.B_NUMBERS_TOO_SMALL in result.applicable_categories
        assert result.category_findings[A6Category.B_NUMBERS_TOO_SMALL] == "n=4 reported"

    def test_multiple_categories_parsed(self) -> None:
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    '{"applicable_categories": ["b-numbers-too-small", "d-lack-of-adequate-controls"], '
                    '"category_findings": {'
                    '"b-numbers-too-small": "n=3",'
                    '"d-lack-of-adequate-controls": "single-arm"'
                    "}, "
                    '"rationale": "Two deficiencies"}'
                )
            ]
        )
        clf = ClaudeA6Classifier(client=fake)
        result = clf.classify(record=_record(), claim=_claim())
        assert A6Category.B_NUMBERS_TOO_SMALL in result.applicable_categories
        assert A6Category.D_LACK_OF_ADEQUATE_CONTROLS in result.applicable_categories

    def test_unknown_category_code_raises(self) -> None:
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    '{"applicable_categories": ["h-made-up"], '
                    '"category_findings": {}, '
                    '"rationale": "x"}'
                )
            ]
        )
        clf = ClaudeA6Classifier(client=fake)
        with pytest.raises(ValueError, match="unknown §A6 category code"):
            clf.classify(record=_record(), claim=_claim())

    def test_findings_with_extra_keys_silently_dropped(self) -> None:
        # If findings contains a key not in applicable, drop it (tolerant).
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    '{"applicable_categories": ["b-numbers-too-small"], '
                    '"category_findings": {'
                    '"b-numbers-too-small": "n=3",'
                    '"d-lack-of-adequate-controls": "extra finding"'
                    "}, "
                    '"rationale": "x"}'
                )
            ]
        )
        clf = ClaudeA6Classifier(client=fake)
        result = clf.classify(record=_record(), claim=_claim())
        # Only b kept; d-finding dropped because d not in applicable.
        assert set(result.category_findings.keys()) == {A6Category.B_NUMBERS_TOO_SMALL}

    def test_malformed_response_raises(self) -> None:
        fake = FakeClaudeClient(responses=[_resp_text("Not JSON.")])
        clf = ClaudeA6Classifier(client=fake)
        with pytest.raises(ValueError, match="could not parse"):
            clf.classify(record=_record(), claim=_claim())

    def test_prompt_includes_verbatim_a6_titles(self) -> None:
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    '{"applicable_categories": [], "category_findings": {}, "rationale": "ok"}'
                )
            ]
        )
        clf = ClaudeA6Classifier(client=fake)
        clf.classify(record=_record(), claim=_claim())
        system_prompt = fake.requests[0]["system"]
        # All 7 §A6 category codes appear in the system prompt.
        for category in A6Category:
            assert category.value in system_prompt


class TestClaudeA6ClassifierToolMode:
    def test_tool_use_parsed(self) -> None:
        fake = FakeClaudeClient(
            responses=[
                _resp_text(
                    "",
                    tool_use={
                        "applicable_categories": ["b-numbers-too-small"],
                        "category_findings": {"b-numbers-too-small": "n=2"},
                        "rationale": "Tiny cohort",
                    },
                )
            ]
        )
        clf = ClaudeA6Classifier(client=fake, use_tools=True)
        result = clf.classify(record=_record(), claim=_claim())
        assert A6Category.B_NUMBERS_TOO_SMALL in result.applicable_categories
        assert fake.requests[0]["tools"][0]["name"] == "submit_a6_classification"


# ===========================================================================
# ClaudeClient — protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_fake_satisfies_protocol(self) -> None:
        fake = FakeClaudeClient()
        assert isinstance(fake, ClaudeClientProtocol)

    def test_screener_accepts_protocol_typed_client(self) -> None:
        fake = FakeClaudeClient(
            responses=[_resp_text('{"outcome": "include", "rationale": "x", "confidence": 0.8}')]
        )
        client: ClaudeClientProtocol = fake
        screener = ClaudeScreener(client=client, claim=_claim())
        result = screener.assess(record_view={"pmid": "1"}, inclusion=[], exclusion=[])
        assert result["outcome"] == "include"

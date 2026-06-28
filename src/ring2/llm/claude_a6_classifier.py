# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Claude-based MeddevA6Classifier — Stufe 1.10c.

Implements :class:`~ring2.adapters.mpco.appraisal.meddev_a6.MeddevA6Classifier`
by sending the record + MPCO claim to Claude with the §A6 catalog
verbatim in the system prompt, then parsing the structured reply.

Unlike :class:`~ring2.adapters.mpco.appraisal.rule_based_a6.RuleBasedA6Classifier`,
this classifier can flag all seven §A6 categories — it has the
reasoning capacity to detect a-lack-of-information, c-improper-
statistical-methods, e-improper-mortality-data, f-misinterpretation,
and g-illegal-activities from abstracts when the evidence is present.

Same two output modes as :class:`~ring2.llm.claude_screener.ClaudeScreener`:
JSON-via-prompt (default) or tool use.

Failure modes raise :class:`ValueError` (matching the lens' contract on
classifier errors).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ring2.adapters.mpco.appraisal.meddev_a6 import A6Category, A6Classification
from ring2.llm.claude_client import ClaudeClientProtocol
from ring2.llm.json_response import (
    JsonExtractionError,
    extract_json_from_text,
    extract_json_from_tool_use,
)
from ring2.llm.prompts import (
    A6_CLASSIFIER_SYSTEM_PROMPT,
    A6_CLASSIFIER_TOOL_SCHEMA,
    build_a6_user_prompt,
)

if TYPE_CHECKING:
    from ring2.adapters.mpco.schema import MPCOClaim
    from ring2.core.adapter_base import PubMedRecord


__all__ = ["ClaudeA6Classifier"]


# Mapping from canonical hyphenated code to enum member, for parsing
# the LLM's response.
_CODE_TO_CATEGORY = {c.value: c for c in A6Category}


@dataclass
class ClaudeA6Classifier:
    """Claude-driven MeddevA6Classifier.

    Attributes:
        client: a :class:`ClaudeClientProtocol`. Production passes a
            real :class:`ClaudeClient`; tests pass a fake.
        use_tools: tool-use mode (``True``) vs. JSON-via-prompt mode
            (``False``, default).
        temperature: sampling temperature. Default 0.0 for
            deterministic classification.
        model: optional model override.
    """

    client: ClaudeClientProtocol
    use_tools: bool = False
    temperature: float = 0.0
    model: str | None = None
    _calls: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def calls(self) -> tuple[str, ...]:
        """List of PMIDs the classifier was asked to assess (audit trail)."""
        return tuple(self._calls)

    def classify(self, *, record: PubMedRecord, claim: MPCOClaim) -> A6Classification:
        self._calls.append(record.pmid)

        user_prompt = build_a6_user_prompt(
            claim_id=claim.claim_id,
            material=claim.material.description,
            property_text=claim.property.description,
            comparator=claim.comparator.description,
            outcome=claim.outcome.description,
            record_pmid=record.pmid,
            record_title=record.title or "",
            record_abstract=record.abstract,
            require_json_only=not self.use_tools,
        )

        tools = [A6_CLASSIFIER_TOOL_SCHEMA] if self.use_tools else None

        response = self.client.complete(
            system=A6_CLASSIFIER_SYSTEM_PROMPT,
            user=user_prompt,
            model=self.model,
            temperature=self.temperature,
            tools=tools,
        )

        # Parse the response per mode, with text fallback.
        parsed = None
        if self.use_tools and response.tool_use is not None:
            try:
                parsed = extract_json_from_tool_use(response.tool_use)
            except JsonExtractionError:
                parsed = None
        if parsed is None:
            try:
                parsed = extract_json_from_text(response.text)
            except JsonExtractionError as e:
                raise ValueError(
                    f"ClaudeA6Classifier could not parse response for "
                    f"pmid={record.pmid!r}: {e}. Raw text (first 200 chars): "
                    f"{response.text[:200]!r}"
                ) from e

        # Pull and validate fields.
        raw_categories = parsed.get("applicable_categories", [])
        if not isinstance(raw_categories, list):
            raise ValueError(
                f"ClaudeA6Classifier: applicable_categories has unexpected "
                f"type {type(raw_categories).__name__} for pmid={record.pmid!r}."
            )

        applicable: set[A6Category] = set()
        for code in raw_categories:
            if not isinstance(code, str):
                raise ValueError(
                    f"ClaudeA6Classifier: non-string category in "
                    f"applicable_categories for pmid={record.pmid!r}: {code!r}."
                )
            category = _CODE_TO_CATEGORY.get(code)
            if category is None:
                raise ValueError(
                    f"ClaudeA6Classifier: unknown §A6 category code "
                    f"{code!r} for pmid={record.pmid!r}. Allowed codes: "
                    f"{sorted(_CODE_TO_CATEGORY)}."
                )
            applicable.add(category)

        raw_findings = parsed.get("category_findings", {})
        if not isinstance(raw_findings, dict):
            raise ValueError(
                f"ClaudeA6Classifier: category_findings has unexpected "
                f"type {type(raw_findings).__name__} for pmid={record.pmid!r}."
            )

        findings: dict[A6Category, str] = {}
        for code, finding in raw_findings.items():
            if not isinstance(code, str):
                continue
            category = _CODE_TO_CATEGORY.get(code)
            if category is None:
                # Silently drop findings keyed on unknown codes.
                # The lens' V1 validator would reject the result anyway
                # if we kept them.
                continue
            if not isinstance(finding, str):
                finding = str(finding)
            findings[category] = finding

        # Findings keys must be subset of applicable_categories.
        # The lens' V1 validator enforces this; we drop excess entries
        # rather than raising, to be tolerant of minor LLM inconsistencies.
        findings = {k: v for k, v in findings.items() if k in applicable}

        rationale = parsed.get("rationale", "")
        if not isinstance(rationale, str):
            rationale = str(rationale)
        if not rationale.strip():
            rationale = (
                f"Claude-driven §A6 classification: "
                f"{sorted(c.value for c in applicable) or 'no deficiency'}"
            )

        return A6Classification(
            applicable_categories=frozenset(applicable),
            category_findings=findings,
            rationale=rationale,
        )

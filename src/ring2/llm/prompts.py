# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Prompt templates for the Claude-based callers.

Two prompt families:

* :mod:`screener` — for :class:`~ring2.llm.claude_screener.ClaudeScreener`.
  Frames Claude as a systematic-literature-review screener; asks for
  one structured decision per record.
* :mod:`a6_classifier` — for
  :class:`~ring2.llm.claude_a6_classifier.ClaudeA6Classifier`. Frames
  Claude as a MEDDEV 2.7/1 Rev. 4 §A6 expert; asks for a 7-category
  applicability classification.

Both prompt families:

* Quote the regulatory / methodological text verbatim where one
  exists (per the project-wide verbatim convention — never paraphrase
  controlling text).
* Specify a strict JSON output schema in the user prompt for the
  JSON-via-prompt mode.
* Expose a tool-use schema (JSON-Schema dict) for the tool-use mode.

Tool-use schemas use the same field names as the JSON-via-prompt
schemas so the downstream parser is mode-agnostic.
"""

from __future__ import annotations

from typing import Any

from ring2.adapters.mpco.appraisal.meddev_a6 import A6_CATEGORY_TITLES, A6Category

__all__ = [
    "A6_CLASSIFIER_SYSTEM_PROMPT",
    "A6_CLASSIFIER_TOOL_SCHEMA",
    "SCREENER_SYSTEM_PROMPT",
    "SCREENER_TOOL_SCHEMA",
    "build_a6_user_prompt",
    "build_screener_user_prompt",
]


# ===========================================================================
# Screener prompts
# ===========================================================================


SCREENER_SYSTEM_PROMPT = """\
You are a careful systematic-literature-review screener for a medical-device \
regulatory submission. Your task is to decide, per record, whether to \
include or exclude it on the basis of title and abstract only, against \
explicit inclusion and exclusion criteria.

Conventions:

* Decide with high specificity. When in doubt, set outcome to \
"requires_review" rather than guessing — false positives downstream are \
more expensive than borderline cases flagged for review.
* Respect the exclusion-code system: if you exclude, pick exactly one of \
the listed exclusion codes that best fits, and quote the matching \
language from the abstract in the rationale where possible.
* Keep rationales short (one or two sentences) and factual. Do not \
editorialise about the study quality — quality appraisal happens in a \
separate downstream step (MEDDEV 2.7/1 Rev. 4 §A6).
"""


SCREENER_TOOL_SCHEMA: dict[str, Any] = {
    "name": "submit_screening_decision",
    "description": ("Submit one screening decision for one bibliographic record."),
    "input_schema": {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["include", "exclude", "requires_review"],
                "description": "Screening outcome.",
            },
            "exclusion_code": {
                "type": "string",
                "description": (
                    "One of the project's exclusion codes; required when "
                    "outcome is 'exclude', omit otherwise."
                ),
            },
            "rationale": {
                "type": "string",
                "description": (
                    "Short factual justification (1-2 sentences). "
                    "Quote the matched abstract language where possible."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Calibrated confidence in the decision.",
            },
        },
        "required": ["outcome", "rationale", "confidence"],
    },
}


def build_screener_user_prompt(
    *,
    claim_id: str,
    material: str,
    property_text: str,
    comparator: str,
    outcome: str,
    inclusion_criteria: list[dict[str, str]],
    exclusion_criteria: list[dict[str, str]],
    record_view: dict[str, Any],
    require_json_only: bool = True,
) -> str:
    """Build the user prompt for one screening decision.

    Args:
        claim_id: stable claim id for context framing.
        material, property_text, comparator, outcome: MPCO-claim
            descriptions (M, P, C, O).
        inclusion_criteria: list of ``{code, description}`` dicts.
        exclusion_criteria: list of ``{code, description}`` dicts.
        record_view: the record view as produced by
            :func:`ring2.core.screening._record_view`.
        require_json_only: if ``True`` (the JSON-via-prompt mode), the
            prompt ends with an instruction to respond with JSON only.
            Set to ``False`` for the tool-use mode where the
            instruction is implicit in the tool schema.
    """
    inc_lines = "\n".join(
        f"  - `{c.get('code', '?')}` — {c.get('description', '')}" for c in inclusion_criteria
    )
    exc_lines = "\n".join(
        f"  - `{c.get('code', '?')}` — {c.get('description', '')}" for c in exclusion_criteria
    )

    record_lines = [
        f"  PMID: {record_view.get('pmid', '?')}",
        f"  Title: {record_view.get('title', '?')}",
    ]
    if record_view.get("journal"):
        record_lines.append(f"  Journal: {record_view['journal']}")
    if record_view.get("year"):
        record_lines.append(f"  Year: {record_view['year']}")
    abstract = record_view.get("abstract")
    if abstract:
        record_lines.append(f"  Abstract:\n    {abstract}")
    else:
        record_lines.append("  Abstract: (not available — title-only pass)")

    prompt_parts = [
        f"CLAIM: {claim_id}",
        "",
        f"  Material:    {material}",
        f"  Property:    {property_text}",
        f"  Comparator:  {comparator}",
        f"  Outcome:     {outcome}",
        "",
        "INCLUSION CRITERIA:",
        inc_lines or "  (none — every record is excluded unless it matches positively)",
        "",
        "EXCLUSION CRITERIA:",
        exc_lines or "  (none)",
        "",
        "RECORD:",
        "\n".join(record_lines),
    ]

    if require_json_only:
        prompt_parts.extend(
            [
                "",
                "Respond with a single JSON object and nothing else. Schema:",
                "",
                "  {",
                '    "outcome": "include" | "exclude" | "requires_review",',
                '    "exclusion_code": "<one of the codes above>" '
                '(required iff outcome is "exclude"),',
                '    "rationale": "<short factual justification>",',
                '    "confidence": <number in [0.0, 1.0]>',
                "  }",
            ]
        )

    return "\n".join(prompt_parts)


# ===========================================================================
# §A6 classifier prompts
# ===========================================================================


def _a6_categories_block() -> str:
    """Build the seven-category verbatim catalog block for the §A6 prompt.

    Reproduces the §A6 titles verbatim from
    :data:`ring2.adapters.mpco.appraisal.meddev_a6.A6_CATEGORY_TITLES`.
    Never paraphrase — these are normative MEDDEV titles.
    """
    lines = []
    for category in A6Category:
        title = A6_CATEGORY_TITLES[category]
        lines.append(f"  `{category.value}` — {title}")
    return "\n".join(lines)


A6_CLASSIFIER_SYSTEM_PROMPT = f"""\
You are a regulatory-affairs expert with deep familiarity with MEDDEV \
2.7/1 Rev. 4, in particular Appendix §A6 — *"Appraisal of clinical data \
— examples of studies that lack scientific validity for demonstration \
of adequate clinical performance and/or clinical safety"*.

The seven §A6 categories (verbatim titles, reproduced from MEDDEV \
2.7/1 Rev. 4):

{_a6_categories_block()}

Your task, per record:

* Read the title and abstract of one bibliographic record in the \
context of one MPCO claim.
* Decide which of the seven §A6 categories *apply* to the record. \
Multiple categories may apply; an empty set means "no §A6 deficiency \
detected — the record qualifies as supporting evidence under §A6".
* For each applicable category, quote the matching abstract language \
verbatim in the per-category finding (do not paraphrase the abstract).
* Be conservative: only flag a category when you have specific \
evidence from the abstract. Speculation or absence of information is \
not sufficient — abstracts are short and routinely omit relevant \
detail.
* Never paraphrase the §A6 category titles in your output; reference \
them by their canonical hyphenated code (e.g. `a-lack-of-information`).
"""


A6_CLASSIFIER_TOOL_SCHEMA: dict[str, Any] = {
    "name": "submit_a6_classification",
    "description": (
        "Submit one MEDDEV 2.7/1 Rev. 4 §A6 classification for one bibliographic record."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "applicable_categories": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [c.value for c in A6Category],
                },
                "description": (
                    "List of §A6 category codes that apply to this "
                    "record. Empty list means no deficiency detected."
                ),
            },
            "category_findings": {
                "type": "object",
                "description": (
                    "Per-applicable-category finding. Keys must be a "
                    "subset of applicable_categories. Values quote the "
                    "matching abstract language verbatim."
                ),
                "additionalProperties": {"type": "string"},
            },
            "rationale": {
                "type": "string",
                "description": (
                    "High-level summary (1-3 sentences) of the verdict. "
                    "Names the applicable categories and notes the "
                    "overall qualifies/non-qualifies conclusion."
                ),
            },
        },
        "required": ["applicable_categories", "category_findings", "rationale"],
    },
}


def build_a6_user_prompt(
    *,
    claim_id: str,
    material: str,
    property_text: str,
    comparator: str,
    outcome: str,
    record_pmid: str,
    record_title: str,
    record_abstract: str | None,
    require_json_only: bool = True,
) -> str:
    """Build the user prompt for one §A6 classification."""
    abstract_text = record_abstract or "(not available — classification based on title alone)"

    prompt_parts = [
        f"CLAIM: {claim_id}",
        "",
        f"  Material:    {material}",
        f"  Property:    {property_text}",
        f"  Comparator:  {comparator}",
        f"  Outcome:     {outcome}",
        "",
        "RECORD:",
        f"  PMID: {record_pmid}",
        f"  Title: {record_title}",
        "  Abstract:",
        f"    {abstract_text}",
    ]

    if require_json_only:
        prompt_parts.extend(
            [
                "",
                "Respond with a single JSON object and nothing else. Schema:",
                "",
                "  {",
                '    "applicable_categories": ["<code1>", "<code2>", ...],   '
                "// empty list = no deficiency",
                '    "category_findings": {"<code1>": "<verbatim abstract quote>", ...},',
                '    "rationale": "<1-3 sentence summary>"',
                "  }",
                "",
                "Use only the canonical category codes listed in the system "
                "prompt (e.g. `b-numbers-too-small`).",
            ]
        )

    return "\n".join(prompt_parts)

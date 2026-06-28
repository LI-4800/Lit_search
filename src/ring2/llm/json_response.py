# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Robust JSON extraction from Claude responses.

Two extraction modes:

* :func:`extract_json_from_tool_use` — preferred when the call used
  tools. The SDK already parsed the JSON; this function only handles
  the ``tool_use is None`` fallback.
* :func:`extract_json_from_text` — used for the JSON-via-prompt path.
  Claude may wrap JSON in markdown code fences, in extra prose, or
  return raw JSON. This function tries, in order:

    1. parse the whole text as JSON,
    2. unwrap a ``\\`\\`\\`json … \\`\\`\\``` fence and parse the inside,
    3. unwrap any ``\\`\\`\\` … \\`\\`\\``` fence and parse the inside,
    4. find the first balanced ``{…}`` substring and parse it.

  If none succeeds, :exc:`JsonExtractionError` is raised with the raw
  text quoted for audit / debugging.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "JsonExtractionError",
    "extract_json_from_text",
    "extract_json_from_tool_use",
]


class JsonExtractionError(ValueError):
    """Raised when no valid JSON object can be extracted from text."""


_CODE_FENCE_JSON = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)
_CODE_FENCE_ANY = re.compile(r"```\s*(.+?)\s*```", re.DOTALL)


def extract_json_from_tool_use(tool_use: dict[str, Any] | None) -> dict[str, Any]:
    """Return ``tool_use`` if it is a non-empty dict, else raise.

    The Anthropic SDK already deserialises tool_use inputs to Python
    dicts; this function just enforces the non-empty invariant.
    """
    if not tool_use:
        raise JsonExtractionError(
            "Response carried no tool_use block — Claude likely returned "
            "free text instead of using the provided tool."
        )
    if not isinstance(tool_use, dict):
        raise JsonExtractionError(
            f"tool_use payload has unexpected type {type(tool_use).__name__}; expected dict."
        )
    return tool_use


def extract_json_from_text(text: str) -> dict[str, Any]:
    """Best-effort JSON object extraction from Claude's text output.

    Tries multiple strategies in order; returns the first parseable
    JSON object found. Raises :class:`JsonExtractionError` if none
    succeed.
    """
    stripped = text.strip()
    if not stripped:
        raise JsonExtractionError("Response text is empty.")

    # Strategy 1: whole-text parse.
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    # Strategy 2: ```json … ``` fence.
    m = _CODE_FENCE_JSON.search(stripped)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Strategy 3: any ``` … ``` fence.
    m = _CODE_FENCE_ANY.search(stripped)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Strategy 4: first balanced {…} substring.
    snippet = _find_first_balanced_braces(stripped)
    if snippet is not None:
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise JsonExtractionError(
        f"No JSON object could be extracted from response. First 200 chars: {stripped[:200]!r}"
    )


def _find_first_balanced_braces(s: str) -> str | None:
    """Return the first balanced ``{…}`` substring, or ``None`` if none.

    Naive scanner — does not understand string literals or escapes, so
    a ``{`` inside a quoted string would confuse it. Adequate for the
    Claude-output-with-prose-around-JSON case which is the only
    situation this function is asked to handle.
    """
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None

# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""LLM integration — Claude-based caller implementations.

Two production callers live here, each implementing a Protocol from
elsewhere in the codebase:

* :class:`ClaudeScreener` — :class:`~ring2.core.screening.ScreenerCaller`
  via Claude. Replaces the demo-grade keyword screener used in tests.
* :class:`ClaudeA6Classifier` — :class:`~ring2.adapters.mpco.appraisal.meddev_a6.MeddevA6Classifier`
  via Claude. Replaces the
  :class:`~ring2.adapters.mpco.appraisal.rule_based_a6.RuleBasedA6Classifier`
  for runs that warrant LLM-driven appraisal.

Both depend on :class:`ClaudeClient` — a thin wrapper around the
Anthropic SDK that adds project-wide defaults (model, max_tokens) and
supports two structured-output modes:

* **JSON-via-prompt** (default): the prompt asks for JSON output, the
  client parses + validates. Simple, testable, slightly less reliable
  on the LLM end.
* **Tool use**: the call passes a JSON-Schema tool definition; Claude
  returns a tool_use block with already-parsed JSON. More robust at
  the cost of slightly more complex code paths.

API key resolution follows the SDK default: ``ANTHROPIC_API_KEY`` env
var. The CLI auto-detects the key and selects LLM callers when set,
falling back to the null callers otherwise.
"""

from __future__ import annotations

from ring2.llm.claude_a6_classifier import ClaudeA6Classifier
from ring2.llm.claude_client import (
    DEFAULT_MODEL,
    ClaudeClient,
    ClaudeClientProtocol,
    ClaudeResponse,
)
from ring2.llm.claude_screener import ClaudeScreener

__all__ = [
    "DEFAULT_MODEL",
    "ClaudeA6Classifier",
    "ClaudeClient",
    "ClaudeClientProtocol",
    "ClaudeResponse",
    "ClaudeScreener",
]

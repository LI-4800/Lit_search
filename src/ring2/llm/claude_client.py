# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Claude API client — thin wrapper over the Anthropic SDK.

Provides:

* :data:`DEFAULT_MODEL` — Stufe-1.10 default (``claude-opus-4-7``).
* :class:`ClaudeResponse` — typed response shape (text + optional
  tool-use blocks) shared by JSON-prompt and tool-use call paths.
* :class:`ClaudeClientProtocol` — the structural type that callers
  (:class:`~ring2.llm.claude_screener.ClaudeScreener`,
  :class:`~ring2.llm.claude_a6_classifier.ClaudeA6Classifier`) depend
  on. Tests inject a fake that satisfies this Protocol; production code
  uses :class:`ClaudeClient`.
* :class:`ClaudeClient` — Production wrapper around
  ``anthropic.Anthropic``. Reads ``ANTHROPIC_API_KEY`` from the
  environment by SDK default; can also be passed explicitly. Other
  parameters (model, max_tokens, temperature) have project defaults but
  are overridable per call.

The client deliberately stays thin — no retries, no caching, no
rate-limit logic. The Anthropic SDK already retries transient errors.
If a richer wrapper is needed later it goes here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from anthropic import Anthropic


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


#: The default Claude model used by RING2 LLM callers. Chosen for the
#: combination of methodological strength (good at multi-step
#: reasoning under regulatory text constraints) and acceptable cost
#: for record-level appraisal. Callers may override per call.
DEFAULT_MODEL: str = "claude-opus-4-7"


#: Default ``max_tokens`` ceiling. Screening / §A6 classification
#: responses are small structured JSON objects; 1024 leaves ample
#: headroom without risking runaway costs.
DEFAULT_MAX_TOKENS: int = 1024


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaudeResponse:
    """One Claude API response, normalised for both call paths.

    Attributes:
        text: concatenated text from all ``text`` blocks in
            ``response.content``. Empty string when the response is
            purely a tool_use block.
        tool_use: parsed JSON input from the first ``tool_use`` block,
            or ``None`` if the response did not include one. The shape
            is whatever the tool's input_schema specifies.
        stop_reason: ``"end_turn"``, ``"tool_use"``, ``"max_tokens"``,
            etc. Useful for distinguishing a normal completion from a
            truncated one.
        model: the model that produced the response (echoed for audit).
        input_tokens: prompt tokens consumed.
        output_tokens: completion tokens generated.
    """

    text: str
    tool_use: dict[str, Any] | None
    stop_reason: str
    model: str
    input_tokens: int
    output_tokens: int


# ---------------------------------------------------------------------------
# Protocol — what callers depend on
# ---------------------------------------------------------------------------


@runtime_checkable
class ClaudeClientProtocol(Protocol):
    """Structural type for a Claude API client.

    :class:`ClaudeClient` is the production implementation; tests
    inject a fake that satisfies this Protocol.
    """

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
        """Send one prompt to Claude and return the normalised response.

        Args:
            system: system prompt (role/context framing).
            user: single user turn — the actual question / task.
            model: model name. Defaults to :data:`DEFAULT_MODEL`.
            max_tokens: response token ceiling. Defaults to
                :data:`DEFAULT_MAX_TOKENS`.
            temperature: sampling temperature. ``None`` lets the SDK
                use the model's default (usually 1.0); RING2 callers
                pass low values (e.g. 0.0) for deterministic
                classification.
            tools: optional tool-use schemas. When provided, Claude may
                return a ``tool_use`` block instead of free text.
        """
        ...


# ---------------------------------------------------------------------------
# Production implementation
# ---------------------------------------------------------------------------


@dataclass
class ClaudeClient:
    """Production Claude client — thin Anthropic SDK wrapper.

    Constructor parameters:
        api_key: optional explicit API key. ``None`` lets the SDK pick
            it up from ``ANTHROPIC_API_KEY``.
        default_model: model used when ``complete(model=...)`` is
            ``None``.
        default_max_tokens: max_tokens used when ``complete()`` does
            not override.

    The SDK client is created lazily on first use so that import-time
    construction does not require an API key.
    """

    api_key: str | None = None
    default_model: str = DEFAULT_MODEL
    default_max_tokens: int = DEFAULT_MAX_TOKENS
    _sdk: Anthropic | None = field(default=None, init=False, repr=False)

    def _get_sdk(self) -> Anthropic:
        if self._sdk is None:
            from anthropic import Anthropic as _Anthropic

            if self.api_key is not None:
                self._sdk = _Anthropic(api_key=self.api_key)
            else:
                # SDK reads ANTHROPIC_API_KEY automatically.
                self._sdk = _Anthropic()
        return self._sdk

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
        sdk = self._get_sdk()
        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "max_tokens": max_tokens or self.default_max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if tools:
            kwargs["tools"] = tools

        msg = sdk.messages.create(**kwargs)

        # Normalise: aggregate all text blocks, find the first tool_use.
        text_parts: list[str] = []
        tool_use_input: dict[str, Any] | None = None
        for block in msg.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", ""))
            elif block_type == "tool_use" and tool_use_input is None:
                tool_input = getattr(block, "input", None)
                if isinstance(tool_input, dict):
                    tool_use_input = tool_input

        usage = msg.usage
        return ClaudeResponse(
            text="".join(text_parts),
            tool_use=tool_use_input,
            stop_reason=getattr(msg, "stop_reason", "") or "",
            model=getattr(msg, "model", kwargs["model"]),
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        )


def api_key_available() -> bool:
    """``True`` iff ``ANTHROPIC_API_KEY`` is set in the environment.

    The CLI uses this to auto-detect whether to wire LLM callers or
    fall back to the null callers.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Claude-based ScreenerCaller — Stufe 1.10c.

Implements :class:`~ring2.core.screening.ScreenerCaller` by sending the
record + criteria to Claude and parsing the structured reply.

Two output modes (matching the project-wide decision "Beides verfügbar
machen"):

* ``use_tools=False`` (default): JSON-via-prompt. Cheaper because the
  prompt is shorter (no tool schema overhead); slightly less reliable
  on the LLM end. Adequate for routine screening.
* ``use_tools=True``: tool use. The SDK passes the JSON-Schema tool
  definition; Claude returns a tool_use block with pre-parsed JSON.
  Use for high-stakes or noisy domains where prompt adherence is
  shaky.

The screener wires the claim into the prompt so Claude has full MPCO
context for each decision. The :class:`MPCOClaim` is taken from the
constructor at orchestrator setup time — the
:class:`~ring2.core.screening.ScreenerCaller` Protocol's
``assess(record_view, inclusion, exclusion)`` signature does not carry
the claim, so we inject it at construction.

Failure modes (raised as ``RuntimeError`` to match the Null-caller
contract on the calling side):

* API call fails (network, auth, rate limit) — propagates from SDK.
* Response is non-JSON or malformed JSON — raises with context.
* Returned ``outcome`` is not one of the three accepted strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ring2.llm.claude_client import ClaudeClientProtocol
from ring2.llm.json_response import (
    JsonExtractionError,
    extract_json_from_text,
    extract_json_from_tool_use,
)
from ring2.llm.prompts import (
    SCREENER_SYSTEM_PROMPT,
    SCREENER_TOOL_SCHEMA,
    build_screener_user_prompt,
)

if TYPE_CHECKING:
    from ring2.adapters.mpco.schema import MPCOClaim


__all__ = ["ClaudeScreener"]


# Allowed values returned in the "outcome" field — matches the screening
# module's parser expectations.
_ALLOWED_OUTCOMES = frozenset({"include", "exclude", "requires_review"})


@dataclass
class ClaudeScreener:
    """Claude-driven ScreenerCaller.

    Attributes:
        client: a :class:`ClaudeClientProtocol`. Production callers
            pass a real :class:`ClaudeClient`; tests pass a fake.
        claim: the :class:`MPCOClaim` to wire into every prompt. The
            ScreenerCaller Protocol's ``assess()`` does not pass the
            claim per call, so it must be supplied at construction.
        use_tools: select tool-use mode (``True``) vs.
            JSON-via-prompt mode (``False``, default).
        temperature: sampling temperature. Default 0.0 for
            deterministic classification.
        model: optional model override. ``None`` lets the client use
            its default (claude-opus-4-7).
    """

    client: ClaudeClientProtocol
    claim: MPCOClaim
    use_tools: bool = False
    temperature: float = 0.0
    model: str | None = None
    _calls: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def calls(self) -> tuple[str, ...]:
        """List of PMIDs the screener was asked to assess (audit trail)."""
        return tuple(self._calls)

    def assess(
        self,
        *,
        record_view: dict[str, Any],
        inclusion: list[dict[str, str]],
        exclusion: list[dict[str, str]],
    ) -> dict[str, Any]:
        pmid = str(record_view.get("pmid", "?"))
        self._calls.append(pmid)

        user_prompt = build_screener_user_prompt(
            claim_id=self.claim.claim_id,
            material=self.claim.material.description,
            property_text=self.claim.property.description,
            comparator=self.claim.comparator.description,
            outcome=self.claim.outcome.description,
            inclusion_criteria=inclusion,
            exclusion_criteria=exclusion,
            record_view=record_view,
            require_json_only=not self.use_tools,
        )

        tools = [SCREENER_TOOL_SCHEMA] if self.use_tools else None

        response = self.client.complete(
            system=SCREENER_SYSTEM_PROMPT,
            user=user_prompt,
            model=self.model,
            temperature=self.temperature,
            tools=tools,
        )

        # Parse the response per mode, but with a generous fallback —
        # if tool_use was requested but Claude returned text anyway,
        # try the text path before giving up.
        parsed: dict[str, Any] | None = None
        if self.use_tools and response.tool_use is not None:
            try:
                parsed = extract_json_from_tool_use(response.tool_use)
            except JsonExtractionError:
                parsed = None
        if parsed is None:
            try:
                parsed = extract_json_from_text(response.text)
            except JsonExtractionError as e:
                raise RuntimeError(
                    f"ClaudeScreener could not parse Claude response for "
                    f"pmid={pmid!r}: {e}. Raw text (first 200 chars): "
                    f"{response.text[:200]!r}"
                ) from e

        # Validate outcome value.
        outcome_value = parsed.get("outcome")
        if outcome_value not in _ALLOWED_OUTCOMES:
            raise RuntimeError(
                f"ClaudeScreener got invalid outcome {outcome_value!r} for "
                f"pmid={pmid!r}; expected one of {sorted(_ALLOWED_OUTCOMES)}."
            )

        # Validate exclusion_code presence consistent with outcome.
        if outcome_value == "exclude" and not parsed.get("exclusion_code"):
            raise RuntimeError(
                f"ClaudeScreener got outcome=exclude without exclusion_code for pmid={pmid!r}."
            )

        return parsed

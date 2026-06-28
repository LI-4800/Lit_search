# Copyright 2026 lets-innovate.ch (Michael Hug)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Rule-based :class:`MeddevA6Classifier` — Stufe 1.9b.

Abstract-only heuristic implementation of the :class:`MeddevA6Classifier`
Protocol. Two of the seven §A6 categories can be approximated from
title/abstract alone with reasonable signal:

* **b** *Numbers too small for statistical significance* — regex on
  explicit ``n=<int>``/``n<10`` patterns, plus title cues
  (``"case report"`` → likely n=1).
* **d** *Lack of adequate controls* — keyword cues
  (``"single-arm"``, ``"uncontrolled"``, ``"no control group"``,
  ``"case series"``, ``"case report"``).

The remaining five categories (**a**, **c**, **e**, **f**, **g**) are
**not** flagged by this classifier — they require full-text inspection
or domain reasoning beyond keyword matching. A future LLM-driven
classifier (Stufe 1.10+) will cover those.

Behavioural contract:

* If neither b nor d is triggered, the classifier returns an empty
  :class:`A6Classification` — the record qualifies under this lens'
  threshold.
* If b or d (or both) trigger, the classifier returns a non-empty
  classification; the lens will flip ``qualifies`` to ``False``.
* The rationale string names the triggered categories and quotes the
  matched evidence verbatim, so the §8 report can be audited.

This classifier is **demonstration-grade**: it is honest about being
heuristic, it does not pretend to detect the harder categories, and it
fails on the side of *qualifying* records (i.e. it has high specificity
but low sensitivity — it misses real deficiencies more often than it
fabricates them).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ring2.adapters.mpco.appraisal.meddev_a6 import (
    A6_CATEGORY_TITLES,
    A6Category,
    A6Classification,
)

if TYPE_CHECKING:
    from ring2.adapters.mpco.schema import MPCOClaim
    from ring2.core.adapter_base import PubMedRecord

__all__ = ["RuleBasedA6Classifier"]


# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------


# Numeric ``n=<int>`` / ``n = <int>`` / ``n<int>`` / ``n=<int>+/-…``.
# The captured int is the sample size.
_N_EQUALS_PATTERN = re.compile(
    r"\bn\s*=\s*(\d{1,4})\b",
    re.IGNORECASE,
)

# Threshold below which a sample size is considered "too small". Chosen
# conservatively (10) — the same cutoff used in several systematic-review
# methodologies (e.g. small-study filters). Below 10 → flag b; ≥ 10 → do
# not flag (this classifier intentionally has high specificity).
_SMALL_N_THRESHOLD = 10

# Title-level cues for very small studies.
_VERY_SMALL_TITLE_CUES = (
    "case report",
    "single case",
    "a case of",
)

# Cues for studies lacking adequate controls.
_UNCONTROLLED_CUES = (
    "single-arm",
    "single arm",
    "uncontrolled",
    "no control group",
    "no control arm",
    "without controls",
    "case series",
    "case report",
)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleBasedA6Classifier:
    """Heuristic, abstract-only :class:`MeddevA6Classifier`.

    Constructor takes no arguments — the heuristic is parameterless. A
    future variant may take a small-n threshold or custom cue lists,
    but defaults are conservative and sensible for the demo path.

    Implements :class:`MeddevA6Classifier` structurally.
    """

    # No state — methods read patterns from module-level constants. The
    # frozen empty dataclass makes the instance hashable and trivially
    # picklable for parallel runs.

    def classify(self, *, record: PubMedRecord, claim: MPCOClaim) -> A6Classification:
        """Classify ``record`` against §A6 categories b and d via heuristics."""
        title = (record.title or "").strip()
        abstract = (record.abstract or "").strip()
        # claim is currently unused by the rule-based heuristic — the
        # MPCOClaim context only matters for LLM-driven classifiers
        # later. Keeping the parameter satisfies the Protocol.
        _ = claim

        flagged: dict[A6Category, str] = {}

        # ---- Category b: numbers too small -----------------------------
        small_n_evidence = _detect_small_n(title, abstract)
        if small_n_evidence is not None:
            flagged[A6Category.B_NUMBERS_TOO_SMALL] = small_n_evidence

        # ---- Category d: lack of adequate controls ---------------------
        uncontrolled_evidence = _detect_uncontrolled(title, abstract)
        if uncontrolled_evidence is not None:
            flagged[A6Category.D_LACK_OF_ADEQUATE_CONTROLS] = uncontrolled_evidence

        if not flagged:
            return A6Classification(
                applicable_categories=frozenset(),
                category_findings={},
                rationale=(
                    "Rule-based heuristic detected no §A6 deficiency from "
                    "title/abstract. Categories a, c, e, f, g are not "
                    "evaluated by this classifier and require full-text "
                    "review or LLM-driven assessment (Stufe 1.10+)."
                ),
            )

        applicable = frozenset(flagged.keys())
        category_names = sorted(c.value for c in applicable)
        # Build rationale that names the categories verbatim from the §A6
        # title table — never paraphrase regulatory text.
        rationale_lines = [
            "Rule-based heuristic flagged the following §A6 category(ies) "
            f"from title/abstract: {category_names}.",
        ]
        for category in sorted(applicable, key=lambda c: c.value):
            title_text = A6_CATEGORY_TITLES[category]
            rationale_lines.append(
                f"  - `{category.value}` — {title_text}: matched '{flagged[category]}'"
            )

        return A6Classification(
            applicable_categories=applicable,
            category_findings=flagged,
            rationale="\n".join(rationale_lines),
        )


# ---------------------------------------------------------------------------
# Helpers (module-level so they're cheap to import & easy to unit-test)
# ---------------------------------------------------------------------------


def _detect_small_n(title: str, abstract: str) -> str | None:
    """Return a verbatim evidence snippet for §A6 category b, or ``None``.

    Detection order:

    1. ``n=<int>`` pattern in title or abstract → if the smallest found
       value is below :data:`_SMALL_N_THRESHOLD`, return that match
       verbatim.
    2. Title-level very-small-study cues (``"case report"`` etc.) →
       return the cue.

    Returns the literal matched substring (lower-cased context preserved
    where possible) so the §8 renderer / audit trail shows exactly what
    the rule fired on.
    """
    title_lc = title.lower()

    # 1. Numeric n= matches
    candidates: list[tuple[int, str]] = []
    for pattern_text in (title, abstract):
        for m in _N_EQUALS_PATTERN.finditer(pattern_text):
            try:
                value = int(m.group(1))
            except ValueError:
                continue
            candidates.append((value, m.group(0)))
    if candidates:
        smallest = min(candidates, key=lambda t: t[0])
        if smallest[0] < _SMALL_N_THRESHOLD:
            return f"{smallest[1]} (below n={_SMALL_N_THRESHOLD} threshold)"

    # 2. Title cues for very small studies
    for cue in _VERY_SMALL_TITLE_CUES:
        if cue in title_lc:
            return f"title contains '{cue}'"
    # Abstract-level case-report cues are intentionally not used here —
    # too many false positives in fields citing prior case reports.

    return None


def _detect_uncontrolled(title: str, abstract: str) -> str | None:
    """Return a verbatim evidence snippet for §A6 category d, or ``None``.

    Matches the cues in :data:`_UNCONTROLLED_CUES` against title and
    abstract (case-insensitive). Returns the first matched cue with a
    short context label.
    """
    title_lc = title.lower()
    abstract_lc = abstract.lower()

    for cue in _UNCONTROLLED_CUES:
        if cue in title_lc:
            return f"title contains '{cue}'"
        if cue in abstract_lc:
            return f"abstract contains '{cue}'"

    return None

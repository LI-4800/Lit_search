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
"""MEDDEV 2.7/1 Rev. 4 §A6 appraisal lens — Stufe 1.8 Inkrement 7.

§A6 of MEDDEV 2.7/1 Rev. 4 — *"Appraisal of clinical data — examples
of studies that lack scientific validity for demonstration of adequate
clinical performance and/or clinical safety"* — enumerates seven
categories (a-g) of methodological deficiency. A record falling under
one or more §A6 categories **does not qualify** as supporting evidence
under this lens.

The seven categories are reproduced verbatim from MEDDEV 2.7/1 Rev. 4
Appendix A6 in :data:`A6_CATEGORY_TITLES`. Per the project's verbatim-
language convention, these titles must not be paraphrased.

Architecture (consistent with :class:`MPCOAdapter`'s ``ScreenerCaller``
pattern):
    The actual classification of a record against §A6 categories is
    methodology-heavy and (in production) LLM-driven. To keep the lens
    testable without an LLM and to defer the classifier implementation
    to a later increment, the lens accepts a
    :class:`MeddevA6Classifier` via constructor injection. The default
    is :class:`NullA6Classifier` which raises loudly on use — never
    silently producing a placeholder verdict.

Threshold:
    The lens treats *no §A6 categories applicable* as the "qualifies"
    state. The moment one §A6 category attaches, the record is
    excluded from the supporting-evidence body.

Applicable claim types:
    ``CLINICAL_PERFORMANCE``, ``SAFETY_ALLERGENICITY``. MEDDEV §A6 is
    targeted at clinical evidence; bio/material-property and historic-
    market-use claims fall under different lenses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Protocol

from pydantic import ConfigDict, model_validator

from ring2.adapters.mpco.appraisal.base import AppraisalLens, AppraisalResult
from ring2.adapters.mpco.appraisal.registry import register_lens
from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import MPCOClaim
from ring2.core.adapter_base import PubMedRecord

__all__ = [
    "A6_CATEGORY_TITLES",
    "A6Category",
    "A6Classification",
    "MeddevA6Classifier",
    "MeddevA6Lens",
    "MeddevA6Result",
    "NullA6Classifier",
]


# ---------------------------------------------------------------------------
# Enum + verbatim titles
# ---------------------------------------------------------------------------


class A6Category(StrEnum):
    """The seven §A6 categories (a-g) of MEDDEV 2.7/1 Rev. 4.

    Values are hyphen-canonical, prefixed with the lower-case letter for
    declaration-order preservation (StrEnum iteration follows declaration
    order). Names use the upper-case letter for Python-identifier
    friendliness in matches and lookups.
    """

    A_LACK_OF_INFORMATION = "a-lack-of-information"
    B_NUMBERS_TOO_SMALL = "b-numbers-too-small"
    C_IMPROPER_STATISTICAL_METHODS = "c-improper-statistical-methods"
    D_LACK_OF_ADEQUATE_CONTROLS = "d-lack-of-adequate-controls"
    E_IMPROPER_MORTALITY_DATA = "e-improper-mortality-data"
    F_MISINTERPRETATION = "f-misinterpretation"
    G_ILLEGAL_ACTIVITIES = "g-illegal-activities"


#: Verbatim §A6 category titles from MEDDEV 2.7/1 Rev. 4 Appendix A6.
#: These strings MUST NOT be paraphrased — they are reproduced exactly
#: as printed in the regulation and used directly in the audit trail
#: and §8 report sections.
A6_CATEGORY_TITLES: Mapping[A6Category, str] = MappingProxyType(
    {
        A6Category.A_LACK_OF_INFORMATION: "Lack of information on elementary aspects",
        A6Category.B_NUMBERS_TOO_SMALL: "Numbers too small for statistical significance",
        A6Category.C_IMPROPER_STATISTICAL_METHODS: "Improper statistical methods",
        A6Category.D_LACK_OF_ADEQUATE_CONTROLS: "Lack of adequate controls",
        A6Category.E_IMPROPER_MORTALITY_DATA: (
            "Improper collection of mortality and serious adverse events data"
        ),
        A6Category.F_MISINTERPRETATION: "Misinterpretation by the authors",
        A6Category.G_ILLEGAL_ACTIVITIES: "Illegal activities",
    }
)


# ---------------------------------------------------------------------------
# Classifier protocol + null default
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class A6Classification:
    """Raw classifier output for one (record, claim) pair under §A6.

    The classifier flags which §A6 categories apply and provides a
    verbatim finding string per applicable category, plus a high-level
    rationale summarising the verdict. The lens then wraps this into
    a :class:`MeddevA6Result`.

    Invariant: every key in :attr:`category_findings` must appear in
    :attr:`applicable_categories` (enforced by the lens, not here, so
    the classifier protocol stays simple).

    Attributes:
        applicable_categories: frozenset of §A6 categories that apply
            to the record. Empty means "no §A6 deficiency detected —
            the record qualifies as supporting evidence".
        category_findings: per-applicable-category verbatim finding
            text. Keys must be a subset of ``applicable_categories``.
        rationale: high-level (≤ ~3 sentence) summary of the verdict,
            naming the applicable categories. Verbatim regulatory text
            where one applies; otherwise the classifier's own
            explanation.
    """

    applicable_categories: frozenset[A6Category]
    category_findings: Mapping[A6Category, str]
    rationale: str


class MeddevA6Classifier(Protocol):
    """Protocol for a §A6 classifier.

    Implementations may be rule-based, LLM-driven, or hybrid. The lens
    receives one via constructor injection; the default in production
    is :class:`NullA6Classifier` which raises on use.
    """

    def classify(self, *, record: PubMedRecord, claim: MPCOClaim) -> A6Classification:
        """Classify ``record`` against §A6 categories for ``claim``."""
        ...


@dataclass(frozen=True, slots=True)
class NullA6Classifier:
    """No-op classifier that raises on call.

    Installed by :class:`MeddevA6Lens` when no classifier is supplied,
    so that calling :meth:`MeddevA6Lens.appraise` without first
    injecting a real classifier fails loudly rather than silently
    returning a placeholder verdict. The error message is configurable
    for context-rich diagnostics.
    """

    error_message: str = (
        "MeddevA6Lens constructed without MeddevA6Classifier; inject one before calling appraise()."
    )

    def classify(
        self,
        *,
        record: PubMedRecord,
        claim: MPCOClaim,
    ) -> A6Classification:
        raise ValueError(self.error_message)


# ---------------------------------------------------------------------------
# Result subclass
# ---------------------------------------------------------------------------


class MeddevA6Result(AppraisalResult):
    """:class:`AppraisalResult` extended with §A6-specific findings.

    Attributes:
        applicable_categories: §A6 categories that apply to this
            record. Empty → :attr:`qualifies` is ``True``.
        category_findings: per-category verbatim finding text. Every
            key MUST appear in :attr:`applicable_categories`
            (validator V1).

    Cross-validators:
        * V1: ``category_findings`` keys are a subset of
          ``applicable_categories`` — prevents drift between the
          flag set and the explanation set.
        * V2: ``qualifies`` is ``True`` if and only if
          ``applicable_categories`` is empty — enforces the lens's
          threshold definition.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    applicable_categories: frozenset[A6Category]
    category_findings: dict[A6Category, str]

    @model_validator(mode="after")
    def _findings_keys_subset_of_categories(self) -> MeddevA6Result:
        """V1: every category in `category_findings` must be flagged applicable."""
        stray = set(self.category_findings) - self.applicable_categories
        if stray:
            stray_repr = sorted(s.value for s in stray)
            raise ValueError(
                f"MeddevA6Result.category_findings has keys "
                f"{stray_repr} not in applicable_categories"
            )
        return self

    @model_validator(mode="after")
    def _qualifies_iff_no_categories(self) -> MeddevA6Result:
        """V2: qualifies ⇔ applicable_categories is empty.

        §A6 lists deficiencies. A record qualifies as supporting
        evidence iff none of the seven categories applies.
        """
        should_qualify = len(self.applicable_categories) == 0
        if self.qualifies != should_qualify:
            raise ValueError(
                f"MeddevA6Result: qualifies={self.qualifies} contradicts "
                f"applicable_categories={sorted(c.value for c in self.applicable_categories)}; "
                f"per §A6 lens threshold, qualifies must equal "
                f"({len(self.applicable_categories)} == 0)"
            )
        return self


# ---------------------------------------------------------------------------
# The lens
# ---------------------------------------------------------------------------


@register_lens
class MeddevA6Lens(AppraisalLens):
    """MEDDEV 2.7/1 Rev. 4 §A6 appraisal lens.

    Construction:

        >>> lens = MeddevA6Lens()                       # registry round-trip OK
        >>> lens = MeddevA6Lens(classifier=real_clf)    # production usage

    The default no-arg constructor installs :class:`NullA6Classifier`,
    so calling :meth:`appraise` without first injecting a real
    classifier raises ``ValueError`` rather than returning a silent
    placeholder verdict.
    """

    name: ClassVar[str] = "meddev_a6"
    applicable_claim_types: ClassVar[frozenset[ClaimType]] = frozenset(
        {ClaimType.CLINICAL_PERFORMANCE, ClaimType.SAFETY_ALLERGENICITY}
    )

    def __init__(self, classifier: MeddevA6Classifier | None = None) -> None:
        """Construct the §A6 lens.

        Args:
            classifier: a :class:`MeddevA6Classifier` implementation.
                If ``None``, a :class:`NullA6Classifier` is installed
                that raises on first :meth:`appraise` call.
        """
        self._classifier: MeddevA6Classifier = classifier or NullA6Classifier()

    def appraise(self, record: PubMedRecord, claim: MPCOClaim) -> MeddevA6Result:
        """Appraise one record against §A6 for one claim.

        Delegates classification to the injected classifier, then
        wraps the outcome into a :class:`MeddevA6Result`. The result's
        :attr:`qualifies` flag is computed by the lens's threshold:
        ``True`` iff the classifier reports no applicable §A6
        category.

        Raises:
            ValueError: if the claim's ``claim_type`` is not in the
                lens's :attr:`applicable_claim_types` — wrong tool for
                the job.
            ValidationError: if the classifier output is internally
                inconsistent (e.g. a finding for a category that
                wasn't flagged applicable).
        """
        if claim.claim_type not in self.applicable_claim_types:
            applicable_repr = sorted(ct.value for ct in self.applicable_claim_types)
            raise ValueError(
                f"MeddevA6Lens does not apply to claim_type={claim.claim_type.value!r}; "
                f"applicable claim types: {applicable_repr}"
            )

        outcome = self._classifier.classify(record=record, claim=claim)

        return MeddevA6Result(
            pmid=record.pmid,
            lens_name=self.name,
            rationale=outcome.rationale,
            qualifies=(len(outcome.applicable_categories) == 0),
            applicable_categories=outcome.applicable_categories,
            category_findings=dict(outcome.category_findings),
        )

    def render_summary(self, results: tuple[AppraisalResult, ...]) -> str:
        """Render a markdown §8 sub-section summarising §A6 appraisal outcomes.

        Output structure:
            * headline tally (total appraised, qualifying, non-qualifying)
            * per-category breakdown — for each of the seven §A6
              categories, the count of records that fell under it
              (categories iterate in declaration order a → g)
            * list of qualifying PMIDs (sorted)
            * list of non-qualifying PMIDs with the applicable
              categories (sorted by PMID)

        Implementations needing lens-specific aggregation cast results
        to :class:`MeddevA6Result`; this method does that internally
        and raises if a result of a different subclass is passed in.
        """
        # Narrow to MeddevA6Result. Be strict — mixed-lens results
        # should never reach this summary method.
        typed: list[MeddevA6Result] = []
        for r in results:
            if not isinstance(r, MeddevA6Result):
                raise TypeError(
                    f"MeddevA6Lens.render_summary received {type(r).__name__}; "
                    f"expected MeddevA6Result"
                )
            typed.append(r)

        lines: list[str] = []
        lines.append("### Lens: MEDDEV 2.7/1 Rev. 4 §A6")
        lines.append("")
        total = len(typed)
        qualifying = sum(1 for r in typed if r.qualifies)
        non_qualifying = total - qualifying
        lines.append(f"- Records appraised: {total}")
        lines.append(f"- Qualifying (no §A6 deficiency): {qualifying}")
        lines.append(f"- Non-qualifying (≥ 1 §A6 deficiency): {non_qualifying}")
        lines.append("")

        # Per-category breakdown, declaration order a → g.
        lines.append("**Category coverage:**")
        lines.append("")
        for category in A6Category:
            count = sum(1 for r in typed if category in r.applicable_categories)
            title = A6_CATEGORY_TITLES[category]
            lines.append(f"- `{category.value}` — {title}: {count} record(s)")
        lines.append("")

        # Qualifying PMIDs.
        qualifying_pmids = sorted(r.pmid for r in typed if r.qualifies)
        lines.append("**Qualifying records:**")
        lines.append("")
        if qualifying_pmids:
            for pmid in qualifying_pmids:
                lines.append(f"- `{pmid}`")
        else:
            lines.append("_None._")
        lines.append("")

        # Non-qualifying PMIDs with their applicable categories.
        non_q = sorted(
            (r for r in typed if not r.qualifies),
            key=lambda r: r.pmid,
        )
        lines.append("**Non-qualifying records:**")
        lines.append("")
        if non_q:
            for r in non_q:
                cats = sorted(c.value for c in r.applicable_categories)
                lines.append(f"- `{r.pmid}` — categories: {cats}")
        else:
            lines.append("_None._")
        lines.append("")

        return "\n".join(lines)

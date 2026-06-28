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
"""Tests for :mod:`ring2.core.project_config` (Pydantic schema only — no I/O)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.adapters.mpco.schema import (
    Comparator,
    Material,
    MPCOClaim,
    Outcome,
    Property,
)
from ring2.adapters.mpco.table_mapping import CellRef
from ring2.core.project_config import (
    AppraisalConfig,
    AppraisalLensSelection,
    ProjectConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cell_ref() -> CellRef:
    return CellRef(
        workbook="Comparator-Tables.xlsx",
        sheet="Bovine-Collagen",
        row=4,
        column_label="Pepsin",
    )


def _inline_claim() -> MPCOClaim:
    return MPCOClaim(
        claim_id="CB-bov-01",
        source_table_cell=_cell_ref(),
        material=Material(description="Bovine-derived collagen"),
        property=Property(description="Biocompatibility"),
        comparator=Comparator(description="Porcine collagen"),
        outcome=Outcome(description="Inflammatory response"),
        applicable_regulation="722_2012",
    )


def _appraisal_default() -> AppraisalConfig:
    return AppraisalConfig(
        biochemistry_material_property=AppraisalLensSelection(lens="glp_oecd"),
        safety_allergenicity=AppraisalLensSelection(lens="care_caseseries"),
        clinical_performance=AppraisalLensSelection(lens="meddev_a6"),
        historical_market_use=AppraisalLensSelection(lens="registry_authoritativeness"),
    )


# ---------------------------------------------------------------------------
# AppraisalLensSelection
# ---------------------------------------------------------------------------


class TestAppraisalLensSelection:
    def test_accepts_registered_lens(self) -> None:
        sel = AppraisalLensSelection(lens="meddev_a6")
        assert sel.lens == "meddev_a6"

    def test_accepts_each_registered_lens(self) -> None:
        # Every registered lens must validate; this guards against
        # registry/schema drift.
        from ring2.adapters.mpco.appraisal import names

        for lens_name in names():
            sel = AppraisalLensSelection(lens=lens_name)
            assert sel.lens == lens_name

    def test_rejects_unknown_lens(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AppraisalLensSelection(lens="nonexistent_lens")
        msg = str(exc_info.value)
        assert "nonexistent_lens" in msg
        assert "not registered" in msg

    def test_rejects_empty_lens_name(self) -> None:
        with pytest.raises(ValidationError):
            AppraisalLensSelection(lens="")

    def test_strips_whitespace(self) -> None:
        # str_strip_whitespace is set on the config.
        sel = AppraisalLensSelection(lens="  meddev_a6  ")
        assert sel.lens == "meddev_a6"

    def test_frozen(self) -> None:
        sel = AppraisalLensSelection(lens="meddev_a6")
        with pytest.raises(ValidationError):
            sel.lens = "glp_oecd"  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            AppraisalLensSelection(lens="meddev_a6", extra_field="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# AppraisalConfig
# ---------------------------------------------------------------------------


class TestAppraisalConfig:
    def test_builds_with_all_four_claim_types(self) -> None:
        cfg = _appraisal_default()
        assert cfg.clinical_performance.lens == "meddev_a6"

    def test_all_four_fields_required(self) -> None:
        with pytest.raises(ValidationError):
            AppraisalConfig(  # type: ignore[call-arg]
                biochemistry_material_property=AppraisalLensSelection(lens="glp_oecd"),
                safety_allergenicity=AppraisalLensSelection(lens="care_caseseries"),
                # clinical_performance omitted
                historical_market_use=AppraisalLensSelection(lens="registry_authoritativeness"),
            )

    def test_lens_for_biochemistry_material_property(self) -> None:
        cfg = _appraisal_default()
        assert cfg.lens_for(ClaimType.BIOCHEMISTRY_MATERIAL_PROPERTY) == "glp_oecd"

    def test_lens_for_safety_allergenicity(self) -> None:
        cfg = _appraisal_default()
        assert cfg.lens_for(ClaimType.SAFETY_ALLERGENICITY) == "care_caseseries"

    def test_lens_for_clinical_performance(self) -> None:
        cfg = _appraisal_default()
        assert cfg.lens_for(ClaimType.CLINICAL_PERFORMANCE) == "meddev_a6"

    def test_lens_for_historical_market_use(self) -> None:
        cfg = _appraisal_default()
        assert cfg.lens_for(ClaimType.HISTORICAL_MARKET_USE) == "registry_authoritativeness"

    def test_lens_for_regulatory_compliance_raises(self) -> None:
        cfg = _appraisal_default()
        with pytest.raises(KeyError) as exc_info:
            cfg.lens_for(ClaimType.REGULATORY_COMPLIANCE)
        assert "reference resolution" in str(exc_info.value)
        assert "1.10" in str(exc_info.value)

    def test_lens_for_unknown_raises(self) -> None:
        cfg = _appraisal_default()
        with pytest.raises(KeyError) as exc_info:
            cfg.lens_for(ClaimType.UNKNOWN)
        assert "UNKNOWN" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ProjectConfig — claim source XOR
# ---------------------------------------------------------------------------


class TestProjectConfigClaimSource:
    def test_inline_claim_ok(self) -> None:
        cfg = ProjectConfig(
            name="CB-bov-01",
            claim=_inline_claim(),
            appraisal=_appraisal_default(),
            output_dir=Path("reports/cb-bov-01"),
        )
        assert cfg.claim is not None
        assert cfg.claim.claim_id == "CB-bov-01"
        assert cfg.claim_file is None

    def test_claim_file_ok(self) -> None:
        cfg = ProjectConfig(
            name="CB-bov-01",
            claim_file=Path("claims/cb-bov-01.yaml"),
            appraisal=_appraisal_default(),
            output_dir=Path("reports/cb-bov-01"),
        )
        assert cfg.claim is None
        assert cfg.claim_file == Path("claims/cb-bov-01.yaml")

    def test_both_set_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ProjectConfig(
                name="CB-bov-01",
                claim=_inline_claim(),
                claim_file=Path("claims/cb-bov-01.yaml"),
                appraisal=_appraisal_default(),
                output_dir=Path("reports/cb-bov-01"),
            )
        assert "mutually exclusive" in str(exc_info.value)

    def test_neither_set_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ProjectConfig(
                name="CB-bov-01",
                appraisal=_appraisal_default(),
                output_dir=Path("reports/cb-bov-01"),
            )
        assert "one of" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ProjectConfig — general schema behaviour
# ---------------------------------------------------------------------------


class TestProjectConfigSchema:
    def test_name_required_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            ProjectConfig(
                name="",
                claim_file=Path("claims/x.yaml"),
                appraisal=_appraisal_default(),
                output_dir=Path("out"),
            )

    def test_appraisal_required(self) -> None:
        with pytest.raises(ValidationError):
            ProjectConfig(  # type: ignore[call-arg]
                name="x",
                claim_file=Path("claims/x.yaml"),
                output_dir=Path("out"),
            )

    def test_output_dir_required(self) -> None:
        with pytest.raises(ValidationError):
            ProjectConfig(  # type: ignore[call-arg]
                name="x",
                claim_file=Path("claims/x.yaml"),
                appraisal=_appraisal_default(),
            )

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            ProjectConfig(
                name="x",
                claim_file=Path("claims/x.yaml"),
                appraisal=_appraisal_default(),
                output_dir=Path("out"),
                unexpected="boom",  # type: ignore[call-arg]
            )

    def test_frozen(self) -> None:
        cfg = ProjectConfig(
            name="x",
            claim_file=Path("claims/x.yaml"),
            appraisal=_appraisal_default(),
            output_dir=Path("out"),
        )
        with pytest.raises(ValidationError):
            cfg.name = "y"  # type: ignore[misc]

    def test_model_validate_from_dict(self) -> None:
        cfg = ProjectConfig.model_validate(
            {
                "name": "CB-bov-01",
                "claim_file": "claims/cb-bov-01.yaml",
                "appraisal": {
                    "biochemistry_material_property": {"lens": "glp_oecd"},
                    "safety_allergenicity": {"lens": "care_caseseries"},
                    "clinical_performance": {"lens": "meddev_a6"},
                    "historical_market_use": {"lens": "registry_authoritativeness"},
                },
                "output_dir": "reports/cb-bov-01",
            }
        )
        assert cfg.name == "CB-bov-01"
        assert cfg.claim_file == Path("claims/cb-bov-01.yaml")
        assert cfg.appraisal.lens_for(ClaimType.CLINICAL_PERFORMANCE) == "meddev_a6"


# ---------------------------------------------------------------------------
# SearchConfig — Inkrement 4
# ---------------------------------------------------------------------------


class TestSearchConfig:
    def test_minimal_construction(self) -> None:
        from ring2.core.project_config import SearchConfig

        cfg = SearchConfig(query="collagen[Title/Abstract]")
        assert cfg.query == "collagen[Title/Abstract]"
        assert cfg.batch_size == 10
        assert cfg.max_batches is None

    def test_full_construction(self) -> None:
        from ring2.core.project_config import SearchConfig

        cfg = SearchConfig(query="q", batch_size=25, max_batches=5)
        assert cfg.batch_size == 25
        assert cfg.max_batches == 5

    def test_empty_query_rejected(self) -> None:
        from ring2.core.project_config import SearchConfig

        with pytest.raises(ValidationError):
            SearchConfig(query="")

    def test_batch_size_must_be_positive(self) -> None:
        from ring2.core.project_config import SearchConfig

        with pytest.raises(ValidationError):
            SearchConfig(query="q", batch_size=0)

    def test_max_batches_must_be_positive_if_set(self) -> None:
        from ring2.core.project_config import SearchConfig

        with pytest.raises(ValidationError):
            SearchConfig(query="q", max_batches=0)

    def test_frozen(self) -> None:
        from ring2.core.project_config import SearchConfig

        cfg = SearchConfig(query="q")
        with pytest.raises(ValidationError):
            cfg.query = "other"  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        from ring2.core.project_config import SearchConfig

        with pytest.raises(ValidationError):
            SearchConfig(query="q", unexpected="boom")  # type: ignore[call-arg]


class TestProjectConfigSearchField:
    def test_search_defaults_to_none(self) -> None:
        cfg = ProjectConfig(
            name="x",
            claim_file=Path("c.yaml"),
            appraisal=_appraisal_default(),
            output_dir=Path("out"),
        )
        assert cfg.search is None

    def test_search_accepts_search_config(self) -> None:
        from ring2.core.project_config import SearchConfig

        cfg = ProjectConfig(
            name="x",
            claim_file=Path("c.yaml"),
            appraisal=_appraisal_default(),
            output_dir=Path("out"),
            search=SearchConfig(query="q"),
        )
        assert cfg.search is not None
        assert cfg.search.query == "q"

    def test_search_from_dict(self) -> None:
        cfg = ProjectConfig.model_validate(
            {
                "name": "x",
                "claim_file": "c.yaml",
                "appraisal": {
                    "biochemistry_material_property": {"lens": "glp_oecd"},
                    "safety_allergenicity": {"lens": "care_caseseries"},
                    "clinical_performance": {"lens": "meddev_a6"},
                    "historical_market_use": {"lens": "registry_authoritativeness"},
                },
                "output_dir": "out",
                "search": {
                    "query": "collagen AND bovine",
                    "batch_size": 25,
                    "max_batches": 3,
                },
            }
        )
        assert cfg.search is not None
        assert cfg.search.query == "collagen AND bovine"
        assert cfg.search.batch_size == 25
        assert cfg.search.max_batches == 3

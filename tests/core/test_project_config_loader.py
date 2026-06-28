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
"""Tests for :mod:`ring2.core.project_config_loader` — YAML I/O."""

from __future__ import annotations

from pathlib import Path

import pytest

from ring2.adapters.mpco.claim_type_classifier import ClaimType
from ring2.core.project_config import ProjectConfig
from ring2.core.project_config_loader import (
    ProjectConfigLoaderError,
    load_project_config,
    resolve_claim,
)

# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------


_PROJECT_YAML_FILE_REF = """\
name: CB-bov-01
claim_file: claims/cb-bov-01.yaml
output_dir: reports/cb-bov-01
appraisal:
  biochemistry_material_property:
    lens: glp_oecd
  safety_allergenicity:
    lens: care_caseseries
  clinical_performance:
    lens: meddev_a6
  historical_market_use:
    lens: registry_authoritativeness
"""


_PROJECT_YAML_INLINE = """\
name: CB-bov-01
output_dir: reports/cb-bov-01
claim:
  claim_id: CB-bov-01
  source_table_cell:
    workbook: Comparator-Tables.xlsx
    sheet: Bovine-Collagen
    row: 4
    column_label: Pepsin
  material:
    description: Bovine-derived collagen
  property:
    description: Biocompatibility
  comparator:
    description: Porcine collagen
  outcome:
    description: Inflammatory response
  applicable_regulation: "722_2012"
appraisal:
  biochemistry_material_property:
    lens: glp_oecd
  safety_allergenicity:
    lens: care_caseseries
  clinical_performance:
    lens: meddev_a6
  historical_market_use:
    lens: registry_authoritativeness
"""


_CLAIM_YAML = """\
claim_id: CB-bov-01
source_table_cell:
  workbook: Comparator-Tables.xlsx
  sheet: Bovine-Collagen
  row: 4
  column_label: Pepsin
material:
  description: Bovine-derived collagen
property:
  description: Biocompatibility
comparator:
  description: Porcine collagen
outcome:
  description: Inflammatory response
applicable_regulation: "722_2012"
"""


# ---------------------------------------------------------------------------
# load_project_config
# ---------------------------------------------------------------------------


class TestLoadProjectConfig:
    def test_loads_file_ref_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "project.yaml"
        p.write_text(_PROJECT_YAML_FILE_REF, encoding="utf-8")

        cfg = load_project_config(p)

        assert isinstance(cfg, ProjectConfig)
        assert cfg.name == "CB-bov-01"
        assert cfg.claim is None
        assert cfg.claim_file == Path("claims/cb-bov-01.yaml")
        assert cfg.appraisal.lens_for(ClaimType.CLINICAL_PERFORMANCE) == "meddev_a6"

    def test_loads_inline_claim_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "project.yaml"
        p.write_text(_PROJECT_YAML_INLINE, encoding="utf-8")

        cfg = load_project_config(p)

        assert cfg.claim is not None
        assert cfg.claim.claim_id == "CB-bov-01"
        assert cfg.claim_file is None

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_project_config(tmp_path / "does-not-exist.yaml")

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ProjectConfigLoaderError) as exc_info:
            load_project_config(p)
        assert "empty" in str(exc_info.value)

    def test_top_level_not_mapping_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "list.yaml"
        p.write_text("- foo\n- bar\n", encoding="utf-8")
        with pytest.raises(ProjectConfigLoaderError) as exc_info:
            load_project_config(p)
        assert "mapping" in str(exc_info.value)

    def test_invalid_lens_name_raises(self, tmp_path: Path) -> None:
        bad = _PROJECT_YAML_FILE_REF.replace("lens: meddev_a6", "lens: bogus_lens")
        p = tmp_path / "project.yaml"
        p.write_text(bad, encoding="utf-8")
        # Pydantic raises ValidationError for invalid lens names.
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            load_project_config(p)
        assert "bogus_lens" in str(exc_info.value)


# ---------------------------------------------------------------------------
# resolve_claim
# ---------------------------------------------------------------------------


class TestResolveClaim:
    def test_returns_inline_claim_directly(self, tmp_path: Path) -> None:
        p = tmp_path / "project.yaml"
        p.write_text(_PROJECT_YAML_INLINE, encoding="utf-8")
        cfg = load_project_config(p)

        claim = resolve_claim(cfg, base_dir=tmp_path)
        assert claim.claim_id == "CB-bov-01"

    def test_loads_relative_claim_file(self, tmp_path: Path) -> None:
        (tmp_path / "claims").mkdir()
        (tmp_path / "claims" / "cb-bov-01.yaml").write_text(_CLAIM_YAML, encoding="utf-8")

        project_path = tmp_path / "project.yaml"
        project_path.write_text(_PROJECT_YAML_FILE_REF, encoding="utf-8")

        cfg = load_project_config(project_path)
        claim = resolve_claim(cfg, base_dir=tmp_path)

        assert claim.claim_id == "CB-bov-01"
        assert claim.material.description == "Bovine-derived collagen"

    def test_loads_absolute_claim_file(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere" / "the-claim.yaml"
        elsewhere.parent.mkdir()
        elsewhere.write_text(_CLAIM_YAML, encoding="utf-8")

        # Build a project YAML with an absolute claim_file path.
        project_yaml = _PROJECT_YAML_FILE_REF.replace(
            "claim_file: claims/cb-bov-01.yaml",
            f"claim_file: {elsewhere}",
        )
        project_path = tmp_path / "project.yaml"
        project_path.write_text(project_yaml, encoding="utf-8")

        cfg = load_project_config(project_path)
        # base_dir is irrelevant for absolute paths.
        claim = resolve_claim(cfg, base_dir=tmp_path / "nonexistent")

        assert claim.claim_id == "CB-bov-01"

    def test_missing_claim_file_raises(self, tmp_path: Path) -> None:
        project_path = tmp_path / "project.yaml"
        project_path.write_text(_PROJECT_YAML_FILE_REF, encoding="utf-8")
        cfg = load_project_config(project_path)

        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_claim(cfg, base_dir=tmp_path)
        assert "claim_file" in str(exc_info.value)

    def test_empty_claim_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "claims").mkdir()
        (tmp_path / "claims" / "cb-bov-01.yaml").write_text("", encoding="utf-8")

        project_path = tmp_path / "project.yaml"
        project_path.write_text(_PROJECT_YAML_FILE_REF, encoding="utf-8")
        cfg = load_project_config(project_path)

        with pytest.raises(ProjectConfigLoaderError) as exc_info:
            resolve_claim(cfg, base_dir=tmp_path)
        assert "empty" in str(exc_info.value)

    def test_non_mapping_claim_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "claims").mkdir()
        (tmp_path / "claims" / "cb-bov-01.yaml").write_text("- foo\n- bar\n", encoding="utf-8")

        project_path = tmp_path / "project.yaml"
        project_path.write_text(_PROJECT_YAML_FILE_REF, encoding="utf-8")
        cfg = load_project_config(project_path)

        with pytest.raises(ProjectConfigLoaderError) as exc_info:
            resolve_claim(cfg, base_dir=tmp_path)
        assert "mapping" in str(exc_info.value)

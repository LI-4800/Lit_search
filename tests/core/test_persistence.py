# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.core.persistence."""

import json
from pathlib import Path

import pytest

from ring2.core.persistence import (
    PersistenceError,
    batch_filename,
    find_batches,
    load,
    save,
    save_batch,
)

# ---------------------------------------------------------------------------
# Format detection and dispatch
# ---------------------------------------------------------------------------


def test_save_yaml_by_extension(tmp_path: Path) -> None:
    p = tmp_path / "x.yaml"
    save(p, {"a": 1, "b": [1, 2, 3]})
    assert p.exists()
    assert load(p) == {"a": 1, "b": [1, 2, 3]}


def test_save_yml_extension_also_works(tmp_path: Path) -> None:
    p = tmp_path / "x.yml"
    save(p, {"key": "value"})
    text = p.read_text()
    assert "key: value" in text


def test_save_json_by_extension(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    save(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}


def test_explicit_format_overrides_extension(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    save(p, {"a": 1}, format="json")
    assert json.loads(p.read_text()) == {"a": 1}


def test_default_format_is_yaml_for_unknown_extension(tmp_path: Path) -> None:
    p = tmp_path / "out"
    save(p, {"key": "value"})
    assert "key: value" in p.read_text()


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "nested" / "dir" / "file.yaml"
    save(p, {"a": 1})
    assert p.exists()


# ---------------------------------------------------------------------------
# Comment preservation (the reason we use ruamel.yaml not PyYAML)
# ---------------------------------------------------------------------------


def test_yaml_roundtrip_preserves_comments(tmp_path: Path) -> None:
    """A YAML file with comments survives load → save unchanged.

    This is critical for audit-trail YAMLs that use comments to carry
    UNKLAR markers, deviation IDs, and regulatory annotations.
    """
    src = tmp_path / "src.yaml"
    src.write_text(
        "# Top-level audit annotation: DEV-722-001\n"
        "claim_id: CB-bov-01  # inline: bovine collagen claim\n"
        "items:\n"
        "  - first\n"
        "  - second  # UNKLAR-X\n"
    )
    data = load(src)

    dst = tmp_path / "dst.yaml"
    save(dst, data)

    out = dst.read_text()
    assert "# Top-level audit annotation: DEV-722-001" in out
    assert "# inline: bovine collagen claim" in out
    assert "# UNKLAR-X" in out


def test_yaml_preserves_unicode(tmp_path: Path) -> None:
    """Regulatory text is multilingual (DE + EN at minimum)."""
    p = tmp_path / "x.yaml"
    save(p, {"de": "Schädigung", "fr": "évaluation"})
    text = p.read_text()
    assert "Schädigung" in text
    assert "évaluation" in text


def test_json_preserves_unicode(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    save(p, {"de": "Schädigung"})
    text = p.read_text()
    assert "Schädigung" in text  # ensure_ascii=False


# ---------------------------------------------------------------------------
# Dual-format read (legacy JSON state)
# ---------------------------------------------------------------------------


def test_load_yaml(tmp_path: Path) -> None:
    p = tmp_path / "a.yaml"
    p.write_text("foo: bar\n")
    assert load(p) == {"foo": "bar"}


def test_load_json(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    p.write_text('{"foo": "bar"}')
    assert load(p) == {"foo": "bar"}


def test_load_unknown_extension_raises(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("foo")
    with pytest.raises(PersistenceError):
        load(p)


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# Batch naming
# ---------------------------------------------------------------------------


def test_batch_filename_default_yaml() -> None:
    assert batch_filename("CB-bov-01", 0) == "search_CB-bov-01_batch_00.yaml"
    assert batch_filename("CB-bov-01", 7) == "search_CB-bov-01_batch_07.yaml"
    assert batch_filename("CB-bov-01", 42) == "search_CB-bov-01_batch_42.yaml"


def test_batch_filename_json() -> None:
    assert batch_filename("X", 3, format="json") == "search_X_batch_03.json"


def test_batch_filename_negative_raises() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        batch_filename("X", -1)


def test_batch_filename_empty_claim_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        batch_filename("", 0)


def test_batch_filename_whitespace_claim_raises() -> None:
    with pytest.raises(ValueError, match="whitespace-free"):
        batch_filename("has space", 0)


# ---------------------------------------------------------------------------
# Batch save + find
# ---------------------------------------------------------------------------


def test_save_batch_writes_canonical_path(tmp_path: Path) -> None:
    p = save_batch(tmp_path, "CB-bov-01", 0, [{"pmid": "12345"}])
    assert p.exists()
    assert p.name == "search_CB-bov-01_batch_00.yaml"
    assert load(p) == [{"pmid": "12345"}]


def test_save_batch_creates_session_dir(tmp_path: Path) -> None:
    target = tmp_path / "sessions" / "proj-X"
    save_batch(target, "CB-bov-01", 0, [])
    assert (target / "search_CB-bov-01_batch_00.yaml").exists()


def test_find_batches_returns_sorted_mixed_formats(tmp_path: Path) -> None:
    save_batch(tmp_path, "X", 0, [{"a": 1}], format="yaml")
    save_batch(tmp_path, "X", 1, [{"a": 2}], format="json")
    save_batch(tmp_path, "X", 2, [{"a": 3}], format="yaml")
    # Unrelated noise: different claim, same convention
    (tmp_path / "search_Y_batch_00.yaml").write_text("[]\n")

    found = find_batches(tmp_path, "X")
    assert [p.name for p in found] == [
        "search_X_batch_00.yaml",
        "search_X_batch_01.json",
        "search_X_batch_02.yaml",
    ]


def test_find_batches_empty_dir(tmp_path: Path) -> None:
    assert find_batches(tmp_path, "nonexistent") == []


def test_find_batches_nonexistent_dir(tmp_path: Path) -> None:
    assert find_batches(tmp_path / "missing", "X") == []

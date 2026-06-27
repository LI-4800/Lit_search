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
"""Persistence — YAML-default write, dual-format read.

YAML is the preferred format because it:

* preserves comments (critical for audit-trail annotations such as
  ``# UNKLAR-XX`` and ``# DEV-NNN``),
* supports multi-line verbatim regulatory text without escaping,
* diffs cleanly under version control.

JSON read support exists for legacy compatibility — the active OsteoGen
CER session persists state via ``json.dumps`` and must remain readable
during the refactor.

This module uses ``ruamel.yaml`` (not PyYAML) because PyYAML strips
comments on round-trip. Audit-trail YAMLs require comment preservation.
"""

import json
from io import StringIO
from pathlib import Path
from typing import Any, Final, Literal

from ruamel.yaml import YAML

PersistenceFormat = Literal["yaml", "json"]


class PersistenceError(Exception):
    """Raised on persistence errors (unknown format, malformed file)."""


# Module-level YAML instance configured for round-trip preservation.
_yaml: Final[YAML] = YAML(typ="rt")
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.preserve_quotes = True
_yaml.width = 4096  # avoid wrapping long lines (regulatory verbatim quotes)
_yaml.allow_unicode = True


_YAML_SUFFIXES: Final[frozenset[str]] = frozenset({".yaml", ".yml"})
_JSON_SUFFIXES: Final[frozenset[str]] = frozenset({".json"})


def _detect_format(path: Path) -> PersistenceFormat:
    """Map file extension to format. Raises PersistenceError on unknown suffix."""
    suffix = path.suffix.lower()
    if suffix in _YAML_SUFFIXES:
        return "yaml"
    if suffix in _JSON_SUFFIXES:
        return "json"
    raise PersistenceError(
        f"Cannot detect format from suffix {suffix!r} for path {path!s}. "
        f"Supported suffixes: {sorted(_YAML_SUFFIXES | _JSON_SUFFIXES)}"
    )


def load(path: Path) -> Any:
    """Load YAML or JSON from disk, format detected by extension.

    Args:
        path: file to load.

    Returns:
        The parsed structure (round-trip YAML for ``.yaml/.yml``,
        plain Python objects for ``.json``).

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        PersistenceError: if the file extension is not recognised.
        ValueError / json.JSONDecodeError / ruamel.yaml.YAMLError: on parse error.
    """
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")
    fmt = _detect_format(path)
    text = path.read_text(encoding="utf-8")
    if fmt == "yaml":
        return _yaml.load(text)
    return json.loads(text)


def save(path: Path, data: Any, *, format: PersistenceFormat | None = None) -> Path:
    """Save ``data`` to ``path``.

    Format resolution priority:

        1. Explicit ``format`` argument.
        2. File extension (``.yaml``/``.yml`` → yaml, ``.json`` → json).
        3. Default: YAML.

    The parent directory is created if it does not exist.

    YAML output uses ``ruamel.yaml`` round-trip serialisation, so data
    loaded with :func:`load` and saved again preserves comments and key
    ordering. JSON output uses ``ensure_ascii=False`` (regulatory text is
    multilingual).

    Args:
        path: destination file.
        data: serialisable Python structure.
        format: optional explicit format override.

    Returns:
        The written path (for chaining / logging).
    """
    resolved_format: PersistenceFormat
    if format is not None:
        resolved_format = format
    else:
        suffix = path.suffix.lower()
        if suffix in _YAML_SUFFIXES:
            resolved_format = "yaml"
        elif suffix in _JSON_SUFFIXES:
            resolved_format = "json"
        else:
            resolved_format = "yaml"

    path.parent.mkdir(parents=True, exist_ok=True)

    if resolved_format == "yaml":
        buf = StringIO()
        _yaml.dump(data, buf)
        path.write_text(buf.getvalue(), encoding="utf-8")
    else:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    return path


# ---------------------------------------------------------------------------
# Batch persistence — canonical naming for per-claim search batches
# ---------------------------------------------------------------------------
#
# Per architecture v1 §4 and prompt v3 §Stage 3.2, search results are
# persisted in batches of 10 records, file-named:
#
#     search_<claim_id>_batch_<NN>.<ext>
#
# where ``NN`` is a zero-padded batch number. Lexical sort then equals
# numeric sort for up to 100 batches; for projects > 100 batches per
# claim, the caller should review the naming scheme.


_BATCH_NUMBER_WIDTH: Final[int] = 2


def batch_filename(
    claim_id: str,
    batch_num: int,
    *,
    format: PersistenceFormat = "yaml",
) -> str:
    """Build the canonical filename for one search-result batch.

    Example::

        >>> batch_filename("CB-bov-01", 7)
        'search_CB-bov-01_batch_07.yaml'

    Args:
        claim_id: stable claim identifier (no whitespace).
        batch_num: non-negative batch index.
        format: ``"yaml"`` (default) or ``"json"``.

    Raises:
        ValueError: on negative ``batch_num`` or whitespace in ``claim_id``.
    """
    if batch_num < 0:
        raise ValueError(f"batch_num must be non-negative, got {batch_num}")
    if not claim_id or any(c.isspace() for c in claim_id):
        raise ValueError(f"claim_id must be non-empty and whitespace-free, got {claim_id!r}")
    extension = "yaml" if format == "yaml" else "json"
    return f"search_{claim_id}_batch_{batch_num:0{_BATCH_NUMBER_WIDTH}d}.{extension}"


def save_batch(
    session_dir: Path,
    claim_id: str,
    batch_num: int,
    records: list[Any],
    *,
    format: PersistenceFormat = "yaml",
) -> Path:
    """Save ``records`` to ``session_dir`` under the canonical batch filename.

    Returns the written path.
    """
    target = session_dir / batch_filename(claim_id, batch_num, format=format)
    return save(target, records, format=format)


def find_batches(session_dir: Path, claim_id: str) -> list[Path]:
    """Return all batch files for ``claim_id`` in ``session_dir``, sorted.

    Both YAML and JSON batches are returned (dual-format read), sorted by
    batch number (zero-padded → lexical sort works).

    A nonexistent ``session_dir`` returns an empty list rather than raising.
    """
    if not session_dir.exists():
        return []
    prefix = f"search_{claim_id}_batch_"
    matches: list[Path] = []
    for pattern in (f"{prefix}*.yaml", f"{prefix}*.yml", f"{prefix}*.json"):
        matches.extend(session_dir.glob(pattern))
    return sorted(matches, key=lambda p: p.stem)

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
"""Audit trail — strategy probes, deviations, and event log.

Three artefacts, each a single YAML file inside the session directory.
All append-only from the caller's perspective; the persistence layer
loads-mutates-saves to keep the YAML diff-friendly and comment-preserving.

Files written under ``session_dir``:

    strategy_build_log.yaml   # every hit-count probe (per claim)
    deviations_register.yaml  # registered deviations (DEV-NNN-NNN)
    event_log.yaml            # timestamped events (workflow milestones)

The choice to keep all three in YAML files (not a SQLite DB) is
intentional: the deliverable is a regulatory artefact. The audit log
must be human-readable, diff-able, and reproducible from the file
contents alone — no opaque binary state.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .persistence import load, save

# ---------------------------------------------------------------------------
# Entry types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StrategyProbeEntry:
    """One hit-count probe — what was tried, what came back.

    Matches the ``strategy_build_log`` schema in SearchProtocol v1.
    """

    timestamp: str  # ISO-8601 UTC
    claim_id: str
    query: str
    total_count: int
    query_translation: str
    heat_bar: str  # "green" | "yellow" | "red"
    rationale: str | None = None

    def to_yaml_dict(self) -> dict[str, Any]:
        """Return a plain dict suitable for YAML serialisation."""
        d: dict[str, Any] = {
            "timestamp": self.timestamp,
            "claim_id": self.claim_id,
            "query": self.query,
            "total_count": self.total_count,
            "query_translation": self.query_translation,
            "heat_bar": self.heat_bar,
        }
        if self.rationale is not None:
            d["rationale"] = self.rationale
        return d

    @classmethod
    def from_yaml_dict(cls, d: dict[str, Any]) -> StrategyProbeEntry:
        return cls(
            timestamp=str(d["timestamp"]),
            claim_id=str(d["claim_id"]),
            query=str(d["query"]),
            total_count=int(d["total_count"]),
            query_translation=str(d.get("query_translation", "")),
            heat_bar=str(d["heat_bar"]),
            rationale=d.get("rationale"),
        )


@dataclass(frozen=True, slots=True)
class DeviationEntry:
    """One registered deviation from baseline methodology.

    Examples: DEV-722-001 (PubMed-only), DEV-722-002 (Barry 2022 hybrid).
    """

    id: str
    title: str
    rationale: str
    mitigation: str | None = None
    affects: str | None = None

    def to_yaml_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "rationale": self.rationale,
        }
        if self.mitigation is not None:
            d["mitigation"] = self.mitigation
        if self.affects is not None:
            d["affects"] = self.affects
        return d

    @classmethod
    def from_yaml_dict(cls, d: dict[str, Any]) -> DeviationEntry:
        return cls(
            id=str(d["id"]),
            title=str(d["title"]),
            rationale=str(d["rationale"]),
            mitigation=d.get("mitigation"),
            affects=d.get("affects"),
        )


@dataclass(frozen=True, slots=True)
class EventLogEntry:
    """One workflow event.

    Example event types:
        - search_started, search_completed
        - batch_persisted
        - screening_started, screening_completed
        - appraisal_completed
        - report_generated
    """

    timestamp: str
    event_type: str
    claim_id: str | None
    details: dict[str, Any] = field(default_factory=dict)

    def to_yaml_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
        }
        if self.claim_id is not None:
            d["claim_id"] = self.claim_id
        if self.details:
            d["details"] = dict(self.details)
        return d

    @classmethod
    def from_yaml_dict(cls, d: dict[str, Any]) -> EventLogEntry:
        return cls(
            timestamp=str(d["timestamp"]),
            event_type=str(d["event_type"]),
            claim_id=d.get("claim_id"),
            details=dict(d.get("details", {})),
        )


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


STRATEGY_LOG_FILENAME: str = "strategy_build_log.yaml"
DEVIATIONS_FILENAME: str = "deviations_register.yaml"
EVENT_LOG_FILENAME: str = "event_log.yaml"


def _is_listlike(obj: Any) -> bool:
    """True if ``obj`` is a plain ``list`` or a ruamel ``CommentedSeq``.

    We avoid importing CommentedSeq directly to keep the dependency
    surface small — duck-typing on append + iteration is sufficient.
    """
    return isinstance(obj, list) or (
        hasattr(obj, "append") and hasattr(obj, "__iter__") and not isinstance(obj, str | bytes)
    )


def _now_iso() -> str:
    """ISO-8601 UTC timestamp with second resolution."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AuditLog:
    """Audit trail persisted across three YAML files in one session directory.

    The class is stateless w.r.t. cached entries — every append loads the
    file, mutates the list, saves. This keeps the canonical state on disk
    (resume-safe across crashes / sessions) and avoids the
    in-memory-versus-disk drift problem.
    """

    def __init__(self, session_dir: Path) -> None:
        self._session_dir = session_dir
        session_dir.mkdir(parents=True, exist_ok=True)

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    # -- file paths ---------------------------------------------------------

    @property
    def strategy_log_path(self) -> Path:
        return self._session_dir / STRATEGY_LOG_FILENAME

    @property
    def deviations_path(self) -> Path:
        return self._session_dir / DEVIATIONS_FILENAME

    @property
    def event_log_path(self) -> Path:
        return self._session_dir / EVENT_LOG_FILENAME

    # -- strategy probes ----------------------------------------------------

    def log_strategy_probe(self, entry: StrategyProbeEntry) -> None:
        """Append one probe entry to ``strategy_build_log.yaml``."""
        existing = self._load_list(self.strategy_log_path)
        existing.append(entry.to_yaml_dict())
        save(self.strategy_log_path, existing, format="yaml")

    def load_strategy_probes(self, claim_id: str | None = None) -> list[StrategyProbeEntry]:
        """Load probes; if ``claim_id`` given, filter to that claim."""
        raw = self._load_list(self.strategy_log_path)
        entries = [StrategyProbeEntry.from_yaml_dict(d) for d in raw]
        if claim_id is None:
            return entries
        return [e for e in entries if e.claim_id == claim_id]

    # -- deviations ---------------------------------------------------------

    def register_deviation(self, deviation: DeviationEntry) -> None:
        """Append a deviation to the register.

        Raises:
            ValueError: if a deviation with the same ``id`` already exists.
        """
        existing = self._load_list(self.deviations_path)
        existing_ids = {str(d.get("id")) for d in existing}
        if deviation.id in existing_ids:
            raise ValueError(
                f"Deviation {deviation.id!r} is already registered. "
                "Edit the YAML file directly to update."
            )
        existing.append(deviation.to_yaml_dict())
        save(self.deviations_path, existing, format="yaml")

    def load_deviations(self) -> list[DeviationEntry]:
        """Load all registered deviations."""
        raw = self._load_list(self.deviations_path)
        return [DeviationEntry.from_yaml_dict(d) for d in raw]

    def register_deviations(self, deviations: Iterable[DeviationEntry]) -> None:
        """Bulk-register deviations, skipping already-present ids silently."""
        existing = self._load_list(self.deviations_path)
        existing_ids = {str(d.get("id")) for d in existing}
        appended = False
        for dev in deviations:
            if dev.id in existing_ids:
                continue
            existing.append(dev.to_yaml_dict())
            existing_ids.add(dev.id)
            appended = True
        if appended:
            save(self.deviations_path, existing, format="yaml")

    # -- events -------------------------------------------------------------

    def log_event(
        self,
        event_type: str,
        claim_id: str | None = None,
        **details: Any,
    ) -> EventLogEntry:
        """Append a timestamped event. Returns the written entry."""
        entry = EventLogEntry(
            timestamp=_now_iso(),
            event_type=event_type,
            claim_id=claim_id,
            details=details,
        )
        existing = self._load_list(self.event_log_path)
        existing.append(entry.to_yaml_dict())
        save(self.event_log_path, existing, format="yaml")
        return entry

    def load_events(self, event_type: str | None = None) -> list[EventLogEntry]:
        """Load all events; optionally filter by ``event_type``."""
        raw = self._load_list(self.event_log_path)
        entries = [EventLogEntry.from_yaml_dict(d) for d in raw]
        if event_type is None:
            return entries
        return [e for e in entries if e.event_type == event_type]

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _load_list(path: Path) -> Any:
        """Load a list-of-dicts YAML/JSON file, returning [] if absent.

        Returns the raw loaded structure (a ``ruamel.yaml.CommentedSeq``
        for YAML, a ``list`` for JSON). The caller appends new entries
        to this object directly; on save, ruamel preserves the original
        comments because the underlying object is unchanged.
        """
        if not path.exists():
            return []
        data = load(path)
        if data is None:
            return []
        if not _is_listlike(data):
            raise ValueError(f"Expected a YAML/JSON list at {path}, got {type(data).__name__}")
        return data


# ---------------------------------------------------------------------------
# Convenience: build a probe entry from a hit-count result
# ---------------------------------------------------------------------------


def probe_entry_from_hit_count(
    claim_id: str,
    hit: Any,  # ring2.core.pubmed_client.HitCountResult — avoid circular import
    *,
    rationale: str | None = None,
    heat_bar_value: str | None = None,
) -> StrategyProbeEntry:
    """Adapter: convert a :class:`HitCountResult` into a :class:`StrategyProbeEntry`.

    Defined here (not in pubmed_client) to avoid coupling the client to
    the audit module — the client should remain usable without an audit
    trail. ``heat_bar_value`` is taken from the caller if provided;
    otherwise the function imports the bar function lazily.
    """
    if heat_bar_value is None:
        from .pubmed_client import heat_bar as _hb

        heat_bar_value = _hb(hit.total_count)
    return StrategyProbeEntry(
        timestamp=hit.timestamp,
        claim_id=claim_id,
        query=hit.query,
        total_count=hit.total_count,
        query_translation=hit.query_translation,
        heat_bar=heat_bar_value,
        rationale=rationale,
    )


__all__ = [
    "DEVIATIONS_FILENAME",
    "EVENT_LOG_FILENAME",
    "STRATEGY_LOG_FILENAME",
    "AuditLog",
    "DeviationEntry",
    "EventLogEntry",
    "StrategyProbeEntry",
    "probe_entry_from_hit_count",
]


# Suppress unused-import warning for asdict (kept for future use in tests/docs)
_ = asdict

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
"""PubMed client — thin wrapper over the PubMed MCP tools.

The actual MCP invocations (``PubMed:search_articles`` etc.) happen at
runtime inside a Claude session. To keep this module fully testable in
isolation, we depend on the abstract :class:`MCPCaller` protocol rather
than calling MCP directly. Production code wires a real MCP-bridging
caller; tests inject a fake.

Hit-count probing (verified in architecture v1 §5):

    >>> client.probe_hit_count("Collagen[Mesh] AND Cattle[Mesh]")
    HitCountResult(total_count=7740, query_translation="...", ...)

Per-term caching is enabled by default. The cache is keyed by the exact
query string passed in; PubMed-side MeSH expansion is captured in
``query_translation`` but does *not* affect cache identity.

Rate-limit policy is currently advisory only (UNKLAR-A2). Callers should
debounce upstream (the UI strategy builder enforces ≥ 300 ms between
probes). NCBI's documented limits: 3 req/s without API key, 10 req/s with.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from .adapter_base import PubMedRecord

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HitCountResult:
    """Output of :meth:`PubMedClient.probe_hit_count`.

    Used both as a return value and as the payload for the strategy
    build log (per architecture v1 §5).
    """

    timestamp: str  # ISO-8601 UTC
    query: str
    total_count: int
    query_translation: str
    returned_count: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Output of :meth:`PubMedClient.search`. Carries one page of records."""

    timestamp: str
    query: str
    total_count: int
    query_translation: str
    records: tuple[PubMedRecord, ...]
    retstart: int
    has_more: bool


# ---------------------------------------------------------------------------
# MCP boundary — Protocol + raw-record helpers
# ---------------------------------------------------------------------------


@runtime_checkable
class MCPCaller(Protocol):
    """Abstraction over the PubMed MCP tools.

    Production implementations bridge to the real MCP server. Tests
    inject fakes returning pre-baked dicts.

    Method shape mirrors the PubMed MCP tool catalog. Every method must
    return a JSON-decoded dict. Implementations should not raise on
    HTTP errors but should surface them in the dict (per MCP convention).
    """

    def search_articles(
        self,
        query: str,
        max_results: int = 10,
        retstart: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Wraps ``PubMed:search_articles``.

        Expected response keys:
        ``total_count``, ``query_translation``, ``returned_count``,
        ``has_more``, ``articles`` (list of record dicts).
        """
        ...

    def get_article_metadata(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        """Wraps ``PubMed:get_article_metadata``."""
        ...

    def get_full_text_article(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        """Wraps ``PubMed:get_full_text_article``."""
        ...

    def convert_article_ids(
        self, ids: list[str], from_type: str, to_type: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Wraps ``PubMed:convert_article_ids``."""
        ...

    def find_related_articles(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        """Wraps ``PubMed:find_related_articles``."""
        ...


# ---------------------------------------------------------------------------
# Heat-bar (per architecture v1 §1.4 / prompt v3 §Stage 2b)
# ---------------------------------------------------------------------------


HEAT_BAR_GREEN_MAX: int = 500
HEAT_BAR_YELLOW_MAX: int = 5_000


def heat_bar(total_count: int) -> str:
    """Map a hit count to a heat-bar bucket.

    Thresholds per architecture v1 §1.4:

        green  : count < 500          (workable scope)
        yellow : 500 ≤ count ≤ 5,000  (likely too broad; consider narrowing)
        red    : count > 5,000        (clearly too broad)

    Note: thresholds flagged as ``U-B2`` (heuristic, not calibrated) —
    revisit after the first batch of real queries.
    """
    if total_count < 0:
        raise ValueError(f"total_count must be non-negative, got {total_count}")
    if total_count < HEAT_BAR_GREEN_MAX:
        return "green"
    if total_count <= HEAT_BAR_YELLOW_MAX:
        return "yellow"
    return "red"


# ---------------------------------------------------------------------------
# Record extraction
# ---------------------------------------------------------------------------


def record_from_mcp_dict(raw: dict[str, Any]) -> PubMedRecord:
    """Convert one PubMed-MCP article dict to a :class:`PubMedRecord`.

    Tolerates missing optional fields (the upstream tool may omit any
    of them depending on the article). The full raw dict is retained
    in ``PubMedRecord.raw`` for adapter-specific later use.

    Raises:
        KeyError: if ``pmid`` is missing (the only truly required field).
    """
    pmid = str(raw["pmid"])
    title = str(raw.get("title", ""))

    authors_raw = raw.get("authors", ())
    if isinstance(authors_raw, str):
        authors: tuple[str, ...] = (authors_raw,)
    else:
        authors = tuple(str(a) for a in authors_raw)

    pubtypes_raw = raw.get("publication_types", ())
    if isinstance(pubtypes_raw, str):
        publication_types: tuple[str, ...] = (pubtypes_raw,)
    else:
        publication_types = tuple(str(t) for t in pubtypes_raw)

    year_raw = raw.get("year")
    year: int | None
    if isinstance(year_raw, int):
        year = year_raw
    elif isinstance(year_raw, str) and year_raw.strip().isdigit():
        year = int(year_raw.strip())
    else:
        year = None

    return PubMedRecord(
        pmid=pmid,
        title=title,
        doi=raw.get("doi") or None,
        abstract=raw.get("abstract") or None,
        journal=raw.get("journal") or None,
        year=year,
        authors=authors,
        publication_types=publication_types,
        raw=dict(raw),
    )


# ---------------------------------------------------------------------------
# PubMedClient
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """ISO-8601 UTC timestamp with second resolution."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class _CachedHitCount:
    """Internal cache value: full HitCountResult plus its timestamp."""

    result: HitCountResult


class PubMedClient:
    """Thin client over the PubMed MCP tools.

    Parameters
    ----------
    caller:
        An :class:`MCPCaller` — the production wrapper around the MCP
        invocations, or a fake for tests.
    cache_hits:
        If ``True`` (default), repeated :meth:`probe_hit_count` calls
        with the same query string return the cached result without
        another MCP call. Use :meth:`clear_cache` to reset.

    Notes
    -----
    The client does **not** debounce internally. The UI strategy
    builder (per architecture v1) is responsible for debouncing
    user-driven probes.
    """

    def __init__(self, caller: MCPCaller, *, cache_hits: bool = True) -> None:
        self._caller = caller
        self._cache_enabled = cache_hits
        self._hit_cache: dict[str, _CachedHitCount] = {}

    # -- hit-count probing --------------------------------------------------

    def probe_hit_count(self, query: str) -> HitCountResult:
        """Probe PubMed for the size of the result set for ``query``.

        Calls ``search_articles(query, max_results=1)`` and extracts
        ``total_count`` + ``query_translation`` from the response.
        """
        if self._cache_enabled and query in self._hit_cache:
            return self._hit_cache[query].result

        response = self._caller.search_articles(query=query, max_results=1)
        result = HitCountResult(
            timestamp=_now_iso(),
            query=query,
            total_count=int(response["total_count"]),
            query_translation=str(response.get("query_translation", "")),
            returned_count=int(response.get("returned_count", 0)),
            has_more=bool(response.get("has_more", False)),
        )

        if self._cache_enabled:
            self._hit_cache[query] = _CachedHitCount(result=result)
        return result

    def clear_cache(self) -> None:
        """Empty the per-term hit-count cache."""
        self._hit_cache.clear()

    @property
    def cache_size(self) -> int:
        return len(self._hit_cache)

    # -- full search --------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        retstart: int = 0,
        **kwargs: Any,
    ) -> SearchResult:
        """Run a paginated PubMed search.

        Args:
            query: full search string (MeSH + free-text, AND/OR-combined).
            max_results: page size. PubMed MCP defaults vary; 10 matches
                our batch convention.
            retstart: zero-based offset for pagination.
            **kwargs: passed through to the MCP caller (e.g. date ranges).
        """
        if max_results < 1:
            raise ValueError(f"max_results must be >= 1, got {max_results}")
        if retstart < 0:
            raise ValueError(f"retstart must be >= 0, got {retstart}")

        response = self._caller.search_articles(
            query=query, max_results=max_results, retstart=retstart, **kwargs
        )
        articles_raw: list[dict[str, Any]] = list(response.get("articles", ()))
        records = tuple(record_from_mcp_dict(a) for a in articles_raw)

        return SearchResult(
            timestamp=_now_iso(),
            query=query,
            total_count=int(response["total_count"]),
            query_translation=str(response.get("query_translation", "")),
            records=records,
            retstart=retstart,
            has_more=bool(response.get("has_more", False)),
        )

    def iter_batches(
        self,
        query: str,
        *,
        batch_size: int = 10,
        max_batches: int | None = None,
        **kwargs: Any,
    ) -> Iterator[SearchResult]:
        """Yield successive batches of search results until exhausted.

        Stops when ``has_more`` is ``False`` or ``max_batches`` is reached.
        Each yielded :class:`SearchResult` carries its own ``retstart``
        so a caller can resume mid-iteration by replaying with the same
        offset.

        Args:
            query: full search string.
            batch_size: records per batch (matches persistence convention).
            max_batches: optional cap to bound total MCP calls in dev/test.
            **kwargs: passed through to each underlying search.
        """
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        if max_batches is not None and max_batches < 0:
            raise ValueError(f"max_batches must be >= 0, got {max_batches}")

        retstart = 0
        batches_yielded = 0
        while True:
            if max_batches is not None and batches_yielded >= max_batches:
                return
            batch = self.search(query, max_results=batch_size, retstart=retstart, **kwargs)
            yield batch
            batches_yielded += 1
            if not batch.has_more or not batch.records:
                return
            retstart += len(batch.records)

    # -- single-record fetch ------------------------------------------------

    def get_metadata(self, pmid: str) -> PubMedRecord:
        """Fetch detailed metadata for one PMID."""
        response = self._caller.get_article_metadata(pmid=pmid)
        # MCP may return either {"article": {...}} or the article dict directly.
        article = response.get("article", response)
        if not isinstance(article, dict):
            raise ValueError(f"Unexpected metadata response for pmid={pmid!r}: {response!r}")
        return record_from_mcp_dict(article)


# ---------------------------------------------------------------------------
# Convenience: a no-op caller for use as a default / sentinel
# ---------------------------------------------------------------------------


@dataclass
class NullMCPCaller:
    """An MCPCaller that raises on every call.

    Useful as a default in code paths that should never reach MCP (e.g.
    when caching guarantees offline behaviour) — fails loudly rather
    than silently emitting empty results.
    """

    message: str = "PubMed MCP not available in this context"
    _calls: list[str] = field(default_factory=list)

    def _fail(self, name: str) -> None:
        self._calls.append(name)
        raise RuntimeError(f"{self.message} (attempted {name})")

    def search_articles(
        self, query: str, max_results: int = 10, retstart: int = 0, **kwargs: Any
    ) -> dict[str, Any]:
        self._fail("search_articles")
        raise AssertionError("unreachable")  # for type-checker

    def get_article_metadata(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        self._fail("get_article_metadata")
        raise AssertionError("unreachable")

    def get_full_text_article(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        self._fail("get_full_text_article")
        raise AssertionError("unreachable")

    def convert_article_ids(
        self, ids: list[str], from_type: str, to_type: str, **kwargs: Any
    ) -> dict[str, Any]:
        self._fail("convert_article_ids")
        raise AssertionError("unreachable")

    def find_related_articles(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        self._fail("find_related_articles")
        raise AssertionError("unreachable")

# Copyright 2026 lets-innovate.ch (Michael Hug)
# Licensed under the Apache License, Version 2.0. See LICENSE for the full text.
"""Tests for ring2.core.pubmed_client.

Uses a fake :class:`MCPCaller` to exercise the client end-to-end without
touching real MCP infrastructure.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from ring2.core.pubmed_client import (
    HEAT_BAR_GREEN_MAX,
    HEAT_BAR_YELLOW_MAX,
    HitCountResult,
    MCPCaller,
    NullMCPCaller,
    PubMedClient,
    SearchResult,
    heat_bar,
    record_from_mcp_dict,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeMCPCaller:
    """Records calls and replays canned responses. Implements MCPCaller."""

    responses: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def search_articles(
        self, query: str, max_results: int = 10, retstart: int = 0, **kwargs: Any
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "tool": "search_articles",
                "query": query,
                "max_results": max_results,
                "retstart": retstart,
                **kwargs,
            }
        )
        if self._index >= len(self.responses):
            raise AssertionError(f"FakeMCPCaller out of canned responses after {self._index} calls")
        response = self.responses[self._index]
        self._index += 1
        return response

    def get_article_metadata(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"tool": "get_article_metadata", "pmid": pmid, **kwargs})
        if self._index >= len(self.responses):
            raise AssertionError("FakeMCPCaller out of canned responses")
        response = self.responses[self._index]
        self._index += 1
        return response

    def get_full_text_article(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def convert_article_ids(
        self, ids: list[str], from_type: str, to_type: str, **kwargs: Any
    ) -> dict[str, Any]:
        raise NotImplementedError

    def find_related_articles(self, pmid: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_fake_caller_is_mcp_caller() -> None:
    fake = FakeMCPCaller()
    assert isinstance(fake, MCPCaller)


def test_null_caller_is_mcp_caller() -> None:
    assert isinstance(NullMCPCaller(), MCPCaller)


# ---------------------------------------------------------------------------
# heat_bar
# ---------------------------------------------------------------------------


def test_heat_bar_green() -> None:
    assert heat_bar(0) == "green"
    assert heat_bar(1) == "green"
    assert heat_bar(HEAT_BAR_GREEN_MAX - 1) == "green"


def test_heat_bar_yellow() -> None:
    assert heat_bar(HEAT_BAR_GREEN_MAX) == "yellow"
    assert heat_bar(2_500) == "yellow"
    assert heat_bar(HEAT_BAR_YELLOW_MAX) == "yellow"


def test_heat_bar_red() -> None:
    assert heat_bar(HEAT_BAR_YELLOW_MAX + 1) == "red"
    assert heat_bar(100_000) == "red"


def test_heat_bar_negative_raises() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        heat_bar(-1)


# ---------------------------------------------------------------------------
# record_from_mcp_dict
# ---------------------------------------------------------------------------


def test_record_minimal() -> None:
    rec = record_from_mcp_dict({"pmid": "1", "title": "Hello"})
    assert rec.pmid == "1"
    assert rec.title == "Hello"
    assert rec.doi is None
    assert rec.year is None
    assert rec.authors == ()
    assert rec.publication_types == ()


def test_record_full() -> None:
    rec = record_from_mcp_dict(
        {
            "pmid": "33899930",
            "title": "Alveolar ridge preservation",
            "doi": "10.1002/14651858.CD009603.pub3",
            "abstract": "Long abstract...",
            "journal": "Cochrane Database Syst Rev",
            "year": "2021",
            "authors": ["Atieh MA", "Alsabeeha NHM"],
            "publication_types": ["Systematic Review", "Meta-Analysis"],
        }
    )
    assert rec.pmid == "33899930"
    assert rec.doi == "10.1002/14651858.CD009603.pub3"
    assert rec.year == 2021
    assert rec.authors == ("Atieh MA", "Alsabeeha NHM")
    assert "Systematic Review" in rec.publication_types
    assert "Cochrane" in rec.journal


def test_record_year_unparseable_is_none() -> None:
    rec = record_from_mcp_dict({"pmid": "1", "title": "t", "year": "not-a-year"})
    assert rec.year is None


def test_record_authors_as_string_normalised_to_tuple() -> None:
    rec = record_from_mcp_dict({"pmid": "1", "title": "t", "authors": "Lone Wolf J"})
    assert rec.authors == ("Lone Wolf J",)


def test_record_pmid_coerced_to_str() -> None:
    rec = record_from_mcp_dict({"pmid": 12345, "title": "t"})
    assert rec.pmid == "12345"


def test_record_missing_pmid_raises() -> None:
    with pytest.raises(KeyError):
        record_from_mcp_dict({"title": "no pmid"})


def test_record_retains_raw_passthrough() -> None:
    raw = {"pmid": "1", "title": "t", "custom_field": {"nested": [1, 2]}}
    rec = record_from_mcp_dict(raw)
    assert rec.raw["custom_field"] == {"nested": [1, 2]}


# ---------------------------------------------------------------------------
# probe_hit_count
# ---------------------------------------------------------------------------


def test_probe_hit_count_returns_full_metadata() -> None:
    fake = FakeMCPCaller(
        responses=[
            {
                "total_count": 360,
                "query_translation": '"collagen"[MeSH Terms] AND "cattle"[MeSH Terms]',
                "returned_count": 1,
                "has_more": True,
            }
        ]
    )
    client = PubMedClient(fake)
    hit = client.probe_hit_count("Collagen[Mesh] AND Cattle[Mesh]")
    assert isinstance(hit, HitCountResult)
    assert hit.total_count == 360
    assert "collagen" in hit.query_translation
    assert hit.returned_count == 1
    assert hit.has_more is True
    assert hit.query == "Collagen[Mesh] AND Cattle[Mesh]"
    assert hit.timestamp.endswith("Z")


def test_probe_hit_count_passes_max_results_one() -> None:
    """Per architecture v1 §5: probe uses max_results=1."""
    fake = FakeMCPCaller(
        responses=[
            {"total_count": 0, "query_translation": "", "returned_count": 0, "has_more": False}
        ]
    )
    PubMedClient(fake).probe_hit_count("anything[tw]")
    assert fake.calls[0]["max_results"] == 1


def test_probe_hit_count_caches_by_query() -> None:
    fake = FakeMCPCaller(
        responses=[
            {"total_count": 5, "query_translation": "x", "returned_count": 1, "has_more": False}
        ]
    )
    client = PubMedClient(fake)
    a = client.probe_hit_count("X")
    b = client.probe_hit_count("X")  # served from cache
    assert a == b
    assert len(fake.calls) == 1
    assert client.cache_size == 1


def test_probe_hit_count_different_queries_not_cached_together() -> None:
    fake = FakeMCPCaller(
        responses=[
            {"total_count": 5, "query_translation": "x", "returned_count": 1, "has_more": False},
            {"total_count": 7, "query_translation": "y", "returned_count": 1, "has_more": False},
        ]
    )
    client = PubMedClient(fake)
    client.probe_hit_count("X")
    client.probe_hit_count("Y")
    assert len(fake.calls) == 2
    assert client.cache_size == 2


def test_probe_hit_count_cache_can_be_disabled() -> None:
    fake = FakeMCPCaller(
        responses=[
            {"total_count": 5, "query_translation": "", "returned_count": 1, "has_more": False},
            {"total_count": 5, "query_translation": "", "returned_count": 1, "has_more": False},
        ]
    )
    client = PubMedClient(fake, cache_hits=False)
    client.probe_hit_count("X")
    client.probe_hit_count("X")
    assert len(fake.calls) == 2


def test_clear_cache_works() -> None:
    fake = FakeMCPCaller(
        responses=[
            {"total_count": 5, "query_translation": "", "returned_count": 1, "has_more": False},
            {"total_count": 5, "query_translation": "", "returned_count": 1, "has_more": False},
        ]
    )
    client = PubMedClient(fake)
    client.probe_hit_count("X")
    client.clear_cache()
    assert client.cache_size == 0
    client.probe_hit_count("X")
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_returns_records() -> None:
    fake = FakeMCPCaller(
        responses=[
            {
                "total_count": 25,
                "query_translation": "X",
                "returned_count": 10,
                "has_more": True,
                "articles": [{"pmid": str(i), "title": f"paper {i}"} for i in range(10)],
            }
        ]
    )
    client = PubMedClient(fake)
    result = client.search("X", max_results=10)
    assert isinstance(result, SearchResult)
    assert len(result.records) == 10
    assert result.records[0].pmid == "0"
    assert result.records[9].pmid == "9"
    assert result.has_more is True
    assert result.total_count == 25
    assert result.retstart == 0


def test_search_invalid_args() -> None:
    fake = FakeMCPCaller()
    client = PubMedClient(fake)
    with pytest.raises(ValueError):
        client.search("X", max_results=0)
    with pytest.raises(ValueError):
        client.search("X", retstart=-1)


# ---------------------------------------------------------------------------
# iter_batches
# ---------------------------------------------------------------------------


def _make_batch_response(pmids: list[str], total_count: int, has_more: bool) -> dict[str, Any]:
    return {
        "total_count": total_count,
        "query_translation": "X",
        "returned_count": len(pmids),
        "has_more": has_more,
        "articles": [{"pmid": p, "title": f"paper {p}"} for p in pmids],
    }


def test_iter_batches_stops_when_no_more() -> None:
    fake = FakeMCPCaller(
        responses=[
            _make_batch_response(["1", "2", "3"], total_count=7, has_more=True),
            _make_batch_response(["4", "5", "6"], total_count=7, has_more=True),
            _make_batch_response(["7"], total_count=7, has_more=False),
        ]
    )
    client = PubMedClient(fake)
    batches = list(client.iter_batches("X", batch_size=3))
    assert len(batches) == 3
    assert [r.pmid for b in batches for r in b.records] == ["1", "2", "3", "4", "5", "6", "7"]
    # retstart progression
    assert [b.retstart for b in batches] == [0, 3, 6]


def test_iter_batches_respects_max_batches() -> None:
    fake = FakeMCPCaller(
        responses=[
            _make_batch_response(["1"], total_count=100, has_more=True),
            _make_batch_response(["2"], total_count=100, has_more=True),
            _make_batch_response(["3"], total_count=100, has_more=True),
        ]
    )
    client = PubMedClient(fake)
    batches = list(client.iter_batches("X", batch_size=1, max_batches=2))
    assert len(batches) == 2


def test_iter_batches_stops_on_empty_batch() -> None:
    """Defensive: if has_more is true but server returns empty articles, stop anyway."""
    fake = FakeMCPCaller(
        responses=[
            _make_batch_response([], total_count=0, has_more=True),
        ]
    )
    client = PubMedClient(fake)
    batches = list(client.iter_batches("X"))
    assert len(batches) == 1
    assert batches[0].records == ()


def test_iter_batches_validates_args() -> None:
    client = PubMedClient(FakeMCPCaller())
    with pytest.raises(ValueError):
        list(client.iter_batches("X", batch_size=0))
    with pytest.raises(ValueError):
        list(client.iter_batches("X", max_batches=-1))


# ---------------------------------------------------------------------------
# get_metadata
# ---------------------------------------------------------------------------


def test_get_metadata_flat_response() -> None:
    fake = FakeMCPCaller(responses=[{"pmid": "42", "title": "answer", "authors": ["Adams D"]}])
    client = PubMedClient(fake)
    rec = client.get_metadata("42")
    assert rec.pmid == "42"
    assert rec.authors == ("Adams D",)


def test_get_metadata_wrapped_response() -> None:
    fake = FakeMCPCaller(responses=[{"article": {"pmid": "42", "title": "answer"}}])
    client = PubMedClient(fake)
    rec = client.get_metadata("42")
    assert rec.pmid == "42"


# ---------------------------------------------------------------------------
# NullMCPCaller
# ---------------------------------------------------------------------------


def test_null_caller_raises_on_search() -> None:
    null = NullMCPCaller()
    with pytest.raises(RuntimeError, match="not available"):
        null.search_articles(query="X")


def test_null_caller_records_attempts() -> None:
    null = NullMCPCaller()
    with pytest.raises(RuntimeError):
        null.search_articles(query="X")
    with pytest.raises(RuntimeError):
        null.get_article_metadata(pmid="42")
    assert null._calls == ["search_articles", "get_article_metadata"]

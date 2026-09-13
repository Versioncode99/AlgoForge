"""The research settings reach the retrieval, or they are decorative.

Depth, freshness and source category are stored, shown on a screen and patched
over the API. None of that means anything unless the next retrieval is different
because of it — and a setting that is stored and never read is exactly the class
of defect this phase's audit opened with.

These drive `search` and `retrieve` against a stub transport, so the assertion is
about which requests were made and which results came back, not about a mock
being called.
"""

from __future__ import annotations

import httpx
import pytest
from forge.research.literature import (
    CATEGORY_INDEXES,
    Source,
    _freshness_adjusted,
    _year_of,
    retrieve,
    search,
)

ARXIV_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Intraday momentum in futures</title>
    <summary>We document significant time series momentum in intraday futures returns.</summary>
    <published>2024-01-02T00:00:00Z</published>
    <author><name>A. Author</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/9901.00002v1</id>
    <title>Intraday momentum revisited</title>
    <summary>We show that intraday futures momentum persists across decades.</summary>
    <published>1999-01-02T00:00:00Z</published>
    <author><name>B. Author</name></author>
  </entry>
</feed>"""

CROSSREF_JSON = {
    "message": {
        "items": [
            {
                "title": ["Order flow and intraday returns"],
                "abstract": "Order imbalance predicts short-horizon futures returns.",
                "URL": "https://doi.org/10.1000/x",
                "issued": {"date-parts": [[2023, 5, 1]]},
                "author": [{"given": "C.", "family": "Author"}],
            }
        ]
    }
}


class _Recorder(httpx.BaseTransport):
    """Answers both indexes and remembers which hosts were asked."""

    def __init__(self) -> None:
        self.hosts: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if "arxiv" in request.url.host:
            return httpx.Response(200, text=ARXIV_FEED, request=request)
        return httpx.Response(200, json=CROSSREF_JSON, request=request)


@pytest.fixture
def transport() -> _Recorder:
    return _Recorder()


# ── depth ────────────────────────────────────────────────────────────────────


def test_the_depth_setting_bounds_how_many_results_come_back(transport) -> None:
    report = retrieve("intraday momentum", limit=1, transport=transport)
    assert report.ok
    assert len(report.found) <= 1


def test_a_deeper_search_may_return_more(transport) -> None:
    shallow = retrieve("intraday momentum", limit=1, transport=transport)
    deep = retrieve("intraday momentum", limit=10, transport=_Recorder())
    assert len(deep.found) >= len(shallow.found)


# ── source category ──────────────────────────────────────────────────────────


def test_choosing_preprints_only_does_not_ask_crossref(transport) -> None:
    search("intraday momentum", limit=5, transport=transport, categories=("preprints",))
    assert any("arxiv" in host for host in transport.hosts)
    assert not any("crossref" in host for host in transport.hosts)


def test_choosing_journals_only_does_not_ask_arxiv(transport) -> None:
    search("intraday momentum", limit=5, transport=transport, categories=("journals",))
    assert any("crossref" in host for host in transport.hosts)
    assert not any("arxiv" in host for host in transport.hosts)


def test_naming_no_category_searches_every_index(transport) -> None:
    search("intraday momentum", limit=5, transport=transport)
    assert any("arxiv" in host for host in transport.hosts)
    assert any("crossref" in host for host in transport.hosts)


def test_the_category_map_only_names_indexes_that_exist() -> None:
    assert set(CATEGORY_INDEXES.values()) == {"arxiv", "crossref"}


# ── freshness ────────────────────────────────────────────────────────────────


def test_preferring_recent_ranks_a_newer_paper_above_an_older_one(transport) -> None:
    found = search(
        "intraday momentum", limit=5, transport=transport, categories=("preprints",),
        freshness="recent",
    )
    years = [int(item.published[:4]) for item in found if item.published[:4].isdigit()]
    assert years == sorted(years, reverse=True)


def test_preferring_recent_does_not_exclude_older_work(transport) -> None:
    """A preference is not a filter. The 1987 variance-ratio paper still counts."""
    found = search(
        "intraday momentum", limit=5, transport=transport, categories=("preprints",),
        freshness="recent",
    )
    assert any(item.published.startswith("1999") for item in found)


def test_current_only_narrows_but_never_to_nothing(transport) -> None:
    """Reporting an empty result would be a false statement about the literature."""
    found = search(
        "intraday momentum", limit=5, transport=transport, categories=("preprints",),
        freshness="current",
    )
    assert found
    assert all(not item.published.startswith("1999") for item in found)


def test_a_missing_publication_date_is_unknown_rather_than_old() -> None:
    """Penalising a result for a date the index did not report is ranking on a
    fact nobody has."""
    item = Source(
        source_id="s", title="t", authors="", source="crossref", url="",
        published="", retrieved_at="2026-09-13T00:00:00Z", abstract="a",
        claims=(), relevance=0.5, query="q", content_level="abstract",
    )
    assert _freshness_adjusted(item, "recent", 2026) == item.relevance
    assert _year_of("") == 0
    assert _year_of("2024-01-01") == 2024


def test_any_freshness_leaves_the_ranking_on_relevance_alone() -> None:
    item = Source(
        source_id="s", title="t", authors="", source="arxiv", url="",
        published="2026-01-01", retrieved_at="2026-09-13T00:00:00Z", abstract="a",
        claims=(), relevance=0.5, query="q", content_level="abstract",
    )
    assert _freshness_adjusted(item, "any", 2026) == 0.5
    assert _freshness_adjusted(item, "recent", 2026) > 0.5

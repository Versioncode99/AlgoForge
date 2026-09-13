"""Panning a chart through history, over the HTTP surface.

The defect these tests exist to hold closed is not a crash. It is a chart that
*looks* finished: one window of bars is fetched, drawn, and then the series
simply stops at its left edge — so dragging backwards through time reveals
nothing, and a sixteen-year archive behaves like a photograph of four hundred
bars. Nothing errors, so nothing catches it except a test that asks for the bars
before the ones it was given.

What is asserted here is the *contract* a chart pages against: exclusive
cursors in both directions, a window that says where it sits in the archive
rather than only what it contains, and the two flags that let a chart tell
"nothing loaded yet" apart from "there is no more history". The browser-side
merge — dedupe, ordering, viewport preservation — is held by
`apps/web/src/bars.test.ts`, because that is where it lives.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app

TIMEFRAME = "1h"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


def page(client, **params) -> dict:
    query = {"dataset": "synthetic", "timeframe": TIMEFRAME, **params}
    response = client.get("/api/v1/bars", params=query)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def times(payload: dict) -> list[str]:
    return [bar["time"] for bar in payload["bars"]]


# ── the initial window ───────────────────────────────────────────────────────


def test_initial_load_reports_its_place_in_the_archive(client) -> None:
    payload = page(client, limit=50)
    assert payload["bar_count"] == 50
    assert payload["total_bars"] > 50
    # Newest bars: there is history behind them and nothing ahead of them.
    assert payload["has_more_before"] is True
    assert payload["has_more_after"] is False
    assert payload["range_end"] == payload["coverage_end"]
    assert payload["range_start"] > payload["coverage_start"]


def test_the_legacy_has_more_flag_still_means_more_history(client) -> None:
    """Renaming it silently would have broken every caller still reading it."""
    payload = page(client, limit=50)
    assert payload["has_more"] == payload["has_more_before"]


# ── panning backwards ────────────────────────────────────────────────────────


def test_panning_backwards_returns_the_bars_before_the_window(client) -> None:
    first = page(client, limit=50)
    earlier = page(client, limit=50, before=first["range_start"])
    assert earlier["bar_count"] == 50
    # Strictly older: the cursor bar is not handed back a second time.
    assert max(times(earlier)) < first["range_start"]
    assert not set(times(earlier)) & set(times(first))


def test_repeated_backward_pans_keep_walking_and_never_repeat_a_bar(client) -> None:
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(6):
        payload = page(client, limit=40, before=cursor) if cursor else page(client, limit=40)
        stamps = times(payload)
        assert stamps == sorted(stamps), "bars must arrive oldest-first"
        assert not set(stamps) & set(seen), "a pan re-served bars already delivered"
        seen.extend(stamps)
        if not payload["has_more_before"]:
            break
        cursor = payload["range_start"]
    assert len(seen) == len(set(seen))
    assert len(seen) > 40, "paging never left the first window"


def test_a_backward_page_knows_there_is_data_ahead_of_it(client) -> None:
    """Without this the chart cannot offer to walk forward again."""
    first = page(client, limit=50)
    earlier = page(client, limit=50, before=first["range_start"])
    assert earlier["has_more_after"] is True


def test_reaching_the_start_of_history_is_reported_rather_than_guessed(client) -> None:
    """An empty response at the archive's start is a boundary, not a fault.

    A chart that cannot tell this apart from a failed request either keeps
    asking forever or stops offering history that exists.
    """
    total = page(client, limit=1)["total_bars"]
    whole = page(client, limit=total)
    assert whole["has_more_before"] is False
    assert whole["range_start"] == whole["coverage_start"]

    beyond = page(client, limit=50, before=whole["coverage_start"])
    assert beyond["bar_count"] == 0
    assert beyond["has_more_before"] is False
    assert beyond["range_start"] is None
    # The archive's own bounds are still reported, so the caller can tell an
    # exhausted pan from a dataset that was never there.
    assert beyond["coverage_start"] == whole["coverage_start"]


# ── panning forwards ─────────────────────────────────────────────────────────


def test_panning_forwards_returns_the_bars_after_the_window(client) -> None:
    # Stand at the oldest end of the archive, then walk forward from there.
    whole = page(client, limit=20_000)
    start = page(client, limit=30, after=whole["coverage_start"])
    assert start["bar_count"] == 30
    assert min(times(start)) > whole["coverage_start"]
    assert start["has_more_before"] is True, "the anchor bar itself is behind this window"

    later = page(client, limit=30, after=start["range_end"])
    assert later["bar_count"] == 30
    assert min(times(later)) > start["range_end"]
    assert not set(times(later)) & set(times(start))


def test_a_forward_pan_round_trips_with_a_backward_one(client) -> None:
    """Walk back, then forward, and land on exactly the bars left behind."""
    first = page(client, limit=40)
    earlier = page(client, limit=40, before=first["range_start"])
    returned = page(client, limit=40, after=earlier["range_end"])
    assert times(returned) == times(first)


def test_reaching_the_present_is_reported(client) -> None:
    latest = page(client, limit=10)
    beyond = page(client, limit=10, after=latest["range_end"])
    assert beyond["bar_count"] == 0
    assert beyond["has_more_after"] is False
    assert beyond["coverage_end"] == latest["coverage_end"]


# ── refusals ─────────────────────────────────────────────────────────────────


def test_paging_both_directions_at_once_is_refused(client) -> None:
    """The two cursors describe opposite journeys.

    Intersecting them, or honouring whichever the server checks first, moves a
    chart somewhere the person dragging it did not ask to go.
    """
    anchor = page(client, limit=10)["range_start"]
    response = client.get(
        "/api/v1/bars",
        params={
            "dataset": "synthetic",
            "timeframe": TIMEFRAME,
            "before": anchor,
            "after": anchor,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "conflicting_cursors"


def test_a_cursor_without_an_offset_pages_rather_than_failing(client) -> None:
    """A browser formatting a local time sends no offset.

    Compared against a tz-aware column that raises rather than comparing, so
    this would have been a 500 on the first pan from a naive client.
    """
    anchor = page(client, limit=20)["range_start"]
    naive = anchor.replace("+00:00", "")
    payload = page(client, limit=20, before=naive)
    assert payload["bar_count"] > 0
    assert max(times(payload)) < anchor


def test_an_unknown_dataset_refuses_instead_of_inventing_candles(client) -> None:
    response = client.get(
        "/api/v1/bars", params={"dataset": "not-a-dataset", "timeframe": TIMEFRAME}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "market_data_unavailable"


# ── ordering and integrity ───────────────────────────────────────────────────


def test_every_window_is_ordered_and_free_of_duplicates(client) -> None:
    for params in ({}, {"limit": 7}, {"limit": 500}):
        payload = page(client, **params)
        stamps = times(payload)
        assert stamps == sorted(stamps)
        assert len(stamps) == len(set(stamps))
        assert payload["range_start"] == stamps[0]
        assert payload["range_end"] == stamps[-1]


def test_the_window_never_reaches_outside_the_archive(client) -> None:
    payload = page(client, limit=100)
    assert payload["coverage_start"] <= payload["range_start"]
    assert payload["range_end"] <= payload["coverage_end"]


def test_a_timeframe_change_keeps_the_same_coverage_window(client) -> None:
    """Zooming changes resolution, not which history exists."""
    hourly = page(client, limit=10)
    daily = page(client, limit=10, timeframe="1d")
    assert daily["coverage_start"][:10] == hourly["coverage_start"][:10]
    assert daily["total_bars"] < hourly["total_bars"]

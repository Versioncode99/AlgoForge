"""Serving a time scope from the archive, and refusing to serve nothing.

`load` takes a bar count and resolves it as `tail(limit)`, which is how every
experiment in a campaign came to share the most recent nine months whatever its
hypothesis. `load_scope` takes dates and returns those dates.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from forge.research.timescope import fixed_range, partitioned, recent_years
from forge_api.market import MarketService, ProviderError

WHY = (
    "A bounded window is enough to falsify this claim, so the rest of the archive "
    "is left untouched for later out-of-sample work."
)


@pytest.fixture
def market() -> MarketService:
    return MarketService(Path("."))


@pytest.fixture
def reservoir(market: MarketService) -> tuple[datetime, datetime]:
    """The synthetic set's real span, measured rather than assumed."""
    return market.reservoir("synthetic")


def test_the_reservoir_is_measured_from_the_archive(market: MarketService) -> None:
    start, end = market.reservoir("synthetic")
    assert start < end
    assert start.tzinfo is not None and end.tzinfo is not None


def test_an_unknown_dataset_has_no_reservoir(market: MarketService) -> None:
    with pytest.raises(ProviderError):
        market.reservoir("no-such-dataset")


def test_a_scope_returns_the_bars_its_dates_name(market, reservoir) -> None:
    start, end = reservoir
    half = start + (end - start) / 2
    scope = fixed_range(
        dataset="synthetic", available_start=start, available_end=end,
        start=half, end=end, rationale=WHY,
    )
    bars, _ = market.load_scope(scope)
    assert bars, "the scope returned no bars"
    assert bars[0].event_time >= half
    assert bars[-1].event_time <= end


def test_two_scopes_over_different_spans_return_different_bars(market, reservoir) -> None:
    """The property the engine never had: the window actually varies."""
    start, end = reservoir
    third = (end - start) / 3
    early = fixed_range(
        dataset="synthetic", available_start=start, available_end=end,
        start=start, end=start + third, rationale=WHY,
    )
    late = fixed_range(
        dataset="synthetic", available_start=start, available_end=end,
        start=end - third, end=end, rationale=WHY,
    )
    early_bars, _ = market.load_scope(early)
    late_bars, _ = market.load_scope(late)
    assert early_bars[0].event_time < late_bars[0].event_time
    assert early_bars[-1].event_time < late_bars[-1].event_time


def test_a_narrower_scope_returns_fewer_bars(market, reservoir) -> None:
    start, end = reservoir
    whole = fixed_range(
        dataset="synthetic", available_start=start, available_end=end,
        start=start, end=end, rationale=WHY,
    )
    part = fixed_range(
        dataset="synthetic", available_start=start, available_end=end,
        start=start + (end - start) / 2, end=end, rationale=WHY,
    )
    assert len(market.load_scope(part)[0]) < len(market.load_scope(whole)[0])


def test_a_window_holding_no_bars_is_refused_not_returned_empty(market, reservoir) -> None:
    """An experiment that ran on nothing must not look like one that ran."""
    start, end = reservoir
    scope = fixed_range(
        dataset="synthetic", available_start=start, available_end=end + timedelta(days=400),
        start=end + timedelta(days=200), end=end + timedelta(days=300), rationale=WHY,
    )
    with pytest.raises(ProviderError) as exc:
        market.load_scope(scope)
    assert "holds no bars" in str(exc.value)


def test_a_named_window_can_be_loaded_on_its_own(market, reservoir) -> None:
    start, end = reservoir
    span = end - start
    scope = partitioned(
        dataset="synthetic", available_start=start, available_end=end,
        train=(start, start + span * 0.6),
        validation=(start + span * 0.6, start + span * 0.8),
        holdout=(start + span * 0.8, end),
        rationale=WHY,
    )
    train, _ = market.load_window(scope, "train")
    holdout, _ = market.load_window(scope, "holdout")
    assert train[-1].event_time <= holdout[0].event_time, "train ran past the holdout"
    assert len(train) > len(holdout)


def test_asking_for_a_window_the_scope_does_not_carry_names_the_ones_it_does(
    market, reservoir
) -> None:
    start, end = reservoir
    scope = recent_years(
        dataset="synthetic", available_start=start, available_end=end,
        years=(end - start).days / 365.25 / 2, rationale=WHY,
    )
    with pytest.raises(ProviderError) as exc:
        market.load_window(scope, "train")
    assert "no 'train' window" in str(exc.value)


def test_a_window_with_too_few_bars_is_refused_by_count(market, reservoir) -> None:
    """Sufficiency is checked here because here is where bars can be counted."""
    start, end = reservoir
    scope = fixed_range(
        dataset="synthetic", available_start=start, available_end=end,
        start=start, end=end, rationale=WHY,
    )
    plenty, _ = market.load_scope(scope)
    with pytest.raises(ProviderError) as exc:
        market.load_scope(scope, minimum_bars=len(plenty) + 1)
    assert "needs at least" in str(exc.value)


def test_padding_extends_a_window_backwards_only(market, reservoir) -> None:
    """A classifier may look further back; it may never look forward."""
    start, end = reservoir
    half = start + (end - start) / 2
    scope = fixed_range(
        dataset="synthetic", available_start=start, available_end=end,
        start=half, end=end, rationale=WHY,
    )
    plain, _ = market.load_scope(scope)
    padded, _ = market.load_scope(scope, pad_bars=200)
    assert len(padded) > len(plain)
    assert padded[0].event_time < plain[0].event_time
    assert padded[-1].event_time == plain[-1].event_time, "padding reached forwards"

"""Symbol and contract mapping, including the cases that over-size an account."""

from __future__ import annotations

from datetime import date

import pytest
from forge.propdesk import (
    Instrument,
    InstrumentCatalogue,
    MappingError,
    MappingPolicy,
    Provider,
    ProviderSymbol,
    RollWindow,
    Rounding,
    default_catalogue,
    parse_exchange_symbol,
)
from pydantic import ValidationError


@pytest.fixture
def catalogue() -> InstrumentCatalogue:
    return default_catalogue()


# ── specifications ───────────────────────────────────────────────────────────


def test_a_tick_value_that_disagrees_with_the_multiplier_is_refused() -> None:
    """Three numbers that must agree; two of them are usually typed by hand."""
    with pytest.raises(ValidationError, match="disagrees with"):
        Instrument(
            root="XX", exchange="CME", multiplier=20.0, tick_size=0.25, tick_value=9.0
        )


def test_the_shipped_micro_specifications_are_a_tenth_of_their_standard(
    catalogue,
) -> None:
    for micro_root in ("MNQ", "MGC", "MCL"):
        micro = catalogue.require(micro_root)
        standard = catalogue.require(micro.micro_of)
        assert micro.multiplier == pytest.approx(standard.multiplier / 10)


def test_unverified_specifications_are_marked_as_such(catalogue) -> None:
    """The research flagged these as not individually re-checked."""
    assert catalogue.require("YM").verified is False
    assert catalogue.require("RTY").verified is False
    assert catalogue.require("NQ").verified is True


# ── mapping policies ─────────────────────────────────────────────────────────


def test_count_cross_maps_one_standard_to_one_micro(catalogue) -> None:
    """What the commercial copiers call Cross Order: a tenth of the exposure."""
    mapped = catalogue.map_quantity(
        source_root="NQ",
        target_root="MNQ",
        source_quantity=1,
        policy=MappingPolicy.COUNT_CROSS,
    )
    assert mapped.quantity == 1
    assert mapped.source_notional == pytest.approx(20.0)
    assert mapped.target_notional == pytest.approx(2.0)


def test_notional_equivalent_maps_one_standard_to_ten_micros(catalogue) -> None:
    mapped = catalogue.map_quantity(
        source_root="NQ",
        target_root="MNQ",
        source_quantity=1,
        policy=MappingPolicy.NOTIONAL_EQUIVALENT,
    )
    assert mapped.quantity == 10
    assert mapped.exposure_error == pytest.approx(0.0)


def test_a_notional_mapping_refuses_an_unverified_specification(catalogue) -> None:
    """A multiplier wrong by ten is a position wrong by ten."""
    with pytest.raises(MappingError, match="not been verified"):
        catalogue.map_quantity(
            source_root="YM",
            target_root="MYM",
            source_quantity=1,
            policy=MappingPolicy.NOTIONAL_EQUIVALENT,
        )


def test_a_count_mapping_does_not_need_a_verified_specification(catalogue) -> None:
    """It reads no specification: one contract becomes one by definition."""
    mapped = catalogue.map_quantity(
        source_root="YM",
        target_root="MYM",
        source_quantity=2,
        policy=MappingPolicy.COUNT_CROSS,
    )
    assert mapped.quantity == 2
    assert mapped.exposure_error is None


def test_the_same_policy_refuses_to_change_product(catalogue) -> None:
    with pytest.raises(MappingError, match="cannot map"):
        catalogue.map_quantity(
            source_root="NQ",
            target_root="MNQ",
            source_quantity=1,
            policy=MappingPolicy.SAME,
        )


def test_risk_budget_sizing_needs_a_stop(catalogue) -> None:
    """Without a stop the worst case is unbounded, so no size follows."""
    with pytest.raises(MappingError, match="stop distance"):
        catalogue.map_quantity(
            source_root="MNQ",
            target_root="MNQ",
            source_quantity=1,
            policy=MappingPolicy.RISK_BUDGET,
            risk_budget=200.0,
        )


def test_risk_budget_sizing_buys_what_the_budget_covers(catalogue) -> None:
    mapped = catalogue.map_quantity(
        source_root="MNQ",
        target_root="MNQ",
        source_quantity=1,
        policy=MappingPolicy.RISK_BUDGET,
        rounding=Rounding.FLOOR,
        risk_budget=200.0,
        stop_ticks=40.0,
    )
    # 40 ticks at $0.50 is $20 a contract; $200 buys ten.
    assert mapped.quantity == 10


# ── rounding, and the over-size it can cause ─────────────────────────────────


def test_ceiling_rounding_over_sizes_a_half_multiplier_and_says_so(catalogue) -> None:
    """The behaviour one commercial product ships as its only option.

    A half-size follower takes a whole contract. That is defensible and it is a
    choice, and the exposure error is reported rather than left in a footnote.
    """
    mapped = catalogue.map_quantity(
        source_root="MNQ",
        target_root="MNQ",
        source_quantity=1,
        policy=MappingPolicy.SAME,
        multiplier=0.5,
        rounding=Rounding.CEIL,
    )
    assert mapped.quantity == 1
    assert mapped.exact_quantity == pytest.approx(0.5)
    assert mapped.exposure_error == pytest.approx(1.0)


def test_floor_rounding_can_round_a_follower_out_of_the_trade(catalogue) -> None:
    mapped = catalogue.map_quantity(
        source_root="MNQ",
        target_root="MNQ",
        source_quantity=1,
        policy=MappingPolicy.SAME,
        multiplier=0.5,
        rounding=Rounding.FLOOR,
    )
    assert mapped.quantity == 0
    assert mapped.deliverable is False


def test_nearest_rounding_sends_a_half_up_not_to_even(catalogue) -> None:
    """`round` is banker's rounding; a trader reading 'nearest' means half up."""
    mapped = catalogue.map_quantity(
        source_root="MNQ", target_root="MNQ", source_quantity=1,
        policy=MappingPolicy.SAME, multiplier=0.5, rounding=Rounding.NEAREST,
    )
    assert mapped.quantity == 1


def test_a_cap_binds_before_the_provider_would_reject(catalogue) -> None:
    mapped = catalogue.map_quantity(
        source_root="NQ",
        target_root="MNQ",
        source_quantity=1,
        policy=MappingPolicy.NOTIONAL_EQUIVALENT,
        max_contracts=3,
    )
    assert mapped.quantity == 3
    assert "capped at 3" in mapped.capped_by


def test_a_negative_multiplier_is_refused(catalogue) -> None:
    with pytest.raises(MappingError, match="cannot be negative"):
        catalogue.map_quantity(
            source_root="MNQ", target_root="MNQ", source_quantity=1,
            policy=MappingPolicy.SAME, multiplier=-1.0,
        )


# ── product groups, for the hedging rule ─────────────────────────────────────


def test_a_mini_and_its_micro_are_one_exposure(catalogue) -> None:
    """The granularity at which every researched firm writes its hedging rule."""
    assert catalogue.same_group("NQ", "MNQ") is True
    assert catalogue.same_group("ES", "MES") is True
    assert catalogue.same_group("NQ", "ES") is False


# ── provider symbols ─────────────────────────────────────────────────────────


def test_a_provider_identifier_must_be_discovered_not_constructed(catalogue) -> None:
    with pytest.raises(MappingError, match="discovered from the provider"):
        catalogue.resolve(Provider.PROJECTX, "NQU6")


def test_a_discovered_provider_identifier_resolves(catalogue) -> None:
    catalogue.record_symbol(
        ProviderSymbol(
            provider=Provider.PROJECTX,
            exchange_symbol="NQU6",
            provider_id="CON.F.US.ENQ.U26",
        )
    )
    assert catalogue.resolve(Provider.PROJECTX, "nqu6") == "CON.F.US.ENQ.U26"


# ── symbols and rolls ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("symbol", "root", "month", "year"),
    [("NQU6", "NQ", 9, 2026), ("MNQU6", "MNQ", 9, 2026), ("ESZ26", "ES", 12, 2026)],
)
def test_a_dated_symbol_parses(symbol, root, month, year) -> None:
    contract = parse_exchange_symbol(symbol)
    assert (contract.root, contract.expiry_month, contract.expiry_year) == (
        root,
        month,
        year,
    )


def test_a_symbol_without_a_month_code_is_refused() -> None:
    with pytest.raises(MappingError, match="not a dated exchange symbol"):
        parse_exchange_symbol("MNQ")


def test_a_roll_window_is_reported_for_the_days_it_covers(catalogue) -> None:
    catalogue.add_roll_window(
        RollWindow(root="NQ", opens=date(2026, 9, 10), closes=date(2026, 9, 12))
    )
    assert catalogue.roll_block("NQ", date(2026, 9, 11)) is not None
    assert catalogue.roll_block("NQ", date(2026, 9, 13)) is None
    assert catalogue.roll_block("ES", date(2026, 9, 11)) is None


def test_an_unknown_instrument_names_what_is_known(catalogue) -> None:
    with pytest.raises(MappingError, match="Known:"):
        catalogue.require("NOTAPRODUCT")

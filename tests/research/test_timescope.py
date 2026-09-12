"""Choosing the slice of history an experiment runs on, before it runs.

The engine had no temporal design at all. It loaded ``tail(max_bars)`` once per
run -- about nine months at the default, always the most recent nine months --
and every experiment in the campaign shared it. `Campaign.start_date` and
`end_date` were stored, returned by the API and read by nothing.

These tests pin the replacement, and above all the one property that makes it
research rather than search: **a window is part of the claim**, so moving it
after seeing a result is a moved claim and G1 already knows what to do with
those.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from forge.contracts.models import Preregistration
from forge.research.timescope import (
    MIN_RATIONALE,
    ScopeError,
    SelectionMethod,
    TimeScope,
    Window,
    exposure,
    fixed_range,
    full_history,
    partitioned,
    recent_years,
    regimes,
    rolling,
)
from pydantic import ValidationError

# A sixteen-year reservoir, as the brief describes it.
A0 = datetime(2009, 1, 1, tzinfo=UTC)
A1 = datetime(2026, 1, 1, tzinfo=UTC)
DS = "nq_1m_16y"

WHY_RECENT = (
    "Recent intraday microstructure: execution conditions and the volatility regime "
    "have both changed since 2020, so older bars describe a different market."
)
WHY_LONG = (
    "A day-of-week seasonal effect needs many observations of each weekday, so the "
    "full available history is used rather than a recent slice."
)


def _recent(years: float = 2.0, **kw) -> TimeScope:
    return recent_years(
        dataset=DS, available_start=A0, available_end=A1, years=years,
        rationale=WHY_RECENT, **kw,
    )


# ── the reservoir is not the window ──────────────────────────────────────────


def test_a_two_year_window_out_of_sixteen_records_both() -> None:
    """The choice is only visible if what was *not* used is recorded too."""
    scope = _recent(2)
    assert scope.selected_years == pytest.approx(2.0, abs=0.01)
    assert scope.available_days > scope.selected_days
    assert scope.coverage < 0.13
    assert "of 17.0y available" in scope.describe()


def test_full_history_is_a_decision_and_says_why() -> None:
    scope = full_history(
        dataset=DS, available_start=A0, available_end=A1, rationale=WHY_LONG
    )
    assert scope.method is SelectionMethod.FULL_AVAILABLE_HISTORY
    assert scope.coverage == pytest.approx(1.0)
    # It carries a reason exactly like every other method. "All of it" is a
    # choice, not the absence of one.
    assert scope.rationale == WHY_LONG


def test_different_hypotheses_legitimately_get_different_windows() -> None:
    """The brief's central claim, as a property rather than a promise."""
    micro = _recent(2)
    seasonal = full_history(
        dataset=DS, available_start=A0, available_end=A1, rationale=WHY_LONG
    )
    assert micro.fingerprint() != seasonal.fingerprint()
    assert micro.selected_start > seasonal.selected_start


def test_a_window_with_no_stated_reason_is_refused() -> None:
    """A rationale shorter than a sentence is a label, not a reason."""
    with pytest.raises(ValidationError):
        recent_years(
            dataset=DS, available_start=A0, available_end=A1, years=2, rationale="recent",
        )
    assert MIN_RATIONALE > 20


def test_asking_for_more_history_than_exists_is_refused_not_truncated(tmp_path) -> None:
    """Silently returning less than was asked for is how nine months became the answer."""
    with pytest.raises(ScopeError) as exc:
        recent_years(
            dataset=DS, available_start=datetime(2024, 1, 1, tzinfo=UTC),
            available_end=A1, years=10, rationale=WHY_LONG,
        )
    assert "only" in str(exc.value) and "available" in str(exc.value)


def test_a_window_outside_the_reservoir_is_refused() -> None:
    with pytest.raises(ValidationError) as exc:
        fixed_range(
            dataset=DS, available_start=A0, available_end=A1,
            start=datetime(2005, 1, 1, tzinfo=UTC), end=datetime(2010, 1, 1, tzinfo=UTC),
            rationale=WHY_LONG,
        )
    assert "cannot reach data that does not exist" in str(exc.value)


def test_the_scope_holds_no_minimum_length_because_it_cannot_know_one() -> None:
    """Sufficiency is a bar count, and this module does not read data.

    The same twenty days is nearly six thousand one-minute bars and twenty
    daily ones. A length guard here would be a bar-rate assumption in a module
    whose whole claim is that it makes none — so a short window is accepted
    here and refused where it can be counted: `MarketService.load_scope` on a
    `minimum_bars`, and `chronological_split` with INSUFFICIENT_SPLIT_BARS.
    """
    short = fixed_range(
        dataset=DS, available_start=A0, available_end=A1,
        start=A1 - timedelta(days=2), end=A1, rationale=WHY_RECENT,
    )
    assert short.selected_days == pytest.approx(2.0)


# ── leakage ──────────────────────────────────────────────────────────────────


def test_a_holdout_overlapping_the_train_window_is_refused() -> None:
    """Not a weaker experiment -- a different and untrue one."""
    with pytest.raises(ValidationError) as exc:
        partitioned(
            dataset=DS, available_start=A0, available_end=A1,
            train=(datetime(2020, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
            validation=(datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
            holdout=(datetime(2023, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
            rationale=WHY_LONG,
        )
    assert "fitted on" in str(exc.value) or "before the train window ends" in str(exc.value)


def test_a_validation_window_before_the_train_window_is_refused() -> None:
    with pytest.raises(ValidationError):
        partitioned(
            dataset=DS, available_start=A0, available_end=A1,
            train=(datetime(2022, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
            validation=(datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC)),
            holdout=(datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
            rationale=WHY_LONG,
        )


def test_a_clean_three_way_split_is_accepted_and_ordered() -> None:
    scope = partitioned(
        dataset=DS, available_start=A0, available_end=A1,
        train=(datetime(2020, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
        validation=(datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
        holdout=(datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        rationale=WHY_LONG,
    )
    assert scope.window_for("train").end <= scope.window_for("validation").start
    assert scope.window_for("validation").end <= scope.window_for("holdout").start


# ── walk-forward ─────────────────────────────────────────────────────────────


def test_rolling_folds_both_move_forward() -> None:
    scope = rolling(
        dataset=DS, available_start=A0, available_end=A1,
        train_months=24, test_months=6, folds=3, rationale=WHY_LONG,
    )
    assert scope.method is SelectionMethod.ROLLING
    trains = [w for w in scope.windows if w.role.endswith("-train")]
    assert len(trains) == 3
    assert trains[0].start < trains[1].start < trains[2].start


def test_anchored_folds_keep_one_start_and_grow() -> None:
    scope = rolling(
        dataset=DS, available_start=A0, available_end=A1,
        train_months=24, test_months=6, folds=3, rationale=WHY_LONG, anchored=True,
    )
    assert scope.method is SelectionMethod.ANCHORED
    trains = [w for w in scope.windows if w.role.endswith("-train")]
    assert len({w.start for w in trains}) == 1, "an anchored train start moved"
    assert trains[0].end < trains[1].end < trains[2].end


def test_every_fold_tests_after_it_trains() -> None:
    scope = rolling(
        dataset=DS, available_start=A0, available_end=A1,
        train_months=12, test_months=6, folds=4, rationale=WHY_LONG,
    )
    for fold in range(1, 5):
        train = scope.window_for(f"fold-{fold}-train")
        test = scope.window_for(f"fold-{fold}-test")
        assert test.start >= train.end, f"fold {fold} tests on bars it trained on"


def test_a_walk_forward_longer_than_the_reservoir_is_refused() -> None:
    with pytest.raises(ScopeError) as exc:
        rolling(
            dataset=DS, available_start=datetime(2024, 1, 1, tzinfo=UTC), available_end=A1,
            train_months=24, test_months=6, folds=4, rationale=WHY_LONG,
        )
    assert "available" in str(exc.value)


# ── regimes ──────────────────────────────────────────────────────────────────


def test_a_regime_scope_must_name_its_regimes() -> None:
    """"Regime-selected" without naming the regime is a gesture at a selection."""
    with pytest.raises(ValidationError) as exc:
        TimeScope(
            dataset=DS, available_start=A0, available_end=A1,
            selected_start=datetime(2020, 1, 1, tzinfo=UTC), selected_end=A1,
            method=SelectionMethod.CROSS_REGIME, rationale=WHY_LONG,
            selected_at=datetime.now(UTC),
        )
    assert "must name" in str(exc.value)


def test_cross_regime_carries_each_regime_and_its_span() -> None:
    scope = regimes(
        dataset=DS, available_start=A0, available_end=A1,
        named=("2015 low volatility", "2020 crisis", "2022 rate shock"),
        spans=(
            (datetime(2015, 1, 1, tzinfo=UTC), datetime(2016, 1, 1, tzinfo=UTC)),
            (datetime(2020, 2, 1, tzinfo=UTC), datetime(2020, 8, 1, tzinfo=UTC)),
            (datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
        ),
        rationale=(
            "The claim is that the effect survives regime change, so it is tested in "
            "three deliberately different historical environments."
        ),
    )
    assert scope.method is SelectionMethod.CROSS_REGIME
    assert len(scope.segments) == 3
    assert scope.window_for("regime-2").selector == "2020 crisis"


# ── identity, and the reason it exists ───────────────────────────────────────


def test_the_fingerprint_ignores_who_chose_it_and_when() -> None:
    """Two experiments over the same dates are temporally identical."""
    a = _recent(2, selected_by="agent-1")
    b = _recent(2, selected_by="operator")
    assert a.fingerprint() == b.fingerprint()
    # And the fields that differ really are different, so the equality above is
    # the fingerprint ignoring them rather than there being nothing to ignore.
    assert a.selected_by != b.selected_by
    assert a.rationale == b.rationale


def test_a_different_window_is_a_different_fingerprint() -> None:
    assert _recent(2).fingerprint() != _recent(5).fingerprint()


def test_a_scope_in_the_claim_makes_a_moved_window_a_moved_claim() -> None:
    """The whole integration, in one test.

    G1 re-derives the preregistration hash at judge time and fails CLAIM_MOVED
    when it differs. Folding the scope fingerprint into that payload means
    "ran two years, disliked it, reported the five-year number" fails G1
    without a second gate, a second freeze, or a change to the ladder.
    """
    frozen = datetime(2026, 1, 1, tzinfo=UTC)
    claim = dict(
        hypothesis="Compressed overnight range precedes directional expansion",
        mechanism="liquidity provision withdraws into the open",
        falsification="no expansion edge after costs",
        frozen_at=frozen,
    )
    two_year = Preregistration.freeze(**claim, scope_fingerprint=_recent(2).fingerprint())
    five_year = Preregistration.freeze(**claim, scope_fingerprint=_recent(5).fingerprint())
    assert two_year.content_hash != five_year.content_hash


def test_a_claim_with_no_scope_hashes_exactly_as_it_always_did() -> None:
    """Backward compatibility, and it is not a nicety.

    Folding the scope in unconditionally would change the hash of every claim
    frozen before time scopes existed, and G1 would report CLAIM_MOVED for
    every strategy in the library -- invalidating evidence nobody touched.
    """
    frozen = datetime(2026, 1, 1, tzinfo=UTC)
    args = ("a hypothesis long enough to pass", "a mechanism", "a falsification", frozen)
    assert Preregistration.freeze(*args).content_hash == Preregistration.freeze(
        *args, scope_fingerprint=""
    ).content_hash


# ── selection exposure ───────────────────────────────────────────────────────


def test_trying_several_windows_counts_as_selection() -> None:
    """Searching windows is searching."""
    report = exposure([_recent(2), _recent(5), _recent(10)])
    assert report["windows_tried"] == 3
    assert report["counts_as_selection"] is True
    assert "Multiple-testing" in report["note"]


def test_re_running_the_identical_window_is_not_a_new_look() -> None:
    report = exposure([_recent(2), _recent(2), _recent(2)])
    assert report["windows_tried"] == 1
    assert report["counts_as_selection"] is False


def test_one_window_adds_no_exposure() -> None:
    assert exposure([_recent(2)])["counts_as_selection"] is False


def test_alternatives_considered_are_recorded_on_the_scope() -> None:
    """Exposure is only countable if the alternatives were written down."""
    scope = _recent(2, alternatives=("5 years", "full history"))
    assert scope.alternatives_considered == ("5 years", "full history")
    assert "alternatives_considered" in scope.as_dict()


# ── shape ────────────────────────────────────────────────────────────────────


def test_a_window_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValidationError):
        Window(role="train", start=A1, end=A0)


def test_the_payload_carries_the_numbers_a_surface_needs() -> None:
    payload = _recent(2).as_dict()
    assert {"scope_id", "fingerprint", "selected_years", "coverage", "summary"} <= set(payload)
    assert payload["coverage"] < 0.2

"""The findings a regime report supports, and the ones it must refuse.

The risk this module exists to hold down is not a wrong number: every figure in
a `Reading` is copied or divided out of a `RegimeReport` that has its own tests.
The risk is a *sentence that is produced regardless* — a claim about where an
edge lives, rendered identically whether the evidence exists or not. So most of
what is asserted here is silence: a report with no concentration produces no
SOURCE finding, a thin cell produces no rate claim, and a full-sample basis
downgrades everything rather than reading the same way a trailing one does.
"""

from __future__ import annotations

import pytest
from forge.analytics.reading import (
    CONCENTRATION_LIMIT,
    MIN_TRANSITIONS,
    Evidence,
    Kind,
    Standing,
    read,
)
from forge.analytics.regime import (
    MEASURED,
    MIN_TRADES_FOR_ESTIMATE,
    SHORT_LABEL,
    Basis,
    Regime,
    RegimeCell,
    RegimeReport,
    RegimeSettings,
)


def cell(
    regime: Regime,
    *,
    trades: int = 60,
    net: float = 0.0,
    gross: float | None = None,
    exposure: float = 0.25,
    share: float = 0.25,
    win_rate: float | None = 0.55,
) -> RegimeCell:
    return RegimeCell(
        regime=regime,
        label=SHORT_LABEL[regime],
        trade_count=trades,
        net_pnl=net,
        gross_pnl=net if gross is None else gross,
        win_rate=win_rate if trades else None,
        average_trade=(net / trades) if trades else None,
        bar_exposure=exposure,
        trade_share=share,
        insufficient=trades < MIN_TRADES_FOR_ESTIMATE,
    )


def report(
    cells: list[RegimeCell],
    *,
    basis: Basis = Basis.TRAILING,
    transitions: tuple[tuple[int, ...], ...] | None = None,
    concentration: float | None = None,
    concentration_regime: Regime | None = None,
    unclassified: int = 0,
    coverage: float = 0.98,
) -> RegimeReport:
    total = sum(c.trade_count for c in cells) + unclassified
    flat = transitions or tuple(tuple(0 for _ in MEASURED) for _ in MEASURED)
    return RegimeReport(
        attribution="entry",
        settings=RegimeSettings(basis=basis),
        series_fingerprint="fingerprint",
        total_trades=total,
        classified_trades=total - unclassified,
        unclassified_trades=unclassified,
        coverage=coverage,
        cells=tuple(cells),
        transitions=flat,
        transition_labels=tuple(SHORT_LABEL[r] for r in MEASURED),
        concentration=concentration,
        concentration_regime=concentration_regime,
    )


def kinds(reading: object) -> set[Kind]:
    return {finding.kind for finding in reading.findings}  # type: ignore[attr-defined]


def find(reading: object, kind: Kind) -> object:
    return next(f for f in reading.findings if f.kind is kind)  # type: ignore[attr-defined]


# ── the concentrated edge ────────────────────────────────────────────────────


def test_a_concentrated_edge_is_named_with_the_share_it_rests_on() -> None:
    cells = [
        cell(Regime.BULL_LOW, trades=90, net=9_000.0, exposure=0.30),
        cell(Regime.BULL_HIGH, trades=40, net=500.0, exposure=0.25),
        cell(Regime.BEAR_LOW, trades=35, net=300.0, exposure=0.25),
        cell(Regime.BEAR_HIGH, trades=30, net=200.0, exposure=0.20),
    ]
    reading = read(
        report(cells, concentration=0.90, concentration_regime=Regime.BULL_LOW)
    )
    source = find(reading, Kind.SOURCE)
    assert "90.0%" in source.fact  # type: ignore[attr-defined]
    assert "concentrated" in source.interpretation  # type: ignore[attr-defined]
    assert source.regimes == (Regime.BULL_LOW,)  # type: ignore[attr-defined]


def test_a_spread_edge_is_not_described_as_concentrated() -> None:
    """The same code path, the opposite sentence.

    A summariser that always says "concentrated" is the failure this whole
    module is built to avoid, so the negative case is asserted explicitly
    rather than left to the positive one.
    """
    cells = [
        cell(Regime.BULL_LOW, trades=60, net=2_600.0),
        cell(Regime.BULL_HIGH, trades=60, net=2_500.0),
        cell(Regime.BEAR_LOW, trades=60, net=2_400.0),
        cell(Regime.BEAR_HIGH, trades=60, net=2_300.0),
    ]
    share = 2_600.0 / 9_800.0
    assert share < CONCENTRATION_LIMIT
    reading = read(
        report(cells, concentration=share, concentration_regime=Regime.BULL_LOW)
    )
    source = find(reading, Kind.SOURCE)
    assert "concentrated" not in source.interpretation  # type: ignore[attr-defined]
    assert "spread" in source.interpretation  # type: ignore[attr-defined]


def test_no_profitable_regime_produces_no_source_finding() -> None:
    cells = [cell(regime, trades=60, net=-500.0) for regime in MEASURED]
    reading = read(report(cells))
    assert Kind.SOURCE not in kinds(reading)


# ── refusals ─────────────────────────────────────────────────────────────────


def test_every_number_in_a_fact_is_also_carried_as_evidence() -> None:
    """A sentence is not allowed to be the only place a figure appears.

    Progressive disclosure only works if the layer underneath is actually
    there: a reader who does not believe the claim has to be able to reach the
    numbers without re-deriving them.
    """
    cells = [
        cell(Regime.BULL_LOW, trades=90, net=9_000.0, exposure=0.30),
        cell(Regime.BULL_HIGH, trades=40, net=-2_000.0, exposure=0.25),
        cell(Regime.BEAR_LOW, trades=35, net=300.0, exposure=0.25),
        cell(Regime.BEAR_HIGH, trades=30, net=200.0, exposure=0.20),
    ]
    reading = read(
        report(cells, concentration=0.90, concentration_regime=Regime.BULL_LOW)
    )
    assert reading.findings
    for finding in reading.findings:
        assert finding.evidence, f"{finding.kind} carries a claim with no evidence"
        for item in finding.evidence:
            assert isinstance(item, Evidence)
            assert item.display


def test_a_full_sample_basis_downgrades_every_finding_to_descriptive() -> None:
    """Same arithmetic, different standing.

    A full-sample volatility threshold labels a bar using the distribution of
    bars that came after it. The findings are still exactly true of the run
    that was produced; they are not claims about what the strategy would have
    done, and nothing in the reading may present them as though they were.
    """
    cells = [
        cell(Regime.BULL_LOW, trades=90, net=9_000.0),
        cell(Regime.BULL_HIGH, trades=60, net=500.0),
        cell(Regime.BEAR_LOW, trades=60, net=300.0),
        cell(Regime.BEAR_HIGH, trades=60, net=200.0),
    ]
    trailing = read(
        report(cells, concentration=0.90, concentration_regime=Regime.BULL_LOW)
    )
    descriptive = read(
        report(
            cells,
            basis=Basis.FULL_SAMPLE,
            concentration=0.90,
            concentration_regime=Regime.BULL_LOW,
        )
    )
    assert trailing.standing is Standing.MEASURED
    assert descriptive.standing is Standing.DESCRIPTIVE
    assert all(f.standing is Standing.DESCRIPTIVE for f in descriptive.findings)
    # The facts themselves are unchanged; only what may be concluded moves.
    assert [f.fact for f in trailing.findings] == [f.fact for f in descriptive.findings]


def test_a_thin_cell_never_produces_a_rate_claim() -> None:
    thin = MIN_TRADES_FOR_ESTIMATE - 1
    cells = [
        cell(Regime.BULL_LOW, trades=90, net=9_000.0),
        cell(Regime.BULL_HIGH, trades=thin, net=-900.0),
        cell(Regime.BEAR_LOW, trades=60, net=300.0),
        cell(Regime.BEAR_HIGH, trades=60, net=200.0),
    ]
    reading = read(
        report(cells, concentration=0.90, concentration_regime=Regime.BULL_LOW)
    )
    drag = find(reading, Kind.DRAG)
    assert drag.standing is Standing.THIN  # type: ignore[attr-defined]
    assert "too small" in drag.interpretation  # type: ignore[attr-defined]
    assert reading.standing is Standing.THIN


# ── persistence ──────────────────────────────────────────────────────────────


def test_persistence_is_refused_below_the_transition_floor() -> None:
    """A 95% built on forty bars is an order statistic, not a rate."""
    few = MIN_TRANSITIONS - 1
    transitions = tuple(
        tuple(few if (i, j) == (0, 0) else 0 for j in range(len(MEASURED)))
        for i in range(len(MEASURED))
    )
    cells = [cell(regime, trades=60, net=1_000.0) for regime in MEASURED]
    reading = read(
        report(
            cells,
            transitions=transitions,
            concentration=0.90,
            concentration_regime=Regime.BULL_LOW,
        )
    )
    assert Kind.PERSISTENCE not in kinds(reading)


def test_persistence_reports_the_rate_and_the_count_it_came_from() -> None:
    stayed, left = 9_500, 500
    transitions = tuple(
        tuple(
            stayed if (i, j) == (0, 0) else left if (i, j) == (0, 1) else 0
            for j in range(len(MEASURED))
        )
        for i in range(len(MEASURED))
    )
    cells = [cell(regime, trades=60, net=1_000.0) for regime in MEASURED]
    reading = read(
        report(
            cells,
            transitions=transitions,
            concentration=0.90,
            concentration_regime=Regime.BULL_LOW,
        )
    )
    persistence = find(reading, Kind.PERSISTENCE)
    labels = {item.label for item in persistence.evidence}  # type: ignore[attr-defined]
    assert labels == {"stay probability", "observed transitions"}
    assert "95.0%" in persistence.fact  # type: ignore[attr-defined]
    assert "10,000" in persistence.fact  # type: ignore[attr-defined]


# ── the empty corners ────────────────────────────────────────────────────────


def test_a_regime_the_strategy_never_traded_is_reported_as_untested() -> None:
    """The empty cell is the answer, not the absence of one."""
    cells = [
        cell(Regime.BULL_LOW, trades=90, net=9_000.0, exposure=0.55),
        cell(Regime.BULL_HIGH, trades=60, net=500.0, exposure=0.25),
        cell(Regime.BEAR_LOW, trades=60, net=300.0, exposure=0.15),
        cell(Regime.BEAR_HIGH, trades=0, net=0.0, exposure=0.05, win_rate=None),
    ]
    reading = read(
        report(cells, concentration=0.90, concentration_regime=Regime.BULL_LOW)
    )
    untested = find(reading, Kind.UNTESTED)
    assert Regime.BEAR_HIGH in untested.regimes  # type: ignore[attr-defined]
    assert "not been tried" in untested.implication  # type: ignore[attr-defined]


def test_a_fully_sampled_grid_produces_no_untested_finding() -> None:
    cells = [cell(regime, trades=60, net=1_000.0) for regime in MEASURED]
    reading = read(
        report(cells, concentration=0.30, concentration_regime=Regime.BULL_LOW)
    )
    assert Kind.UNTESTED not in kinds(reading)


# ── exposure that is not paid for ────────────────────────────────────────────


def test_time_spent_without_profit_is_stated_separately_from_a_loss() -> None:
    """Exposure and contribution are different facts.

    A regime holding 40% of the clock and 4% of the profit is not visible in a
    P&L column at all — it is positive there — which is exactly why it needs
    its own finding.
    """
    cells = [
        cell(Regime.BULL_LOW, trades=90, net=9_000.0, exposure=0.30),
        cell(Regime.BULL_HIGH, trades=40, net=400.0, exposure=0.40),
        cell(Regime.BEAR_LOW, trades=60, net=300.0, exposure=0.20),
        cell(Regime.BEAR_HIGH, trades=60, net=300.0, exposure=0.10),
    ]
    reading = read(
        report(cells, concentration=0.90, concentration_regime=Regime.BULL_LOW)
    )
    mismatch = find(reading, Kind.MISMATCH)
    assert mismatch.regimes == (Regime.BULL_HIGH,)  # type: ignore[attr-defined]
    assert "40.0%" in mismatch.fact  # type: ignore[attr-defined]


def test_a_losing_total_produces_no_mismatch_finding() -> None:
    """Share-of-profit is undefined when there is no profit to share."""
    cells = [cell(regime, trades=60, net=-1_000.0, exposure=0.25) for regime in MEASURED]
    reading = read(report(cells))
    assert Kind.MISMATCH not in kinds(reading)


# ── coverage ─────────────────────────────────────────────────────────────────


def test_unplaced_trades_are_surfaced_rather_than_absorbed() -> None:
    cells = [cell(regime, trades=60, net=1_000.0) for regime in MEASURED]
    reading = read(report(cells, unclassified=40, coverage=0.72))
    coverage = find(reading, Kind.COVERAGE)
    assert "40 of 280" in coverage.fact  # type: ignore[attr-defined]
    assert "72.0%" in coverage.fact  # type: ignore[attr-defined]


def test_full_coverage_produces_no_coverage_finding() -> None:
    cells = [cell(regime, trades=60, net=1_000.0) for regime in MEASURED]
    reading = read(report(cells, unclassified=0, coverage=1.0))
    assert Kind.COVERAGE not in kinds(reading)


# ── determinism ──────────────────────────────────────────────────────────────


def test_the_same_report_reads_identically_twice() -> None:
    """A surface may cache a reading, and a test may assert on one."""
    cells = [
        cell(Regime.BULL_LOW, trades=90, net=9_000.0, exposure=0.30),
        cell(Regime.BULL_HIGH, trades=40, net=-2_000.0, exposure=0.40),
        cell(Regime.BEAR_LOW, trades=35, net=300.0, exposure=0.20),
        cell(Regime.BEAR_HIGH, trades=5, net=200.0, exposure=0.10),
    ]
    built = report(cells, concentration=0.88, concentration_regime=Regime.BULL_LOW)
    assert read(built).model_dump() == read(built).model_dump()


@pytest.mark.parametrize("basis", list(Basis))
def test_a_reading_never_invents_a_finding_for_an_empty_report(basis: Basis) -> None:
    cells = [cell(regime, trades=0, net=0.0, exposure=0.25, win_rate=None) for regime in MEASURED]
    reading = read(report(cells, basis=basis, coverage=1.0))
    # UNTESTED is the one thing an empty grid does support, and it is the
    # honest one: nothing has been observed anywhere.
    assert kinds(reading) == {Kind.UNTESTED}

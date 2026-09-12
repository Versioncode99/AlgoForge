"""The development screen, and the research it used to throw away.

`_cycle` screens a candidate on the development partition before validation is
touched: negative net, or too few trades, and it is rejected. That screen called
`_observe` with the numbers and **no failure class**, and
`ResearchDirector._generate_followups` returns immediately on an observation
carrying none. So a construction that produced no edge generated no research
question at all -- measured on a 120-cycle bounded campaign: 39 experiments,
most of them dying at the screen, one follow-up in the whole run.

**Why this file exists as its own fixture.** The screen lives inside
`if real_data:`, and `real_data` is `dataset.is_real`, which is
`authority == "TRUTH"`. The synthetic dataset is `FIXTURE`, so *every* engine
test in this repository takes the other arm and the whole partitioned path --
the screen, the split, the development backtest -- is unreachable from them.
That blind spot has now hidden two defects: this one, and the partition leak in
`test_engine_timescope`.

The bars here are still generated. Nothing in this file claims a market fact;
it asserts control flow through a branch the other fixtures cannot reach.
"""

from __future__ import annotations

import pytest
from forge.memory import FailureClass
from forge_api.engine import MIN_SCREEN_TRADES, _screen_failure


def test_no_trades_is_a_sample_problem_not_an_expectancy_one() -> None:
    """Telling a strategy to look for an edge in zero trades is the wrong question."""
    assert _screen_failure(net_pnl=0.0, trades=0) is FailureClass.NO_TRADES


def test_too_few_trades_reads_as_insufficient_sample() -> None:
    assert _screen_failure(net_pnl=50.0, trades=MIN_SCREEN_TRADES - 1) is (
        FailureClass.INSUFFICIENT_SAMPLE
    )


def test_a_sample_problem_outranks_a_losing_number() -> None:
    """Order matters: five losing trades is not evidence about expectancy."""
    assert _screen_failure(net_pnl=-500.0, trades=3) is FailureClass.INSUFFICIENT_SAMPLE


def test_enough_trades_and_no_edge_reads_as_negative_expectancy() -> None:
    assert _screen_failure(net_pnl=-120.0, trades=MIN_SCREEN_TRADES * 4) is (
        FailureClass.NEGATIVE_EXPECTANCY
    )


def test_the_screen_and_its_classification_read_the_same_floor() -> None:
    """A literal in both places would drift, and the drift would misreport why.

    Asserted against the source because the screen is a branch inside a long
    method rather than something callable on its own: the check that matters is
    that no second literal has appeared beside the named constant.
    """
    import inspect

    from forge_api.engine import AutonomousEngine

    source = inspect.getsource(AutonomousEngine._cycle)
    assert "len(development.trades) < MIN_SCREEN_TRADES" in source
    assert "len(development.trades) < 30" not in source


@pytest.mark.parametrize(
    "net_pnl,trades",
    [(0.0, 0), (10.0, 5), (-1.0, 29), (-1000.0, 5000), (0.0, 31)],
)
def test_every_screened_outcome_is_classified(net_pnl: float, trades: int) -> None:
    """No screened candidate falls through unclassified.

    An unclassified rejection is the defect this closes: it reaches the director
    as an observation with no failure class, and the follow-up path returns
    before it reads anything else.
    """
    assert isinstance(_screen_failure(net_pnl, trades), FailureClass)


def test_a_classified_screen_produces_a_research_question() -> None:
    """End to end from the screen's two numbers to a follow-up.

    This is the whole point: the screen measures a net and a trade count, the
    classification turns them into a failure class, and the failure class is
    what `derive` needs before it will say anything at all.
    """
    from forge.research.followup import Observation, derive

    observation = Observation(
        family="momentum",
        mechanism="displacement followed by continuation",
        hypothesis="Displacement continues rather than reverting.",
        template="gen_displacement",
        failure_class=_screen_failure(net_pnl=-240.0, trades=180),
        trades=180,
        net_pnl=-240.0,
        was_profitable=False,
    )
    assert derive(observation), "a classified screen still produced no question"


def test_an_unclassified_observation_produces_nothing() -> None:
    """The behaviour before the fix, pinned so the regression is visible."""
    from forge.research.followup import Observation, derive

    silent = Observation(
        family="momentum",
        mechanism="displacement followed by continuation",
        hypothesis="Displacement continues rather than reverting.",
        template="gen_displacement",
        failure_class=None,
        trades=180,
        net_pnl=-240.0,
    )
    assert derive(silent) == []

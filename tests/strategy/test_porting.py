"""Carrying a strategy to another platform, and what the port may claim about it.

The dangerous failure in a translator is not a syntax error — that surfaces the
moment somebody pastes the file. It is a translator that produces something
plausible and describes it as faithful, so a person runs a strategy that is not
the one they validated and finds out by losing money.

So the assertions here are mostly about claims rather than about code:

  - a target this machine cannot execute can never reach VERIFIED, whatever the
    strategy;
  - one unsupported element drags the whole port to INCOMPLETE, because a
    strategy missing an entry condition is not a mostly-correct strategy;
  - every approximation names its difference, since an approximation with no
    stated reason is indistinguishable from one nobody looked at;
  - the phrase "logic preserved" appears nowhere below VERIFIED.

The Pine emitter is checked for structure rather than for exact text. Asserting
the literal output would make every future improvement a test edit, and would
not catch the thing that matters — that what came out says what the definition
says.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.strategy.ir import (
    Combine,
    Compare,
    Constant,
    Cross,
    DefinitionProvenance,
    EntryRules,
    ExitRules,
    Feature,
    FeatureRef,
    Level,
    ParamRef,
    SessionWindow,
    StrategyDefinition,
)
from forge.strategy.models import ParameterSpec
from forge.strategy.porting import (
    TARGET_KEYS,
    TARGETS,
    Fidelity,
    Status,
    analyse,
    port,
    target,
    to_pine,
    verified,
)

NOW = datetime(2026, 9, 13, tzinfo=UTC)

HYPOTHESIS = (
    "A close above the prior N-bar high reflects liquidity-taking flow that must be "
    "absorbed over subsequent bars, so short-horizon continuation should follow."
)
PREDICTION = (
    "Continuation must be stronger on above-median volume; if it is not, the stated "
    "absorption mechanism is wrong."
)


def definition(**overrides: object) -> StrategyDefinition:
    base: dict[str, object] = {
        "name": "Port Breakout",
        "family": "breakout",
        "symbol": "MNQ",
        "hypothesis": HYPOTHESIS,
        "falsifiable_prediction": PREDICTION,
        "features": (
            Feature(name="prior_high", kind="highest", args=(ParamRef(name="lookback"),), shift=1),
            Feature(name="atr14", kind="atr", args=(Constant(value=14),)),
            Feature(name="last_close", kind="close"),
        ),
        "entry": EntryRules(
            long=Compare(
                op="gt", left=FeatureRef(name="last_close"), right=FeatureRef(name="prior_high")
            )
        ),
        "exit": ExitRules(
            stop=Level(kind="feature", multiple=ParamRef(name="stop_atr"), feature="atr14"),
            max_bars=ParamRef(name="max_bars"),
        ),
        "parameters": (
            ParameterSpec(name="lookback", default=20, low=5, high=60, step=5),
            ParameterSpec(name="stop_atr", default=2.0, low=0.5, high=5.0, step=0.5),
            ParameterSpec(name="max_bars", default=12, low=3, high=60, step=3),
        ),
        "provenance": DefinitionProvenance(created_at=NOW, derived_from="test"),
    }
    base.update(overrides)
    return StrategyDefinition(**base)  # type: ignore[arg-type]


# ── what a target may claim ──────────────────────────────────────────────────


def test_a_target_this_machine_cannot_run_can_never_be_verified() -> None:
    """The ceiling is a property of the machine, not of the strategy.

    A strategy that maps perfectly to Pine is STRUCTURAL, because nothing
    executed it. Without the ceiling, a clean mapping would look like a checked
    one, which is precisely the claim nobody can support.
    """
    clean = definition()
    for key in ("pine", "ninjascript", "mql5"):
        report = port(clean, key)
        assert report.status is not Status.VERIFIED, key
        assert target(key).ceiling is not Status.VERIFIED


def test_the_python_target_is_only_verified_by_an_actual_comparison() -> None:
    """VERIFIED is not reachable by inspecting a definition.

    `port` can only ever reach STRUCTURAL; the upgrade requires the result of a
    real run to be handed in, which is why it lives in a separate function that
    takes one.
    """
    clean = definition()
    assert port(clean, "python").status is Status.STRUCTURAL
    assert verified(clean, {"identical": True}).status is Status.VERIFIED


def test_a_comparison_that_did_not_match_does_not_upgrade_anything() -> None:
    report = verified(definition(), {"identical": False, "note": "3 trades differ"})
    assert report.status is not Status.VERIFIED
    assert any("did NOT match" in note for note in report.notes)


@pytest.mark.parametrize("key", TARGET_KEYS)
def test_no_port_below_verified_claims_the_logic_survived(key: str) -> None:
    """The sentence a translator must not be able to produce on its own.

    Checked as the absence of every affirmative phrasing rather than of the
    words themselves, because the disclaimer necessarily contains them: the
    report says it does *not* claim preservation, and a naive substring test
    would fail on its own denial.
    """
    report = port(definition(), key)
    if report.status is Status.VERIFIED:
        return
    body = " ".join(report.notes).lower() + " " + report.code.lower()
    for affirmative in (
        "logic preserved",
        "logic has been preserved",
        "semantics preserved",
        "semantically equivalent",
        "identical to the original",
        "faithful translation",
    ):
        assert affirmative not in body, affirmative
    assert any("claims the logic is preserved" in note for note in report.notes)


def test_an_unknown_target_is_refused_with_the_valid_set() -> None:
    with pytest.raises(ValueError, match="python, pine, ninjascript, mql5"):
        port(definition(), "metatrader4")


# ── the status is the worst element ──────────────────────────────────────────


def test_one_unsupported_feature_drags_the_whole_port_down() -> None:
    """A strategy missing an entry input is not a mostly-correct strategy."""
    with_orb = definition(
        features=(
            Feature(name="orh", kind="opening_range_high", args=(Constant(value=15),)),
            Feature(name="last_close", kind="close"),
        ),
        entry=EntryRules(
            long=Compare(op="gt", left=FeatureRef(name="last_close"), right=FeatureRef(name="orh"))
        ),
        exit=ExitRules(max_bars=Constant(value=10)),
        parameters=(),
    )
    report = port(with_orb, "pine")
    assert report.status is Status.INCOMPLETE
    assert [e.name for e in report.unsupported] == ["orh (opening_range_high)"]


def test_an_approximation_alone_lands_on_approximate() -> None:
    report = port(definition(), "pine")
    # The execution model is always approximated on Pine, so a clean strategy
    # still cannot claim a clean crossing.
    assert report.status is Status.APPROXIMATE
    assert not report.unsupported
    assert report.approximated


def test_every_approximation_names_its_difference() -> None:
    """An approximation with no stated reason is one nobody looked at."""
    for key in TARGET_KEYS:
        for element in port(definition(), key).elements:
            if element.fidelity is not Fidelity.EQUIVALENT:
                assert element.detail.strip(), f"{key}/{element.name} has no reason"


def test_the_report_counts_what_crossed_and_what_did_not() -> None:
    payload = port(definition(), "pine").as_dict()
    counts = payload["counts"]
    assert counts["equivalent"] + counts["approximated"] + counts["unsupported"] == len(
        payload["elements"]
    )


# ── coverage of the strategy, not just its features ──────────────────────────


def test_the_analysis_reaches_every_part_of_a_strategy() -> None:
    """A port that only looked at features would miss the parts that break.

    Exits, the session gate and the fill model are where two platforms actually
    disagree; a feature table alone would report a clean crossing for a strategy
    whose trailing stop behaves differently on every bar.
    """
    rich = definition(
        entry=EntryRules(
            long=Combine(
                kind="all",
                of=(
                    Cross(
                        direction="above",
                        left=FeatureRef(name="last_close"),
                        right=FeatureRef(name="prior_high"),
                    ),
                    SessionWindow(start_minute=840, end_minute=1200, label="RTH"),
                ),
            ),
            session=SessionWindow(start_minute=840, end_minute=1200, label="RTH"),
        ),
        exit=ExitRules(
            stop=Level(kind="feature", multiple=Constant(value=2.0), feature="atr14"),
            target=Level(kind="points", multiple=Constant(value=40.0)),
            trailing=Level(kind="feature", multiple=Constant(value=1.5), feature="atr14"),
            max_bars=Constant(value=30),
            flat_by_minute=1200,
        ),
    )
    parts = {element.part for element in analyse(rich, "pine")}
    assert {"feature", "entry", "exit", "execution", "parameters", "warmup"} <= parts


def test_a_trailing_stop_is_reported_as_different_on_pine() -> None:
    """Pine ratchets intrabar; AlgoForge ratchets on closed bars.

    This is the sort of difference that never shows up in a diff of the code and
    shows up in every drawdown.
    """
    trailed = definition(
        exit=ExitRules(
            trailing=Level(kind="feature", multiple=Constant(value=1.5), feature="atr14"),
            max_bars=Constant(value=30),
        ),
    )
    trailing = next(
        element
        for element in analyse(trailed, "pine")
        if element.name.startswith("trailing stop")
    )
    assert trailing.fidelity is Fidelity.APPROXIMATED
    assert "intrabar" in trailing.detail


def test_a_session_window_is_reported_wherever_it_appears() -> None:
    gated = definition(
        entry=EntryRules(
            long=Compare(
                op="gt", left=FeatureRef(name="last_close"), right=FeatureRef(name="prior_high")
            ),
            session=SessionWindow(start_minute=840, end_minute=1200, label="RTH"),
        ),
    )
    sessions = [e for e in analyse(gated, "mql5") if e.name.startswith("session")]
    assert sessions and all(e.fidelity is Fidelity.APPROXIMATED for e in sessions)
    assert "server time" in sessions[0].detail


# ── what is and is not generated ─────────────────────────────────────────────


def test_only_the_targets_that_generate_produce_code() -> None:
    """A file nobody can check is worse than no file, and the two are distinct.

    Empty code with an honest report is not the same as a failure, and the
    report says which it is.
    """
    for spec in TARGETS:
        report = port(definition(), spec.key)
        assert bool(report.code) is spec.generates, spec.key
        if not spec.generates:
            assert any("not generated" in note.lower() for note in report.notes)


def test_the_generated_pine_says_it_was_never_run() -> None:
    """The warning belongs in the file, because the file is what leaves.

    The report stays in AlgoForge; the script gets pasted into TradingView by
    somebody who will not have the report open.
    """
    code = to_pine(definition())
    assert "// @version=6" in code.splitlines()[0]
    assert "never executed here" in code
    assert "PORT, not the strategy AlgoForge validated" in code


def test_the_generated_pine_carries_what_did_not_cross() -> None:
    with_orb = definition(
        features=(
            Feature(name="orh", kind="opening_range_high", args=(Constant(value=15),)),
            Feature(name="last_close", kind="close"),
        ),
        entry=EntryRules(
            long=Compare(op="gt", left=FeatureRef(name="last_close"), right=FeatureRef(name="orh"))
        ),
        exit=ExitRules(max_bars=Constant(value=10)),
        parameters=(),
    )
    code = to_pine(with_orb)
    assert "What did not cross cleanly" in code
    assert "unsupported" in code
    # And it does not quietly emit something for the feature it cannot express.
    assert "f_orh = na" in code


# ── the emitted Pine says what the definition says ───────────────────────────


def test_features_become_their_pine_builtins() -> None:
    code = to_pine(definition())
    assert "f_prior_high = ta.highest(high, p_lookback)" in code
    # `ta.atr` takes a `simple int`. `ta.atr(14.0)` is a compile error, so an
    # integral constant must not carry a decimal point.
    assert "f_atr14 = ta.atr(14)" in code


def test_an_integral_length_is_emitted_as_an_integer() -> None:
    """Pine's `ta.*` length arguments reject a float outright.

    Emitting `14.0` produces a script that looks right in a diff and does not
    compile, which is the specific way a generated file wastes somebody's
    afternoon.
    """
    lengths = definition(
        features=(
            Feature(name="fast", kind="sma", args=(Constant(value=10),)),
            Feature(name="slow", kind="ema", args=(Constant(value=50),)),
            Feature(name="trend", kind="adx", args=(Constant(value=14),)),
            Feature(name="last_close", kind="close"),
        ),
        entry=EntryRules(
            long=Compare(
                op="gt", left=FeatureRef(name="fast"), right=FeatureRef(name="slow")
            )
        ),
        exit=ExitRules(max_bars=Constant(value=20)),
        parameters=(),
    )
    code = to_pine(lengths)
    assert "ta.sma(close, 10)" in code
    assert "ta.ema(close, 50)" in code
    assert ".0)" not in code.split("// Features.")[1].split("// Entries.")[0]


def test_adx_is_destructured_because_ta_dmi_returns_a_tuple() -> None:
    """`ta.dmi(n, n).adx` is not Pine and does not compile.

    The kind of mistake a translator makes when it reasons about a function from
    its name rather than from its signature.
    """
    trended = definition(
        features=(
            Feature(name="trend", kind="adx", args=(Constant(value=14),)),
            Feature(name="last_close", kind="close"),
        ),
        entry=EntryRules(
            long=Compare(
                op="gt", left=FeatureRef(name="trend"), right=Constant(value=25.0)
            )
        ),
        exit=ExitRules(max_bars=Constant(value=20)),
        parameters=(),
    )
    code = to_pine(trended)
    assert "[_af_dip_trend, _af_dim_trend, f_trend] = ta.dmi(14, 14)" in code
    assert ".adx" not in code


def test_a_stop_on_a_shifted_feature_reads_the_same_bar_the_signal_did() -> None:
    """Otherwise the stop is a different distance from the one that was tested.

    Silently: the code looks correct and the level is measured off the wrong
    bar.
    """
    shifted = definition(
        features=(
            Feature(name="atr_prev", kind="atr", args=(Constant(value=14),), shift=1),
            Feature(name="last_close", kind="close"),
            Feature(name="prior_high", kind="highest", args=(Constant(value=20),), shift=1),
        ),
        exit=ExitRules(
            stop=Level(kind="feature", multiple=Constant(value=2.0), feature="atr_prev"),
            max_bars=Constant(value=20),
        ),
        parameters=(),
    )
    code = to_pine(shifted)
    assert "f_atr_prev[1]" in code


def test_a_shifted_feature_is_indexed_rather_than_recomputed() -> None:
    """`shift` is bars back, which is Pine's `[n]`. Recomputing would be a
    different series."""
    code = to_pine(definition())
    assert "f_prior_high[1]" in code


def test_parameters_become_inputs_at_their_defaults() -> None:
    code = to_pine(definition())
    assert 'p_lookback = input.int(20, "lookback")' in code
    assert 'p_stop_atr = input.float(2.0, "stop_atr")' in code


def test_the_fill_model_is_declared_rather_than_left_to_the_default() -> None:
    """AlgoForge decides on a closed bar and fills at the next open.

    Pine's default happens to agree, and relying on a default that happens to
    agree is how a strategy changes when a default does.
    """
    code = to_pine(definition())
    assert "process_orders_on_close=false" in code


def test_costs_declared_on_the_definition_reach_the_script(
) -> None:
    code = to_pine(definition())
    assert "commission_value=0.62" in code
    assert "slippage=1" in code


def test_a_cross_becomes_a_cross_and_not_a_comparison() -> None:
    """`close > high` and `close crossing above high` are different strategies."""
    crossed = definition(
        entry=EntryRules(
            long=Cross(
                direction="above",
                left=FeatureRef(name="last_close"),
                right=FeatureRef(name="prior_high"),
            )
        )
    )
    code = to_pine(crossed)
    assert "ta.crossover(" in code


def test_a_session_window_that_wraps_midnight_is_not_an_empty_range() -> None:
    """start > end means the window crosses midnight.

    Emitted as a range it would be empty, and a strategy that never trades looks
    like a strategy with no edge.
    """
    overnight = definition(
        entry=EntryRules(
            long=Compare(
                op="gt", left=FeatureRef(name="last_close"), right=FeatureRef(name="prior_high")
            ),
            session=SessionWindow(start_minute=1320, end_minute=180, label="Asia"),
        ),
    )
    code = to_pine(overnight)
    assert ">= 1320 or" in code


def test_boolean_combinations_keep_their_shape() -> None:
    combined = definition(
        entry=EntryRules(
            long=Combine(
                kind="all",
                of=(
                    Compare(
                        op="gt",
                        left=FeatureRef(name="last_close"),
                        right=FeatureRef(name="prior_high"),
                    ),
                    Combine(
                        kind="not",
                        of=(
                            Compare(
                                op="gt",
                                left=FeatureRef(name="atr14"),
                                right=Constant(value=50.0),
                            ),
                        ),
                    ),
                ),
            )
        )
    )
    code = to_pine(combined)
    assert " and " in code
    assert "(not " in code


def test_exits_reach_the_script(
) -> None:
    full = definition(
        exit=ExitRules(
            stop=Level(kind="feature", multiple=Constant(value=2.0), feature="atr14"),
            target=Level(kind="points", multiple=Constant(value=40.0)),
            max_bars=Constant(value=30),
            flat_by_minute=1200,
        ),
    )
    code = to_pine(full)
    assert "strategy.exit(" in code
    assert "stop=_af_stop" in code
    assert "limit=_af_target" in code
    assert 'strategy.close_all("time")' in code
    assert 'strategy.close_all("eod")' in code


# ── determinism ──────────────────────────────────────────────────────────────


def test_the_same_definition_ports_identically_twice() -> None:
    clean = definition()
    for key in TARGET_KEYS:
        assert port(clean, key).as_dict() == port(clean, key).as_dict()


def test_the_port_names_the_definition_it_came_from() -> None:
    """Provenance: a generated file has to be traceable to the thing it renders."""
    clean = definition()
    report = port(clean, "pine")
    assert report.definition_hash == clean.definition_hash
    assert clean.definition_id in report.code

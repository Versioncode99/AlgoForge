"""G7: does the engine reproduce its own result?

G7 was named "Engine consistency" and its rule read "oracle tolerance passes".
Nothing compared anything — `engine_consistent` was a dataclass default of
`True` that no call site overrode — so the gate asserted, per run, in a
machine-readable verdict, a comparison against a reference engine that is never
executed, in a system whose own report says nothing is calibrated.

Two claims were tangled under one name. These tests pin down which one the gate
now makes:

* it asserts the run **reproduces**, and that can genuinely fail;
* it does **not** assert venue calibration, and says so.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any, ClassVar

from forge.judge import Judge, JudgeInput
from forge.strategy import StrategyLibrary, check_determinism, run_backtest, run_digest
from forge.strategy.determinism import describe_divergence
from forge.strategy.synthetic import generate_bars


class FakeTrade:
    def __init__(self, index: int, net: float = 9.0, exit_index: int | None = None) -> None:
        self.direction = 1
        self.entry_decision_index = index
        self.entry_index = index + 1
        self.exit_decision_index = index + 4
        self.exit_index = exit_index if exit_index is not None else index + 5
        self.entry_price = 100.0
        self.exit_price = 101.0
        self.gross_pnl = 10.0
        self.costs = 1.0
        self.net_pnl = net
        self.bars_held = 4
        self.exit_reason = "signal"
        # Mirrors the real `Trade`. `test_the_digest_covers_every_measured_field`
        # is what stops this stand-in from silently falling behind it again.
        self.mfe = 12.0
        self.mae = -3.0
        self.mfe_index = index + 2
        self.mae_index = index + 3
        self.stop_price = 97.0
        self.target_price = 104.0
        self.trailing_stop_price = None
        self.entry_context: dict[str, float] = {"atr14": 1.25}


class FakeResult:
    def __init__(self, trades: list[FakeTrade], net: float = 27.0) -> None:
        self.trades = trades
        self.equity = [0.0, 9.0, 18.0, net]
        self.net_pnl = net
        self.gross_pnl = net + 3.0
        self.total_costs = 3.0
        self.win_rate = 1.0
        self.max_drawdown = 0.0
        self.lookahead_clean = True


# ── the check itself ─────────────────────────────────────────────────────────


def test_a_deterministic_run_reproduces() -> None:
    def stable(bars: Any, parameters: Any) -> Any:
        return FakeResult([FakeTrade(index) for index in range(3)])

    report = check_determinism("demo", code_hash="h", backtest=stable, bars=[0] * 500)
    assert report.reproduced is True
    assert report.trade_count == 3
    assert report.bars_checked == 500
    assert report.reason.startswith("REPRODUCED_3_TRADES_OVER_500_BARS")


def test_a_drifting_run_is_caught() -> None:
    """The failure this gate exists to catch: state that survives between runs."""
    counter = {"n": 0}

    def leaky(bars: Any, parameters: Any) -> Any:
        counter["n"] += 1
        return FakeResult([FakeTrade(index, net=9.0 + counter["n"]) for index in range(3)])

    report = check_determinism("demo", code_hash="h", backtest=leaky, bars=[0] * 100)
    assert report.reproduced is False
    assert report.reason.startswith("DIVERGED")


def test_a_divergence_names_the_first_concrete_difference() -> None:
    """'The digests differ' is not actionable. Name the trade and the field."""
    first = FakeResult([FakeTrade(0), FakeTrade(10), FakeTrade(20)])
    second = FakeResult([FakeTrade(0), FakeTrade(10), FakeTrade(20, exit_index=99)])
    assert describe_divergence(first, second) == "trade 2 exit_index: 25 then 99"


def test_a_different_trade_count_is_reported_as_such() -> None:
    first = FakeResult([FakeTrade(0), FakeTrade(10)])
    second = FakeResult([FakeTrade(0)])
    assert describe_divergence(first, second) == "trade count 2 then 1"


def test_a_run_that_raises_is_unmeasured_not_failed() -> None:
    def broken(bars: Any, parameters: Any) -> Any:
        raise ValueError("no bars")

    report = check_determinism("demo", code_hash="h", backtest=broken, bars=[0] * 10)
    assert report.reproduced is None
    assert report.reason.startswith("NOT_CHECKED: ValueError")


def test_the_digest_ignores_fields_that_must_differ_between_runs() -> None:
    """Hashing backtest_id or the timestamps would fail every check trivially."""
    trades = [FakeTrade(index) for index in range(3)]
    first, second = FakeResult(trades), FakeResult(trades)
    first.backtest_id, second.backtest_id = "a", "b"  # type: ignore[attr-defined]
    first.started_at, second.started_at = "t1", "t2"  # type: ignore[attr-defined]
    assert run_digest(first) == run_digest(second)


def test_the_report_states_what_it_does_not_assert() -> None:
    def stable(bars: Any, parameters: Any) -> Any:
        return FakeResult([FakeTrade(0)])

    payload = check_determinism("demo", code_hash="h", backtest=stable, bars=[0]).as_dict()
    assert "venue calibration" in payload["does_not_assert"]


# ── the real backtester ──────────────────────────────────────────────────────


def test_the_real_engine_reproduces_a_real_strategy(tmp_path: Any) -> None:
    library = StrategyLibrary(tmp_path / "strategies")
    spec = library.create_from_template("momentum_breakout", symbol="MNQ.CME")
    module = library.load_module(spec.strategy_id)
    bars = generate_bars(count=3_000, seed=11)

    def once(slice_bars: Any, parameters: Any) -> Any:
        return run_backtest(
            module,
            spec,
            list(slice_bars),
            parameters=dict(parameters or {}),
            code_hash=library.code_hash(spec.strategy_id),
            labels=("DETERMINISM_CHECK",),
            evidence_tier="SYNTHETIC",
        )

    report = check_determinism(
        spec.strategy_id, code_hash=library.code_hash(spec.strategy_id), backtest=once, bars=bars
    )
    assert report.reproduced is True, report.reason
    library.unload_module(spec.strategy_id)


# ── the gate ─────────────────────────────────────────────────────────────────


def judge_with(**overrides: object) -> Any:
    base: dict[str, Any] = {
        "run_id": "run-1",
        "tier": "TRUTH_OOS",
        "pnl": tuple(float(v) for v in (80, -25, 95, -30, 70, -20, 110, -35, 60, 45) * 4),
        "trial_count": 1,
        "data_gate_passed": True,
        "preregistered": True,
        "implementation_tests_passed": True,
    }
    return Judge().evaluate(JudgeInput(**{**base, **overrides}))


def gate(verdict: Any, name: str) -> Any:
    return next(item for item in verdict.gates if item.gate == name)


def test_g7_is_inconclusive_when_nothing_re_ran_the_strategy() -> None:
    verdict = judge_with(engine_consistent=None)
    assert gate(verdict, "G7").status == "INCONCLUSIVE"
    assert gate(verdict, "G7").observed == "NOT_MEASURED"


def test_g7_passes_on_a_reproduced_run() -> None:
    assert gate(judge_with(engine_consistent=True), "G7").status == "PASS"


def test_g7_fails_on_a_diverged_run() -> None:
    verdict = judge_with(engine_consistent=False)
    assert gate(verdict, "G7").status == "FAIL"
    assert gate(verdict, "G7").observed == "DIVERGED"
    assert verdict.decision == "FAIL"


def test_g7_does_not_claim_calibration() -> None:
    """The rule string is the contract a reader relies on. It used to lie."""
    rule = gate(judge_with(engine_consistent=True), "G7").rule
    assert "does NOT assert venue calibration" in rule
    assert "oracle" not in rule.lower()


def test_g9_is_inconclusive_because_nothing_falsifies_the_mechanism() -> None:
    """Honest: no mechanism test exists, so the gate reports it was not measured."""
    verdict = judge_with(mechanism_aligned=None)
    assert gate(verdict, "G9").status == "INCONCLUSIVE"
    assert gate(verdict, "G9").observed == "NOT_MEASURED"


def test_g9_can_fail_when_something_does_falsify_it() -> None:
    assert gate(judge_with(mechanism_aligned=False), "G9").status == "FAIL"


def test_neither_gate_defaults_to_true_any_more() -> None:
    """The regression guard for the whole class of defect."""
    defaults = {field.name: field.default for field in fields(JudgeInput)}
    assert defaults["engine_consistent"] is None
    assert defaults["mechanism_aligned"] is None
    assert defaults["implementation_tests_passed"] is None


def test_the_digest_covers_every_measured_field_of_a_trade() -> None:
    """A field added to `Trade` and forgotten here is a determinism hole.

    `run_digest` names the fields it hashes, which is right — a blanket
    `model_dump` would sweep in `trade_id` and the timestamps and make every
    check fail for the wrong reason. The cost of naming them is that a new
    measured field can be added and never checked, so a strategy whose stop
    depends on wall clock would reproduce its P&L and place its stop somewhere
    different every run, and G7 would call that consistent.

    So the exclusions are stated, and everything else must be covered.
    """
    from forge.strategy.models import Trade

    # Excluded by design: derived from the run rather than from the strategy.
    excluded = {
        "trade_id",  # a hash of indices already covered
        "entry_time",  # derived from entry_index and the bars
        "exit_time",  # derived from exit_index and the bars
        "mfe_index",  # positional; the measured value is what must reproduce
        "mae_index",
    }
    covered = _digest_fields()
    missing = set(Trade.model_fields) - excluded - covered
    assert not missing, (
        f"run_digest does not hash {sorted(missing)}. Either hash it, or add it "
        "to this test's `excluded` set with the reason it cannot differ."
    )


def _digest_fields() -> set[str]:
    """Which `Trade` attributes `run_digest` actually reads.

    Determined by handing it a trade that records every attribute access, which
    is more honest than parsing the source and cannot drift from it.
    """
    from forge.strategy.determinism import run_digest

    seen: set[str] = set()

    class Recording:
        def __getattr__(self, name: str) -> object:
            seen.add(name)
            return {} if name == "entry_context" else 1.0

    class Result:
        trades: ClassVar[list[Recording]] = [Recording()]
        bar_count = 1
        equity: ClassVar[list[float]] = []
        parameters: ClassVar[dict[str, float]] = {}
        net_pnl = 0.0
        gross_pnl = 0.0
        total_costs = 0.0
        win_rate = 0.0
        max_drawdown = 0.0
        lookahead_clean = True

    run_digest(Result())
    return seen

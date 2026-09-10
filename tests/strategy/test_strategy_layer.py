from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from forge.strategy import (
    TEMPLATES,
    GuardViolation,
    StrategyLibrary,
    Window,
    check_source,
    generate_bars,
    run_backtest,
)


@pytest.fixture
def library(tmp_path: Path) -> StrategyLibrary:
    return StrategyLibrary(tmp_path / "strategies")


@pytest.fixture(scope="module")
def bars():
    return generate_bars(count=1200, seed=42)


# ── the window cannot see the future ─────────────────────────────────────────


def test_window_exposes_only_bars_up_to_its_end_index():
    data = np.arange(100, dtype=np.float64)
    w = Window(data, data, data, data, data, list(range(100)), 40)
    assert w.closes.size == 41
    assert w.closes[-1] == 40
    assert w.highs.size == 41


def test_window_slices_are_independent_of_later_data():
    """Appending future bars must not change what an earlier window saw."""
    short = np.arange(60, dtype=np.float64)
    long = np.arange(200, dtype=np.float64)
    a = Window(short, short, short, short, short, list(range(60)), 50)
    b = Window(long, long, long, long, long, list(range(200)), 50)
    assert np.array_equal(a.closes, b.closes)


# ── the guard ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "snippet",
    [
        "import os\ndef entry_signal(w,p): return 1\ndef exit_signal(w,p,pos): return None",
        "import subprocess\ndef entry_signal(w,p): return 1\ndef exit_signal(w,p,pos): return None",
        "def entry_signal(w,p): return eval('1')\ndef exit_signal(w,p,pos): return None",
        "def entry_signal(w,p): return open('x').read()\ndef exit_signal(w,p,pos): return None",
        "def entry_signal(w,p): return (1).__class__\ndef exit_signal(w,p,pos): return None",
    ],
)
def test_guard_rejects_unsafe_source(snippet: str):
    assert check_source(snippet), f"guard failed to reject: {snippet[:40]}"


def test_guard_requires_both_signal_functions():
    problems = check_source("import numpy\ndef entry_signal(w, p):\n    return 1\n")
    assert any("exit_signal" in p for p in problems)


def test_guard_accepts_every_shipped_template():
    for template in TEMPLATES.values():
        assert check_source(template.source) == [], f"{template.key} would be refused at run time"


def test_library_refuses_to_write_unsafe_source(library: StrategyLibrary):
    spec = library.create_from_template("momentum_breakout")
    with pytest.raises(GuardViolation):
        library.write_source(spec.strategy_id, "import socket\ndef entry_signal(w,p): return 1\n")


# ── the library writes real, loadable files ──────────────────────────────────


def test_created_strategy_writes_readable_files_to_disk(library: StrategyLibrary):
    spec = library.create_from_template("mean_reversion_band", name="Absorption Probe")
    folder = library.dir_for(spec.strategy_id)
    assert (folder / "strategy.py").exists()
    assert (folder / "spec.json").exists()
    assert (folder / "test_strategy.py").exists()
    assert "def entry_signal" in library.get_source(spec.strategy_id)
    assert spec.hypothesis and spec.falsifiable_prediction


def test_duplicate_names_do_not_collide(library: StrategyLibrary):
    a = library.create_from_template("momentum_breakout")
    b = library.create_from_template("momentum_breakout")
    assert a.strategy_id != b.strategy_id
    assert len(library.list_specs()) == 2


def test_module_loads_and_is_callable(library: StrategyLibrary):
    spec = library.create_from_template("momentum_breakout")
    module = library.load_module(spec.strategy_id)
    assert callable(module.entry_signal)
    assert callable(module.exit_signal)


# ── backtests produce real, honest results ───────────────────────────────────


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_every_template_backtests_without_lookahead(library: StrategyLibrary, bars, template: str):
    spec = library.create_from_template(template)
    module = library.load_module(spec.strategy_id)
    result = run_backtest(module, spec, bars, code_hash=library.code_hash(spec.strategy_id))

    assert result.lookahead_clean, "decision bar must always precede the fill bar"
    for trade in result.trades:
        assert trade.entry_decision_index == trade.entry_index - 1
        assert trade.exit_decision_index == trade.exit_index - 1
        assert trade.exit_index > trade.entry_index


def test_backtest_is_deterministic(library: StrategyLibrary, bars):
    spec = library.create_from_template("momentum_breakout")
    module = library.load_module(spec.strategy_id)
    a = run_backtest(module, spec, bars, code_hash="x")
    b = run_backtest(module, spec, bars, code_hash="x")
    assert a.backtest_id == b.backtest_id
    assert a.net_pnl == b.net_pnl
    assert [t.trade_id for t in a.trades] == [t.trade_id for t in b.trades]


def test_costs_are_always_deducted(library: StrategyLibrary, bars):
    spec = library.create_from_template("mean_reversion_band")
    module = library.load_module(spec.strategy_id)
    result = run_backtest(module, spec, bars, code_hash="x")
    if result.trades:
        assert result.total_costs > 0
        assert result.net_pnl == pytest.approx(result.gross_pnl - result.total_costs, abs=1e-6)


def test_backtest_refuses_insufficient_history(library: StrategyLibrary):
    spec = library.create_from_template("momentum_breakout")
    module = library.load_module(spec.strategy_id)
    with pytest.raises(ValueError, match="at least"):
        run_backtest(module, spec, generate_bars(count=50), code_hash="x")


# ── the synthetic generator must not smuggle in an edge ──────────────────────


def test_synthetic_bars_are_deterministic_for_a_seed():
    a, b = generate_bars(count=300, seed=5), generate_bars(count=300, seed=5)
    assert [x.close for x in a] == [x.close for x in b]


def test_synthetic_bars_are_internally_consistent():
    for bar in generate_bars(count=500, seed=11):
        assert bar.low <= bar.open <= bar.high
        assert bar.low <= bar.close <= bar.high
        assert bar.volume >= 0
        assert bar.knowledge_time >= bar.event_time


def test_synthetic_returns_have_no_embedded_drift():
    """A planted edge would teach the whole pipeline to find things that are not there."""
    closes = np.array([b.close for b in generate_bars(count=4000, seed=99)])
    returns = np.diff(np.log(closes))
    t_stat = returns.mean() / (returns.std(ddof=1) / np.sqrt(returns.size))
    assert abs(t_stat) < 3.0, "synthetic series shows directional drift; it must be edge-free"


# ── directional symmetry ─────────────────────────────────────────────────────


def test_no_shipped_template_can_only_take_one_side() -> None:
    """Nine of the twelve templates were long-only, and that was a measurement
    problem rather than a missing feature.

    A long-only breakout on an index future that rose across the sample is
    measuring the drift plus a filter and reporting it as an edge. Worse, every
    one of these templates states its mechanism symmetrically — "a break is
    liquidity-taking flow that must be absorbed" — so testing it on one side
    only means the falsifiable prediction it carries was never actually at risk.

    This is a source-level check rather than a behavioural one on purpose: a
    template that *could* go short but never happens to on one fixture would
    pass a behavioural check and still be broken.
    """
    from forge.strategy import SHIPPED_TEMPLATE_KEYS, TEMPLATES

    # Shipped templates only. The catalogue is open at run time by design, and a
    # template an operator or the research director registered is not something
    # this repository ships or can make promises about.
    long_only = [
        key
        for key in SHIPPED_TEMPLATE_KEYS
        if "return 1" in TEMPLATES[key].source and "return -1" not in TEMPLATES[key].source
    ]
    assert not long_only, (
        f"{', '.join(sorted(long_only))} can only ever go long. If that is "
        "deliberate for a template, its hypothesis must say why the mechanism is "
        "one-sided — and then this test needs an explicit exemption naming it."
    )


def test_every_template_actually_trades_both_sides_on_real_bars() -> None:
    """The source check above proves the branch exists; this proves it is
    reachable. A short branch guarded by a condition that can never hold is the
    same defect wearing a fix."""
    import importlib.util
    import sys
    import tempfile
    import uuid
    from datetime import UTC, datetime
    from pathlib import Path as _Path

    from forge.strategy import SHIPPED_TEMPLATE_KEYS, TEMPLATES, generate_bars, run_backtest
    from forge.strategy.models import StrategySpec

    bars = generate_bars(count=30_000, seed=4242)
    one_sided: list[str] = []
    silent: list[str] = []
    for key, template in sorted(
        (key, TEMPLATES[key]) for key in SHIPPED_TEMPLATE_KEYS if key in TEMPLATES
    ):
        module_name = f"symmetry_{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory() as tmp:
            path = _Path(tmp) / f"{module_name}.py"
            path.write_text(template.source, encoding="utf-8")
            spec_obj = importlib.util.spec_from_file_location(module_name, path)
            assert spec_obj and spec_obj.loader
            module = importlib.util.module_from_spec(spec_obj)
            sys.modules[module_name] = module
            try:
                spec_obj.loader.exec_module(module)
            finally:
                sys.modules.pop(module_name, None)

        spec = StrategySpec(
            strategy_id=key,
            name=template.name,
            lineage=key,
            family=template.family,
            market="futures",
            symbol="MNQ.SYNTH",
            template=key,
            hypothesis=template.hypothesis,
            falsifiable_prediction=template.falsifiable_prediction,
            parameters=template.parameters,
            warmup_bars=template.warmup_bars,
            created_at=datetime(2026, 9, 8, tzinfo=UTC),
        )
        result = run_backtest(module, spec, bars, code_hash=key)
        if not result.trades:
            silent.append(key)
            continue
        directions = {trade.direction for trade in result.trades}
        if directions != {1, -1}:
            one_sided.append(f"{key} (took only {directions})")

    assert not one_sided, "; ".join(one_sided)
    assert not silent, f"{', '.join(silent)} took no trades at all on 30,000 bars"

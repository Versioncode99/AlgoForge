"""The expanded feature vocabulary, and the three things that make it safe.

A larger vocabulary is only an improvement if every addition keeps the
properties the small one had. These tests are the check, and each one is about a
specific way the expansion could have gone wrong:

* a transformation could read its source's history from a cache that had seen a
  later bar, which would be lookahead wearing an optimisation's clothes;
* the cached evaluator could disagree with a naive recomputation, which would
  mean the speed came from computing something else;
* the exported Python could drift from the compiled definition, which would mean
  the file a person downloads is not the strategy that was measured.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest
from forge.strategy.export import to_python, verify_python
from forge.strategy.guard import check_source
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
    IRError,
    Level,
    ParamRef,
    StrategyDefinition,
    _Evaluator,
    compile_definition,
    validate_definition,
)
from forge.strategy.models import ParameterSpec, StrategySpec
from forge.strategy.primitives import (
    BAR_KINDS,
    CATALOGUE,
    SERIES_KINDS,
    apply_transform,
    catalogue_rows,
    primitive,
    window_length,
)
from forge.strategy.runtime import Window, run_backtest
from forge.strategy.synthetic import generate_bars

PROVENANCE = DefinitionProvenance(created_at=datetime(2026, 9, 12, tzinfo=UTC))
HYPOTHESIS = (
    "A stated observation, transformed against its own recent distribution, separates "
    "the regime the effect is claimed to live in from the one it is not."
)
PREDICTION = (
    "If the effect is the same on both sides of the transformed condition, the "
    "conditioning does nothing and the claim is abandoned."
)


def _definition(features: tuple[Feature, ...], **overrides: object) -> StrategyDefinition:
    base: dict[str, object] = {
        "name": "Probe",
        "family": "volatility",
        "symbol": "MNQ",
        "hypothesis": HYPOTHESIS,
        "falsifiable_prediction": PREDICTION,
        "features": features,
        "entry": EntryRules(
            long=Compare(
                op="gt", left=FeatureRef(name=features[-1].name), right=Constant(value=-1e9)
            )
        ),
        "exit": ExitRules(max_bars=Constant(value=20)),
        "provenance": PROVENANCE,
    }
    base.update(overrides)
    return StrategyDefinition(**base)  # type: ignore[arg-type]


def _spec(defn: StrategyDefinition) -> StrategySpec:
    return StrategySpec(
        strategy_id="probe",
        name=defn.name,
        lineage="probe",
        family=defn.family,
        market="futures",
        symbol=defn.symbol,
        bar_spec=defn.timeframe,
        template=f"ir:{defn.definition_id}",
        hypothesis=defn.hypothesis,
        falsifiable_prediction=defn.falsifiable_prediction,
        parameters=defn.parameters,
        warmup_bars=defn.required_warmup(),
        created_at=datetime.now(UTC),
        created_by="test",
    )


# ── the catalogue itself ─────────────────────────────────────────────────────


def test_the_vocabulary_is_materially_larger_than_the_ten_archetype_era() -> None:
    """The bottleneck was the size of the closed set, so the size is asserted."""
    assert len(CATALOGUE) >= 45
    assert len(SERIES_KINDS) >= 12
    assert BAR_KINDS and not (BAR_KINDS & SERIES_KINDS)


def test_every_primitive_declares_what_a_caller_needs_to_reason_about_it() -> None:
    for spec in CATALOGUE.values():
        assert spec.label.strip(), spec.kind
        # A primitive with no description is one nobody can tell apart from its
        # neighbour in the interface, which is how a vocabulary becomes noise.
        # A sentence, not a placeholder: some of these genuinely are short.
        assert len(spec.description) >= 12 and spec.description.endswith("."), spec.kind
        assert spec.category
        assert spec.unit
        assert spec.arity in (0, 1)
        assert spec.data_requirement


def test_catalogue_rows_cover_the_catalogue() -> None:
    rows = catalogue_rows()
    assert {row["kind"] for row in rows} == set(CATALOGUE)


def test_window_length_is_stated_for_every_transformation() -> None:
    for kind in SERIES_KINDS:
        assert window_length(kind, 10) >= 10, kind
    assert window_length("change", 10) == 11
    assert window_length("accel", 10) == 21
    assert window_length("ewm", 10) == 50


def test_an_unknown_kind_names_what_is_known() -> None:
    with pytest.raises(KeyError, match="unknown feature kind"):
        primitive("teleport")


# ── transformations in isolation ─────────────────────────────────────────────


def test_transformations_answer_nan_rather_than_guessing_on_short_history() -> None:
    short = np.array([1.0, 2.0], dtype=np.float64)
    for kind in SERIES_KINDS:
        assert math.isnan(apply_transform(kind, short, 20)), kind


def test_transformations_answer_nan_rather_than_propagating_a_nan_source() -> None:
    values = np.array([1.0, float("nan"), 3.0, 4.0, 5.0], dtype=np.float64)
    for kind in ("mean", "zscore", "percentile_rank", "slope", "max_of"):
        assert math.isnan(apply_transform(kind, values, 5)), kind


def test_zscore_is_zero_at_the_mean_and_one_deviation_out() -> None:
    values = np.array([1.0, 2.0, 3.0, 4.0, 3.0], dtype=np.float64)
    spread = float(values.std(ddof=1))
    expected = (values[-1] - values.mean()) / spread
    assert apply_transform("zscore", values, 5) == pytest.approx(expected)


def test_zscore_is_undefined_on_a_flat_source_rather_than_infinite() -> None:
    assert math.isnan(apply_transform("zscore", np.ones(10), 10))


def test_percentile_rank_is_one_at_the_top_and_bounded_below() -> None:
    rising = np.arange(10, dtype=np.float64)
    assert apply_transform("percentile_rank", rising, 10) == pytest.approx(1.0)
    falling = rising[::-1].copy()
    assert apply_transform("percentile_rank", falling, 10) == pytest.approx(0.1)


def test_slope_recovers_a_known_gradient() -> None:
    values = np.arange(20, dtype=np.float64) * 2.5 + 7.0
    assert apply_transform("slope", values, 20) == pytest.approx(2.5)


def test_persistence_counts_the_share_above_zero() -> None:
    values = np.array([1.0, -1.0, 2.0, -2.0, 3.0], dtype=np.float64)
    assert apply_transform("persistence", values, 5) == pytest.approx(0.6)


def test_accel_separates_a_steady_move_from_an_accelerating_one() -> None:
    steady = np.arange(11, dtype=np.float64)
    assert apply_transform("accel", steady, 5) == pytest.approx(0.0)
    accelerating = np.arange(11, dtype=np.float64) ** 2
    assert apply_transform("accel", accelerating, 5) > 0.0


def test_an_unknown_transformation_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(KeyError, match="not a series transformation"):
        apply_transform("sma", np.ones(5), 5)


# ── declaration rules ────────────────────────────────────────────────────────


def test_a_transformation_without_a_source_is_refused() -> None:
    with pytest.raises(IRError, match="names none"):
        validate_definition(
            _definition(
                (
                    Feature(name="atr", kind="atr", args=(Constant(value=14),)),
                    Feature(name="z", kind="zscore", args=(Constant(value=20),)),
                )
            )
        )


def test_an_observation_carrying_a_source_is_refused() -> None:
    with pytest.raises(IRError, match="must not"):
        validate_definition(
            _definition(
                (
                    Feature(name="atr", kind="atr", args=(Constant(value=14),)),
                    Feature(name="px", kind="close", source="atr"),
                )
            )
        )


def test_a_source_that_is_not_declared_is_refused() -> None:
    with pytest.raises(IRError, match="not declared"):
        validate_definition(
            _definition(
                (Feature(name="z", kind="zscore", args=(Constant(value=20),), source="ghost"),)
            )
        )


def test_a_transformation_chain_that_loops_is_refused() -> None:
    with pytest.raises(IRError, match="loops back"):
        validate_definition(
            _definition(
                (
                    Feature(name="a", kind="zscore", args=(Constant(value=5),), source="b"),
                    Feature(name="b", kind="mean", args=(Constant(value=5),), source="a"),
                )
            )
        )


def test_a_chain_deeper_than_the_bound_is_refused_with_the_bound_named() -> None:
    features = (
        Feature(name="atr", kind="atr", args=(Constant(value=14),)),
        Feature(name="t1", kind="mean", args=(Constant(value=5),), source="atr"),
        Feature(name="t2", kind="mean", args=(Constant(value=5),), source="t1"),
        Feature(name="t3", kind="mean", args=(Constant(value=5),), source="t2"),
        Feature(name="t4", kind="mean", args=(Constant(value=5),), source="t3"),
    )
    with pytest.raises(IRError, match="deeper than"):
        validate_definition(_definition(features))


def test_a_source_whose_length_is_itself_a_feature_is_refused() -> None:
    features = (
        Feature(name="len", kind="close"),
        Feature(name="dyn", kind="sma", args=(FeatureRef(name="len"),)),
        Feature(name="z", kind="zscore", args=(Constant(value=10),), source="dyn"),
    )
    with pytest.raises(IRError, match="fixed window"):
        validate_definition(_definition(features))


# ── warmup and retention ─────────────────────────────────────────────────────


def test_warmup_covers_the_whole_chain_not_just_the_outermost_window() -> None:
    defn = _definition(
        (
            Feature(name="atr", kind="atr", args=(Constant(value=20),)),
            Feature(name="rank", kind="percentile_rank", args=(Constant(value=50),), source="atr"),
            Feature(name="sl", kind="slope", args=(Constant(value=30),), source="rank"),
        )
    )
    # 30 for the slope, 50 for the rank underneath it, 40 for the ATR under
    # that. Taking the largest instead of the sum is the mistake this asserts
    # against.
    assert defn.required_warmup() > 30 + 50


def test_warmup_is_at_least_the_history_the_evaluator_has_to_backfill() -> None:
    """The invariant that makes the cache and a fresh recomputation agree.

    The evaluator backfills a bounded stretch of history on its first call. If
    warmup were shorter than that stretch, the first bars of a run would read a
    transformation that had not filled yet — and the exported Python, which
    recomputes from the bars every time, would not agree.
    """
    for lengths in ((14, 20, 10), (30, 60, 25), (7, 120, 40)):
        defn = _definition(
            (
                Feature(name="atr", kind="atr", args=(Constant(value=lengths[0]),)),
                Feature(
                    name="rank",
                    kind="percentile_rank",
                    args=(Constant(value=lengths[1]),),
                    source="atr",
                ),
                Feature(name="sl", kind="slope", args=(Constant(value=lengths[2]),), source="rank"),
            )
        )
        retained = defn.source_history()
        assert defn.required_warmup() >= max(retained.values()) + 1


def test_only_features_something_transforms_are_retained() -> None:
    defn = _definition(
        (
            Feature(name="atr", kind="atr", args=(Constant(value=14),)),
            Feature(name="px", kind="close"),
            Feature(name="z", kind="zscore", args=(Constant(value=20),), source="atr"),
        )
    )
    retained = defn.source_history()
    assert "atr" in retained
    assert "px" not in retained
    assert "z" not in retained


# ── the cache tells the truth ────────────────────────────────────────────────


def _naive(defn: StrategyDefinition, name: str, end: int, arrays: tuple, params: dict) -> float:
    """Recompute one feature from the bars, with no cache anywhere.

    This is the reference the cached evaluator has to match. It is deliberately
    the slow, obvious implementation: it builds a window ending at the bar it
    was asked about and, for a transformation, calls itself once per offset.
    """
    item = next(f for f in defn.features if f.name == name)
    if item.kind in SERIES_KINDS:
        length = max(1, int(item.args[0].value)) if item.args else 1  # type: ignore[union-attr]
        need = window_length(item.kind, length)
        values = [
            _naive(defn, item.source, end - item.shift - offset, arrays, params)
            for offset in range(need)
        ][::-1]
        return apply_transform(item.kind, np.asarray(values, dtype=np.float64), length)
    window = Window(*arrays, end)
    return _Evaluator(defn, window, params, None).evaluate(item, end)


def test_the_cached_evaluator_matches_a_naive_recomputation_bar_for_bar() -> None:
    defn = _definition(
        (
            Feature(name="atr", kind="atr", args=(Constant(value=14),)),
            Feature(name="rank", kind="percentile_rank", args=(Constant(value=40),), source="atr"),
            Feature(name="sl", kind="slope", args=(Constant(value=15),), source="rank"),
            Feature(name="tr", kind="true_range"),
            Feature(name="tr_z", kind="zscore", args=(Constant(value=30),), source="tr"),
            Feature(name="pos", kind="session_range_position"),
            Feature(name="pos_p", kind="persistence", args=(Constant(value=12),), source="pos"),
        )
    )
    bars = generate_bars(symbol="MNQ", count=1400, seed=31)
    arrays = (
        np.array([b.open for b in bars]),
        np.array([b.high for b in bars]),
        np.array([b.low for b in bars]),
        np.array([b.close for b in bars]),
        np.array([b.volume for b in bars]),
        [b.event_time for b in bars],
    )
    module = compile_definition(defn)
    params: dict[str, float] = {}
    checked = 0
    for end in range(600, 900):
        window = Window(*arrays, end)
        evaluator = module._evaluator(window, params)
        for name in ("atr", "rank", "sl", "tr_z", "pos_p"):
            cached = evaluator.feature(name, 0)
            reference = _naive(defn, name, end, arrays, params)
            assert cached == reference or (
                math.isnan(cached) and math.isnan(reference)
            ), f"{name} at {end}: {cached} != {reference}"
            checked += 1
    assert checked > 1000


def test_a_transformation_read_one_bar_back_is_the_value_from_that_bar() -> None:
    """`Cross` reads its operands a bar back, so a shifted read has to be right."""
    defn = _definition(
        (
            Feature(name="atr", kind="atr", args=(Constant(value=14),)),
            Feature(name="z", kind="zscore", args=(Constant(value=25),), source="atr"),
        )
    )
    bars = generate_bars(symbol="MNQ", count=900, seed=33)
    arrays = (
        np.array([b.open for b in bars]),
        np.array([b.high for b in bars]),
        np.array([b.low for b in bars]),
        np.array([b.close for b in bars]),
        np.array([b.volume for b in bars]),
        [b.event_time for b in bars],
    )
    module = compile_definition(defn)
    for end in range(500, 560):
        now = module._evaluator(Window(*arrays, end), {}).feature("z", 1)
        before = module._evaluator(Window(*arrays, end - 1), {}).feature("z", 0)
        assert now == before or (math.isnan(now) and math.isnan(before))


def test_the_cache_resets_when_the_parameters_change() -> None:
    defn = _definition(
        (
            Feature(name="atr", kind="atr", args=(ParamRef(name="n"),)),
            Feature(name="z", kind="zscore", args=(Constant(value=25),), source="atr"),
        ),
        parameters=(ParameterSpec(name="n", default=14, low=5, high=40, step=1),),
    )
    bars = generate_bars(symbol="MNQ", count=900, seed=35)
    arrays = (
        np.array([b.open for b in bars]),
        np.array([b.high for b in bars]),
        np.array([b.low for b in bars]),
        np.array([b.close for b in bars]),
        np.array([b.volume for b in bars]),
        [b.event_time for b in bars],
    )
    module = compile_definition(defn)
    window = Window(*arrays, 700)
    first = module._evaluator(window, {"n": 14.0}).feature("z", 0)
    second = module._evaluator(window, {"n": 35.0}).feature("z", 0)
    fresh = compile_definition(defn)._evaluator(window, {"n": 35.0}).feature("z", 0)
    assert second == fresh
    assert first != second


def test_the_cache_resets_when_the_bars_change_underneath_it() -> None:
    defn = _definition(
        (
            Feature(name="atr", kind="atr", args=(Constant(value=14),)),
            Feature(name="z", kind="zscore", args=(Constant(value=25),), source="atr"),
        )
    )
    module = compile_definition(defn)
    values = []
    for seed in (41, 42, 41):
        bars = generate_bars(symbol="MNQ", count=900, seed=seed)
        arrays = (
            np.array([b.open for b in bars]),
            np.array([b.high for b in bars]),
            np.array([b.low for b in bars]),
            np.array([b.close for b in bars]),
            np.array([b.volume for b in bars]),
            [b.event_time for b in bars],
        )
        values.append(module._evaluator(Window(*arrays, 700), {}).feature("z", 0))
    assert values[0] != values[1]
    # Running the first dataset again after the second must give the first
    # answer back. A cache that kept the second run's values would not.
    assert values[0] == values[2]


# ── the exported file is the strategy that was measured ──────────────────────


def test_the_generated_python_reproduces_a_transformed_definition_exactly() -> None:
    defn = _definition(
        (
            Feature(name="atr", kind="atr", args=(Constant(value=14),)),
            Feature(name="rank", kind="percentile_rank", args=(Constant(value=40),), source="atr"),
            Feature(name="sl", kind="slope", args=(Constant(value=15),), source="rank"),
            Feature(name="rv", kind="realised_vol", args=(Constant(value=20),)),
            Feature(name="rv_z", kind="zscore", args=(Constant(value=40),), source="rv"),
            Feature(name="px", kind="close"),
            Feature(name="sma", kind="sma", args=(Constant(value=20),)),
        ),
        entry=EntryRules(
            long=Combine(
                kind="all",
                of=(
                    Compare(op="gt", left=FeatureRef(name="rv_z"), right=Constant(value=0.2)),
                    Cross(
                        direction="above",
                        left=FeatureRef(name="px"),
                        right=FeatureRef(name="sma"),
                    ),
                ),
            ),
            short=Compare(op="lt", left=FeatureRef(name="sl"), right=Constant(value=-0.01)),
        ),
        exit=ExitRules(
            stop=Level(kind="feature", multiple=Constant(value=2.0), feature="atr"),
            max_bars=Constant(value=30),
        ),
    )
    report = to_python(defn)
    # Generated is not the same as trusted: the export passes the same static
    # guard as anything else this application executes.
    check_source(report.code)
    bars = generate_bars(symbol="MNQ", count=3000, seed=37)
    outcome = verify_python(defn, bars, _spec(defn))
    assert outcome["verified"], outcome.get("reason") or outcome.get("differences")
    assert outcome["native_trades"] == outcome["exported_trades"]
    assert outcome["native_trades"] > 0


def test_every_observation_in_the_catalogue_can_be_exported_and_reproduced() -> None:
    """No primitive may exist in the IR that the export cannot render.

    A feature the evaluator computes and the exporter does not is a strategy
    that runs here and cannot leave, which is the gap this asserts is empty.
    """
    features = [Feature(name="px", kind="close")]
    for index, kind in enumerate(sorted(BAR_KINDS - {"close"})):
        spec = primitive(kind)
        args = (Constant(value=12.0),) if spec.arity == 1 else ()
        features.append(Feature(name=f"f{index}", kind=kind, args=args))
    defn = _definition(
        tuple(features),
        entry=EntryRules(
            long=Compare(op="gt", left=FeatureRef(name="px"), right=Constant(value=0.0))
        ),
        exit=ExitRules(max_bars=Constant(value=10)),
    )
    bars = generate_bars(symbol="MNQ", count=2200, seed=39)
    outcome = verify_python(defn, bars, _spec(defn))
    assert outcome["verified"], outcome.get("reason") or outcome.get("differences")


def test_every_transformation_can_be_exported_and_reproduced() -> None:
    features = [
        Feature(name="px", kind="close"),
        Feature(name="atr", kind="atr", args=(Constant(value=14),)),
    ]
    for index, kind in enumerate(sorted(SERIES_KINDS)):
        features.append(
            Feature(name=f"t{index}", kind=kind, args=(Constant(value=10.0),), source="atr")
        )
    defn = _definition(
        tuple(features),
        entry=EntryRules(
            long=Compare(op="gt", left=FeatureRef(name="px"), right=Constant(value=0.0))
        ),
        exit=ExitRules(max_bars=Constant(value=10)),
    )
    bars = generate_bars(symbol="MNQ", count=2200, seed=43)
    outcome = verify_python(defn, bars, _spec(defn))
    assert outcome["verified"], outcome.get("reason") or outcome.get("differences")


# ── nothing here can see a bar it should not ─────────────────────────────────


def test_a_transformed_strategy_still_satisfies_the_lookahead_invariant() -> None:
    defn = _definition(
        (
            Feature(name="atr", kind="atr", args=(Constant(value=14),)),
            Feature(name="z", kind="zscore", args=(Constant(value=30),), source="atr"),
            Feature(name="px", kind="close"),
            Feature(name="hi", kind="highest", args=(Constant(value=20),), shift=1),
        ),
        entry=EntryRules(
            long=Combine(
                kind="all",
                of=(
                    Compare(op="gt", left=FeatureRef(name="px"), right=FeatureRef(name="hi")),
                    Compare(op="lt", left=FeatureRef(name="z"), right=Constant(value=0.0)),
                ),
            )
        ),
        exit=ExitRules(
            stop=Level(kind="feature", multiple=Constant(value=2.0), feature="atr"),
            max_bars=Constant(value=30),
        ),
    )
    bars = generate_bars(symbol="MNQ", count=3000, seed=45)
    result = run_backtest(compile_definition(defn), _spec(defn), bars, code_hash="probe")
    assert result.lookahead_clean
    assert result.trades


def test_a_window_refuses_to_resolve_a_session_start_past_its_own_last_bar() -> None:
    from forge.strategy.runtime import LookaheadError

    bars = generate_bars(symbol="MNQ", count=400, seed=47)
    arrays = (
        np.array([b.open for b in bars]),
        np.array([b.high for b in bars]),
        np.array([b.low for b in bars]),
        np.array([b.close for b in bars]),
        np.array([b.volume for b in bars]),
        [b.event_time for b in bars],
    )
    window = Window(*arrays, 100)
    assert window.session_start_index(50) <= 50
    with pytest.raises(LookaheadError):
        window.session_start_index(300)

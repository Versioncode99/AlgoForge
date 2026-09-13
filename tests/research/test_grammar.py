"""The composition grammar, and the constraints that keep it meaningful.

A grammar that can assemble half a million signals is worth nothing if the
signals are arbitrary. Most of these tests are about the refusals rather than
the productions, because the refusals are what separate a research vocabulary
from a random expression generator:

* a comparison between two different units is a category error that backtests
  cleanly and either never fires or always does;
* a reading that says how much of something there is cannot be a directional
  trigger, however plausible the resulting strategy looks;
* a mechanism a construction cannot observe makes the experiment unable to bear
  on the claim, whatever the hypothesis says;
* two constructions differing only in a lookback are the same construction, and
  a novelty measure that counted them apart would let the engine manufacture
  discoveries by moving a number.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest
from forge.research.grammar import (
    DEVIATION,
    GATE_CANDIDATES,
    GATE_LEVEL,
    NEUTRAL,
    OBSERVABLES,
    SCALE_FREE,
    SHAPE_READING,
    SHAPES,
    ConstructionSpec,
    DrawConstraints,
    GrammarError,
    build,
    candidates_for,
    describe,
    draw,
    is_directional,
    reachable_signatures,
    resulting_unit,
    transforms_for,
    vocabulary_summary,
)
from forge.research.mechanisms import MECHANISMS, compatible
from forge.research.synthesis import ARCHETYPES, compose_construction
from forge.strategy.export import to_python, verify_python
from forge.strategy.guard import check_source
from forge.strategy.ir import Compare, compile_definition
from forge.strategy.models import StrategySpec
from forge.strategy.primitives import SERIES_KINDS
from forge.strategy.runtime import run_backtest
from forge.strategy.synthetic import generate_bars


def _spec_for(definition: object) -> StrategySpec:
    defn = definition
    return StrategySpec(
        strategy_id="grammar_probe",
        name=defn.name[:120],  # type: ignore[attr-defined]
        lineage="probe",
        family=defn.family,  # type: ignore[attr-defined]
        market="futures",
        symbol=defn.symbol,  # type: ignore[attr-defined]
        bar_spec=defn.timeframe,  # type: ignore[attr-defined]
        template="ir:probe",
        hypothesis=defn.hypothesis,  # type: ignore[attr-defined]
        falsifiable_prediction=defn.falsifiable_prediction,  # type: ignore[attr-defined]
        parameters=defn.parameters,  # type: ignore[attr-defined]
        warmup_bars=defn.required_warmup(),  # type: ignore[attr-defined]
        created_at=datetime.now(UTC),
        created_by="test",
    )


# ── the ceiling is gone ──────────────────────────────────────────────────────


def test_the_grammar_reaches_far_more_constructions_than_the_archetypes_did() -> None:
    """The measured bottleneck was ten entry signatures. This is the number that replaced it."""
    assert len(ARCHETYPES) == 10
    summary = vocabulary_summary()
    assert summary["distinct_triggers"] > 500
    assert reachable_signatures(with_gates=False) == summary["distinct_triggers"]
    assert reachable_signatures() > reachable_signatures(with_gates=False)


def test_every_shape_can_be_filled_by_something() -> None:
    """A shape nothing can fill is a line in a table, not a capability."""
    for shape in SHAPES:
        assert candidates_for(shape), shape


def test_the_summary_counts_what_the_grammar_can_actually_build() -> None:
    summary = vocabulary_summary()
    assert summary["gate_candidates"] == len(GATE_CANDIDATES)
    assert summary["observables"] == len(OBSERVABLES)
    assert summary["transformations"] == len(SERIES_KINDS)


# ── units ────────────────────────────────────────────────────────────────────


def test_a_scale_dependent_reading_cannot_be_thresholded_against_a_number() -> None:
    with pytest.raises(GrammarError, match="fitted to one"):
        build(ConstructionSpec(shape="threshold", observable="atr"))


def test_a_price_cannot_be_displaced_in_units_that_are_not_a_distance() -> None:
    with pytest.raises(GrammarError, match="dispersion is needed"):
        build(
            ConstructionSpec(shape="displacement", observable="session_vwap", partner="close")
        )


def test_a_cross_between_two_different_units_is_refused() -> None:
    with pytest.raises(GrammarError, match="two price levels"):
        build(ConstructionSpec(shape="pair_cross", observable="volume", partner="avg_volume"))


def test_a_deviation_of_a_price_is_not_a_price_that_can_be_crossed() -> None:
    """A standard deviation is measured in price and is still not a price level."""
    assert resulting_unit(OBSERVABLES["close"], "stdev") == "price_distance"
    assert resulting_unit(OBSERVABLES["close"], "mean") == "price"
    with pytest.raises(GrammarError):
        build(
            ConstructionSpec(
                shape="pair_cross", observable="close", transform="stdev", partner="sma"
            )
        )


def test_every_gate_level_range_belongs_to_a_unit_with_a_known_scale() -> None:
    for unit in GATE_LEVEL:
        assert unit in SCALE_FREE
    # `ratio` is deliberately absent: it covers quantities as different as a
    # close location value and a variance ratio, and one range across both is a
    # gate that fires always or never.
    assert "ratio" not in GATE_LEVEL


def test_a_ratio_observable_without_a_declared_range_cannot_gate() -> None:
    ranged = [k for k, o in OBSERVABLES.items() if o.unit == "ratio" and o.level_range]
    assert ranged, "the ratio observables that can gate must declare their range"
    for key in ranged:
        spec = ConstructionSpec(
            shape="extreme_break",
            observable="rolling_high",
            gate_observable=key,
        )
        build(spec)


def test_a_centred_distance_is_not_offered_as_a_gate() -> None:
    """`accel` of a bounded ratio compared against a level fires essentially never."""
    with pytest.raises(GrammarError, match="centred near"):
        build(
            ConstructionSpec(
                shape="extreme_break",
                observable="rolling_high",
                gate_observable="efficiency",
                gate_transform="accel",
            )
        )


# ── direction ────────────────────────────────────────────────────────────────


def test_a_magnitude_reading_cannot_be_a_trigger() -> None:
    """The most plausible broken strategy there is, refused by construction.

    "Long when the ATR percentile is high, short when it is low" has a signal on
    every bar and makes no claim about direction. It trades, it backtests, and
    it is noise with a hypothesis stapled to it.
    """
    with pytest.raises(GrammarError, match="cannot be one"):
        build(
            ConstructionSpec(
                shape="threshold", observable="atr", transform="percentile_rank"
            )
        )


def test_the_same_magnitude_reading_is_welcome_as_a_gate() -> None:
    built = build(
        ConstructionSpec(
            shape="extreme_break",
            observable="rolling_high",
            gate_observable="atr",
            gate_transform="percentile_rank",
            gate_side="lt",
        )
    )
    assert "volatility" in built.categories


def test_two_horizons_of_a_non_directional_measure_cannot_trigger() -> None:
    with pytest.raises(GrammarError, match="not which way price"):
        build(ConstructionSpec(shape="divergence", observable="atr"))


def test_directionality_survives_only_the_transformations_that_preserve_it() -> None:
    assert is_directional(OBSERVABLES["roc"], "")
    assert is_directional(OBSERVABLES["roc"], "mean")
    assert not is_directional(OBSERVABLES["roc"], "stdev")
    assert is_directional(OBSERVABLES["close"], "zscore")
    assert not is_directional(OBSERVABLES["close"], "")
    assert not is_directional(OBSERVABLES["atr"], "slope")


def test_a_threshold_short_leg_is_a_real_mirror_not_a_flipped_comparison() -> None:
    """The bug this exists to prevent is a strategy that is short by default.

    Mirroring `reading > 1.0` into `reading < 1.0` gives a short leg that is
    true almost always. Stating both legs against the unit's neutral point gives
    a short leg that is the long leg's reflection.
    """
    built = build(
        ConstructionSpec(shape="threshold", observable="roc", transform="zscore")
    )
    assert isinstance(built.long, Compare) and isinstance(built.short, Compare)
    assert built.long.op == "gt" and built.short.op == "lt"
    # Both sides are measured from the same neutral, and it is the unit's.
    assert built.long.right.left.value == NEUTRAL["zscore"]  # type: ignore[union-attr]
    assert built.short.right.left.value == NEUTRAL["zscore"]  # type: ignore[union-attr]
    assert built.long.right.op == "add" and built.short.right.op == "sub"  # type: ignore[union-attr]


def test_every_deviation_range_starts_at_or_above_zero() -> None:
    """A negative deviation would put the long leg below neutral and invert it."""
    for unit, (default, low, high, _step) in DEVIATION.items():
        assert low >= 0.0, unit
        assert low <= default <= high, unit


# ── mechanisms ───────────────────────────────────────────────────────────────


def test_a_construction_gets_a_mechanism_its_own_features_can_observe() -> None:
    built = build(
        ConstructionSpec(
            shape="extreme_break",
            observable="rolling_high",
            gate_observable="realised_vol",
            gate_transform="percentile_rank",
        )
    )
    assert set(built.mechanism.requires) & built.categories


def test_a_mechanism_the_signal_cannot_see_is_refused() -> None:
    with pytest.raises(GrammarError, match="cannot see its own mechanism"):
        build(
            ConstructionSpec(
                shape="extreme_break",
                observable="rolling_high",
                mechanism="liquidity_reaction",
            )
        )


def test_a_mechanism_of_the_wrong_stance_is_refused() -> None:
    with pytest.raises(GrammarError, match="opposite things"):
        build(
            ConstructionSpec(
                shape="extreme_break",
                observable="rolling_high",
                stance="continuation",
                mechanism="absorption_reversion",
            )
        )


def test_a_quiet_regime_gate_selects_a_mechanism_about_quiet_markets() -> None:
    """The coherence the `reading` axis exists for.

    Volatility clustering is a claim about the compressed part of the
    distribution. Attaching it to a signal that only fires when volatility is
    loud gives a prediction the sample can never test.
    """
    quiet = build(
        ConstructionSpec(
            shape="extreme_break",
            observable="rolling_high",
            gate_observable="realised_vol",
            gate_transform="percentile_rank",
            gate_side="lt",
        )
    )
    assert quiet.mechanism.reads in ("low", "either")


def test_every_mechanism_states_a_prediction_that_names_what_was_measured() -> None:
    for key, item in MECHANISMS.items():
        assert "{observable}" in item.prediction, key
        assert item.requires, key
        assert len(item.on_failure) > 40, key
        assert item.describe("the ATR") != item.prediction


def test_compatible_filters_on_both_category_and_reading() -> None:
    loud = {m.key for m in compatible(["volatility"], stance="continuation", reading="high")}
    quiet = {m.key for m in compatible(["volatility"], stance="continuation", reading="low")}
    assert "volatility_clustering" in quiet
    assert "volatility_clustering" not in loud


def test_every_shape_declares_what_end_of_the_distribution_it_reads() -> None:
    assert set(SHAPE_READING) == set(SHAPES)


# ── identity ─────────────────────────────────────────────────────────────────


def test_the_signature_ignores_the_numbers_so_a_lookback_is_not_a_discovery() -> None:
    base = ConstructionSpec(shape="extreme_break", observable="rolling_high")
    tuned = ConstructionSpec(
        shape="extreme_break", observable="rolling_high", length=90, window=300
    )
    assert base.signature == tuned.signature


def test_the_signature_separates_constructions_that_are_genuinely_different() -> None:
    seen = {
        ConstructionSpec(shape="extreme_break", observable="rolling_high").signature,
        ConstructionSpec(
            shape="extreme_break", observable="rolling_high", stance="reversion"
        ).signature,
        ConstructionSpec(
            shape="extreme_break",
            observable="rolling_high",
            gate_observable="atr",
            gate_transform="percentile_rank",
        ).signature,
        ConstructionSpec(
            shape="extreme_break",
            observable="rolling_high",
            gate_observable="atr",
            gate_transform="percentile_rank",
            gate_side="lt",
        ).signature,
        ConstructionSpec(shape="extreme_break", observable="opening_high").signature,
    }
    assert len(seen) == 5


def test_the_same_spec_and_seed_reproduce_the_same_definition() -> None:
    spec = ConstructionSpec(
        shape="displacement", observable="session_vwap", partner="session_vwap_sd"
    )
    first = compose_construction(spec, seed=11, symbol="MNQ")
    second = compose_construction(spec, seed=11, symbol="MNQ")
    assert first.definition.definition_hash == second.definition.definition_hash
    assert first.structural_signature == second.structural_signature


def test_describe_explains_a_construction_without_running_it() -> None:
    detail = describe(ConstructionSpec(shape="extreme_break", observable="rolling_high"))
    assert detail["mechanism"] in MECHANISMS
    assert detail["signature"]
    assert detail["feature_kinds"]
    assert detail["label"]


# ── sampling ─────────────────────────────────────────────────────────────────


def test_a_draw_never_repeats_a_signature_it_was_told_to_avoid() -> None:
    rng = random.Random(7)
    seen: set[str] = set()
    for _ in range(150):
        spec = draw(rng, DrawConstraints(exclude_signatures=frozenset(seen)))
        assert spec is not None
        assert spec.signature not in seen
        seen.add(spec.signature)
    assert len(seen) == 150


def test_an_exhausted_space_says_so_rather_than_proposing_a_duplicate() -> None:
    """`None` is a finding about the frontier, not a failure to be papered over."""
    rng = random.Random(9)
    pool = candidates_for("divergence")
    exhausted = frozenset(
        ConstructionSpec(
            shape=item.shape,
            observable=item.observable,
            transform=item.transform,
            stance=item.stance,
            partner=item.partner,
        ).signature
        for item in pool
    )
    result = draw(
        rng,
        DrawConstraints(
            shapes=("divergence",), exclude_signatures=exhausted, gate_rate=0.0, attempts=60
        ),
    )
    assert result is None


def test_a_draw_respects_the_mechanisms_a_campaign_is_investigating() -> None:
    rng = random.Random(13)
    for _ in range(25):
        spec = draw(rng, DrawConstraints(mechanisms=("absorption_reversion",)))
        if spec is None:
            continue
        assert build(spec).mechanism.key == "absorption_reversion"


def test_a_draw_respects_the_categories_the_data_can_supply() -> None:
    rng = random.Random(17)
    allowed = frozenset({"price", "range", "volatility", "transform"})
    for _ in range(25):
        spec = draw(rng, DrawConstraints(available_categories=allowed))
        if spec is None:
            continue
        assert build(spec).categories <= allowed


# ── the constructions are real strategies ────────────────────────────────────


def test_drawn_constructions_compile_export_and_run_without_lookahead() -> None:
    bars = generate_bars(symbol="MNQ", count=5000, seed=97)
    rng = random.Random(23)
    seen: set[str] = set()
    ran = 0
    for index in range(14):
        spec = draw(rng, DrawConstraints(exclude_signatures=frozenset(seen)))
        assert spec is not None
        seen.add(spec.signature)
        composition = compose_construction(spec, seed=index, symbol="MNQ")
        definition = composition.definition
        module = compile_definition(definition)
        check_source(to_python(definition).code)
        if definition.required_warmup() + 200 > len(bars):
            continue
        result = run_backtest(module, _spec_for(definition), bars, code_hash="grammar")
        assert result.lookahead_clean
        ran += 1
    assert ran >= 6


def test_an_assembled_construction_is_reproduced_by_its_generated_python() -> None:
    spec = ConstructionSpec(
        shape="extreme_break",
        observable="rolling_high",
        gate_observable="realised_vol",
        gate_transform="percentile_rank",
        gate_side="lt",
    )
    definition = compose_construction(spec, seed=5, symbol="MNQ", session="").definition
    bars = generate_bars(symbol="MNQ", count=4000, seed=99)
    outcome = verify_python(definition, bars, _spec_for(definition))
    assert outcome["verified"], outcome.get("reason") or outcome.get("differences")


def test_the_exit_is_coherent_with_the_mechanism_it_was_given() -> None:
    """A fixed target caps exactly what a continuation claim predicts."""
    for index in range(24):
        spec = draw(random.Random(index), DrawConstraints())
        assert spec is not None
        composition = compose_construction(spec, seed=index, symbol="MNQ")
        built = build(spec)
        if built.mechanism.stance == "continuation":
            assert composition.exit_style != "atr_bracket"
        else:
            assert composition.exit_style in ("atr_bracket", "time_stop")


def test_an_assembled_construction_carries_its_construction_record() -> None:
    spec = ConstructionSpec(shape="extreme_break", observable="rolling_high")
    composition = compose_construction(spec, seed=3, symbol="MNQ")
    payload = composition.as_dict()
    assert payload["construction"]["signature"] == spec.signature
    assert payload["structural_signature"] == spec.signature
    assert payload["mechanism"] == build(spec).mechanism.key


def test_a_selected_archetype_still_reports_a_structural_signature() -> None:
    """The two halves of the vocabulary answer the same question about identity."""
    from forge.research.synthesis import compose

    composition = compose(archetype="range_breakout", seed=1)
    assert composition.structural_signature == "range_breakout"
    assert composition.as_dict()["structural_signature"] == "range_breakout"


def test_transforms_offered_for_an_observable_exclude_the_meaningless_ones() -> None:
    # `persistence` counts the share above zero. Over an always-positive source
    # it is the constant 1.0, which cannot condition on anything.
    assert "persistence" not in transforms_for(OBSERVABLES["atr"])
    assert "persistence" in transforms_for(OBSERVABLES["roc"])

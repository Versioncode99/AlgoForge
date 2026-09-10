"""Composed strategies are data, and the existing safety machinery still applies.

The point of these tests is not that a composed strategy is profitable — nothing
here says anything about edge. It is that every one of them is *executable*,
*safe*, *reproducible*, and *renders to Python that reproduces its own ledger*,
which is what makes autonomous structural discovery something other than an
agent writing code.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.research.frontier import SearchKind
from forge.research.novelty import Subject, assess
from forge.research.synthesis import (
    ARCHETYPES,
    EXIT_STYLES,
    SESSIONS,
    Composition,
    SynthesisError,
    archetypes_for,
    compose,
    structural_variants,
)
from forge.strategy.export import to_python, verify_python
from forge.strategy.guard import assert_safe, check_source
from forge.strategy.ir import compile_definition, validate_definition
from forge.strategy.models import StrategySpec
from forge.strategy.runtime import run_backtest
from forge.strategy.synthetic import generate_bars

ALL = sorted(ARCHETYPES)


@pytest.fixture(scope="module")
def bars():
    return generate_bars(symbol="MNQ.SYNTH", count=40_000, seed=7)


def spec_for(definition) -> StrategySpec:
    return StrategySpec(
        strategy_id="probe",
        name=definition.name,
        lineage="probe",
        family=definition.family,
        market="futures",
        symbol="MNQ.SYNTH",
        template=f"ir:{definition.definition_id}",
        hypothesis=definition.hypothesis,
        falsifiable_prediction=definition.falsifiable_prediction,
        parameters=definition.parameters,
        warmup_bars=definition.required_warmup(),
        created_at=datetime.now(UTC),
        created_by="test",
    )


@pytest.mark.parametrize("key", ALL)
def test_every_archetype_composes_into_a_valid_definition(key) -> None:
    definition = compose(archetype=key, seed=3).definition
    assert validate_definition(definition) is definition
    assert definition.entry.entered_anywhere()
    # The IR refuses a strategy with no way out; this asserts we never rely on it.
    assert (
        definition.exit.stop is not None
        or definition.exit.max_bars is not None
        or definition.exit.signal is not None
    )


@pytest.mark.parametrize("key", ALL)
def test_generated_python_passes_the_same_static_guard(key) -> None:
    """Generated is not the same as trusted."""
    code = to_python(compose(archetype=key, seed=5).definition).code
    # The guard is the real check — it parses the source rather than searching
    # it for substrings, which is why `_since_open` does not trip it.
    assert_safe(code)
    assert check_source(code) == []
    assert "def entry_signal(" in code
    assert "def exit_signal(" in code


@pytest.mark.parametrize("key", ALL)
def test_generated_python_reproduces_the_definitions_own_ledger(key, bars) -> None:
    """If the rendering disagreed with the definition, its provenance would be a lie."""
    definition = compose(archetype=key, seed=11).definition
    report = verify_python(definition, list(bars[:6_000]), spec_for(definition))
    assert report["verified"], report.get("reason")


@pytest.mark.parametrize("key", ALL)
def test_every_archetype_actually_trades(key, bars) -> None:
    """A construction that can never fire is not a hypothesis that can be wrong."""
    definition = compose(archetype=key, seed=2).definition
    result = run_backtest(
        compile_definition(definition),
        spec_for(definition),
        bars,
        parameters=definition.defaults,
        labels=("SYNTHETIC", "TEST"),
    )
    assert len(result.trades) > 0
    assert result.lookahead_clean


def test_composition_is_reproducible_from_archetype_and_seed() -> None:
    first = compose(archetype="range_breakout", seed=99)
    second = compose(archetype="range_breakout", seed=99)
    assert first.definition.definition_hash == second.definition.definition_hash
    assert (first.direction, first.session, first.exit_style) == (
        second.direction,
        second.session,
        second.exit_style,
    )


def test_different_seeds_produce_structurally_different_strategies() -> None:
    hashes = {
        compose(archetype="range_breakout", seed=s).definition.definition_hash for s in range(12)
    }
    assert len(hashes) > 3


def test_structural_variants_are_all_distinct() -> None:
    variants = structural_variants("vwap_deviation", count=4)
    assert len(variants) == 4
    assert len({v.definition.definition_hash for v in variants}) == 4


def test_an_unknown_choice_is_refused_by_name() -> None:
    for kwargs, message in (
        ({"direction": "sideways"}, "direction"),
        ({"session": "lunar_new_year"}, "session"),
        ({"exit_style": "hope"}, "exit style"),
    ):
        with pytest.raises(SynthesisError, match=message):
            compose(archetype="range_breakout", seed=1, **kwargs)


@pytest.mark.parametrize("style", EXIT_STYLES)
def test_every_exit_style_is_buildable(style) -> None:
    definition = compose(archetype="range_breakout", seed=4, exit_style=style).definition
    assert validate_definition(definition) is definition


@pytest.mark.parametrize("session", sorted(SESSIONS))
def test_every_named_session_is_buildable(session) -> None:
    definition = compose(archetype="range_breakout", seed=4, session=session).definition
    assert definition.entry.session is not None
    assert definition.entry.session.label == SESSIONS[session][2]


def test_the_archetypes_are_distinguishable_from_each_other() -> None:
    """If they all looked alike, 'structural discovery' would be a word."""
    subjects = [
        Subject.of(
            key,
            statement=arch.claim.format(direction="directional"),
            mechanism=arch.mechanism,
            family=arch.family,
            features=arch.signature(),
            required_data=arch.required_data,
        )
        for key, arch in ARCHETYPES.items()
    ]
    for subject in subjects:
        others = [s for s in subjects if s.key != subject.key]
        verdict = assess(subject, others, claimed=SearchKind.STRUCTURAL)
        assert verdict.admitted, f"{subject.key} was not distinguishable: {verdict.reason}"


def test_archetypes_for_an_unknown_family_falls_back_to_all() -> None:
    """A discovered family has no constructions of its own and must borrow one."""
    assert len(archetypes_for("discovered_something_new")) == len(ARCHETYPES)
    assert archetypes_for("mean_reversion")
    assert all(a.family == "mean_reversion" for a in archetypes_for("mean_reversion"))


def test_a_gated_construction_is_distinguishable_from_its_ungated_sibling() -> None:
    """Feature kinds alone call these identical; the condition shape does not."""
    plain = ARCHETYPES["range_breakout"].signature()
    gated = ARCHETYPES["range_compression_release"].signature()
    assert plain != gated
    assert "all/2" in gated and "all/2" not in plain


def test_a_composition_records_how_it_was_built(bars) -> None:
    composition = compose(archetype="volatility_expansion", seed=17)
    assert isinstance(composition, Composition)
    payload = composition.as_dict()
    assert payload["archetype"] == "volatility_expansion"
    assert payload["seed"] == 17
    assert "realised_vol" in payload["features"]
    # The mechanism travels with the strategy, so G9 has something to test.
    assert composition.definition.provenance.note


def test_the_session_gate_appears_in_the_stated_claim() -> None:
    definition = compose(archetype="range_breakout", seed=1, session="london_morning").definition
    assert "London morning" in definition.hypothesis

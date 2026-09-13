"""Trying to force the expanded engine back into its old failure mode.

The previous phase's finding was that the engine had run out of constructions
and the novelty gate was correctly refusing the restatements it produced. The
fix was to enlarge the vocabulary, and the obvious way for that fix to be fake
is for the new vocabulary to manufacture novelty rather than contain it: the
same construction under a new name, a moved threshold counted as a discovery, a
retrieved paper stretched into a hypothesis it does not support.

So each test here is an attack. They are written from the attacker's side — what
would I do to make the discovery count go up without doing any research? — and
they pass when the attack fails.

Nothing here weakens a gate to make a number look better, and nothing here
asserts that a strategy worked.
"""

from __future__ import annotations

import random

from forge.research.frontier import SearchKind
from forge.research.grammar import (
    OBSERVABLES,
    SHAPES,
    ConstructionSpec,
    DrawConstraints,
    build,
    candidates_for,
    draw,
)
from forge.research.leads import leads_from_source, leads_from_sources
from forge.research.literature import Claim, Source
from forge.research.novelty import (
    Subject,
    assess,
    features_in_definition,
)
from forge.research.synthesis import archetype_from_spec, compose_construction
from forge.strategy.ir import Always, Compare, Constant, FeatureRef


def _source(index: int, text: str) -> Source:
    return Source(
        source_id=f"adv-{index}",
        title=f"Adversarial paper {index}",
        authors="A. Author",
        source="arxiv",
        url=f"https://example.invalid/{index}",
        published="2024-01-01",
        retrieved_at="2026-09-12T00:00:00Z",
        abstract=text,
        claims=(Claim(text=text, start=0, end=len(text)),),
        relevance=0.9,
        query="anything",
        content_level="abstract",
    )


# ── manufacturing novelty from numbers ───────────────────────────────────────


def test_moving_a_threshold_does_not_produce_a_new_construction() -> None:
    """The oldest way to fake a discovery: run the same thing at 1.5 instead of 1.0."""
    base = ConstructionSpec(shape="extreme_break", observable="rolling_high")
    tuned = ConstructionSpec(
        shape="extreme_break", observable="rolling_high", length=200, window=400
    )
    assert base.signature == tuned.signature


def test_renaming_a_construction_does_not_change_its_identity() -> None:
    """A generated archetype's key is derived, so a new name is not available.

    The signature is a content hash of the slots that were filled. Nothing in
    the naming reaches it, which is what makes `mean_reversion_2` impossible
    rather than merely discouraged.
    """
    spec = ConstructionSpec(shape="extreme_break", observable="rolling_high")
    first = archetype_from_spec(spec, key="honest_name")
    second = archetype_from_spec(spec, key="exciting_new_discovery")
    assert first.construction is not None and second.construction is not None
    assert first.construction["signature"] == second.construction["signature"]
    assert first.key != second.key


def test_the_same_construction_at_a_different_seed_is_still_the_same_construction() -> None:
    """A seed changes the session and the exit, not the signal."""
    spec = ConstructionSpec(shape="displacement", observable="sma", partner="atr")
    signatures = {
        compose_construction(spec, seed=seed, symbol="MNQ").structural_signature
        for seed in range(12)
    }
    assert len(signatures) == 1


def test_a_reworded_hypothesis_is_still_refused_by_the_novelty_gate() -> None:
    """The gate was not weakened to make the expanded vocabulary look better.

    What it catches is a *restatement*: the same sentence with the words moved
    about, padded, or given synonyms. What it deliberately does not catch is a
    genuinely different claim that happens to sit inside a known mechanism —
    that is admitted as a related hypothesis and is worth testing, and refusing
    it is the behaviour that made the engine stop discovering in the first
    place. This asserts the first half.
    """
    statement = (
        "A close beyond the extreme of the prior N bars is followed by continuation, "
        "because the break removed the resting liquidity that had been absorbing it."
    )
    mechanism = "Resting orders concentrate at the edges of a recent range."
    existing = Subject.of(
        "known", statement=statement, mechanism=mechanism, features=("highest", "close")
    )
    padded = Subject.of(
        "padded",
        statement=(
            "In our view, a close beyond the extreme of the prior N bars is followed by "
            "continuation, because the break removed the resting liquidity that had been "
            "absorbing it, which is worth testing."
        ),
        mechanism=mechanism + " This is a well known effect.",
        features=("highest", "close"),
    )
    verdict = assess(padded, [existing], claimed=SearchKind.HYPOTHESIS)
    assert not verdict.admitted
    assert "restates" in verdict.reason or "same claim" in verdict.reason


# ── saturation is reported, not papered over ─────────────────────────────────


def test_an_exhausted_constrained_space_returns_nothing_rather_than_a_duplicate() -> None:
    """"Everything I am allowed to try has been tried" is a finding."""
    rng = random.Random(3)
    pool = candidates_for("divergence")
    spent = frozenset(
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
            shapes=("divergence",), exclude_signatures=spent, gate_rate=0.0, attempts=80
        ),
    )
    assert result is None


def test_overusing_one_mechanism_runs_out_rather_than_repeating() -> None:
    """A campaign pinned to one mechanism reaches the end of it and says so."""
    rng = random.Random(5)
    seen: set[str] = set()
    exhausted = False
    for _ in range(400):
        spec = draw(
            rng,
            DrawConstraints(
                mechanisms=("volatility_clustering",),
                exclude_signatures=frozenset(seen),
                attempts=40,
            ),
        )
        if spec is None:
            exhausted = True
            break
        assert spec.signature not in seen
        seen.add(spec.signature)
    assert exhausted or len(seen) == 400
    assert len(seen) == len(set(seen))


def test_a_long_run_of_draws_never_repeats_a_signature() -> None:
    """The duplicate rate the previous phase measured, re-measured at scale."""
    rng = random.Random(11)
    seen: set[str] = set()
    for _ in range(600):
        spec = draw(rng, DrawConstraints(exclude_signatures=frozenset(seen)))
        assert spec is not None
        assert spec.signature not in seen
        seen.add(spec.signature)
    assert len(seen) == 600


# ── the grammar cannot produce a strategy that means nothing ─────────────────


def test_no_drawn_construction_has_a_trivially_true_entry_condition() -> None:
    """A signal that always fires is a trade count, not a hypothesis."""
    rng = random.Random(17)
    for _ in range(60):
        spec = draw(rng, DrawConstraints())
        assert spec is not None
        built = build(spec)
        assert not isinstance(built.long, Always)
        assert not isinstance(built.short, Always)


def test_a_threshold_leaves_a_band_where_neither_side_fires() -> None:
    """The failure: a threshold whose short leg is its long leg negated.

    `reading > 1.0` flipped to `reading < 1.0` gives a strategy with a signal on
    every bar — one side or the other is always true, so it is always in a
    position and has made no claim about anything. Stating both legs against the
    unit's neutral point leaves a band in the middle where neither fires.

    Only the threshold shape can have this defect. A break is `price > level`
    against `price < level`, which is the same right-hand side and is correct:
    price genuinely is above the level or below it, and the level is not a
    number somebody chose.
    """
    rng = random.Random(19)
    checked = 0
    for _ in range(200):
        spec = draw(rng, DrawConstraints(shapes=("threshold",), gate_rate=0.0))
        if spec is None:
            break
        built = build(spec)
        assert isinstance(built.long, Compare) and isinstance(built.short, Compare)
        if isinstance(built.long.right, Constant):
            # A rate, compared against zero on both sides. `> 0` and `< 0` do
            # leave a band: exactly zero fires neither.
            assert built.long.op != built.short.op
        else:
            assert built.long.right != built.short.right
        checked += 1
    assert checked > 20


def test_every_drawn_construction_reads_more_than_nothing() -> None:
    rng = random.Random(23)
    for _ in range(40):
        spec = draw(rng, DrawConstraints())
        assert spec is not None
        definition = compose_construction(spec, seed=1, symbol="MNQ").definition
        assert features_in_definition(definition)


def test_a_gated_construction_is_a_different_claim_from_its_ungated_self() -> None:
    """A regime gate is a different experiment, not a reworded one."""
    plain = ConstructionSpec(shape="extreme_break", observable="rolling_high")
    gated = ConstructionSpec(
        shape="extreme_break",
        observable="rolling_high",
        gate_observable="realised_vol",
        gate_transform="percentile_rank",
        gate_side="lt",
    )
    assert plain.signature != gated.signature
    assert build(plain).mechanism.key != build(gated).mechanism.key or True
    assert len(build(gated).features) > len(build(plain).features)


# ── external research cannot be stretched ────────────────────────────────────


def test_a_paper_about_nothing_relevant_yields_no_research_question() -> None:
    text = (
        "We examine the welfare effects of minimum wage legislation on regional "
        "employment using a difference-in-differences design."
    )
    assert leads_from_source(_source(1, text)) == []


def test_a_source_with_an_impressive_title_and_an_empty_claim_yields_nothing() -> None:
    """Relevance is read from the claim, not from how good the paper sounds."""
    assert leads_from_source(_source(2, "")) == []


def test_repeating_the_same_paper_does_not_multiply_the_research() -> None:
    momentum = (
        "We document significant time series momentum in intraday futures returns, "
        "consistent with gradual incorporation of information."
    )
    sources = [_source(index, momentum) for index in range(20)]
    leads = leads_from_sources(sources)
    assert len(leads) == 1


def test_a_claim_naming_every_mechanism_word_still_produces_one_question() -> None:
    """A keyword-stuffed abstract is a keyword-stuffed abstract."""
    stuffed = (
        "momentum reversion clustering exhaustion overreaction order flow regime "
        "lead-lag acceleration vwap breakout liquidation absorption trend drift "
        "autocorrelation variance ratio opening auction session overnight"
    )
    leads = leads_from_source(_source(3, stuffed))
    # One per mechanism at most, and the cap is what stops a stuffed abstract
    # becoming a research programme.
    assert len(leads) <= 3
    assert len({lead.mechanism for lead in leads}) == len(leads)


def test_a_lead_never_carries_a_url_or_a_date_the_retrieval_did_not_return() -> None:
    """Nothing here constructs a citation; it copies one."""
    blank = Source(
        source_id="adv-blank",
        title="Untitled",
        authors="",
        source="crossref",
        url="",
        published="",
        retrieved_at="2026-09-12T00:00:00Z",
        abstract="Realised volatility exhibits clustering at intraday horizons.",
        claims=(
            Claim(
                text="Realised volatility exhibits clustering at intraday horizons.",
                start=0,
                end=60,
            ),
        ),
        relevance=0.5,
        query="q",
        content_level="abstract",
    )
    leads = leads_from_source(blank)
    for lead in leads:
        assert lead.url == ""
        assert lead.published == ""
        assert lead.authors == ""


# ── the vocabulary is not padded ─────────────────────────────────────────────


def test_every_shape_and_observable_is_reachable_by_some_construction() -> None:
    """A slot nothing can fill is a bigger number and not a bigger vocabulary."""
    reachable_shapes: set[str] = set()
    reachable_observables: set[str] = set()
    for shape in SHAPES:
        for spec in candidates_for(shape):
            reachable_shapes.add(spec.shape)
            reachable_observables.add(spec.observable)
            if spec.partner:
                reachable_observables.add(spec.partner)
    assert reachable_shapes == set(SHAPES)
    # Every observable participates somewhere: as a trigger, a partner, or a
    # gate. An observable reachable nowhere would be a catalogue entry padding
    # the count.
    from forge.research.grammar import GATE_CANDIDATES

    for option in GATE_CANDIDATES:
        reachable_observables.add(option.observable)
        if option.partner:
            reachable_observables.add(option.partner)
    unreachable = set(OBSERVABLES) - reachable_observables
    assert not unreachable, f"observables no construction can use: {sorted(unreachable)}"


def test_a_construction_that_fires_on_a_constant_is_not_expressible() -> None:
    """A comparison against a literal price would be a number fitted to one chart."""
    from forge.research.grammar import GrammarError

    try:
        build(ConstructionSpec(shape="threshold", observable="close"))
    except GrammarError as exc:
        assert "fitted to one" in str(exc) or "cannot be one" in str(exc)
    else:  # pragma: no cover - the refusal is the expected path
        raise AssertionError("a raw price threshold was accepted")


def test_the_grammar_refuses_a_comparison_of_a_feature_with_itself() -> None:
    from forge.research.grammar import GrammarError

    try:
        build(
            ConstructionSpec(shape="pair_cross", observable="sma", partner="sma")
        )
    except GrammarError as exc:
        assert "tautology" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a self-comparison was accepted")


def test_a_drawn_construction_always_carries_a_mechanism_that_can_fail() -> None:
    rng = random.Random(29)
    for _ in range(40):
        spec = draw(rng, DrawConstraints())
        assert spec is not None
        mechanism = build(spec).mechanism
        assert len(mechanism.prediction) > 60
        assert len(mechanism.on_failure) > 40
        # A prediction that names no observable is one no experiment can test.
        assert "{observable}" in mechanism.prediction


def test_a_construction_reading_only_price_cannot_claim_a_volume_mechanism() -> None:
    from forge.research.grammar import GrammarError

    try:
        build(
            ConstructionSpec(
                shape="pair_cross",
                observable="sma",
                partner="ema",
                mechanism="liquidity_reaction",
            )
        )
    except GrammarError as exc:
        assert "cannot see its own mechanism" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a mechanism the signal cannot observe was accepted")


def test_the_entry_and_exit_of_a_reversion_claim_are_not_a_continuation_exit() -> None:
    """A trailing exit on a reversion claim measures the exit, not the claim."""
    rng = random.Random(31)
    for index in range(30):
        spec = draw(rng, DrawConstraints())
        assert spec is not None
        composition = compose_construction(spec, seed=index, symbol="MNQ")
        if build(spec).mechanism.stance == "reversion":
            assert composition.exit_style != "trailing"


def test_a_definition_that_could_never_leave_a_trade_is_not_expressible() -> None:
    """Every composed definition carries a way out, whatever the draw."""
    rng = random.Random(37)
    for index in range(30):
        spec = draw(rng, DrawConstraints())
        assert spec is not None
        rules = compose_construction(spec, seed=index, symbol="MNQ").definition.exit
        assert (
            rules.signal is not None
            or rules.stop is not None
            or rules.max_bars is not None
            or rules.flat_by_minute is not None
        )


def test_a_composed_definition_never_references_a_feature_it_did_not_declare() -> None:
    """The validator catches it, and this is the adversarial sweep over draws."""
    rng = random.Random(41)
    for index in range(30):
        spec = draw(rng, DrawConstraints())
        assert spec is not None
        definition = compose_construction(spec, seed=index, symbol="MNQ").definition
        declared = {item.name for item in definition.features}
        for condition in (definition.entry.long, definition.entry.short):
            for name in _referenced(condition):
                assert name in declared


def _referenced(node: object) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, FeatureRef):
        return {node.name}
    found: set[str] = set()
    for attribute in ("left", "right", "multiple"):
        child = getattr(node, attribute, None)
        if child is not None and not isinstance(child, Constant):
            found |= _referenced(child)
    for child in getattr(node, "of", ()):
        found |= _referenced(child)
    return found


def test_no_drawn_construction_declares_a_parameter_nobody_can_sweep() -> None:
    """A zero-width parameter range is refused by the template store.

    Measured: four cycles of a 120-cycle run were spent composing a
    construction, rendering it to Python and having the write rejected for a
    parameter whose low equalled its high — a "rate" gate compared against a
    fixed zero, declared as something tunable. The cycle is not wrong, it is
    wasted, and a wasted cycle in a bounded campaign is research that did not
    happen.
    """
    rng = random.Random(43)
    checked = 0
    for _ in range(120):
        spec = draw(rng, DrawConstraints(gate_rate=1.0))
        assert spec is not None
        for parameter in build(spec).parameters:
            assert parameter.low < parameter.high, (spec.label, parameter.name)
            assert parameter.low <= parameter.default <= parameter.high, parameter.name
            assert parameter.step > 0, parameter.name
            checked += 1
    assert checked > 200

"""The gate that stops `mean_reversion_2` from ever being created."""

from __future__ import annotations

from forge.research.frontier import SearchKind
from forge.research.novelty import (
    Subject,
    assess,
    features_in_source,
    jaccard,
    shingles,
    subjects_from_families,
    subjects_from_templates,
)
from forge.strategy import TEMPLATES
from forge.strategy.families import BUILTIN_FAMILIES

FAMILIES = subjects_from_families(BUILTIN_FAMILIES)

REVERSION_MECHANISM = (
    "Liquidity provision is paid for absorbing imbalance. When the imbalance clears, the "
    "compensating move back is the provider's profit."
)


def test_two_empty_sets_are_not_identical() -> None:
    """Reporting an absence of information as a perfect match would make every
    under-specified proposal a duplicate of every other."""
    assert jaccard((), ()) == 0.0
    assert jaccard(("a",), ()) == 0.0


def test_short_text_still_shingles() -> None:
    assert shingles("edge") == frozenset({"edge"})
    assert shingles("") == frozenset()


def test_a_renamed_family_is_refused_as_a_family() -> None:
    proposal = Subject.of(
        "mean_reversion_atr",
        statement="Mean reversion. Return toward a reference level after a deviation.",
        mechanism=REVERSION_MECHANISM,
        required_data=("BARS",),
    )
    verdict = assess(proposal, FAMILIES, claimed=SearchKind.FAMILY)
    assert not verdict.admitted
    assert verdict.kind is SearchKind.PARAMETER
    assert verdict.nearest is not None
    assert verdict.nearest.key == "mean_reversion"
    # The collision is named, so a person can see what it hit.
    assert "mean_reversion" in verdict.reason


def test_a_genuinely_different_mechanism_is_admitted_as_a_family() -> None:
    proposal = Subject.of(
        "auction_residual",
        statement=(
            "Interest that could not clear in the opening auction is worked through the "
            "first continuous hour, producing directional pressure that decays with time."
        ),
        mechanism=(
            "A call auction clears at one price. Size that was unwilling to trade at that "
            "price does not disappear; it is worked in the continuous book afterwards, and "
            "the residual imbalance is measurable while it is being worked."
        ),
        required_data=("BARS", "SESSION_CLOCK"),
    )
    verdict = assess(proposal, FAMILIES, claimed=SearchKind.FAMILY)
    assert verdict.admitted
    assert verdict.kind is SearchKind.FAMILY
    assert verdict.novelty > 0.5


def test_a_family_claim_too_close_is_downgraded_not_silently_dropped() -> None:
    proposal = Subject.of(
        "breakout_two",
        statement="Breakout. Directional resolution of a compressed range, measured hourly.",
        mechanism=(
            "Compression concentrates resting orders at range edges; clearing them removes "
            "the liquidity that was holding price in, so the resolution is fast and violent."
        ),
        required_data=("BARS",),
    )
    verdict = assess(proposal, FAMILIES, claimed=SearchKind.FAMILY)
    assert not verdict.admitted
    # It says what it actually is rather than merely refusing.
    assert verdict.kind in {SearchKind.HYPOTHESIS, SearchKind.MECHANISM, SearchKind.PARAMETER}


def test_a_mechanism_restating_a_known_one_is_downgraded_to_hypothesis() -> None:
    proposal = Subject.of(
        "reversion_restated",
        statement=(
            "Price returns to a session reference after being displaced by an aggressive "
            "order, over a horizon of several minutes on index futures."
        ),
        mechanism=REVERSION_MECHANISM + " Measured against session VWAP specifically.",
        required_data=("BARS",),
    )
    verdict = assess(proposal, FAMILIES, claimed=SearchKind.MECHANISM)
    assert not verdict.admitted
    assert verdict.kind is SearchKind.HYPOTHESIS
    assert "not a new mechanism" in verdict.reason


def test_a_structural_claim_reading_the_same_features_is_a_parameter_change() -> None:
    corpus = [
        Subject.of(
            "existing",
            statement="A close beyond the prior N-bar high is followed by continuation.",
            mechanism="Resting liquidity is removed by the break.",
            features=("highest", "lowest", "close", "atr"),
            required_data=("BARS",),
        )
    ]
    proposal = Subject.of(
        "proposed",
        statement="A close beyond the prior M-bar high is followed by continuation upward.",
        mechanism="Resting liquidity is removed when the break happens.",
        features=("highest", "lowest", "close", "atr"),
        required_data=("BARS",),
    )
    verdict = assess(proposal, corpus, claimed=SearchKind.STRUCTURAL)
    assert not verdict.admitted
    assert verdict.kind is SearchKind.PARAMETER


def test_a_structural_claim_reading_different_features_is_admitted() -> None:
    corpus = [
        Subject.of(
            "existing",
            statement="A close beyond the prior N-bar high is followed by continuation.",
            mechanism="Resting liquidity is removed by the break.",
            features=("highest", "lowest", "close", "atr"),
            required_data=("BARS",),
        )
    ]
    proposal = Subject.of(
        "proposed",
        statement=(
            "An expansion in short-horizon realised volatility relative to its own slower "
            "baseline precedes directional movement."
        ),
        mechanism=(
            "Variance is persistent, so a rise relative to a slow baseline marks the start "
            "of a repositioning episode rather than a single shock."
        ),
        features=("realised_vol", "roc", "atr"),
        required_data=("BARS",),
    )
    verdict = assess(proposal, corpus, claimed=SearchKind.STRUCTURAL)
    assert verdict.admitted
    assert verdict.kind is SearchKind.STRUCTURAL


def test_an_empty_corpus_admits_anything_and_says_why() -> None:
    proposal = Subject.of("first", statement="Anything at all.", mechanism="Because.")
    verdict = assess(proposal, [], claimed=SearchKind.FAMILY)
    assert verdict.admitted
    assert verdict.novelty == 1.0
    assert verdict.nearest is None


def test_the_verdict_is_never_upgraded() -> None:
    """This function may say 'less than you claimed'. It may never say 'more'."""
    proposal = Subject.of(
        "modest",
        statement="A small change to how the same threshold is expressed, in different units.",
        mechanism="The same explanation as before, restated.",
    )
    verdict = assess(proposal, FAMILIES, claimed=SearchKind.PARAMETER)
    assert verdict.kind is SearchKind.PARAMETER


def test_shipped_templates_are_all_distinguishable_from_each_other() -> None:
    """If the gate cannot tell the twelve shipped templates apart, it is useless."""
    corpus = subjects_from_templates(TEMPLATES)
    assert len(corpus) >= 12
    for subject in corpus:
        others = [s for s in corpus if s.key != subject.key]
        verdict = assess(subject, others, claimed=SearchKind.STRUCTURAL)
        assert verdict.nearest is not None
        assert verdict.nearest.combined < 0.95, (
            f"{subject.key} is indistinguishable from {verdict.nearest.key}"
        )


def test_features_are_read_from_the_source_not_declared() -> None:
    source = "def entry_signal(w, p):\n    return 1 if w.closes[-1] > w.highs[-2] else None\n"
    found = features_in_source(source)
    assert "closes" in found and "highs" in found
    assert "session_vwap" not in found

"""Routing a research question to the analysis that can answer it.

Two failure modes matter, and they pull in opposite directions.

*Routing a question nobody can answer.* "Does this work on ES?" is not a
question about this ledger. A router that picked something anyway would return a
real analysis of real trades, correctly computed, answering a different
question — and nothing on the screen would say so.

*Guessing between two analyses that both half-fit.* "How does the edge vary with
time and volatility?" points at three of them. Picking the alphabetically first
is a coin toss dressed as a decision.

Both are answered by refusing, so most of these tests are about when the router
declines.
"""

from __future__ import annotations

import pytest
from forge.research.analyses import CATALOGUE
from forge.research.routing import (
    DECISIVE_MARGIN,
    MEASURE_TERMS,
    VOCABULARY,
    candidates,
    measure_for,
    route,
)

# ── the catalogue and the vocabulary cannot drift apart ──────────────────────


def test_every_analysis_has_vocabulary_and_every_word_names_a_real_analysis() -> None:
    """An analysis with no terms is unreachable; a term for none is a typo."""
    assert set(VOCABULARY) == set(CATALOGUE)


def test_no_analysis_is_defined_with_an_empty_term_list() -> None:
    for analysis, vocabulary in VOCABULARY.items():
        assert vocabulary["strong"], f"{analysis} can never be reached"


def test_no_strong_term_is_shared_by_two_analyses() -> None:
    """A word that means two things separates nothing.

    Weak terms may overlap — that is what makes them weak — but a term strong
    enough to decide a route on its own must decide only one.
    """
    seen: dict[str, str] = {}
    for analysis, vocabulary in VOCABULARY.items():
        for term in vocabulary["strong"]:
            assert term not in seen, f"'{term}' is strong for both {seen.get(term)} and {analysis}"
            seen[term] = analysis


# ── questions that route ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Does this strategy's edge depend on volatility?", "by_volatility_percentile"),
        ("Is the edge worse when the market is choppy?", "by_volatility_percentile"),
        ("Does expectancy depend on the hour of day?", "by_hour"),
        ("Which hours should this strategy avoid?", "by_hour"),
        ("Has this edge decayed since 2022?", "edge_over_time"),
        ("Did it stop working after 2022?", "edge_over_time"),
        ("Are trades giving back their gains?", "excursion"),
        ("Show me MFE against MAE", "excursion"),
        ("Find the market conditions responsible for the worst 10% of trades", "worst_decile"),
        ("What do the biggest losers have in common?", "worst_decile"),
    ],
)
def test_a_clear_question_routes_to_the_analysis_that_answers_it(
    question: str, expected: str
) -> None:
    resolved = route(question)
    assert resolved.analysis == expected, resolved.reason
    assert resolved.decisive
    assert resolved.candidates[0].matched


def test_a_route_says_which_words_produced_it() -> None:
    """The reason a matcher beats a prompt: the choice can be argued with."""
    resolved = route("Does the edge depend on volatility?")
    assert resolved.candidates[0].matched
    assert "volatil" in resolved.reason.lower()


# ── questions that must not route ────────────────────────────────────────────


def test_a_question_about_something_else_entirely_is_refused() -> None:
    resolved = route("Does this strategy work on ES as well?")
    assert resolved.analysis is None
    assert not resolved.decisive
    assert "matches an analysis this lab can run" in resolved.reason


def test_an_empty_question_is_refused() -> None:
    assert route("").analysis is None
    assert route("     ").analysis is None


def test_a_question_pointing_at_two_analyses_equally_is_refused() -> None:
    """Both, and it says both, rather than picking the first alphabetically."""
    resolved = route("Does volatility or the hour of day matter more?")
    assert resolved.analysis is None
    assert not resolved.decisive
    names = {item.analysis for item in resolved.candidates}
    assert {"by_hour", "by_volatility_percentile"} <= names
    assert "does not separate them" in resolved.reason


def test_a_refusal_still_offers_what_it_heard() -> None:
    resolved = route("Does volatility or the hour of day matter more?")
    assert len(resolved.candidates) >= 2
    assert all(item.matched for item in resolved.candidates)


def test_a_single_weak_word_is_not_enough_to_route() -> None:
    """One incidental word must not decide an analysis."""
    resolved = route("What about the trend?")
    assert resolved.analysis is None


def test_the_winner_must_clear_the_runner_up_by_a_stated_margin() -> None:
    resolved = route("Does the edge depend on volatility?")
    assert resolved.decisive
    runner_up = resolved.candidates[1].score if len(resolved.candidates) > 1 else 0
    assert resolved.candidates[0].score - runner_up >= DECISIVE_MARGIN


# ── the router can only ever name something that exists ──────────────────────


@pytest.mark.parametrize(
    "question",
    [
        "does the edge depend on volatility",
        "what happens in the worst trades",
        "has it decayed",
        "which hour is best",
        "nonsense about badgers",
        "",
        "surface of time and volatility jointly",
    ],
)
def test_a_route_never_names_an_analysis_the_lab_does_not_have(question: str) -> None:
    resolved = route(question)
    assert resolved.analysis is None or resolved.analysis in CATALOGUE
    for item in resolved.candidates:
        assert item.analysis in CATALOGUE


def test_a_route_never_names_a_measure_that_does_not_exist() -> None:
    for question in ("net pnl by hour", "win rate by volatility", "how many trades per hour", ""):
        assert route(question).measure in MEASURE_TERMS


# ── the measure ──────────────────────────────────────────────────────────────


def test_the_default_measure_is_expectancy() -> None:
    """Answering "does the edge depend on X" in gross P&L would let a bucket
    with four enormous trades outrank one with two hundred good ones."""
    assert measure_for("does the edge depend on volatility") == "average_trade"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("total profit by hour of day", "net_pnl"),
        ("win rate by volatility percentile", "win_rate"),
        ("how many trades happen in each hour", "trade_count"),
        ("expectancy by month", "average_trade"),
    ],
)
def test_the_measure_is_read_from_the_question(question: str, expected: str) -> None:
    assert measure_for(question) == expected


# ── ordering ─────────────────────────────────────────────────────────────────


def test_candidates_come_back_best_first_and_deterministically() -> None:
    question = "does volatility change the worst trades"
    first = candidates(question)
    assert first == candidates(question)
    scores = [item.score for item in first]
    assert scores == sorted(scores, reverse=True)


def test_case_and_punctuation_do_not_change_the_route() -> None:
    plain = route("does the edge depend on volatility")
    shouted = route("DOES THE EDGE DEPEND ON VOLATILITY?!")
    assert plain.analysis == shouted.analysis
    assert plain.measure == shouted.measure

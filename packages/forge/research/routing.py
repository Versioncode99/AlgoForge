"""Turning a research question into one of the analyses that can answer it.

The lab has six analyses and a dropdown. A researcher does not think in
dropdowns — they think *"does this edge disappear when volatility spikes?"* — so
something has to get from the sentence to the verb.

**Why this is a matcher and not a prompt.** The obvious implementation is to
hand the sentence to a model and let it choose. That has two problems, and the
second is the serious one. A model call cannot be tested deterministically, so
the routing would be the one part of the lab with no regression coverage. And a
model asked "which analysis answers this?" will answer *something* for a
question none of them answer — "does this work on ES?" is not a question about
this ledger, and a confident wrong route is worse than a refusal, because the
result that comes back looks like an answer to what was asked.

So the route is scored against declared vocabulary. Everything it can return is
an analysis that exists; every choice comes with the words that produced it; and
when nothing scores clearly it says so and offers the candidates rather than
picking the first one.

**What it deliberately does not do.** It does not invent an analysis, widen the
catalogue, or fall back to a default. `route` returning `None` is a real
outcome — the honest answer to a question the lab cannot answer is that it
cannot answer it.

A model can still sit on top of this: `candidates` returns the ranked set with
its reasons, and anything choosing among them is choosing between analyses that
exist. That is a different thing from letting one be conjured.
"""

from __future__ import annotations

import re
from typing import Any

from forge.contracts.models import FrozenModel

#: The vocabulary each analysis answers to, and how strongly. A term is worth
#: `strong` if hearing it alone would be enough to pick this analysis, and
#: `weak` if it only supports a choice something else has already suggested.
#:
#: These are terms a researcher would actually type. Adding a word here is
#: cheap; the cost is that a word shared between two analyses stops separating
#: them, which is why the overlap is kept deliberate and small.
VOCABULARY: dict[str, dict[str, tuple[str, ...]]] = {
    "by_hour": {
        "strong": (
            "hour",
            "hourly",
            "time of day",
            "session",
            "morning",
            "afternoon",
            "london open",
            "new york open",
            "overnight",
            "intraday timing",
            "what time",
            "which hours",
            "open",
            "close",
        ),
        # Not "when": it is an interrogative, not a signal about time of day.
        # "Is the edge worse when the market is choppy" is a volatility
        # question, and a weak match here was enough to block it.
        "weak": ("timing", "clock", "day"),
    },
    "by_volatility_percentile": {
        "strong": (
            "volatility",
            "volatile",
            "atr",
            "vol regime",
            "quiet market",
            "choppy",
            "calm",
            "percentile",
            "vix",
        ),
        "weak": ("regime", "conditions", "range"),
    },
    "hour_by_volatility": {
        "strong": (
            "surface",
            "3d",
            "three dimensional",
            "both",
            "interaction",
            "time and volatility",
            "hour and volatility",
            "combination",
            "where does the edge live",
            "jointly",
        ),
        "weak": ("map", "landscape", "heatmap", "together", "across"),
    },
    "edge_over_time": {
        "strong": (
            "decay",
            "decayed",
            "still work",
            "stop working",
            "stopped working",
            "over time",
            "by month",
            "monthly",
            "since",
            "after 20",
            "before 20",
            "recent",
            "degraded",
            "deteriorat",
            "getting worse",
            "held up",
        ),
        "weak": ("year", "years", "history", "period", "trend"),
    },
    "excursion": {
        "strong": (
            "mfe",
            "mae",
            "excursion",
            "giving back",
            "give back",
            "stopped out",
            "stop too tight",
            "target too far",
            "run up",
            "drawdown per trade",
            "unrealised",
            "unrealized",
        ),
        # Not bare "stop": "did it stop working" is a decay question, and the
        # stop-loss senses are already covered by phrases above.
        "weak": ("target", "exit", "hold"),
    },
    "worst_decile": {
        "strong": (
            "worst",
            "biggest losers",
            "largest losses",
            "bad trades",
            "losing trades",
            "what went wrong",
            "worst 10",
            "worst decile",
            "responsible for the losses",
            "blow up",
            "blew up",
        ),
        "weak": ("loss", "losses", "losers", "damage", "hurt"),
    },
}

#: What each measure is called in a sentence. The default stays
#: `average_trade`, because "does the edge depend on X" is a question about
#: expectancy, and answering it in gross P&L would let a bucket with four
#: enormous trades outrank one with two hundred good ones.
MEASURE_TERMS: dict[str, tuple[str, ...]] = {
    "average_trade": ("expectancy", "average trade", "per trade", "edge", "average"),
    "net_pnl": ("net pnl", "net p&l", "total profit", "total pnl", "how much money", "gross"),
    "win_rate": ("win rate", "hit rate", "strike rate", "percentage of winners", "how often"),
    "trade_count": ("how many trades", "trade count", "number of trades", "frequency"),
}

STRONG_WEIGHT = 3
WEAK_WEIGHT = 1

#: How far ahead the winner has to be before the route is taken without asking.
#: More than a single weak term: one incidental word must not be able to block a
#: clear strong match, but two analyses that each caught something real have not
#: been separated by the question and guessing between them would be a coin toss
#: dressed as a decision.
DECISIVE_MARGIN = WEAK_WEIGHT + 1

#: Below this the question said nothing the catalogue recognises.
MINIMUM_SCORE = WEAK_WEIGHT + 1


class Candidate(FrozenModel):
    """One analysis that might answer the question, and why it might."""

    analysis: str
    score: int
    #: The terms from the question that selected it, in the order they matched.
    #: This is the whole reason a matcher is preferable to a prompt: the route
    #: can be disagreed with, because it says what it heard.
    matched: tuple[str, ...]


class Route(FrozenModel):
    """A question, resolved — or explicitly not."""

    question: str
    #: `None` when the question did not pick anything out clearly. That is a
    #: real answer, not a failure to produce one.
    analysis: str | None
    measure: str
    candidates: tuple[Candidate, ...]
    #: Why this route, or why there isn't one. Written for a person.
    reason: str
    #: True only when one analysis was clearly ahead. A caller may still run an
    #: ambiguous route's top candidate, but it has to decide to.
    decisive: bool


def _normalise(question: str) -> str:
    return re.sub(r"[^a-z0-9&% ]+", " ", question.lower())


def _matches(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    """Terms present in the question.

    Multi-word terms match as substrings. Single words match at a word boundary
    but are open-ended, so a term is a **prefix**: "deteriorat" catches
    deteriorated and deteriorating, "loss" catches losses, "volatil" would catch
    both volatile and volatility. That is why the vocabulary can stay short.

    The cost is that a prefix also catches words nobody meant — "open" fires on
    "opening" — so terms are chosen to be ones whose extensions still indicate
    the same analysis. A term that would need an exact match is written as a
    phrase instead.
    """
    found: list[str] = []
    for term in terms:
        if " " in term:
            if term in text:
                found.append(term)
        elif re.search(rf"\b{re.escape(term)}", text):
            found.append(term)
    return tuple(found)


def candidates(question: str) -> tuple[Candidate, ...]:
    """Every analysis the question points at, best first."""
    text = _normalise(question)
    scored: list[Candidate] = []
    for analysis, vocabulary in VOCABULARY.items():
        strong = _matches(text, vocabulary["strong"])
        weak = _matches(text, vocabulary["weak"])
        score = len(strong) * STRONG_WEIGHT + len(weak) * WEAK_WEIGHT
        if score:
            scored.append(Candidate(analysis=analysis, score=score, matched=(*strong, *weak)))
    scored.sort(key=lambda item: (-item.score, item.analysis))
    return tuple(scored)


def measure_for(question: str, default: str = "average_trade") -> str:
    """Which quantity the question asks about, defaulting to expectancy."""
    text = _normalise(question)
    best = default
    best_length = 0
    for measure, terms in MEASURE_TERMS.items():
        for term in _matches(text, terms):
            # Longest match wins: "how many trades" is about the count even
            # though "how" appears in "how often".
            if len(term) > best_length:
                best, best_length = measure, len(term)
    return best


def route(question: str) -> Route:
    """Resolve a question to one analysis, or say why it could not be."""
    ranked = candidates(question)
    measure = measure_for(question)

    if not ranked or ranked[0].score < MINIMUM_SCORE:
        return Route(
            question=question,
            analysis=None,
            measure=measure,
            candidates=ranked,
            reason=(
                "Nothing in that question matches an analysis this lab can run. It "
                "answers questions about one strategy's own trades — when it trades, "
                "what conditions it trades in, whether the edge has decayed, how "
                "trades are exited, and what the worst ones have in common."
            ),
            decisive=False,
        )

    top = ranked[0]
    runner_up = ranked[1].score if len(ranked) > 1 else 0
    # A route needs a term that was strong enough to mean this analysis on its
    # own. Two incidental words adding up to the minimum score is not a question
    # about anything in particular.
    strongest = max(VOCABULARY[top.analysis]["strong"], key=len, default="")
    has_strong = any(term in VOCABULARY[top.analysis]["strong"] for term in top.matched)
    if not has_strong or top.score - runner_up < DECISIVE_MARGIN:
        return Route(
            question=question,
            analysis=None,
            measure=measure,
            candidates=ranked,
            reason=(
                (
                    "That question suggests "
                    f"{top.analysis} only in passing. Say it more directly — "
                    f"something like '{strongest}'."
                )
                if not has_strong
                else (
                    "That question points at more than one analysis and does not "
                    "separate them: "
                    + ", ".join(item.analysis for item in ranked[:3])
                    + ". Pick one rather than have it guessed."
                )
            ),
            decisive=False,
        )

    return Route(
        question=question,
        analysis=top.analysis,
        measure=measure,
        candidates=ranked,
        reason="Matched " + ", ".join(f"'{term}'" for term in top.matched) + ".",
        decisive=True,
    )


def as_dict(resolved: Route) -> dict[str, Any]:
    return resolved.model_dump(mode="json")

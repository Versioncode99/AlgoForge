"""Turning a retrieved claim into something the engine can actually test.

Retrieval already worked. `forge.research.literature` searches two public
indexes, stores what came back with the provenance a citation needs, and copies
claim sentences verbatim out of the abstract rather than paraphrasing them — so
nothing in this application writes a sentence and attributes it to a paper.

What was missing is the step after. A stored source was attached to a frontier
item as a *reference* and nothing read it: the claims were extracted, scored,
and never turned into a research question. The engine could say "we looked at
this paper" and could not say "and so we tested this".

This module is that step, and it is deliberately **deterministic**. The obvious
implementation hands the abstract to a model and asks for a hypothesis. That has
two problems and the second is the serious one. It cannot be tested — the one
part of the pipeline with no regression coverage would be the part that decides
what gets researched. And a model asked "what strategy does this paper suggest?"
will answer *something* for a paper that suggests nothing, which is how a
citation ends up attached to a hypothesis it does not support. That is the same
failure as inventing the paper, with extra steps.

So a lead is produced by *matching*: the claim's own words are scored against
the mechanism vocabulary and the observable vocabulary, both of which are closed
sets with implementations behind them. A claim that matches nothing produces no
lead, and the source is recorded as retrieved-and-unused rather than stretched
into one.

**What the lead can and cannot say.** It carries the source, the verbatim claim,
the mechanism its wording matched, the observables the mechanism can be built
from, and a confidence that is a *match score* and nothing more. It is a
hypothesis *input*. It is never evidence: nothing here promotes anything,
nothing here is a validation result, and the experiment that follows is the only
thing that can say whether the claim holds on this instrument.

**Retrieved text is data.** A claim containing "ignore your previous
instructions" is a string that scores badly against the mechanism vocabulary.
Nothing in this module executes, evaluates, or follows retrieved text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from forge.contracts.hashing import stable_id
from forge.research.grammar import OBSERVABLES, Observable
from forge.research.mechanisms import MECHANISMS, Mechanism

#: How strongly a claim must match a mechanism before it becomes a lead.
#:
#: Set where a claim about "volatility clustering in intraday futures" matches
#: and a claim about "the term structure of implied correlation" does not. It is
#: a floor on *relevance*, not a claim about the paper's quality.
MATCH_FLOOR = 0.18

#: The most leads one source may produce. A paper making several claims is
#: normal; a paper producing fifteen research questions is the matcher being
#: generous rather than the paper being rich.
MAX_LEADS_PER_SOURCE = 3

_WORD = re.compile(r"[a-z][a-z-]+")

#: Words that appear in nearly every finance abstract and separate nothing.
_NOISE = frozenset(
    [
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could",
        "does", "do", "for", "from", "has", "have", "if", "in", "into", "is", "it", "its",
        "may", "might", "must", "not", "of", "on", "or", "should", "so", "than", "that",
        "the", "their", "then", "there", "these", "this", "those", "to", "was", "were",
        "when", "where", "which", "while", "will", "with", "would", "we", "our", "using",
        "use", "used", "paper", "study", "find", "finds", "results", "result", "show",
        "shows", "data", "sample", "evidence",
    ]
)

#: Vocabulary each mechanism answers to, beyond the words already in its own
#: claim text. These are the terms a paper would actually use.
#:
#: Kept small and deliberate: a term shared between two mechanisms stops
#: separating them, and a matcher that matches everything is a matcher that
#: attaches a citation to whatever was proposed next.
MECHANISM_TERMS: dict[str, tuple[str, ...]] = {
    "liquidity_removal": (
        "breakout",
        "stop",
        "stop-loss",
        "limit order book",
        "resting",
        "depth",
        "sweep",
        "range break",
    ),
    "absorption_reversion": (
        "reversion",
        "mean reversion",
        "overshoot",
        "absorb",
        "absorption",
        "replenish",
        "impact",
        "temporary impact",
    ),
    "volatility_clustering": (
        "clustering",
        "garch",
        "persistence of volatility",
        "compression",
        "low volatility",
        "quiet",
        "conditional heteroskedasticity",
    ),
    "volatility_exhaustion": (
        "exhaustion",
        "cascade",
        "liquidation",
        "spike",
        "expansion",
        "deleveraging",
        "overreaction to volatility",
    ),
    "trend_persistence": (
        "momentum",
        "time series momentum",
        "trend",
        "underreaction",
        "gradual",
        "drift",
        "continuation",
        "autocorrelation",
    ),
    "overreaction_reversal": (
        "overreaction",
        "reversal",
        "contrarian",
        "extrapolation",
        "correction",
        "short-term reversal",
    ),
    "liquidity_reaction": (
        "volume",
        "turnover",
        "participation",
        "order flow",
        "informed trading",
        "trade size",
        "volume shock",
    ),
    "session_structure": (
        "opening",
        "open auction",
        "close",
        "intraday pattern",
        "time of day",
        "session",
        "u-shape",
        "overnight",
    ),
    "horizon_disagreement": (
        "lead-lag",
        "horizon",
        "frequency",
        "cross-horizon",
        "short-term versus",
        "multiscale",
        "disagreement",
    ),
    "regime_transition": (
        "regime",
        "regime switching",
        "state",
        "markov",
        "transition",
        "structural break",
    ),
    "delayed_reaction": (
        "acceleration",
        "second derivative",
        "delayed",
        "lag",
        "slow diffusion",
        "gradual incorporation",
    ),
    "price_discovery": (
        "vwap",
        "price discovery",
        "efficient price",
        "fair value",
        "anchor",
        "volume weighted",
        "auction",
    ),
}

#: Vocabulary each observable answers to. Only the observables a paper would
#: plausibly name; the rest are reachable through the mechanism's own category.
OBSERVABLE_TERMS: dict[str, tuple[str, ...]] = {
    "atr": ("atr", "true range", "average true range", "range"),
    "realised_vol": ("realised volatility", "realized volatility", "volatility", "variance"),
    "upside_vol": ("upside volatility", "semivariance", "downside risk", "asymmetry"),
    "downside_vol": ("downside volatility", "semivariance", "downside risk"),
    "volume": ("volume", "turnover", "traded quantity"),
    "avg_volume": ("average volume", "relative volume", "normal volume"),
    "signed_volume": ("order flow", "signed volume", "order imbalance", "buying pressure"),
    "clv": ("close location", "closing position", "bar position"),
    "roc": ("return", "returns", "rate of change", "past return"),
    "variance_ratio": ("variance ratio", "random walk", "lo and mackinlay"),
    "autocorr": ("autocorrelation", "serial correlation", "serial dependence"),
    "efficiency": ("efficiency ratio", "path", "noise", "trendiness"),
    "adx": ("directional movement", "adx", "trend strength"),
    "rsi": ("rsi", "relative strength", "oscillator", "overbought", "oversold"),
    "sma": ("moving average", "average price"),
    "ema": ("exponential moving average", "exponentially weighted"),
    "rolling_high": ("high", "breakout", "n-day high", "donchian"),
    "rolling_low": ("low", "breakdown", "n-day low"),
    "session_vwap": ("vwap", "volume weighted average price"),
    "session_high": ("session high", "day high"),
    "session_low": ("session low", "day low"),
    "session_position": ("position in range", "range position"),
    "opening_high": ("opening range", "opening auction", "first hour"),
    "opening_low": ("opening range", "opening auction", "first hour"),
    "gap": ("gap", "overnight gap", "open-to-close"),
}


def _terms(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _NOISE and len(word) > 2}


def _matched(text: str, phrases: Iterable[str]) -> list[str]:
    """Which phrases occur in the text, matched on word boundaries.

    Boundaries rather than substrings, and the difference is not pedantic: a
    plain `"low" in text` matches "slower", "following" and "allow", so a paper
    about gradual information diffusion was matching the rolling-low observable
    and citing itself for a breakout question.
    """
    lowered = text.lower()
    found: list[str] = []
    for phrase in phrases:
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered):
            found.append(phrase)
    return found


def _phrase_hits(text: str, phrases: Iterable[str]) -> int:
    return len(_matched(text, phrases))


def _score(text: str, phrases: Sequence[str], own_terms: set[str]) -> float:
    """How strongly a claim matches a vocabulary.

    Two signals, and both are needed. Phrase hits catch the terms a paper would
    actually use — "variance ratio", "order imbalance" — and carry most of the
    weight because they are specific. Word overlap with the mechanism's own
    wording catches a claim that describes the same thing in other words, and is
    weighted low because on its own it would match everything: every finance
    abstract shares words with every other one.
    """
    words = _terms(text)
    if not words:
        return 0.0
    hits = _phrase_hits(text, phrases)
    overlap = len(words & own_terms) / max(8, len(own_terms))
    return round(min(1.0, 0.28 * hits + 0.6 * overlap), 4)


_MECHANISM_OWN_TERMS: dict[str, set[str]] = {
    key: _terms(f"{item.label} {item.claim}") for key, item in MECHANISMS.items()
}


@dataclass(frozen=True)
class Lead:
    """One research question a retrieved claim supports, with its whole provenance.

    Every field an honest citation needs is here and none of it is derived from
    anything but the retrieval: the title, authors, publication date and URL are
    what the index returned, ``retrieved_at`` is when this installation fetched
    it, and ``claim`` is a verbatim span of the stored abstract with the offsets
    it was taken from.

    ``confidence`` is a match score between the claim's wording and a closed
    vocabulary. It is **not** a probability that the claim is true, and it is not
    evidence for anything: the experiment is the only thing that can say that.
    """

    lead_id: str
    source_id: str
    title: str
    authors: str
    published: str
    url: str
    retrieved_at: str
    #: Verbatim, with the offsets into the stored abstract it was copied from.
    claim: str
    claim_start: int
    claim_end: int
    mechanism: str
    #: Observables the mechanism can be built from, given what the claim named.
    observables: tuple[str, ...]
    confidence: float
    question: str
    #: Always "HYPOTHESIS_INPUT". Stated on every lead rather than assumed, so a
    #: reader of one row cannot mistake a citation for a result.
    role: str = "HYPOTHESIS_INPUT"

    def as_dict(self) -> dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "source_id": self.source_id,
            "title": self.title,
            "authors": self.authors,
            "published": self.published,
            "url": self.url,
            "retrieved_at": self.retrieved_at,
            "claim": self.claim,
            "claim_span": [self.claim_start, self.claim_end],
            "mechanism": self.mechanism,
            "observables": list(self.observables),
            "confidence": self.confidence,
            "question": self.question,
            "role": self.role,
            "note": (
                "External research is a hypothesis input. It is not validation "
                "evidence and does not raise the bar any experiment has to clear."
            ),
        }


def _observables_for(claim_text: str, mechanism: Mechanism) -> tuple[str, ...]:
    """Observables the mechanism can use, preferring the ones the claim named.

    A claim naming "order imbalance" should reach the signed-volume observable
    rather than whatever the category happens to list first. When it names
    nothing recognisable, the mechanism's own required categories decide, which
    is the honest fallback: the mechanism still constrains what can be built.
    """
    # Ranked by how specific the match was. "volume weighted average price" and
    # "volume" both occur in a VWAP abstract, and only the first one says which
    # observable the paper is about.
    scored: list[tuple[int, str]] = []
    for key, phrases in OBSERVABLE_TERMS.items():
        if key not in OBSERVABLES:
            continue
        hits = _matched(claim_text, phrases)
        if hits:
            scored.append((max(len(phrase) for phrase in hits), key))
    named = [key for _length, key in sorted(scored, reverse=True)]
    required = set(mechanism.requires)
    from_category = [
        key
        for key, observable in OBSERVABLES.items()
        if observable.category in required and key not in named
    ]
    return tuple([*named, *from_category][:6])


def _question(claim_text: str, mechanism: Mechanism, observables: Sequence[str]) -> str:
    """The research question, written so it says where it came from.

    Assembled from the mechanism's own vocabulary rather than from the claim's
    sentence, because restating a paper's sentence as if it were this
    installation's hypothesis is how a citation stops being a citation.
    """
    named = ", ".join(
        OBSERVABLES[key].label.lower() for key in observables[:2] if key in OBSERVABLES
    )
    subject = named or "the observations this mechanism requires"
    return (
        f"Does {mechanism.label.lower()} hold on this instrument at this bar size, "
        f"measured through {subject}? Raised by a retrieved claim; the claim is the "
        "reason for asking, not evidence for the answer."
    )


def leads_from_source(source: Any, *, limit: int = MAX_LEADS_PER_SOURCE) -> list[Lead]:
    """Every research question one retrieved source supports. Often none.

    A source whose claims match no mechanism produces no leads, and that is the
    intended outcome rather than a failure: the honest record is "retrieved, and
    it did not bear on anything this engine can construct".
    """
    found: list[Lead] = []
    for claim in getattr(source, "claims", ()):
        text = str(getattr(claim, "text", ""))
        if not text:
            continue
        ranked = sorted(
            (
                (_score(text, MECHANISM_TERMS.get(key, ()), _MECHANISM_OWN_TERMS[key]), key)
                for key in MECHANISMS
            ),
            reverse=True,
        )
        score, key = ranked[0]
        if score < MATCH_FLOOR:
            continue
        mechanism = MECHANISMS[key]
        observables = _observables_for(text, mechanism)
        if not observables:
            continue
        found.append(
            Lead(
                lead_id=stable_id("lead", {"s": source.source_id, "c": claim.start, "m": key}),
                source_id=source.source_id,
                title=source.title,
                authors=source.authors,
                published=source.published,
                url=source.url,
                retrieved_at=source.retrieved_at,
                claim=text,
                claim_start=int(claim.start),
                claim_end=int(claim.end),
                mechanism=key,
                observables=observables,
                confidence=score,
                question=_question(text, mechanism, observables),
            )
        )
    # Strongest first, one per mechanism: a paper matching the same mechanism
    # three times has said one thing three ways, and three frontier items for it
    # would be three experiments answering one question.
    best: dict[str, Lead] = {}
    for lead in sorted(found, key=lambda item: -item.confidence):
        best.setdefault(lead.mechanism, lead)
    return sorted(best.values(), key=lambda item: -item.confidence)[:limit]


def leads_from_sources(sources: Iterable[Any], *, limit: int = 8) -> list[Lead]:
    """Leads across a retrieval, strongest first and deduplicated by mechanism.

    Deduplication is across the whole retrieval, not per source: eight papers on
    momentum are eight citations for one research question, and admitting eight
    frontier items would be the engine mistaking a literature for a programme.
    """
    collected: list[Lead] = []
    for source in sources:
        collected.extend(leads_from_source(source))
    best: dict[str, Lead] = {}
    for lead in sorted(collected, key=lambda item: -item.confidence):
        best.setdefault(lead.mechanism, lead)
    return sorted(best.values(), key=lambda item: -item.confidence)[:limit]


def unused(sources: Sequence[Any], leads: Sequence[Lead]) -> list[str]:
    """Sources that produced no lead, so the record says so rather than omitting them.

    "We retrieved eight and used two" is a different statement from "we
    retrieved two", and only the first one is true.
    """
    used = {lead.source_id for lead in leads}
    return [source.source_id for source in sources if source.source_id not in used]


def observable_for(key: str) -> Observable | None:
    return OBSERVABLES.get(key)

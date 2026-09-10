"""Is this actually new, or is it ``mean_reversion_2``?

The engine is allowed to create families and templates. That capability is only
worth having if something refuses the fourteenth restatement of momentum, so this
module is the gate between *proposing* and *admitting*.

It answers one question — **how far does this proposal depart from what already
exists?** — and returns the answer as one of five categories:

===============  =========================================================
PARAMETER        Same claim, different numbers.
STRUCTURAL       Same family and mechanism, materially different signal.
HYPOTHESIS       A different falsifiable claim within a known mechanism.
MECHANISM        A different economic or statistical explanation.
FAMILY           A genuinely new research family.
===============  =========================================================

**Why this is deterministic and not an embedding.** A model asked "is this
novel?" will say yes, because that is what the shape of the question rewards.
Similarity here is computed from the text and the structure: character shingles
over the statement and the mechanism, plus exact set overlap over the features a
signal reads and the data it requires. It runs with no network, no credential
and no model, gives the same answer twice, and can be tested — which is what
makes it a gate rather than an opinion. A model may still *propose*; this
decides whether the proposal counts as new.

**Why three signals and not one.** Text alone would pass
``"momentum, but with a different word for momentum"``. Feature overlap alone
would fail two genuinely different mechanisms that happen to read the same ATR.
Requiring a proposal to be distant on the combination is what stops both.

Nothing here decides whether an idea is *good*. It only ever decides whether it
is *different*, and a proposal it refuses is recorded on the frontier with the
nearest existing item named, so a person can see exactly what it collided with.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from forge.research.frontier import SearchKind

#: Length of the character shingles compared. Four is the usual compromise for
#: short technical English: long enough that common words do not dominate, short
#: enough to survive inflection and word order.
SHINGLE = 4

#: Words carrying no discriminating power in a corpus that is entirely about
#: trading hypotheses. Removed before shingling so "the edge in the market"
#: and "an edge in a market" do not look different for grammatical reasons.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "does",
        "do",
        "for",
        "from",
        "has",
        "have",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "must",
        "not",
        "of",
        "on",
        "or",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "was",
        "were",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "would",
    ]
)

#: Above this, two statements are the same claim differently worded.
DUPLICATE_STATEMENT = 0.82
#: Above this, two mechanisms are the same explanation.
DUPLICATE_MECHANISM = 0.75
#: A new family has to be further from every existing one than this on the
#: combined score. Set where `mean_reversion` vs `mean_reversion_atr` lands
#: firmly on the wrong side of it.
FAMILY_DISTANCE = 0.45
#: A structural proposal must change at least this share of the features it
#: reads, or it is a parameter change wearing a new name.
STRUCTURAL_FEATURE_CHANGE = 0.34

_WORD = re.compile(r"[a-z0-9]+")


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, drop stopwords, collapse whitespace."""
    words = [w for w in _WORD.findall(text.lower()) if w not in STOPWORDS]
    return " ".join(words)


def shingles(text: str, size: int = SHINGLE) -> frozenset[str]:
    """Character n-grams of the normalised text.

    Returns the whole string as a single shingle when it is shorter than the
    window, so two very short texts still compare meaningfully rather than both
    reducing to the empty set and scoring 1.0 against each other.
    """
    cleaned = normalise(text)
    if not cleaned:
        return frozenset()
    if len(cleaned) <= size:
        return frozenset({cleaned})
    return frozenset(cleaned[i : i + size] for i in range(len(cleaned) - size + 1))


def containment(left: Iterable[Any], right: Iterable[Any]) -> float:
    """How much of the *smaller* set the two share.

    Jaccard alone misses the most common way a restatement arrives: the same
    explanation with an extra sentence bolted on. The addition inflates the
    union and the score falls, so "mean reversion, and also measured against
    VWAP" scores as a new mechanism when it is the old one plus a clause.

    Containment asks the question that actually matters — is one of these
    essentially contained in the other? — and is what catches padding. Two empty
    sets are 0.0 for the same reason as in :func:`jaccard`.
    """
    a, b = frozenset(left), frozenset(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def overlap(left: Iterable[Any], right: Iterable[Any]) -> float:
    """The stronger of the two similarity readings.

    Jaccard catches two texts that are the same size and say the same thing;
    containment catches one that is the other with padding. A restatement only
    has to be caught by one of them to be caught.
    """
    return max(jaccard(left, right), containment(left, right))


def jaccard(left: Iterable[Any], right: Iterable[Any]) -> float:
    """Overlap of two sets. Two empty sets are 0.0, not 1.0.

    The convention matters: "neither declares any features" is an absence of
    information, and reporting it as perfect similarity would make every
    under-specified proposal a duplicate of every other.
    """
    a, b = frozenset(left), frozenset(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class Subject:
    """Whatever is being compared: an existing family, template or hypothesis.

    One shape for all three, because the comparison does not care which it is —
    it cares about the claim, the explanation, and what the signal reads.
    """

    key: str
    statement: str
    mechanism: str
    family: str = ""
    features: frozenset[str] = field(default_factory=frozenset)
    required_data: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def of(
        cls,
        key: str,
        *,
        statement: str,
        mechanism: str,
        family: str = "",
        features: Iterable[str] = (),
        required_data: Iterable[str] = (),
    ) -> Subject:
        return cls(
            key=key,
            statement=statement,
            mechanism=mechanism,
            family=family,
            features=frozenset(f.strip().lower() for f in features if f.strip()),
            required_data=frozenset(d.strip().upper() for d in required_data if d.strip()),
        )


@dataclass(frozen=True)
class Similarity:
    """How close two subjects are, component by component.

    The components are kept rather than collapsed, because "these say the same
    thing but read completely different features" and "these read the same
    features but claim opposite things" are different situations that a single
    number cannot distinguish.
    """

    key: str
    statement: float
    mechanism: float
    features: float
    data: float

    @property
    def combined(self) -> float:
        """One number for ranking, weighted towards what is being claimed.

        The statement and the mechanism carry most of the weight because they
        are what makes a proposal a different piece of research. Features and
        data requirements are corroborating: two hypotheses reading identical
        inputs are more likely to be the same idea, but not certainly so.
        """
        return round(
            0.40 * self.statement + 0.35 * self.mechanism + 0.18 * self.features + 0.07 * self.data,
            6,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "statement": round(self.statement, 4),
            "mechanism": round(self.mechanism, 4),
            "features": round(self.features, 4),
            "data": round(self.data, 4),
            "combined": self.combined,
        }


def compare(proposal: Subject, existing: Subject) -> Similarity:
    """Similarity component by component.

    Statement and mechanism use :func:`overlap`, so a claim restated with extra
    words is still recognised as the same claim. Features and data requirements
    use plain Jaccard: those are small exact sets where containment would call
    a two-feature signal identical to a five-feature one that happens to
    include both.
    """
    return Similarity(
        key=existing.key,
        statement=overlap(shingles(proposal.statement), shingles(existing.statement)),
        mechanism=overlap(shingles(proposal.mechanism), shingles(existing.mechanism)),
        features=jaccard(proposal.features, existing.features),
        data=jaccard(proposal.required_data, existing.required_data),
    )


@dataclass(frozen=True)
class NoveltyVerdict:
    """The decision, the number behind it, and what it collided with."""

    admitted: bool
    #: What the proposal actually is, which may be less than it claimed to be.
    kind: SearchKind
    novelty: float
    reason: str
    nearest: Similarity | None
    #: Every comparison that scored, strongest first. Kept for the interface:
    #: "rejected as a duplicate" is not useful without "of what".
    ranked: tuple[Similarity, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "kind": str(self.kind),
            "novelty": round(self.novelty, 4),
            "reason": self.reason,
            "nearest": self.nearest.as_dict() if self.nearest else None,
            "ranked": [s.as_dict() for s in self.ranked[:5]],
        }


def assess(
    proposal: Subject,
    corpus: Sequence[Subject],
    *,
    claimed: SearchKind,
) -> NoveltyVerdict:
    """Decide what ``proposal`` is, given everything already known.

    ``claimed`` is what the proposer thinks it has. The verdict may downgrade it
    — a proposal claiming ``FAMILY`` that sits 0.9 from an existing family is
    reported as ``PARAMETER``, refused, and the collision is named. It is never
    upgraded: this function can say "less than you claimed", never "more".
    """
    if not corpus:
        return NoveltyVerdict(
            admitted=True,
            kind=claimed,
            novelty=1.0,
            reason="nothing to compare against; this is the first proposal of its kind",
            nearest=None,
        )

    ranked = tuple(sorted((compare(proposal, item) for item in corpus), key=lambda s: -s.combined))
    nearest = ranked[0]
    novelty = round(1.0 - nearest.combined, 6)

    # Each check compares against the subject closest *on the dimension it is
    # about*, not against the combined-nearest. A proposal whose explanation
    # restates one family while its wording sits closer to another would
    # otherwise be measured against the wrong neighbour and slip through as a
    # new mechanism — which is precisely the restatement this gate exists to
    # catch.
    closest_mechanism = max(ranked, key=lambda s: s.mechanism)
    closest_statement = max(ranked, key=lambda s: s.statement)
    closest_features = max(ranked, key=lambda s: s.features)

    # A restatement is a restatement whatever it claims to be. Checked first so
    # the message names the real problem rather than a threshold further down.
    if (
        closest_statement.statement >= DUPLICATE_STATEMENT
        and closest_statement.mechanism >= DUPLICATE_MECHANISM
    ):
        return NoveltyVerdict(
            admitted=False,
            kind=SearchKind.PARAMETER,
            novelty=novelty,
            reason=(
                f"the same claim and the same mechanism as '{closest_statement.key}' "
                f"(statement {closest_statement.statement:.0%}, "
                f"mechanism {closest_statement.mechanism:.0%}). "
                "Different numbers against a claim already on the frontier are a parameter "
                "search, not a new hypothesis."
            ),
            nearest=closest_statement,
            ranked=ranked,
        )

    if claimed is SearchKind.FAMILY:
        # A family needs distance on the *combined* score and a mechanism that
        # is not a restatement. Either alone is insufficient: a distinctive
        # wording of a known explanation is not a new family.
        if (
            nearest.combined > 1.0 - FAMILY_DISTANCE
            or closest_mechanism.mechanism >= DUPLICATE_MECHANISM
        ):
            collision = (
                closest_mechanism if closest_mechanism.mechanism >= DUPLICATE_MECHANISM else nearest
            )
            return NoveltyVerdict(
                admitted=False,
                kind=(
                    SearchKind.MECHANISM
                    if closest_mechanism.mechanism < DUPLICATE_MECHANISM
                    else SearchKind.HYPOTHESIS
                ),
                novelty=novelty,
                reason=(
                    f"too close to the existing family '{collision.key}' "
                    f"({collision.combined:.0%} combined, "
                    f"mechanism {collision.mechanism:.0%}) to be a new family. A family "
                    "needs a different mechanism, not a different name for one that exists."
                ),
                nearest=collision,
                ranked=ranked,
            )
        return NoveltyVerdict(
            admitted=True,
            kind=SearchKind.FAMILY,
            novelty=novelty,
            reason=(
                f"furthest existing family is '{nearest.key}' at {nearest.combined:.0%}; "
                "the mechanism and the signal construction are both distinct"
            ),
            nearest=nearest,
            ranked=ranked,
        )

    if claimed is SearchKind.MECHANISM:
        if closest_mechanism.mechanism >= DUPLICATE_MECHANISM:
            return NoveltyVerdict(
                admitted=False,
                kind=SearchKind.HYPOTHESIS,
                novelty=novelty,
                reason=(
                    f"the mechanism restates '{closest_mechanism.key}' "
                    f"({closest_mechanism.mechanism:.0%}). This is a new hypothesis within a "
                    "known explanation, which is worth testing - but it is not a new "
                    "mechanism."
                ),
                nearest=closest_mechanism,
                ranked=ranked,
            )
        return NoveltyVerdict(
            admitted=True,
            kind=SearchKind.MECHANISM,
            novelty=novelty,
            reason=(
                f"the closest explanation is '{closest_mechanism.key}' at "
                f"{closest_mechanism.mechanism:.0%}; this proposes a different reason for "
                "the effect"
            ),
            nearest=closest_mechanism,
            ranked=ranked,
        )

    if claimed is SearchKind.STRUCTURAL:
        change = 1.0 - closest_features.features
        if change < STRUCTURAL_FEATURE_CHANGE:
            return NoveltyVerdict(
                admitted=False,
                kind=SearchKind.PARAMETER,
                novelty=novelty,
                reason=(
                    f"reads {1 - change:.0%} the same features as "
                    f"'{closest_features.key}'. Moving a threshold is a parameter change "
                    "however it is packaged."
                ),
                nearest=closest_features,
                ranked=ranked,
            )
        return NoveltyVerdict(
            admitted=True,
            kind=SearchKind.STRUCTURAL,
            novelty=novelty,
            reason=(
                f"builds the signal from materially different inputs to "
                f"'{closest_features.key}' ({change:.0%} of the feature set changed)"
            ),
            nearest=nearest,
            ranked=ranked,
        )

    # HYPOTHESIS and PARAMETER. A hypothesis only has to be a different claim;
    # the duplicate check above has already refused it if it is not.
    if claimed is SearchKind.HYPOTHESIS and closest_statement.statement >= DUPLICATE_STATEMENT:
        return NoveltyVerdict(
            admitted=False,
            kind=SearchKind.PARAMETER,
            novelty=novelty,
            reason=(
                f"the claim restates '{closest_statement.key}' "
                f"({closest_statement.statement:.0%}). Testing it again at different "
                "settings is parameter refinement."
            ),
            nearest=closest_statement,
            ranked=ranked,
        )
    return NoveltyVerdict(
        admitted=True,
        kind=claimed,
        novelty=novelty,
        reason=f"closest prior work is '{nearest.key}' at {nearest.combined:.0%}",
        nearest=nearest,
        ranked=ranked,
    )


# ── corpus construction ──────────────────────────────────────────────────────
# One helper per existing store, so callers never hand-roll the mapping and the
# three corpora stay consistent with each other.


def subjects_from_families(families: Iterable[Any]) -> list[Subject]:
    """Every registered family as a comparison subject."""
    return [
        Subject.of(
            family.key,
            statement=f"{family.label}. {family.description}",
            mechanism=family.mechanism,
            family=family.key,
            required_data=tuple(family.data_requirements),
        )
        for family in families
    ]


def subjects_from_templates(templates: Mapping[str, Any]) -> list[Subject]:
    """Every executable template, with the features its source actually reads.

    Features are extracted by name from the source rather than declared, because
    a declaration can be wrong and the source cannot. The list is the IR's
    closed feature vocabulary plus the window attributes a hand-written template
    reads directly.
    """
    return [
        Subject.of(
            key,
            statement=template.hypothesis,
            mechanism=f"{template.family}: {template.falsifiable_prediction}",
            family=template.family,
            features=features_in_source(template.source),
            required_data=(template.data_requirement,),
        )
        for key, template in templates.items()
    ]


def subjects_from_hypotheses(hypotheses: Iterable[Any]) -> list[Subject]:
    return [
        Subject.of(
            node.hypothesis_id,
            statement=node.statement,
            mechanism=node.mechanism,
            family=node.family,
            required_data=tuple(node.required_data),
        )
        for node in hypotheses
    ]


#: What a signal can read. The IR's feature vocabulary, plus the raw window
#: attributes a hand-written template uses. Kept as a literal set rather than
#: imported from `forge.strategy.ir` so this module has no strategy dependency
#: and can be tested on its own.
KNOWN_FEATURES = frozenset(
    [
        "sma",
        "ema",
        "atr",
        "adx",
        "rsi",
        "highest",
        "lowest",
        "realised_vol",
        "roc",
        "session_vwap",
        "session_vwap_sd",
        "opening_range_high",
        "opening_range_low",
        "bars_since_session_open",
        "minute_of_day",
        "closes",
        "highs",
        "lows",
        "opens",
        "volumes",
    ]
)


def features_in_source(source: str) -> frozenset[str]:
    """Which known features appear as identifiers in a template's source."""
    found = {word for word in _WORD.findall(source.lower()) if word in KNOWN_FEATURES}
    return frozenset(found)


def features_in_definition(definition: Any) -> frozenset[str]:
    """Which features a :class:`StrategyDefinition` declares.

    Reads the declared feature list rather than the rendered source: for an IR
    definition the declaration *is* the source of truth, and it is exact.
    """
    return frozenset(str(feature.kind).lower() for feature in getattr(definition, "features", ()))

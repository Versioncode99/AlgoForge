"""Why an effect would exist, stated so it can be wrong.

A mechanism is the part of a hypothesis that is not the signal. "Price beyond
the twenty-bar high is followed by continuation" is a pattern; "the break
removed the resting liquidity that had been absorbing the move, so the next
orders face a thinner book" is a claim about the world, and only the second one
can be refuted by anything other than the P&L.

The distinction is load-bearing here because the judge's mechanism gate (G9)
tests the *prediction* against a randomised-entry control. A mechanism with a
vague prediction produces candidates that gate cannot fail, which is precisely
the situation it exists to end. So every entry below carries five things, and a
mechanism missing any of them is not admitted:

* **what it claims** — the economic or statistical reason;
* **what it predicts** — a comparison between two subsets of the same sample,
  where the wrong answer abandons the claim regardless of aggregate P&L;
* **what it needs to observe** — the feature categories a signal for it must
  read, so a construction that cannot see the mechanism is refused before it
  runs rather than after it fails;
* **what a failure would mean** — the difference between "this instrument, this
  window" and "the reason was wrong", which is what makes a failure knowledge;
* **its stance** — whether the effect is claimed to persist or to revert, which
  decides whether a trailing exit or a fixed target is even coherent with it.

This is a vocabulary, not a taxonomy of everything. Adding to it is a deliberate
act with a prediction attached, exactly as adding a feature primitive is.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

#: Does the mechanism claim the move keeps going, or that it gives back?
#:
#: Not decoration: a continuation claim earns a trailing exit and a reversion
#: claim earns a target at the level it says price returns to. Pairing them the
#: other way caps exactly the outcome the hypothesis predicts, and then the
#: result measures the exit rather than the claim.
Stance = Literal["continuation", "reversion"]

#: Which end of its own distribution the mechanism's claim is about.
#:
#: "Quiet stretches cluster" and "expansion exhausts" are both volatility
#: claims, and attaching either to a signal that fires at the other end produces
#: a hypothesis whose experiment cannot bear on it: the prediction asks for a
#: comparison across a split the signal never straddles. ``either`` is for
#: mechanisms whose claim is about structure rather than magnitude.
Reading = Literal["high", "low", "either"]


@dataclass(frozen=True)
class Mechanism:
    key: str
    label: str
    stance: Stance
    #: The reason the effect would exist.
    claim: str
    #: What must be true in a subset comparison for the reason to survive.
    #: Written with `{observable}` so the prediction names what was actually
    #: measured rather than a placeholder.
    prediction: str
    #: Feature categories a construction must read to be testing *this*.
    requires: tuple[str, ...]
    #: What a failure rules out, and what it leaves open.
    on_failure: str
    #: Data classes the mechanism needs. Everything here is BARS today; the
    #: field is what a mechanism needing more would state rather than discover.
    data: tuple[str, ...] = ("BARS",)
    #: Which end of the observation's own distribution the claim is about.
    reads: Reading = "high"

    def describe(self, observable: str) -> str:
        return self.prediction.format(observable=observable)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "stance": self.stance,
            "claim": self.claim,
            "prediction": self.prediction,
            "requires": list(self.requires),
            "on_failure": self.on_failure,
            "data": list(self.data),
            "reads": self.reads,
        }


_MECHANISMS: tuple[Mechanism, ...] = (
    Mechanism(
        key="liquidity_removal",
        label="Liquidity removal",
        stance="continuation",
        claim=(
            "Resting orders concentrate at the edges of a recent range. Clearing them removes "
            "the liquidity that had been absorbing the move, so the orders arriving next face "
            "a thinner book and the move continues while the taking interest is worked."
        ),
        prediction=(
            "Continuation must be stronger on the half of the sample where {observable} "
            "showed the larger displacement. If the small-displacement half performs equally "
            "or better, nothing was removed and the reason is wrong."
        ),
        requires=("range", "price"),
        on_failure=(
            "Rules out liquidity removal as the reason for continuation at this horizon. "
            "Leaves open that the level matters for a different reason, and that the effect "
            "exists at a horizon this construction did not measure."
        ),
    ),
    Mechanism(
        key="absorption_reversion",
        label="Absorption and reversion",
        stance="reversion",
        claim=(
            "A displacement driven by impatient flow into a thin book overshoots the price "
            "the book would have cleared at. As liquidity replenishes, the overshoot is "
            "absorbed and price returns toward the reference it was displaced from."
        ),
        prediction=(
            "Reversion must be measurably stronger in the low-volatility half of the sample "
            "than in the high-volatility half, because absorption is easier in a calm book. "
            "Equal or inverted behaviour across the {observable} split disproves it."
        ),
        requires=("session", "volatility"),
        on_failure=(
            "Rules out absorption as the reason for reversion here. Leaves open that the "
            "displacement was information rather than impatience, which would predict "
            "continuation and is a different hypothesis."
        ),
    ),
    Mechanism(
        key="volatility_clustering",
        label="Volatility clustering",
        stance="continuation",
        claim=(
            "Realised volatility is serially dependent: a quiet stretch is more likely to be "
            "followed by a quiet one, and the transition out of compression is when the "
            "largest moves per unit of risk occur, because risk was priced for the quiet."
        ),
        prediction=(
            "The effect must be concentrated where {observable} sat in the compressed part of "
            "its own distribution. If the expanded part performs equally, the clustering is "
            "doing no work."
        ),
        requires=("volatility",),
        on_failure=(
            "Rules out compression as the conditioning variable at this horizon. Leaves open "
            "that volatility clusters on a horizon this construction did not measure."
        ),
        reads="low",
    ),
    Mechanism(
        key="volatility_exhaustion",
        label="Volatility exhaustion",
        stance="reversion",
        claim=(
            "An expansion in realised volatility is mostly liquidation and stop cascade rather "
            "than repricing. The flow causing it is finite, so once it is exhausted price "
            "gives back the part of the move that was mechanics rather than information."
        ),
        prediction=(
            "Give-back must be larger where {observable} expanded most. If the mild-expansion "
            "subset reverts as much, the move was not exhaustion and the reason is wrong."
        ),
        requires=("volatility",),
        on_failure=(
            "Rules out exhaustion as the reason for give-back. Leaves open that the expansion "
            "was information and the new level is the right one."
        ),
    ),
    Mechanism(
        key="trend_persistence",
        label="Trend persistence",
        stance="continuation",
        claim=(
            "Information is incorporated gradually rather than at once, because participants "
            "who must trade size cannot do so without moving the price against themselves. "
            "The resulting order flow is autocorrelated and the move extends."
        ),
        prediction=(
            "The edge must be concentrated where {observable} indicates directional structure. "
            "If entries taken where it indicates no structure perform equally well, the "
            "gradual-incorporation reason is wrong."
        ),
        requires=("trend", "momentum"),
        on_failure=(
            "Rules out gradual incorporation as the reason at this horizon. Leaves open that "
            "persistence exists over a longer horizon than this construction measured."
        ),
    ),
    Mechanism(
        key="overreaction_reversal",
        label="Overreaction reversal",
        stance="reversion",
        claim=(
            "A move without a corresponding change in the underlying information is an "
            "overreaction by participants extrapolating a short run. The extrapolation is "
            "corrected as the move fails to continue."
        ),
        prediction=(
            "Reversal must be stronger where {observable} moved furthest without a "
            "corroborating expansion. If the corroborated subset reverses as much, the "
            "overreaction reading is wrong."
        ),
        requires=("momentum", "oscillator"),
        on_failure=(
            "Rules out overreaction. Leaves open that the move was information and the "
            "reversal seen elsewhere is a different effect."
        ),
    ),
    Mechanism(
        key="liquidity_reaction",
        label="Liquidity reaction",
        stance="continuation",
        claim=(
            "Unusual participation marks where an informed or size-constrained participant is "
            "working. The volume itself is the observable, and the price effect follows the "
            "participation rather than preceding it."
        ),
        prediction=(
            "The effect must be concentrated where {observable} is unusual against its own "
            "recent level. If ordinary participation produces the same effect, volume is not "
            "the mechanism."
        ),
        requires=("volume", "microstructure"),
        on_failure=(
            "Rules out participation as the marker. Leaves open that the informed flow is "
            "present but not visible in aggregate volume at this bar size."
        ),
    ),
    Mechanism(
        key="session_structure",
        label="Session structure",
        stance="continuation",
        claim=(
            "A session's opening auction establishes a reference the day's participants trade "
            "against. Where price sits relative to that reference conditions what happens "
            "next, because it determines who is holding a loss."
        ),
        prediction=(
            "The effect must depend on {observable} within the session. If the same effect "
            "appears at every point in the session, the structure is not what is producing it."
        ),
        requires=("session",),
        on_failure=(
            "Rules out the session reference as the conditioning variable. Leaves open that "
            "the effect is time-of-day rather than structure, which is a different claim."
        ),
        reads="either",
    ),
    Mechanism(
        key="horizon_disagreement",
        label="Cross-horizon disagreement",
        stance="continuation",
        claim=(
            "Participants operating on different horizons disagree about the current price. "
            "When a short-horizon measure diverges from a long-horizon one, the shorter "
            "horizon is the one currently being traded and the divergence resolves in its "
            "direction."
        ),
        prediction=(
            "The effect must require the divergence in {observable}. If entries taken while "
            "the horizons agree perform equally well, the disagreement is doing nothing."
        ),
        requires=("momentum", "trend"),
        on_failure=(
            "Rules out disagreement as the driver. Leaves open that the shorter horizon alone "
            "carries the effect, which this construction cannot separate."
        ),
        reads="either",
    ),
    Mechanism(
        key="regime_transition",
        label="Regime transition",
        stance="continuation",
        claim=(
            "The market alternates between states with different statistical behaviour, and "
            "the transition between them is more predictable than the states are. A measure "
            "of how persistently a condition has held identifies where a transition is due."
        ),
        prediction=(
            "The effect must be concentrated where {observable} shows the condition has held "
            "persistently rather than intermittently. If intermittent conditions perform "
            "equally, no transition is being identified."
        ),
        requires=("transform", "volatility"),
        on_failure=(
            "Rules out the persistence measure as a transition marker. Leaves open that "
            "regimes exist and are marked by something this construction did not read."
        ),
        reads="either",
    ),
    Mechanism(
        key="delayed_reaction",
        label="Delayed reaction",
        stance="continuation",
        claim=(
            "A change in the rate of change reaches participants before the level does. "
            "Acceleration therefore leads the price move, and the lag is the opportunity."
        ),
        prediction=(
            "The effect must require the acceleration in {observable}, not merely its level. "
            "If a level condition alone produces the same result, nothing was delayed."
        ),
        requires=("transform", "momentum"),
        on_failure=(
            "Rules out a lead from the second derivative. Leaves open that the level effect "
            "is real, which is a claim already on record and not this one."
        ),
        reads="either",
    ),
    Mechanism(
        key="price_discovery",
        label="Price discovery at an auction reference",
        stance="reversion",
        claim=(
            "A volume-weighted reference is where the largest share of the session's business "
            "was done, so it is the price the market currently agrees on. Displacement from it "
            "is disagreement, and disagreement without new information resolves back."
        ),
        prediction=(
            "Reversion must scale with how far {observable} sits from the reference in the "
            "reference's own dispersion units. If small and large displacements revert "
            "equally, the reference is not what price is returning to."
        ),
        requires=("session",),
        on_failure=(
            "Rules out the volume-weighted reference as the attractor. Leaves open a different "
            "reference — the session open, the prior settlement — which is a separate claim."
        ),
    ),
)

MECHANISMS: dict[str, Mechanism] = {m.key: m for m in _MECHANISMS}

#: Mechanisms by stance, which is how a composer picks one that is coherent with
#: the exit it is about to attach.
BY_STANCE: dict[str, tuple[Mechanism, ...]] = {
    "continuation": tuple(m for m in _MECHANISMS if m.stance == "continuation"),
    "reversion": tuple(m for m in _MECHANISMS if m.stance == "reversion"),
}


def mechanism(key: str) -> Mechanism:
    try:
        return MECHANISMS[key]
    except KeyError:
        raise KeyError(
            f"unknown mechanism '{key}'. Known: {', '.join(sorted(MECHANISMS))}"
        ) from None


def compatible(
    categories: Iterable[str], *, stance: str | None = None, reading: str | None = None
) -> list[Mechanism]:
    """Mechanisms a construction reading ``categories`` could actually be testing.

    Two filters, and both are about falsifiability rather than tidiness. The
    category filter stops a composer attaching "volatility clustering" to a
    signal that reads no volatility feature: a hypothesis whose signal cannot
    observe its own mechanism is untouched by whatever the experiment returns.
    The ``reading`` filter stops it attaching a claim about quiet markets to a
    signal that only fires in loud ones, which is the same failure one level
    down — the prediction asks for a comparison the sample never contains.
    """
    seen = {str(c).lower() for c in categories}
    pool = BY_STANCE.get(str(stance), _MECHANISMS) if stance else _MECHANISMS
    found = [m for m in pool if seen & set(m.requires)]
    if reading in ("high", "low"):
        found = [m for m in found if m.reads in (reading, "either")]
    return found


def catalogue_rows() -> list[dict[str, Any]]:
    return [m.as_dict() for m in _MECHANISMS]

"""Composing a signal instead of choosing one.

The ten archetypes in :mod:`forge.research.synthesis` are ten *constants*. Each
one carries a fixed entry condition, so everything the composer varied over them
— direction, session gate, exit style — left the signal itself untouched. Ten
archetypes meant ten entry signatures, and a campaign that had tried all ten had
nothing left to propose that the novelty gate would admit. That is the ceiling
the previous phase measured, and this module is what removes it.

A construction here is assembled rather than selected:

    OBSERVABLE -> TRANSFORMATION -> SHAPE -> STANCE -> REGIME GATE -> SIGNAL

Each slot draws from a closed set with an implementation behind it, and the
result is a :class:`~forge.strategy.ir.StrategyDefinition` — data, validated,
hashed, exportable and provably reproducible by the generated Python. Nothing
here writes code, and nothing here can express a computation the primitive
catalogue does not already implement and test.

Three rules keep the space large without making it meaningless.

**Units must agree.** A comparison between a price and a z-score is a category
error that backtests perfectly and never fires, or fires always. So a shape
states the unit each of its slots needs, and a filler that cannot supply it is
not offered. This is also what forces a scale-dependent observable through a
transformation before it can be thresholded: "ATR above 3" means nothing across
instruments, "ATR in the top decile of its own last two hundred" means the same
thing everywhere.

**The signal must be able to see its own mechanism.** A construction reading no
volatility feature cannot be testing volatility clustering, whatever its
hypothesis says, because no result it produces would bear on the claim. The
mechanism is therefore chosen from those the construction's own feature
categories can speak to.

**The space is sampled, never enumerated.** The reachable set is large enough
that enumerating it would be a compute bill rather than a research programme.
:func:`draw` samples under constraints — what the campaign is for, what has
already been tried, what the data supports — and :func:`signature` is what the
novelty gate compares, so two draws that land on the same construction are
recognised as the same construction however differently they are worded.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal

from forge.contracts.hashing import content_hash
from forge.research.mechanisms import Mechanism, compatible, mechanism
from forge.strategy.ir import (
    Arithmetic,
    Combine,
    Compare,
    Constant,
    Cross,
    Feature,
    FeatureRef,
    ParamRef,
)
from forge.strategy.models import ParameterSpec
from forge.strategy.primitives import SERIES_KINDS, primitive


class GrammarError(ValueError):
    """A construction could not be assembled. The message says which slot failed."""


#: How much of a structural signature is carried in a generated template's key.
#: Long enough that a collision is not a practical concern, short enough that
#: the key stays inside the store's length limit.
SIGNATURE_PREFIX = 10


# ── units ────────────────────────────────────────────────────────────────────
# A unit is what makes two quantities comparable. The scale-free ones can be
# compared against a number a person can reason about; the rest can only be
# compared against something in the same unit.

#: Units whose values mean the same thing on any instrument, so a scalar
#: threshold against them is meaningful rather than fitted to one chart.
SCALE_FREE: frozenset[str] = frozenset({"zscore", "percentile", "ratio", "index", "rate"})

#: Where a scale-free unit sits when it is saying nothing.
#:
#: This is what makes a directional threshold mirrorable. A long condition is
#: "above neutral by d" and the short is "below neutral by d"; mirroring a bare
#: comparison instead — flipping `> 1.0` to `< 1.0` — produces a short leg that
#: is true almost always, which is a strategy that is short by default and a
#: backtest that measures nothing.
NEUTRAL: dict[str, float] = {
    "zscore": 0.0,
    "percentile": 0.5,
    "index": 50.0,
    "rate": 0.0,
}

#: How far from neutral a threshold may be asked to sit: (default, low, high,
#: step). Search bounds, not a claim about where an edge is.
DEVIATION: dict[str, tuple[float, float, float, float]] = {
    "zscore": (1.0, 0.25, 3.0, 0.25),
    "percentile": (0.3, 0.05, 0.45, 0.05),
    "index": (15.0, 5.0, 40.0, 5.0),
    # A rate is compared against zero and nothing else: "the slope turned
    # positive" is scale free without a number, and any other number would be
    # one somebody fitted to one instrument.
    "rate": (0.0, 0.0, 0.0, 0.0),
}

#: Levels a *gate* is compared against. A gate is not mirrored — "while
#: volatility is compressed" has no short version — so it takes a plain level
#: rather than a deviation from neutral.
#:
#: Only units whose range is known appear here, and that is the point. A level
#: is a number, and a number is only meaningful against a quantity whose scale
#: is known: a z-score lives in roughly +/-3, a percentile in 0 to 1, an index
#: in 0 to 100. ``ratio`` is deliberately absent — "ratio" covers quantities as
#: different as a close location value in [-1, 1] and a variance ratio around 1,
#: and one level range across both produces a gate that fires always or never.
#: A ratio observable declares its own range instead.
GATE_LEVEL: dict[str, tuple[float, float, float, float]] = {
    "zscore": (1.0, -2.5, 2.5, 0.25),
    "percentile": (0.8, 0.05, 0.95, 0.05),
    "index": (60.0, 10.0, 90.0, 5.0),
    "rate": (0.0, 0.0, 0.0, 0.0),
}

#: The level range used when a *relative* gate compares one measure against a
#: multiple of another. This one genuinely is a multiplicative ratio, whatever
#: the two measures are, so a single range is right.
RELATIVE_LEVEL: tuple[float, float, float, float] = (1.5, 0.2, 4.0, 0.1)


# ── observables ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Observable:
    """One thing the grammar can observe, and how to declare it in the IR."""

    key: str
    label: str
    kind: str
    #: (default, low, high, step) for the length parameter, or None for an
    #: observation that takes no argument.
    length: tuple[int, int, int, int] | None
    unit: str
    category: str
    #: True when the quantity is centred on zero, so its *sign* carries meaning
    #: and `persistence` over it says something.
    signed: bool = False
    #: True when the quantity's own sign says which way *price* went or is
    #: being pushed. This is narrower than `signed`: the slope of an ATR has a
    #: sign, and it says volatility is rising, not that price is.
    directional: bool = False
    #: (default, low, high, step) for a gate level on this observable, when its
    #: unit does not imply one. Required for a `ratio` observable to gate at
    #: all: without a declared range there is no number to compare it against
    #: that would mean the same thing for the next one.
    level_range: tuple[float, float, float, float] | None = None

    @property
    def arity(self) -> int:
        return primitive(self.kind).arity


def _o(
    key: str,
    kind: str,
    unit: str,
    category: str,
    length: tuple[int, int, int, int] | None = None,
    *,
    signed: bool = False,
    directional: bool = False,
    level_range: tuple[float, float, float, float] | None = None,
    label: str = "",
) -> Observable:
    return Observable(
        key=key,
        label=label or primitive(kind).label,
        kind=kind,
        length=length,
        unit=unit,
        category=category,
        signed=signed,
        directional=directional or (signed and kind in _DIRECTIONAL_KINDS),
        level_range=level_range,
    )


#: Observations whose sign is a statement about price direction rather than
#: about the magnitude of something. A gap up is price going up; a rising ATR
#: is not.
_DIRECTIONAL_KINDS: frozenset[str] = frozenset(
    {"roc", "clv", "gap", "signed_volume"}
)


OBSERVABLES: dict[str, Observable] = {
    item.key: item
    for item in (
        _o("close", "close", "price", "price"),
        _o("typical", "typical_price", "price", "price"),
        _o("bar_range", "bar_range", "price_distance", "range"),
        _o("true_range", "true_range", "price_distance", "range"),
        _o("gap", "gap", "price_distance", "range", signed=True, directional=True),
        _o("atr", "atr", "price_distance", "volatility", (14, 5, 60, 1)),
        _o("realised_vol", "realised_vol", "return", "volatility", (30, 10, 180, 5)),
        _o("upside_vol", "upside_vol", "return", "volatility", (30, 10, 180, 5)),
        _o("downside_vol", "downside_vol", "return", "volatility", (30, 10, 180, 5)),
        _o("volume", "volume", "volume", "volume"),
        _o("avg_volume", "volume_sma", "volume", "volume", (30, 10, 180, 10)),
        _o(
            "signed_volume", "signed_volume", "volume", "microstructure", signed=True,
            directional=True,
        ),
        _o(
            "clv", "clv", "ratio", "microstructure", signed=True, directional=True,
            level_range=(0.3, -0.8, 0.8, 0.1),
        ),
        _o("roc", "roc", "return", "momentum", (20, 3, 180, 1), signed=True, directional=True),
        _o(
            "variance_ratio", "variance_ratio", "ratio", "momentum", (5, 2, 30, 1),
            level_range=(1.2, 0.4, 2.5, 0.1),
        ),
        _o(
            "autocorr", "return_autocorr", "ratio", "momentum", (40, 10, 180, 5), signed=True,
            level_range=(0.1, -0.5, 0.5, 0.05),
        ),
        _o(
            "efficiency", "efficiency_ratio", "ratio", "trend", (20, 5, 180, 5),
            level_range=(0.4, 0.05, 0.9, 0.05),
        ),
        _o("adx", "adx", "index", "trend", (14, 7, 40, 1)),
        _o("rsi", "rsi", "index", "oscillator", (14, 5, 40, 1)),
        _o("sma", "sma", "price", "trend", (20, 5, 200, 5)),
        _o("ema", "ema", "price", "trend", (20, 5, 90, 5)),
        _o("rolling_high", "highest", "price", "range", (20, 5, 180, 5)),
        _o("rolling_low", "lowest", "price", "range", (20, 5, 180, 5)),
        _o("session_vwap", "session_vwap", "price", "session"),
        _o("session_vwap_sd", "session_vwap_sd", "price_distance", "session"),
        _o("session_high", "session_high", "price", "session"),
        _o("session_low", "session_low", "price", "session"),
        _o("session_position", "session_range_position", "percentile", "session"),
        _o("since_open", "bars_since_session_open", "bars", "session"),
        _o("opening_high", "opening_range_high", "price", "session", (30, 5, 120, 5)),
        _o("opening_low", "opening_range_low", "price", "session", (30, 5, 120, 5)),
    )
}


# ── transformations ──────────────────────────────────────────────────────────


#: What a transformation turns its source's unit into.
#:
#: ``""`` means the unit is unchanged, which is what lets `mean` of a price
#: still be compared to a price. ``"@spread"`` means the result is a *distance*
#: in the source's unit rather than a level in it: a standard deviation of price
#: is measured in price and is nonetheless not a price you could cross, and a
#: construction that crossed one would be a category error that backtests
#: cleanly and never fires.
TRANSFORM_UNIT: dict[str, str] = {
    "mean": "",
    "ewm": "",
    "max_of": "",
    "min_of": "",
    "stdev": "@spread",
    "change": "@spread",
    "accel": "@spread",
    "zscore": "zscore",
    "percentile_rank": "percentile",
    "persistence": "percentile",
    "slope": "rate",
    "pct_change": "ratio",
}

#: What a level's unit becomes once the reading is a distance rather than a
#: level. Only price has a distinct distance unit; everything else is already
#: measured in a way where the two coincide.
_SPREAD_UNIT: dict[str, str] = {"price": "price_distance"}

#: Windows a transformation is offered at: (default, low, high, step). Longer
#: than the observation underneath it on purpose — a z-score over twenty values
#: is noise, and the point of the transformation is a *distribution*.
#:
#: The upper bound is the one that costs something. Warmup is derived from a
#: parameter's declared *maximum*, because the first candidate of a sweep must
#: not run against undefined features, and warmup is subtracted from every
#: partition a campaign can test on. A window nobody would sweep to is therefore
#: bars nobody gets to use.
TRANSFORM_WINDOW: tuple[int, int, int, int] = (100, 20, 240, 10)

#: Transformations whose result only means something over a signed source.
#: `persistence` counts the share above zero, so over an always-positive source
#: it is the constant 1.0 — a feature that never varies, which is a construction
#: that cannot condition on anything.
SIGNED_ONLY: frozenset[str] = frozenset({"persistence"})

#: Transformations that say nothing useful about a bounded index or share.
#: A z-score of a percentile rank is a rescaling of something already scaled.
NOT_OVER_SCALE_FREE: frozenset[str] = frozenset({"pct_change"})


def transforms_for(observable: Observable) -> tuple[str, ...]:
    """Which transformations may be applied to this observable, and why not the rest.

    The filter is about meaning, not taste. A transformation that would produce
    a constant, or that rescales something already scale free, adds a name to
    the catalogue and nothing to the research.
    """
    options: list[str] = []
    for kind in sorted(SERIES_KINDS):
        if kind in SIGNED_ONLY and not observable.signed:
            continue
        if observable.unit in SCALE_FREE and kind in NOT_OVER_SCALE_FREE:
            continue
        if observable.unit == "bars" and kind not in ("change", "slope"):
            continue
        options.append(kind)
    return tuple(options)


def resulting_unit(observable: Observable, transform: str) -> str:
    if not transform:
        return observable.unit
    mapped = TRANSFORM_UNIT.get(transform, "")
    if mapped == "@spread":
        return _SPREAD_UNIT.get(observable.unit, observable.unit)
    return mapped or observable.unit


#: Transformations that turn a price *level* into a statement about direction:
#: where price sits against its own recent history, or which way it is moving.
_MAKES_PRICE_DIRECTIONAL: frozenset[str] = frozenset(
    {"slope", "change", "pct_change", "zscore", "percentile_rank", "accel"}
)

#: Transformations that preserve an already-directional reading. A rolling mean
#: of a signed return still says which way; a standard deviation of it does not.
_PRESERVES_DIRECTION: frozenset[str] = frozenset(
    {"", "mean", "ewm", "zscore", "percentile_rank", "change", "max_of", "min_of", "accel", "slope"}
)


def is_directional(observable: Observable, transform: str) -> bool:
    """Does this reading say which way price went, or only how much of something there is?

    The distinction decides whether a reading may be a *trigger* or only a
    *gate*, and getting it wrong produces the most plausible-looking broken
    strategy there is. "Enter long when the ATR percentile is above 0.8, short
    when it is below" has a signal on every bar and no claim about direction at
    all: it backtests, it produces trades, and it is noise with a hypothesis
    stapled to it.
    """
    if observable.directional:
        return transform in _PRESERVES_DIRECTION
    if observable.unit == "price":
        return transform in _MAKES_PRICE_DIRECTIONAL
    return False


# ── shapes ───────────────────────────────────────────────────────────────────


ShapeKey = Literal[
    "threshold",
    "extreme_break",
    "pair_cross",
    "divergence",
    "displacement",
    "relative",
]


@dataclass(frozen=True)
class Shape:
    """One structural form a signal can take.

    Every shape here is a **trigger**: it says which way price is expected to
    go, and it has a real mirror for the short side. Readings that say how much
    of something there is rather than which way price went are not shapes at
    all — they are gates, and they live in the gate pool below, because using
    one as a trigger produces a strategy with a signal on every bar and no
    directional claim.

    ``label`` is what the interface shows and what the hypothesis is written
    around. ``needs_partner`` says whether the shape compares two observables
    rather than an observable against a number — which is what distinguishes a
    cross from a threshold structurally, not merely visually.
    """

    key: str
    label: str
    #: The sentence fragment the hypothesis is built from.
    phrasing: str
    needs_partner: bool = False
    #: Units this shape's primary slot can accept. Empty means any.
    primary_units: frozenset[str] = field(default_factory=frozenset)


SHAPES: dict[str, Shape] = {
    shape.key: shape
    for shape in (
        Shape(
            key="threshold",
            label="Directional reading beyond its neutral point",
            phrasing="{primary} moves beyond its neutral reading",
            primary_units=SCALE_FREE,
        ),
        Shape(
            key="extreme_break",
            label="Break of a rolling extreme",
            phrasing="price closes beyond the {primary} of its lookback",
            primary_units=frozenset({"price"}),
        ),
        Shape(
            key="pair_cross",
            label="Crossing of two price levels",
            phrasing="{primary} crosses {partner}",
            needs_partner=True,
            primary_units=frozenset({"price"}),
        ),
        Shape(
            key="divergence",
            label="Disagreement between two horizons",
            phrasing=(
                "{primary} over a short horizon disagrees with the same measure over a "
                "long one"
            ),
            primary_units=frozenset({"return", "price", "ratio", "volume"}),
        ),
        Shape(
            key="displacement",
            label="Displacement from a reference, in its own dispersion",
            phrasing=(
                "price sits more than its configured distance from {primary}, measured in "
                "{partner}"
            ),
            needs_partner=True,
            primary_units=frozenset({"price"}),
        ),
    )
}


# ── the choice, as data ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConstructionSpec:
    """Every free choice in one construction, as a hashable record.

    This is the thing that is compared, stored, refused and re-derived. Two
    specs that are equal describe the same signal; a spec plus a seed
    reproduces a definition exactly. That is what makes a discovery
    re-derivable rather than merely observed.
    """

    shape: str
    observable: str
    transform: str = ""
    length: int = 0
    window: int = 0
    stance: str = "continuation"
    partner: str = ""
    partner_length: int = 0
    gate_observable: str = ""
    gate_transform: str = ""
    gate_window: int = 0
    gate_side: str = "gt"
    #: Set when the gate is a *relative* reading — one measure against another
    #: of the same unit — rather than a scaled one against a level.
    gate_partner: str = ""
    mechanism: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "observable": self.observable,
            "transform": self.transform,
            "length": self.length,
            "window": self.window,
            "stance": self.stance,
            "partner": self.partner,
            "partner_length": self.partner_length,
            "gate_observable": self.gate_observable,
            "gate_transform": self.gate_transform,
            "gate_window": self.gate_window,
            "gate_side": self.gate_side,
            "gate_partner": self.gate_partner,
            "mechanism": self.mechanism,
        }

    @property
    def signature(self) -> str:
        """Structural identity, ignoring the numbers.

        The lengths are deliberately excluded: two constructions differing only
        in a lookback are the same construction at different settings, which is
        a parameter search. Including them here would let the engine
        manufacture novelty by moving a window, which is exactly the failure
        this whole phase exists to avoid.
        """
        return content_hash(
            {
                "shape": self.shape,
                "observable": self.observable,
                "transform": self.transform,
                "stance": self.stance,
                "partner": self.partner,
                "gate_observable": self.gate_observable,
                "gate_transform": self.gate_transform,
                "gate_side": self.gate_side,
                "gate_partner": self.gate_partner,
            }
        )

    @property
    def label(self) -> str:
        parts = [OBSERVABLES[self.observable].label]
        if self.transform:
            parts.insert(0, primitive(self.transform).label)
        if self.partner:
            parts.append(f"vs {OBSERVABLES[self.partner].label}")
        head = " ".join(parts)
        if self.gate_observable:
            gate = OBSERVABLES[self.gate_observable].label
            if self.gate_transform:
                gate = f"{primitive(self.gate_transform).label} {gate}"
            if self.gate_partner:
                gate += f" against {OBSERVABLES[self.gate_partner].label}"
            head += f", gated on {gate}"
        return f"{SHAPES[self.shape].label}: {head}"


@dataclass(frozen=True)
class BuiltSignal:
    """What a spec assembles into: IR parts, plus what it can honestly claim."""

    features: tuple[Feature, ...]
    long: Any
    short: Any
    parameters: tuple[ParameterSpec, ...]
    categories: frozenset[str]
    mechanism: Mechanism
    description: str
    spec: ConstructionSpec


# ── assembly ─────────────────────────────────────────────────────────────────


def _length_param(name: str, bounds: tuple[int, int, int, int], description: str) -> ParameterSpec:
    default, low, high, step = bounds
    return ParameterSpec(
        name=name, default=default, low=low, high=high, step=step, description=description
    )


def _gate_level_param(
    name: str, unit: str, description: str, observable: Observable | None = None
) -> ParameterSpec:
    bounds = GATE_LEVEL.get(unit)
    if bounds is None and observable is not None:
        bounds = observable.level_range
    if bounds is None:
        raise GrammarError(
            f"unit '{unit}' has no known range, so there is no level to compare it against "
            "that would mean the same thing on the next instrument. Scale it first."
        )
    default, low, high, step = bounds
    return ParameterSpec(
        name=name, default=default, low=low, high=high, step=step, description=description
    )


def _deviation_param(name: str, unit: str, description: str) -> ParameterSpec:
    default, low, high, step = DEVIATION[unit]
    return ParameterSpec(
        name=name, default=default, low=low, high=high, step=step, description=description
    )


def _observable_feature(
    name: str, observable: Observable, param: str | None
) -> Feature:
    args: tuple[Any, ...] = ()
    if observable.arity == 1:
        args = (ParamRef(name=param),) if param else (Constant(value=float(observable.length[0])),)  # type: ignore[index]
    return Feature(
        name=name,
        kind=observable.kind,
        args=args,
        description=observable.label,
    )


def _transform_feature(name: str, transform: str, source: str, param: str) -> Feature:
    return Feature(
        name=name,
        kind=transform,
        args=(ParamRef(name=param),),
        source=source,
        description=f"{primitive(transform).label} of {source}",
    )


def _relative_gate(
    spec: ConstructionSpec,
    features: list[Feature],
    parameters: list[ParameterSpec],
) -> Any:
    """One measure against another of the same unit: "volume unlike its usual".

    A gate rather than a trigger, and deliberately so. Unusual volume says
    something is happening; it does not say which way. Used as a trigger it
    produces a strategy that is long on every busy bar and short on every quiet
    one, which trades a great deal and claims nothing.
    """
    observable = OBSERVABLES[spec.gate_observable]
    partner = OBSERVABLES[spec.gate_partner]
    if partner.unit != observable.unit:
        raise GrammarError(
            f"'{spec.gate_observable}' is in '{observable.unit}' and '{spec.gate_partner}' is "
            f"in '{partner.unit}'. Their ratio is not a number anybody can threshold."
        )
    if spec.gate_partner == spec.gate_observable:
        raise GrammarError("a measure relative to itself is the constant one.")
    obs_param = None
    if observable.arity == 1:
        obs_param = "gate_length"
        parameters.append(
            _length_param(
                "gate_length",
                observable.length or (20, 5, 200, 5),
                f"Bars in the gating {observable.label}.",
            )
        )
    features.append(_observable_feature("gate_src", observable, obs_param))
    partner_param = None
    if partner.arity == 1:
        partner_param = "gate_partner_length"
        parameters.append(
            _length_param(
                "gate_partner_length",
                partner.length or (60, 10, 400, 10),
                f"Bars in the {partner.label} the gate is measured against.",
            )
        )
    features.append(_observable_feature("gate_partner", partner, partner_param))
    default, low, high, step = RELATIVE_LEVEL
    parameters.append(
        ParameterSpec(
            name="gate_level",
            default=default,
            low=low,
            high=high,
            step=step,
            description=f"How far {observable.label} must sit from {partner.label}.",
        )
    )
    return Compare(
        op="gt" if spec.gate_side == "gt" else "lt",
        left=FeatureRef(name="gate_src"),
        right=Arithmetic(
            op="mul", left=ParamRef(name="gate_level"), right=FeatureRef(name="gate_partner")
        ),
    )


def _gate(
    spec: ConstructionSpec,
    features: list[Feature],
    parameters: list[ParameterSpec],
) -> Any | None:
    """The regime condition, if the construction has one.

    A gate is a second, scale-free reading that restricts *when* the trigger
    counts. It is what turns "breakouts continue" into "breakouts continue while
    volatility sits in the low part of its own distribution", which is a
    different claim and a different experiment, not a different wording.
    """
    if not spec.gate_observable:
        return None
    if spec.gate_observable not in OBSERVABLES:
        raise GrammarError(f"unknown gate observable '{spec.gate_observable}'")
    if spec.gate_partner:
        return _relative_gate(spec, features, parameters)
    observable = OBSERVABLES[spec.gate_observable]
    length_param = None
    if observable.arity == 1:
        length_param = "gate_length"
        parameters.append(
            _length_param(
                "gate_length",
                observable.length or (20, 5, 200, 5),
                f"Bars in the gating {observable.label}.",
            )
        )
    features.append(_observable_feature("gate_src", observable, length_param))
    unit = resulting_unit(observable, spec.gate_transform)
    if spec.gate_transform:
        parameters.append(
            _length_param(
                "gate_window",
                TRANSFORM_WINDOW,
                f"Values of {observable.label} the gate is measured against.",
            )
        )
        features.append(_transform_feature("gate", spec.gate_transform, "gate_src", "gate_window"))
        target = "gate"
    else:
        target = "gate_src"
    if TRANSFORM_UNIT.get(spec.gate_transform) == "@spread":
        raise GrammarError(
            f"'{spec.gate_transform}' of '{spec.gate_observable}' is a distance, centred near "
            "zero and measured in the source's own scale. Comparing it to a level is a "
            "threshold fitted to one instrument; rank or standardise it instead."
        )
    if unit not in SCALE_FREE:
        raise GrammarError(
            f"gate on '{spec.gate_observable}' has unit '{unit}', which has no meaningful "
            "scalar threshold. Apply a transformation that scales it first."
        )
    parameters.append(
        _gate_level_param(
            "gate_level", unit, f"Where the gating {observable.label} must sit.", observable
        )
    )
    return Compare(
        op="gt" if spec.gate_side == "gt" else "lt",
        left=FeatureRef(name=target),
        right=ParamRef(name="gate_level"),
    )


def build(spec: ConstructionSpec) -> BuiltSignal:
    """Assemble one construction. Raises :class:`GrammarError` if the slots do not fit."""
    if spec.shape not in SHAPES:
        raise GrammarError(f"unknown shape '{spec.shape}'. Known: {', '.join(sorted(SHAPES))}")
    if spec.observable not in OBSERVABLES:
        raise GrammarError(f"unknown observable '{spec.observable}'")
    shape = SHAPES[spec.shape]
    observable = OBSERVABLES[spec.observable]

    features: list[Feature] = []
    parameters: list[ParameterSpec] = []
    categories = {observable.category}

    builder = _BUILDERS[spec.shape]
    long_trigger, short_trigger = builder(spec, observable, features, parameters, categories)

    gate = _gate(spec, features, parameters)
    if gate is not None:
        categories.add(OBSERVABLES[spec.gate_observable].category)
        if spec.gate_transform:
            categories.add("transform")
    if spec.transform:
        categories.add("transform")

    long_condition = long_trigger if gate is None else Combine(kind="all", of=(long_trigger, gate))
    short_condition = (
        short_trigger if gate is None else Combine(kind="all", of=(short_trigger, gate))
    )
    if spec.stance == "reversion":
        # A reversion claim takes the other side of the same event: the signal
        # is identical, what differs is what it is claimed to mean. That is a
        # different hypothesis about the same observation, which is exactly what
        # a research vocabulary should be able to state.
        long_condition, short_condition = short_condition, long_condition

    chosen = _choose_mechanism(spec, categories)
    return BuiltSignal(
        features=tuple(features),
        long=long_condition,
        short=short_condition,
        parameters=tuple(parameters),
        categories=frozenset(categories),
        mechanism=chosen,
        description=_describe(spec, shape, observable),
        spec=spec if spec.mechanism == chosen.key else _with_mechanism(spec, chosen.key),
    )


def _with_mechanism(spec: ConstructionSpec, key: str) -> ConstructionSpec:
    data = spec.as_dict()
    data["mechanism"] = key
    return ConstructionSpec(**data)


#: What each shape's trigger is a reading *of*, for mechanism coherence.
#: A break and a displacement fire on a large move; a cross and a divergence
#: fire on a structural change that can happen anywhere in the distribution.
SHAPE_READING: dict[str, str] = {
    "threshold": "high",
    "extreme_break": "high",
    "displacement": "high",
    "pair_cross": "either",
    "divergence": "either",
}


def _choose_mechanism(spec: ConstructionSpec, categories: set[str]) -> Mechanism:
    """A mechanism the construction can actually observe.

    A stated mechanism the signal cannot see makes the experiment unable to bear
    on the claim, so a named one that does not fit the categories is refused
    rather than kept for the sake of the label.
    """
    reading = SHAPE_READING.get(spec.shape, "either")
    if spec.gate_observable and spec.gate_side == "lt":
        # A gate that fires on the *low* side is the construction saying its
        # effect lives in the quiet part of the distribution, which is a
        # different mechanism from the same trigger gated on the loud part.
        reading = "low"
    options = compatible(categories, stance=spec.stance, reading=reading)
    if spec.mechanism:
        named = mechanism(spec.mechanism)
        if named.stance != spec.stance:
            raise GrammarError(
                f"mechanism '{named.key}' is a {named.stance} claim and this construction "
                f"takes the {spec.stance} stance. They predict opposite things."
            )
        if named not in options:
            raise GrammarError(
                f"mechanism '{named.key}' needs an observation of "
                f"{' or '.join(named.requires)} read at its {named.reads} end; this "
                f"construction reads {', '.join(sorted(categories))} at its {reading} end. "
                "A signal that cannot see its own mechanism produces a result that does not "
                "bear on the claim."
            )
        return named
    if not options:
        raise GrammarError(
            f"no {spec.stance} mechanism can be observed by a construction reading "
            f"{', '.join(sorted(categories))} at its {reading} end."
        )
    return options[0]


def _describe(spec: ConstructionSpec, shape: Shape, observable: Observable) -> str:
    primary = observable.label
    if spec.transform:
        primary = f"the {primitive(spec.transform).label.lower()} of {observable.label}"
    partner = OBSERVABLES[spec.partner].label if spec.partner else ""
    return shape.phrasing.format(
        primary=primary, partner=partner, threshold="its configured level"
    )


# ── one builder per shape ────────────────────────────────────────────────────


def _primary(
    spec: ConstructionSpec,
    observable: Observable,
    features: list[Feature],
    parameters: list[ParameterSpec],
) -> tuple[str, str]:
    """Declare the primary observable and its transformation. Returns (name, unit)."""
    length_param = None
    if observable.arity == 1:
        length_param = "obs_length"
        parameters.append(
            _length_param(
                "obs_length",
                observable.length or (20, 5, 200, 5),
                f"Bars in the {observable.label}.",
            )
        )
    features.append(_observable_feature("obs", observable, length_param))
    if not spec.transform:
        return "obs", observable.unit
    parameters.append(
        _length_param(
            "obs_window",
            TRANSFORM_WINDOW,
            f"Values of {observable.label} the transformation is measured over.",
        )
    )
    features.append(_transform_feature("signal", spec.transform, "obs", "obs_window"))
    return "signal", resulting_unit(observable, spec.transform)


def _build_threshold(
    spec: ConstructionSpec,
    observable: Observable,
    features: list[Feature],
    parameters: list[ParameterSpec],
    categories: set[str],
) -> tuple[Any, Any]:
    """A reading beyond a deviation from its own neutral point.

    Both legs are stated against the same neutral, so the short is a real
    mirror rather than the negation of the long's comparison. That distinction
    is the difference between a strategy that takes a position when the reading
    is extreme in either direction, and one that is short whenever the reading
    is not extreme up — which trades constantly and means nothing.
    """
    name, unit = _primary(spec, observable, features, parameters)
    if unit not in SCALE_FREE:
        raise GrammarError(
            f"'{spec.observable}' with transformation '{spec.transform or 'none'}' has unit "
            f"'{unit}'. A scalar threshold against it would be a number fitted to one "
            "instrument; transform it to a scale-free reading first."
        )
    if unit not in NEUTRAL:
        raise GrammarError(
            f"unit '{unit}' has no neutral point, so a threshold on it cannot be mirrored "
            "into a short. It is a gate, not a trigger."
        )
    if not is_directional(observable, spec.transform):
        raise GrammarError(
            f"'{spec.observable}' with transformation '{spec.transform or 'none'}' says how "
            "much of something there is, not which way price went. It can gate a signal; it "
            "cannot be one."
        )
    neutral = NEUTRAL[unit]
    if unit == "rate":
        return (
            Compare(op="gt", left=FeatureRef(name=name), right=Constant(value=neutral)),
            Compare(op="lt", left=FeatureRef(name=name), right=Constant(value=neutral)),
        )
    parameters.append(
        _deviation_param(
            "deviation", unit, f"How far {observable.label} must sit from its neutral reading."
        )
    )
    return (
        Compare(
            op="gt",
            left=FeatureRef(name=name),
            right=Arithmetic(
                op="add", left=Constant(value=neutral), right=ParamRef(name="deviation")
            ),
        ),
        Compare(
            op="lt",
            left=FeatureRef(name=name),
            right=Arithmetic(
                op="sub", left=Constant(value=neutral), right=ParamRef(name="deviation")
            ),
        ),
    )


def _build_extreme_break(
    spec: ConstructionSpec,
    observable: Observable,
    features: list[Feature],
    parameters: list[ParameterSpec],
    categories: set[str],
) -> tuple[Any, Any]:
    if observable.unit != "price":
        raise GrammarError(f"'{spec.observable}' is not a price level, so nothing breaks it.")
    if spec.transform:
        raise GrammarError(
            "a break is of a level, and a transformed level is no longer one. Use the "
            "threshold shape for a transformed reading."
        )
    length_param = None
    if observable.arity == 1:
        length_param = "obs_length"
        parameters.append(
            _length_param(
                "obs_length",
                observable.length or (20, 5, 200, 5),
                f"Bars in the {observable.label}.",
            )
        )
    level = _observable_feature("obs", observable, length_param)
    # Read one bar back, so the bar that breaks the level is not part of it.
    features.append(
        Feature(
            name="obs",
            kind=level.kind,
            args=level.args,
            shift=1,
            description=f"{observable.label}, as of the prior bar",
        )
    )
    features.append(Feature(name="px", kind="close"))
    categories.add("price")
    return (
        Compare(op="gt", left=FeatureRef(name="px"), right=FeatureRef(name="obs")),
        Compare(op="lt", left=FeatureRef(name="px"), right=FeatureRef(name="obs")),
    )


def _build_pair_cross(
    spec: ConstructionSpec,
    observable: Observable,
    features: list[Feature],
    parameters: list[ParameterSpec],
    categories: set[str],
) -> tuple[Any, Any]:
    partner = _require_partner(spec, observable)
    unit = resulting_unit(observable, spec.transform)
    if unit != "price" or partner.unit != "price":
        raise GrammarError(
            "a crossing is directional only between two price levels. Two readings of "
            "something else crossing says which is larger, not which way price went."
        )
    name, _ = _primary(spec, observable, features, parameters)
    partner_param = None
    if partner.arity == 1:
        partner_param = "partner_length"
        parameters.append(
            _length_param(
                "partner_length",
                partner.length or (60, 10, 400, 10),
                f"Bars in the {partner.label} being crossed.",
            )
        )
    features.append(_observable_feature("partner", partner, partner_param))
    categories.add(partner.category)
    return (
        Cross(direction="above", left=FeatureRef(name=name), right=FeatureRef(name="partner")),
        Cross(direction="below", left=FeatureRef(name=name), right=FeatureRef(name="partner")),
    )


def _build_divergence(
    spec: ConstructionSpec,
    observable: Observable,
    features: list[Feature],
    parameters: list[ParameterSpec],
    categories: set[str],
) -> tuple[Any, Any]:
    if observable.arity != 1:
        raise GrammarError(
            f"'{spec.observable}' has no horizon to disagree across — it takes no lookback."
        )
    if spec.transform:
        raise GrammarError("the disagreement is between two horizons, not two transformations.")
    if not (observable.directional or observable.unit == "price"):
        raise GrammarError(
            f"two horizons of '{spec.observable}' disagreeing says that quantity is changing, "
            "not which way price is going. It gates a signal; it is not one."
        )
    bounds = observable.length or (20, 5, 200, 5)
    parameters.append(
        _length_param("short_length", bounds, f"Short horizon of {observable.label}.")
    )
    parameters.append(
        _length_param(
            "long_length",
            # Three times, not four. The long horizon's *maximum* is what sizes
            # the warmup, and warmup is bars nobody gets to test on.
            (max(bounds[0] * 3, bounds[1] + 1), bounds[1] + 1, min(bounds[2] * 3, 400), bounds[3]),
            f"Long horizon of {observable.label}.",
        )
    )
    features.append(
        Feature(
            name="fast",
            kind=observable.kind,
            args=(ParamRef(name="short_length"),),
            description=f"{observable.label}, short horizon",
        )
    )
    features.append(
        Feature(
            name="slow",
            kind=observable.kind,
            args=(ParamRef(name="long_length"),),
            description=f"{observable.label}, long horizon",
        )
    )
    # The difference is in the observable's own unit, so it is compared against
    # zero rather than a fitted number: "the horizons disagree in sign".
    difference = Arithmetic(op="sub", left=FeatureRef(name="fast"), right=FeatureRef(name="slow"))
    return (
        Compare(op="gt", left=difference, right=Constant(value=0.0)),
        Compare(op="lt", left=difference, right=Constant(value=0.0)),
    )


def _build_displacement(
    spec: ConstructionSpec,
    observable: Observable,
    features: list[Feature],
    parameters: list[ParameterSpec],
    categories: set[str],
) -> tuple[Any, Any]:
    if observable.unit != "price" or spec.transform:
        raise GrammarError(
            f"'{spec.observable}' is not a reference price to be displaced from."
        )
    partner = _require_partner(spec, observable)
    if partner.unit != "price_distance":
        raise GrammarError(
            f"'{spec.partner}' is measured in '{partner.unit}' and cannot scale a price "
            "displacement. A dispersion is needed, not a level."
        )
    anchor_param = None
    if observable.arity == 1:
        anchor_param = "obs_length"
        parameters.append(
            _length_param(
                "obs_length",
                observable.length or (20, 5, 200, 5),
                f"Bars in the {observable.label} reference.",
            )
        )
    features.append(_observable_feature("obs", observable, anchor_param))
    scale_param = None
    if partner.arity == 1:
        scale_param = "partner_length"
        parameters.append(
            _length_param(
                "partner_length",
                partner.length or (14, 5, 60, 1),
                f"Bars in the {partner.label} the displacement is measured in.",
            )
        )
    features.append(_observable_feature("scale", partner, scale_param))
    features.append(Feature(name="px", kind="close"))
    default, low, high, step = RELATIVE_LEVEL
    parameters.append(
        ParameterSpec(
            name="level",
            default=default,
            low=low,
            high=high,
            step=step,
            description=f"Displacement from {observable.label}, in {partner.label}.",
        )
    )
    categories.update({partner.category, "price"})
    gap = Arithmetic(op="sub", left=FeatureRef(name="px"), right=FeatureRef(name="obs"))
    span = Arithmetic(op="mul", left=ParamRef(name="level"), right=FeatureRef(name="scale"))
    return (
        Compare(op="gt", left=gap, right=span),
        Compare(
            op="lt",
            left=gap,
            right=Arithmetic(op="mul", left=Constant(value=-1.0), right=span),
        ),
    )


#: Pairs whose ordering is fixed by construction, so a crossing between them is
#: either impossible or constant. A high is never below its own low, and a
#: strategy waiting for that cross is one that never trades — a real outcome,
#: but not one worth a backtest to discover.
_ORDERED_PAIRS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"rolling_high", "rolling_low"}),
        frozenset({"session_high", "session_low"}),
        frozenset({"opening_high", "opening_low"}),
        frozenset({"session_high", "close"}),
        frozenset({"session_low", "close"}),
        frozenset({"upside_vol", "downside_vol"}),
    }
)


def _require_partner(spec: ConstructionSpec, observable: Observable) -> Observable:
    if not spec.partner:
        raise GrammarError(f"shape '{spec.shape}' compares two measures and only one was given.")
    if spec.partner not in OBSERVABLES:
        raise GrammarError(f"unknown partner observable '{spec.partner}'")
    if spec.partner == spec.observable:
        raise GrammarError(
            f"'{spec.observable}' compared against itself is a tautology, not a signal."
        )
    if not spec.transform and frozenset({spec.observable, spec.partner}) in _ORDERED_PAIRS:
        raise GrammarError(
            f"'{spec.observable}' and '{spec.partner}' are ordered by construction, so the "
            "comparison between them is constant rather than a signal."
        )
    return OBSERVABLES[spec.partner]


_BUILDERS: dict[str, Any] = {
    "threshold": _build_threshold,
    "extreme_break": _build_extreme_break,
    "pair_cross": _build_pair_cross,
    "divergence": _build_divergence,
    "displacement": _build_displacement,
}


# ── sampling ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateOption:
    """One legitimate regime gate: a reading that can be compared to a level.

    A gate is what turns "breakouts continue" into "breakouts continue while
    volatility sits in the low part of its own distribution" — a different
    claim, a different experiment, and the thing that lets one trigger carry
    many distinct hypotheses without any of them being a reworded duplicate.
    """

    observable: str
    transform: str = ""
    partner: str = ""

    @property
    def label(self) -> str:
        head = OBSERVABLES[self.observable].label
        if self.transform:
            head = f"{primitive(self.transform).label} of {head}"
        if self.partner:
            head = f"{head} against {OBSERVABLES[self.partner].label}"
        return head


def _gate_candidates() -> tuple[GateOption, ...]:
    """Every reading that can legitimately gate a signal.

    Two forms. A **scaled** gate is an observable a transformation has made
    scale free, compared against a level. A **relative** gate is one measure
    against another of the same unit, which is scale free by construction —
    volume against its own average being the obvious one.

    Scale-freeness is the whole filter: a gate is compared against a number, and
    a number compared against a price is a threshold fitted to one instrument at
    one time.
    """
    proposed: list[GateOption] = []
    for key, observable in OBSERVABLES.items():
        if observable.unit in SCALE_FREE:
            proposed.append(GateOption(key))
        for transform in transforms_for(observable):
            if resulting_unit(observable, transform) in SCALE_FREE:
                proposed.append(GateOption(key, transform))
        for other, partner in OBSERVABLES.items():
            if other != key and partner.unit == observable.unit and observable.unit != "price":
                proposed.append(GateOption(key, "", other))

    # Proposed is not the same as usable. Each one is assembled against a
    # trivial trigger, and the ones the builder refuses — a distance with no
    # level to compare it against, a ratio whose range nobody declared — never
    # reach the sampler. Checking here rather than at draw time means a refused
    # gate costs one call at import, not one wasted draw per campaign.
    usable: list[GateOption] = []
    for option in proposed:
        probe = ConstructionSpec(
            shape="extreme_break",
            observable="rolling_high",
            stance="continuation",
            gate_observable=option.observable,
            gate_transform=option.transform,
            gate_partner=option.partner,
        )
        try:
            build(probe)
        except (GrammarError, KeyError):
            continue
        usable.append(option)
    return tuple(usable)


GATE_CANDIDATES: tuple[GateOption, ...] = _gate_candidates()


@cache
def candidates_for(shape: str) -> tuple[ConstructionSpec, ...]:
    """Every structurally distinct trigger this shape can carry, gate aside.

    Enumerated per shape rather than across the whole grammar on purpose: the
    full product with gates and stances is large enough that materialising it
    would be the compute bill Part 6 warns about. This is the slice a sampler
    draws from.

    Cached because the answer is a pure function of the vocabulary and building
    it assembles about a thousand constructions. A sampler calls this once per
    attempt and may make dozens of attempts per cycle, so recomputing it turned
    a draw into a tenth of a second of work that produced nothing new.
    """
    form = SHAPES[shape]
    out: list[ConstructionSpec] = []
    for key, observable in OBSERVABLES.items():
        options: Iterable[str] = ("", *transforms_for(observable))
        for transform in options:
            unit = resulting_unit(observable, transform)
            if form.primary_units and unit not in form.primary_units:
                continue
            partners: Iterable[str] = ("",)
            if form.needs_partner:
                partners = tuple(
                    other
                    for other, spec in OBSERVABLES.items()
                    if other != key
                    and (
                        spec.unit == "price_distance"
                        if shape == "displacement"
                        else spec.unit == observable.unit
                    )
                )
            for partner in partners:
                for stance in ("continuation", "reversion"):
                    spec = ConstructionSpec(
                        shape=shape,
                        observable=key,
                        transform=transform,
                        stance=stance,
                        partner=partner,
                    )
                    try:
                        build(spec)
                    except (GrammarError, KeyError):
                        continue
                    out.append(spec)
    return tuple(out)


def reachable_signatures(*, with_gates: bool = True) -> int:
    """How many structurally distinct constructions the grammar can reach.

    Counted rather than claimed. The gate multiplier is applied rather than
    enumerated for the same reason `candidates_for` is per shape: the number is
    the point, not the list.
    """
    triggers = sum(len(candidates_for(shape)) for shape in SHAPES)
    if not with_gates:
        return triggers
    # Each trigger may carry no gate, or one of the candidates on either side.
    return triggers * (1 + 2 * len(GATE_CANDIDATES))


@dataclass(frozen=True)
class DrawConstraints:
    """What a campaign will and will not spend compute on.

    This is where Part 6's "explore intelligently" lives. A sampler with no
    constraints is a combinatorial enumeration with extra steps; these are the
    facts that make a draw a research decision.
    """

    #: Structural signatures already tried. Drawn against, not merely recorded.
    #:
    #: A full signature or a :data:`SIGNATURE_PREFIX`-length prefix of one both
    #: work. The prefix form exists because a generated template's key carries
    #: the prefix and survives a restart, while an in-memory record of what a
    #: campaign has proposed does not: a campaign resumed after a restart that
    #: could not see its own prior constructions would propose them again and
    #: be refused, which is the "activity without progress" failure one level up.
    exclude_signatures: frozenset[str] = frozenset()
    #: Shapes the campaign is interested in. Empty means all of them.
    shapes: tuple[str, ...] = ()
    #: Mechanism keys the campaign is investigating. Empty means all of them.
    mechanisms: tuple[str, ...] = ()
    #: Feature categories the available data can actually supply.
    available_categories: frozenset[str] = frozenset()
    #: How often a draw should carry a regime gate, from 0 to 1. A campaign
    #: looking for conditional effects raises it; one looking for unconditional
    #: ones lowers it.
    gate_rate: float = 0.5
    #: How many draws to attempt before reporting that the space is exhausted
    #: under these constraints. Bounded so a saturated campaign says so quickly
    #: rather than spinning.
    attempts: int = 240


def draw(
    rng: random.Random,
    constraints: DrawConstraints | None = None,
) -> ConstructionSpec | None:
    """Sample one construction, or ``None`` when the constrained space is exhausted.

    Returning ``None`` is a real outcome and the caller is expected to record it:
    "this campaign has tried everything it is allowed to try" is a finding about
    the frontier, and dressing it up as another near-duplicate proposal is how a
    research loop produces activity without progress.
    """
    limits = constraints or DrawConstraints()
    shapes = limits.shapes or tuple(SHAPES)
    for _ in range(max(1, limits.attempts)):
        shape = shapes[rng.randrange(len(shapes))]
        pool = candidates_for(shape)
        if not pool:
            continue
        base = pool[rng.randrange(len(pool))]
        gate = None
        if rng.random() < limits.gate_rate and GATE_CANDIDATES:
            gate = GATE_CANDIDATES[rng.randrange(len(GATE_CANDIDATES))]
        spec = ConstructionSpec(
            shape=base.shape,
            observable=base.observable,
            transform=base.transform,
            stance=base.stance,
            partner=base.partner,
            gate_observable=gate.observable if gate else "",
            gate_transform=gate.transform if gate else "",
            gate_partner=gate.partner if gate else "",
            gate_side="gt" if not gate or rng.random() < 0.5 else "lt",
        )
        try:
            built = build(spec)
        except (GrammarError, KeyError):
            continue
        spec = built.spec
        if (
            spec.signature in limits.exclude_signatures
            or spec.signature[:SIGNATURE_PREFIX] in limits.exclude_signatures
        ):
            continue
        if limits.mechanisms and built.mechanism.key not in limits.mechanisms:
            continue
        if limits.available_categories and not built.categories <= limits.available_categories:
            continue
        return spec
    return None


def vocabulary_summary() -> dict[str, Any]:
    """What the interface shows when it is asked how large the vocabulary is.

    Measured from the grammar rather than stated in prose, so the number on the
    screen cannot drift from the number the engine can actually reach.
    """
    per_shape = {shape: len(candidates_for(shape)) for shape in SHAPES}
    return {
        "observables": len(OBSERVABLES),
        "transformations": len(SERIES_KINDS),
        "shapes": len(SHAPES),
        "gate_candidates": len(GATE_CANDIDATES),
        "triggers_per_shape": per_shape,
        "distinct_triggers": sum(per_shape.values()),
        "reachable_signatures": reachable_signatures(),
        "categories": sorted({o.category for o in OBSERVABLES.values()}),
    }


def describe(spec: ConstructionSpec) -> dict[str, Any]:
    """One construction, explained, for the interface and the research record."""
    built = build(spec)
    return {
        **spec.as_dict(),
        "signature": spec.signature,
        "label": spec.label,
        "description": built.description,
        "mechanism": built.mechanism.key,
        "mechanism_claim": built.mechanism.claim,
        "categories": sorted(built.categories),
        "features": [f.name for f in built.features],
        "feature_kinds": sorted({f.kind for f in built.features}),
        "parameters": [p.name for p in built.parameters],
    }


def signatures_of(specs: Sequence[ConstructionSpec]) -> frozenset[str]:
    return frozenset(spec.signature for spec in specs)

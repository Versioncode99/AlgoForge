"""The Strategy Intermediate Representation.

A strategy in AlgoForge has always been a Python module with `entry_signal` and
`exit_signal`. That is honest — it is what actually runs — but it is opaque: you
cannot ask a function where its stop is, you cannot draw its levels on a chart,
and you cannot translate it into Pine without reading it like prose.

So this module adds a *declarative* form. A :class:`StrategyDefinition` states
what the strategy computes, when it enters, and how it leaves, as data. It is
serialisable, content-hashed and versioned, and :func:`compile_definition` turns
it into exactly the module protocol the existing runtime already executes.

Three properties are deliberate, and each is the reason for a design choice that
would otherwise look fussy.

**The IR is not a second engine.** It compiles down to the same
`entry_signal`/`exit_signal` protocol, runs through the same `run_backtest`, and
gets the same right-bounded `Window`. There is no path by which an IR strategy
sees a bar a Python strategy could not. If the IR could look ahead, so could
everything else, and the lookahead assertion in `run_backtest` would still catch
it.

**Levels are frozen once, at the bar after the fill.** A stop recomputed from
today's ATR is not the stop the trade was taken with, and a chart drawing it
would be drawing a number that never existed. The compiled module freezes stop,
target and initial trail when the position's first bar closes, and reports them
back so the trade ledger can record what was actually in force.

**The feature set is closed.** Like the action registry and the panel kinds:
an agent may compose a strategy, not invent an indicator with unknown
semantics. Adding a feature is a code change with an implementation and a test
behind it, which is what stops the IR from ever describing something that cannot
be computed or exported.

**A feature may read another feature's history.** :mod:`forge.strategy.primitives`
splits the closed set in two: observations computed from the bars, and
transformations computed from a named source feature's own past values. That is
what turns a list of indicators into a vocabulary — "ATR" and "how unusual this
ATR is against its own last hundred" are different observations, and only the
second one can state a regime. The source is a declared feature name, so the
dependency is data the validator can check and the evaluator can order; it is
not arithmetic somebody generated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import Field

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel
from forge.strategy.models import ParameterSpec, ParamValue
from forge.strategy.primitives import (
    ARITY as FEATURE_ARITY,
)
from forge.strategy.primitives import (
    SERIES_KINDS,
    apply_transform,
    primitive,
    window_length,
)

IR_SCHEMA_VERSION = "1"

#: How deep a chain of transformations may go. A transformation of a
#: transformation is a real construction — the percentile rank of a rolling
#: slope says something neither says alone — but the chain has to terminate, and
#: a depth nobody bounded is a warmup nobody can compute.
MAX_SOURCE_DEPTH = 3

#: Extra source history retained beyond what a transformation's own length
#: requires. `Cross` reads its operands one bar back, so a transformation used
#: in a cross needs one more value than its length implies; the rest is margin.
_SERIES_MARGIN = 8


class IRError(ValueError):
    """The definition is not well formed. The message says why."""


# ── operands ─────────────────────────────────────────────────────────────────


class Constant(FrozenModel):
    kind: Literal["const"] = "const"
    value: float


class ParamRef(FrozenModel):
    """A reference to a tunable parameter, resolved at run time."""

    kind: Literal["param"] = "param"
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")


class FeatureRef(FrozenModel):
    """A named feature declared on the definition."""

    kind: Literal["feature"] = "feature"
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")


class Arithmetic(FrozenModel):
    kind: Literal["expr"] = "expr"
    op: Literal["add", "sub", "mul", "div"]
    left: Operand
    right: Operand


Operand = Annotated[
    Constant | ParamRef | FeatureRef | Arithmetic,
    Field(discriminator="kind"),
]


class Feature(FrozenModel):
    """One named computation over the window.

    `shift` is how many closed bars back the value is read from. It is bounded
    below at zero: a negative shift is the only way this language could express
    a future bar, so it is not representable rather than merely discouraged.

    `source` names another declared feature when `kind` is a transformation —
    a z-score, a percentile rank, a slope. It is empty for every observation
    computed straight from the bars, and the validator refuses either half of
    that being wrong, because a transformation with no source has nothing to
    transform and an observation with one is reading something it ignores.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: str
    args: tuple[Operand, ...] = ()
    shift: int = Field(default=0, ge=0, le=500)
    source: str = Field(default="", pattern=r"^([a-z][a-z0-9_]{0,31})?$")
    description: str = ""


# ── conditions ───────────────────────────────────────────────────────────────


class Compare(FrozenModel):
    kind: Literal["compare"] = "compare"
    op: Literal["gt", "gte", "lt", "lte"]
    left: Operand
    right: Operand


class Cross(FrozenModel):
    """`left` crossing `right`, evaluated between the last two closed bars."""

    kind: Literal["cross"] = "cross"
    direction: Literal["above", "below"]
    left: Operand
    right: Operand


class SessionWindow(FrozenModel):
    """Inside a time-of-day window, in minutes from midnight UTC.

    Windows that wrap midnight are expressed with `start > end` and handled, so
    an Asian-session rule does not need two conditions.
    """

    kind: Literal["session"] = "session"
    start_minute: int = Field(ge=0, lt=1440)
    end_minute: int = Field(ge=0, lt=1440)
    label: str = ""


class Combine(FrozenModel):
    kind: Literal["all", "any", "not"]
    of: tuple[Condition, ...] = Field(min_length=1)


class Always(FrozenModel):
    kind: Literal["always"] = "always"


Condition = Annotated[
    Compare | Cross | SessionWindow | Combine | Always,
    Field(discriminator="kind"),
]


# ── risk ─────────────────────────────────────────────────────────────────────


class Level(FrozenModel):
    """A stop or target distance from the entry price.

    `feature` names the feature supplying the unit for `kind="feature"` — an
    ATR multiple, usually. `points` is in instrument price units and `percent`
    is a fraction of the entry price.
    """

    kind: Literal["feature", "points", "percent"]
    multiple: Operand
    feature: str = ""


class ExitRules(FrozenModel):
    signal: Condition | None = None
    stop: Level | None = None
    target: Level | None = None
    #: Ratchets in the favourable direction only; never loosens.
    trailing: Level | None = None
    #: Hard time stop, in bars held.
    max_bars: Operand | None = None
    #: Flat by this minute of day (UTC). The honest way to state "no overnight".
    flat_by_minute: int | None = Field(default=None, ge=0, lt=1440)


class EntryRules(FrozenModel):
    long: Condition | None = None
    short: Condition | None = None
    #: A time-of-day window outside which no entry is taken, whatever the
    #: signals say. Separate from the signal so it survives signal edits, and so
    #: "no entries after 11:30 London" is one field a person can read.
    session: SessionWindow | None = None

    def entered_anywhere(self) -> bool:
        return self.long is not None or self.short is not None


class ExecutionAssumptions(FrozenModel):
    """What the backtest charges, stated on the definition rather than implied.

    Copied onto the `StrategySpec` when one is derived, so an exported strategy
    carries the costs it was measured under.
    """

    commission_per_side: float = Field(default=0.62, ge=0)
    slippage_ticks: float = Field(default=1.0, ge=0)
    #: Decisions are taken on a closed bar and filled at the next bar's open.
    #: A field rather than a comment, so an export cannot silently assume
    #: something better.
    fill: Literal["next_bar_open"] = "next_bar_open"


class DefinitionProvenance(FrozenModel):
    author: str = "operator"
    created_at: datetime
    #: Where this came from — a template key, a parent definition, a request.
    derived_from: str = ""
    note: str = ""


class StrategyDefinition(FrozenModel):
    """A strategy stated as data.

    This is canonical. Python, Pine and every other rendering is an *export* of
    it. Two definitions with the same `definition_hash` describe the same
    strategy whatever they are called or where they are stored.
    """

    schema_version: Literal["1"] = "1"
    name: str = Field(min_length=1, max_length=120)
    family: str = Field(pattern=r"^[a-z][a-z0-9_]{2,39}$")
    market: Literal["futures", "crypto"] = "futures"
    symbol: str = Field(min_length=1, max_length=16)
    timeframe: str = "1m"
    hypothesis: str = Field(min_length=40)
    falsifiable_prediction: str = Field(min_length=30)
    features: tuple[Feature, ...] = ()
    entry: EntryRules
    exit: ExitRules
    parameters: tuple[ParameterSpec, ...] = ()
    execution: ExecutionAssumptions = ExecutionAssumptions()
    warmup_bars: int = Field(default=0, ge=0, le=5000)
    provenance: DefinitionProvenance

    @property
    def definition_hash(self) -> str:
        """Content identity.

        Excludes provenance: who wrote it and when do not change what it does,
        and two people arriving at the same strategy should be able to see that
        they did.
        """
        payload = self.model_dump(mode="json")
        payload.pop("provenance", None)
        return content_hash(payload)

    @property
    def definition_id(self) -> str:
        return stable_id("sdef", self.definition_hash)

    @property
    def defaults(self) -> dict[str, ParamValue]:
        return {p.name: p.default for p in self.parameters}

    def feature(self, name: str) -> Feature | None:
        return next((f for f in self.features if f.name == name), None)

    def required_warmup(self) -> int:
        """Bars needed before any feature is defined.

        Derived rather than declared, because a declared warmup that is too
        small does not fail — it silently produces NaN comparisons that read as
        "no signal", and a strategy that never trades looks like a strategy with
        no edge.

        A transformation adds its own window on top of its source's, all the way
        down the chain: the percentile rank of a 100-bar ATR over 200 values
        needs both, not the larger of the two.
        """
        by_name = {item.name: item for item in self.features}
        need = 1
        for item in self.features:
            need = max(need, _feature_depth(item, by_name, self) + 2)
        return max(self.warmup_bars, need + 5)

    def source_history(self) -> dict[str, int]:
        """How many past values of each feature the evaluator has to retain.

        Only features that something else transforms appear here: an
        observation nobody reads the history of is computed and discarded. The
        number is what bounds the evaluator's memory, so it is derived from the
        definition rather than guessed at with a constant.
        """
        by_name = {item.name: item for item in self.features}
        retained: dict[str, int] = {}
        for item in self.features:
            if item.kind not in SERIES_KINDS or not item.source:
                continue
            length = 1
            for arg in item.args:
                length = max(length, int(_static_upper_bound(arg, self)))
            span = window_length(item.kind, length) + item.shift + _SERIES_MARGIN
            chain: str | None = item.source
            depth = 0
            while chain and depth <= MAX_SOURCE_DEPTH:
                retained[chain] = max(retained.get(chain, 0), span)
                parent = by_name.get(chain)
                if parent is None or parent.kind not in SERIES_KINDS:
                    break
                inner = 1
                for arg in parent.args:
                    inner = max(inner, int(_static_upper_bound(arg, self)))
                span += window_length(parent.kind, inner) + parent.shift
                chain = parent.source or None
                depth += 1
        return retained


Arithmetic.model_rebuild()
Combine.model_rebuild()
StrategyDefinition.model_rebuild()


def _feature_depth(
    item: Feature, by_name: dict[str, Feature], defn: StrategyDefinition, seen: int = 0
) -> int:
    """Bars of history one feature reads, following its source chain.

    ``seen`` bounds the recursion independently of the validator, so this stays
    total even if it is called on a definition that has not been validated yet —
    which is exactly what `required_warmup` does when a caller sizes a dataset
    before compiling.
    """
    length = 1
    for arg in item.args:
        length = max(length, int(_static_upper_bound(arg, defn)))
    if item.kind in SERIES_KINDS:
        own = window_length(item.kind, length) + item.shift
        parent = by_name.get(item.source) if item.source else None
        if parent is None or seen >= MAX_SOURCE_DEPTH:
            return own
        return own + _feature_depth(parent, by_name, defn, seen + 1)
    spec = primitive(item.kind) if item.kind in FEATURE_ARITY else None
    multiplier = spec.warmup_multiplier if spec else 1
    return length * multiplier + item.shift


def _static_upper_bound(operand: Any, defn: StrategyDefinition) -> float:
    """The largest value an operand can take across the declared parameter grid.

    Used only to size warmup. A parameter reference resolves to its declared
    `high`, so a lookback the operator can sweep up to 200 gets 200 bars of
    warmup even when the default is 20 — otherwise the first sweep candidate
    silently runs against undefined features.
    """
    if isinstance(operand, Constant):
        return abs(operand.value)
    if isinstance(operand, ParamRef):
        spec = next((p for p in defn.parameters if p.name == operand.name), None)
        return abs(float(spec.high)) if spec else 1.0
    if isinstance(operand, FeatureRef):
        return 1.0
    left = _static_upper_bound(operand.left, defn)
    right = _static_upper_bound(operand.right, defn)
    if operand.op == "add":
        return left + right
    if operand.op == "sub":
        return max(left, right)
    if operand.op == "mul":
        return left * right
    return left if right == 0 else left / max(right, 1e-9)


# ── validation ───────────────────────────────────────────────────────────────


def validate_definition(defn: StrategyDefinition) -> StrategyDefinition:
    """Reject a definition that cannot be executed, with the reason.

    Called by `compile_definition`, so nothing reaches the runtime unchecked.
    Pydantic covers shape; this covers meaning — an unknown feature kind, a
    reference to a parameter that does not exist, a stop naming a feature the
    definition never declared.
    """
    names = [f.name for f in defn.features]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise IRError(f"duplicate feature names: {', '.join(duplicates)}")

    known_params = {p.name for p in defn.parameters}
    known_features = set(names)

    by_name = {item.name: item for item in defn.features}
    for item in defn.features:
        arity = FEATURE_ARITY.get(item.kind)
        if arity is None:
            raise IRError(
                f"feature '{item.name}' uses unknown kind '{item.kind}'. "
                f"Known kinds: {', '.join(sorted(FEATURE_ARITY))}"
            )
        if len(item.args) != arity:
            raise IRError(
                f"feature '{item.name}' ({item.kind}) takes {arity} argument(s), "
                f"got {len(item.args)}"
            )
        for arg in item.args:
            _check_operand(arg, known_params, known_features, f"feature '{item.name}'")
        _check_source(item, by_name, defn)

    if not defn.entry.entered_anywhere():
        raise IRError("a strategy with no long and no short entry never takes a trade")

    for label, condition in (
        ("entry.long", defn.entry.long),
        ("entry.short", defn.entry.short),
        ("exit.signal", defn.exit.signal),
    ):
        if condition is not None:
            _check_condition(condition, known_params, known_features, label)

    for label, level in (
        ("exit.stop", defn.exit.stop),
        ("exit.target", defn.exit.target),
        ("exit.trailing", defn.exit.trailing),
    ):
        if level is None:
            continue
        _check_operand(level.multiple, known_params, known_features, label)
        if level.kind == "feature":
            if not level.feature:
                raise IRError(f"{label} is a feature multiple but names no feature")
            if level.feature not in known_features:
                raise IRError(f"{label} names feature '{level.feature}', which is not declared")
        elif level.feature:
            raise IRError(f"{label} is a {level.kind} distance and must not name a feature")

    if defn.exit.max_bars is not None:
        _check_operand(defn.exit.max_bars, known_params, known_features, "exit.max_bars")

    if (
        defn.exit.signal is None
        and defn.exit.stop is None
        and defn.exit.max_bars is None
        and defn.exit.flat_by_minute is None
    ):
        raise IRError(
            "a strategy needs at least one way out: an exit signal, a stop, a time "
            "stop, or a session flat. Without one every trade runs to the end of the "
            "data and the result measures the data's length, not the strategy."
        )
    return defn


def _check_source(item: Feature, by_name: dict[str, Feature], defn: StrategyDefinition) -> None:
    """A transformation's source must exist, terminate, and be precomputable.

    Three separate refusals, because they are three separate mistakes:

    * a transformation with no source, or an observation carrying one, is a
      declaration that contradicts its own kind;
    * a chain that revisits a name never terminates, and the evaluator that
      walks it would not either;
    * a source whose *length* depends on another feature cannot be given a
      fixed history window, so its own history cannot be retained — and a
      transformation over a source the evaluator cannot keep is a value that
      would silently be NaN for the whole run.
    """
    is_transform = item.kind in SERIES_KINDS
    if is_transform and not item.source:
        raise IRError(
            f"feature '{item.name}' ({item.kind}) transforms a source series but names none. "
            "Set `source` to another declared feature."
        )
    if not is_transform and item.source:
        raise IRError(
            f"feature '{item.name}' ({item.kind}) is computed from the bars and must not "
            f"name a source, but names '{item.source}'."
        )
    if not is_transform:
        return

    seen = [item.name]
    cursor: str | None = item.source
    depth = 0
    while cursor:
        if cursor in seen:
            raise IRError(
                "transformation chain loops back on itself: " + " -> ".join([*seen, cursor])
            )
        parent = by_name.get(cursor)
        if parent is None:
            raise IRError(
                f"feature '{item.name}' transforms '{cursor}', which is not declared. "
                f"Declared: {', '.join(sorted(by_name)) or 'none'}"
            )
        if any(_has_feature_ref(arg) for arg in parent.args):
            raise IRError(
                f"feature '{item.name}' transforms '{parent.name}', whose length is itself a "
                "feature. A transformation needs a source with a fixed window, so its history "
                "can be retained."
            )
        seen.append(cursor)
        depth += 1
        if depth > MAX_SOURCE_DEPTH:
            raise IRError(
                f"transformation chain is deeper than {MAX_SOURCE_DEPTH}: "
                + " -> ".join(seen)
                + ". A longer chain needs more warmup than it explains."
            )
        cursor = parent.source or None
    if any(_has_feature_ref(arg) for arg in item.args):
        raise IRError(
            f"feature '{item.name}' is a transformation whose length is itself a feature. "
            "Transformation windows must be a constant or a parameter."
        )
    _ = defn


def _has_feature_ref(operand: Any) -> bool:
    if isinstance(operand, FeatureRef):
        return True
    if isinstance(operand, Arithmetic):
        return _has_feature_ref(operand.left) or _has_feature_ref(operand.right)
    return False


def _check_operand(operand: Any, params: set[str], features: set[str], where: str) -> None:
    if isinstance(operand, ParamRef):
        if operand.name not in params:
            raise IRError(
                f"{where} references parameter '{operand.name}', which is not declared. "
                f"Declared: {', '.join(sorted(params)) or 'none'}"
            )
    elif isinstance(operand, FeatureRef):
        if operand.name not in features:
            raise IRError(
                f"{where} references feature '{operand.name}', which is not declared. "
                f"Declared: {', '.join(sorted(features)) or 'none'}"
            )
    elif isinstance(operand, Arithmetic):
        _check_operand(operand.left, params, features, where)
        _check_operand(operand.right, params, features, where)


def _check_condition(condition: Any, params: set[str], features: set[str], where: str) -> None:
    if isinstance(condition, Compare | Cross):
        _check_operand(condition.left, params, features, where)
        _check_operand(condition.right, params, features, where)
    elif isinstance(condition, Combine):
        for child in condition.of:
            _check_condition(child, params, features, where)


# ── evaluation ───────────────────────────────────────────────────────────────


class _Frame:
    """The arrays a definition is evaluated against, as of one bar.

    Constructed from the `Window` the runtime hands the strategy, so it inherits
    the window's right bound: the last element is the last closed bar and
    nothing past it exists in the arrays.

    ``end`` moves that bound *earlier*, never later. A frame at ``end`` is the
    window as it stood when bar ``end`` closed, which is what makes a feature's
    value at a past bar a fixed number rather than something that changes as
    the run advances. That property is what lets the evaluator compute a value
    once and keep it — and a value that could change later would be a value
    that had seen later bars.

    The bound is carried as ``stop`` rather than by slicing. Slicing five arrays
    to build a frame is cheap but not free, and a frame is built for every
    distinct offset a condition reads; `stop` gives the same guarantee — nothing
    reads past it — for the cost of an integer.
    """

    __slots__ = ("_session", "c", "h", "index", "l", "o", "offset", "stop", "v", "window")

    def __init__(self, window: Any, end: int | None = None) -> None:
        self.window = window
        index = window.index if end is None else min(int(end), window.index)
        self.o = window.opens
        self.h = window.highs
        self.l = window.lows
        self.c = window.closes
        self.v = window.volumes
        self.index = index
        self.stop = index + 1
        #: How far this frame sits behind the window's own last bar, so
        #: timestamp lookups resolve against this bar rather than that one.
        self.offset = window.index - index
        self._session = -1

    @property
    def session_start(self) -> int:
        """First bar of this bar's session. Resolved once, and only if asked.

        Most features never touch the session, and the lookup is a search over
        the timestamps — paying for it on every frame made session-blind
        strategies slower for nothing.
        """
        if self._session < 0:
            self._session = int(self.window.session_start_index(self.index))
        return self._session

    def time_at(self, back: int) -> Any:
        return self.window.time_at(self.offset + back)


def _tail(array: np.ndarray, bound: int, shift: int, length: int) -> np.ndarray:
    """The `length` values ending `shift` bars before bar ``bound - 1``.

    ``bound`` is the frame's right edge rather than the array's, so a frame at
    an earlier bar reads an earlier slice of the same array without anybody
    having to copy it.
    """
    stop = bound - shift
    start = stop - length
    if start < 0 or stop <= 0:
        return np.empty(0, dtype=np.float64)
    return array[start:stop]


def _feature_value(frame: _Frame, item: Feature, args: list[float], shift: int) -> float:
    kind = item.kind
    total = shift + item.shift
    nan = float("nan")

    if kind in ("close", "open", "high", "low", "volume"):
        source = {
            "close": frame.c,
            "open": frame.o,
            "high": frame.h,
            "low": frame.l,
            "volume": frame.v,
        }[kind]
        idx = frame.stop - 1 - total
        return float(source[idx]) if idx >= 0 else nan

    if kind == "typical_price":
        idx = frame.stop - 1 - total
        if idx < 0:
            return nan
        return float((frame.h[idx] + frame.l[idx] + frame.c[idx]) / 3.0)

    if kind == "bar_range":
        idx = frame.stop - 1 - total
        return float(frame.h[idx] - frame.l[idx]) if idx >= 0 else nan

    if kind == "true_range":
        idx = frame.stop - 1 - total
        if idx < 1:
            return nan
        prior_close = float(frame.c[idx - 1])
        bar_high, bar_low = float(frame.h[idx]), float(frame.l[idx])
        return float(
            max(
                bar_high - bar_low,
                abs(bar_high - prior_close),
                abs(bar_low - prior_close),
            )
        )

    if kind == "gap":
        idx = frame.stop - 1 - total
        if idx < 1:
            return nan
        return float(frame.o[idx] - frame.c[idx - 1])

    if kind in ("clv", "signed_volume"):
        idx = frame.stop - 1 - total
        if idx < 0:
            return nan
        bar_high = float(frame.h[idx])
        bar_low = float(frame.l[idx])
        bar_close = float(frame.c[idx])
        span = bar_high - bar_low
        if span <= 0:
            return nan
        location = ((bar_close - bar_low) - (bar_high - bar_close)) / span
        return location if kind == "clv" else location * float(frame.v[idx])

    if kind in ("session_high", "session_low", "session_range_position"):
        stop = frame.stop - total
        start = frame.session_start
        if stop <= start:
            return nan
        session_high = float(frame.h[start:stop].max())
        session_low = float(frame.l[start:stop].min())
        if kind == "session_high":
            return session_high
        if kind == "session_low":
            return session_low
        if session_high <= session_low:
            return nan
        return float((frame.c[stop - 1] - session_low) / (session_high - session_low))

    if kind == "minute_of_day":
        stamp = frame.time_at(total)
        return nan if stamp is None else float(stamp.hour * 60 + stamp.minute)

    if kind == "bars_since_session_open":
        return float(max(0, frame.index - total - frame.session_start))

    if kind in ("session_vwap", "session_vwap_sd"):
        stop = frame.stop - total
        start = frame.session_start
        if stop <= start:
            return nan
        high, low, close = frame.h[start:stop], frame.l[start:stop], frame.c[start:stop]
        volume = frame.v[start:stop]
        weight = float(volume.sum())
        if weight <= 0:
            return nan
        typical = (high + low + close) / 3.0
        vwap = float((volume * typical).sum() / weight)
        if kind == "session_vwap":
            return vwap
        variance = max(0.0, float((volume * typical * typical).sum() / weight) - vwap * vwap)
        return float(np.sqrt(variance))

    if kind in ("opening_range_high", "opening_range_low"):
        bars = max(1, int(args[0]))
        start = frame.session_start
        stop = min(start + bars, frame.stop - total)
        if stop <= start:
            return nan
        return (
            float(frame.h[start:stop].max())
            if kind == "opening_range_high"
            else float(frame.l[start:stop].min())
        )

    length = max(1, int(args[0])) if args else 1

    if kind == "sma":
        values = _tail(frame.c, frame.stop, total, length)
        return float(values.mean()) if values.size == length else nan

    if kind == "volume_sma":
        values = _tail(frame.v, frame.stop, total, length)
        return float(values.mean()) if values.size == length else nan

    if kind == "ema":
        span = max(2, length)
        depth = span * 5
        values = _tail(frame.c, frame.stop, total, depth)
        if values.size < depth:
            return nan
        alpha = 2.0 / (span + 1.0)
        weights = (1.0 - alpha) ** np.arange(values.size - 1, -1, -1)
        return float((values * weights).sum() / weights.sum())

    if kind == "highest":
        values = _tail(frame.h, frame.stop, total, length)
        return float(values.max()) if values.size == length else nan

    if kind == "lowest":
        values = _tail(frame.l, frame.stop, total, length)
        return float(values.min()) if values.size == length else nan

    if kind == "atr":
        highs = _tail(frame.h, frame.stop, total, length)
        lows = _tail(frame.l, frame.stop, total, length)
        prev = _tail(frame.c, frame.stop, total + 1, length)
        if highs.size != length or prev.size != length:
            return nan
        true_range = np.maximum(highs - lows, np.maximum(np.abs(highs - prev), np.abs(lows - prev)))
        return float(true_range.mean())

    if kind == "realised_vol":
        values = _tail(frame.c, frame.stop, total, length + 1)
        if values.size != length + 1 or bool((values <= 0).any()):
            return nan
        return float(np.diff(np.log(values)).std(ddof=1))

    if kind in ("upside_vol", "downside_vol"):
        values = _tail(frame.c, frame.stop, total, length + 1)
        if values.size != length + 1 or bool((values <= 0).any()):
            return nan
        returns = np.diff(np.log(values))
        side = returns[returns > 0] if kind == "upside_vol" else returns[returns < 0]
        # Two observations is the minimum a sample deviation is defined on.
        # Reporting zero for one observation would say "no volatility on this
        # side" when what happened is that there was one move.
        return float(side.std(ddof=1)) if side.size >= 2 else nan

    if kind == "roc":
        values = _tail(frame.c, frame.stop, total, length + 1)
        if values.size != length + 1 or values[0] == 0:
            return nan
        return float((values[-1] - values[0]) / values[0])

    if kind == "efficiency_ratio":
        values = _tail(frame.c, frame.stop, total, length + 1)
        if values.size != length + 1:
            return nan
        path = float(np.abs(np.diff(values)).sum())
        if path <= 0:
            return nan
        return float(abs(values[-1] - values[0]) / path)

    if kind == "variance_ratio":
        # Overlapping q-period returns against q times the one-period variance,
        # over a window long enough for both estimates to mean something. The
        # multiplier is stated in `primitives.window_length`, so the warmup
        # this needs is derived from the same number.
        depth = length * 5
        values = _tail(frame.c, frame.stop, total, depth + 1)
        if values.size != depth + 1 or bool((values <= 0).any()) or length < 2:
            return nan
        logs = np.log(values)
        single = np.diff(logs)
        multi = logs[length:] - logs[:-length]
        if single.size < 2 or multi.size < 2:
            return nan
        base = float(single.var(ddof=1))
        if base <= 0:
            return nan
        return float(multi.var(ddof=1) / (length * base))

    if kind == "return_autocorr":
        values = _tail(frame.c, frame.stop, total, length + 2)
        if values.size != length + 2 or bool((values <= 0).any()) or length < 3:
            return nan
        returns = np.diff(np.log(values))
        first, second = returns[:-1], returns[1:]
        spread = float(first.std(ddof=1)) * float(second.std(ddof=1))
        if spread <= 0:
            return nan
        covariance = float(((first - first.mean()) * (second - second.mean())).sum())
        return float(covariance / ((first.size - 1) * spread))

    if kind == "rsi":
        depth = length * 4
        values = _tail(frame.c, frame.stop, total, depth)
        if values.size < depth:
            return nan
        delta = np.diff(values)
        gain = float(np.maximum(delta, 0.0).mean())
        loss = float(np.maximum(-delta, 0.0).mean())
        if loss <= 0:
            return 100.0 if gain > 0 else 50.0
        return float(100.0 - 100.0 / (1.0 + gain / loss))

    if kind == "adx":
        span = length * 3
        high = _tail(frame.h, frame.stop, total, span)
        low = _tail(frame.l, frame.stop, total, span)
        close = _tail(frame.c, frame.stop, total, span)
        if high.size != span:
            return nan
        up, down = high[1:] - high[:-1], low[:-1] - low[1:]
        plus = np.where((up > down) & (up > 0), up, 0.0)
        minus = np.where((down > up) & (down > 0), down, 0.0)
        true_range = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )
        atr = float(true_range.mean())
        if atr <= 0:
            return nan
        di_plus = 100.0 * float(plus.mean()) / atr
        di_minus = 100.0 * float(minus.mean()) / atr
        total_di = di_plus + di_minus
        return float(100.0 * abs(di_plus - di_minus) / total_di) if total_di > 0 else nan

    raise IRError(f"unknown feature kind '{kind}'")


class _History:
    """The past values of every feature something else transforms.

    A transformation reads its source's own history, and recomputing that
    history from the bars on every bar is quadratic: a twenty-value z-score
    would evaluate its source twenty times per bar, for ever. So each source is
    computed once per bar and kept.

    Two properties make keeping it sound rather than merely fast.

    **A stored value cannot have seen a later bar.** Values are computed from a
    frame bounded at the bar they belong to, so the value at bar *j* is the same
    number whether it is computed when *j* closes or during a backfill a
    thousand bars later. If that were not true, caching would be a lookahead.

    **A different run gets a different cache.** The parameters and the bars are
    both part of the identity: a changed lookback or a different dataset resets
    the store rather than answering from values computed under the old one.
    """

    __slots__ = ("_anchor", "_key", "_order", "_retain", "_span", "_start", "filled_to", "values")

    def __init__(self, retain: dict[str, int], order: tuple[str, ...]) -> None:
        self._retain = retain
        self._order = order
        self._span = max(retain.values(), default=0)
        self.values: dict[str, list[float]] = {name: [] for name in order}
        self._start = 0
        self.filled_to = -1
        self._key: tuple[tuple[str, float], ...] | None = None
        self._anchor: float | None = None

    @property
    def span(self) -> int:
        return self._span

    def reset(self) -> None:
        for values in self.values.values():
            values.clear()
        self._start = 0
        self.filled_to = -1
        self._anchor = None

    def stale(self, window: Any, key: tuple[tuple[str, float], ...]) -> bool:
        """Is what is stored about a different run?

        Three ways it can be: nothing stored yet, a parameter changed, or the
        bars underneath moved. The last is checked by re-reading the close at
        the last filled bar — a dataset that agrees there and disagrees earlier
        would have to be a different series that coincides at exactly that
        index, and the run's own data receipt is what rules that out.
        """
        if self._key != key or self.filled_to < 0:
            return True
        if window.index < self.filled_to:
            return True
        return self._anchor != float(window.closes[self.filled_to])

    def begin(self, window: Any, key: tuple[tuple[str, float], ...]) -> int:
        """Prepare for a fill and return the first index that needs computing."""
        if self.stale(window, key):
            self.reset()
            self._key = key
            self._start = max(0, window.index - self._span - 1)
            return self._start
        return self.filled_to + 1

    def append(self, name: str, value: float) -> None:
        self.values[name].append(value)

    def close(self, window: Any, index: int) -> None:
        self.filled_to = index
        self._anchor = float(window.closes[index])
        limit = 2 * self._span + 64
        first = next(iter(self.values.values()), None)
        if first is None or len(first) <= limit:
            return
        drop = len(first) - (self._span + 32)
        for values in self.values.values():
            del values[:drop]
        self._start += drop

    def series(self, name: str, end: int, count: int) -> np.ndarray:
        """``count`` values of ``name`` ending at window index ``end``.

        Returns fewer than asked for — often nothing — when the history does not
        reach back that far. The transformations treat a short array as "not
        computable yet" and answer NaN, which is the only honest answer.
        """
        stored = self.values.get(name)
        if stored is None:
            return np.empty(0, dtype=np.float64)
        stop = end - self._start + 1
        start = stop - count
        if start < 0 or stop <= 0 or stop > len(stored):
            return np.empty(0, dtype=np.float64)
        return np.asarray(stored[start:stop], dtype=np.float64)


class _Evaluator:
    """Resolves operands and conditions against a window, as of a given bar."""

    __slots__ = ("_cache", "_features", "_frames", "defn", "history", "params", "window")

    def __init__(
        self,
        defn: StrategyDefinition,
        window: Any,
        params: dict[str, float],
        history: _History | None = None,
    ) -> None:
        self.defn = defn
        self.window = window
        self.params = params
        self.history = history
        self._features = {f.name: f for f in defn.features}
        self._cache: dict[tuple[str, int], float] = {}
        self._frames: dict[int, _Frame] = {}

    def frame(self, end: int) -> _Frame:
        existing = self._frames.get(end)
        if existing is None:
            existing = _Frame(self.window, end)
            self._frames[end] = existing
        return existing

    def operand(self, operand: Any, shift: int = 0) -> float:
        if isinstance(operand, Constant):
            return operand.value
        if isinstance(operand, ParamRef):
            return float(self.params[operand.name])
        if isinstance(operand, FeatureRef):
            return self.feature(operand.name, shift)
        left = self.operand(operand.left, shift)
        right = self.operand(operand.right, shift)
        if operand.op == "add":
            return left + right
        if operand.op == "sub":
            return left - right
        if operand.op == "mul":
            return left * right
        return left / right if right not in (0.0, -0.0) else float("nan")

    def feature(self, name: str, shift: int = 0) -> float:
        key = (name, shift)
        if key in self._cache:
            return self._cache[key]
        item = self._features[name]
        value = self.evaluate(item, self.window.index - shift)
        self._cache[key] = value
        return value

    def evaluate(self, item: Feature, index: int) -> float:
        """One feature's value as of window index ``index``.

        The two halves of the catalogue are answered differently and that is the
        whole of it: an observation is computed from a frame bounded at the bar,
        a transformation is computed from its source's stored history ending at
        the same bar.
        """
        if item.kind in SERIES_KINDS:
            if self.history is None:
                return float("nan")
            length = max(1, int(self.operand(item.args[0]))) if item.args else 1
            need = window_length(item.kind, length)
            values = self.history.series(item.source, index - item.shift, need)
            if values.size != need:
                return float("nan")
            return apply_transform(item.kind, values, length)
        frame = self.frame(index)
        args = [self.operand(arg) for arg in item.args]
        return _feature_value(frame, item, args, 0)

    def condition(self, condition: Any, shift: int = 0) -> bool:
        if isinstance(condition, Always):
            return True
        if isinstance(condition, Compare):
            left = self.operand(condition.left, shift)
            right = self.operand(condition.right, shift)
            # NaN means "not computable yet", which must never read as a signal.
            # `left > right` is already False for NaN, but `lt`/`lte` would be
            # too, so this is explicit rather than accidental.
            if left != left or right != right:
                return False
            return {
                "gt": left > right,
                "gte": left >= right,
                "lt": left < right,
                "lte": left <= right,
            }[condition.op]
        if isinstance(condition, Cross):
            now_left = self.operand(condition.left, shift)
            now_right = self.operand(condition.right, shift)
            was_left = self.operand(condition.left, shift + 1)
            was_right = self.operand(condition.right, shift + 1)
            if any(v != v for v in (now_left, now_right, was_left, was_right)):
                return False
            if condition.direction == "above":
                return was_left <= was_right and now_left > now_right
            return was_left >= was_right and now_left < now_right
        if isinstance(condition, SessionWindow):
            stamp = self.window.time_at(shift)
            if stamp is None:
                return False
            minute = int(stamp.hour) * 60 + int(stamp.minute)
            if condition.start_minute <= condition.end_minute:
                return condition.start_minute <= minute < condition.end_minute
            return minute >= condition.start_minute or minute < condition.end_minute
        assert isinstance(condition, Combine)
        if condition.kind == "all":
            return all(self.condition(child, shift) for child in condition.of)
        if condition.kind == "any":
            return any(self.condition(child, shift) for child in condition.of)
        return not self.condition(condition.of[0], shift)


def _fill_order(defn: StrategyDefinition, retain: dict[str, int]) -> tuple[str, ...]:
    """The retained features, sources before the transformations that read them.

    Order is not a nicety here. A transformation is computed from its source's
    stored history, so computing it before the source has been stored for this
    bar would read one value short — silently, and only at the newest bar, which
    is the worst kind of wrong.
    """
    by_name = {item.name: item for item in defn.features}
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(name: str, depth: int = 0) -> None:
        if name in seen or name not in by_name or depth > MAX_SOURCE_DEPTH + 1:
            return
        item = by_name[name]
        if item.kind in SERIES_KINDS and item.source:
            visit(item.source, depth + 1)
        seen.add(name)
        ordered.append(name)

    for name in retain:
        visit(name)
    return tuple(ordered)


# ── compilation ──────────────────────────────────────────────────────────────


class CompiledStrategy:
    """A definition wearing the runtime's module protocol.

    Holds one position's frozen levels at a time, which is safe because
    `run_backtest` is single-position and strictly sequential. The levels are
    keyed by entry index, so a stale set from a previous trade can never be
    reported against the current one.
    """

    def __init__(self, definition: StrategyDefinition) -> None:
        self.definition = validate_definition(definition)
        self._entry_index: int | None = None
        self._levels: dict[str, float] = {}
        self._trail: float | None = None
        self._context: dict[str, float] = {}
        self._by_name = {item.name: item for item in self.definition.features}
        retain = self.definition.source_history()
        self._history = _History(retain, _fill_order(self.definition, retain))

    # -- evaluation -----------------------------------------------------------
    def _evaluator(self, w: Any, params: dict[str, float]) -> _Evaluator:
        """An evaluator for this bar, with every transformed source up to date.

        The fill loop normally runs once — one new bar, one new value per
        source — because `run_backtest` walks forward. It runs many times on the
        first call of a run, which is the backfill that makes the earliest
        transformation defined rather than NaN for its first window.
        """
        history = self._history
        if history.span:
            key = tuple(sorted(params.items()))
            index = w.index
            start = history.begin(w, key)
            if start <= index:
                filler = _Evaluator(self.definition, w, params, history)
                for step in range(start, index + 1):
                    for name in history.values:
                        history.append(name, filler.evaluate(self._by_name[name], step))
                    history.close(w, step)
        return _Evaluator(self.definition, w, params, history)

    # -- protocol -------------------------------------------------------------
    def entry_signal(self, w: Any, p: dict[str, ParamValue]) -> int | None:
        params = {k: float(v) for k, v in p.items()}
        ev = self._evaluator(w, params)
        entry = self.definition.entry
        if entry.session is not None and not ev.condition(entry.session):
            return None
        if entry.long is not None and ev.condition(entry.long):
            self._context = self._snapshot(ev)
            return 1
        if entry.short is not None and ev.condition(entry.short):
            self._context = self._snapshot(ev)
            return -1
        return None

    def exit_signal(self, w: Any, p: dict[str, ParamValue], pos: Any) -> str | None:
        params = {k: float(v) for k, v in p.items()}
        ev = self._evaluator(w, params)
        rules = self.definition.exit

        if self._entry_index != pos.entry_index:
            # The first bar of this position has closed. Freeze the levels the
            # trade is actually running under, from the fill price the runtime
            # gives us and the features as they stand on the entry bar.
            self._entry_index = pos.entry_index
            self._levels = self._freeze(ev, pos)
            self._trail = self._levels.get("trailing_stop")

        stop = self._levels.get("stop")
        target = self._levels.get("target")
        low = float(w.lows[-1])
        high = float(w.highs[-1])
        close = float(w.closes[-1])

        if self._trail is not None:
            distance = self._levels.get("trailing_distance", 0.0)
            self._trail = (
                max(self._trail, close - distance)
                if pos.direction == 1
                else min(self._trail, close + distance)
            )
            self._levels["trailing_stop"] = self._trail

        # Order matters, and it is the conservative one: a bar that touched both
        # the stop and the target is reported as a stop, because the sequence
        # inside the bar is unknown and assuming the good one is how backtests
        # flatter themselves.
        if stop is not None:
            if pos.direction == 1 and low <= stop:
                return "stop"
            if pos.direction == -1 and high >= stop:
                return "stop"
        if self._trail is not None:
            if pos.direction == 1 and low <= self._trail:
                return "stop"
            if pos.direction == -1 and high >= self._trail:
                return "stop"
        if target is not None:
            if pos.direction == 1 and high >= target:
                return "signal"
            if pos.direction == -1 and low <= target:
                return "signal"
        if rules.max_bars is not None:
            held = w.index - pos.entry_index
            if held >= int(ev.operand(rules.max_bars)):
                return "max_bars"
        if rules.flat_by_minute is not None:
            stamp = w.time_at(0)
            if stamp is not None and stamp.hour * 60 + stamp.minute >= rules.flat_by_minute:
                return "signal"
        if rules.signal is not None and ev.condition(rules.signal):
            return "signal"
        return None

    # -- reporting ------------------------------------------------------------
    def position_levels(self) -> dict[str, float]:
        """The levels in force for the position that just closed.

        Read by `run_backtest` when it writes the trade, so the ledger records
        the stop and target the trade actually ran under rather than a number
        recomputed later against different bars.
        """
        return {k: round(v, 6) for k, v in self._levels.items()}

    def entry_context(self) -> dict[str, float]:
        """Feature values at the decision bar of the most recent entry.

        This is what "why did it enter?" is answered from. Recorded rather than
        recomputed, because recomputing needs the bars and the ledger has to be
        readable without them.
        """
        return dict(self._context)

    # -- internals ------------------------------------------------------------
    def _snapshot(self, ev: _Evaluator) -> dict[str, float]:
        values: dict[str, float] = {}
        for item in self.definition.features:
            value = ev.feature(item.name)
            if value == value:  # drop NaN rather than serialise it
                values[item.name] = round(value, 6)
        return values

    def _freeze(self, ev: _Evaluator, pos: Any) -> dict[str, float]:
        rules = self.definition.exit
        levels: dict[str, float] = {}
        entry = float(pos.entry_price)

        def distance(level: Level) -> float | None:
            multiple = ev.operand(level.multiple)
            if multiple != multiple:
                return None
            if level.kind == "points":
                return abs(multiple)
            if level.kind == "percent":
                return abs(entry * multiple)
            unit = ev.feature(level.feature)
            return None if unit != unit else abs(unit * multiple)

        if rules.stop is not None:
            span = distance(rules.stop)
            if span is not None:
                levels["stop"] = entry - span * pos.direction
                levels["stop_distance"] = span
        if rules.target is not None:
            span = distance(rules.target)
            if span is not None:
                levels["target"] = entry + span * pos.direction
                levels["target_distance"] = span
        if rules.trailing is not None:
            span = distance(rules.trailing)
            if span is not None:
                levels["trailing_stop"] = entry - span * pos.direction
                levels["trailing_distance"] = span
        return levels


def compile_definition(definition: StrategyDefinition) -> CompiledStrategy:
    """Turn a definition into something `run_backtest` can execute.

    Validation happens here, so an invalid definition fails at compile time with
    a sentence rather than at bar 40,000 with a KeyError.
    """
    return CompiledStrategy(definition)

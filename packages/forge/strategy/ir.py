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
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import Field

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel
from forge.strategy.models import ParameterSpec, ParamValue

IR_SCHEMA_VERSION = "1"

#: Every feature the IR can compute, and how many arguments it takes. Closed on
#: purpose: see the module docstring.
FEATURE_ARITY: dict[str, int] = {
    "close": 0,
    "open": 0,
    "high": 0,
    "low": 0,
    "volume": 0,
    "sma": 1,
    # A signal about liquidity has to be able to say "compared with usual". The
    # raw `volume` feature is an absolute count, and comparing it to a price
    # average — the only other average the vocabulary had — is a category
    # error that silently never fires. This is the baseline that makes the
    # `liquidity` family reachable at all.
    "volume_sma": 1,
    "ema": 1,
    "atr": 1,
    "adx": 1,
    "rsi": 1,
    "highest": 1,
    "lowest": 1,
    "realised_vol": 1,
    "roc": 1,
    "session_vwap": 0,
    "session_vwap_sd": 0,
    "opening_range_high": 1,
    "opening_range_low": 1,
    "bars_since_session_open": 0,
    "minute_of_day": 0,
}

#: Features needing more history than their length argument implies. Used to
#: derive a warmup that is sufficient rather than hopeful.
_WARMUP_MULTIPLIER: dict[str, int] = {"adx": 3, "atr": 2, "rsi": 4, "ema": 5, "realised_vol": 2}


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
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: str
    args: tuple[Operand, ...] = ()
    shift: int = Field(default=0, ge=0, le=500)
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
        """
        need = 1
        for item in self.features:
            length = 1
            for arg in item.args:
                length = max(length, int(_static_upper_bound(arg, self)))
            need = max(need, length * _WARMUP_MULTIPLIER.get(item.kind, 1) + item.shift + 2)
        return max(self.warmup_bars, need + 5)


Arithmetic.model_rebuild()
Combine.model_rebuild()
StrategyDefinition.model_rebuild()


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
    """The arrays a definition is evaluated against.

    Constructed from the `Window` the runtime hands the strategy, so it inherits
    the window's right bound: the last element is the last closed bar and
    nothing past it exists in the arrays.
    """

    __slots__ = ("c", "h", "index", "l", "o", "session_start", "v", "window")

    def __init__(self, window: Any) -> None:
        self.window = window
        self.o = window.opens
        self.h = window.highs
        self.l = window.lows
        self.c = window.closes
        self.v = window.volumes
        self.index = window.index
        self.session_start = window.session_start_index()


def _tail(array: np.ndarray, shift: int, length: int) -> np.ndarray:
    """The `length` values ending `shift` bars before the last closed bar."""
    stop = array.size - shift
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
        idx = source.size - 1 - total
        return float(source[idx]) if idx >= 0 else nan

    if kind == "minute_of_day":
        stamp = frame.window.time_at(total)
        return nan if stamp is None else float(stamp.hour * 60 + stamp.minute)

    if kind == "bars_since_session_open":
        return float(max(0, frame.index - total - frame.session_start))

    if kind in ("session_vwap", "session_vwap_sd"):
        stop = frame.c.size - total
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
        stop = min(start + bars, frame.c.size - total)
        if stop <= start:
            return nan
        return (
            float(frame.h[start:stop].max())
            if kind == "opening_range_high"
            else float(frame.l[start:stop].min())
        )

    length = max(1, int(args[0])) if args else 1

    if kind == "sma":
        values = _tail(frame.c, total, length)
        return float(values.mean()) if values.size == length else nan

    if kind == "volume_sma":
        values = _tail(frame.v, total, length)
        return float(values.mean()) if values.size == length else nan

    if kind == "ema":
        span = max(2, length)
        depth = span * 5
        values = _tail(frame.c, total, depth)
        if values.size < depth:
            return nan
        alpha = 2.0 / (span + 1.0)
        weights = (1.0 - alpha) ** np.arange(values.size - 1, -1, -1)
        return float((values * weights).sum() / weights.sum())

    if kind == "highest":
        values = _tail(frame.h, total, length)
        return float(values.max()) if values.size == length else nan

    if kind == "lowest":
        values = _tail(frame.l, total, length)
        return float(values.min()) if values.size == length else nan

    if kind == "atr":
        high = _tail(frame.h, total, length)
        low = _tail(frame.l, total, length)
        prev = _tail(frame.c, total + 1, length)
        if high.size != length or prev.size != length:
            return nan
        true_range = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
        return float(true_range.mean())

    if kind == "realised_vol":
        values = _tail(frame.c, total, length + 1)
        if values.size != length + 1 or bool((values <= 0).any()):
            return nan
        return float(np.diff(np.log(values)).std(ddof=1))

    if kind == "roc":
        values = _tail(frame.c, total, length + 1)
        if values.size != length + 1 or values[0] == 0:
            return nan
        return float((values[-1] - values[0]) / values[0])

    if kind == "rsi":
        depth = length * 4
        values = _tail(frame.c, total, depth)
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
        high = _tail(frame.h, total, span)
        low = _tail(frame.l, total, span)
        close = _tail(frame.c, total, span)
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


class _Evaluator:
    """Resolves operands and conditions against one frame."""

    def __init__(self, defn: StrategyDefinition, frame: _Frame, params: dict[str, float]) -> None:
        self.defn = defn
        self.frame = frame
        self.params = params
        self._features = {f.name: f for f in defn.features}
        self._cache: dict[tuple[str, int], float] = {}

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
        args = [self.operand(arg, shift) for arg in item.args]
        value = _feature_value(self.frame, item, args, shift)
        self._cache[key] = value
        return value

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
            stamp = self.frame.window.time_at(shift)
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

    # -- protocol -------------------------------------------------------------
    def entry_signal(self, w: Any, p: dict[str, ParamValue]) -> int | None:
        params = {k: float(v) for k, v in p.items()}
        ev = _Evaluator(self.definition, _Frame(w), params)
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
        ev = _Evaluator(self.definition, _Frame(w), params)
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

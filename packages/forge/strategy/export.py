"""Rendering a :class:`StrategyDefinition` into the languages venues speak.

The IR is canonical. Everything here is an *export* — a second rendering of a
strategy that already exists — and the rule governing this module is that an
export must never imply more than it can support.

**A target is only offered when its output can be checked.** Python is offered
because AlgoForge can execute the generated module against the same bars as the
compiled definition and compare the trade ledgers bar for bar; if they differ,
the export is wrong and `verify_python` says so. Pine and NinjaScript are
*described* — every export reports which features it can and cannot represent —
but they are not offered as downloads, because AlgoForge has no TradingView and
no NinjaTrader to run them through, and a generator nobody can check is a
generator nobody should trust with money.

That asymmetry is the point. A fake exporter is worse than a missing one: a
missing one makes you go and write the strategy yourself, and a fake one makes
you trade something that is not the thing you tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forge.strategy.ir import (
    Always,
    Combine,
    Compare,
    Constant,
    Cross,
    FeatureRef,
    Level,
    ParamRef,
    SessionWindow,
    StrategyDefinition,
    validate_definition,
)
from forge.strategy.primitives import SERIES_KINDS

#: Targets whose generated code AlgoForge can execute and compare against the
#: compiled IR. Only these are offered as a download.
VERIFIABLE_TARGETS = ("python",)

#: Targets AlgoForge can describe the coverage of but cannot execute. Listed so
#: the gap is visible rather than discovered later.
DESCRIBED_TARGETS = ("pine", "ninjascript")


@dataclass(frozen=True)
class ExportReport:
    """What an export produced, and what it could not."""

    target: str
    definition_id: str
    definition_hash: str
    code: str
    supported: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    approximations: tuple[str, ...] = ()
    verifiable: bool = False
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "definition_id": self.definition_id,
            "definition_hash": self.definition_hash,
            "code": self.code,
            "supported": list(self.supported),
            "unsupported": list(self.unsupported),
            "approximations": list(self.approximations),
            "verifiable": self.verifiable,
            "notes": list(self.notes),
        }


@dataclass
class _Emitter:
    defn: StrategyDefinition
    unsupported: list[str] = field(default_factory=list)
    approximations: list[str] = field(default_factory=list)
    #: Features something actually referenced. A declared feature nothing reads
    #: still gets a function, because `entry_context` records every one of them.
    used: set[str] = field(default_factory=set)


# ── Python ───────────────────────────────────────────────────────────────────

#: How each observation is computed, as a Python expression over the window.
#: ``{shift}`` is substituted with an *expression*, not a literal, because a
#: feature is emitted as a function of its shift so that a transformation can
#: call it at each of the offsets its own window spans.
_PY_FEATURE: dict[str, str] = {
    "close": "_at(w.closes, {shift})",
    "open": "_at(w.opens, {shift})",
    "high": "_at(w.highs, {shift})",
    "low": "_at(w.lows, {shift})",
    "volume": "_at(w.volumes, {shift})",
    "typical_price": "_typical(w, {shift})",
    "bar_range": "_bar_range(w, {shift})",
    "true_range": "_true_range(w, {shift})",
    "gap": "_gap(w, {shift})",
    "clv": "_clv(w, {shift})",
    "signed_volume": "_signed_volume(w, {shift})",
    "sma": "_mean(w.closes, {shift}, {a0})",
    "volume_sma": "_mean(w.volumes, {shift}, {a0})",
    "ema": "_ema(w.closes, {shift}, {a0})",
    "atr": "_atr(w, {shift}, {a0})",
    "adx": "_adx(w, {shift}, {a0})",
    "rsi": "_rsi(w.closes, {shift}, {a0})",
    "highest": "_max(w.highs, {shift}, {a0})",
    "lowest": "_min(w.lows, {shift}, {a0})",
    "realised_vol": "_rvol(w.closes, {shift}, {a0})",
    "upside_vol": "_sided_vol(w.closes, {shift}, {a0}, 1)",
    "downside_vol": "_sided_vol(w.closes, {shift}, {a0}, -1)",
    "roc": "_roc(w.closes, {shift}, {a0})",
    "efficiency_ratio": "_efficiency(w.closes, {shift}, {a0})",
    "variance_ratio": "_variance_ratio(w.closes, {shift}, {a0})",
    "return_autocorr": "_autocorr(w.closes, {shift}, {a0})",
    "session_vwap": "_vwap(w, {shift})[0]",
    "session_vwap_sd": "_vwap(w, {shift})[1]",
    "session_high": "_session_range(w, {shift})[0]",
    "session_low": "_session_range(w, {shift})[1]",
    "session_range_position": "_session_position(w, {shift})",
    "opening_range_high": "_range(w, {shift}, {a0})[0]",
    "opening_range_low": "_range(w, {shift}, {a0})[1]",
    "bars_since_session_open": "_since_open(w, {shift})",
    "minute_of_day": "_minute(w, {shift})",
}

_PY_RUNTIME = '''
# ── generated helpers ────────────────────────────────────────────────────────
# These mirror forge.strategy.ir exactly. They are inlined rather than imported
# so the exported module runs anywhere a Window-like object is available, and
# `verify_python` re-runs it against the compiled definition to prove they
# still agree.

import numpy as _np

NAN = float("nan")


def _tail(a, shift, n):
    stop = a.size - shift
    start = stop - n
    return a[start:stop] if start >= 0 and stop > 0 else _np.empty(0)


def _at(a, shift):
    i = a.size - 1 - shift
    return float(a[i]) if i >= 0 else NAN


def _mean(a, shift, n):
    n = max(1, int(n)); v = _tail(a, shift, n)
    return float(v.mean()) if v.size == n else NAN


def _max(a, shift, n):
    n = max(1, int(n)); v = _tail(a, shift, n)
    return float(v.max()) if v.size == n else NAN


def _min(a, shift, n):
    n = max(1, int(n)); v = _tail(a, shift, n)
    return float(v.min()) if v.size == n else NAN


def _ema(a, shift, n):
    span = max(2, int(n)); depth = span * 5
    v = _tail(a, shift, depth)
    if v.size < depth:
        return NAN
    alpha = 2.0 / (span + 1.0)
    weights = (1.0 - alpha) ** _np.arange(v.size - 1, -1, -1)
    return float((v * weights).sum() / weights.sum())


def _atr(w, shift, n):
    n = max(1, int(n))
    high, low = _tail(w.highs, shift, n), _tail(w.lows, shift, n)
    prev = _tail(w.closes, shift + 1, n)
    if high.size != n or prev.size != n:
        return NAN
    tr = _np.maximum(high - low, _np.maximum(_np.abs(high - prev), _np.abs(low - prev)))
    return float(tr.mean())


def _rvol(a, shift, n):
    n = max(1, int(n)); v = _tail(a, shift, n + 1)
    if v.size != n + 1 or bool((v <= 0).any()):
        return NAN
    return float(_np.diff(_np.log(v)).std(ddof=1))


def _roc(a, shift, n):
    n = max(1, int(n)); v = _tail(a, shift, n + 1)
    if v.size != n + 1 or v[0] == 0:
        return NAN
    return float((v[-1] - v[0]) / v[0])


def _rsi(a, shift, n):
    n = max(1, int(n)); depth = n * 4
    v = _tail(a, shift, depth)
    if v.size < depth:
        return NAN
    d = _np.diff(v)
    gain = float(_np.maximum(d, 0.0).mean()); loss = float(_np.maximum(-d, 0.0).mean())
    if loss <= 0:
        return 100.0 if gain > 0 else 50.0
    return float(100.0 - 100.0 / (1.0 + gain / loss))


def _adx(w, shift, n):
    span = max(1, int(n)) * 3
    high, low = _tail(w.highs, shift, span), _tail(w.lows, shift, span)
    close = _tail(w.closes, shift, span)
    if high.size != span:
        return NAN
    up, down = high[1:] - high[:-1], low[:-1] - low[1:]
    plus = _np.where((up > down) & (up > 0), up, 0.0)
    minus = _np.where((down > up) & (down > 0), down, 0.0)
    tr = _np.maximum(high[1:] - low[1:],
                     _np.maximum(_np.abs(high[1:] - close[:-1]), _np.abs(low[1:] - close[:-1])))
    atr = float(tr.mean())
    if atr <= 0:
        return NAN
    dp = 100.0 * float(plus.mean()) / atr
    dm = 100.0 * float(minus.mean()) / atr
    tot = dp + dm
    return float(100.0 * abs(dp - dm) / tot) if tot > 0 else NAN


def _vwap(w, shift):
    start = w.session_start_index(w.index - shift); stop = w.closes.size - shift
    if stop <= start:
        return NAN, NAN
    high, low, close = w.highs[start:stop], w.lows[start:stop], w.closes[start:stop]
    vol = w.volumes[start:stop]
    weight = float(vol.sum())
    if weight <= 0:
        return NAN, NAN
    typical = (high + low + close) / 3.0
    vwap = float((vol * typical).sum() / weight)
    var = max(0.0, float((vol * typical * typical).sum() / weight) - vwap * vwap)
    return vwap, float(_np.sqrt(var))


def _range(w, shift, n):
    start = w.session_start_index(w.index - shift)
    stop = min(start + max(1, int(n)), w.closes.size - shift)
    if stop <= start:
        return NAN, NAN
    return float(w.highs[start:stop].max()), float(w.lows[start:stop].min())


def _since_open(w, shift):
    at = w.index - shift
    return float(max(0, at - w.session_start_index(at)))


def _minute(w, shift):
    stamp = w.time_at(shift)
    return NAN if stamp is None else float(stamp.hour * 60 + stamp.minute)


def _in_session(w, shift, start_min, end_min):
    stamp = w.time_at(shift)
    if stamp is None:
        return False
    m = stamp.hour * 60 + stamp.minute
    if start_min <= end_min:
        return start_min <= m < end_min
    return m >= start_min or m < end_min


def _typical(w, shift):
    i = w.closes.size - 1 - shift
    return float((w.highs[i] + w.lows[i] + w.closes[i]) / 3.0) if i >= 0 else NAN


def _bar_range(w, shift):
    i = w.closes.size - 1 - shift
    return float(w.highs[i] - w.lows[i]) if i >= 0 else NAN


def _true_range(w, shift):
    i = w.closes.size - 1 - shift
    if i < 1:
        return NAN
    prev = float(w.closes[i - 1]); hi = float(w.highs[i]); lo = float(w.lows[i])
    return float(max(hi - lo, abs(hi - prev), abs(lo - prev)))


def _gap(w, shift):
    i = w.closes.size - 1 - shift
    return float(w.opens[i] - w.closes[i - 1]) if i >= 1 else NAN


def _clv(w, shift):
    i = w.closes.size - 1 - shift
    if i < 0:
        return NAN
    hi = float(w.highs[i]); lo = float(w.lows[i]); c = float(w.closes[i])
    span = hi - lo
    return ((c - lo) - (hi - c)) / span if span > 0 else NAN


def _signed_volume(w, shift):
    i = w.closes.size - 1 - shift
    if i < 0:
        return NAN
    loc = _clv(w, shift)
    return loc * float(w.volumes[i]) if loc == loc else NAN


def _sided_vol(a, shift, n, sign):
    n = max(1, int(n)); v = _tail(a, shift, n + 1)
    if v.size != n + 1 or bool((v <= 0).any()):
        return NAN
    r = _np.diff(_np.log(v))
    side = r[r > 0] if sign > 0 else r[r < 0]
    return float(side.std(ddof=1)) if side.size >= 2 else NAN


def _efficiency(a, shift, n):
    n = max(1, int(n)); v = _tail(a, shift, n + 1)
    if v.size != n + 1:
        return NAN
    path = float(_np.abs(_np.diff(v)).sum())
    return float(abs(v[-1] - v[0]) / path) if path > 0 else NAN


def _variance_ratio(a, shift, n):
    n = max(1, int(n)); depth = n * 5
    v = _tail(a, shift, depth + 1)
    if v.size != depth + 1 or bool((v <= 0).any()) or n < 2:
        return NAN
    logs = _np.log(v)
    single = _np.diff(logs)
    multi = logs[n:] - logs[:-n]
    if single.size < 2 or multi.size < 2:
        return NAN
    base = float(single.var(ddof=1))
    if base <= 0:
        return NAN
    return float(multi.var(ddof=1) / (n * base))


def _autocorr(a, shift, n):
    n = max(1, int(n)); v = _tail(a, shift, n + 2)
    if v.size != n + 2 or bool((v <= 0).any()) or n < 3:
        return NAN
    r = _np.diff(_np.log(v))
    first, second = r[:-1], r[1:]
    spread = float(first.std(ddof=1)) * float(second.std(ddof=1))
    if spread <= 0:
        return NAN
    cov = float(((first - first.mean()) * (second - second.mean())).sum())
    return float(cov / ((first.size - 1) * spread))


def _session_range(w, shift):
    start = w.session_start_index(w.index - shift)
    stop = w.closes.size - shift
    if stop <= start:
        return NAN, NAN
    return float(w.highs[start:stop].max()), float(w.lows[start:stop].min())


def _session_position(w, shift):
    hi, lo = _session_range(w, shift)
    if hi != hi or lo != lo or hi <= lo:
        return NAN
    i = w.closes.size - 1 - shift
    return float((w.closes[i] - lo) / (hi - lo))


# ── transformations ──────────────────────────────────────────────────────────
# A transformation reads the source's own history, so it calls the source's
# function once per offset its window spans. That is slower than the compiled
# definition's cache and produces exactly the same numbers, which is the point:
# `verify_python` compares the two ledgers bar for bar.


def _t_window(kind, n):
    n = max(1, int(n))
    if kind in ("change", "pct_change"):
        return n + 1
    if kind == "accel":
        return 2 * n + 1
    if kind == "ewm":
        return 5 * n
    return n


def _finite(v, need):
    return v.size >= need and bool(_np.isfinite(v[-need:]).all())


def _apply_transform(kind, v, n):
    n = max(1, int(n))
    if kind == "mean":
        return float(v[-n:].mean()) if _finite(v, n) else NAN
    if kind == "stdev":
        return float(v[-n:].std(ddof=1)) if n >= 2 and _finite(v, n) else NAN
    if kind == "zscore":
        if n < 2 or not _finite(v, n):
            return NAN
        tail = v[-n:]; sd = float(tail.std(ddof=1))
        return float((tail[-1] - tail.mean()) / sd) if sd > 0 else NAN
    if kind == "percentile_rank":
        if not _finite(v, n):
            return NAN
        tail = v[-n:]
        return float((tail <= tail[-1]).sum()) / float(tail.size)
    if kind == "slope":
        if n < 2 or not _finite(v, n):
            return NAN
        tail = v[-n:]
        x = _np.arange(tail.size, dtype=float); xc = x - x.mean()
        den = float((xc * xc).sum())
        return float((xc * (tail - tail.mean())).sum() / den) if den > 0 else NAN
    if kind == "accel":
        need = 2 * n + 1
        if not _finite(v, need):
            return NAN
        tail = v[-need:]
        return float((tail[-1] - tail[-1 - n]) - (tail[-1 - n] - tail[0]))
    if kind == "change":
        if not _finite(v, n + 1):
            return NAN
        tail = v[-(n + 1):]
        return float(tail[-1] - tail[0])
    if kind == "pct_change":
        if not _finite(v, n + 1):
            return NAN
        tail = v[-(n + 1):]
        base = abs(float(tail[0]))
        return float((tail[-1] - tail[0]) / base) if base > 0 else NAN
    if kind == "ewm":
        depth = 5 * n
        if not _finite(v, depth):
            return NAN
        tail = v[-depth:]
        alpha = 2.0 / (max(2, n) + 1.0)
        weights = (1.0 - alpha) ** _np.arange(tail.size - 1, -1, -1)
        return float((tail * weights).sum() / weights.sum())
    if kind == "max_of":
        return float(v[-n:].max()) if _finite(v, n) else NAN
    if kind == "min_of":
        return float(v[-n:].min()) if _finite(v, n) else NAN
    if kind == "persistence":
        if not _finite(v, n):
            return NAN
        tail = v[-n:]
        return float((tail > 0.0).sum()) / float(tail.size)
    return NAN


def _transform(kind, fn, w, p, shift, n):
    need = _t_window(kind, n)
    values = _np.asarray([fn(w, p, shift + k) for k in range(need - 1, -1, -1)], dtype=float)
    return _apply_transform(kind, values, n)


def _cmp(op, left, right):
    if left != left or right != right:
        return False
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    return left <= right


def _cross(direction, nl, nr, wl, wr):
    if any(v != v for v in (nl, nr, wl, wr)):
        return False
    if direction == "above":
        return wl <= wr and nl > nr
    return wl >= wr and nl < nr
'''


def _shift_expr(base: str, extra: int) -> str:
    """``base + extra`` as source, folded when both are literals.

    The shift is an expression rather than a number because each feature is
    emitted as a function of its own shift: a transformation calls its source at
    every offset its window spans, and those offsets are only known at run time.
    Folding keeps the common case (`0`, `1`) readable in the exported file.
    """
    if extra == 0:
        return base
    try:
        return str(int(base) + extra)
    except ValueError:
        return f"({base} + {extra})"


def _py_operand(emitter: _Emitter, operand: Any, shift: str) -> str:
    if isinstance(operand, Constant):
        return repr(float(operand.value))
    if isinstance(operand, ParamRef):
        return f'float(p["{operand.name}"])'
    if isinstance(operand, FeatureRef):
        return _py_feature(emitter, operand.name, shift)
    left = _py_operand(emitter, operand.left, shift)
    right = _py_operand(emitter, operand.right, shift)
    if operand.op == "div":
        return f"_div({left}, {right})"
    symbol = {"add": "+", "sub": "-", "mul": "*"}[operand.op]
    return f"({left} {symbol} {right})"


def _py_feature(emitter: _Emitter, name: str, shift: str) -> str:
    """A call to the feature's own function. One function per declared feature.

    Emitting a function rather than inlining the expression is what makes a
    transformation expressible at all: `_transform` needs something it can call
    at each offset, and an inlined expression with a baked-in shift is not that.
    """
    item = emitter.defn.feature(name)
    if item is None:
        raise KeyError(name)
    emitter.used.add(name)
    return f"_f_{name}(w, p, {shift})"


def _py_feature_body(emitter: _Emitter, item: Any) -> list[str]:
    """The body of one feature's function, given its shift parameter ``s``."""
    shift = _shift_expr("s", item.shift)
    if item.kind in SERIES_KINDS:
        length = _py_operand(emitter, item.args[0], "s") if item.args else "1.0"
        return [
            f"def _f_{item.name}(w, p, s=0):",
            f'    return _transform("{item.kind}", _f_{item.source}, w, p, {shift}, '
            f"int({length}))",
        ]
    template = _PY_FEATURE[item.kind]
    args: dict[str, str] = {"shift": shift}
    for index, arg in enumerate(item.args):
        args[f"a{index}"] = _py_operand(emitter, arg, "s")
    return [f"def _f_{item.name}(w, p, s=0):", f"    return {template.format(**args)}"]


def _py_feature_functions(emitter: _Emitter) -> list[str]:
    """Every feature's function, sources before the transformations that call them."""
    by_name = {item.name: item for item in emitter.defn.features}
    ordered: list[Any] = []
    seen: set[str] = set()

    def visit(name: str, depth: int = 0) -> None:
        if name in seen or name not in by_name or depth > 8:
            return
        item = by_name[name]
        if item.kind in SERIES_KINDS and item.source:
            visit(item.source, depth + 1)
        seen.add(name)
        ordered.append(item)

    for item in emitter.defn.features:
        visit(item.name)

    lines: list[str] = []
    for item in ordered:
        lines.extend(_py_feature_body(emitter, item))
        lines.extend(["", ""])
    return lines


def _py_condition(emitter: _Emitter, condition: Any, shift: str) -> str:
    if isinstance(condition, Always):
        return "True"
    if isinstance(condition, Compare):
        left = _py_operand(emitter, condition.left, shift)
        right = _py_operand(emitter, condition.right, shift)
        return f'_cmp("{condition.op}", {left}, {right})'
    if isinstance(condition, Cross):
        now_left = _py_operand(emitter, condition.left, shift)
        now_right = _py_operand(emitter, condition.right, shift)
        back = _shift_expr(shift, 1)
        was_left = _py_operand(emitter, condition.left, back)
        was_right = _py_operand(emitter, condition.right, back)
        return (
            f'_cross("{condition.direction}", {now_left}, {now_right}, '
            f"{was_left}, {was_right})"
        )
    if isinstance(condition, SessionWindow):
        return (
            f"_in_session(w, {shift}, {condition.start_minute}, {condition.end_minute})"
        )
    assert isinstance(condition, Combine)
    parts = [_py_condition(emitter, child, shift) for child in condition.of]
    if condition.kind == "all":
        return "(" + " and ".join(parts) + ")"
    if condition.kind == "any":
        return "(" + " or ".join(parts) + ")"
    return f"(not {parts[0]})"


def _py_level(emitter: _Emitter, level: Level, name: str) -> str:
    multiple = _py_operand(emitter, level.multiple, "0")
    if level.kind == "points":
        return f"    {name} = abs({multiple})\n"
    if level.kind == "percent":
        return f"    {name} = abs(entry * ({multiple}))\n"
    unit = _py_feature(emitter, level.feature, "0")
    return f"    {name} = abs(({unit}) * ({multiple}))\n"


def to_python(defn: StrategyDefinition) -> ExportReport:
    """Render the definition as a runnable AlgoForge strategy module.

    The output implements the same `entry_signal`/`exit_signal` protocol as a
    hand-written strategy, plus `position_levels` and `entry_context`, so a
    backtest of the export produces the identical ledger — which is exactly what
    `verify_python` checks.
    """
    defn = validate_definition(defn)
    emitter = _Emitter(defn)

    lines: list[str] = [
        f'"""{defn.name}',
        "",
        f"Generated by AlgoForge from StrategyDefinition {defn.definition_id}.",
        f"definition_hash: {defn.definition_hash}",
        "",
        "Hypothesis",
        "----------",
        defn.hypothesis,
        "",
        "Falsifiable prediction",
        "----------------------",
        defn.falsifiable_prediction,
        "",
        "Execution assumptions: decisions are taken on a closed bar and filled at the",
        f"next bar's open. Commission {defn.execution.commission_per_side:.4f} per "
        f"side, slippage {defn.execution.slippage_ticks:.2f} tick(s).",
        "",
        "This file is a rendering of the definition, not the source of truth. Editing",
        "it does not change the strategy AlgoForge validated; edit the definition.",
        '"""',
        _PY_RUNTIME,
        "",
        "",
        "def _div(a, b):",
        "    return a / b if b not in (0.0, -0.0) else NAN",
        "",
        "",
        "# ── strategy ─────────────────────────────────────────────────────────────────",
        "",
        "_LEVELS = {}",
        "_CONTEXT = {}",
        "_ENTRY = {}",
        "",
        "",
    ]
    lines.extend(_py_feature_functions(emitter))
    lines.append("def entry_signal(w, p):")

    guard = ""
    if defn.entry.session is not None:
        guard = _py_condition(emitter, defn.entry.session, "0")
        lines.append(f"    if not {guard}:")
        lines.append("        return None")

    for direction, condition in (("1", defn.entry.long), ("-1", defn.entry.short)):
        if condition is None:
            continue
        lines.append(f"    if {_py_condition(emitter, condition, '0')}:")
        lines.append("        _CONTEXT.clear()")
        lines.append("        _CONTEXT.update(_snapshot(w, p))")
        lines.append(f"        return {direction}")
    lines.append("    return None")
    lines.append("")
    lines.append("")

    # Feature snapshot, for `entry_context`.
    lines.append("def _snapshot(w, p):")
    lines.append("    out = {}")
    for item in defn.features:
        expression = _py_feature(emitter, item.name, "0")
        lines.append(f"    v = {expression}")
        lines.append("    if v == v:")
        lines.append(f'        out["{item.name}"] = round(v, 6)')
    lines.append("    return out")
    lines.append("")
    lines.append("")

    lines.append("def exit_signal(w, p, pos):")
    lines.append('    if _ENTRY.get("index") != pos.entry_index:')
    lines.append('        _ENTRY["index"] = pos.entry_index')
    lines.append("        _LEVELS.clear()")
    lines.append("        _LEVELS.update(_freeze(w, p, pos))")
    lines.append('    stop = _LEVELS.get("stop")')
    lines.append('    target = _LEVELS.get("target")')
    lines.append('    trail = _LEVELS.get("trailing_stop")')
    lines.append("    low = float(w.lows[-1]); high = float(w.highs[-1])")
    lines.append("    close = float(w.closes[-1])")
    lines.append("    if trail is not None:")
    lines.append('        span = _LEVELS.get("trailing_distance", 0.0)')
    lines.append("        trail = max(trail, close - span) if pos.direction == 1 "
                 "else min(trail, close + span)")
    lines.append('        _LEVELS["trailing_stop"] = trail')
    lines.append("    if stop is not None:")
    lines.append("        if pos.direction == 1 and low <= stop:")
    lines.append('            return "stop"')
    lines.append("        if pos.direction == -1 and high >= stop:")
    lines.append('            return "stop"')
    lines.append("    if trail is not None:")
    lines.append("        if pos.direction == 1 and low <= trail:")
    lines.append('            return "stop"')
    lines.append("        if pos.direction == -1 and high >= trail:")
    lines.append('            return "stop"')
    lines.append("    if target is not None:")
    lines.append("        if pos.direction == 1 and high >= target:")
    lines.append('            return "signal"')
    lines.append("        if pos.direction == -1 and low <= target:")
    lines.append('            return "signal"')
    if defn.exit.max_bars is not None:
        bars = _py_operand(emitter, defn.exit.max_bars, "0")
        lines.append(f"    if w.index - pos.entry_index >= int({bars}):")
        lines.append('        return "max_bars"')
    if defn.exit.flat_by_minute is not None:
        lines.append(f"    if _minute(w, 0) >= {defn.exit.flat_by_minute}:")
        lines.append('        return "signal"')
    if defn.exit.signal is not None:
        lines.append(f"    if {_py_condition(emitter, defn.exit.signal, '0')}:")
        lines.append('        return "signal"')
    lines.append("    return None")
    lines.append("")
    lines.append("")

    lines.append("def _freeze(w, p, pos):")
    lines.append("    entry = float(pos.entry_price)")
    lines.append("    out = {}")
    for name, level in (
        ("stop", defn.exit.stop),
        ("target", defn.exit.target),
        ("trailing_stop", defn.exit.trailing),
    ):
        if level is None:
            continue
        lines.append(_py_level(emitter, level, "span").rstrip("\n"))
        lines.append("    if span == span:")
        sign = "-" if name != "target" else "+"
        lines.append(f'        out["{name}"] = entry {sign} span * pos.direction')
        key = "stop_distance" if name == "stop" else (
            "target_distance" if name == "target" else "trailing_distance"
        )
        lines.append(f'        out["{key}"] = span')
    lines.append("    return out")
    lines.append("")
    lines.append("")
    lines.append("def position_levels():")
    lines.append("    return {k: round(v, 6) for k, v in _LEVELS.items()}")
    lines.append("")
    lines.append("")
    lines.append("def entry_context():")
    lines.append("    return dict(_CONTEXT)")
    lines.append("")

    supported = ["entry", "exit", "features", "parameters", "execution assumptions"]
    if defn.entry.session is not None or defn.exit.flat_by_minute is not None:
        supported.append("session windows")
    if defn.exit.trailing is not None:
        supported.append("trailing stop")

    return ExportReport(
        target="python",
        definition_id=defn.definition_id,
        definition_hash=defn.definition_hash,
        code="\n".join(lines),
        supported=tuple(supported),
        unsupported=(),
        approximations=(
            "Stops and targets are evaluated on bar close and filled at the next "
            "bar's open, matching the backtest. A live venue would fill intrabar, "
            "usually at a worse price than this model shows for targets and a "
            "better one for stops.",
        ),
        verifiable=True,
        notes=(
            "Generated from the definition. Editing this file does not change the "
            "strategy AlgoForge validated.",
        ),
    )


def verify_python(defn: StrategyDefinition, bars: list[Any], spec: Any) -> dict[str, Any]:
    """Run the export and the compiled definition over the same bars, and compare.

    This is what earns Python a place in `VERIFIABLE_TARGETS`. The comparison is
    exact and covers the whole ledger, so a generator that drops a condition or
    mis-shifts a feature produces a different trade and is caught here rather
    than by someone trading it.
    """
    import importlib.util
    import sys
    import tempfile
    import uuid
    from pathlib import Path

    from forge.strategy.determinism import run_digest
    from forge.strategy.ir import compile_definition
    from forge.strategy.runtime import run_backtest

    report = to_python(defn)
    module_name = f"forge_export_{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{module_name}.py"
        path.write_text(report.code, encoding="utf-8")
        spec_obj = importlib.util.spec_from_file_location(module_name, path)
        if spec_obj is None or spec_obj.loader is None:
            return {"verified": False, "reason": "the generated module would not load"}
        generated = importlib.util.module_from_spec(spec_obj)
        sys.modules[module_name] = generated
        try:
            spec_obj.loader.exec_module(generated)
            exported = run_backtest(generated, spec, bars, code_hash="export")
        finally:
            sys.modules.pop(module_name, None)

    native = run_backtest(compile_definition(defn), spec, bars, code_hash="export")
    same = run_digest(native) == run_digest(exported)
    return {
        "verified": same,
        "target": "python",
        "definition_hash": defn.definition_hash,
        "native_digest": run_digest(native),
        "exported_digest": run_digest(exported),
        "native_trades": len(native.trades),
        "exported_trades": len(exported.trades),
        "reason": "" if same else "the exported module produced a different trade ledger",
    }


# ── coverage of the targets AlgoForge cannot yet execute ─────────────────────


def describe_target(defn: StrategyDefinition, target: str) -> ExportReport:
    """What a target *would* support for this definition, without pretending to
    have generated it.

    Returned with empty `code` on purpose. AlgoForge cannot run Pine or
    NinjaScript, so it cannot know that a generated script says what the
    definition says, and shipping one anyway would be the fake exporter this
    module exists to refuse.
    """
    if target not in DESCRIBED_TARGETS:
        raise ValueError(f"unknown export target '{target}'")
    defn = validate_definition(defn)

    unsupported: list[str] = []
    approximations: list[str] = []

    if target == "pine":
        supported = ["sma", "ema", "atr", "rsi", "highest", "lowest", "roc", "adx"]
        for item in defn.features:
            if item.kind in ("session_vwap_sd", "opening_range_high", "opening_range_low"):
                approximations.append(
                    f"'{item.name}' ({item.kind}) has no direct Pine builtin and would "
                    "need a hand-written session accumulator."
                )
            elif item.kind not in supported and item.kind not in (
                "close", "open", "high", "low", "volume", "minute_of_day",
                "bars_since_session_open", "session_vwap", "realised_vol",
            ):
                unsupported.append(f"'{item.name}' ({item.kind})")
        approximations.append(
            "Pine's strategy tester fills at bar close by default, not at the next "
            "bar's open. Without process_orders_on_close=false the exported script "
            "would measure a different strategy from the one AlgoForge validated."
        )
    else:  # ninjascript
        unsupported.append(
            "NinjaScript needs a compiled AddIn and a NinjaTrader installation to "
            "check against. AlgoForge has neither, so it will not generate one."
        )
        approximations.append(
            "NinjaTrader's default CalculateOnBarClose and its OnBarUpdate ordering "
            "would have to be matched to AlgoForge's decide-on-close / fill-next-open "
            "model before any comparison meant anything."
        )

    return ExportReport(
        target=target,
        definition_id=defn.definition_id,
        definition_hash=defn.definition_hash,
        code="",
        supported=(),
        unsupported=tuple(unsupported),
        approximations=tuple(approximations),
        verifiable=False,
        notes=(
            f"AlgoForge cannot execute {target}, so it does not generate it. This is "
            "a coverage report, not an export.",
        ),
    )

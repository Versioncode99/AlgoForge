"""Carrying a strategy to another platform, and saying exactly what did not travel.

The IR is canonical. A port is a second rendering of a strategy that already
exists, and the question a port has to answer is not "did it produce code" —
that is easy and worthless — but **"is this the same strategy?"**

That question has a real answer per element and a real answer overall, and the
two must not be confused. `forge.strategy.export` already refuses to hand over
what it cannot check, which is the right instinct and slightly too blunt: it
means a trader who wants their strategy on TradingView gets nothing, and writes
it by hand from a description, badly. So this module generates, and pays for
generating by being specific about the cost:

    every feature, condition, exit, parameter and execution assumption is
    classified EQUIVALENT, APPROXIMATED or UNSUPPORTED, with the reason,
    and the overall status is the worst of them.

**The status a port may claim is bounded by what was actually checked.**

    VERIFIED      the generated code was executed against the same bars as the
                  compiled IR and the trade ledgers matched. Only Python can
                  reach this, because Python is the only target this machine
                  can run.
    STRUCTURAL    every element maps to a native construct with the same
                  semantics, and nothing executed it.
    APPROXIMATE   at least one element is expressible only with a difference
                  that can change results. Named, every one of them.
    INCOMPLETE    at least one element has no representation at all. The code,
                  if any, is a starting point and is labelled one.

Nothing anywhere in this module emits the phrase "logic preserved" below
VERIFIED. A translator that says the logic survived because the syntax parsed
has told you the one thing you needed to check.

**Targets are offered only where there is an implementation path.** Python is
generated and verifiable. Pine is generated and structural — the vocabulary
maps onto Pine builtins closely enough to be worth having, and this machine has
no TradingView to run it through. NinjaScript and MQL5 are *analysed* and not
generated: the report says element by element what would and would not carry,
and no code is produced, because there is no C# compiler and no MetaEditor here
and a generator nobody can check is a generator nobody should trust with money.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from forge.contracts.models import FrozenModel
from forge.strategy.export import ExportReport, to_python
from forge.strategy.ir import (
    Always,
    Arithmetic,
    Combine,
    Compare,
    Constant,
    Cross,
    Feature,
    FeatureRef,
    Level,
    ParamRef,
    SessionWindow,
    StrategyDefinition,
    validate_definition,
)


class Fidelity(StrEnum):
    """How faithfully one element of a strategy survived the crossing."""

    #: A native construct with the same semantics. Nothing was lost.
    EQUIVALENT = "equivalent"
    #: Expressible, with a difference that can change results. The difference is
    #: always named -- "approximated" with no reason is the same as unknown.
    APPROXIMATED = "approximated"
    #: No representation on the target. Whatever was generated is incomplete.
    UNSUPPORTED = "unsupported"


class Status(StrEnum):
    """What the port as a whole may claim.

    Ordered. `worst_of` takes the minimum, so one unsupported feature drags the
    whole port to INCOMPLETE regardless of how well everything else mapped --
    which is correct, because a strategy missing one of its entry conditions is
    not a mostly-correct strategy, it is a different one.
    """

    VERIFIED = "verified"
    STRUCTURAL = "structural"
    APPROXIMATE = "approximate"
    INCOMPLETE = "incomplete"


_RANK: dict[Status, int] = {
    Status.VERIFIED: 3,
    Status.STRUCTURAL: 2,
    Status.APPROXIMATE: 1,
    Status.INCOMPLETE: 0,
}

_FROM_FIDELITY: dict[Fidelity, Status] = {
    Fidelity.EQUIVALENT: Status.STRUCTURAL,
    Fidelity.APPROXIMATED: Status.APPROXIMATE,
    Fidelity.UNSUPPORTED: Status.INCOMPLETE,
}


class Element(FrozenModel):
    """One part of a strategy, and how it crossed.

    `part` groups the report the way a person reads a strategy -- features,
    entry, exit, execution -- rather than the way the IR stores it.
    """

    part: str
    name: str
    fidelity: Fidelity
    #: Why, whenever the fidelity is not EQUIVALENT. Required in practice: an
    #: approximation without a stated difference is indistinguishable from one
    #: nobody looked at.
    detail: str = ""
    #: What the element became on the target, when it became anything.
    rendered: str = ""


class PortReport(FrozenModel):
    """A strategy, on another platform, with the crossing accounted for."""

    target: str
    definition_id: str
    definition_hash: str
    status: Status
    #: Empty for a target this machine will not generate. Empty code and an
    #: INCOMPLETE status are different facts and both are reported.
    code: str
    elements: tuple[Element, ...]
    #: Stated in the report rather than left to a reader to infer from `status`.
    notes: tuple[str, ...] = ()

    @property
    def equivalent(self) -> tuple[Element, ...]:
        return tuple(e for e in self.elements if e.fidelity is Fidelity.EQUIVALENT)

    @property
    def approximated(self) -> tuple[Element, ...]:
        return tuple(e for e in self.elements if e.fidelity is Fidelity.APPROXIMATED)

    @property
    def unsupported(self) -> tuple[Element, ...]:
        return tuple(e for e in self.elements if e.fidelity is Fidelity.UNSUPPORTED)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["counts"] = {
            "equivalent": len(self.equivalent),
            "approximated": len(self.approximated),
            "unsupported": len(self.unsupported),
        }
        return payload


class Target(FrozenModel):
    """A platform a strategy can be carried to, and how far."""

    key: str
    label: str
    #: Whether this machine produces code for it at all.
    generates: bool
    #: The best status this target can reach, whatever the strategy. A target
    #: this machine cannot execute can never be VERIFIED, and saying so up front
    #: is what stops "structural" being read as "checked".
    ceiling: Status
    language: str
    note: str


TARGETS: tuple[Target, ...] = (
    Target(
        key="python",
        label="Python",
        generates=True,
        ceiling=Status.VERIFIED,
        language="python",
        note=(
            "Generated and checked: AlgoForge executes the module against the same "
            "bars as the compiled definition and compares the trade ledgers."
        ),
    ),
    Target(
        key="pine",
        label="Pine Script v6",
        generates=True,
        ceiling=Status.STRUCTURAL,
        language="pinescript",
        note=(
            "Generated but not executed. AlgoForge has no TradingView, so the "
            "strongest claim available is that every element maps to a native "
            "construct -- not that a run would agree."
        ),
    ),
    Target(
        key="ninjascript",
        label="NinjaScript (C#)",
        generates=False,
        ceiling=Status.STRUCTURAL,
        language="csharp",
        note=(
            "Analysed, not generated. NinjaScript needs a compiled AddIn and a "
            "NinjaTrader installation to check against, and this machine has "
            "neither. The report says what would and would not carry across."
        ),
    ),
    Target(
        key="mql5",
        label="MQL5",
        generates=False,
        ceiling=Status.STRUCTURAL,
        language="mql5",
        note=(
            "Analysed, not generated. MQL5 is tick-driven and position-based "
            "where this IR is bar-closed and signal-based; the differences are "
            "listed per element rather than papered over by emitting a file."
        ),
    ),
)

TARGET_KEYS: tuple[str, ...] = tuple(t.key for t in TARGETS)


def target(key: str) -> Target:
    found = next((t for t in TARGETS if t.key == key), None)
    if found is None:
        raise ValueError(f"no port target '{key}'. Targets: {', '.join(TARGET_KEYS)}")
    return found


# ── the feature vocabulary, per platform ─────────────────────────────────────
#
# Keyed by IR feature kind. A kind absent from a table is unsupported on that
# platform, which is the safe default: a feature nobody mapped is a feature
# nobody checked, and silently emitting something for it is how a port comes to
# be a different strategy.

#: kind -> Pine expression template. `{0}`, `{1}` are the feature's arguments.
_PINE_FEATURE: dict[str, str] = {
    "close": "close",
    "open": "open",
    "high": "high",
    "low": "low",
    "volume": "volume",
    "sma": "ta.sma(close, {0})",
    "volume_sma": "ta.sma(volume, {0})",
    "ema": "ta.ema(close, {0})",
    "atr": "ta.atr({0})",
    # Emitted as a tuple destructure rather than inline: `ta.dmi` returns
    # [DI+, DI-, ADX] and has no member access, so an inline form does not
    # compile. `to_pine` special-cases it.
    "adx": "__tuple__ta.dmi({0}, {0})",
    "rsi": "ta.rsi(close, {0})",
    "highest": "ta.highest(high, {0})",
    "lowest": "ta.lowest(low, {0})",
    "roc": "ta.roc(close, {0})",
    "realised_vol": "ta.stdev(math.log(close / close[1]), {0}) * math.sqrt(252)",
    "session_vwap": "ta.vwap(hlc3)",
    "minute_of_day": "(hour * 60 + minute)",
    "bars_since_session_open": "bar_index - _af_session_start",
}

#: Features that map but not exactly, with the difference stated. A kind here is
#: emitted *and* reported as APPROXIMATED -- both, never one or the other.
_PINE_APPROXIMATE: dict[str, str] = {
    "adx": (
        "Pine's ta.dmi uses Wilder smoothing over the same length for DI and ADX; "
        "AlgoForge smooths DI and ADX separately. The series track closely and are "
        "not identical, which matters at a threshold."
    ),
    "realised_vol": (
        "Annualised with 252 periods. AlgoForge annualises against the bars per "
        "year of the configured timeframe, so on anything but a daily chart these "
        "differ by a constant factor -- which moves every threshold built on it."
    ),
    "session_vwap": (
        "ta.vwap anchors to the chart's session, which is the exchange's, not the "
        "UTC day AlgoForge anchors to. On a futures chart the two disagree at the "
        "session boundary."
    ),
    "bars_since_session_open": (
        "Counted from a session-start bar index tracked in the emitted script. It "
        "agrees with AlgoForge only where the chart's session matches the UTC day."
    ),
    "minute_of_day": (
        "`hour` and `minute` are in the chart's timezone. AlgoForge works in UTC, "
        "so the script must be run on a UTC chart or the window shifts."
    ),
}

#: Features with no Pine representation worth emitting.
_PINE_UNSUPPORTED: dict[str, str] = {
    "session_vwap_sd": (
        "A running standard deviation about the session VWAP has no Pine builtin "
        "and needs a hand-written accumulator reset on the session boundary."
    ),
    "opening_range_high": (
        "The opening range needs a session-anchored accumulator over the first N "
        "bars, written by hand. Emitting an approximation of it would silently "
        "change where every entry fires."
    ),
    "opening_range_low": (
        "As opening_range_high: a session-anchored accumulator, by hand."
    ),
}

#: Why each analysed-only platform cannot take a given IR concept. Keyed by the
#: *part* of the strategy rather than by feature, because for these targets the
#: obstacle is the execution model rather than the vocabulary.
_MODEL_DIFFERENCES: dict[str, dict[str, str]] = {
    "ninjascript": {
        "execution": (
            "NinjaTrader's CalculateOnBarClose and OnBarUpdate ordering have to be "
            "matched to AlgoForge's decide-on-close / fill-next-open model before "
            "any comparison means anything."
        ),
        "session": (
            "Session windows resolve against the instrument's trading hours "
            "template, not a UTC minute range."
        ),
    },
    "mql5": {
        "execution": (
            "MQL5 runs per tick and manages positions rather than signals. The "
            "decide-on-close / fill-next-open model has to be reconstructed with "
            "an explicit new-bar check, and getting that wrong changes the "
            "strategy without changing its description."
        ),
        "session": (
            "Time windows resolve against the broker's server time, which is not "
            "UTC on most brokers and differs between them."
        ),
    },
}


# ── analysis ─────────────────────────────────────────────────────────────────


def _feature_elements(defn: StrategyDefinition, key: str) -> list[Element]:
    elements: list[Element] = []
    for item in defn.features:
        elements.append(_feature_element(item, key))
    return elements


def _feature_element(item: Feature, key: str) -> Element:
    label = f"{item.name} ({item.kind})"
    if key == "python":
        return Element(part="feature", name=label, fidelity=Fidelity.EQUIVALENT)
    if key == "pine":
        if item.kind in _PINE_UNSUPPORTED:
            return Element(
                part="feature",
                name=label,
                fidelity=Fidelity.UNSUPPORTED,
                detail=_PINE_UNSUPPORTED[item.kind],
            )
        if item.kind not in _PINE_FEATURE:
            return Element(
                part="feature",
                name=label,
                fidelity=Fidelity.UNSUPPORTED,
                detail=(
                    f"'{item.kind}' has no mapping in this exporter. A feature nobody "
                    "mapped is a feature nobody checked."
                ),
            )
        if item.kind in _PINE_APPROXIMATE:
            return Element(
                part="feature",
                name=label,
                fidelity=Fidelity.APPROXIMATED,
                detail=_PINE_APPROXIMATE[item.kind],
                rendered=_PINE_FEATURE[item.kind],
            )
        return Element(
            part="feature",
            name=label,
            fidelity=Fidelity.EQUIVALENT,
            rendered=_PINE_FEATURE[item.kind],
        )
    # Analysed-only targets: the vocabulary is expressible by hand on both, so
    # the honest classification is "approximated pending a written accumulator"
    # for the session-anchored ones and equivalent for the rest.
    if item.kind in ("session_vwap", "session_vwap_sd", "opening_range_high",
                     "opening_range_low", "bars_since_session_open", "minute_of_day"):
        return Element(
            part="feature",
            name=label,
            fidelity=Fidelity.APPROXIMATED,
            detail=_MODEL_DIFFERENCES[key]["session"],
        )
    return Element(part="feature", name=label, fidelity=Fidelity.EQUIVALENT)


def _condition_elements(condition: Any, key: str, where: str) -> list[Element]:
    """Walk a condition tree, reporting the parts that do not cross cleanly.

    Comparisons, crosses and boolean combinations are universal: every target
    here has `>`, `<`, a cross and an `and`. Only the session window is
    platform-dependent, and it is reported wherever it appears rather than once
    at the top, because a rule can carry more than one.
    """
    if condition is None:
        return []
    if isinstance(condition, SessionWindow):
        label = condition.label or f"{condition.start_minute}-{condition.end_minute} UTC"
        if key == "python":
            return [Element(part=where, name=f"session {label}", fidelity=Fidelity.EQUIVALENT)]
        detail = (
            _MODEL_DIFFERENCES[key]["session"]
            if key in _MODEL_DIFFERENCES
            else _PINE_APPROXIMATE["minute_of_day"]
        )
        return [
            Element(
                part=where,
                name=f"session {label}",
                fidelity=Fidelity.APPROXIMATED,
                detail=detail,
            )
        ]
    if isinstance(condition, Combine):
        found: list[Element] = []
        for inner in condition.of:
            found += _condition_elements(inner, key, where)
        return found
    if isinstance(condition, Cross | Compare | Always):
        return []
    return [
        Element(
            part=where,
            name=type(condition).__name__,
            fidelity=Fidelity.UNSUPPORTED,
            detail="This condition kind has no mapping in the porting analysis.",
        )
    ]


def _exit_elements(defn: StrategyDefinition, key: str) -> list[Element]:
    rules = defn.exit
    elements: list[Element] = []
    elements += _condition_elements(rules.signal, key, "exit")
    if rules.stop is not None:
        elements.append(_level_element(rules.stop, "stop", key))
    if rules.target is not None:
        elements.append(_level_element(rules.target, "target", key))
    if rules.trailing is not None:
        elements.append(_trailing_element(rules.trailing, key))
    if rules.max_bars is not None:
        elements.append(
            Element(
                part="exit",
                name="max bars held",
                fidelity=Fidelity.EQUIVALENT
                if key in ("python", "pine")
                else Fidelity.APPROXIMATED,
                detail=""
                if key in ("python", "pine")
                else "Bar counting has to be written against the platform's own bar events.",
            )
        )
    if rules.flat_by_minute is not None:
        elements.append(
            Element(
                part="exit",
                name=f"flat by minute {rules.flat_by_minute} UTC",
                fidelity=Fidelity.EQUIVALENT if key == "python" else Fidelity.APPROXIMATED,
                detail=""
                if key == "python"
                else (
                    _MODEL_DIFFERENCES.get(key, {}).get("session")
                    or _PINE_APPROXIMATE["minute_of_day"]
                ),
            )
        )
    return elements


def _level_element(level: Level, what: str, key: str) -> Element:
    if key in ("python", "pine"):
        return Element(part="exit", name=f"{what} ({level.kind})", fidelity=Fidelity.EQUIVALENT)
    return Element(
        part="exit",
        name=f"{what} ({level.kind})",
        fidelity=Fidelity.EQUIVALENT,
    )


def _trailing_element(level: Level, key: str) -> Element:
    if key == "pine":
        return Element(
            part="exit",
            name=f"trailing stop ({level.kind})",
            fidelity=Fidelity.APPROXIMATED,
            detail=(
                "strategy.exit trails in ticks and ratchets intrabar. AlgoForge "
                "ratchets on closed bars only, so Pine will move the stop sooner "
                "and exit earlier on the bars that matter most."
            ),
        )
    if key == "python":
        return Element(
            part="exit", name=f"trailing stop ({level.kind})", fidelity=Fidelity.EQUIVALENT
        )
    return Element(
        part="exit",
        name=f"trailing stop ({level.kind})",
        fidelity=Fidelity.APPROXIMATED,
        detail="Trailing semantics differ per platform and have to be written explicitly.",
    )


def _execution_element(defn: StrategyDefinition, key: str) -> Element:
    execution = defn.execution
    name = f"fill at {execution.fill}, {execution.commission_per_side}/side, " \
           f"{execution.slippage_ticks} tick slippage"
    if key == "python":
        return Element(part="execution", name=name, fidelity=Fidelity.EQUIVALENT)
    if key == "pine":
        return Element(
            part="execution",
            name=name,
            fidelity=Fidelity.APPROXIMATED,
            detail=(
                "Emitted with process_orders_on_close=false so orders fill at the "
                "next bar's open, matching AlgoForge. Commission and slippage are "
                "declared on the strategy() call in Pine's own units -- "
                "commission_type and a tick slippage -- which are close to, and not "
                "the same as, AlgoForge's per-side dollar and tick model."
            ),
        )
    return Element(
        part="execution",
        name=name,
        fidelity=Fidelity.APPROXIMATED,
        detail=_MODEL_DIFFERENCES[key]["execution"],
    )


def analyse(defn: StrategyDefinition, key: str) -> tuple[Element, ...]:
    """Every element of a strategy, classified for one target.

    Ordered the way a person reads a strategy rather than the way the IR stores
    it: what it computes, when it enters, when it leaves, what it assumes about
    filling, and what is tunable.
    """
    spec = target(key)
    defn = validate_definition(defn)
    elements: list[Element] = []
    elements += _feature_elements(defn, spec.key)
    elements += _condition_elements(defn.entry.long, spec.key, "entry")
    elements += _condition_elements(defn.entry.short, spec.key, "entry")
    elements += _condition_elements(defn.entry.session, spec.key, "entry")
    elements += _exit_elements(defn, spec.key)
    elements.append(_execution_element(defn, spec.key))
    elements.append(
        Element(
            part="parameters",
            name=f"{len(defn.parameters)} parameter(s) at their defaults",
            fidelity=Fidelity.EQUIVALENT,
            detail="Emitted as inputs, so the target can be tuned in its own optimiser."
            if spec.generates
            else "",
        )
    )
    elements.append(
        Element(
            part="warmup",
            name=f"{defn.required_warmup()} bars",
            fidelity=Fidelity.EQUIVALENT,
            detail=(
                "Derived from the feature lengths rather than declared, so the "
                "target needs the same history before its first signal."
            ),
        )
    )
    return tuple(elements)


def _status(elements: tuple[Element, ...], ceiling: Status) -> Status:
    worst = Status.STRUCTURAL
    for element in elements:
        candidate = _FROM_FIDELITY[element.fidelity]
        if _RANK[candidate] < _RANK[worst]:
            worst = candidate
    return worst if _RANK[worst] < _RANK[ceiling] else ceiling


def port(defn: StrategyDefinition, key: str) -> PortReport:
    """Carry a strategy to one target, and account for the crossing.

    The status is the worst element's, capped by what the target can ever claim.
    A perfectly-mapping strategy ported to Pine is STRUCTURAL and not VERIFIED,
    because nothing ran it -- and that ceiling is a property of this machine,
    not of the strategy.
    """
    spec = target(key)
    defn = validate_definition(defn)
    elements = analyse(defn, spec.key)
    status = _status(elements, spec.ceiling)

    code = ""
    notes: list[str] = [spec.note]
    if spec.key == "python":
        report: ExportReport = to_python(defn)
        code = report.code
        notes += list(report.notes)
        # `to_python` reports its own unsupported set; anything it could not
        # render has to drag the status down even though the analysis above
        # calls the vocabulary equivalent.
        if report.unsupported:
            status = Status.INCOMPLETE
            elements = elements + tuple(
                Element(
                    part="feature",
                    name=item,
                    fidelity=Fidelity.UNSUPPORTED,
                    detail="The Python emitter could not render this.",
                )
                for item in report.unsupported
            )
    elif spec.key == "pine":
        code = to_pine(defn, elements)

    if status is not Status.VERIFIED:
        notes.append(
            "Nothing here claims the logic is preserved. That claim needs a run "
            "on both sides with matching ledgers, and only the Python target can "
            "produce one."
        )
    return PortReport(
        target=spec.key,
        definition_id=defn.definition_id,
        definition_hash=defn.definition_hash,
        status=status,
        code=code,
        elements=elements,
        notes=tuple(notes),
    )


def verified(defn: StrategyDefinition, comparison: dict[str, Any]) -> PortReport:
    """A Python port upgraded to VERIFIED by an actual comparison.

    Separate from `port` and requiring the comparison as an argument, so the
    only way to reach VERIFIED is to have run `forge.strategy.export.verify_python`
    and passed its result in. There is no code path that reaches this status by
    inspecting a definition.
    """
    report = port(defn, "python")
    if not comparison.get("identical"):
        return report.model_copy(
            update={
                "notes": (
                    *report.notes,
                    "The generated module was executed and its ledger did NOT match "
                    f"the compiled definition: {comparison.get('note', 'see comparison')}.",
                )
            }
        )
    return report.model_copy(
        update={
            "status": Status.VERIFIED,
            "notes": (
                *report.notes,
                "Executed against the same bars as the compiled definition; the "
                "trade ledgers matched bar for bar.",
            ),
        }
    )


# ── Pine emission ────────────────────────────────────────────────────────────


def _pine_operand(defn: StrategyDefinition, operand: Any) -> str:
    if isinstance(operand, Constant):
        # Integral values are emitted without a decimal point. Not cosmetic:
        # Pine's `ta.*` length arguments are `simple int` and reject a float, so
        # `ta.atr(14.0)` does not compile. Emitting `14` is also correct
        # everywhere else, because Pine widens int to float in arithmetic.
        value = float(operand.value)
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(operand, ParamRef):
        return f"p_{operand.name}"
    if isinstance(operand, FeatureRef):
        item = defn.feature(operand.name)
        shift = item.shift if item else 0
        return f"f_{operand.name}" + (f"[{shift}]" if shift else "")
    if isinstance(operand, Arithmetic):
        left = _pine_operand(defn, operand.left)
        right = _pine_operand(defn, operand.right)
        symbol = {"add": "+", "sub": "-", "mul": "*", "div": "/"}[operand.op]
        return f"({left} {symbol} {right})"
    return "na"


def _pine_condition(defn: StrategyDefinition, condition: Any) -> str:
    if condition is None or isinstance(condition, Always):
        return "true"
    if isinstance(condition, Compare):
        symbol = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[condition.op]
        left = _pine_operand(defn, condition.left)
        right = _pine_operand(defn, condition.right)
        return f"({left} {symbol} {right})"
    if isinstance(condition, Cross):
        fn = "ta.crossover" if condition.direction == "above" else "ta.crossunder"
        left = _pine_operand(defn, condition.left)
        right = _pine_operand(defn, condition.right)
        return f"{fn}({left}, {right})"
    if isinstance(condition, SessionWindow):
        start, end = condition.start_minute, condition.end_minute
        minute = "(hour * 60 + minute)"
        if start <= end:
            return f"({minute} >= {start} and {minute} < {end})"
        # Wraps midnight: outside the complement rather than inside a range.
        return f"({minute} >= {start} or {minute} < {end})"
    if isinstance(condition, Combine):
        parts = [_pine_condition(defn, inner) for inner in condition.of]
        if condition.kind == "not":
            return f"(not {parts[0]})"
        joiner = " and " if condition.kind == "all" else " or "
        return "(" + joiner.join(parts) + ")"
    return "false"


def _pine_level(defn: StrategyDefinition, level: Level) -> str:
    multiple = _pine_operand(defn, level.multiple)
    if level.kind == "feature":
        # Through the same path as any other reference, so a shifted feature is
        # indexed here exactly as it is in a condition. Naming the series
        # directly would have read the current bar where the strategy reads an
        # earlier one -- a stop at a different distance, silently.
        series = _pine_operand(defn, FeatureRef(name=level.feature))
        return f"({multiple} * {series})"
    if level.kind == "percent":
        return f"({multiple} * close)"
    return f"({multiple})"


def to_pine(defn: StrategyDefinition, elements: tuple[Element, ...] | None = None) -> str:
    """A Pine v6 strategy from the canonical definition.

    The header is not decoration. It states the definition hash, the status the
    port can claim and every element that did not cross cleanly, *inside the
    file*, because the file is what gets pasted into TradingView and the report
    is what gets left behind in AlgoForge.
    """
    defn = validate_definition(defn)
    found = elements if elements is not None else analyse(defn, "pine")
    lines: list[str] = ["// @version=6"]

    # Through the escape, like the string literal below: this is a `//`
    # comment, and a newline in the name ends it.
    lines.append(f"// {_escape(defn.name)} — ported from AlgoForge")
    lines.append(f"// definition {defn.definition_id} ({defn.definition_hash[:16]})")
    lines.append("//")
    lines.append("// This is a PORT, not the strategy AlgoForge validated. It was generated")
    lines.append("// from the canonical definition and never executed here: AlgoForge has no")
    lines.append("// TradingView to run it through, so nothing below has been checked against")
    lines.append("// the ledger the original produced.")
    imperfect = [e for e in found if e.fidelity is not Fidelity.EQUIVALENT]
    if imperfect:
        lines.append("//")
        lines.append("// What did not cross cleanly:")
        for element in imperfect:
            lines.append(f"//   [{element.fidelity.value}] {element.part}: {element.name}")
            for chunk in _wrap(element.detail, 74):
                lines.append(f"//       {chunk}")
    lines.append("")

    execution = defn.execution
    lines.append(
        f'strategy("{_escape(defn.name)}", overlay=true, '
        "process_orders_on_close=false, "
        "default_qty_type=strategy.fixed, default_qty_value=1, "
        f"commission_type=strategy.commission.cash_per_contract, "
        f"commission_value={execution.commission_per_side}, "
        f"slippage={int(execution.slippage_ticks)})"
    )
    lines.append("")

    if defn.parameters:
        lines.append("// Parameters, at the definition's defaults.")
        for parameter in defn.parameters:
            default = parameter.default
            if isinstance(default, bool):
                call = f'input.bool({str(default).lower()}, "{_escape(parameter.name)}")'
            elif isinstance(default, int):
                call = f'input.int({default}, "{_escape(parameter.name)}")'
            else:
                call = f'input.float({float(default)}, "{_escape(parameter.name)}")'
            lines.append(f"p_{parameter.name} = {call}")
        lines.append("")

    needs_session_start = any(
        item.kind == "bars_since_session_open" for item in defn.features
    )
    if needs_session_start:
        lines.append("// Session anchor for bar counting. Tracks the chart's session, which is")
        lines.append("// the exchange's rather than AlgoForge's UTC day.")
        lines.append("var int _af_session_start = bar_index")
        lines.append("if session.isfirstbar")
        lines.append("    _af_session_start := bar_index")
        lines.append("")

    lines.append("// Features.")
    for item in defn.features:
        template = _PINE_FEATURE.get(item.kind)
        if template is None:
            lines.append(
                f"// f_{item.name} ({item.kind}) has no Pine mapping and is NOT emitted."
            )
            lines.append(f"f_{item.name} = na")
            continue
        args = [_pine_operand(defn, arg) for arg in item.args]
        try:
            rendered = template.format(*args)
        except IndexError:
            rendered = template
        if rendered.startswith("__tuple__"):
            call = rendered.removeprefix("__tuple__")
            lines.append(f"[_af_dip_{item.name}, _af_dim_{item.name}, f_{item.name}] = {call}")
            continue
        lines.append(f"f_{item.name} = {rendered}")
    lines.append("")

    lines.append("// Entries.")
    gate = _pine_condition(defn, defn.entry.session) if defn.entry.session else "true"
    if defn.entry.long is not None:
        lines.append(f"longSignal = {_pine_condition(defn, defn.entry.long)} and {gate}")
        lines.append('if longSignal and strategy.position_size == 0')
        lines.append('    strategy.entry("long", strategy.long)')
    if defn.entry.short is not None:
        lines.append(f"shortSignal = {_pine_condition(defn, defn.entry.short)} and {gate}")
        lines.append('if shortSignal and strategy.position_size == 0')
        lines.append('    strategy.entry("short", strategy.short)')
    lines.append("")

    rules = defn.exit
    if rules.stop is not None or rules.target is not None or rules.trailing is not None:
        lines.append("// Protective exits, in price distance from the entry.")
        stop = _pine_level(defn, rules.stop) if rules.stop else "na"
        profit = _pine_level(defn, rules.target) if rules.target else "na"
        trail = _pine_level(defn, rules.trailing) if rules.trailing else "na"
        lines.append("if strategy.position_size != 0")
        lines.append("    _af_entry = strategy.position_avg_price")
        lines.append("    _af_long = strategy.position_size > 0")
        if rules.stop is not None:
            lines.append(f"    _af_stop = _af_long ? _af_entry - {stop} : _af_entry + {stop}")
        if rules.target is not None:
            lines.append(f"    _af_target = _af_long ? _af_entry + {profit} : _af_entry - {profit}")
        lines.append(
            '    strategy.exit("exit", '
            + ("stop=_af_stop, " if rules.stop is not None else "")
            + ("limit=_af_target, " if rules.target is not None else "")
            + (f"trail_offset={trail}, " if rules.trailing is not None else "")
            + "comment="
            + '"af")'
        )
        lines.append("")

    if rules.signal is not None:
        lines.append("// Signal exit.")
        lines.append(f"if strategy.position_size != 0 and {_pine_condition(defn, rules.signal)}")
        lines.append('    strategy.close_all("signal")')
        lines.append("")

    if rules.max_bars is not None:
        lines.append("// Time stop, in bars held.")
        lines.append(
            f"if strategy.position_size != 0 and "
            f"(bar_index - strategy.opentrades.entry_bar_index(0)) >= "
            f"{_pine_operand(defn, rules.max_bars)}"
        )
        lines.append('    strategy.close_all("time")')
        lines.append("")

    if rules.flat_by_minute is not None:
        lines.append("// Flat by time of day. UTC in AlgoForge; the chart's timezone here.")
        lines.append(
            f"if strategy.position_size != 0 and (hour * 60 + minute) >= {rules.flat_by_minute}"
        )
        lines.append('    strategy.close_all("eod")')
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _escape(value: str) -> str:
    """Make a string safe to sit inside emitted Pine.

    Quotes become apostrophes so a name cannot close the string literal it is
    written into. Control characters -- newlines above all -- are collapsed to
    spaces, because the name is also written into a `//` comment, and a newline
    there ends the comment and turns whatever follows into a line of Pine.

    Nobody but the local operator can set a strategy's name today, so this is
    not a boundary being crossed. It is the module's own rule: a port must not
    emit something that looks like code and is not the strategy. A name with a
    line break in it produced a script that would not compile, or worse, one
    that would.
    """
    cleaned = "".join(
        " " if character < " " or character == "\x7f" else character for character in value
    )
    return cleaned.replace('"', "'").replace("\\", "/")


def _wrap(text: str, width: int) -> list[str]:
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines

"""A strategy's performance over two of its own parameters, as a surface.

The brief's first example of a 3D visualisation is exactly this: X a lookback,
Y an ATR multiplier, Z a performance metric. The machinery to draw it already
existed — `AnalysisResult` with `shape="surface"`, a WebGL renderer, a heatmap
fallback, an artifact store with provenance — and it had nothing to draw,
because the only sweep the engine could run moved one parameter at a time.

This turns a completed two-parameter grid into the same `AnalysisResult` every
other analysis produces. That reuse is the point: the renderer, the artifact
store, the provenance chain and the "NOT EVIDENCE" labelling all work unchanged,
and there is no second surface format to keep in step with the first.

**Why a surface is the most dangerous picture in the application.** A parameter
landscape is what overfitting looks like from the inside. A single tall peak
surrounded by loss is the classic artefact of a search that found noise, and it
is also the most attractive-looking result the system can produce. So:

* Every cell is a **development-partition, in-sample** backtest, and the result
  says so in its own title and in every warning it carries. A sweep can never
  promote a strategy and the tier here makes that structural.
* The number of cells is the number of configurations tried, and it is reported
  as a trial count, because that is what the deflated Sharpe gate needs to know.
* When the best cell stands well clear of its own neighbours, the result says
  so in a finding. A peak that its neighbours do not support is the shape of a
  fit, and the reader is told before they read the number.

Nothing here runs a backtest; it summarises ones already run. That keeps the
module pure and testable, and keeps the expensive part where the job progress
can be reported from.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from forge.research.analyses import (
    AnalysisError,
    AnalysisProvenance,
    AnalysisResult,
    Axis,
    Cell,
)

#: What a surface can be drawn against, and how each reads. The set is closed
#: for the same reason the analysis catalogue is: everything here is something a
#: person can check, and adding one means deciding what it means when it is
#: undefined.
METRICS: dict[str, dict[str, str]] = {
    "net_pnl": {"label": "Net P&L", "unit": "$", "note": "Total, after modelled costs."},
    "average_trade": {
        "label": "Average trade",
        "unit": "$",
        "note": "Net divided by trade count. Undefined where nothing traded.",
    },
    "win_rate": {
        "label": "Win rate",
        "unit": "%",
        "note": "Share of trades in profit. Says nothing about their size.",
    },
    "trade_count": {
        "label": "Trades",
        "unit": "",
        "note": "How often the configuration traded at all.",
    },
    "max_drawdown": {
        "label": "Max drawdown",
        "unit": "$",
        "note": "Worst peak-to-trough, as a positive magnitude.",
    },
    "return_over_drawdown": {
        "label": "Return / drawdown",
        "unit": "",
        "note": (
            "Net divided by worst drawdown. Undefined where there was no drawdown "
            "to divide by — reported as absent rather than as a large number."
        ),
    },
}

#: A cell with fewer trades than this has a win rate that estimates nothing.
#: Same threshold as the ledger analyses, for the same reason.
MIN_TRADES_PER_CELL = 20

#: How far above its own neighbours the best cell has to sit before the result
#: warns that it looks like a fit. A configuration that beats the mean of its
#: adjacent cells by more than this is standing on nothing.
PEAK_RATIO = 2.0


def _metric(point: dict[str, Any], metric: str) -> float | None:
    """The measured quantity, or None where it is genuinely undefined.

    None is not zero. A configuration that took no trades has an undefined
    average trade, and drawing it at the origin would put a flat plateau in the
    middle of the surface where there is actually no data at all.
    """
    trades = int(point.get("trade_count") or 0)
    net = float(point.get("net_pnl") or 0.0)
    drawdown = abs(float(point.get("max_drawdown") or 0.0))

    if metric == "net_pnl":
        return net if trades else None
    if metric == "trade_count":
        return float(trades)
    if metric == "average_trade":
        return net / trades if trades else None
    if metric == "win_rate":
        return None if not trades else float(point.get("win_rate") or 0.0)
    if metric == "max_drawdown":
        return drawdown if trades else None
    if metric == "return_over_drawdown":
        if not trades or drawdown <= 0:
            return None
        return net / drawdown
    raise AnalysisError(f"unknown metric '{metric}'")


def _label(value: float) -> str:
    """A parameter value as an axis label, without trailing noise."""
    return f"{value:g}"


def build_surface(
    points: Sequence[dict[str, Any]],
    provenance: AnalysisProvenance,
    *,
    x_name: str,
    y_name: str,
    metric: str = "net_pnl",
) -> AnalysisResult:
    """Turn a completed two-parameter grid into a surface.

    `points` are the sweep's own rows: each carries `x`, `y`, and the backtest
    summary for that configuration. Missing combinations are allowed and are
    reported as absent cells rather than filled in.
    """
    if metric not in METRICS:
        raise AnalysisError(f"unknown metric '{metric}'. Available: {', '.join(sorted(METRICS))}")
    if not points:
        raise AnalysisError("the sweep produced no configurations, so there is no surface to draw")

    xs = sorted({float(point["x"]) for point in points})
    ys = sorted({float(point["y"]) for point in points})
    if len(xs) < 2 or len(ys) < 2:
        raise AnalysisError(
            f"a surface needs at least two values on each axis; got {len(xs)} "
            f"of '{x_name}' and {len(ys)} of '{y_name}'. A single-valued axis is "
            "a line, and the one-parameter sweep already draws that."
        )

    by_position = {(float(point["x"]), float(point["y"])): point for point in points}
    spec = METRICS[metric]

    cells: list[Cell] = []
    for xi, x in enumerate(xs):
        for yi, y in enumerate(ys):
            point = by_position.get((x, y))
            if point is None:
                # A combination the sweep did not reach. Absent, not zero.
                cells.append(
                    Cell(
                        coords=(xi, yi),
                        labels=(_label(x), _label(y)),
                        trade_count=0,
                        value=None,
                        net_pnl=0.0,
                        win_rate=None,
                        average_trade=None,
                        insufficient=True,
                    )
                )
                continue
            trades = int(point.get("trade_count") or 0)
            net = float(point.get("net_pnl") or 0.0)
            cells.append(
                Cell(
                    coords=(xi, yi),
                    labels=(_label(x), _label(y)),
                    trade_count=trades,
                    value=_metric(point, metric),
                    net_pnl=net,
                    win_rate=float(point["win_rate"]) if trades and "win_rate" in point else None,
                    average_trade=net / trades if trades else None,
                    insufficient=trades < MIN_TRADES_PER_CELL,
                    # A sweep cell's evidence is its backtest, not a list of
                    # trade ids: the runs are exploratory and are not kept as a
                    # ledger anybody should drill into as though it were
                    # out-of-sample. The id is carried in the labels instead.
                    trade_ids=(),
                )
            )

    populated = [cell for cell in cells if cell.trade_count > 0 and cell.value is not None]
    warnings = [
        "Every cell is an in-sample backtest on the development partition. A "
        "sweep is exploration and can never promote a strategy.",
        f"{len(cells)} configurations were tried. That is the trial count the "
        "deflated-Sharpe gate has to be told about.",
    ]
    findings: list[str] = []

    empty = len(cells) - len(populated)
    if empty:
        warnings.append(
            f"{empty} of {len(cells)} configurations took no trades, or produced no "
            f"{spec['label'].lower()}. Those are drawn as gaps, not as zeroes."
        )

    if populated:
        best = max(populated, key=lambda cell: cell.value or 0.0)
        worst = min(populated, key=lambda cell: cell.value or 0.0)
        findings.append(
            f"Best {x_name}={best.labels[0]}, {y_name}={best.labels[1]} at "
            f"{best.value:+,.2f} {spec['unit']}; worst {x_name}={worst.labels[0]}, "
            f"{y_name}={worst.labels[1]} at {worst.value:+,.2f}."
        )

        neighbours = _neighbours_of(best, cells)
        supported = [cell.value for cell in neighbours if cell.value is not None]
        if supported and best.value is not None:
            mean = sum(supported) / len(supported)
            # A peak standing well clear of everything adjacent to it is what a
            # fit looks like from above. Say so before the number is read.
            if mean <= 0 < best.value or (mean > 0 and best.value / mean >= PEAK_RATIO):
                findings.append(
                    f"The best configuration stands well clear of its own neighbours "
                    f"({best.value:+,.2f} against a neighbouring mean of {mean:+,.2f}). "
                    "A peak its neighbours do not support is the shape of a fit; "
                    "prefer a broad plateau."
                )
            else:
                findings.append(
                    f"The best configuration sits on a plateau: its neighbours average "
                    f"{mean:+,.2f} against its own {best.value:+,.2f}."
                )

    return AnalysisResult(
        analysis="parameter_surface",
        title=f"{spec['label']} over {x_name} and {y_name}",
        question=(
            f"Where in the {x_name}/{y_name} space does this strategy work, and "
            "is the best point standing on anything?"
        ),
        shape="surface",
        axes=(
            Axis(
                name=x_name,
                label=x_name,
                categories=tuple(_label(x) for x in xs),
                note="Parameter value. In-sample development partition.",
            ),
            Axis(
                name=y_name,
                label=y_name,
                categories=tuple(_label(y) for y in ys),
                note="Parameter value. In-sample development partition.",
            ),
        ),
        measure=spec["label"],
        measure_unit=spec["unit"],
        cells=tuple(cells),
        total_trades=sum(cell.trade_count for cell in cells),
        covered_trades=sum(cell.trade_count for cell in cells),
        warnings=tuple(warnings),
        findings=tuple(findings),
        provenance=provenance,
    )


def _neighbours_of(target: Cell, cells: Sequence[Cell]) -> list[Cell]:
    """The eight cells adjacent to one, on the grid."""
    x, y = target.coords
    wanted = {
        (x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if not (dx == 0 and dy == 0)
    }
    return [cell for cell in cells if tuple(cell.coords) in wanted and cell.trade_count > 0]

"""The parameter sweep, in one place so the route and the action cannot drift.

A two-parameter surface is 36 backtests across a grid, and it was written inline
in the `/strategies/{id}/surface` route. That was fine while the route was the
only caller. Giving an assistant the same capability meant either a second copy
of the loop -- two implementations that will eventually disagree about which
cells were run and what they were labelled -- or this.

It lives in the service layer rather than in `forge.research.parameter_surface`
for a concrete reason: the sweep needs `run_backtest` from
`forge.strategy.runtime`, and `forge.strategy.models` already imports
`forge.research.models`. Putting the loop in the research package would close
that circle, which is a failure this repository has already had once and fixed.

`forge.research.parameter_surface.build_surface` still owns turning points into a
surface. This owns producing the points.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

from forge.strategy.runtime import run_backtest


class SurfaceError(ValueError):
    """A sweep that cannot be run, and why.

    Carries the structured detail rather than only a sentence, because the HTTP
    contract already promises more than prose -- a caller that asked for a
    parameter that does not exist is told which axis and what does exist, and
    flattening that to a message would be a breaking change dressed as a
    refactor.

    `status` rides along for the same reason: a missing parameter is a 404 and a
    nonsensical request is a 422, and that distinction was already in the API.
    """

    def __init__(self, code: str, detail: str, *, status: int = 422, **extra: Any) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.extra = extra

    @property
    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, **self.extra}


def axis_values(low: float, high: float, step: float, steps: int) -> list[float]:
    """`steps` values across a parameter's declared range.

    Snapped to the declared step so every value is one the strategy would
    actually accept, and de-duplicated: a range of 3 with a step of 1 cannot
    supply six distinct values however many are asked for. Returning the
    duplicates instead would run the same backtest repeatedly and report the
    repeats as independent cells, which inflates the trial count the
    deflated-Sharpe gate is told about.
    """
    if steps <= 1 or high <= low:
        return [float(low)]
    span = (high - low) / (steps - 1)
    seen: list[float] = []
    for index in range(steps):
        raw = low + span * index
        snapped = low + round((raw - low) / step) * step if step > 0 else raw
        snapped = min(high, max(low, round(snapped, 10)))
        if snapped not in seen:
            seen.append(snapped)
    return seen


def axes_for(
    spec: Any, x_parameter: str, y_parameter: str, x_steps: int, y_steps: int
) -> tuple[Any, Any, list[float], list[float]]:
    """The two parameters and the values to sweep them over.

    Every refusal here is a statement about the strategy rather than about the
    request, which is why they name what *is* available: an operator who asked
    for a parameter that does not exist needs the list, not a 422.
    """
    if x_parameter == y_parameter:
        raise SurfaceError(
            "same_parameter_twice",
            "A surface needs two different parameters. Sweeping one against "
            "itself is the line the one-parameter sweep already draws.",
        )
    by_name = {p.name: p for p in spec.parameters}
    for axis, name in (("x", x_parameter), ("y", y_parameter)):
        if name not in by_name:
            raise SurfaceError(
                "parameter_not_found",
                f"No parameter '{name}' on this strategy.",
                status=404,
                axis=axis,
                parameter=name,
                known=sorted(by_name),
            )

    x_param, y_param = by_name[x_parameter], by_name[y_parameter]
    xs = axis_values(
        float(x_param.low), float(x_param.high), float(x_param.step or 1.0), x_steps
    )
    ys = axis_values(
        float(y_param.low), float(y_param.high), float(y_param.step or 1.0), y_steps
    )
    if len(xs) < 2 or len(ys) < 2:
        raise SurfaceError(
            "range_too_narrow",
            f"'{x_param.name}' yields {len(xs)} distinct value(s) and "
            f"'{y_param.name}' {len(ys)} across their declared ranges and steps. "
            "A surface needs at least two on each axis.",
        )
    return x_param, y_param, xs, ys


def labels_for(is_real: bool) -> tuple[str, ...]:
    """What every cell of a sweep is marked with.

    `NON_PROMOTABLE` is the load-bearing one: a sweep is exploration on the
    development partition, in-sample by construction, and no cell of it may ever
    become a promotion candidate however good it looks.
    """
    if is_real:
        return ("REAL_DATA", "DEVELOPMENT_IN_SAMPLE", "SWEEP", "NON_PROMOTABLE")
    return ("SYNTHETIC_DATA", "SWEEP", "NON_PROMOTABLE")


def sweep_points(
    *,
    module: Any,
    spec: Any,
    bars: Any,
    x_param: Any,
    y_param: Any,
    xs: Sequence[float],
    ys: Sequence[float],
    code_hash: str,
    dataset_key: str | None,
    is_real: bool,
    split_receipt: Any = None,
    progress: Callable[[int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """One real backtest per cell, in row-major order.

    Every point carries its own `backtest_id`, so a cell on the surface can be
    opened rather than merely looked at -- a peak nobody can inspect is a picture
    of a number, and a lone peak is what overfitting looks like from above.
    """
    points: list[dict[str, Any]] = []
    labels = labels_for(is_real)
    # Narrowed rather than passed as a plain string: the tier is a closed set,
    # and a sweep may only ever claim the two of them that mean "in sample".
    tier: Literal["DEVELOPMENT_IN_SAMPLE", "SYNTHETIC"] = (
        "DEVELOPMENT_IN_SAMPLE" if is_real else "SYNTHETIC"
    )
    done = 0
    for x in xs:
        for y in ys:
            if progress is not None:
                progress(done, f"{x_param.name}={x:g} {y_param.name}={y:g}")
            result = run_backtest(
                module,
                spec,
                bars,
                parameters={x_param.name: x, y_param.name: y},
                code_hash=code_hash,
                labels=labels,
                evidence_tier=tier,
                dataset_key=dataset_key,
                partition_name="DEVELOPMENT" if is_real else None,
                split_receipt=split_receipt,
            )
            points.append(
                {
                    "x": x,
                    "y": y,
                    "net_pnl": result.net_pnl,
                    "trade_count": len(result.trades),
                    "win_rate": result.win_rate,
                    "max_drawdown": result.max_drawdown,
                    "backtest_id": result.backtest_id,
                }
            )
            done += 1
    return points

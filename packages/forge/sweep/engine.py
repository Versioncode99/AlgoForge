from __future__ import annotations

from itertools import product

import numpy as np
from pydantic import BaseModel, ConfigDict

from forge.contracts.hashing import stable_id


class Trial(BaseModel):
    model_config = ConfigDict(frozen=True)
    trial_id: str
    parameters: dict[str, float]
    net_pnl: float


class SweepResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    trials: tuple[Trial, ...]
    best_trial_id: str
    tier: str = "SWEEP"
    promotable: bool = False


class ArraySweepEngine:
    def run(
        self, returns: np.ndarray, parameter_grid: dict[str, list[float]], cost_per_trade: float
    ) -> SweepResult:
        names = sorted(parameter_grid)
        trials: list[Trial] = []
        for values in product(*(parameter_grid[name] for name in names)):
            parameters = dict(zip(names, values, strict=True))
            threshold = parameters.get("threshold", 0.0)
            size = parameters.get("size", 1.0)
            active = np.where(np.abs(returns) >= threshold, returns * size, 0.0)
            trades = int(np.count_nonzero(active))
            pnl = float(active.sum() - trades * cost_per_trade)
            payload = {"parameters": parameters, "pnl": round(pnl, 12)}
            trials.append(
                Trial(trial_id=stable_id("trial", payload), parameters=parameters, net_pnl=pnl)
            )
        if not trials:
            raise ValueError("parameter grid produced no trials")
        best = max(trials, key=lambda trial: trial.net_pnl)
        return SweepResult(trials=tuple(trials), best_trial_id=best.trial_id)

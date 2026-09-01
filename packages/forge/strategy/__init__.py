from forge.strategy.guard import GuardViolation, assert_safe, check_source
from forge.strategy.library import StrategyLibrary, slugify
from forge.strategy.models import BacktestResult, ParameterSpec, StrategySpec, Trade
from forge.strategy.runtime import LookaheadError, Position, Window, run_backtest
from forge.strategy.synthetic import generate_bars
from forge.strategy.templates import TEMPLATES, Template
from forge.strategy.templates_quant import QUANT_TEMPLATES

# Session-aware families are registered into the same dict every consumer holds.
TEMPLATES.update(QUANT_TEMPLATES)

__all__ = [
    "TEMPLATES",
    "BacktestResult",
    "GuardViolation",
    "LookaheadError",
    "ParameterSpec",
    "Position",
    "StrategyLibrary",
    "StrategySpec",
    "Template",
    "Trade",
    "Window",
    "assert_safe",
    "check_source",
    "generate_bars",
    "run_backtest",
    "slugify",
]

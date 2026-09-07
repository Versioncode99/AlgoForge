from forge.strategy.authoring import TemplateRejected, TemplateStore
from forge.strategy.catalog import StrategyCapability, strategy_capability_catalog
from forge.strategy.conformance import ConformanceCase, ConformanceReport, run_conformance
from forge.strategy.families import BUILTIN_FAMILIES, Family, FamilyRegistry
from forge.strategy.guard import GuardViolation, assert_safe, check_source, check_test_source
from forge.strategy.library import StrategyLibrary, slugify
from forge.strategy.models import BacktestResult, ParameterSpec, StrategySpec, Trade
from forge.strategy.runtime import LookaheadError, Position, Window, run_backtest
from forge.strategy.synthetic import generate_bars
from forge.strategy.templates import TEMPLATES, Template
from forge.strategy.templates_quant import QUANT_TEMPLATES
from forge.strategy.templates_statistical import STATISTICAL_TEMPLATES

# Session-aware families are registered into the same dict every consumer holds.
TEMPLATES.update(QUANT_TEMPLATES)
TEMPLATES.update(STATISTICAL_TEMPLATES)

__all__ = [
    "BUILTIN_FAMILIES",
    "TEMPLATES",
    "BacktestResult",
    "ConformanceCase",
    "ConformanceReport",
    "Family",
    "FamilyRegistry",
    "GuardViolation",
    "LookaheadError",
    "ParameterSpec",
    "Position",
    "StrategyCapability",
    "StrategyLibrary",
    "StrategySpec",
    "Template",
    "TemplateRejected",
    "TemplateStore",
    "Trade",
    "Window",
    "assert_safe",
    "check_source",
    "check_test_source",
    "generate_bars",
    "run_backtest",
    "run_conformance",
    "slugify",
    "strategy_capability_catalog",
]

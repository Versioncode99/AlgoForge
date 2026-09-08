from forge.strategy.authoring import TemplateRejected, TemplateStore
from forge.strategy.catalog import StrategyCapability, strategy_capability_catalog
from forge.strategy.conformance import ConformanceCase, ConformanceReport, run_conformance
from forge.strategy.determinism import DeterminismReport, check_determinism, run_digest
from forge.strategy.families import BUILTIN_FAMILIES, Family, FamilyRegistry
from forge.strategy.guard import GuardViolation, assert_safe, check_source, check_test_source
from forge.strategy.ir import (
    Always,
    Arithmetic,
    Combine,
    Compare,
    CompiledStrategy,
    Constant,
    Cross,
    DefinitionProvenance,
    EntryRules,
    ExecutionAssumptions,
    ExitRules,
    Feature,
    FeatureRef,
    IRError,
    Level,
    ParamRef,
    SessionWindow,
    StrategyDefinition,
    compile_definition,
    validate_definition,
)
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
    "Always",
    "Arithmetic",
    "BacktestResult",
    "Combine",
    "Compare",
    "CompiledStrategy",
    "ConformanceCase",
    "ConformanceReport",
    "Constant",
    "Cross",
    "DefinitionProvenance",
    "DeterminismReport",
    "EntryRules",
    "ExecutionAssumptions",
    "ExitRules",
    "Family",
    "FamilyRegistry",
    "Feature",
    "FeatureRef",
    "GuardViolation",
    "IRError",
    "Level",
    "LookaheadError",
    "ParamRef",
    "ParameterSpec",
    "Position",
    "SessionWindow",
    "StrategyCapability",
    "StrategyDefinition",
    "StrategyLibrary",
    "StrategySpec",
    "Template",
    "TemplateRejected",
    "TemplateStore",
    "Trade",
    "Window",
    "assert_safe",
    "check_determinism",
    "check_source",
    "check_test_source",
    "compile_definition",
    "generate_bars",
    "run_backtest",
    "run_conformance",
    "run_digest",
    "slugify",
    "strategy_capability_catalog",
    "validate_definition",
]

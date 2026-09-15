"""The front door: "what do you want to do?", answered with registered actions.

§25 asks for an intent-based entry point and, in the same breath, forbids fake
conversational AI. The two are compatible in exactly one way: an intent is a
*route into the existing action registry*, chosen from a closed list, not a
sentence parsed by a model.

So each intent names the actions it would run and the sections it opens. An
assistant handed "I want to test an idea" looks the intent up and calls the
actions it names — the same actions the interface calls, under the same
permission policy, producing the same audit rows. Nothing here grants a
capability, and nothing here can run anything: it is a map.

`tests/modes/test_intents.py::test_every_intent_names_registered_actions`
imports the action registry and asserts every name exists, so an intent cannot
come to offer a verb that was renamed or removed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from forge.contracts.models import FrozenModel
from forge.modes.models import MODE_ORDER, WorkspaceMode
from forge.product.navigation import resolve


class Intent(StrEnum):
    FIND_A_STRATEGY = "find_a_strategy"
    TEST_AN_IDEA = "test_an_idea"
    ANALYSE_A_STRATEGY = "analyse_a_strategy"
    IMPROVE_A_STRATEGY = "improve_a_strategy"
    MANAGE_PROP_ACCOUNTS = "manage_prop_accounts"
    RUN_A_STRATEGY = "run_a_strategy"
    RESEARCH_THE_MARKET = "research_the_market"


class IntentDescriptor(FrozenModel):
    """One thing somebody might want, and where it actually goes."""

    intent: Intent
    label: str
    #: What this will do, in the operator's terms. Shown before anything runs.
    detail: str
    #: Registered action names, in the order they would run.
    actions: tuple[str, ...]
    #: Where the operator ends up, as product links.
    #:
    #: This used to be keyed by mode, because the same intent landed in
    #: different places: "research" was a section in AI and Normal reached the
    #: same material through "strategies". There is one navigation now, so the
    #: keying described a difference that no longer exists — and a map keyed by
    #: something that has stopped varying is a map that will quietly stop being
    #: checked. Each link is validated against `forge.product.navigation`, so an
    #: intent cannot offer a destination the shell does not have.
    links: tuple[str, ...]
    #: What this intent cannot do, stated at the front door rather than found
    #: three screens in.
    caveat: str = ""

    @property
    def modes(self) -> tuple[WorkspaceMode, ...]:
        """Every mode. Kept so a client built against the old payload still reads.

        An intent used to be offered in some modes and not others. Navigation is
        horizontal now: the Prop Desk is a destination everybody has, so an
        intent that lands there is offered to everybody.
        """
        return tuple(MODE_ORDER)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["modes"] = [mode.value for mode in self.modes]
        # The links, resolved. A client rendering a button wants the hash it
        # should navigate to, not a route it has to translate itself.
        payload["destinations"] = [resolve(link).hash for link in self.links]
        return payload


INTENTS: dict[Intent, IntentDescriptor] = {
    descriptor.intent: descriptor
    for descriptor in (
        IntentDescriptor(
            intent=Intent.FIND_A_STRATEGY,
            label="Find a strategy",
            detail=(
                "Search the literature for a documented effect, then build a strategy "
                "from a template that expresses it. You will see the source before "
                "anything is generated."
            ),
            actions=("search_papers", "create_family", "create_strategy_from_blueprint"),
            links=(
                "strategies",
                "research?tab=evidence",
                "research?tab=validation",
                "research?tab=findings",
            ),
            caveat=(
                "A paper's result is a claim about its own data. AlgoForge will not "
                "report it as validated until the judge has run over yours."
            ),
        ),
        IntentDescriptor(
            intent=Intent.TEST_AN_IDEA,
            label="Test an idea",
            detail=(
                "Freeze a hypothesis, build a strategy that expresses it, run it over "
                "the data you hold, and judge the result. You will see what is about to "
                "run before it does."
            ),
            actions=("create_strategy", "backtest_strategy", "validate_strategy"),
            links=(
                "strategies",
                "research?tab=validation",
                "research?tab=evidence",
            ),
            caveat=(
                "The hypothesis is frozen before the run. Changing it afterwards "
                "produces a new claim, not a better result."
            ),
        ),
        IntentDescriptor(
            intent=Intent.ANALYSE_A_STRATEGY,
            label="Analyse a strategy",
            detail=(
                "Read everything known about a strategy: its hypothesis, its evidence, "
                "the G0-G13 ladder, what was measured and what was not."
            ),
            actions=("run_analysis", "validate_strategy"),
            links=(
                "research?tab=evidence",
                "research?tab=validation",
                "strategies?tab=trades",
                "trading?tab=performance",
                "research?tab=experiments",
            ),
            caveat="",
        ),
        IntentDescriptor(
            intent=Intent.IMPROVE_A_STRATEGY,
            label="Improve a strategy",
            detail=(
                "Start from what failed. The judge's findings say which gate did not "
                "hold and what would address it; each one becomes a new experiment with "
                "its own frozen claim."
            ),
            actions=("create_strategy", "backtest_strategy", "validate_strategy"),
            links=(
                "strategies",
                "research?tab=validation",
                "research?tab=evidence",
                "research?tab=experiments",
                "campaigns?tab=memory",
            ),
            caveat=(
                "Re-running a failed strategy with different parameters is another "
                "trial, and every trial raises the bar gate G5 applies."
            ),
        ),
        IntentDescriptor(
            intent=Intent.MANAGE_PROP_ACCOUNTS,
            label="Manage prop accounts",
            detail=(
                "Connect accounts, record what each programme permits, choose a risk "
                "mode, and see which validated strategy each account should be running."
            ),
            actions=("assess_prop_account", "propdesk_connections", "propdesk_plan_allocation"),
            links=(
                "propdesk?tab=accounts",
                "propdesk?tab=allocation",
                "propdesk?tab=limits",
                "propdesk?tab=risk",
            ),
            caveat=(
                "No live broker connector exists in this build. Accounts connect to "
                "AlgoForge's own simulator, and every fill is labelled simulated."
            ),
        ),
        IntentDescriptor(
            intent=Intent.RUN_A_STRATEGY,
            label="Run a strategy",
            detail=(
                "Put a validated strategy on an account, inside a risk configuration you "
                "set, through the refusal ladder and the pre-trade gate."
            ),
            actions=("prepare_orders", "screen_orders"),
            links=(
                "trading?tab=portfolio",
                "trading?tab=gate",
                "trading?tab=execution",
                "propdesk?tab=allocation",
                "propdesk?tab=activity",
                "trading?tab=overview",
            ),
            caveat=(
                "Execution is simulated. `forge.execution.lifecycle` refuses the "
                "DEPLOYED stage outright while no broker connector exists."
            ),
        ),
        IntentDescriptor(
            intent=Intent.RESEARCH_THE_MARKET,
            label="Research the market",
            detail=(
                "Look at the instruments and periods you hold: coverage, quality, "
                "regimes, and what the archive cannot tell you."
            ),
            actions=("run_analysis",),
            links=(
                "markets?tab=charts",
                "markets?tab=data",
                "trading?tab=performance",
                "research?tab=findings",
                "campaigns?tab=memory",
            ),
            caveat="",
        ),
    )
}


def descriptor(intent: Intent | str) -> IntentDescriptor:
    try:
        parsed = Intent(intent)
    except ValueError:
        valid = ", ".join(item.value for item in Intent)
        raise KeyError(f"no intent '{intent}'. Intents: {valid}") from None
    return INTENTS[parsed]


def for_mode(mode: WorkspaceMode | str) -> list[IntentDescriptor]:
    """The intents offered in one mode, in display order.

    Every intent, for every mode. The filter is kept because the endpoint takes
    the parameter and a client may still pass it; `WorkspaceMode(mode)` is still
    called so an unknown mode is still refused rather than silently ignored.
    """
    WorkspaceMode(mode)
    return list(INTENTS.values())


def catalogue(mode: WorkspaceMode | str | None = None) -> list[dict[str, Any]]:
    items = INTENTS.values() if mode is None else for_mode(mode)
    return [item.as_dict() for item in items]

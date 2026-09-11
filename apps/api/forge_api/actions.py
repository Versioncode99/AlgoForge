"""The verbs AlgoForge can perform on its own behalf.

This module exists because of a specific, correct complaint from the console
assistant: asked to find papers, add families and start backtesting, it replied
that it could do none of those things and was right — it had no tools, only a
context blob.

Everything the interface can do is now also reachable as a named action with a
declared schema. One registry serves three callers:

* the **console assistant**, which turns a request into an action call;
* the **orchestrator**, which sequences actions into a mission;
* the **HTTP API**, so nothing here is a private back door.

Three properties are deliberate:

* **Bounded.** The set is fixed and each action validates its own arguments.
  There is no "run arbitrary code" verb and no way to reach one.
* **Honest.** An action that cannot do the thing returns a refusal with a reason,
  never a plausible-sounding success. `create_family` on a data requirement the
  installed providers cannot serve registers a *blocked* family and says so.
* **Logged.** Every call lands in the activity log, so an autonomous run leaves
  the same trail as an operator clicking.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from fastapi import HTTPException
from forge.execution.oms import OrderRefused
from forge.hedgefund.approvals import ApprovalQueue, ApprovalRequest
from forge.hedgefund.audit import AuditLog, Outcome
from forge.hedgefund.config import FundConfig
from forge.modes.models import MODE_ORDER, Stance, WorkspaceMode, parse_stance
from forge.modes.models import catalogue as mode_catalogue
from forge.modes.models import descriptor as mode_descriptor
from forge.modes.permissions import (
    ActionFacts,
    Actor,
    Judgement,
    Ruling,
    evaluate,
    summarise,
)
from forge.modes.store import ModeStore
from forge.prop.account import AccountRules, AccountState, ClosedTrade, state_from_trades
from forge.prop.account import assess as prop_assess
from forge.research import chronological_split
from forge.strategy import (
    DESCRIBED_TARGETS,
    TEMPLATES,
    VERIFIABLE_TARGETS,
    TemplateRejected,
    describe_target,
    generate_bars,
    run_backtest,
    to_python,
)
from forge.strategy.blueprints import blueprint as ir_blueprint
from forge.strategy.blueprints import catalogue as blueprint_catalogue
from forge.strategy.ir import IRError
from forge.strategy.ir import SessionWindow as IRSessionWindow
from forge.vault import VaultMirror
from forge.workstation import (
    GRID_COLUMNS,
    MAX_ROWS,
    Panel,
    PanelKind,
    Workspace,
    WorkspaceImportError,
    WorkspaceProfile,
    WorkspaceStore,
    catalogue,
    describe,
    layout_for,
    markets_for,
    new_panel_id,
    template,
)
from forge.workstation.sidebar import CATALOGUE as SIDEBAR_CATALOGUE
from forge.workstation.sidebar import Sidebar, SidebarError
from forge.workstation.sidebar import destinations as sidebar_destinations
from forge.workstation.sidebar import known as sidebar_known
from pydantic import ValidationError

from forge_api.fund import FundError
from forge_api.jobs import REGISTRY, JobHandle


class ActionError(Exception):
    """A refusal with a reason the caller can show verbatim."""


class ApprovalRequired(ActionError):
    """The call was held for a person. Nothing ran.

    A subclass of `ActionError` so every existing handler keeps showing the
    reason rather than returning a 500, and a carrier for the request so a
    surface that knows about approvals can offer the decision instead of only
    reporting the refusal.
    """

    def __init__(self, message: str, *, request: ApprovalRequest) -> None:
        super().__init__(message)
        self.request = request


class ActionRisk(StrEnum):
    """How much a caller should have to mean it.

    Separate from `mutating`, which only says whether something is written.
    Rearranging panels writes to a database and is still nothing to worry
    about; cancelling an order writes no more than that and is not. What
    matters is what is lost if the caller misunderstood.
    """

    #: Layout, opening records, changing a chart's symbol. Reversible by doing
    #: the opposite, and nothing downstream depends on it.
    SAFE = "safe"
    #: Destroys something a person made, or changes configuration that outlives
    #: the session. Recoverable only if they happen to have a backup.
    CONFIRM = "confirm"
    #: Reaches a broker, an account, or real money. No agent may perform one of
    #: these on inference alone, ever.
    HIGH = "high"


@dataclass(frozen=True)
class Action:
    name: str
    summary: str
    parameters: dict[str, Any]
    run: Callable[..., dict[str, Any]]
    mutating: bool = False
    risk: ActionRisk = ActionRisk.SAFE
    #: Changes a deterministic control: a risk limit, a prop rule set, the fund
    #: configuration, the kill switch, the operating mode or the stance.
    #: `forge.modes.permissions` denies every protected action to an AI actor in
    #: every mode and every stance, which is what makes "AI cannot raise its own
    #: limits" a property of the code rather than an intention.
    protected: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.summary,
            "mutating": self.mutating,
            "risk": str(self.risk),
            "protected": self.protected,
            "requires_confirmation": self.risk is not ActionRisk.SAFE,
            "parameters": {
                "type": "object",
                "properties": self.parameters,
                "required": [
                    key
                    for key, value in self.parameters.items()
                    if not value.get("optional", False)
                ],
            },
        }


def _advisory(value: Any) -> float:
    """An advisory multiplier, refused rather than clamped when out of range.

    A caller passing 1.4 has misunderstood what this parameter is — the advisory
    layer narrows a proposal and never widens one — and quietly treating it as
    1.0 would let the misunderstanding persist into a place where it matters.
    """
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ActionError("advisory must be a number between 0 and 1") from exc
    if not 0.0 <= number <= 1.0:
        raise ActionError(
            f"advisory must be between 0 and 1; got {number}. It is a multiplier that "
            "may narrow a risk proposal and can never widen one."
        )
    return number


def _str(value: Any, field: str, *, limit: int = 4000, lower: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionError(f"'{field}' must be a non-empty string.")
    text = value.strip()[:limit]
    return text.lower() if lower else text


class Actions:
    """The registry. Constructed once and shared."""

    def __init__(
        self,
        *,
        workspace: Any,
        library: Any,
        store: Any,
        log: Any,
        market: Any,
        engine: Any,
        agents: Any,
        families: Any,
        templates: Any,
        mirror: VaultMirror,
        workspaces: WorkspaceStore,
        ledger: Any = None,
        modes: ModeStore | None = None,
        approvals: ApprovalQueue | None = None,
        audit: AuditLog | None = None,
        prop_accounts: Any = None,
        fund: Any = None,
        prop_desk: Any = None,
    ) -> None:
        self.workspace = workspace
        self.library = library
        # The trade-ledger view and the research lab, when attached. Optional
        # so this registry can be built before they exist; the actions that
        # need them refuse by name rather than raising an attribute error.
        self.ledger = ledger
        self.lab: Any = None
        # The validation runner, attached by `forge_api.strategies` so the action
        # and the HTTP route call one function rather than two.
        self.validation: Callable[..., dict[str, Any]] | None = None
        self.store = store
        self.log = log
        self.market = market
        self.engine = engine
        self.agents = agents
        self.families = families
        self.templates = templates
        self.mirror = mirror
        self.workspaces = workspaces
        # The mode session, the approval queue and the audit log. Optional so a
        # test can build a bare registry, and checked by name at the point of
        # use: a registry without them still enforces the policy, it simply has
        # nowhere to hold a call that needs a person and says so.
        self.modes = modes
        self.approvals = approvals
        self.audit = audit
        self.prop_accounts = prop_accounts
        self.fund = fund
        # The Prop Desk service. Optional so a bare registry can be built in a
        # test; the actions that need it refuse by name rather than raising an
        # attribute error.
        self.prop_desk = prop_desk
        self._lock = threading.Lock()
        # Set for the duration of one `call`, so an action can attribute what it
        # writes without every signature growing an actor argument.
        self._actor_local = threading.local()
        self.history: list[dict[str, Any]] = []
        self._registry: dict[str, Action] = {}
        self._register_all()

    # ── dispatch ─────────────────────────────────────────────────────────────
    def names(self) -> list[str]:
        return sorted(self._registry)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._registry[name].schema() for name in self.names()]

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        confirmed: bool = False,
        actor: Actor = Actor.HUMAN,
        origin: str = "",
        approval_id: str = "",
    ) -> dict[str, Any]:
        """Run one action.

        `confirmed` is the operator's answer, not the caller's opinion. Anything
        above SAFE refuses without it, and the refusal names the tier so the
        surface asking can show the right prompt. An agent cannot set this on
        its own behalf: it has to come back through a person, which is the whole
        point of the boundary.

        `approval_id` is set only when this call *is* the execution of a held
        request. It goes onto the audit row so the chain from proposal, through
        the person who authorised it, to what happened, is followable by one id
        — which is the question the audit log exists to answer.

        `actor` is the second, independent gate, and it is the one that knows
        about modes. A surface a person drives passes `HUMAN`; the assistant,
        the orchestrator, the research loop and MCP pass `AI`, and their calls
        are ruled on by `forge.modes.permissions` against whichever mode is open.
        A caller cannot set `HUMAN` on an agent's behalf any more than it can set
        `confirmed` — those call sites are fixed in this repository, and the
        boundary is only as good as they are.
        """
        action = self._registry.get(name)
        if action is None:
            raise ActionError(f"No action named '{name}'. Available: {', '.join(self.names())}.")
        judgement = self.permission(name, actor=actor)
        if judgement.ruling is Ruling.DENY:
            self._audit(
                actor, origin, action, arguments, judgement, Outcome.DENIED,
                error=judgement.reason, approval_id=approval_id,
            )
            raise ActionError(judgement.reason)
        if judgement.ruling is Ruling.REQUIRE_APPROVAL:
            raise self._hold(action, dict(arguments or {}), judgement, actor, origin)
        if action.risk is not ActionRisk.SAFE and not confirmed:
            raise ActionError(
                f"'{name}' is a {action.risk} action and needs explicit confirmation "
                f"from the operator before it will run."
            )
        args = dict(arguments or {})
        unknown = set(args) - set(action.parameters)
        if unknown:
            raise ActionError(
                f"'{name}' does not take {', '.join(sorted(unknown))}. "
                f"It takes: {', '.join(sorted(action.parameters)) or 'no arguments'}."
            )
        started = time.time()
        # Who is running this, for the duration of this call only. Workspace
        # version history reads it so an edit the agent made is attributable
        # without threading an actor argument through every action signature.
        self._actor_local.actor = actor
        try:
            result = action.run(**args)
            record = {"action": name, "ok": True, "arguments": args, "result": result}
        except ActionError as exc:
            record = {"action": name, "ok": False, "arguments": args, "error": str(exc)}
        except Exception as exc:
            record = {
                "action": name,
                "ok": False,
                "arguments": args,
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            self._actor_local.actor = Actor.HUMAN
        record["elapsed_seconds"] = round(time.time() - started, 3)
        record["at"] = time.time()
        with self._lock:
            self.history.append(record)
            del self.history[:-200]
        if action.mutating:
            self.log.record(
                "ACTION",
                f"{name} — {'ok' if record['ok'] else record.get('error', 'failed')}"[:400],
                "info" if record["ok"] else "fail",
            )
        # Every call is audited, not only the mutating ones. The activity log
        # answers "what is happening"; this answers "who did that, and was it
        # allowed", and a read an agent performed is part of that answer.
        self._audit(
            actor, origin, action, args, judgement,
            Outcome.OK if record["ok"] else Outcome.ERROR,
            result=record.get("result"),
            error=str(record.get("error", "")),
            approval_id=approval_id,
        )
        if not record["ok"]:
            raise ActionError(str(record["error"]))
        return dict(result)

    # ── permissions, approval and audit ──────────────────────────────────────
    def context(self) -> tuple[WorkspaceMode, Stance | None]:
        """Which mode the policy should rule against.

        With no mode entered, AI mode's policy applies. That is the posture of a
        headless agent run — workflow actions permitted, destructive ones held,
        protected ones denied — and it is deliberately not the most permissive
        reading: "no mode selected" must never mean "no policy".
        """
        if self.modes is None:
            return WorkspaceMode.AI, None
        session = self.modes.session()
        if session.mode is None:
            return WorkspaceMode.AI, None
        return session.mode, session.stance

    def permission(self, name: str, *, actor: Actor = Actor.HUMAN) -> Judgement:
        """Rule on one action without running it.

        Exposed so a surface can grey out what an agent may not do, and so the
        Actions screen can show the policy rather than describe it.
        """
        action = self._registry.get(name)
        if action is None:
            raise ActionError(f"No action named '{name}'.")
        mode, stance = self.context()
        return evaluate(
            ActionFacts(
                name=action.name,
                mutating=action.mutating,
                risk=str(action.risk),
                protected=action.protected,
            ),
            actor=actor,
            mode=mode,
            stance=stance,
        )

    def _hold(
        self,
        action: Action,
        args: dict[str, Any],
        judgement: Judgement,
        actor: Actor,
        origin: str,
    ) -> ActionError:
        """Queue the call for a person and return the error to raise.

        Returns rather than raises so the call site reads as `raise self._hold(...)`
        and a reader can see that nothing continues past it.
        """
        if self.approvals is None:
            self._audit(
                actor, origin, action, args, judgement, Outcome.DENIED,
                error="no approval queue is configured",
            )
            return ActionError(
                f"{judgement.reason}. No approval queue is configured in this process, "
                f"so '{action.name}' cannot be held for review either."
            )
        request = self.approvals.submit(
            action=action.name,
            arguments=args,
            reason=judgement.reason,
            requested_by=str(actor),
            origin=origin,
            mode=judgement.mode.value,
            stance=judgement.stance.value if judgement.stance else "",
            summary=action.summary[:300],
        )
        self._audit(
            actor, origin, action, args, judgement, Outcome.PENDING_APPROVAL,
            approval_id=request.request_id,
        )
        return ApprovalRequired(
            f"{judgement.reason}. '{action.name}' is waiting for approval as "
            f"{request.request_id}; it has not run.",
            request=request,
        )

    def approve(self, request_id: str, *, decided_by: str = "operator", note: str = "") -> Any:
        """Run a held action as the operator.

        The action executes with `actor=HUMAN` because a human is what
        authorised it, and the audit row says so — with the approval id attached,
        so the chain from proposal to authorisation to result is followable.
        """
        if self.approvals is None:
            raise ActionError("no approval queue is configured")
        return self.approvals.approve(
            request_id,
            lambda name, args: self.call(
                name, args, confirmed=True, actor=Actor.HUMAN,
                origin=f"approval {request_id}", approval_id=request_id,
            ),
            decided_by=decided_by,
            note=note,
        )

    def _audit(
        self,
        actor: Actor,
        origin: str,
        action: Action,
        arguments: Any,
        judgement: Judgement,
        outcome: Outcome,
        *,
        result: Any = None,
        error: str = "",
        approval_id: str = "",
    ) -> None:
        if self.audit is None:
            return
        self.audit.record(
            actor=str(actor),
            origin=origin,
            mode=judgement.mode.value,
            stance=judgement.stance.value if judgement.stance else "",
            action=action.name,
            arguments=arguments,
            ruling=judgement.ruling.value,
            ruling_reason=judgement.reason,
            outcome=outcome,
            result=result,
            error=error,
            approval_id=approval_id,
        )

    def recent(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.history[-limit:])[::-1]

    # ── registration ─────────────────────────────────────────────────────────
    def _add(
        self,
        name: str,
        summary: str,
        parameters: dict[str, Any],
        run: Callable[..., dict[str, Any]],
        *,
        mutating: bool = False,
        risk: ActionRisk = ActionRisk.SAFE,
        protected: bool = False,
    ) -> None:
        self._registry[name] = Action(
            name, summary, parameters, run, mutating, risk, protected
        )

    def _register_all(self) -> None:
        self._add(
            "search_papers",
            "Search Crossref for quantitative-finance research and save the metadata and "
            "any available abstract into the research library. Never claims to have read "
            "the full paper.",
            {
                "query": {
                    "type": "string",
                    "description": "A mechanism or topic, at least three characters.",
                }
            },
            self.search_papers,
            mutating=True,
        )
        self._add(
            "list_families",
            "List every strategy family, whether it is runnable on the configured data, "
            "and which templates implement it.",
            {},
            self.list_families,
        )
        self._add(
            "create_family",
            "Register a new strategy family. Requires a stated economic mechanism. A "
            "family needing data the app cannot serve is registered blocked, not runnable.",
            {
                "key": {"type": "string", "description": "lowercase_with_underscores"},
                "label": {"type": "string", "description": "Display name."},
                "mechanism": {
                    "type": "string",
                    "description": "Why this should work, economically. 40 characters minimum.",
                },
                "description": {"type": "string", "optional": True},
                "data_requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "optional": True,
                    "description": "Defaults to ['BARS'].",
                },
            },
            self.create_family,
            mutating=True,
        )
        self._add(
            "list_templates",
            "List every executable strategy template with its family, parameters and grid size.",
            {"family": {"type": "string", "optional": True}},
            self.list_templates,
        )
        self._add(
            "create_template",
            "Register a new executable template. The source must define entry_signal(w, p) "
            "and exit_signal(w, p, pos), pass the static guard, and run on synthetic bars "
            "before it is accepted.",
            {
                "key": {"type": "string"},
                "name": {"type": "string"},
                "family": {"type": "string"},
                "hypothesis": {"type": "string", "description": "Mechanism, 40+ characters."},
                "falsifiable_prediction": {
                    "type": "string",
                    "description": "What result would abandon it. 30+ characters.",
                },
                "parameters": {
                    "type": "array",
                    "description": "Objects: name, default, low, high, step, description.",
                    "items": {"type": "object"},
                },
                "source": {"type": "string", "description": "The Python module."},
                "warmup_bars": {"type": "integer", "optional": True},
            },
            self.create_template,
            mutating=True,
        )
        self._add(
            "create_strategy",
            "Write a strategy to disk from a template, with parameters inside the "
            "template's declared ranges.",
            {
                "template": {"type": "string"},
                "name": {"type": "string", "optional": True},
                "parameters": {"type": "object", "optional": True},
                "hypothesis": {"type": "string", "optional": True},
                "research_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "optional": True,
                },
            },
            self.create_strategy,
            mutating=True,
        )
        self._add(
            "backtest_strategy",
            "Backtest a strategy on real bars: development first, and the reserved "
            "validation slice only if development survives. Runs as a background job.",
            {
                "strategy_id": {"type": "string"},
                "dataset": {"type": "string", "optional": True},
                "max_bars": {"type": "integer", "optional": True},
            },
            self.backtest_strategy,
            mutating=True,
        )
        self._add(
            "list_strategies",
            "List strategies with their latest result, newest first.",
            {"limit": {"type": "integer", "optional": True}},
            self.list_strategies,
        )
        self._add(
            "engine_status",
            "What the autonomous engine is doing right now, per worker.",
            {},
            self.engine_status,
        )
        self._add(
            "list_experiments",
            "Experiments in the current scope, newest first. Each carries the policy "
            "that produced it, its parent, and how it ended.",
            {
                "limit": {"type": "integer", "optional": True},
                "roots_only": {"type": "boolean", "optional": True},
            },
            self.list_experiments,
        )
        self._add(
            "experiment_lineage",
            "Where one experiment came from and everything derived from it: "
            "ancestors back to the root, direct children, and all descendants.",
            {"experiment_id": {"type": "string"}},
            self.experiment_lineage,
        )
        self._add(
            "strategy_dossier",
            "Everything known about one candidate in a single structured record: "
            "spec, backtests, verdict and its gates, validation evidence, provenance "
            "and lineage, related failures, specialist dissent, and stated limitations.",
            {"strategy_id": {"type": "string"}},
            self.strategy_dossier,
        )
        self._add(
            "research_memory",
            "What the search has already disproven: failure counts by class and the "
            "constraints now pruning candidates before any compute is spent.",
            {"limit": {"type": "integer", "optional": True}},
            self.research_memory,
        )
        self._add(
            "start_engine",
            "Start the autonomous engine: it draws candidates, writes them, backtests "
            "and judges them continuously until stopped.",
            {
                "dataset": {"type": "string", "optional": True},
                "workers": {"type": "integer", "optional": True},
                "cycle_seconds": {"type": "number", "optional": True},
                "max_strategies": {"type": "integer", "optional": True},
            },
            self.start_engine,
            mutating=True,
        )
        self._add(
            "stop_engine",
            "Ask the engine to stop after the work in flight finishes.",
            {},
            self.stop_engine,
            mutating=True,
        )
        self._add(
            "run_agent",
            "Assign a task to one specialist and return its job handle.",
            {"role": {"type": "string"}, "task": {"type": "string", "optional": True}},
            self.run_agent,
            mutating=True,
        )
        self._add(
            "read_research",
            "Read the research library: titles, mechanisms, replication gaps.",
            {
                "limit": {"type": "integer", "optional": True},
                "filter": {"type": "string", "optional": True},
            },
            self.read_research,
        )
        self._register_ir()
        self._register_lab()
        self._register_workspace()
        self._register_builder()
        self._register_validation()
        self._register_modes()
        self._register_prop()
        self._register_prop_desk()
        self._register_fund()

    def _register_ir(self) -> None:
        """The Strategy IR verbs.

        Registered here rather than given to the agent separately, which is the
        whole architecture: an agent composing a strategy calls the same action
        the interface calls, so a definition refused in one place is refused in
        both, and there is no path by which the agent can write a strategy a
        person could not.
        """
        self._add(
            "list_blueprints",
            "List the strategies that exist as data rather than as a function body. "
            "A blueprint knows where its stop, target and session window are, which "
            "is what lets the chart draw them and the ledger record them.",
            {},
            self.list_blueprints,
        )
        self._add(
            "create_strategy_from_blueprint",
            "Write a strategy from a blueprint, optionally on a different instrument, "
            "with different parameter defaults, or inside a different session window. "
            "The generated Python is checked against the definition before anything "
            "is written.",
            {
                "blueprint": {"type": "string", "description": "A key from list_blueprints."},
                "name": {"type": "string", "optional": True},
                "symbol": {"type": "string", "optional": True},
                "parameters": {"type": "object", "optional": True},
                "session_start_minute": {
                    "type": "integer",
                    "optional": True,
                    "description": "Minutes from midnight UTC. 0-1439.",
                },
                "session_end_minute": {"type": "integer", "optional": True},
                "flat_by_minute": {
                    "type": "integer",
                    "optional": True,
                    "description": "Close any open position at this minute of day, UTC.",
                },
            },
            self.create_strategy_from_blueprint,
            mutating=True,
        )
        self._add(
            "strategy_definition",
            "Read the Strategy IR a strategy renders. Hand-written strategies have "
            "none, and that is reported as an absence rather than inferred from the "
            "source.",
            {"strategy_id": {"type": "string"}},
            self.strategy_definition,
        )
        self._add(
            "export_strategy",
            "Render a strategy into another language. Python is generated because it "
            "can be executed and compared against the definition; Pine and "
            "NinjaScript return a coverage report and no code, because AlgoForge "
            "cannot check them.",
            {
                "strategy_id": {"type": "string"},
                "target": {
                    "type": "string",
                    "description": "python | pine | ninjascript",
                },
            },
            self.export_strategy,
        )
        self._add(
            "strategy_trades",
            "The historical trades of a strategy's most recent run, with the regime "
            "each was taken in and the excursion each reached. Read from the backtest "
            "artifact; nothing is recomputed.",
            {
                "strategy_id": {"type": "string"},
                "limit": {"type": "integer", "optional": True},
                "outcome": {"type": "string", "optional": True, "description": "all|win|loss"},
                "regime": {
                    "type": "string",
                    "optional": True,
                    "description": "all | BULL_LOW | BULL_HIGH | BEAR_LOW | BEAR_HIGH",
                },
            },
            self.strategy_trades,
        )
        self._add(
            "strategy_regimes",
            "Where a strategy's P&L actually came from, by market regime: exposure, "
            "trade count and net result per regime, and how the regimes follow each "
            "other.",
            {
                "strategy_id": {"type": "string"},
                "attribution": {
                    "type": "string",
                    "optional": True,
                    "description": "entry | dominant | exit",
                },
            },
            self.strategy_regimes,
        )

    # ── implementations ──────────────────────────────────────────────────────
    def search_papers(self, query: str) -> dict[str, Any]:
        text = _str(query, "query", limit=240)
        if len(text) < 3:
            raise ActionError("A research query needs at least three characters.")
        try:
            found = self.agents.research.search(text)
        except Exception as exc:
            raise ActionError(
                f"The scholarly search failed ({type(exc).__name__}). Crossref may be "
                "unreachable from this machine."
            ) from exc
        for item in found:
            self.mirror.paper(item)
        self.log.record(
            "RESEARCH",
            f"'{text}' — {len(found)} references retrieved and mirrored to the vault",
            "pass" if found else "warn",
        )
        return {
            "query": text,
            "count": len(found),
            "papers": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "authors": item["authors"],
                    "year": item["year"],
                    "url": item["url"],
                    "content_level": item["content_level"],
                }
                for item in found
            ],
            "note": (
                "Metadata and any published abstract only. Nobody has read these papers; "
                "they are UNREVIEWED until a methodology review says otherwise."
            ),
        }

    def list_families(self) -> dict[str, Any]:
        rows = self.families.with_templates(TEMPLATES)
        return {
            "count": len(rows),
            "runnable": [r["key"] for r in rows if r["runnable"]],
            "blocked": {r["key"]: r["blocked_by"] for r in rows if not r["runnable"]},
            "families": rows,
        }

    def create_family(
        self,
        key: str,
        label: str,
        mechanism: str,
        description: str = "",
        data_requirements: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            family = self.families.create(
                key=_str(key, "key", limit=40, lower=True),
                label=_str(label, "label", limit=80),
                description=str(description or "")[:600],
                mechanism=_str(mechanism, "mechanism", limit=2000),
                data_requirements=tuple(data_requirements or ("BARS",)),
                created_by="agent",
            )
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
        self.mirror.family(family.as_dict())
        return {
            "created": family.key,
            "runnable": family.runnable,
            "blocked_by": list(family.blocked_by),
            "note": (
                "Registered and usable."
                if family.runnable
                else "Registered but blocked: the data it declares is not configured, so the "
                "strategy writer will refuse templates in it. Adding the family did not add "
                "the data."
            ),
        }

    def list_templates(self, family: str | None = None) -> dict[str, Any]:
        custom = set(self.templates.keys())
        rows = [
            {
                "key": t.key,
                "name": t.name,
                "family": t.family,
                "origin": "custom" if t.key in custom else "builtin",
                "parameters": {
                    p.name: {"default": p.default, "low": p.low, "high": p.high, "step": p.step}
                    for p in t.parameters
                },
            }
            for t in sorted(TEMPLATES.values(), key=lambda t: (t.family, t.key))
            if family is None or t.family == family
        ]
        return {"count": len(rows), "templates": rows}

    def create_template(
        self,
        key: str,
        name: str,
        family: str,
        hypothesis: str,
        falsifiable_prediction: str,
        parameters: list[dict[str, Any]],
        source: str,
        warmup_bars: int = 60,
    ) -> dict[str, Any]:
        if not isinstance(parameters, list):
            raise ActionError("'parameters' must be a list of parameter objects.")
        try:
            template = self.templates.create(
                key=_str(key, "key", limit=50, lower=True),
                name=_str(name, "name", limit=120),
                family=_str(family, "family", limit=40, lower=True),
                hypothesis=_str(hypothesis, "hypothesis"),
                falsifiable_prediction=_str(falsifiable_prediction, "falsifiable_prediction"),
                parameters=parameters,
                source=_str(source, "source", limit=24_000),
                warmup_bars=int(warmup_bars),
                known_families=self.families.runnable_keys(),
                existing_templates=set(TEMPLATES),
                created_by="agent",
            )
        except TemplateRejected as exc:
            raise ActionError(str(exc)) from exc
        TEMPLATES[template.key] = template
        meta = self.templates.metadata(template.key)
        return {
            "created": template.key,
            "family": template.family,
            "smoke_test": meta["smoke_test"],
            "note": (
                "Registered. The engine can draw candidates from it on the next cycle. "
                "The smoke test proves the code runs on edge-free synthetic bars — it is "
                "not evidence the idea works."
            ),
        }

    def _register_lab(self) -> None:
        """Research-lab verbs.

        These answer questions about a ledger that already exists. None of them
        runs a strategy, consumes a holdout or produces a verdict, which is why
        they are all safe and none is mutating in the sense that matters —
        saving an artifact writes a derived record that can be regenerated from
        the backtest it cites.
        """
        self._add(
            "list_analyses",
            "The questions the research lab can answer about a strategy's trades, "
            "and what each one needs.",
            {},
            self.list_analyses,
        )
        self._add(
            "run_analysis",
            "Answer one question about a strategy's historical trades — expectancy "
            "by hour, by volatility percentile, as a surface over both, by month, "
            "by excursion, or what the worst 10% of trades have in common. Every "
            "bucket reports its own sample size and carries the trades behind it.",
            {
                "analysis": {
                    "type": "string",
                    "description": (
                        "by_hour | by_volatility_percentile | hour_by_volatility | "
                        "edge_over_time | excursion | worst_decile"
                    ),
                },
                "strategy_id": {"type": "string"},
                "backtest_id": {"type": "string", "optional": True},
                "measure": {
                    "type": "string",
                    "optional": True,
                    "description": "average_trade | net_pnl | win_rate | trade_count",
                },
            },
            self.run_analysis,
            mutating=True,
        )
        self._add(
            "list_analysis_artifacts",
            "Questions that have already been asked, newest first, with the run "
            "each was asked of.",
            {
                "strategy_id": {"type": "string", "optional": True},
                "limit": {"type": "integer", "optional": True},
            },
            self.list_analysis_artifacts,
        )
        self._add(
            "analysis_trades",
            "The trades behind one cell of a stored analysis — the drilldown from "
            "a region of a chart to the evidence under it. Coordinates are one "
            "index per axis.",
            {
                "artifact_id": {"type": "string"},
                "coords": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "One index per axis, in axis order.",
                },
                "limit": {"type": "integer", "optional": True},
            },
            self.analysis_trades,
        )

    # ── research lab ─────────────────────────────────────────────────────────
    def _require_lab(self) -> Any:
        if self.lab is None:
            raise ActionError("The research lab is not attached to this registry.")
        return self.lab

    def list_analyses(self) -> dict[str, Any]:
        items = self._require_lab().catalogue()
        return {
            "analyses": items,
            "total": len(items),
            "note": (
                "These slice a ledger that already exists. None of them runs a "
                "strategy, consumes a holdout, or produces a verdict."
            ),
        }

    def run_analysis(
        self,
        analysis: str,
        strategy_id: str,
        backtest_id: str | None = None,
        measure: str | None = None,
    ) -> dict[str, Any]:
        lab = self._require_lab()
        try:
            payload: dict[str, Any] = lab.run(
                _str(analysis, "analysis", limit=64, lower=True),
                _str(strategy_id, "strategy_id", limit=120),
                backtest_id=backtest_id or None,
                measure=_str(measure or "average_trade", "measure", limit=32, lower=True),
                created_by="agent",
            )
        except Exception as exc:
            raise ActionError(str(exc)) from exc
        # The full cell list can be thousands of entries with trade ids attached.
        # An agent reading this wants the shape and the findings; the artifact id
        # is how it gets the rest.
        return {
            "artifact_id": payload["artifact_id"],
            "analysis": payload["analysis"],
            "title": payload["title"],
            "question": payload["question"],
            "shape": payload["shape"],
            "measure": payload["measure"],
            "axes": [
                {"name": axis["name"], "label": axis["label"], "categories": axis["categories"]}
                for axis in payload["axes"]
            ],
            "cells": [
                {
                    "coords": cell["coords"],
                    "labels": cell["labels"],
                    "trade_count": cell["trade_count"],
                    "value": cell["value"],
                    "insufficient": cell["insufficient"],
                }
                for cell in payload["cells"]
            ],
            "total_trades": payload["total_trades"],
            "covered_trades": payload["covered_trades"],
            "findings": payload["findings"],
            "warnings": payload["warnings"],
            "is_evidence": False,
            "next": (
                "analysis_trades(artifact_id, coords) opens the trades behind any "
                "cell."
            ),
        }

    def list_analysis_artifacts(
        self, strategy_id: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        lab = self._require_lab()
        rows = lab.store.list(strategy_id or "", max(1, min(int(limit or 50), 500)))
        return {"artifacts": rows, "total": len(rows), "stored": lab.store.count()}

    def analysis_trades(
        self, artifact_id: str, coords: list[int], limit: int | None = None
    ) -> dict[str, Any]:
        lab = self._require_lab()
        if not isinstance(coords, list) or not coords:
            raise ActionError("'coords' must be a non-empty list of integers, one per axis.")
        try:
            payload: dict[str, Any] = lab.drilldown(
                _str(artifact_id, "artifact_id", limit=120),
                [int(value) for value in coords],
                limit=max(1, min(int(limit or 100), 2000)),
            )
        except Exception as exc:
            raise ActionError(str(exc)) from exc
        return payload

    # ── the Strategy IR ──────────────────────────────────────────────────────
    def list_blueprints(self) -> dict[str, Any]:
        items = blueprint_catalogue()
        return {
            "blueprints": items,
            "total": len(items),
            "note": (
                "Session windows are minutes from midnight UTC. A window written in "
                "local time and read as UTC is a strategy trading the wrong hours."
            ),
        }

    def create_strategy_from_blueprint(
        self,
        blueprint: str,
        name: str | None = None,
        symbol: str | None = None,
        parameters: dict[str, Any] | None = None,
        session_start_minute: int | None = None,
        session_end_minute: int | None = None,
        flat_by_minute: int | None = None,
    ) -> dict[str, Any]:
        key = _str(blueprint, "blueprint", limit=64, lower=True)
        try:
            definition = ir_blueprint(key)
        except KeyError as exc:
            raise ActionError(str(exc)) from exc

        changes: dict[str, Any] = {}
        if symbol:
            changes["symbol"] = _str(symbol, "symbol", limit=16).upper()
        if parameters:
            declared = {p.name: p for p in definition.parameters}
            unknown = set(parameters) - set(declared)
            if unknown:
                raise ActionError(
                    f"Unknown parameter(s) {', '.join(sorted(unknown))}. This blueprint "
                    f"declares {', '.join(sorted(declared))}."
                )
            for pname, raw in parameters.items():
                value = float(raw)
                spec = declared[pname]
                if not spec.low <= value <= spec.high:
                    raise ActionError(
                        f"Parameter '{pname}' = {value} is outside its declared range "
                        f"[{spec.low}, {spec.high}]."
                    )
            changes["parameters"] = tuple(
                p.model_copy(update={"default": float(parameters[p.name])})
                if p.name in parameters
                else p
                for p in definition.parameters
            )
        if session_start_minute is not None or session_end_minute is not None:
            current = definition.entry.session
            if current is None and (session_start_minute is None or session_end_minute is None):
                raise ActionError(
                    "This blueprint has no session window, so both a start and an end "
                    "minute are needed to give it one."
                )
            changes["entry"] = definition.entry.model_copy(
                update={
                    "session": IRSessionWindow(
                        start_minute=(
                            session_start_minute
                            if session_start_minute is not None
                            else (current.start_minute if current else 0)
                        ),
                        end_minute=(
                            session_end_minute
                            if session_end_minute is not None
                            else (current.end_minute if current else 0)
                        ),
                        label="operator window (UTC minutes)",
                    )
                }
            )
        if flat_by_minute is not None:
            changes["exit"] = definition.exit.model_copy(
                update={"flat_by_minute": int(flat_by_minute)}
            )
        if changes:
            definition = definition.model_copy(update=changes)

        # The same check the HTTP route performs. A definition whose rendering
        # disagrees with it is refused, not written and flagged.
        bars = generate_bars(count=4000, seed=20260908)
        try:
            spec = self.library.create_from_definition(
                definition, name=name, symbol=symbol, created_by="agent", verify_bars=bars
            )
        except (IRError, ValueError) as exc:
            raise ActionError(str(exc)) from exc

        self.mirror.strategy(spec)
        return {
            "strategy_id": spec.strategy_id,
            "name": spec.name,
            "family": spec.family,
            "symbol": spec.symbol,
            "warmup_bars": spec.warmup_bars,
            "definition_id": definition.definition_id,
            "definition_hash": definition.definition_hash,
            "session": (
                definition.entry.session.label if definition.entry.session else "none"
            ),
            "has_stop": definition.exit.stop is not None,
            "has_target": definition.exit.target is not None,
            "has_trailing": definition.exit.trailing is not None,
            "generated_python_verified": True,
            "next": (
                f"backtest_strategy(strategy_id='{spec.strategy_id}') to produce a "
                "trade ledger, then strategy_trades to read it."
            ),
        }

    def strategy_definition(self, strategy_id: str) -> dict[str, Any]:
        key = _str(strategy_id, "strategy_id", limit=120)
        try:
            definition = self.library.get_definition(key)
        except KeyError as exc:
            raise ActionError(
                f"Strategy '{key}' is hand-written Python and has no definition. "
                "AlgoForge does not infer one from source: a guess presented as the "
                "canonical record is worse than no record."
            ) from exc
        return {
            "definition": definition.model_dump(mode="json"),
            "definition_id": definition.definition_id,
            "definition_hash": definition.definition_hash,
        }

    def export_strategy(self, strategy_id: str, target: str) -> dict[str, Any]:
        key = _str(strategy_id, "strategy_id", limit=120)
        want = _str(target, "target", limit=32, lower=True)
        try:
            definition = self.library.get_definition(key)
        except KeyError as exc:
            raise ActionError(
                f"Export renders the Strategy IR, and '{key}' is hand-written Python. "
                "Its source is already readable in the library."
            ) from exc
        if want in VERIFIABLE_TARGETS:
            report = to_python(definition)
        elif want in DESCRIBED_TARGETS:
            report = describe_target(definition, want)
        else:
            raise ActionError(
                f"'{want}' is not an export target. Generated: "
                f"{', '.join(VERIFIABLE_TARGETS)}. Described but not generated: "
                f"{', '.join(DESCRIBED_TARGETS)}."
            )
        return report.as_dict()

    def strategy_trades(
        self,
        strategy_id: str,
        limit: int | None = None,
        outcome: str | None = None,
        regime: str | None = None,
    ) -> dict[str, Any]:
        key = _str(strategy_id, "strategy_id", limit=120)
        if self.ledger is None:
            raise ActionError("The trade ledger service is not attached to this registry.")
        try:
            payload: dict[str, Any] = self.ledger.ledger(
                key,
                outcome=_str(outcome or "all", "outcome", limit=8, lower=True),
                regime=(regime or "all").strip().upper() if regime else "all",
                limit=max(1, min(int(limit or 200), 4000)),
            )
        except Exception as exc:
            raise ActionError(str(exc)) from exc
        return payload

    def strategy_regimes(
        self, strategy_id: str, attribution: str | None = None
    ) -> dict[str, Any]:
        key = _str(strategy_id, "strategy_id", limit=120)
        basis = _str(attribution or "entry", "attribution", limit=16, lower=True)
        if basis not in ("entry", "dominant", "exit"):
            raise ActionError("attribution must be one of entry, dominant, exit.")
        if self.ledger is None:
            raise ActionError("The trade ledger service is not attached to this registry.")
        try:
            report: dict[str, Any] = self.ledger.regime_report(key, attribution=basis)
        except Exception as exc:
            raise ActionError(str(exc)) from exc
        return report

    def create_strategy(
        self,
        template: str,
        name: str | None = None,
        parameters: dict[str, Any] | None = None,
        hypothesis: str | None = None,
        research_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        key = _str(template, "template", limit=50, lower=True)
        item = TEMPLATES.get(key)
        if item is None:
            raise ActionError(f"No template '{key}'. Available: {', '.join(sorted(TEMPLATES))}.")
        family = self.families.get(item.family)
        if family is not None and not family.runnable:
            raise ActionError(
                f"Template '{key}' is in family '{item.family}', which is blocked on "
                f"{', '.join(family.blocked_by)}. That data is not configured, so a strategy "
                "written from it could not be honestly backtested."
            )
        clean: dict[str, float] = {}
        for spec in item.parameters:
            if not parameters or spec.name not in parameters:
                continue
            value = float(parameters[spec.name])
            if not spec.low <= value <= spec.high:
                raise ActionError(
                    f"Parameter '{spec.name}' = {value} is outside its declared range "
                    f"[{spec.low}, {spec.high}]."
                )
            clean[spec.name] = value
        spec_out = self.library.create_from_template(
            key,
            name=name,
            parameters=clean or None,
            created_by="agent",
            research_sources=tuple(str(s) for s in (research_sources or []))[:8],
            hypothesis=hypothesis,
        )
        titles = []
        for source_id in spec_out.research_sources:
            match = next((s for s in self.agents.research.list() if s["id"] == source_id), None)
            if match:
                titles.append(str(match["title"]))
        self.mirror.strategy(spec_out, source_titles=titles)
        self.log.record(
            "STRATEGY", f"created {spec_out.strategy_id} via action", "info", spec_out.strategy_id
        )
        return {
            "strategy_id": spec_out.strategy_id,
            "name": spec_out.name,
            "family": spec_out.family,
            "template": spec_out.template,
            "parameters": spec_out.defaults,
            "note": "Written to disk. Nothing has been measured yet — run backtest_strategy.",
        }

    def backtest_strategy(
        self, strategy_id: str, dataset: str | None = None, max_bars: int | None = None
    ) -> dict[str, Any]:
        sid = _str(strategy_id, "strategy_id", limit=120)
        try:
            spec = self.library.get_spec(sid)
        except KeyError as exc:
            raise ActionError(f"No strategy '{sid}'.") from exc
        key = dataset or self.engine.state.config.dataset
        limit = int(max_bars or 250_000)

        def work(handle: JobHandle) -> dict[str, Any]:
            handle.progress(0, "loading bars")
            bars, meta = self.market.load(key, limit=limit)
            module = self.library.load_module(sid)
            code_hash = self.library.code_hash(sid)
            if not meta.is_real:
                handle.progress(1, "synthetic run")
                result = run_backtest(
                    module,
                    spec,
                    bars,
                    code_hash=code_hash,
                    labels=("SYNTHETIC_DATA", "NON_PROMOTABLE"),
                    evidence_tier="SYNTHETIC",
                    dataset_key=key,
                )
                self.store.save(result)
                self.mirror.backtest(result.model_dump(mode="json"), strategy_name=spec.name)
                return {"partition": "SYNTHETIC", "net_pnl": result.net_pnl}

            partitions = chronological_split(bars, warmup_bars=spec.warmup_bars)
            handle.progress(1, "development partition")
            development = run_backtest(
                module,
                spec,
                partitions.development,
                code_hash=code_hash,
                labels=("REAL_DATA", "DEVELOPMENT_IN_SAMPLE", "UNCALIBRATED"),
                evidence_tier="DEVELOPMENT_IN_SAMPLE",
                dataset_key=key,
                partition_name="DEVELOPMENT",
                split_receipt=partitions.receipt,
            )
            self.store.save(development)
            self.mirror.backtest(development.model_dump(mode="json"), strategy_name=spec.name)
            if development.net_pnl <= 0 or len(development.trades) < 30:
                handle.progress(3, "screened out")
                return {
                    "partition": "DEVELOPMENT",
                    "net_pnl": development.net_pnl,
                    "trades": len(development.trades),
                    "reached_validation": False,
                    "note": (
                        "Development did not survive, so the reserved validation slice was "
                        "not touched. That reservation is the point: looking at it now would "
                        "spend the only unbiased sample this strategy has."
                    ),
                }
            handle.progress(2, "validation partition")
            validation = run_backtest(
                module,
                spec,
                partitions.validation,
                code_hash=code_hash,
                labels=("REAL_DATA", "VALIDATION_OOS", "UNCALIBRATED"),
                evidence_tier="VALIDATION_OOS",
                dataset_key=key,
                partition_name="VALIDATION",
                split_receipt=partitions.receipt,
            )
            self.store.save(validation)
            self.mirror.backtest(validation.model_dump(mode="json"), strategy_name=spec.name)
            handle.progress(3, "complete")
            return {
                "partition": "VALIDATION",
                "development_net": development.net_pnl,
                "net_pnl": validation.net_pnl,
                "trades": len(validation.trades),
                "win_rate": validation.win_rate,
                "max_drawdown": validation.max_drawdown,
                "reached_validation": True,
            }

        job = REGISTRY.submit("backtest", f"{spec.name} on {key}", 3, work)
        return {
            "job_id": job.job_id,
            "strategy_id": sid,
            "dataset": key,
            "note": "Running in the background. Poll /api/v1/jobs/{job_id} for the result.",
        }

    def list_strategies(self, limit: int = 25) -> dict[str, Any]:
        rows = []
        for spec in self.library.list_specs()[: max(1, min(int(limit), 200))]:
            latest = self.store.latest(spec.strategy_id)
            rows.append(
                {
                    "strategy_id": spec.strategy_id,
                    "name": spec.name,
                    "family": spec.family,
                    "template": spec.template,
                    "created_at": spec.created_at.isoformat(),
                    "net_pnl": None if latest is None else latest.get("net_pnl"),
                    "trades": 0 if latest is None else len(latest.get("trades", [])),
                    "evidence_tier": None if latest is None else latest.get("evidence_tier"),
                }
            )
        return {"count": len(rows), "strategies": rows}

    def engine_status(self) -> dict[str, Any]:
        return dict(self.engine.status())

    def list_experiments(
        self, limit: int | None = None, roots_only: bool | None = None
    ) -> dict[str, Any]:
        scope = self.engine._scope()
        count = max(1, min(int(limit or 80), 500))
        rows = (
            self.engine.experiments.roots(scope, count)
            if roots_only
            else self.engine.experiments.recent(scope, count)
        )
        return {"scope": scope, "total": self.engine.experiments.count(scope), "experiments": rows}

    def experiment_lineage(self, experiment_id: str) -> dict[str, Any]:
        key = _str(experiment_id, "experiment_id", limit=120)
        line = self.engine.experiments.lineage(key)
        if line["experiment"] is None:
            raise ActionError(
                f"No experiment '{key}'. Use list_experiments to see what exists."
            )
        return dict(line)

    def strategy_dossier(self, strategy_id: str) -> dict[str, Any]:
        from forge_api.dossier import build_dossier

        key = _str(strategy_id, "strategy_id", limit=120)
        try:
            return build_dossier(
                root=self.workspace.repo,
                library=self.library,
                store=self.store,
                experiments=self.engine.experiments,
                memory=self.engine.memory,
                snapshots=self.engine.snapshots,
                scope=self.engine._scope(),
                strategy_id=key,
            )
        except KeyError as exc:
            raise ActionError(
                f"No strategy '{key}'. Use list_strategies to see what exists."
            ) from exc

    def research_memory(self, limit: int | None = None) -> dict[str, Any]:
        scope = self.engine._scope()
        count = max(1, min(int(limit or 100), 500))
        return {
            "scope": scope,
            "counts": self.engine.memory.counts(scope),
            "total": self.engine.memory.total(scope),
            "constraints": self.engine.constraints()[:count],
        }

    def start_engine(
        self,
        dataset: str | None = None,
        workers: int | None = None,
        cycle_seconds: float | None = None,
        max_strategies: int | None = None,
    ) -> dict[str, Any]:
        from forge_api.engine import EngineConfig
        from forge_api.market import DATASETS

        current = self.engine.state.config
        key = dataset or current.dataset
        if key not in DATASETS:
            raise ActionError(f"Unknown dataset '{key}'. Available: {', '.join(sorted(DATASETS))}.")
        if self.engine.state.running:
            return {"already_running": True, **self.engine.status()}
        config = EngineConfig(
            dataset=key,
            cycle_seconds=float(cycle_seconds or current.cycle_seconds),
            max_strategies=int(max_strategies or current.max_strategies),
            max_bars=current.max_bars,
            workers=max(1, min(int(workers or current.workers), 8)),
        )
        return dict(self.engine.start(config))

    def stop_engine(self) -> dict[str, Any]:
        return dict(self.engine.stop())

    def run_agent(self, role: str, task: str = "") -> dict[str, Any]:
        try:
            return dict(self.agents.submit(_str(role, "role", limit=40, lower=True), task))
        except KeyError as exc:
            raise ActionError(
                f"No specialist '{role}'. Available: {', '.join(self.agents.roles())}."
            ) from exc
        except ValueError as exc:
            raise ActionError(str(exc)) from exc

    def read_research(self, limit: int = 20, filter: str | None = None) -> dict[str, Any]:
        needle = (filter or "").lower()
        rows = [
            {
                "id": item["id"],
                "title": item["title"],
                "authors": item.get("authors"),
                "year": item.get("year"),
                "evidence": item.get("evidence"),
                "replication_gap": item.get("replication_gap"),
                "templates": item.get("templates", []),
            }
            for item in self.agents.research.list()
            if not needle or needle in f"{item.get('title', '')} {item.get('topic', '')}".lower()
        ][: max(1, min(int(limit), 100))]
        return {"count": len(rows), "sources": rows}

    # ── workspace ────────────────────────────────────────────────────────────
    # These verbs are what make the agent an operator rather than a parallel
    # implementation. Each one is what the interface itself calls when a person
    # drags a panel, so there is exactly one way to change a layout and both
    # callers go through it. An agent able to arrange panels by some private
    # route would be a second implementation to keep in step, and the first
    # divergence between them would be silent.

    def _register_workspace(self) -> None:
        self._add(
            "list_workspace_templates",
            "List the workspace templates available as starting points. Templates are "
            "presets, not modes: nothing is locked behind one, and a workspace built "
            "from a template can be changed into anything else.",
            {},
            self.list_workspace_templates,
        )
        self._add(
            "list_workspaces",
            "List the operator's saved workspaces, newest first, with panel counts.",
            {},
            self.list_workspaces,
        )
        self._add(
            "describe_workspace",
            "The full contents of one workspace: every panel, its kind, position and "
            "settings. Omit workspace_id for the one currently open.",
            {"workspace_id": {"type": "string", "optional": True}},
            self.describe_workspace,
        )
        self._add(
            "create_workspace",
            "Create a workspace, optionally seeded from a template and with its own "
            "sidebar. Pass sidebar_items to compose one in a single call - 'a workspace "
            "for NQ research and prop trading' is charts + campaigns + agents + desk.",
            {
                "name": {"type": "string", "description": "What to call it."},
                "template_key": {
                    "type": "string",
                    "optional": True,
                    "description": "A key from list_workspace_templates. Omit for empty.",
                },
                "activate": {"type": "boolean", "optional": True},
                "description": {"type": "string", "optional": True},
                "icon": {"type": "string", "optional": True},
                "mode": {
                    "type": "string",
                    "optional": True,
                    "description": "A built-in mode to inherit the default rail from.",
                },
                "sidebar_items": {
                    "type": "array",
                    "optional": True,
                    "description": "Routes from list_sidebar_destinations, grouped for you.",
                },
            },
            self.create_workspace,
            mutating=True,
        )
        self._add(
            "open_workspace",
            "Make a workspace the active one.",
            {"workspace_id": {"type": "string"}},
            self.open_workspace,
            mutating=True,
        )
        self._add(
            "rename_workspace",
            "Rename a workspace. The layout is untouched.",
            {"workspace_id": {"type": "string"}, "name": {"type": "string"}},
            self.rename_workspace,
            mutating=True,
        )
        self._add(
            "clone_workspace",
            "Copy a workspace under a new name - the way to make an ES version of an NQ "
            "desk without disturbing the original.",
            {"workspace_id": {"type": "string"}, "name": {"type": "string"}},
            self.clone_workspace,
            mutating=True,
        )
        self._add(
            "set_default_workspace",
            "Choose which workspace opens when AlgoForge starts. Different from opening "
            "one: opening is for now, the default is for every cold start.",
            {"workspace_id": {"type": "string"}},
            self.set_default_workspace,
            mutating=True,
        )
        self._add(
            "export_workspace",
            "Export a workspace as a portable document. Carries the layout only - no "
            "research, no credentials, and no ids that would collide on the way back in.",
            {"workspace_id": {"type": "string"}},
            self.export_workspace,
        )
        self._add(
            "import_workspace",
            "Create a workspace from an exported document. Every panel is validated by "
            "the same models the rest of the store uses, so an import cannot introduce a "
            "panel kind that does not render.",
            {
                "document": {"type": "object", "description": "The exported payload."},
                "name": {"type": "string", "optional": True, "description": "Rename on import."},
            },
            self.import_workspace,
            mutating=True,
        )
        self._add(
            "workspace_history",
            "Every saved state of a workspace, newest first, with what changed, who "
            "changed it and which version came before. Omit workspace_id for the open one.",
            {"workspace_id": {"type": "string", "optional": True}},
            self.workspace_history,
        )
        self._add(
            "restore_workspace_version",
            "Put a previous state back. Recorded as a new version on top rather than a "
            "rewind, so the work in between stays readable and the restore is undoable.",
            {
                "version": {"type": "integer", "description": "From workspace_history."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.restore_workspace_version,
            mutating=True,
        )
        self._add(
            "duplicate_workspace_version",
            "Copy a previous state into a new workspace, leaving the original alone.",
            {
                "version": {"type": "integer"},
                "name": {"type": "string", "description": "What to call the copy."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.duplicate_workspace_version,
            mutating=True,
        )
        self._add(
            "collapse_panel",
            "Fold a panel to its title bar, or unfold it. The geometry is kept, so "
            "unfolding restores exactly what was there.",
            {
                "panel_id": {"type": "string"},
                "collapsed": {"type": "boolean", "optional": True, "description": "Defaults true."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.collapse_panel,
            mutating=True,
        )
        self._add(
            "reorder_panel",
            "Move a panel within the stacking order. Later panels draw over earlier ones, "
            "so moving one to the end is 'bring to front'.",
            {
                "panel_id": {"type": "string"},
                "position": {"type": "integer", "description": "0 is the back of the stack."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.reorder_panel,
            mutating=True,
        )
        # ── the sidebar ──────────────────────────────────────────────────────
        # Registered here rather than anywhere AI-specific, because there is
        # only one implementation: the interface's "add to sidebar" button and
        # "add the agent monitor to this workspace" asked of the assistant call
        # the same function. An agent cannot compose a workspace the interface
        # could not.
        self._add(
            "list_sidebar_destinations",
            "Every destination that can go in a sidebar, from every mode - charts, prop "
            "accounts, research campaigns, agents, validation, risk. This union is the "
            "point: a workspace can hold any of them without switching modes.",
            {},
            self.list_sidebar_destinations,
        )
        self._add(
            "describe_sidebar",
            "The sidebar of a workspace: its groups, their items and which are pinned or "
            "hidden. Omit workspace_id for the one currently open.",
            {"workspace_id": {"type": "string", "optional": True}},
            self.describe_sidebar,
        )
        self._add(
            "add_sidebar_item",
            "Put a destination in the sidebar. Use list_sidebar_destinations for the "
            "routes. Omit group_id and it joins the first group.",
            {
                "route": {"type": "string", "description": "From list_sidebar_destinations."},
                "workspace_id": {"type": "string", "optional": True},
                "group_id": {"type": "string", "optional": True},
                "label": {"type": "string", "optional": True, "description": "Your own wording."},
                "position": {"type": "integer", "optional": True},
            },
            self.add_sidebar_item,
            mutating=True,
        )
        self._add(
            "remove_sidebar_item",
            "Take a destination out of the sidebar. Hiding keeps its position; removing "
            "does not.",
            {
                "route": {"type": "string"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.remove_sidebar_item,
            mutating=True,
        )
        self._add(
            "move_sidebar_item",
            "Move a sidebar item to another group, or to another position in its own.",
            {
                "route": {"type": "string"},
                "group_id": {"type": "string"},
                "position": {"type": "integer", "optional": True},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.move_sidebar_item,
            mutating=True,
        )
        self._add(
            "rename_sidebar_item",
            "Give a sidebar item your own label. The destination is unchanged.",
            {
                "route": {"type": "string"},
                "label": {"type": "string"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.rename_sidebar_item,
            mutating=True,
        )
        self._add(
            "pin_sidebar_item",
            "Pin a sidebar item above its group, or unpin it. Pinned items survive a "
            "group collapse.",
            {
                "route": {"type": "string"},
                "pinned": {"type": "boolean", "optional": True, "description": "Defaults true."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.pin_sidebar_item,
            mutating=True,
        )
        self._add(
            "hide_sidebar_item",
            "Hide a sidebar item without removing it, so showing it again restores its "
            "position.",
            {
                "route": {"type": "string"},
                "hidden": {"type": "boolean", "optional": True, "description": "Defaults true."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.hide_sidebar_item,
            mutating=True,
        )
        self._add(
            "add_sidebar_group",
            "Create a sidebar group - 'MY TRADING', 'RESEARCH', 'BUSINESS'.",
            {
                "group_id": {"type": "string", "description": "A short key, lower case."},
                "label": {"type": "string", "description": "What the operator reads."},
                "position": {"type": "integer", "optional": True},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.add_sidebar_group,
            mutating=True,
        )
        self._add(
            "remove_sidebar_group",
            "Remove a sidebar group and everything in it.",
            {
                "group_id": {"type": "string"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.remove_sidebar_group,
            mutating=True,
        )
        self._add(
            "rename_sidebar_group",
            "Rename a sidebar group.",
            {
                "group_id": {"type": "string"},
                "label": {"type": "string"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.rename_sidebar_group,
            mutating=True,
        )
        self._add(
            "collapse_sidebar_group",
            "Fold a sidebar group, or unfold it.",
            {
                "group_id": {"type": "string"},
                "collapsed": {"type": "boolean", "optional": True, "description": "Defaults true."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.collapse_sidebar_group,
            mutating=True,
        )
        self._add(
            "reorder_sidebar_groups",
            "Put the sidebar groups in this order. Any omitted keep their relative order.",
            {
                "group_ids": {"type": "array", "description": "Group keys, in the order wanted."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.reorder_sidebar_groups,
            mutating=True,
        )
        self._add(
            "reset_sidebar",
            "Throw away a custom sidebar and rebuild the one this workspace's mode ships "
            "with. The layout is untouched.",
            {"workspace_id": {"type": "string", "optional": True}},
            self.reset_sidebar,
            mutating=True,
            risk=ActionRisk.CONFIRM,
        )
        self._add(
            "link_campaign_to_workspace",
            "Associate a research campaign with a workspace so its surfaces open on this "
            "screen. A link only: it does not start the campaign or change what it may do.",
            {
                "campaign_id": {"type": "string"},
                "linked": {"type": "boolean", "optional": True, "description": "Defaults true."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.link_campaign_to_workspace,
            mutating=True,
        )
        self._add(
            "link_account_to_workspace",
            "Associate a trading account with a workspace. A link only: it does not "
            "connect the account or grant any permission over it.",
            {
                "account_id": {"type": "string"},
                "linked": {"type": "boolean", "optional": True, "description": "Defaults true."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.link_account_to_workspace,
            mutating=True,
        )
        self._add(
            "describe_this_workspace",
            "Give a workspace a description and an icon, so the switcher says what it is "
            "for rather than only what it is called.",
            {
                "description": {"type": "string", "optional": True},
                "icon": {
                    "type": "string",
                    "optional": True,
                    "description": "An emoji, an initial, or a ticker.",
                },
                "workspace_id": {"type": "string", "optional": True},
            },
            self.describe_this_workspace,
            mutating=True,
        )
        self._add(
            "pin_workspace",
            "Pin a workspace to the top of the switcher, or unpin it.",
            {
                "pinned": {"type": "boolean", "optional": True, "description": "Defaults true."},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.pin_workspace,
            mutating=True,
        )
        self._add(
            "delete_workspace",
            "Delete a workspace and its layout. Research is not touched: a workspace "
            "holds no experiments, artifacts or evidence.",
            {"workspace_id": {"type": "string"}},
            self.delete_workspace,
            mutating=True,
            risk=ActionRisk.CONFIRM,
        )
        self._add(
            "add_panel",
            "Add a panel to a workspace. Position is in grid units on a 12-column grid; "
            "omit it and the panel lands below everything already there.",
            {
                "kind": {
                    "type": "string",
                    "description": "One of: " + ", ".join(sorted(k.value for k in PanelKind)),
                },
                "workspace_id": {"type": "string", "optional": True},
                "x": {"type": "integer", "optional": True},
                "y": {"type": "integer", "optional": True},
                "width": {"type": "integer", "optional": True},
                "height": {"type": "integer", "optional": True},
                "symbol": {"type": "string", "optional": True},
                "timeframe": {"type": "string", "optional": True},
                "title": {"type": "string", "optional": True},
            },
            self.add_panel,
            mutating=True,
        )
        self._add(
            "remove_panel",
            "Remove a panel from a workspace.",
            {
                "panel_id": {"type": "string"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.remove_panel,
            mutating=True,
        )
        self._add(
            "move_panel",
            "Move a panel to a new grid position, keeping its size.",
            {
                "panel_id": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.move_panel,
            mutating=True,
        )
        self._add(
            "resize_panel",
            "Resize a panel, keeping its position.",
            {
                "panel_id": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.resize_panel,
            mutating=True,
        )
        self._add(
            "set_panel_setting",
            "Change one setting on a panel - a chart's symbol or timeframe, a table's "
            "filter. Use add_indicator for indicators.",
            {
                "panel_id": {"type": "string"},
                "key": {"type": "string"},
                "value": {"type": "string"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.set_panel_setting,
            mutating=True,
        )
        self._add(
            "add_indicator",
            "Add an indicator to a chart panel. Refuses on a panel that is not a chart "
            "rather than storing a setting nothing will read.",
            {
                "panel_id": {"type": "string"},
                "indicator": {"type": "string", "description": "e.g. vwap, ema, atr"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.add_indicator,
            mutating=True,
        )
        self._add(
            "link_panels",
            "Put panels in a link group so they follow each other's symbol and "
            "timeframe. Pass no group to unlink them.",
            {
                "panel_ids": {"type": "array", "description": "Panel ids to link together."},
                "group": {"type": "string", "optional": True},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.link_panels,
            mutating=True,
        )

    # ── workspace implementations ────────────────────────────────────────────
    def _workspace(self, workspace_id: str | None) -> Workspace:
        """The named workspace, or the open one.

        Refuses rather than creating something. "No workspace is open" is a true
        statement the caller can act on; silently conjuring one would make
        `add_panel` quietly build a desk nobody asked for.
        """
        if workspace_id:
            found = self.workspaces.get(_str(workspace_id, "workspace_id", limit=120))
            if found is None:
                raise ActionError(f"No workspace '{workspace_id}'.")
            return found
        active = self.workspaces.active()
        if active is None:
            raise ActionError(
                "No workspace is open. Create one with create_workspace, or pass "
                "workspace_id to say which you mean."
            )
        return active

    @staticmethod
    def _panel_view(panel: Panel) -> dict[str, Any]:
        return {
            "panel_id": panel.panel_id,
            "kind": panel.kind.value,
            "title": panel.display_title(),
            "x": panel.x,
            "y": panel.y,
            "width": panel.width,
            "height": panel.height,
            "settings": panel.settings,
            "link_group": panel.link_group,
            "collapsed": panel.collapsed,
        }

    def _view(self, workspace: Workspace) -> dict[str, Any]:
        return {
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "template_key": workspace.template_key,
            "updated_at": workspace.updated_at.isoformat(),
            "panels": [self._panel_view(p) for p in workspace.panels],
            "description": workspace.description,
            "icon": workspace.icon,
            "kind": str(workspace.kind),
            "pinned": workspace.pinned,
            "mode": workspace.mode,
            "campaign_ids": list(workspace.campaign_ids),
            "account_ids": list(workspace.account_ids),
            # The rail, resolved: a workspace with no sidebar of its own shows
            # the one its mode always had rather than an empty list.
            "sidebar": workspace.rail().as_dict(),
            "sidebar_is_custom": workspace.sidebar is not None,
        }

    def _current_actor(self) -> str:
        """Who is running the action in progress, for version attribution."""
        return str(getattr(self._actor_local, "actor", Actor.HUMAN))

    def _save_workspace(self, workspace: Workspace, summary: str = "") -> dict[str, Any]:
        self.workspaces.save(workspace, summary=summary, actor=self._current_actor())
        return self._view(workspace)

    def list_workspace_templates(self) -> dict[str, Any]:
        items = catalogue()
        return {"count": len(items), "templates": items}

    def list_workspaces(self) -> dict[str, Any]:
        rows = self.workspaces.summaries()
        return {
            "count": len(rows),
            "active": self.workspaces.active_id(),
            # Which one opens on a cold start. Travels with the list because a
            # switcher that cannot show it makes "set as default" a button with
            # no visible effect.
            "default": self.workspaces.default_id(),
            "workspaces": rows,
        }

    def describe_workspace(self, workspace_id: str | None = None) -> dict[str, Any]:
        return self._view(self._workspace(workspace_id))

    def create_workspace(
        self,
        name: str,
        template_key: str | None = None,
        activate: bool = True,
        description: str = "",
        icon: str = "",
        mode: str = "",
        sidebar_items: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a workspace, optionally seeded and optionally with a rail.

        ``sidebar_items`` is what makes "build me a workspace for NQ research
        and prop trading" one call rather than a sequence the caller has to get
        right. Every route is checked against the destination catalogue, so a
        workspace cannot be created carrying a link to nothing.
        """
        label = _str(name, "name", limit=120)
        panels: tuple[Panel, ...] = ()
        key = None
        if template_key:
            key = _str(template_key, "template_key", limit=60, lower=True)
            try:
                preset = template(key)
            except KeyError as exc:
                raise ActionError(str(exc)) from exc
            panels = preset.panels
        rail: Sidebar | None = None
        if sidebar_items:
            rail = Sidebar()
            for route in sidebar_items:
                cleaned = _str(route, "sidebar_items", limit=60, lower=True)
                if not sidebar_known(cleaned):
                    raise ActionError(
                        f"'{cleaned}' is not a destination. Valid: "
                        + ", ".join(sorted(SIDEBAR_CATALOGUE))
                    )
                group = SIDEBAR_CATALOGUE[cleaned].group
                group_id = group.lower().replace(" ", "_").replace("&", "and")[:60]
                if rail.group(group_id) is None:
                    rail = rail.with_group(group_id, group)
                rail = rail.with_item(cleaned, group_id=group_id)
        workspace = self.workspaces.create(
            label,
            panels=panels,
            template_key=key,
            description=_str(description, "description", limit=600) if description else "",
            icon=_str(icon, "icon", limit=8) if icon else "",
            sidebar=rail,
            mode=_str(mode, "mode", limit=40, lower=True) if mode else "",
        )
        if activate:
            self.workspaces.set_active(workspace.workspace_id)
        return self._view(workspace)

    # ── the sidebar ──────────────────────────────────────────────────────────
    # These are the same operations the interface calls. There is deliberately
    # no AI-only path: "add the agent monitor to this workspace" typed by a
    # person and asked of the assistant go through this identical registry, so
    # a capability the interface does not have is not one the agent can invent.

    def list_sidebar_destinations(self) -> dict[str, Any]:
        """Everything that can go in a rail, from every mode.

        The union, on purpose. The whole complaint this answers is that
        navigation used to be a property of the mode, so reaching a research
        screen from a prop workspace meant leaving the prop workspace.
        """
        items = sidebar_destinations()
        return {"count": len(items), "destinations": items}

    def describe_sidebar(self, workspace_id: str | None = None) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        return {
            "workspace_id": workspace.workspace_id,
            "custom": workspace.sidebar is not None,
            **workspace.rail().as_dict(),
        }

    def _rail_edit(
        self, workspace_id: str | None, operation: str, summary: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        try:
            updated = getattr(workspace, operation)(*args, **kwargs)
        except (SidebarError, ValueError) as exc:
            raise ActionError(str(exc)) from exc
        return self._save_workspace(updated, summary)

    def add_sidebar_item(
        self,
        route: str,
        workspace_id: str | None = None,
        group_id: str | None = None,
        label: str = "",
        position: int | None = None,
    ) -> dict[str, Any]:
        cleaned = _str(route, "route", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "adding_sidebar_item",
            f"added '{cleaned}' to the sidebar",
            cleaned,
            group_id=group_id,
            label=label[:60],
            position=position,
        )

    def remove_sidebar_item(
        self, route: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        cleaned = _str(route, "route", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "removing_sidebar_item",
            f"removed '{cleaned}' from the sidebar",
            cleaned,
        )

    def move_sidebar_item(
        self,
        route: str,
        group_id: str,
        position: int | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        cleaned = _str(route, "route", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "moving_sidebar_item",
            f"moved '{cleaned}' to '{group_id}'",
            cleaned,
            group_id=group_id,
            position=position,
        )

    def rename_sidebar_item(
        self, route: str, label: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        cleaned = _str(route, "route", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "renaming_sidebar_item",
            f"renamed '{cleaned}'",
            cleaned,
            _str(label, "label", limit=60),
        )

    def pin_sidebar_item(
        self, route: str, pinned: bool = True, workspace_id: str | None = None
    ) -> dict[str, Any]:
        cleaned = _str(route, "route", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "pinning_sidebar_item",
            f"{'pinned' if pinned else 'unpinned'} '{cleaned}'",
            cleaned,
            bool(pinned),
        )

    def hide_sidebar_item(
        self, route: str, hidden: bool = True, workspace_id: str | None = None
    ) -> dict[str, Any]:
        cleaned = _str(route, "route", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "hiding_sidebar_item",
            f"{'hid' if hidden else 'showed'} '{cleaned}'",
            cleaned,
            bool(hidden),
        )

    def add_sidebar_group(
        self,
        group_id: str,
        label: str,
        position: int | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        cleaned = _str(group_id, "group_id", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "adding_sidebar_group",
            f"added the '{label}' group",
            cleaned,
            _str(label, "label", limit=60),
            position=position,
        )

    def remove_sidebar_group(
        self, group_id: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        cleaned = _str(group_id, "group_id", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "removing_sidebar_group",
            f"removed the '{cleaned}' group",
            cleaned,
        )

    def rename_sidebar_group(
        self, group_id: str, label: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        cleaned = _str(group_id, "group_id", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "renaming_sidebar_group",
            f"renamed the '{cleaned}' group",
            cleaned,
            _str(label, "label", limit=60),
        )

    def collapse_sidebar_group(
        self, group_id: str, collapsed: bool = True, workspace_id: str | None = None
    ) -> dict[str, Any]:
        cleaned = _str(group_id, "group_id", limit=60, lower=True)
        return self._rail_edit(
            workspace_id,
            "collapsing_sidebar_group",
            f"{'collapsed' if collapsed else 'expanded'} '{cleaned}'",
            cleaned,
            bool(collapsed),
        )

    def reorder_sidebar_groups(
        self, group_ids: list[str], workspace_id: str | None = None
    ) -> dict[str, Any]:
        return self._rail_edit(
            workspace_id,
            "reordering_sidebar_groups",
            "reordered the sidebar",
            [_str(g, "group_ids", limit=60, lower=True) for g in group_ids],
        )

    def reset_sidebar(self, workspace_id: str | None = None) -> dict[str, Any]:
        """Throw away a custom rail and rebuild the mode's default."""
        return self._rail_edit(
            workspace_id, "with_default_sidebar", "restored the default sidebar"
        )

    def link_campaign_to_workspace(
        self, campaign_id: str, linked: bool = True, workspace_id: str | None = None
    ) -> dict[str, Any]:
        cleaned = _str(campaign_id, "campaign_id", limit=80)
        workspace = self._workspace(workspace_id)
        return self._save_workspace(
            workspace.linking_campaign(cleaned, bool(linked)),
            f"{'linked' if linked else 'unlinked'} campaign {cleaned}",
        )

    def link_account_to_workspace(
        self, account_id: str, linked: bool = True, workspace_id: str | None = None
    ) -> dict[str, Any]:
        cleaned = _str(account_id, "account_id", limit=80)
        workspace = self._workspace(workspace_id)
        return self._save_workspace(
            workspace.linking_account(cleaned, bool(linked)),
            f"{'linked' if linked else 'unlinked'} account {cleaned}",
        )

    def describe_this_workspace(
        self, description: str = "", icon: str = "", workspace_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        return self._save_workspace(
            workspace.described(description[:600], icon[:8]), "updated the description"
        )

    def pin_workspace(
        self, pinned: bool = True, workspace_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        return self._save_workspace(
            workspace.pinning(bool(pinned)),
            f"{'pinned' if pinned else 'unpinned'} the workspace",
        )

    def open_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        self.workspaces.set_active(workspace.workspace_id)
        return self._view(workspace)

    def rename_workspace(self, workspace_id: str, name: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        label = _str(name, "name", limit=120)
        return self._save_workspace(
            workspace.renamed(label), f"renamed from '{workspace.name}' to '{label}'"
        )

    def clone_workspace(self, workspace_id: str, name: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        clone = self.workspaces.clone(workspace.workspace_id, _str(name, "name", limit=120))
        return self._view(clone)

    def set_default_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Choose which workspace opens on a cold start.

        Deliberately not the same as opening one: a workspace opened once to
        check something must not become the one that greets the operator every
        morning.
        """
        workspace = self._workspace(workspace_id)
        self.workspaces.set_default(workspace.workspace_id)
        return {**self._view(workspace), "is_default": True}

    def export_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        return {
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "document": self.workspaces.export(workspace.workspace_id),
        }

    def import_workspace(self, document: Any, name: str | None = None) -> dict[str, Any]:
        try:
            imported = self.workspaces.import_workspace(
                document,
                name=_str(name, "name", limit=120) if name else None,
                actor=self._current_actor(),
            )
        except WorkspaceImportError as exc:
            raise ActionError(str(exc)) from exc
        return self._view(imported)

    def workspace_history(self, workspace_id: str | None = None) -> dict[str, Any]:
        """Every saved state of one workspace, newest first."""
        workspace = self._workspace(workspace_id)
        versions = self.workspaces.versions(workspace.workspace_id)
        return {
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "count": len(versions),
            "versions": versions,
        }

    def restore_workspace_version(
        self, version: int, workspace_id: str | None = None
    ) -> dict[str, Any]:
        """Put a previous state back, as a new version on top of the history."""
        workspace = self._workspace(workspace_id)
        try:
            restored = self.workspaces.restore(
                workspace.workspace_id, int(version), actor=self._current_actor()
            )
        except KeyError as exc:
            raise ActionError(str(exc)) from exc
        return self._view(restored)

    def duplicate_workspace_version(
        self, version: int, name: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        try:
            copy = self.workspaces.duplicate_version(
                workspace.workspace_id, int(version), _str(name, "name", limit=120)
            )
        except KeyError as exc:
            raise ActionError(str(exc)) from exc
        return self._view(copy)

    def collapse_panel(
        self, panel_id: str, collapsed: bool = True, workspace_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        try:
            updated = workspace.collapsing(_str(panel_id, "panel_id", limit=80), bool(collapsed))
        except KeyError as exc:
            raise ActionError(str(exc)) from exc
        return self._save_workspace(
            updated, f"{'collapsed' if collapsed else 'expanded'} {panel_id}"
        )

    def reorder_panel(
        self, panel_id: str, position: int, workspace_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        try:
            updated = workspace.reordered(_str(panel_id, "panel_id", limit=80), int(position))
        except KeyError as exc:
            raise ActionError(str(exc)) from exc
        return self._save_workspace(updated, f"moved {panel_id} to position {position}")

    def delete_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        removed = self.workspaces.delete(workspace.workspace_id)
        if removed and self.modes is not None:
            # Every mode pointing at it, not only the open one. A mode left
            # holding a pointer to a deleted layout opens on nothing until it is
            # re-seeded, and the operator has no way to know why.
            self.modes.forget_workspace(workspace.workspace_id)
        return {"workspace_id": workspace.workspace_id, "deleted": removed}

    def add_panel(
        self,
        kind: str,
        workspace_id: str | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        try:
            panel_kind = PanelKind(_str(kind, "kind", limit=40, lower=True))
        except ValueError as exc:
            raise ActionError(
                f"'{kind}' is not a panel kind. Available: "
                f"{', '.join(sorted(k.value for k in PanelKind))}."
            ) from exc

        settings: dict[str, Any] = {}
        if symbol:
            settings["symbol"] = _str(symbol, "symbol", limit=24).upper()
        if timeframe:
            settings["timeframe"] = _str(timeframe, "timeframe", limit=12, lower=True)

        # Below everything already placed, so a new panel never lands on top of
        # one the operator is using.
        default_y = max((p.y + p.height for p in workspace.panels), default=0)
        try:
            panel = Panel(
                panel_id=new_panel_id(panel_kind, tuple(p.panel_id for p in workspace.panels)),
                kind=panel_kind,
                title=_str(title, "title", limit=80) if title else "",
                x=_bounded(x, 0, 0, GRID_COLUMNS - 1, "x"),
                y=_bounded(y, default_y, 0, MAX_ROWS - 1, "y"),
                width=_bounded(width, 6, 1, GRID_COLUMNS, "width"),
                height=_bounded(height, 6, 1, MAX_ROWS, "height"),
                settings=settings,
            )
            updated = workspace.with_panel(panel)
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
        return self._save_workspace(updated, f"added {panel.panel_id}")

    def remove_panel(self, panel_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        resolved = self._panel_id(workspace, panel_id)
        return self._save_workspace(workspace.without_panel(resolved), f"removed {resolved}")

    def move_panel(
        self, panel_id: str, x: int, y: int, workspace_id: str | None = None
    ) -> dict[str, Any]:
        return self._reshape(workspace_id, panel_id, x=x, y=y)

    def resize_panel(
        self, panel_id: str, width: int, height: int, workspace_id: str | None = None
    ) -> dict[str, Any]:
        return self._reshape(workspace_id, panel_id, width=width, height=height)

    @staticmethod
    def _panel_id(workspace: Workspace, panel_id: str) -> str:
        """Check the panel exists, and turn a miss into a refusal with the list."""
        wanted = _str(panel_id, "panel_id", limit=60)
        if workspace.panel(wanted) is None:
            known = ", ".join(p.panel_id for p in workspace.panels) or "none"
            raise ActionError(f"No panel '{wanted}' in this workspace. Panels: {known}.")
        return wanted

    def _reshape(self, workspace_id: str | None, panel_id: str, **geometry: int) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        panel = workspace.require(self._panel_id(workspace, panel_id))
        try:
            moved = panel.model_copy(update={k: int(v) for k, v in geometry.items()})
            # Re-validate: model_copy skips validators, so a panel that runs off
            # the grid would otherwise be stored and only fail on the way out.
            moved = Panel.model_validate(moved.model_dump())
        except (ValueError, TypeError) as exc:
            raise ActionError(f"That geometry does not fit the grid: {exc}") from exc
        return self._save_workspace(
            workspace.replacing_panel(moved),
            f"moved {moved.panel_id} to {moved.width}x{moved.height} at ({moved.x}, {moved.y})",
        )

    def set_panel_setting(
        self, panel_id: str, key: str, value: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        panel = workspace.require(self._panel_id(workspace, panel_id))
        name = _str(key, "key", limit=40, lower=True)
        resolved = _str(value, "value", limit=200)
        settings = {**panel.settings, name: resolved}
        return self._save_workspace(
            workspace.replacing_panel(panel.model_copy(update={"settings": settings})),
            f"set {panel.panel_id} {name} to {resolved}",
        )

    def add_indicator(
        self, panel_id: str, indicator: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        panel = workspace.require(self._panel_id(workspace, panel_id))
        if panel.kind is not PanelKind.CHART:
            raise ActionError(
                f"'{panel.panel_id}' is a {panel.kind} panel, not a chart. An indicator "
                "only means something on a chart."
            )
        name = _str(indicator, "indicator", limit=40, lower=True)
        existing = list(panel.settings.get("indicators", []))
        if name not in existing:
            existing.append(name)
        settings = {**panel.settings, "indicators": existing}
        return self._save_workspace(
            workspace.replacing_panel(panel.model_copy(update={"settings": settings})),
            f"added the {name} indicator to {panel.panel_id}",
        )

    def link_panels(
        self,
        panel_ids: list[str],
        group: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if not isinstance(panel_ids, list) or not panel_ids:
            raise ActionError("'panel_ids' must be a non-empty list of panel ids.")
        ids = tuple(self._panel_id(workspace, item) for item in panel_ids)
        name = _str(group, "group", limit=40, lower=True) if group else None
        return self._save_workspace(
            workspace.linked(name, ids),
            f"{'linked' if name else 'unlinked'} {', '.join(ids)}"
            + (f" as '{name}'" if name else ""),
        )


    def _register_builder(self) -> None:
        self._add(
            "build_workspace",
            "Build a workspace from a stated purpose: which markets, how they are "
            "traded, which sessions, what research is wanted. Returns the layout and "
            "an explanation of why each panel is there. The result is an ordinary "
            "workspace - every panel can be removed and any other added, and the "
            "profile is recorded as what was asked for rather than consulted later "
            "to decide what is allowed.",
            {
                "name": {"type": "string", "description": "What to call the workspace."},
                "purpose": {
                    "type": "string",
                    "optional": True,
                    "description": "e.g. prop_trading, quant_research, strategy_development.",
                },
                "markets": {
                    "type": "array",
                    "optional": True,
                    "description": "Instrument roots, e.g. ['NQ', 'ES']. Charts are only "
                    "created for roots this build holds archives for.",
                },
                "style": {
                    "type": "string",
                    "optional": True,
                    "description": "scalp, intraday, swing or position. Sets the timeframe.",
                },
                "sessions": {"type": "array", "optional": True},
                "research": {
                    "type": "array",
                    "optional": True,
                    "description": "Mechanisms of interest, e.g. ['breakout', 'momentum'].",
                },
                "risk": {"type": "string", "optional": True, "description": "e.g. prop_firm."},
                "datasets": {"type": "array", "optional": True},
                "preferred_export": {"type": "string", "optional": True},
                "activate": {"type": "boolean", "optional": True},
            },
            self.build_workspace,
            mutating=True,
        )

    def build_workspace(
        self,
        name: str,
        purpose: str | None = None,
        markets: list[str] | None = None,
        style: str | None = None,
        sessions: list[str] | None = None,
        research: list[str] | None = None,
        risk: str | None = None,
        datasets: list[str] | None = None,
        preferred_export: str | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Construct a workspace from a profile.

        The language understanding happens before this call; the construction is
        deterministic and happens here. That split is what keeps the product
        usable with no model configured, and what makes the result reproducible:
        the same profile always builds the same workspace, so "build me another
        of these for ES" is a sentence with a checkable answer.
        """
        profile = WorkspaceProfile(
            purpose=_str(purpose, "purpose", limit=60) if purpose else "",
            markets=tuple(_str(m, "markets", limit=24).upper() for m in (markets or [])),
            style=_str(style, "style", limit=40, lower=True) if style else "",
            sessions=tuple(_str(s, "sessions", limit=40) for s in (sessions or [])),
            research=tuple(_str(r, "research", limit=60, lower=True) for r in (research or [])),
            risk=_str(risk, "risk", limit=40, lower=True) if risk else "",
            datasets=tuple(_str(d, "datasets", limit=60, lower=True) for d in (datasets or [])),
            preferred_export=(
                _str(preferred_export, "preferred_export", limit=40) if preferred_export else ""
            ),
        )
        panels = layout_for(profile)
        workspace = self.workspaces.create(
            _str(name, "name", limit=120), panels=panels, profile=profile
        )
        if activate:
            self.workspaces.set_active(workspace.workspace_id)

        unknown = tuple(
            market
            for market in profile.markets
            if market not in markets_for(profile) and market
        )
        return {
            **self._view(workspace),
            "profile": profile.model_dump(mode="json"),
            "explanation": describe(profile, panels),
            # Named rather than dropped in silence: a chart for an instrument
            # with no archive would have nothing to draw, and the operator
            # should know which of their markets did not get one.
            "markets_without_archives": list(unknown),
        }


    def attach_validation(self, runner: Callable[..., dict[str, Any]]) -> None:
        self.validation = runner

    def _register_validation(self) -> None:
        self._add(
            "validate_strategy",
            "Run the walk-forward, CSCV and CPCV stack over a strategy's parameter grid "
            "and store the evidence the judge reads. Produces evidence, never a verdict.",
            {
                "strategy_id": {"type": "string"},
                "dataset": {"type": "string", "optional": True},
                "bar_count": {"type": "integer", "optional": True},
                "max_trials": {
                    "type": "integer",
                    "optional": True,
                    "description": "Configurations to search. Every trial is a full backtest.",
                },
                "folds": {"type": "integer", "optional": True},
            },
            self.validate_strategy,
            mutating=True,
        )

    def validate_strategy(
        self,
        strategy_id: Any,
        dataset: Any = None,
        bar_count: Any = None,
        max_trials: Any = None,
        folds: Any = None,
    ) -> dict[str, Any]:
        if self.validation is None:
            raise ActionError(
                "the validation stack is not attached in this process, so validation "
                "cannot be run from here"
            )
        options: dict[str, Any] = {}
        if dataset is not None:
            options["dataset"] = _str(dataset, "dataset")
        if bar_count is not None:
            options["bar_count"] = _bounded(bar_count, 60_000, 2_000, 2_000_000, "bar_count")
        if max_trials is not None:
            options["max_trials"] = _bounded(max_trials, 16, 2, 120, "max_trials")
        if folds is not None:
            options["folds"] = _bounded(folds, 6, 2, 20, "folds")
        try:
            return self.validation(_str(strategy_id, "strategy_id"), **options)
        except HTTPException as exc:
            # The route's refusals are written for a person and say which
            # requirement was not met. Reusing them keeps the agent's answer and
            # the interface's answer the same sentence.
            detail = exc.detail
            reason = (
                detail.get("detail") or detail.get("code")
                if isinstance(detail, dict)
                else str(detail)
            )
            raise ActionError(str(reason)) from exc
        except ValidationError as exc:
            raise ActionError(f"invalid validation options: {exc.errors()[0]['msg']}") from exc

    # ── modes ────────────────────────────────────────────────────────────────
    def _register_modes(self) -> None:
        self._add(
            "list_modes",
            "The four operating environments, their purpose, their sections and the "
            "workspace each one opens with.",
            {},
            self.list_modes,
        )
        self._add(
            "current_mode",
            "Which mode is open, on which stance, and what an AI actor may do inside it.",
            {},
            self.current_mode,
        )
        self._add(
            "enter_mode",
            "Open an operating mode, seeding its default workspace the first time. "
            "Protected: an AI actor changing the mode would be changing its own "
            "permissions, so this is refused to one in every mode.",
            {
                "mode": {
                    "type": "string",
                    "description": "normal, prop_firm, ai or hedge_fund.",
                },
                "stance": {
                    "type": "string",
                    "optional": True,
                    "description": "Hedge Fund only: human_in_the_loop or autonomous.",
                },
            },
            self.enter_mode,
            mutating=True,
            protected=True,
        )
        self._add(
            "set_stance",
            "Switch Hedge Fund mode between human-in-the-loop and autonomous. Protected "
            "for the same reason as enter_mode.",
            {"stance": {"type": "string", "description": "human_in_the_loop or autonomous."}},
            self.set_stance,
            mutating=True,
            protected=True,
        )
        self._add(
            "leave_mode",
            "Return to the mode-selection screen. Nothing is forgotten: each mode keeps "
            "its own layout and stance.",
            {},
            self.leave_mode,
            mutating=True,
            protected=True,
        )

    def list_modes(self) -> dict[str, Any]:
        return {"modes": mode_catalogue()}

    def current_mode(self) -> dict[str, Any]:
        mode, stance = self.context()
        session = self.modes.session().as_dict() if self.modes else {
            "mode": None, "stance": None, "workspace_id": None
        }
        return {
            "session": session,
            "descriptor": mode_descriptor(mode).as_dict(),
            "policy": summarise(mode, stance),
            "policy_applies": session["mode"] is not None,
        }

    def _require_modes(self) -> ModeStore:
        if self.modes is None:
            raise ActionError("no mode store is configured in this process")
        return self.modes

    def enter_mode(self, mode: Any, stance: Any = None) -> dict[str, Any]:
        store = self._require_modes()
        try:
            chosen = WorkspaceMode(_str(mode, "mode", lower=True))
        except ValueError:
            valid = ", ".join(m.value for m in MODE_ORDER)
            raise ActionError(f"No mode '{mode}'. Modes: {valid}.") from None
        try:
            resolved = parse_stance(chosen, None if stance is None else str(stance))
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
        session = store.enter(chosen, resolved)
        workspace = self._seed_workspace(chosen, store)
        return {
            "session": store.session().as_dict(),
            "descriptor": mode_descriptor(chosen).as_dict(),
            "workspace": None if workspace is None else self._view(workspace),
            "policy": summarise(chosen, session.stance),
        }

    def _seed_workspace(self, mode: WorkspaceMode, store: ModeStore) -> Workspace | None:
        """Open this mode's layout, creating it from the template on first entry.

        The pointer is checked against the workspace store rather than trusted:
        a layout deleted while another mode was open leaves a pointer at nothing,
        and re-seeding is the recovery. Silently opening whatever workspace
        happened to be active would be the other option, and it is how a fund
        layout gets replaced by a chart.
        """
        remembered = store.workspace_for(mode)
        if remembered and self.workspaces.get(remembered) is not None:
            self.workspaces.set_active(remembered)
            return self.workspaces.get(remembered)
        descriptor = mode_descriptor(mode)
        try:
            seed = template(descriptor.workspace_template)
        except KeyError:
            return None
        created = self.workspaces.create(
            f"{descriptor.name} Workspace", panels=seed.panels, template_key=seed.key
        )
        self.workspaces.set_active(created.workspace_id)
        store.remember_workspace(mode, created.workspace_id)
        return created

    def set_stance(self, stance: Any) -> dict[str, Any]:
        store = self._require_modes()
        session = store.session()
        if session.mode is None:
            raise ActionError("no mode is open, so there is no stance to set")
        try:
            chosen = Stance(_str(stance, "stance", lower=True))
            resolved = store.set_stance(session.mode, chosen)
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
        return {
            "session": store.session().as_dict(),
            "policy": summarise(session.mode, resolved),
        }

    def leave_mode(self) -> dict[str, Any]:
        return {"session": self._require_modes().leave().as_dict()}

    # ── prop accounts ────────────────────────────────────────────────────────
    def _register_prop(self) -> None:
        self._add(
            "list_prop_accounts",
            "Configured funded-account rule sets, and which one is selected.",
            {},
            self.list_prop_accounts,
        )
        self._add(
            "prop_account_status",
            "Where the selected account stands against its own rules right now: every "
            "rule, its buffer, and whether trading is permitted.",
            {
                "account_id": {"type": "string", "optional": True},
                "proposed_contracts": {
                    "type": "integer",
                    "optional": True,
                    "description": "Ask the question one trade ahead.",
                },
            },
            self.prop_account_status,
        )
        self._add(
            "assess_prop_account",
            "Replay a strategy's backtested trades through an account's rules and report "
            "whether it would have survived. Computes; records nothing.",
            {
                "strategy_id": {"type": "string"},
                "account_id": {"type": "string", "optional": True},
            },
            self.assess_prop_account,
            mutating=True,
        )
        self._add(
            "create_prop_account",
            "Configure a funded or evaluation account from a rule set. Protected: the "
            "rule engine is authoritative in Prop Firm mode and AI may not write to it.",
            {"rules": {"type": "object", "description": "An AccountRules document."}},
            self.create_prop_account,
            mutating=True,
            protected=True,
        )
        self._add(
            "update_prop_rules",
            "Replace an account's rule set. Protected for the same reason.",
            {
                "account_id": {"type": "string"},
                "rules": {"type": "object"},
            },
            self.update_prop_rules,
            mutating=True,
            protected=True,
        )
        self._add(
            "record_prop_state",
            "Record what the account looks like now. Protected: a balance an AI could "
            "write is a rule engine an AI could satisfy.",
            {
                "account_id": {"type": "string"},
                "state": {"type": "object", "description": "An AccountState document."},
                "source": {
                    "type": "string",
                    "description": "Where the numbers came from, in words.",
                },
            },
            self.record_prop_state,
            mutating=True,
            protected=True,
        )
        self._add(
            "select_prop_account",
            "Choose which configured account the Prop Firm workspace shows.",
            {"account_id": {"type": "string"}},
            self.select_prop_account,
            mutating=True,
            protected=True,
        )

    def _require_accounts(self) -> Any:
        if self.prop_accounts is None:
            raise ActionError("no prop account store is configured in this process")
        return self.prop_accounts

    def list_prop_accounts(self) -> dict[str, Any]:
        store = self._require_accounts()
        return {
            "accounts": [account.as_dict() for account in store.all_accounts()],
            "selected": store.selected_id(),
        }

    def _account(self, account_id: Any) -> Any:
        store = self._require_accounts()
        if account_id is None:
            account = store.selected()
            if account is None:
                raise ActionError(
                    "no prop account is selected. Configure one first: the rule engine "
                    "has nothing to evaluate against."
                )
            return account
        account = store.get(_str(account_id, "account_id"))
        if account is None:
            raise ActionError(f"No prop account '{account_id}'.")
        return account

    def prop_account_status(
        self, account_id: Any = None, proposed_contracts: Any = None
    ) -> dict[str, Any]:
        store = self._require_accounts()
        account = self._account(account_id)
        latest = store.latest(account.account_id)
        if latest is None:
            return {
                "account": account.as_dict(),
                "assessment": None,
                # Not a zeroed account. Nobody has said what this account holds,
                # and rendering it flat at its starting balance would show a
                # comfortable drawdown buffer for a position that might be open.
                "reason": (
                    "no state has been recorded for this account, so there is nothing to "
                    "assess. Record a balance, or derive one from a strategy's trades."
                ),
            }
        state, source = latest
        assessment = prop_assess(
            account.rules,
            state,
            proposed_contracts=_bounded(proposed_contracts, 0, 0, 10_000, "proposed_contracts"),
        )
        return {
            "account": account.as_dict(),
            "state": state.model_dump(mode="json"),
            "state_source": source,
            "assessment": assessment.as_dict(),
        }

    def assess_prop_account(self, strategy_id: Any, account_id: Any = None) -> dict[str, Any]:
        account = self._account(account_id)
        wanted = _str(strategy_id, "strategy_id")
        latest = self.store.latest(wanted)
        if latest is None:
            raise ActionError(f"'{wanted}' has no backtest to replay.")
        trades = [
            ClosedTrade(
                exit_time=datetime.fromisoformat(str(trade["exit_time"])),
                pnl=float(trade["net_pnl"]),
                contracts=1,
            )
            for trade in latest.get("trades", [])
            if trade.get("exit_time")
        ]
        if not trades:
            raise ActionError(
                f"'{wanted}' produced no trades, so there is no path to replay through "
                "the account rules."
            )
        state = state_from_trades(account.rules, trades)
        assessment = prop_assess(account.rules, state)
        return {
            "strategy_id": wanted,
            "account": account.as_dict(),
            "state": state.model_dump(mode="json"),
            "assessment": assessment.as_dict(),
            "limitations": [
                "Derived from closed backtested trades: there is no open position and no "
                "unrealised P&L in this replay, so intraday rules are evaluated only at "
                "the points where a trade closed.",
                "Backtest fills are modelled, not calibrated against a broker.",
            ],
        }

    def create_prop_account(self, rules: Any) -> dict[str, Any]:
        store = self._require_accounts()
        return {"account": store.create(_parse_rules(rules)).as_dict()}

    def update_prop_rules(self, account_id: Any, rules: Any) -> dict[str, Any]:
        store = self._require_accounts()
        account_id = _str(account_id, "account_id")
        try:
            return {"account": store.update(account_id, _parse_rules(rules)).as_dict()}
        except KeyError as exc:
            raise ActionError(f"No prop account '{account_id}'.") from exc

    def record_prop_state(self, account_id: Any, state: Any, source: Any) -> dict[str, Any]:
        store = self._require_accounts()
        if not isinstance(state, dict):
            raise ActionError("'state' must be an AccountState object.")
        try:
            parsed = AccountState.model_validate(state)
        except ValidationError as exc:
            raise ActionError(
                f"That is not a valid account state: {exc.errors()[0]['msg']}"
            ) from exc
        account_id = _str(account_id, "account_id")
        try:
            store.record(account_id, parsed, source=_str(source, "source"))
        except (KeyError, ValueError) as exc:
            raise ActionError(str(exc)) from exc
        return self.prop_account_status(account_id)

    def select_prop_account(self, account_id: Any) -> dict[str, Any]:
        store = self._require_accounts()
        account_id = _str(account_id, "account_id")
        try:
            store.select(account_id)
        except KeyError as exc:
            raise ActionError(f"No prop account '{account_id}'.") from exc
        return {"selected": account_id}


    # ── the prop desk ────────────────────────────────────────────────────────
    def _register_prop_desk(self) -> None:
        """Multi-account execution, as bounded verbs.

        Everything that *writes* here is `protected`, which means
        `forge.modes.permissions` denies it to an AI actor in every mode and on
        every stance. That is deliberate and it is the same rule the prop rule
        engine already carries: a connection, a firm-permission policy, a copy
        group and an allocation are all controls, and a control an agent can set
        is not a control.

        The reads are open, because an assistant that cannot look cannot report
        honestly on what it found. `propdesk_plan_allocation` is a read in this
        sense: it computes a proposal and writes nothing, and the proposal it
        computes is bounded by the deterministic feasible set whoever asked for
        it — a recommendation an agent produces cannot introduce a pairing the
        judge, the firm policy and the account's rule engine did not already
        permit.
        """
        self._add(
            "propdesk_providers",
            "The execution providers, the platforms that alias onto them, and which "
            "of them this build has a live connector for. Currently: the simulator only.",
            {},
            self.propdesk_providers,
        )
        self._add(
            "propdesk_connections",
            "Broker connections, the accounts they expose, and the health of each.",
            {},
            self.propdesk_connections,
        )
        self._add(
            "propdesk_create_connection",
            "Record a broker connection. Protected: a connection is order authority "
            "over every account under it.",
            {
                "provider": {"type": "string"},
                "label": {"type": "string"},
                "environment": {"type": "string", "optional": True},
                "credential_ref": {"type": "string", "optional": True},
                "platform": {"type": "string", "optional": True},
            },
            self.propdesk_create_connection,
            mutating=True,
            risk=ActionRisk.HIGH,
            protected=True,
        )
        self._add(
            "propdesk_connect",
            "Authenticate a connection and discover its accounts. Protected.",
            {"connection_id": {"type": "string"}},
            self.propdesk_connect,
            mutating=True,
            risk=ActionRisk.HIGH,
            protected=True,
        )
        self._add(
            "propdesk_disconnect",
            "Close a connection. Protected, though it only ever reduces reach.",
            {"connection_id": {"type": "string"}},
            self.propdesk_disconnect,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_policies",
            "The firm-permission policies recorded for accounts, and the questions "
            "each one answers. AlgoForge ships no firm's rules.",
            {},
            self.propdesk_policies,
        )
        self._add(
            "propdesk_save_policy",
            "Record what an account programme permits. Protected: a permission an AI "
            "could write is a permission an AI could grant itself.",
            {"policy": {"type": "object"}},
            self.propdesk_save_policy,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_link_policy",
            "Attach a recorded programme policy to an account. Protected.",
            {
                "account_uid": {"type": "string"},
                "policy_id": {"type": "string", "optional": True},
            },
            self.propdesk_link_policy,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_link_rules",
            "Attach a funded-account rule set to a desk account, so its contract can "
            "be evaluated. Protected.",
            {
                "account_uid": {"type": "string"},
                "prop_account_id": {"type": "string", "optional": True},
            },
            self.propdesk_link_rules,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_groups",
            "Copy groups: leaders, followers, sizing and policies.",
            {},
            self.propdesk_groups,
        )
        self._add(
            "propdesk_create_group",
            "Create a copy group. Needs an attestation that every account in it has "
            "one owner. Protected.",
            {
                "name": {"type": "string"},
                "leader_account_uid": {"type": "string"},
                "attested_by": {"type": "string"},
            },
            self.propdesk_create_group,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_add_follower",
            "Add a follower to a copy group, with its sizing policy. Protected.",
            {
                "group_id": {"type": "string"},
                "account_uid": {"type": "string"},
                "sizing": {"type": "object", "optional": True},
            },
            self.propdesk_add_follower,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_remove_follower",
            "Remove a follower from a copy group. Protected.",
            {"group_id": {"type": "string"}, "account_uid": {"type": "string"}},
            self.propdesk_remove_follower,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_set_group_active",
            "Start or stop replication for a copy group. Protected.",
            {"group_id": {"type": "string"}, "active": {"type": "boolean"}},
            self.propdesk_set_group_active,
            mutating=True,
            risk=ActionRisk.HIGH,
            protected=True,
        )
        self._add(
            "propdesk_copy_pass",
            "Evaluate a copy group against a leader position. Reports what each "
            "follower would do and why; sends nothing unless dry_run is false, which "
            "reaches accounts and is protected.",
            {
                "group_id": {"type": "string"},
                "symbol": {"type": "string"},
                "leader_position": {"type": "integer"},
                "dry_run": {"type": "boolean", "optional": True},
            },
            self.propdesk_copy_pass,
            mutating=True,
            risk=ActionRisk.HIGH,
            protected=True,
        )
        self._add(
            "propdesk_reconcile",
            "Compare an account against its provider's own state and report every "
            "disagreement. The provider is authoritative.",
            {
                "account_uid": {"type": "string"},
                "trigger": {"type": "string", "optional": True},
            },
            self.propdesk_reconcile,
            mutating=True,
        )
        self._add(
            "propdesk_allocation",
            "Which account is running which strategy, the constraints, and the history.",
            {},
            self.propdesk_allocation,
        )
        self._add(
            "propdesk_plan_allocation",
            "Which validated strategy each account could run, at what size, and every "
            "pairing that was refused with the reason. Computes; records nothing. "
            "Advisory recommendations can only narrow the deterministic feasible set.",
            {
                "strategy_ids": {"type": "array", "items": {"type": "string"}},
                "advice": {"type": "array", "items": {"type": "object"}, "optional": True},
            },
            self.propdesk_plan_allocation,
        )
        self._add(
            "propdesk_apply_allocation",
            "Record which account runs which strategy. Protected: it decides where "
            "capital is put to work, and it places nothing by itself.",
            {
                "allocations": {"type": "array", "items": {"type": "object"}},
                "actor": {"type": "string", "optional": True},
            },
            self.propdesk_apply_allocation,
            mutating=True,
            risk=ActionRisk.HIGH,
            protected=True,
        )
        self._add(
            "propdesk_set_constraints",
            "Change the allocator's risk budget and ceilings. Protected: these are "
            "deterministic controls.",
            {"constraints": {"type": "object"}},
            self.propdesk_set_constraints,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_news",
            "Scheduled economic events, which calendar sources could be read, and "
            "whether a blackout window is open.",
            {"days": {"type": "integer", "optional": True}},
            self.propdesk_news,
        )
        self._add(
            "propdesk_record_event",
            "Record a scheduled economic event by hand. Protected: news feeds a risk "
            "control, and a control an AI can write is not a control.",
            {
                "title": {"type": "string"},
                "at": {"type": "string"},
                "impact": {"type": "string", "optional": True},
                "country": {"type": "string", "optional": True},
                "note": {"type": "string", "optional": True},
            },
            self.propdesk_record_event,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_set_news_policy",
            "Change the blackout windows around economic events. Protected.",
            {"policy": {"type": "object"}},
            self.propdesk_set_news_policy,
            mutating=True,
            protected=True,
        )
        self._add(
            "propdesk_activity",
            "Orders, refusals, reconciliation passes and allocation changes.",
            {"limit": {"type": "integer", "optional": True}},
            self.propdesk_activity,
        )

        self._add(
            "propdesk_risk",
            "Each account's risk mode, boundaries, appetite, last evaluation and "
            "autonomy level, with the catalogue of what the modes promise and what "
            "the advisory layer may never do.",
            {"account_uid": {"type": "string", "optional": True}},
            self.propdesk_risk,
        )
        self._add(
            "propdesk_set_risk",
            "Set an account's risk mode, appetite and boundaries. Protected: the "
            "boundaries are the ceiling every automated proposal is clamped to, so an "
            "actor that could write them could raise its own limit.",
            {"settings": {"type": "object"}},
            self.propdesk_set_risk,
            mutating=True,
            risk=ActionRisk.HIGH,
            protected=True,
        )
        self._add(
            "propdesk_evaluate_risk",
            "Run the risk scaler over one account and return what it proposes, with "
            "every driver, the one that bound, and the governor limit that applied. "
            "Evaluating writes a record and changes nothing; applying is a separate, "
            "protected verb.",
            {
                "account_uid": {"type": "string"},
                "strategy_id": {
                    "type": "string",
                    "optional": True,
                    "description": (
                        "Size against this strategy's measured drawdown when the account "
                        "is not running one yet. Ignored when it is."
                    ),
                },
                "advisory": {
                    "type": "number",
                    "optional": True,
                    "description": (
                        "A multiplier between 0 and 1. It may narrow the proposal and "
                        "can never widen one."
                    ),
                },
                "advisory_note": {"type": "string", "optional": True},
            },
            self.propdesk_evaluate_risk,
            # Mutating because it appends the evaluation to the append-only
            # record — the same treatment `propdesk_reconcile` gets, and for the
            # same reason: it changes no control and places nothing, but a
            # reader of the audit trail will see a row that this call wrote.
            mutating=True,
        )
        self._add(
            "propdesk_apply_risk",
            "Apply the risk fraction the scaler proposed. Protected: this is the verb "
            "that moves the number an order is sized from.",
            {
                "account_uid": {"type": "string"},
                "advisory": {"type": "number", "optional": True},
                "advisory_note": {"type": "string", "optional": True},
            },
            self.propdesk_apply_risk,
            mutating=True,
            risk=ActionRisk.HIGH,
            protected=True,
        )
        self._add(
            "propdesk_disclosures",
            "The safety disclosures, their current versions, and what has been "
            "acknowledged.",
            {},
            self.propdesk_disclosures,
        )
        self._add(
            "propdesk_acknowledge",
            "Record that a person read a disclosure and accepted every statement in "
            "it. Protected: an acknowledgement an agent could write is consent nobody "
            "gave.",
            {
                "key": {"type": "string"},
                "accepted": {"type": "array", "items": {"type": "string"}},
                "account_uid": {"type": "string", "optional": True},
            },
            self.propdesk_acknowledge,
            mutating=True,
            risk=ActionRisk.HIGH,
            protected=True,
        )
        self._add(
            "propdesk_set_autonomy",
            "Set autonomous deployment to off, approval-required or fully autonomous "
            "for one account. Protected, and refused without a current acknowledgement "
            "of the autonomous-deployment disclosure.",
            {"account_uid": {"type": "string"}, "level": {"type": "string"}},
            self.propdesk_set_autonomy,
            mutating=True,
            risk=ActionRisk.HIGH,
            protected=True,
        )
        self._add(
            "propdesk_evaluate_deployment",
            "Run every mandatory deployment control for one strategy on one account "
            "and report which passed. It deploys nothing: this build has no live "
            "connector and the lifecycle refuses the deployed stage outright.",
            {"strategy_id": {"type": "string"}, "account_uid": {"type": "string"}},
            self.propdesk_evaluate_deployment,
            mutating=True,
        )
        self._add(
            "propdesk_audit",
            "The consequential audit trail: risk changes, deployment evaluations and "
            "acknowledgements, with the state on both sides of each one.",
            {
                "account_uid": {"type": "string", "optional": True},
                "limit": {"type": "integer", "optional": True},
            },
            self.propdesk_audit,
        )
        self._add(
            "propdesk_why",
            "Answer one of the desk's questions from recorded state: why risk changed, "
            "why a deployment is blocked, why an account is blocked, why an allocation "
            "changed. A question with no record behind it is declined rather than "
            "answered.",
            {
                "question": {"type": "string"},
                "account_uid": {"type": "string", "optional": True},
                "strategy_id": {"type": "string", "optional": True},
            },
            self.propdesk_why,
        )

    def _desk(self) -> Any:
        if self.prop_desk is None:
            raise ActionError("no prop desk is configured in this process")
        return self.prop_desk

    def _desk_call(self, method: str, **arguments: Any) -> dict[str, Any]:
        """Call one service method, turning its refusal into an action refusal.

        Every prop-desk action goes through here so a refusal reads the same
        whether it arrived over HTTP or from the assistant, and so the service
        remains the single implementation rather than one of two.
        """
        from forge_api.propdesk import PropDeskError

        try:
            result: dict[str, Any] = getattr(self._desk(), method)(**arguments)
        except PropDeskError as exc:
            raise ActionError(str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise ActionError(str(exc)) from exc
        return result

    def propdesk_risk(self, account_uid: Any = None) -> dict[str, Any]:
        return self._desk_call(
            "risk",
            account_uid=None if account_uid is None else _str(account_uid, "account_uid"),
        )

    def propdesk_set_risk(self, settings: Any) -> dict[str, Any]:
        if not isinstance(settings, dict):
            raise ActionError("settings must be an object")
        return self._desk_call("save_risk_settings", settings=settings, actor=self._current_actor())

    def propdesk_evaluate_risk(
        self,
        account_uid: Any,
        strategy_id: Any = None,
        advisory: Any = None,
        advisory_note: Any = None,
    ) -> dict[str, Any]:
        return self._desk_call(
            "evaluate_risk",
            account_uid=_str(account_uid, "account_uid"),
            strategy_id="" if strategy_id is None else _str(strategy_id, "strategy_id"),
            advisory=None if advisory is None else _advisory(advisory),
            advisory_note="" if advisory_note is None else _str(advisory_note, "advisory_note"),
            apply_change=False,
        )

    def propdesk_apply_risk(
        self, account_uid: Any, advisory: Any = None, advisory_note: Any = None
    ) -> dict[str, Any]:
        return self._desk_call(
            "evaluate_risk",
            account_uid=_str(account_uid, "account_uid"),
            advisory=None if advisory is None else _advisory(advisory),
            advisory_note="" if advisory_note is None else _str(advisory_note, "advisory_note"),
            apply_change=True,
            actor=self._current_actor(),
        )

    def propdesk_disclosures(self) -> dict[str, Any]:
        return self._desk_call("disclosures")

    def propdesk_acknowledge(
        self, key: Any, accepted: Any, account_uid: Any = None
    ) -> dict[str, Any]:
        if not isinstance(accepted, list | tuple):
            raise ActionError("accepted must be a list of the statements that were ticked")
        return self._desk_call(
            "acknowledge",
            key=_str(key, "key", lower=True),
            accepted=tuple(_str(item, "accepted") for item in accepted),
            actor=self._current_actor(),
            account_uid="" if account_uid is None else _str(account_uid, "account_uid"),
        )

    def propdesk_set_autonomy(self, account_uid: Any, level: Any) -> dict[str, Any]:
        return self._desk_call(
            "set_autonomy",
            account_uid=_str(account_uid, "account_uid"),
            level=_str(level, "level", lower=True),
            actor=self._current_actor(),
        )

    def propdesk_evaluate_deployment(self, strategy_id: Any, account_uid: Any) -> dict[str, Any]:
        return self._desk_call(
            "evaluate_deployment",
            strategy_id=_str(strategy_id, "strategy_id"),
            account_uid=_str(account_uid, "account_uid"),
        )

    def propdesk_audit(self, account_uid: Any = None, limit: Any = None) -> dict[str, Any]:
        return self._desk_call(
            "audit",
            account_uid=None if account_uid is None else _str(account_uid, "account_uid"),
            limit=200 if limit is None else int(limit),
        )

    def propdesk_why(
        self, question: Any, account_uid: Any = None, strategy_id: Any = None
    ) -> dict[str, Any]:
        return self._desk_call(
            "why",
            question=_str(question, "question", lower=True),
            account_uid="" if account_uid is None else _str(account_uid, "account_uid"),
            strategy_id="" if strategy_id is None else _str(strategy_id, "strategy_id"),
        )

    def propdesk_providers(self) -> dict[str, Any]:
        return self._desk_call("providers")

    def propdesk_connections(self) -> dict[str, Any]:
        return self._desk_call("connections")

    def propdesk_create_connection(
        self,
        provider: Any,
        label: Any,
        environment: Any = None,
        credential_ref: Any = None,
        platform: Any = None,
    ) -> dict[str, Any]:
        return self._desk_call(
            "create_connection",
            provider=_str(provider, "provider", lower=True),
            label=_str(label, "label", limit=120),
            environment=_str(environment or "simulation", "environment", lower=True),
            credential_ref="" if credential_ref is None else _str(
                credential_ref, "credential_ref", limit=120
            ),
            platform=None if platform is None else _str(platform, "platform", lower=True),
        )

    def propdesk_connect(self, connection_id: Any) -> dict[str, Any]:
        return self._desk_call("connect", connection_id=_str(connection_id, "connection_id"))

    def propdesk_disconnect(self, connection_id: Any) -> dict[str, Any]:
        return self._desk_call(
            "disconnect", connection_id=_str(connection_id, "connection_id")
        )

    def propdesk_policies(self) -> dict[str, Any]:
        return self._desk_call("policies")

    def propdesk_save_policy(self, policy: Any) -> dict[str, Any]:
        if not isinstance(policy, dict):
            raise ActionError("'policy' must be a programme policy object.")
        return self._desk_call("save_policy", policy=policy)

    def propdesk_link_policy(self, account_uid: Any, policy_id: Any = None) -> dict[str, Any]:
        return self._desk_call(
            "link_policy",
            account_uid=_str(account_uid, "account_uid"),
            policy_id=None if policy_id is None else _str(policy_id, "policy_id"),
        )

    def propdesk_link_rules(
        self, account_uid: Any, prop_account_id: Any = None
    ) -> dict[str, Any]:
        return self._desk_call(
            "link_prop_account",
            account_uid=_str(account_uid, "account_uid"),
            prop_account_id=(
                None if prop_account_id is None else _str(prop_account_id, "prop_account_id")
            ),
        )

    def propdesk_groups(self) -> dict[str, Any]:
        return self._desk_call("groups")

    def propdesk_create_group(
        self, name: Any, leader_account_uid: Any, attested_by: Any
    ) -> dict[str, Any]:
        return self._desk_call(
            "create_group",
            name=_str(name, "name", limit=120),
            leader_account_uid=_str(leader_account_uid, "leader_account_uid"),
            attested_by=_str(attested_by, "attested_by", limit=120),
        )

    def propdesk_add_follower(
        self, group_id: Any, account_uid: Any, sizing: Any = None
    ) -> dict[str, Any]:
        if sizing is not None and not isinstance(sizing, dict):
            raise ActionError("'sizing' must be a sizing-policy object.")
        return self._desk_call(
            "add_follower",
            group_id=_str(group_id, "group_id"),
            account_uid=_str(account_uid, "account_uid"),
            sizing=sizing,
        )

    def propdesk_remove_follower(self, group_id: Any, account_uid: Any) -> dict[str, Any]:
        return self._desk_call(
            "remove_follower",
            group_id=_str(group_id, "group_id"),
            account_uid=_str(account_uid, "account_uid"),
        )

    def propdesk_set_group_active(self, group_id: Any, active: Any) -> dict[str, Any]:
        return self._desk_call(
            "set_group_active", group_id=_str(group_id, "group_id"), active=bool(active)
        )

    def propdesk_copy_pass(
        self, group_id: Any, symbol: Any, leader_position: Any, dry_run: Any = None
    ) -> dict[str, Any]:
        return self._desk_call(
            "copy_pass",
            group_id=_str(group_id, "group_id"),
            symbol=_str(symbol, "symbol", limit=16),
            leader_position=_bounded(
                leader_position, 0, -10_000, 10_000, "leader_position"
            ),
            # Defaults to a dry run. An operator asking what a group would do
            # must be able to find out without it happening.
            dry_run=True if dry_run is None else bool(dry_run),
        )

    def propdesk_reconcile(self, account_uid: Any, trigger: Any = None) -> dict[str, Any]:
        return self._desk_call(
            "reconcile",
            account_uid=_str(account_uid, "account_uid"),
            trigger=_str(trigger or "periodic", "trigger", lower=True),
        )

    def propdesk_allocation(self) -> dict[str, Any]:
        return self._desk_call("allocation_state")

    def propdesk_plan_allocation(
        self, strategy_ids: Any, advice: Any = None
    ) -> dict[str, Any]:
        if not isinstance(strategy_ids, list | tuple) or not strategy_ids:
            raise ActionError("'strategy_ids' must be a non-empty list of strategy ids.")
        if advice is not None and not isinstance(advice, list | tuple):
            raise ActionError("'advice' must be a list of recommendation objects.")
        return self._desk_call(
            "plan_allocation",
            strategy_ids=tuple(_str(item, "strategy_id") for item in strategy_ids),
            advice=tuple(advice or ()),
        )

    def propdesk_apply_allocation(
        self, allocations: Any, actor: Any = None
    ) -> dict[str, Any]:
        if not isinstance(allocations, list | tuple):
            raise ActionError("'allocations' must be a list of allocation objects.")
        return self._desk_call(
            "apply_allocation",
            allocations=tuple(allocations),
            actor=_str(actor or "operator", "actor", limit=120),
        )

    def propdesk_set_constraints(self, constraints: Any) -> dict[str, Any]:
        if not isinstance(constraints, dict):
            raise ActionError("'constraints' must be an allocation-constraints object.")
        return self._desk_call("save_constraints", constraints=constraints)

    def propdesk_news(self, days: Any = None) -> dict[str, Any]:
        return self._desk_call("news", days=_bounded(days, 7, 1, 60, "days"))

    def propdesk_record_event(
        self, title: Any, at: Any, impact: Any = None, country: Any = None, note: Any = None
    ) -> dict[str, Any]:
        return self._desk_call(
            "record_event",
            title=_str(title, "title", limit=200),
            at=_str(at, "at", limit=64),
            impact="" if impact is None else _str(impact, "impact", lower=True),
            country="US" if country is None else _str(country, "country", limit=8),
            note="" if note is None else _str(note, "note", limit=400),
        )

    def propdesk_set_news_policy(self, policy: Any) -> dict[str, Any]:
        if not isinstance(policy, dict):
            raise ActionError("'policy' must be a news-policy object.")
        return self._desk_call("save_news_policy", policy=policy)

    def propdesk_activity(self, limit: Any = None) -> dict[str, Any]:
        return self._desk_call("activity", limit=_bounded(limit, 100, 1, 1000, "limit"))

    # ── the fund ─────────────────────────────────────────────────────────────
    def _register_fund(self) -> None:
        self._add(
            "fund_state",
            "NAV, exposure, risk status and the state of every stage of the fund loop.",
            {},
            self.fund_state,
        )
        self._add(
            "fund_config",
            "Capital, limits, constraints, universe and restricted list, plus who last "
            "changed them.",
            {},
            self.fund_config,
        )
        self._add(
            "set_fund_config",
            "Replace the fund configuration. Protected: these are the deterministic "
            "controls, and an AI actor that could widen them would not be constrained "
            "by them.",
            {
                "config": {"type": "object", "description": "A FundConfig document."},
                "note": {"type": "string", "optional": True},
            },
            self.set_fund_config,
            mutating=True,
            protected=True,
        )
        self._add(
            "construct_portfolio",
            "Size the eligible signals into a portfolio inside the configured "
            "constraints, at a stated cost. Proposes; trades nothing.",
            {"capital": {"type": "number", "optional": True}},
            self.construct_portfolio,
            mutating=True,
        )
        self._add(
            "calculate_risk",
            "Measure the book, or a named proposal, against the enforceable limits.",
            {"portfolio_id": {"type": "string", "optional": True}},
            self.calculate_risk,
        )
        self._add(
            "prepare_orders",
            "The rebalance that would reach a proposal from the current book. Prepared "
            "only: nothing is screened and nothing is placed.",
            {"portfolio_id": {"type": "string"}},
            self.prepare_orders,
            mutating=True,
        )
        self._add(
            "screen_orders",
            "Run prepared orders through the deterministic pre-trade gate. Every check "
            "runs and every reason is reported.",
            {"portfolio_id": {"type": "string"}},
            self.screen_orders,
            mutating=True,
        )
        self._add(
            "submit_orders",
            "Route cleared orders through the OMS to the execution adapter. Refused for "
            "anything the gate did not clear, and simulated in this build.",
            {
                "order_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Order ids the gate has cleared.",
                }
            },
            self.submit_orders,
            mutating=True,
        )
        self._add(
            "cancel_order",
            "Cancel a working order. Reaches the book, so only Hedge Fund mode on the "
            "autonomous stance runs it without a person.",
            {"order_id": {"type": "string"}},
            self.cancel_order,
            mutating=True,
        )
        self._add(
            "fund_operations",
            "The book, reconciliation, orders, fills and the audit summary.",
            {},
            self.fund_operations,
        )
        self._add(
            "fund_performance",
            "Attribution: where the return came from, and what risk generated it.",
            {},
            self.fund_performance,
        )
        self._add(
            "audit_log",
            "Who did what, on what, with what result — including what was refused.",
            {"limit": {"type": "integer", "optional": True}},
            self.audit_log,
        )
        self._add(
            "pending_approvals",
            "Consequential actions an AI actor has proposed and a person has not yet "
            "decided on.",
            {},
            self.pending_approvals,
        )

    def _require_fund(self) -> Any:
        if self.fund is None:
            raise ActionError("no fund service is configured in this process")
        return self.fund

    def fund_state(self) -> dict[str, Any]:
        _, stance = self.context()
        state: dict[str, Any] = self._require_fund().state(
            stance=stance.value if stance else None
        )
        return state

    def fund_config(self) -> dict[str, Any]:
        fund = self._require_fund()
        return {
            "config": fund.config.as_dict(),
            "history": fund.config_store.history(20),
        }

    def set_fund_config(self, config: Any, note: Any = None) -> dict[str, Any]:
        fund = self._require_fund()
        if not isinstance(config, dict):
            raise ActionError("'config' must be a FundConfig object.")
        try:
            parsed = FundConfig.model_validate(config)
        except ValidationError as exc:
            raise ActionError(
                f"That is not a valid fund configuration: {exc.errors()[0]['msg']}"
            ) from exc
        saved = fund.save_config(
            parsed, changed_by="operator", note="" if note is None else str(note)
        )
        return {"config": saved.as_dict()}

    def construct_portfolio(self, capital: Any = None) -> dict[str, Any]:
        fund = self._require_fund()
        money = None
        if capital is not None:
            try:
                money = float(capital)
            except (TypeError, ValueError) as exc:
                raise ActionError("'capital' must be a number.") from exc
            if money <= 0:
                raise ActionError("'capital' must be positive.")
        try:
            return {"portfolio": fund.construct_portfolio(capital=money).as_dict()}
        except FundError as exc:
            raise ActionError(str(exc)) from exc

    def calculate_risk(self, portfolio_id: Any = None) -> dict[str, Any]:
        fund = self._require_fund()
        try:
            assessment = fund.risk(
                portfolio_id=None if portfolio_id is None else _str(portfolio_id, "portfolio_id")
            )
        except FundError as exc:
            raise ActionError(str(exc)) from exc
        return {"risk": assessment.as_dict()}

    def prepare_orders(self, portfolio_id: Any) -> dict[str, Any]:
        fund = self._require_fund()
        try:
            orders = fund.prepare_orders(_str(portfolio_id, "portfolio_id"))
        except FundError as exc:
            raise ActionError(str(exc)) from exc
        return {
            "orders": [order.model_dump(mode="json") for order in orders],
            "note": "prepared only. Nothing is screened or placed until screen_orders runs.",
        }

    def screen_orders(self, portfolio_id: Any) -> dict[str, Any]:
        fund = self._require_fund()
        try:
            orders = fund.prepare_orders(_str(portfolio_id, "portfolio_id"))
            decisions = fund.screen(orders)
        except FundError as exc:
            raise ActionError(str(exc)) from exc
        return {
            "screened": [
                {"order": order.model_dump(mode="json"), "decision": decision.as_dict()}
                for order, decision in zip(orders, decisions, strict=True)
            ],
            "cleared": [d.order_id for d in decisions if d.allowed],
            "blocked": [d.order_id for d in decisions if not d.allowed],
        }

    def submit_orders(self, order_ids: Any) -> dict[str, Any]:
        fund = self._require_fund()
        if not isinstance(order_ids, list) or not order_ids:
            raise ActionError("'order_ids' must be a non-empty list of order ids.")
        results = fund.submit(tuple(str(value) for value in order_ids))
        return {
            "results": results,
            "accepted": sum(1 for row in results if row["accepted"]),
            "refused": sum(1 for row in results if not row["accepted"]),
            "execution_mode": "PAPER",
        }

    def cancel_order(self, order_id: Any) -> dict[str, Any]:
        fund = self._require_fund()
        try:
            cancelled = fund.oms.cancel(_str(order_id, "order_id"))
        except OrderRefused as exc:
            raise ActionError(str(exc)) from exc
        return {"order": cancelled.model_dump(mode="json")}

    def fund_operations(self) -> dict[str, Any]:
        operations: dict[str, Any] = self._require_fund().operations()
        return operations

    def fund_performance(self) -> dict[str, Any]:
        performance: dict[str, Any] = self._require_fund().performance()
        return performance

    def audit_log(self, limit: Any = None) -> dict[str, Any]:
        if self.audit is None:
            raise ActionError("no audit log is configured in this process")
        count = _bounded(limit, 100, 1, 1000, "limit")
        return {
            "entries": [entry.as_dict() for entry in self.audit.recent(count)],
            "summary": self.audit.summary(),
        }

    def pending_approvals(self) -> dict[str, Any]:
        if self.approvals is None:
            raise ActionError("no approval queue is configured in this process")
        return {
            "pending": [request.as_dict() for request in self.approvals.pending()],
            "history": [request.as_dict() for request in self.approvals.history(50)],
        }


def _parse_rules(rules: Any) -> AccountRules:
    """Validate a rule-set document, refusing with the field that is wrong.

    Pydantic's own message is used rather than a generic one: an operator typing
    a contract into a form needs to know that `maximum_loss` must be positive,
    not that "the rules are invalid".
    """
    if not isinstance(rules, dict):
        raise ActionError("'rules' must be an account rule-set object.")
    try:
        return AccountRules.model_validate(rules)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "rules"
        raise ActionError(f"That is not a valid rule set: {where} — {first['msg']}") from exc


def _bounded(value: Any, fallback: int, low: int, high: int, field: str) -> int:
    if value is None:
        return fallback
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ActionError(f"'{field}' must be a whole number.") from exc
    if not low <= number <= high:
        raise ActionError(f"'{field}' must be between {low} and {high}.")
    return number

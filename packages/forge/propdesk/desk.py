"""The funnel. Every order on the desk goes through it, in this order.

    Research Factory → Strategy Library → Strategy Health → Prop Account
    Compatibility → Strategy Allocation → Risk / Prop Rules Gate →
    Execution Fabric → Broker adapter → Account

This module owns the last four arrows, and it owns them *exclusively*. There is
no other path from an intent to an adapter: the copy resolver, the allocator and
the reconciliation engine all produce intents and none of them can send one.
That is the structural reason the gates cannot be skipped, as distinct from the
procedural reason that nobody would want to.

The ladder each intent climbs, in order, accumulating every refusal rather than
stopping at the first:

1. **Account known and bound.** An intent for an account no connection serves is
   refused here rather than at a socket.
2. **Connection health.** A follower whose connection is not live cannot be
   traded; a degraded connection may still be reduced and cancelled.
3. **Source evidence.** What backs this order. A strategy-driven order names a
   strategy and the gate's own validation check refuses one the judge has not
   passed. A copied discretionary trade has no strategy and never will, so what
   stands in its place is the operator's attestation that the leader account and
   this one belong to the same owner — which is the condition every researched
   firm attaches to copying. An order with neither is refused here.
4. **Prop-firm compatibility.** May this account be used this way at all —
   copied onto, automated, allocated to. `UNKNOWN` refuses.
5. **Cross-account direction.** Would this put the owner on both sides of one
   product group across their accounts.
6. **News blackout.** An advisory input that can only tighten: it may add a
   refusal and there is no branch by which it removes one.
7. **The account rule engine.** `forge.prop.account.assess` with the proposed
   contracts, which is the existing deterministic engine and not a second one.
8. **The pre-trade gate.** `forge.execution.gate.screen`, which is the existing
   deterministic gate and not a second one. A cleared order leaves with a
   clearance bound to a hash of that exact order.

Only an intent that survives all eight is dispatched, and every intent — cleared
or refused — produces a `DeskDecision` with the whole ladder on it, so the
Activity panel answers "why did that follower not take the trade" from a record
rather than from a reconstruction.

**A note on `capital`, and why it defaults to zero.** The pre-trade gate's
leverage and concentration checks measure an order's notional as a fraction of
capital, and its single-name ceiling cannot exceed 100% of it. That is the right
measure for a cash portfolio and the wrong one for a leveraged futures account:
one Micro Nasdaq contract is roughly eighty per cent of a fifty-thousand-dollar
account's *notional*, so those two checks would refuse every order a prop trader
has ever placed. With `capital` left at zero the gate reports honestly that the
order's exposure could not be computed and does not block on it — and the limits
that actually bind a funded account, its contract caps and its distance to the
loss floor, are enforced by the account rule engine one rung above. An operator
who does supply a capital figure engages both checks deliberately, and they will
be strict.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.execution.gate import (
    GateContext,
    GateDecision,
    InstrumentRule,
    MarketState,
    ProposedOrder,
)
from forge.execution.gate import Side as GateSide
from forge.execution.gate import screen as screen_order
from forge.prop.account import AccountAssessment, AccountRules, AccountState
from forge.prop.account import assess as assess_account
from forge.propdesk.fabric import (
    Acknowledgement,
    CommandKind,
    ExecutionFabric,
    FabricError,
    OrderIntent,
    Priority,
)
from forge.propdesk.identity import ConnectionState
from forge.propdesk.news import NewsAssessment
from forge.propdesk.policy import (
    CompatibilityReason,
    CompatibilityReport,
    DirectionGuard,
    Permission,
    UseCase,
)
from forge.risk.portfolio import PortfolioLimits


class DeskError(Exception):
    """The desk refused. Nothing was sent."""


class Stage(FrozenModel):
    """One rung of the ladder, and what it said."""

    stage: str
    passed: bool
    #: True when the stage could not be evaluated. Never a pass — the same
    #: discipline the pre-trade gate applies to its own unknowns.
    unknown: bool = False
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return not self.passed


class DeskDecision(FrozenModel):
    """One intent, the whole ladder, and what happened.

    The refusals are the valuable half. "Why is this follower flat while the
    leader is long three" has an answer here, per stage, in words.
    """

    decision_id: str
    intent: OrderIntent
    cleared: bool
    stages: tuple[Stage, ...]
    reasons: tuple[str, ...] = ()
    gate: GateDecision | None = None
    assessment: AccountAssessment | None = None
    acknowledgement: Acknowledgement | None = None
    dispatched: bool = False
    at: datetime

    @property
    def blocking_stages(self) -> tuple[str, ...]:
        return tuple(s.stage for s in self.stages if s.blocking)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["blocking_stages"] = list(self.blocking_stages)
        return payload


class DeskAccount(FrozenModel):
    """Everything the ladder reads about one account, assembled by the caller.

    A single shape, so this module does not know whether the rule set came from
    a store, the compatibility from a policy the operator typed, or the position
    from a provider snapshot. The caller assembles; the desk decides.
    """

    account_uid: str
    display_name: str = ""
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    rules: AccountRules | None = None
    state: AccountState | None = None
    compatibility: CompatibilityReport | None = None
    #: Signed net positions by product root, for the direction guard and the
    #: gate's own position limit.
    positions: dict[str, int] = Field(default_factory=dict)
    working: dict[str, int] = Field(default_factory=dict)
    equity: float | None = None

    @property
    def rules_known(self) -> bool:
        return self.rules is not None and self.state is not None


class DeskContext(FrozenModel):
    """Everything one dispatch pass reads. Assembled, never fetched here.

    A desk that went and looked things up would produce decisions that depend on
    when it ran and on what a network did, and a refused copy would not be
    reproducible from the record. Assembling the context is the caller's job for
    the same reason it is the caller's job at the pre-trade gate.
    """

    now: datetime
    accounts: dict[str, DeskAccount] = Field(default_factory=dict)
    #: Every account of this owner, for the cross-account direction guard —
    #: including accounts not being traded in this pass. The rule is written at
    #: the owner level, so a guard that only saw the group could not enforce it.
    owner_positions: dict[str, dict[str, int]] = Field(default_factory=dict)
    limits: PortfolioLimits = PortfolioLimits()
    instruments: dict[str, InstrumentRule] = Field(default_factory=dict)
    market: dict[str, MarketState] = Field(default_factory=dict)
    verdicts: dict[str, str] = Field(default_factory=dict)
    eligible_verdicts: tuple[str, ...] = ("PASS",)
    news: NewsAssessment | None = None
    #: Which permission question this pass asks of each account's policy.
    use_case: UseCase = UseCase.COPY_FOLLOWER
    #: Product groups, for the direction guard.
    product_groups: dict[str, str] = Field(default_factory=dict)
    #: Gross exposure of the existing book, threaded into the gate.
    current_gross: float = 0.0
    capital: float = 0.0


class PropDesk:
    """The funnel. Screens intents, dispatches the survivors, records both."""

    def __init__(
        self,
        fabric: ExecutionFabric,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.fabric = fabric
        self._now = now
        self.decisions: list[DeskDecision] = []

    # ── screening ────────────────────────────────────────────────────────────
    def screen(self, intent: OrderIntent, context: DeskContext) -> DeskDecision:
        """Run the whole ladder against one intent. Nothing is sent."""
        stages: list[Stage] = []
        account = context.accounts.get(intent.account_uid)

        stages.append(self._binding(intent, account))
        stages.append(self._connection(intent, account))

        stages.append(self._source_evidence(intent))

        compatibility = account.compatibility if account else None
        stages.append(self._compatibility(context.use_case, compatibility))
        stages.append(self._direction(intent, account, context))
        stages.append(self._news(intent, context))

        assessment: AccountAssessment | None = None
        if account is not None and account.rules_known:
            assessment = assess_account(
                account.rules,  # type: ignore[arg-type]
                account.state,  # type: ignore[arg-type]
                proposed_contracts=intent.quantity or 0,
            )
            stages.append(self._rules(assessment))
        else:
            stages.append(
                Stage(
                    stage="account_rules",
                    passed=False,
                    unknown=True,
                    detail=(
                        "no rule set and recorded state are linked to this account, so "
                        "its contract cannot be evaluated. An unassessable account is "
                        "not a compliant one."
                    ),
                )
            )

        gate: GateDecision | None = None
        if intent.kind is CommandKind.PLACE:
            gate = self._gate(intent, account, context)
            stages.append(
                Stage(
                    stage="pretrade_gate",
                    passed=gate.allowed,
                    detail=(
                        "cleared"
                        if gate.allowed
                        else "; ".join(gate.reasons) or "blocked"
                    ),
                )
            )
        else:
            # A cancel or a flatten reduces exposure. Refusing it because a
            # position limit is breached would mean the moment the operator most
            # needs to get out is the moment the desk stops letting them.
            stages.append(
                Stage(
                    stage="pretrade_gate",
                    passed=True,
                    detail=(
                        f"a {intent.kind.value} reduces exposure and is not screened as "
                        "a new order"
                    ),
                )
            )

        blocking = [s for s in stages if s.blocking]
        # A reducing command survives a blocked prop-rule or compatibility stage
        # for the same reason: getting flat is always permitted. It does not
        # survive a missing binding, because there is genuinely nowhere to send
        # it.
        reducing = intent.priority <= Priority.CANCEL
        fatal = [
            s
            for s in blocking
            if not reducing or s.stage in {"account_binding", "connection"}
        ]

        cleared = not fatal
        reasons = tuple(f"{s.stage}: {s.detail}" for s in blocking)
        if cleared and blocking:
            reasons = (
                *reasons,
                "these refusals were overridden because the command reduces exposure: "
                "getting flat is permitted even when opening is not",
            )

        return DeskDecision(
            decision_id=stable_id(
                "deskdec",
                {"intent": intent.intent_id, "at": context.now.isoformat()},
            ),
            intent=intent,
            cleared=cleared,
            stages=tuple(stages),
            reasons=reasons,
            gate=gate,
            assessment=assessment,
            at=context.now,
        )

    def screen_all(
        self, intents: Iterable[OrderIntent], context: DeskContext
    ) -> tuple[DeskDecision, ...]:
        """Screen a batch, threading each cleared order's effect into the next.

        A basket screened independently can pass order by order and breach a
        limit together — the same reason `forge.execution.gate.screen_all`
        exists. Here it matters twice over, because a copy group is *always* a
        basket: eight followers taking one contract each is eight orders that
        each look trivial.
        """
        decisions: list[DeskDecision] = []
        accounts = dict(context.accounts)
        owner = {uid: dict(book) for uid, book in context.owner_positions.items()}

        for intent in intents:
            stepped = context.model_copy(
                update={"accounts": dict(accounts), "owner_positions": owner}
            )
            decision = self.screen(intent, stepped)
            decisions.append(decision)
            if decision.cleared and intent.kind is CommandKind.PLACE and intent.side:
                signed = intent.side.sign * (intent.quantity or 0)
                account = accounts.get(intent.account_uid)
                if account is not None:
                    positions = dict(account.positions)
                    positions[intent.symbol] = positions.get(intent.symbol, 0) + signed
                    working = dict(account.working)
                    working[intent.symbol] = working.get(intent.symbol, 0) + (
                        intent.quantity or 0
                    )
                    accounts[intent.account_uid] = account.model_copy(
                        update={"positions": positions, "working": working}
                    )
                book = dict(owner.get(intent.account_uid, {}))
                book[intent.symbol] = book.get(intent.symbol, 0) + signed
                owner[intent.account_uid] = book
        return tuple(decisions)

    # ── dispatch ─────────────────────────────────────────────────────────────
    def dispatch(
        self, intents: Iterable[OrderIntent], context: DeskContext
    ) -> tuple[DeskDecision, ...]:
        """Screen, then send only what cleared.

        The one entry point. There is deliberately no `send_without_screening`
        and no flag on this method that skips the ladder: a caller holding an
        intent has exactly one thing it can do with it.
        """
        decisions = list(self.screen_all(intents, context))
        for index, decision in enumerate(decisions):
            if not decision.cleared:
                continue
            try:
                self.fabric.dispatch(decision.intent)
            except FabricError as exc:
                decisions[index] = decision.model_copy(
                    update={
                        "cleared": False,
                        "reasons": (*decision.reasons, f"fabric: {exc}"),
                    }
                )
                continue
            decisions[index] = decision.model_copy(update={"dispatched": True})

        acknowledgements = {
            intent.intent_id: ack for intent, ack in self.fabric.flush()
        }
        for index, decision in enumerate(decisions):
            ack = acknowledgements.get(decision.intent.intent_id)
            if ack is not None:
                decisions[index] = decision.model_copy(update={"acknowledgement": ack})

        self.decisions.extend(decisions)
        del self.decisions[:-2000]
        return tuple(decisions)

    def recent(self, limit: int = 100) -> tuple[DeskDecision, ...]:
        return tuple(self.decisions[-max(1, limit):][::-1])

    # ── the rungs ────────────────────────────────────────────────────────────
    @staticmethod
    def _binding(intent: OrderIntent, account: DeskAccount | None) -> Stage:
        if account is None:
            return Stage(
                stage="account_binding",
                passed=False,
                detail=(
                    f"'{intent.account_uid}' is not a known account on this desk, so "
                    "there is nowhere to send this"
                ),
            )
        return Stage(
            stage="account_binding",
            passed=True,
            detail=f"{account.display_name or account.account_uid}",
        )

    @staticmethod
    def _connection(intent: OrderIntent, account: DeskAccount | None) -> Stage:
        if account is None:
            return Stage(stage="connection", passed=False, detail="no account")
        state = account.connection_state
        if state is ConnectionState.LIVE:
            return Stage(stage="connection", passed=True, detail="the connection is live")
        if state is ConnectionState.DEGRADED and intent.priority <= Priority.CANCEL:
            return Stage(
                stage="connection",
                passed=True,
                detail=(
                    "the connection is degraded; cancels and flattens are still "
                    "permitted so exposure can always be reduced"
                ),
            )
        return Stage(
            stage="connection",
            passed=False,
            detail=f"the connection is {state.value}, so nothing can be sent",
        )

    @staticmethod
    def _source_evidence(intent: OrderIntent) -> Stage:
        """What backs this order: a judged strategy, or an attested own-account copy.

        These are the only two, and they are genuinely different claims. A
        strategy-driven order is checked against the judge's verdict by the gate
        itself, and this rung only records which case applies. A copied trade
        carries no verdict because there is no strategy to judge — the evidence
        is the operator's statement that both accounts are theirs, which is the
        condition every researched firm attaches to permitting copying at all.

        An order with neither is refused. That is stricter than the gate alone,
        which would have nothing to say about an intent that named no strategy.
        """
        if intent.kind is not CommandKind.PLACE:
            return Stage(
                stage="source_evidence",
                passed=True,
                detail=f"a {intent.kind.value} needs no source evidence",
            )
        if intent.strategy_id:
            return Stage(
                stage="source_evidence",
                passed=True,
                detail=(
                    f"strategy '{intent.strategy_id}'; its verdict is checked by the "
                    "pre-trade gate"
                ),
            )
        if intent.attestation:
            return Stage(
                stage="source_evidence",
                passed=True,
                detail=(
                    f"a copied trade, attested by {intent.attestation} as being between "
                    "accounts of one owner. It carries no strategy verdict because "
                    "there is no strategy to judge."
                ),
            )
        return Stage(
            stage="source_evidence",
            passed=False,
            unknown=True,
            detail=(
                "this order names no strategy and carries no ownership attestation, so "
                "nothing stands behind it. A strategy order must name a judged "
                "strategy; a copied trade must be attested as being between accounts "
                "of one owner."
            ),
        )

    @staticmethod
    def _compatibility(use_case: UseCase, report: CompatibilityReport | None) -> Stage:
        if report is None:
            return Stage(
                stage="compatibility",
                passed=False,
                unknown=True,
                detail=(
                    f"no programme policy has been evaluated for this account, so it is "
                    f"not known whether {use_case.value.replace('_', ' ')} is permitted. "
                    "An unrecorded rule is not permission."
                ),
            )
        if report.permits_automatic_action:
            return Stage(
                stage="compatibility",
                passed=True,
                detail=f"{use_case.value.replace('_', ' ')} is permitted on this account",
            )
        return Stage(
            stage="compatibility",
            passed=False,
            unknown=report.verdict is Permission.UNKNOWN,
            detail="; ".join(report.blocking_reasons) or report.verdict.value,
        )

    @staticmethod
    def _direction(
        intent: OrderIntent, account: DeskAccount | None, context: DeskContext
    ) -> Stage:
        if intent.kind is not CommandKind.PLACE or intent.side is None or account is None:
            return Stage(
                stage="cross_account_direction",
                passed=True,
                detail="not an opening order",
            )
        guard = DirectionGuard(context.product_groups)
        reason: CompatibilityReason = guard.check(
            positions=context.owner_positions,
            account_uid=intent.account_uid,
            symbol=intent.symbol,
            delta=intent.side.sign * (intent.quantity or 0),
            policy=None,
        )
        return Stage(
            stage="cross_account_direction",
            passed=reason.verdict.permits_automatic_action,
            unknown=reason.verdict is Permission.UNKNOWN,
            detail=reason.detail,
        )

    @staticmethod
    def _news(intent: OrderIntent, context: DeskContext) -> Stage:
        news = context.news
        if news is None:
            return Stage(
                stage="news_blackout", passed=True, detail="no news policy is in force"
            )
        if not news.restricted:
            detail = "no blackout window is open"
            if news.next_event is not None and news.minutes_to_next is not None:
                detail += (
                    f"; '{news.next_event.title}' in {news.minutes_to_next:.0f} minute(s)"
                )
            if news.gaps:
                detail += f" ({len(news.gaps)} gap(s) in the calendar)"
            return Stage(stage="news_blackout", passed=True, detail=detail)
        if news.action == "warn":
            # A warning tightens nothing on its own. It is reported and the
            # order proceeds, which is what the operator asked for by choosing
            # "warn" rather than "block".
            return Stage(
                stage="news_blackout",
                passed=True,
                detail=(
                    "inside a window for "
                    + ", ".join(event.title for event in news.events)
                    + "; the policy warns rather than blocks"
                ),
            )
        if intent.priority <= Priority.CANCEL:
            return Stage(
                stage="news_blackout",
                passed=True,
                detail="a blackout does not prevent reducing exposure",
            )
        return Stage(
            stage="news_blackout",
            passed=False,
            detail=(
                "inside a blackout window for "
                + ", ".join(event.title for event in news.events)
            ),
        )

    @staticmethod
    def _rules(assessment: AccountAssessment) -> Stage:
        if assessment.can_trade:
            return Stage(
                stage="account_rules",
                passed=True,
                detail=(
                    f"the rule engine reports {assessment.level.value} with "
                    f"{len(assessment.breaches)} advisory breach(es)"
                    if assessment.breaches
                    else f"the rule engine reports {assessment.level.value}"
                ),
            )
        breached = ", ".join(
            status.label
            for status in assessment.statuses
            if status.key in assessment.breaches
        )
        return Stage(
            stage="account_rules",
            passed=False,
            detail=f"the account's rules are breached: {breached or 'see the assessment'}",
        )

    def _gate(
        self, intent: OrderIntent, account: DeskAccount | None, context: DeskContext
    ) -> GateDecision:
        """The existing pre-trade gate, called with a context built from the desk.

        Not a reimplementation and not a second ladder: the same `screen` the
        fund's own execution path uses, so an order refused there is refused
        here for the same reason and in the same words.
        """
        positions = dict(account.positions) if account else {}
        working = dict(account.working) if account else {}
        # What the gate's validation check is given. A strategy order names its
        # strategy and the gate refuses one the judge has not passed — this
        # module supplies no verdict for it and cannot. A copied trade names the
        # attestation instead, carrying the verdict `ATTESTED`, which is a
        # different and true claim rather than a judge verdict: the operator
        # said both accounts are theirs. `_source_evidence` has already refused
        # anything with neither, so there is no third path through here.
        strategy_id = intent.strategy_id
        verdicts = dict(context.verdicts)
        eligible = context.eligible_verdicts
        if not strategy_id and intent.attestation:
            strategy_id = f"attested-copy:{intent.attestation}"
            verdicts[strategy_id] = "ATTESTED"
            eligible = (*eligible, "ATTESTED")

        proposed = ProposedOrder(
            order_id=intent.intent_id,
            symbol=intent.symbol,
            side=GateSide.BUY if intent.side and intent.side.value == "buy" else GateSide.SELL,
            quantity=intent.quantity or 1,
            order_type="limit" if intent.limit_price else "market",
            limit_price=intent.limit_price,
            strategy_id=strategy_id,
            portfolio_id="propdesk",
            intent="open",
            reference_price=intent.reference_price,
            data_as_of=(
                context.market[intent.symbol].last_price_at
                if intent.symbol in context.market
                else None
            ),
            created_at=intent.created_at,
        )
        gate_context = GateContext(
            now=context.now,
            limits=context.limits,
            capital=context.capital,
            instruments=context.instruments,
            market=context.market,
            positions=positions,
            working=working,
            verdicts=verdicts,
            eligible_verdicts=eligible,
            current_gross=context.current_gross,
        )
        return screen_order(proposed, gate_context)


def cleared(decisions: Iterable[DeskDecision]) -> tuple[DeskDecision, ...]:
    return tuple(d for d in decisions if d.cleared)


def refused(decisions: Iterable[DeskDecision]) -> tuple[DeskDecision, ...]:
    return tuple(d for d in decisions if not d.cleared)


def summarise(decisions: Iterable[DeskDecision]) -> dict[str, Any]:
    """A count per blocking stage. What the Activity panel leads with."""
    items = list(decisions)
    per_stage: dict[str, int] = {}
    for decision in items:
        for stage in decision.blocking_stages:
            per_stage[stage] = per_stage.get(stage, 0) + 1
    return {
        "screened": len(items),
        "cleared": sum(1 for d in items if d.cleared),
        "dispatched": sum(1 for d in items if d.dispatched),
        "accepted": sum(
            1 for d in items if d.acknowledgement and d.acknowledgement.accepted
        ),
        "refused_by_stage": dict(sorted(per_stage.items())),
    }

"""The Prop Desk over HTTP, and the service that assembles its context.

The service exists because `forge.propdesk.desk` is deliberately a pure
function of a `DeskContext`: it decides, it does not fetch. Somebody has to
assemble that context from the account store, the policy store, the fabric's
health and the calendar — and doing it in one place is what keeps a screening
decision reproducible from the record rather than dependent on what a network
did at the moment.

Three things this layer is careful about.

**It reads verdicts through the path the Evidence screen uses.** `verdict_for`
is injected by `forge_api.control`, the same callable the fund uses, so a
strategy the judge failed cannot be allocated by a desk that believes otherwise.

**It never invents an account state.** A prop account with no recorded snapshot
produces a `DeskAccount` with no rules and no state, and the desk's
`account_rules` rung refuses it. The alternative — defaulting to the starting
balance — would show a comfortable drawdown buffer for a position that might be
open, which is the failure `forge.prop.accounts` already refuses to commit.

**Every route that writes is also an action.** The registry in
`forge_api.actions` is the bounded verb set, and these routes call the same
methods, so there is no HTTP back door and no verb the assistant can reach that
the interface cannot.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope
from forge.execution.gate import InstrumentRule, MarketState
from forge.prop.account import AccountRules, AccountState
from forge.prop.account import assess as assess_account
from forge.prop.accounts import PropAccountStore
from forge.propdesk import (
    POLICY_QUESTIONS,
    SUGGESTED_SOURCES,
    AccountSnapshot,
    Advice,
    Allocation,
    AllocationConstraints,
    Allocator,
    AuthMethod,
    BrokerConnection,
    CalendarRegistry,
    CompatibilityReport,
    ConnectionState,
    CopyGroup,
    CopyResolver,
    CredentialBroker,
    CredentialRecord,
    DeskAccount,
    DeskContext,
    Environment,
    ExecutionFabric,
    FabricError,
    Follower,
    FredReleaseCalendar,
    Impact,
    InstrumentCatalogue,
    Leader,
    LocalView,
    ManualCalendar,
    MappingError,
    NewsPolicy,
    Permission,
    Platform,
    PropDesk,
    PropDeskStore,
    PropProgramPolicy,
    Provider,
    ReconciliationEngine,
    ReconciliationPolicy,
    SizingPolicy,
    StrategyHealth,
    Trigger,
    UseCase,
    assess_news,
    default_catalogue,
    diff_allocations,
    evaluate_compatibility,
    new_connection_id,
    new_group_id,
    redact,
)
from forge.propdesk import (
    catalogue as identity_catalogue,
)
from forge.propdesk.adapters import (
    SimulatedAccount,
    SimulatedAdapter,
    projectx_adapter,
    rithmic_adapter,
    tradovate_adapter,
)
from pydantic import ValidationError

#: Which adapter serves which provider. The simulator executes; the other three
#: declare their provider's interface and refuse every command with the reason.
#: One mapping, so "which providers can this build actually reach" has a single
#: answer that the API, the action registry and the interface all read.
ADAPTERS: dict[Provider, Callable[[], Any]] = {
    Provider.SIMULATED: SimulatedAdapter,
    Provider.RITHMIC: rithmic_adapter,
    Provider.TRADOVATE: tradovate_adapter,
    Provider.PROJECTX: projectx_adapter,
}


def adapter_for(connection: BrokerConnection) -> Any:
    """The adapter that serves this connection.

    The simulator needs to know which connection and credential it belongs to so
    the account keys it mints are provider-qualified like any other provider's;
    the three declared adapters need nothing, because they do nothing.
    """
    if connection.provider is Provider.SIMULATED:
        return SimulatedAdapter(
            connection_id=connection.connection_id,
            credential_ref=connection.credential_ref,
        )
    return ADAPTERS[connection.provider]()


class PropDeskError(Exception):
    """A refusal with a reason the caller can show verbatim."""


class PropDeskService:
    """Assembles the desk's context, and owns the objects that outlive a request."""

    def __init__(
        self,
        *,
        store: PropDeskStore,
        prop_accounts: PropAccountStore,
        verdict_for: Callable[[str], str | None],
        health_for: Callable[[str], StrategyHealth] | None = None,
        catalogue: InstrumentCatalogue | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.prop_accounts = prop_accounts
        self.verdict_for = verdict_for
        self.health_for = health_for
        self.catalogue = catalogue or default_catalogue()
        self._now = now

        self.fabric = ExecutionFabric(now=now)
        self.desk = PropDesk(self.fabric, now=now)
        self.resolver = CopyResolver(self.catalogue, now=now)
        self.reconciler = ReconciliationEngine(ReconciliationPolicy(), now=now)
        self.credentials = CredentialBroker(now=now)
        self.calendars = CalendarRegistry([ManualCalendar(), FredReleaseCalendar()])
        #: Live provider state per account, rebuilt from snapshots rather than
        #: accumulated from events — the provider is authoritative.
        self.views: dict[str, LocalView] = {}
        self._restore()

    # ── startup ──────────────────────────────────────────────────────────────
    def _restore(self) -> None:
        """Re-register stored connections, disconnected.

        A connection that was live when the process stopped is not live now.
        Restoring it as `LIVE` would let the first order of the session be sent
        into a socket that does not exist.
        """
        for connection in self.store.connections():
            if connection.provider not in ADAPTERS:
                continue
            self.fabric.register(
                connection.model_copy(update={"state": ConnectionState.DISCONNECTED}),
                adapter_for(connection),
            )
            for account in self.store.accounts(connection.connection_id):
                self.fabric.bind_account(account.account_uid, connection.connection_id)

    # ── the layer model ──────────────────────────────────────────────────────
    def providers(self) -> dict[str, Any]:
        published = identity_catalogue()
        published["adapters"] = {
            provider.value: (
                "executes against a local simulator"
                if provider is Provider.SIMULATED
                else "declared; refuses every command, no live connector in this build"
            )
            for provider in ADAPTERS
        }
        published["required_work"] = {
            provider.value: factory().work.as_dict()
            for provider, factory in ADAPTERS.items()
            if provider is not Provider.SIMULATED
        }
        return published

    # ── connections and accounts ─────────────────────────────────────────────
    def connections(self) -> dict[str, Any]:
        health = {h.connection_id: h.as_dict() for h in self.fabric.health()}
        return {
            "connections": [
                {**connection.as_dict(), "health": health.get(connection.connection_id)}
                for connection in self.store.connections()
            ],
            "accounts": [account.as_dict() for account in self.store.accounts()],
        }

    def create_connection(
        self,
        *,
        provider: str,
        label: str,
        environment: str = "simulation",
        credential_ref: str = "",
        platform: str | None = None,
    ) -> dict[str, Any]:
        try:
            chosen = Provider(provider)
            env = Environment(environment)
        except ValueError as exc:
            raise PropDeskError(
                f"'{provider}' / '{environment}' is not a provider and environment "
                f"this build knows. Providers: {', '.join(p.value for p in Provider)}."
            ) from exc
        if platform is not None:
            try:
                Platform(platform)
            except ValueError as exc:
                raise PropDeskError(f"'{platform}' is not a known platform.") from exc

        now = self._now()
        connection = BrokerConnection(
            connection_id=new_connection_id(chosen, label, now),
            provider=chosen,
            environment=env,
            label=label,
            credential_ref=credential_ref or f"{chosen.value}-unconfigured",
            platform=Platform(platform) if platform else None,
            created_at=now,
            updated_at=now,
        )
        self.fabric.register(connection, adapter_for(connection))
        self.store.save_connection(connection)
        return {
            "connection": connection.as_dict(),
            "live_connector_implemented": chosen is Provider.SIMULATED,
            "note": (
                "The simulator executes against a local model and labels every fill "
                "simulated."
                if chosen is Provider.SIMULATED
                else (
                    f"AlgoForge has no live {chosen.value} connector. The connection is "
                    "recorded so the desk can reason about it; every command to it is "
                    "refused with the reason."
                )
            ),
        }

    def connect(self, connection_id: str) -> dict[str, Any]:
        connection = self.store.connection(connection_id)
        if connection is None:
            raise PropDeskError(f"No connection '{connection_id}'.")
        record = self.store.credential(connection.credential_ref) or CredentialRecord(
            credential_ref=connection.credential_ref,
            provider=connection.provider,
            auth_method=AuthMethod.NONE,
            label=connection.label,
            created_at=self._now(),
        )
        available, missing = self.credentials.available(record)
        if not available:
            raise PropDeskError(
                "This connection's credentials could not be resolved, so nothing was "
                "attempted: " + "; ".join(missing)
            )
        updated = self.fabric.connect(connection_id, record)
        self.store.save_connection(updated)
        if updated.state.value == "failed":
            return {"connection": updated.as_dict(), "connected": False,
                    "reason": updated.last_error}
        accounts = self.fabric.discover_accounts(connection_id)
        for account in accounts:
            self.store.save_account(account)
        return {
            "connection": updated.as_dict(),
            "connected": True,
            "accounts": [account.as_dict() for account in accounts],
        }

    def disconnect(self, connection_id: str) -> dict[str, Any]:
        updated = self.fabric.disconnect(connection_id, reason="the operator disconnected")
        self.store.save_connection(updated)
        return {"connection": updated.as_dict()}

    def seed_simulator(
        self, connection_id: str, *, account_id: str, balance: float = 50_000.0,
        max_contracts: int | None = None,
    ) -> dict[str, Any]:
        """Add an account to a simulated connection.

        The only way an account comes into existence without a provider, and it
        exists for exactly one reason: so the desk can be exercised end to end
        without credentials. It is refused on every other provider, because an
        account this application invented on a real connection would be a
        fiction sitting beside real ones.
        """
        runtime = self.fabric.runtime(connection_id)
        adapter = runtime.adapter
        if not isinstance(adapter, SimulatedAdapter):
            raise PropDeskError(
                "Accounts can only be added to a simulated connection. A real "
                "provider's accounts are discovered from the provider."
            )
        account = adapter.add_account(
            SimulatedAccount(
                account_id,
                display_name=account_id,
                balance=balance,
                max_contracts=max_contracts,
            )
        )
        adapter.mark("MNQ", 20_000.0)
        self.fabric.bind_account(account.account_uid, connection_id)
        self.store.save_account(account)
        return {"account": account.as_dict()}

    # ── policies ─────────────────────────────────────────────────────────────
    def policies(self) -> dict[str, Any]:
        return {
            "policies": [policy.as_dict() for policy in self.store.policies()],
            "questions": [
                {"key": key, "question": question} for key, question in POLICY_QUESTIONS
            ],
            "values": [value.value for value in Permission],
            "note": (
                "AlgoForge ships no firm's rules and asserts nothing about what any "
                "named firm's contract says. Every permission starts UNKNOWN, and "
                "UNKNOWN is never permission."
            ),
        }

    def save_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        try:
            parsed = PropProgramPolicy.model_validate(policy)
        except ValidationError as exc:
            raise PropDeskError(
                f"That is not a valid programme policy: {exc.errors()[0]['msg']}"
            ) from exc
        return {"policy": self.store.save_policy(parsed).as_dict()}

    def link_policy(self, account_uid: str, policy_id: str | None) -> dict[str, Any]:
        account = self.store.account(account_uid)
        if account is None:
            raise PropDeskError(f"No account '{account_uid}'.")
        if policy_id and self.store.policy(policy_id) is None:
            raise PropDeskError(f"No policy '{policy_id}'.")
        updated = account.model_copy(update={"policy_id": policy_id})
        self.store.save_account(updated)
        return {"account": updated.as_dict()}

    def link_prop_account(self, account_uid: str, prop_account_id: str | None) -> dict[str, Any]:
        account = self.store.account(account_uid)
        if account is None:
            raise PropDeskError(f"No account '{account_uid}'.")
        if prop_account_id and self.prop_accounts.get(prop_account_id) is None:
            raise PropDeskError(f"No prop account '{prop_account_id}'.")
        updated = account.model_copy(update={"prop_account_id": prop_account_id})
        self.store.save_account(updated)
        return {"account": updated.as_dict()}

    # ── copy groups ──────────────────────────────────────────────────────────
    def groups(self) -> dict[str, Any]:
        return {"groups": [group.as_dict() for group in self.store.groups()]}

    def create_group(
        self, *, name: str, leader_account_uid: str, attested_by: str
    ) -> dict[str, Any]:
        if not attested_by.strip():
            raise PropDeskError(
                "A copy group needs an attestation that every account in it belongs to "
                "one owner. Copying between different people's accounts is prohibited "
                "at every firm the research examined that addressed it."
            )
        if self.store.account(leader_account_uid) is None:
            raise PropDeskError(f"No account '{leader_account_uid}' to lead this group.")
        now = self._now()
        group = CopyGroup(
            group_id=new_group_id(name, leader_account_uid, now),
            name=name,
            leader=Leader(account_uid=leader_account_uid),
            owner_attested_by=attested_by.strip(),
            created_at=now,
            updated_at=now,
        )
        return {"group": self.store.save_group(group).as_dict()}

    def add_follower(
        self, *, group_id: str, account_uid: str, sizing: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        group = self._group(group_id)
        if self.store.account(account_uid) is None:
            raise PropDeskError(f"No account '{account_uid}'.")
        try:
            policy = SizingPolicy.model_validate(sizing or {})
        except ValidationError as exc:
            raise PropDeskError(
                f"That is not a valid sizing policy: {exc.errors()[0]['msg']}"
            ) from exc
        try:
            updated = group.with_follower(Follower(account_uid=account_uid, sizing=policy))
        except Exception as exc:
            raise PropDeskError(str(exc)) from exc
        return {"group": self.store.save_group(updated).as_dict()}

    def remove_follower(self, *, group_id: str, account_uid: str) -> dict[str, Any]:
        updated = self._group(group_id).without_follower(account_uid)
        return {"group": self.store.save_group(updated).as_dict()}

    def set_group_active(self, *, group_id: str, active: bool) -> dict[str, Any]:
        group = self._group(group_id)
        updated = group.model_copy(update={"active": bool(active), "updated_at": self._now()})
        return {"group": self.store.save_group(updated).as_dict()}

    def _group(self, group_id: str) -> CopyGroup:
        group = self.store.group(group_id)
        if group is None:
            raise PropDeskError(f"No copy group '{group_id}'.")
        return group

    # ── the copy pass ────────────────────────────────────────────────────────
    def copy_pass(
        self, *, group_id: str, symbol: str, leader_position: int, dry_run: bool = True
    ) -> dict[str, Any]:
        """Evaluate a group against a leader position, and optionally dispatch.

        `dry_run` is the default and is not a courtesy: an operator asking "what
        would this do" must be able to find out without it happening.
        """
        group = self._group(group_id)
        positions = {
            follower.account_uid: self._net(follower.account_uid, symbol)
            for follower in group.followers
        }
        try:
            decisions = self.resolver.resolve(
                group=group,
                leader_root=symbol.upper(),
                leader_position=int(leader_position),
                follower_positions=positions,
                reference_price=self._mark(symbol),
                leader_equity=self._equity(group.leader.account_uid),
                follower_equity={
                    follower.account_uid: self._equity(follower.account_uid) or 0.0
                    for follower in group.followers
                },
                today=self._now(),
            )
        except MappingError as exc:
            raise PropDeskError(str(exc)) from exc

        intents = [decision.intent for decision in decisions if decision.intent]
        context = self.context(
            [d.follower_account_uid for d in decisions], use_case=UseCase.COPY_FOLLOWER
        )
        screened = (
            self.desk.screen_all(intents, context)
            if dry_run
            else self.desk.dispatch(intents, context)
        )
        if not dry_run:
            for decision in screened:
                self.store.record_decision(decision)

        from forge.propdesk import assess_health

        health = assess_health(
            group=group,
            decisions=decisions,
            unavailable_accounts=frozenset(
                h.connection_id for h in self.fabric.unhealthy()
            ),
            last_leader_event_at=self._now(),
        )
        return {
            "group": group.as_dict(),
            "dry_run": dry_run,
            "copy_decisions": [decision.as_dict() for decision in decisions],
            "desk_decisions": [decision.as_dict() for decision in screened],
            "health": health.as_dict(),
        }

    # ── reconciliation ───────────────────────────────────────────────────────
    def reconcile(self, account_uid: str, trigger: str = "periodic") -> dict[str, Any]:
        try:
            moment = Trigger(trigger)
        except ValueError as exc:
            raise PropDeskError(
                f"'{trigger}' is not a reconciliation trigger. Choose from "
                f"{', '.join(t.value for t in Trigger)}."
            ) from exc
        try:
            snapshot = self.fabric.snapshot(account_uid)
        except FabricError as exc:
            raise PropDeskError(str(exc)) from exc
        except Exception as exc:
            raise PropDeskError(
                f"the provider could not be asked for a snapshot: {exc}"
            ) from exc

        local = self.views.get(account_uid, LocalView(account_uid=account_uid))
        report = self.reconciler.reconcile(local=local, snapshot=snapshot, trigger=moment)
        # The provider is authoritative: adopt rather than merge.
        self.views[account_uid] = self.reconciler.adopt(snapshot)
        self.store.record_reconciliation(report)
        return {"report": report.as_dict()}

    # ── allocation ───────────────────────────────────────────────────────────
    def allocation_state(self) -> dict[str, Any]:
        return {
            "allocations": [item.as_dict() for item in self.store.allocations()],
            "constraints": self.store.constraints().model_dump(mode="json"),
            "history": list(self.store.history(limit=200)),
        }

    def strategy_health(self, strategy_ids: tuple[str, ...]) -> tuple[StrategyHealth, ...]:
        """Health for each strategy, from the judge and whatever evidence exists.

        With no `health_for` attached this reports the verdict and nothing else,
        which grades every strategy UNPROVEN and allocates none of them. That is
        the correct answer for a desk that has not been given evidence, and it
        is visible rather than silent.
        """
        if self.health_for is not None:
            return tuple(self.health_for(strategy_id) for strategy_id in strategy_ids)
        return tuple(
            StrategyHealth(
                strategy_id=strategy_id,
                verdict=self.verdict_for(strategy_id),
                as_of=self._now(),
                as_of_basis="judge verdict only",
                limitations=(
                    "No out-of-sample drawdown estimate is attached to this strategy, so "
                    "an allocation to it cannot be sized and will be refused.",
                ),
            )
            for strategy_id in strategy_ids
        )

    def plan_allocation(
        self, *, strategy_ids: tuple[str, ...], advice: tuple[dict[str, Any], ...] = ()
    ) -> dict[str, Any]:
        constraints = self.store.constraints()
        allocator = Allocator(constraints, now=self._now)
        accounts = tuple(self._allocation_snapshot(a.account_uid) for a in self.store.accounts())
        healths = self.strategy_health(strategy_ids)
        # A pairing with no evaluated compatibility is simply absent from this
        # map, and the allocator treats an absent one as UNKNOWN and refuses it.
        # Storing a `None` would be the same fact stated twice, and the second
        # statement is the one somebody eventually reads as "no objection".
        compatibility = {}
        for snapshot in accounts:
            report = self._compatibility(
                snapshot.account_uid, UseCase.ALGORITHMIC_ALLOCATION
            )
            if report is None:
                continue
            for health in healths:
                compatibility[(snapshot.account_uid, health.strategy_id)] = report
        parsed: list[Advice] = []
        for item in advice:
            try:
                parsed.append(Advice.model_validate(item))
            except ValidationError as exc:
                raise PropDeskError(
                    f"That is not a valid recommendation: {exc.errors()[0]['msg']}"
                ) from exc

        plan = allocator.plan(
            accounts=accounts,
            healths=healths,
            compatibility=compatibility,
            changes_today={
                snapshot.account_uid: self.store.changes_today(snapshot.account_uid)
                for snapshot in accounts
            },
            advice=parsed,
        )
        return {"plan": plan.as_dict()}

    def apply_allocation(
        self, *, allocations: tuple[dict[str, Any], ...], actor: str
    ) -> dict[str, Any]:
        """Record an allocation. It places nothing.

        An allocation says which strategy an account should be running and at
        what size. Turning that into orders is the strategy's job, and every one
        of those orders still climbs the desk's ladder.
        """
        parsed: list[Allocation] = []
        for item in allocations:
            try:
                parsed.append(Allocation.model_validate(item))
            except ValidationError as exc:
                raise PropDeskError(
                    f"That is not a valid allocation: {exc.errors()[0]['msg']}"
                ) from exc
        unconfirmed = [a for a in parsed if not a.actionable]
        if unconfirmed:
            raise PropDeskError(
                "These allocations need your confirmation before they can be applied: "
                + ", ".join(f"{a.strategy_id} on {a.account_uid}" for a in unconfirmed)
            )

        previous = self.store.allocations()
        changes = diff_allocations(
            previous=previous, proposed=parsed, at=self._now(), actor=actor
        )
        for allocation in parsed:
            self.store.save_allocation(allocation)
        applied = {a.account_uid for a in parsed}
        for stale in previous:
            if stale.account_uid not in applied:
                self.store.clear_allocation(stale.account_uid)
        for change in changes:
            self.store.record_change(change)
        return {
            "applied": [a.as_dict() for a in parsed],
            "changes": [c.as_dict() for c in changes],
        }

    def save_constraints(self, constraints: dict[str, Any]) -> dict[str, Any]:
        try:
            parsed = AllocationConstraints.model_validate(constraints)
        except ValidationError as exc:
            raise PropDeskError(
                f"Those are not valid allocation constraints: {exc.errors()[0]['msg']}"
            ) from exc
        return {"constraints": self.store.save_constraints(parsed).model_dump(mode="json")}

    # ── news ─────────────────────────────────────────────────────────────────
    def news(self, *, days: int = 7) -> dict[str, Any]:
        start = self._now()
        end = start + timedelta(days=max(1, min(days, 60)))
        events, problems = self.calendars.events(start, end)
        stored = self.store.events(start, end)
        merged = {event.event_id: event for event in (*stored, *events)}
        policy = self.store.news_policy()
        assessment = assess_news(
            policy=policy, events=merged.values(), at=start, problems=problems
        )
        return {
            "events": [event.as_dict() for event in sorted(
                merged.values(), key=lambda e: e.at
            )],
            "policy": policy.model_dump(mode="json"),
            "assessment": assessment.as_dict(),
            "availability": [item.as_dict() for item in self.calendars.availability()],
            "sources": list(SUGGESTED_SOURCES),
        }

    def record_event(
        self, *, title: str, at: str, impact: str = "", country: str = "US", note: str = ""
    ) -> dict[str, Any]:
        try:
            moment = datetime.fromisoformat(at)
        except ValueError as exc:
            raise PropDeskError(f"'{at}' is not an ISO-8601 timestamp.") from exc
        calendar = ManualCalendar()
        event = calendar.record(
            title=title,
            at=moment if moment.tzinfo else moment.replace(tzinfo=UTC),
            impact=Impact(impact) if impact in {i.value for i in Impact} else None,
            country=country,
            note=note,
        )
        return {"event": self.store.save_event(event).as_dict()}

    def save_news_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        try:
            parsed = NewsPolicy.model_validate(policy)
        except ValidationError as exc:
            raise PropDeskError(
                f"That is not a valid news policy: {exc.errors()[0]['msg']}"
            ) from exc
        return {"policy": self.store.save_news_policy(parsed).model_dump(mode="json")}

    # ── activity ─────────────────────────────────────────────────────────────
    def activity(self, *, limit: int = 100) -> dict[str, Any]:
        return {
            "decisions": list(self.store.decisions(limit=limit)),
            "reconciliations": list(self.store.reconciliations(limit=limit)),
            "allocation_changes": list(self.store.history(limit=limit)),
            "health": [item.as_dict() for item in self.fabric.health()],
            "counts": self.store.counts(),
        }

    # ── context assembly ─────────────────────────────────────────────────────
    def context(
        self, account_uids: list[str], *, use_case: UseCase = UseCase.COPY_FOLLOWER
    ) -> DeskContext:
        now = self._now()
        accounts = {
            uid: self._desk_account(uid, use_case) for uid in dict.fromkeys(account_uids)
        }
        # Every account of this owner, not only the ones in this pass: the
        # cross-account direction rule is written at the owner level.
        owner = {
            account.account_uid: self._book(account.account_uid)
            for account in self.store.accounts()
        }
        instruments: dict[str, InstrumentRule] = {}
        market: dict[str, MarketState] = {}
        for instrument in self.catalogue.all():
            instruments[instrument.root] = InstrumentRule(
                symbol=instrument.root, tradable=True, multiplier=instrument.multiplier
            )
            price = self._mark(instrument.root)
            if price is not None:
                market[instrument.root] = MarketState(
                    symbol=instrument.root, open=True, last_price=price, last_price_at=now
                )
        news = self.news(days=1)["assessment"]
        from forge.propdesk import NewsAssessment

        return DeskContext(
            now=now,
            accounts=accounts,
            owner_positions=owner,
            instruments=instruments,
            market=market,
            verdicts={},
            use_case=use_case,
            product_groups={i.root: i.group for i in self.catalogue.all()},
            news=NewsAssessment.model_validate(news),
        )

    def _desk_account(self, account_uid: str, use_case: UseCase) -> DeskAccount:
        account = self.store.account(account_uid)
        if account is None:
            return DeskAccount(account_uid=account_uid)
        rules, state = self._rules_and_state(account.prop_account_id)
        try:
            connection_state = self.fabric.runtime(account.connection_id).connection.state
        except FabricError:
            connection_state = ConnectionState.DISCONNECTED
        return DeskAccount(
            account_uid=account_uid,
            display_name=account.name,
            connection_state=connection_state,
            rules=rules,
            state=state,
            compatibility=self._compatibility(account_uid, use_case),
            positions=self._book(account_uid),
            equity=account.equity,
        )

    def _rules_and_state(
        self, prop_account_id: str | None
    ) -> tuple[AccountRules | None, AccountState | None]:
        """The account's contract and what was last recorded about it.

        Both `None` when nothing is linked or nothing has been recorded. The
        desk refuses an account it cannot assess, which is the point: a balance
        nobody stated is not a balance.
        """
        if not prop_account_id:
            return None, None
        account = self.prop_accounts.get(prop_account_id)
        if account is None:
            return None, None
        latest = self.prop_accounts.latest(prop_account_id)
        return account.rules, (latest[0] if latest else None)

    def _compatibility(
        self, account_uid: str, use_case: UseCase
    ) -> CompatibilityReport | None:
        account = self.store.account(account_uid)
        if account is None:
            return None
        policy = self.store.policy(account.policy_id) if account.policy_id else None
        rules, _ = self._rules_and_state(account.prop_account_id)
        return evaluate_compatibility(
            account_uid=account_uid,
            use_case=use_case,
            policy=policy,
            capability_order_types=account.capability.order_types,
            capability_max_contracts=account.capability.max_contracts,
            rules_max_contracts=rules.max_position_contracts if rules else None,
            account_can_trade=account.capability.can_trade,
            at=self._now(),
        )

    def _allocation_snapshot(self, account_uid: str) -> AccountSnapshot:
        account = self.store.account(account_uid)
        rules, state = self._rules_and_state(account.prop_account_id if account else None)
        if rules is None or state is None:
            return AccountSnapshot(
                account_uid=account_uid,
                display_name=account.name if account else account_uid,
            )
        assessment = assess_account(rules, state)
        current = self.store.allocation(account_uid)
        return AccountSnapshot(
            account_uid=account_uid,
            display_name=account.name if account else account_uid,
            can_trade=assessment.can_trade,
            buffer=round(assessment.equity - assessment.loss_floor, 2),
            drawdown=round(max(0.0, state.high_water_equity - state.equity), 2),
            max_contracts=rules.max_position_contracts,
            current_strategy_id=current.strategy_id if current else "",
            rules_id=assessment.rules_id,
            rules_level=assessment.level.value,
            equity=assessment.equity,
        )

    def _book(self, account_uid: str) -> dict[str, int]:
        view = self.views.get(account_uid)
        if view is None:
            return {}
        return {p.symbol: p.quantity for p in view.positions if p.quantity}

    def _net(self, account_uid: str, symbol: str) -> int:
        return self._book(account_uid).get(symbol.upper(), 0)

    def _equity(self, account_uid: str) -> float | None:
        account = self.store.account(account_uid)
        return account.equity if account else None

    def _mark(self, symbol: str) -> float | None:
        """The reference price, from whichever adapter holds one.

        `None` when nobody does, and the simulator then rests the order rather
        than inventing a fill — which is the honest outcome for a desk with no
        market-data feed of its own.
        """
        for runtime in self.fabric._runtimes.values():
            adapter = runtime.adapter
            marks = getattr(adapter, "_marks", None)
            if isinstance(marks, dict) and symbol.upper() in marks:
                return float(marks[symbol.upper()])
        return None


def build_propdesk_router(service: PropDeskService) -> APIRouter:
    """HTTP over the same methods the action registry calls."""
    router = APIRouter(prefix="/api/v1/propdesk", tags=["propdesk"])

    def guard(
        call: Callable[[], dict[str, Any]],
    ) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=call())
        except PropDeskError as exc:
            raise HTTPException(
                status_code=409, detail={"code": "prop_desk_refused", "reason": str(exc)}
            ) from exc

    @router.get("/providers", response_model=ApiEnvelope[dict[str, Any]])
    def providers() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=service.providers())

    @router.get("/connections", response_model=ApiEnvelope[dict[str, Any]])
    def connections() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=redact(service.connections()))

    @router.post("/connections", response_model=ApiEnvelope[dict[str, Any]])
    def create_connection(body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.create_connection(**body))

    @router.post("/connections/{connection_id}/connect",
                 response_model=ApiEnvelope[dict[str, Any]])
    def connect(connection_id: str) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.connect(connection_id))

    @router.post("/connections/{connection_id}/disconnect",
                 response_model=ApiEnvelope[dict[str, Any]])
    def disconnect(connection_id: str) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.disconnect(connection_id))

    @router.post("/connections/{connection_id}/simulated-accounts",
                 response_model=ApiEnvelope[dict[str, Any]])
    def seed(connection_id: str, body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.seed_simulator(connection_id, **body))

    @router.get("/policies", response_model=ApiEnvelope[dict[str, Any]])
    def policies() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=service.policies())

    @router.post("/policies", response_model=ApiEnvelope[dict[str, Any]])
    def save_policy(body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.save_policy(body))

    @router.post("/accounts/{account_uid}/policy", response_model=ApiEnvelope[dict[str, Any]])
    def link_policy(account_uid: str, body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.link_policy(account_uid, body.get("policy_id")))

    @router.post("/accounts/{account_uid}/rules", response_model=ApiEnvelope[dict[str, Any]])
    def link_rules(account_uid: str, body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(
            lambda: service.link_prop_account(account_uid, body.get("prop_account_id"))
        )

    @router.get("/groups", response_model=ApiEnvelope[dict[str, Any]])
    def groups() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=service.groups())

    @router.post("/groups", response_model=ApiEnvelope[dict[str, Any]])
    def create_group(body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.create_group(**body))

    @router.post("/groups/{group_id}/followers", response_model=ApiEnvelope[dict[str, Any]])
    def add_follower(group_id: str, body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.add_follower(group_id=group_id, **body))

    @router.delete("/groups/{group_id}/followers/{account_uid}",
                   response_model=ApiEnvelope[dict[str, Any]])
    def remove_follower(group_id: str, account_uid: str) -> ApiEnvelope[dict[str, Any]]:
        return guard(
            lambda: service.remove_follower(group_id=group_id, account_uid=account_uid)
        )

    @router.post("/groups/{group_id}/active", response_model=ApiEnvelope[dict[str, Any]])
    def set_active(group_id: str, body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(
            lambda: service.set_group_active(
                group_id=group_id, active=bool(body.get("active", False))
            )
        )

    @router.post("/groups/{group_id}/pass", response_model=ApiEnvelope[dict[str, Any]])
    def copy_pass(group_id: str, body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.copy_pass(group_id=group_id, **body))

    @router.post("/accounts/{account_uid}/reconcile",
                 response_model=ApiEnvelope[dict[str, Any]])
    def reconcile(account_uid: str, body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(
            lambda: service.reconcile(account_uid, str(body.get("trigger", "periodic")))
        )

    @router.get("/allocation", response_model=ApiEnvelope[dict[str, Any]])
    def allocation() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=service.allocation_state())

    @router.post("/allocation/plan", response_model=ApiEnvelope[dict[str, Any]])
    def plan(body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(
            lambda: service.plan_allocation(
                strategy_ids=tuple(body.get("strategy_ids", ())),
                advice=tuple(body.get("advice", ())),
            )
        )

    @router.post("/allocation/apply", response_model=ApiEnvelope[dict[str, Any]])
    def apply(body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(
            lambda: service.apply_allocation(
                allocations=tuple(body.get("allocations", ())),
                actor=str(body.get("actor", "operator")),
            )
        )

    @router.post("/allocation/constraints", response_model=ApiEnvelope[dict[str, Any]])
    def constraints(body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.save_constraints(body))

    @router.get("/news", response_model=ApiEnvelope[dict[str, Any]])
    def news(days: int = 7) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=service.news(days=days))

    @router.post("/news/events", response_model=ApiEnvelope[dict[str, Any]])
    def record_event(body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.record_event(**body))

    @router.post("/news/policy", response_model=ApiEnvelope[dict[str, Any]])
    def news_policy(body: dict[str, Any]) -> ApiEnvelope[dict[str, Any]]:
        return guard(lambda: service.save_news_policy(body))

    @router.get("/activity", response_model=ApiEnvelope[dict[str, Any]])
    def activity(limit: int = 100) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=service.activity(limit=limit))

    return router


__all__ = [
    "ADAPTERS",
    "PropDeskError",
    "PropDeskService",
    "build_propdesk_router",
]

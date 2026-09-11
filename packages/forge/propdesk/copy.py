"""Leader, followers, and the arithmetic of keeping them together.

The commercial products model this as event replication: the leader places an
order, the copier places one per follower. That works until it does not, and the
research is a catalogue of when it does not — a follower rejected for a contract
cap, a follower whose connection dropped for ninety seconds, a cancel that
arrived after its order filled, a partial fill sequence that rounded up four
times.

This engine replicates events and converges on **net position targets**, and the
second of those is what makes it correct. A follower's target is a function of
the leader's *current net position*, the mapping and the multiplier — not of the
stream of deltas that produced it. So a duplicated event, a reordered event, or
a missed event all converge to the same place on the next evaluation, and a
rounding rule that over-sizes on each of four partial fills cannot accumulate
into an over-size of four.

Two execution policies, both from the research and both legitimate:

* `MIRROR_ORDERS` — followers receive the leader's working orders, modified and
  cancelled in step. Simultaneous resting orders; more requests against a shared
  rate budget; every pending-order edge case is live.
* `MARKET_ON_FILL` — followers receive a market order for each leader fill. No
  resting follower orders and far fewer requests; price risk between the
  leader's fill and the follower's.

`OFF` is the third, and it is per follower, because taking one account out of a
group must not require dismantling the group.

**Nothing here places an order.** The resolver returns intents. They pass
through compatibility, the account rule engine and the pre-trade gate in
`forge.propdesk.desk` before anything reaches the fabric, and there is no path
from this module to an adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.propdesk.fabric import CommandKind, OrderIntent, Priority
from forge.propdesk.instruments import (
    InstrumentCatalogue,
    MappedQuantity,
    MappingError,
    MappingPolicy,
    Rounding,
)
from forge.propdesk.orders import (
    OrderRecord,
    OrderState,
    OrderType,
    Side,
    TimeInForce,
    idempotency_key,
)


class CopyError(Exception):
    """The copy could not be resolved. No intent was produced."""


class ExecutionMode(StrEnum):
    MIRROR_ORDERS = "mirror_orders"
    MARKET_ON_FILL = "market_on_fill"
    OFF = "off"


class SizingMethod(StrEnum):
    #: A multiple of the leader's contract count.
    RATIO = "ratio"
    #: The same number of contracts every time, regardless of the leader.
    FIXED = "fixed"
    #: Scaled by the follower's equity against the leader's.
    EQUITY_SCALED = "equity_scaled"
    #: As many contracts as the follower's risk allowance buys at a stop distance.
    RISK_BUDGET = "risk_budget"


class SizingPolicy(FrozenModel):
    """How much of the leader's trade this follower takes."""

    method: SizingMethod = SizingMethod.RATIO
    #: The multiplier for RATIO; the contract count for FIXED.
    value: float = Field(default=1.0, ge=0)
    rounding: Rounding = Rounding.NEAREST
    mapping: MappingPolicy = MappingPolicy.SAME
    #: The follower's product, when it differs from the leader's.
    target_root: str = ""
    #: Hard ceilings the sizing may not exceed, whatever the arithmetic says.
    max_contracts_per_order: int | None = Field(default=None, gt=0)
    max_position_contracts: int | None = Field(default=None, gt=0)
    #: For RISK_BUDGET.
    risk_budget: float | None = Field(default=None, gt=0)
    stop_ticks: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _coherent(self) -> SizingPolicy:
        if self.method is SizingMethod.RISK_BUDGET and (
            self.risk_budget is None or self.stop_ticks is None
        ):
            raise ValueError(
                "risk-budget sizing needs a currency budget and a stop distance in "
                "ticks; without a stop the worst case is unbounded"
            )
        if self.method is SizingMethod.FIXED and self.value <= 0:
            raise ValueError("fixed sizing needs a positive contract count")
        if self.mapping is not MappingPolicy.SAME and not self.target_root:
            raise ValueError(
                f"the {self.mapping.value} mapping needs a target product to map into"
            )
        return self


class GroupPolicy(FrozenModel):
    """Decisions the operator makes once, for the whole group.

    Every one of these is a behaviour the research found in at least one
    commercial product, and in several cases found the shipped default
    disagreeing with the vendor's own documentation. They are explicit here and
    there is no hidden alternative.
    """

    #: Followers go flat when the leader does. The "follower protection" of one
    #: product and the "auto-close followers" of the other.
    flatten_followers_when_leader_flat: bool = True
    #: Seconds to wait before acting on the leader reaching flat. A leader that
    #: is momentarily flat between two legs of a scale is not finished.
    flatten_delay_seconds: float = Field(default=1.5, ge=0)
    #: Cancel follower working orders when the leader cancels.
    cancel_followers_on_leader_cancel: bool = True
    #: Go further and flatten the follower when the leader cancels. Off by
    #: default: a cancel is not an exit, and conflating them closes positions
    #: the operator did not ask to close.
    flatten_followers_on_leader_cancel: bool = False
    #: Close positions on a follower that the leader does not hold. The
    #: "position reconciler" behaviour, which also closes the operator's own
    #: manual trades on a follower — hence off by default.
    close_untracked_follower_positions: bool = False
    #: Stop copying rather than correcting when a follower diverges.
    disable_on_divergence: bool = False
    #: Refuse an order that would put the follower opposite another of the
    #: owner's accounts. On by default: nearly every firm prohibits it.
    prevent_cross_account_hedging: bool = True
    #: Refuse to follow a leader reversal in one step. A reversal leaves the
    #: follower on the old side for as long as the round trip takes, which is
    #: the window in which a hedging rule is violated.
    block_reversals: bool = True


class Leader(FrozenModel):
    """The account whose activity is copied.

    `source` distinguishes a human trading an account from a strategy running on
    one, because the firm-permission question is different for each and the
    compatibility engine asks a different question accordingly.
    """

    account_uid: str = Field(min_length=1, max_length=80)
    source: str = Field(default="manual", pattern=r"^(manual|strategy)$")
    strategy_id: str = ""
    #: Only these products are copied. Empty means all of them.
    products: tuple[str, ...] = ()

    def copies(self, root: str) -> bool:
        return not self.products or root.upper() in {p.upper() for p in self.products}


class Follower(FrozenModel):
    """One account receiving a leader's activity, and how."""

    account_uid: str = Field(min_length=1, max_length=80)
    mode: ExecutionMode = ExecutionMode.MARKET_ON_FILL
    sizing: SizingPolicy = SizingPolicy()
    active: bool = True
    #: Why a follower was disabled, whether by a person or by the engine.
    disabled_reason: str = ""

    @property
    def replicating(self) -> bool:
        return self.active and self.mode is not ExecutionMode.OFF


class CopyGroup(FrozenModel):
    """One leader, many followers, one set of policies."""

    group_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    leader: Leader
    followers: tuple[Follower, ...] = ()
    policy: GroupPolicy = GroupPolicy()
    active: bool = False
    #: Every account in a group must belong to one owner. Firms permit copying
    #: between a trader's own accounts and prohibit copying between people's;
    #: the attestation is recorded rather than assumed.
    owner_attested_by: str = ""
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _leader_is_not_its_own_follower(self) -> CopyGroup:
        uids = [f.account_uid for f in self.followers]
        if self.leader.account_uid in uids:
            raise ValueError(
                "the leader cannot also be a follower of its own group: every fill "
                "would copy to itself"
            )
        duplicates = {uid for uid in uids if uids.count(uid) > 1}
        if duplicates:
            raise ValueError(f"duplicate followers: {', '.join(sorted(duplicates))}")
        return self

    def follower(self, account_uid: str) -> Follower | None:
        return next((f for f in self.followers if f.account_uid == account_uid), None)

    @property
    def replicating_followers(self) -> tuple[Follower, ...]:
        return tuple(f for f in self.followers if f.replicating)

    def with_follower(self, follower: Follower) -> CopyGroup:
        if self.follower(follower.account_uid) is not None:
            raise CopyError(f"'{follower.account_uid}' is already in this group")
        return self.model_copy(
            update={
                "followers": (*self.followers, follower),
                "updated_at": datetime.now(UTC),
            }
        )

    def without_follower(self, account_uid: str) -> CopyGroup:
        return self.model_copy(
            update={
                "followers": tuple(
                    f for f in self.followers if f.account_uid != account_uid
                ),
                "updated_at": datetime.now(UTC),
            }
        )

    def replacing_follower(self, follower: Follower) -> CopyGroup:
        if self.follower(follower.account_uid) is None:
            raise CopyError(f"'{follower.account_uid}' is not in this group")
        return self.model_copy(
            update={
                "followers": tuple(
                    follower if f.account_uid == follower.account_uid else f
                    for f in self.followers
                ),
                "updated_at": datetime.now(UTC),
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CopyOutcome(StrEnum):
    """What the resolver decided for one follower, and why."""

    INTENT = "intent"
    ALREADY_AT_TARGET = "already_at_target"
    SKIPPED_INACTIVE = "skipped_inactive"
    SKIPPED_PRODUCT = "skipped_product"
    SKIPPED_MODE = "skipped_mode"
    BLOCKED_REVERSAL = "blocked_reversal"
    BLOCKED_ROLL = "blocked_roll"
    REFUSED_MAPPING = "refused_mapping"
    ROUNDED_TO_ZERO = "rounded_to_zero"


class CopyDecision(FrozenModel):
    """One follower, one leader event, one outcome — with the arithmetic shown.

    `mapping` is carried so the interface can show *why* a follower is taking
    three contracts rather than four, which is the question an operator asks
    about a copier more than any other.
    """

    group_id: str
    follower_account_uid: str
    outcome: CopyOutcome
    detail: str
    intent: OrderIntent | None = None
    mapping: MappedQuantity | None = None
    leader_position: int = 0
    follower_position: int = 0
    target_position: int = 0

    @property
    def actionable(self) -> bool:
        return self.intent is not None

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CopyHealth(FrozenModel):
    """Whether this group is actually copying, in numbers."""

    group_id: str
    active: bool
    followers: int
    replicating: int
    #: Followers whose account the fabric reports as not tradeable.
    unavailable: int = 0
    #: Followers whose position differs from their target right now.
    diverged: int = 0
    quarantined: int = 0
    intents_last_pass: int = 0
    failures_last_pass: int = 0
    last_leader_event_at: datetime | None = None
    issues: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return (
            self.active
            and self.replicating > 0
            and not self.unavailable
            and not self.diverged
            and not self.quarantined
        )

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["healthy"] = self.healthy
        return payload


class CopyResolver:
    """Turns leader state into per-follower intents.

    Stateless with respect to the group: every call is given the current leader
    and follower positions and derives targets from them. That is what makes the
    engine converge rather than accumulate — there is no running total here to
    drift out of step with a provider.
    """

    def __init__(
        self,
        catalogue: InstrumentCatalogue,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.catalogue = catalogue
        self._now = now

    # ── the target ───────────────────────────────────────────────────────────
    def target_position(
        self,
        *,
        follower: Follower,
        leader_position: int,
        leader_root: str,
        leader_equity: float | None = None,
        follower_equity: float | None = None,
    ) -> tuple[int, MappedQuantity | None, str]:
        """How many contracts this follower should hold, signed.

        Computed from the leader's net position rather than from the event that
        changed it. Two identical fill events produce the same target twice, and
        applying a target twice is a no-op — which is the entire duplicate-event
        defence, expressed as arithmetic rather than as a ledger lookup.
        """
        sizing = follower.sizing
        target_root = sizing.target_root or leader_root
        magnitude = abs(leader_position)
        if magnitude == 0:
            return 0, None, "the leader is flat"

        multiplier = sizing.value
        if sizing.method is SizingMethod.FIXED:
            signed = int(sizing.value) * (1 if leader_position > 0 else -1)
            capped = self._cap(abs(signed), sizing)
            return (
                capped * (1 if leader_position > 0 else -1),
                None,
                f"fixed size of {int(sizing.value)} contract(s)",
            )
        if sizing.method is SizingMethod.EQUITY_SCALED:
            if not leader_equity or not follower_equity or leader_equity <= 0:
                raise CopyError(
                    "equity-scaled sizing needs both accounts' equity, and at least one "
                    "has not been reported by its provider. No size follows from an "
                    "equity nobody stated."
                )
            multiplier = follower_equity / leader_equity

        mapped = self.catalogue.map_quantity(
            source_root=leader_root,
            target_root=target_root,
            source_quantity=magnitude,
            policy=sizing.mapping,
            multiplier=multiplier,
            rounding=sizing.rounding,
            max_contracts=sizing.max_position_contracts,
            risk_budget=sizing.risk_budget,
            stop_ticks=sizing.stop_ticks,
        )
        signed = mapped.quantity * (1 if leader_position > 0 else -1)
        return signed, mapped, mapped.detail

    @staticmethod
    def _cap(quantity: int, sizing: SizingPolicy) -> int:
        if sizing.max_position_contracts is not None:
            return min(quantity, sizing.max_position_contracts)
        return quantity

    # ── the pass ─────────────────────────────────────────────────────────────
    def resolve(
        self,
        *,
        group: CopyGroup,
        leader_root: str,
        leader_position: int,
        follower_positions: dict[str, int],
        reference_price: float | None = None,
        leader_equity: float | None = None,
        follower_equity: dict[str, float] | None = None,
        today: datetime | None = None,
    ) -> tuple[CopyDecision, ...]:
        """One evaluation of the whole group against the leader's net position.

        Called on every leader fill, on every reconciliation pass, and on demand.
        Calling it more often is free in correctness terms: an evaluation that
        finds every follower at target produces no intents at all.
        """
        moment = today or self._now()
        if not group.active:
            return tuple(
                self._skip(group, f, CopyOutcome.SKIPPED_INACTIVE, "the group is not active")
                for f in group.followers
            )
        if not group.leader.copies(leader_root):
            return tuple(
                self._skip(
                    group,
                    f,
                    CopyOutcome.SKIPPED_PRODUCT,
                    f"{leader_root} is not in this leader's copied products",
                )
                for f in group.followers
            )

        roll = self.catalogue.roll_block(leader_root, moment.date())
        decisions: list[CopyDecision] = []
        for follower in group.followers:
            if not follower.replicating:
                decisions.append(
                    self._skip(
                        group,
                        follower,
                        CopyOutcome.SKIPPED_INACTIVE
                        if not follower.active
                        else CopyOutcome.SKIPPED_MODE,
                        follower.disabled_reason or "this follower is not replicating",
                    )
                )
                continue
            if roll is not None:
                decisions.append(
                    self._skip(
                        group,
                        follower,
                        CopyOutcome.BLOCKED_ROLL,
                        f"{roll.reason} ({roll.opens} to {roll.closes})",
                    )
                )
                continue
            decisions.append(
                self._for_follower(
                    group=group,
                    follower=follower,
                    leader_root=leader_root,
                    leader_position=leader_position,
                    current=follower_positions.get(follower.account_uid, 0),
                    reference_price=reference_price,
                    leader_equity=leader_equity,
                    follower_equity=(follower_equity or {}).get(follower.account_uid),
                    at=moment,
                )
            )
        return tuple(decisions)

    def _for_follower(
        self,
        *,
        group: CopyGroup,
        follower: Follower,
        leader_root: str,
        leader_position: int,
        current: int,
        reference_price: float | None,
        leader_equity: float | None,
        follower_equity: float | None,
        at: datetime,
    ) -> CopyDecision:
        target_root = follower.sizing.target_root or leader_root
        try:
            target, mapping, detail = self.target_position(
                follower=follower,
                leader_position=leader_position,
                leader_root=leader_root,
                leader_equity=leader_equity,
                follower_equity=follower_equity,
            )
        except (MappingError, CopyError) as exc:
            return CopyDecision(
                group_id=group.group_id,
                follower_account_uid=follower.account_uid,
                outcome=CopyOutcome.REFUSED_MAPPING,
                detail=str(exc),
                leader_position=leader_position,
                follower_position=current,
            )

        # A follower whose sizing rounds the leader's position away is *not*
        # "already at target": the leader is in a trade and this account is not,
        # and reporting that as agreement is how somebody discovers a follower
        # has never traded from their month-end statement.
        if (
            leader_position != 0
            and current == 0
            and target == 0
            and mapping is not None
            and mapping.exact_quantity > 0
        ):
            return CopyDecision(
                group_id=group.group_id,
                follower_account_uid=follower.account_uid,
                outcome=CopyOutcome.ROUNDED_TO_ZERO,
                detail=(
                    f"the sizing rounds a leader position of {leader_position:+d} "
                    f"{leader_root} to zero contracts of {target_root} "
                    f"({mapping.exact_quantity:g} before rounding)"
                ),
                mapping=mapping,
                leader_position=leader_position,
                follower_position=current,
                target_position=target,
            )

        if target == current:
            return CopyDecision(
                group_id=group.group_id,
                follower_account_uid=follower.account_uid,
                outcome=CopyOutcome.ALREADY_AT_TARGET,
                detail=f"already holding {current:+d} {target_root}",
                mapping=mapping,
                leader_position=leader_position,
                follower_position=current,
                target_position=target,
            )

        if target == 0 and current != 0 and not (
            group.policy.flatten_followers_when_leader_flat
        ):
            return CopyDecision(
                group_id=group.group_id,
                follower_account_uid=follower.account_uid,
                outcome=CopyOutcome.SKIPPED_MODE,
                detail=(
                    "the leader is flat and this group does not flatten followers when "
                    "it is"
                ),
                mapping=mapping,
                leader_position=leader_position,
                follower_position=current,
                target_position=target,
            )

        crosses_flat = current != 0 and target != 0 and (current > 0) != (target > 0)
        if crosses_flat and group.policy.block_reversals:
            # Refusing the one-step reversal is not refusing the trade: the
            # operator closes, the group converges to flat, and the new
            # direction is then a fresh entry. That sequence never leaves this
            # account opposite another of the owner's for the length of a round
            # trip, which is the window a hedging rule catches.
            return CopyDecision(
                group_id=group.group_id,
                follower_account_uid=follower.account_uid,
                outcome=CopyOutcome.BLOCKED_REVERSAL,
                detail=(
                    f"this would reverse {current:+d} to {target:+d} in one order. The "
                    "group blocks reversals: close to flat first, confirm, then enter "
                    "the new direction."
                ),
                mapping=mapping,
                leader_position=leader_position,
                follower_position=current,
                target_position=target,
            )

        delta = target - current
        if delta == 0:
            return CopyDecision(
                group_id=group.group_id,
                follower_account_uid=follower.account_uid,
                outcome=CopyOutcome.ALREADY_AT_TARGET,
                detail="no change required",
                mapping=mapping,
                leader_position=leader_position,
                follower_position=current,
                target_position=target,
            )

        quantity = abs(delta)
        per_order = follower.sizing.max_contracts_per_order
        capped = ""
        if per_order is not None and quantity > per_order:
            quantity = per_order
            capped = f"; capped at {per_order} contract(s) per order"

        if quantity == 0:
            return CopyDecision(
                group_id=group.group_id,
                follower_account_uid=follower.account_uid,
                outcome=CopyOutcome.ROUNDED_TO_ZERO,
                detail=(
                    f"the sizing rounds to zero contracts for a leader position of "
                    f"{leader_position:+d}"
                ),
                mapping=mapping,
                leader_position=leader_position,
                follower_position=current,
                target_position=target,
            )

        reducing = abs(target) < abs(current)
        key = idempotency_key(
            group_id=group.group_id,
            source_order_id=f"{leader_root}:{leader_position}",
            source_marker=f"t{target}",
            account_uid=follower.account_uid,
            action="place",
        )
        intent = OrderIntent(
            intent_id=stable_id("intent", {"key": key, "at": at.isoformat()}),
            kind=CommandKind.PLACE,
            account_uid=follower.account_uid,
            idempotency_key=key,
            symbol=target_root,
            side=Side.BUY if delta > 0 else Side.SELL,
            quantity=quantity,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            strategy_id=group.leader.strategy_id,
            attestation=group.owner_attested_by,
            priority=Priority.FLATTEN if reducing else Priority.ENTRY,
            reference_price=reference_price,
            reason=(
                f"copy {group.name}: leader {leader_position:+d} {leader_root} -> "
                f"follower target {target:+d} {target_root} ({detail}){capped}"
            ),
            created_at=at,
        )
        return CopyDecision(
            group_id=group.group_id,
            follower_account_uid=follower.account_uid,
            outcome=CopyOutcome.INTENT,
            detail=intent.reason,
            intent=intent,
            mapping=mapping,
            leader_position=leader_position,
            follower_position=current,
            target_position=target,
        )

    @staticmethod
    def _skip(
        group: CopyGroup, follower: Follower, outcome: CopyOutcome, detail: str
    ) -> CopyDecision:
        return CopyDecision(
            group_id=group.group_id,
            follower_account_uid=follower.account_uid,
            outcome=outcome,
            detail=detail,
        )

    # ── mirrored working orders ──────────────────────────────────────────────
    def mirror_order(
        self,
        *,
        group: CopyGroup,
        follower: Follower,
        leader_order: OrderRecord,
        reference_price: float | None = None,
        at: datetime | None = None,
    ) -> CopyDecision:
        """One follower's copy of a leader's *working order*.

        Only for `MIRROR_ORDERS`. Prices transfer directly because a micro and
        its standard share a tick grid and trade the same underlying — the price
        level means the same thing on both. What does *not* transfer is a
        bracket expressed in currency: a stop worth five hundred dollars on a
        standard contract is a different distance entirely on its micro, and
        this method refuses to convert one rather than producing a plausible
        number.
        """
        moment = at or self._now()
        if follower.mode is not ExecutionMode.MIRROR_ORDERS:
            return self._skip(
                group,
                follower,
                CopyOutcome.SKIPPED_MODE,
                "this follower mirrors fills, not working orders",
            )
        if leader_order.state not in {OrderState.SUBMITTED, OrderState.WORKING}:
            return self._skip(
                group,
                follower,
                CopyOutcome.SKIPPED_MODE,
                f"the leader's order is {leader_order.state.value}, not working",
            )

        target_root = follower.sizing.target_root or leader_order.symbol
        try:
            mapped = self.catalogue.map_quantity(
                source_root=leader_order.symbol,
                target_root=target_root,
                source_quantity=leader_order.remaining or leader_order.quantity,
                policy=follower.sizing.mapping,
                multiplier=(
                    follower.sizing.value
                    if follower.sizing.method is SizingMethod.RATIO
                    else 1.0
                ),
                rounding=follower.sizing.rounding,
                max_contracts=follower.sizing.max_contracts_per_order,
                risk_budget=follower.sizing.risk_budget,
                stop_ticks=follower.sizing.stop_ticks,
            )
        except MappingError as exc:
            return CopyDecision(
                group_id=group.group_id,
                follower_account_uid=follower.account_uid,
                outcome=CopyOutcome.REFUSED_MAPPING,
                detail=str(exc),
            )

        if mapped.quantity == 0:
            return self._skip(
                group, follower, CopyOutcome.ROUNDED_TO_ZERO,
                "the sizing rounds this order to zero contracts",
            )

        key = idempotency_key(
            group_id=group.group_id,
            source_order_id=leader_order.order_id,
            source_marker=f"v{leader_order.last_sequence or 0}",
            account_uid=follower.account_uid,
            action="mirror",
        )
        intent = OrderIntent(
            intent_id=stable_id("intent", {"key": key, "at": moment.isoformat()}),
            kind=CommandKind.PLACE,
            account_uid=follower.account_uid,
            idempotency_key=key,
            symbol=target_root,
            side=leader_order.side,
            quantity=mapped.quantity,
            order_type=leader_order.order_type,
            time_in_force=leader_order.time_in_force,
            limit_price=leader_order.limit_price,
            stop_price=leader_order.stop_price,
            strategy_id=group.leader.strategy_id,
            attestation=group.owner_attested_by,
            priority=(
                Priority.PROTECTIVE
                if leader_order.bracket_role.value != "entry"
                else Priority.ENTRY
            ),
            reference_price=reference_price,
            reason=(
                f"mirror {leader_order.side.value} {mapped.quantity} {target_root} from "
                f"leader order {leader_order.order_id}"
            ),
            created_at=moment,
        )
        return CopyDecision(
            group_id=group.group_id,
            follower_account_uid=follower.account_uid,
            outcome=CopyOutcome.INTENT,
            detail=intent.reason,
            intent=intent,
            mapping=mapped,
        )

    def cancel_mirror(
        self,
        *,
        group: CopyGroup,
        follower: Follower,
        follower_order_id: str,
        reason: str,
        at: datetime | None = None,
    ) -> OrderIntent:
        """Cancel a follower's mirrored order.

        `Priority.CANCEL` so it goes ahead of any queued entry under a
        rate-limit penalty. A cancel that queues behind four entries is a
        position nobody wanted, held for the length of the queue.
        """
        moment = at or self._now()
        key = idempotency_key(
            group_id=group.group_id,
            source_order_id=follower_order_id,
            source_marker="cancel",
            account_uid=follower.account_uid,
            action="cancel",
        )
        return OrderIntent(
            intent_id=stable_id("intent", {"key": key, "at": moment.isoformat()}),
            kind=CommandKind.CANCEL,
            account_uid=follower.account_uid,
            idempotency_key=key,
            order_id=follower_order_id,
            priority=Priority.CANCEL,
            reason=reason,
            created_at=moment,
        )


def new_group_id(name: str, leader_account_uid: str, created_at: datetime) -> str:
    return stable_id(
        "copygrp",
        {"name": name, "leader": leader_account_uid, "at": created_at.isoformat()},
    )


def assess_health(
    *,
    group: CopyGroup,
    decisions: tuple[CopyDecision, ...],
    unavailable_accounts: frozenset[str] = frozenset(),
    quarantined_accounts: frozenset[str] = frozenset(),
    last_leader_event_at: datetime | None = None,
    failures: int = 0,
) -> CopyHealth:
    """What the operator needs to know about whether this group is working.

    A group with three followers, one of which has been refusing for an hour, is
    not healthy, and reporting it as "active" because the leader is connected is
    how a trader discovers a missing follower from their month-end statement.
    """
    issues: list[str] = []
    diverged = sum(
        1
        for d in decisions
        if d.outcome in {CopyOutcome.INTENT, CopyOutcome.BLOCKED_REVERSAL}
    )
    for decision in decisions:
        if decision.outcome in {
            CopyOutcome.REFUSED_MAPPING,
            CopyOutcome.BLOCKED_REVERSAL,
            CopyOutcome.BLOCKED_ROLL,
            CopyOutcome.ROUNDED_TO_ZERO,
        }:
            issues.append(f"{decision.follower_account_uid}: {decision.detail}")
    for account_uid in sorted(unavailable_accounts):
        if group.follower(account_uid) is not None:
            issues.append(f"{account_uid}: the connection is not live")
    for account_uid in sorted(quarantined_accounts):
        if group.follower(account_uid) is not None:
            issues.append(f"{account_uid}: quarantined by reconciliation")
    if group.active and not group.replicating_followers:
        issues.append("the group is active and no follower is replicating")
    if group.active and not group.owner_attested_by:
        issues.append(
            "nobody has attested that every account in this group has one owner; "
            "copying between different people's accounts is prohibited at every firm "
            "researched that addressed it"
        )

    return CopyHealth(
        group_id=group.group_id,
        active=group.active,
        followers=len(group.followers),
        replicating=len(group.replicating_followers),
        unavailable=len(
            {a for a in unavailable_accounts if group.follower(a) is not None}
        ),
        diverged=diverged,
        quarantined=len(
            {a for a in quarantined_accounts if group.follower(a) is not None}
        ),
        intents_last_pass=sum(1 for d in decisions if d.actionable),
        failures_last_pass=failures,
        last_leader_event_at=last_leader_event_at,
        issues=tuple(issues),
    )

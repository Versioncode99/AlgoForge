"""What an account is *permitted* to do — as distinct from what it can afford.

`forge.prop.account` already answers the numeric question: how close is this
account to its drawdown floor, its daily limit, its contract cap. That engine is
authoritative for every limit expressible as a number and this module does not
duplicate a single one of them.

The question here is different and, for a multi-account desk, more dangerous:
*may* this account be traded automatically, *may* its trades be copied, and
*may* it hold this direction while another account of the same owner holds the
other. Those are contract terms, not arithmetic, and the forensic research found
no blanket answer to any of them. Across seven firms it found copying allowed on
own accounts at some and only with approved tools at others; automation
permitted at one, prohibited outright on a tier of another, conditional at a
third; and one firm requiring that order placement originate from the trader's
personal device. Several of those positions changed during the research window.

Three commitments follow.

**Unknown is never permission.** `Permission` has four values and `UNKNOWN` is
one of them. A firm rule nobody has recorded produces `UNKNOWN`, which blocks
automated action and offers the operator an explicit confirmation — it does not
quietly become yes. This is the same three-valued discipline the judge and the
pre-trade gate already use, extended by a fourth value for the case where a
person can legitimately settle the question.

**AlgoForge asserts nothing about any named firm.** `firm_label` is the
operator's own text and there are no shipped firm rules. What ships is the
*shape* of a policy and a set of questions; the answers come from the operator
reading their own contract. A default that said "Apex allows copying" would be a
legal claim this application is in no position to make, and it would be wrong
within a quarter.

**Policies are effective-dated and cite a source.** A decision records which
version of which policy it relied on, because "the allocator used a rule that
changed last month" is otherwise unanswerable.

The cross-account direction guard is the one rule enforced across *all* of an
owner's accounts rather than per account, because that is how the firms write
it: holding opposite sides of correlated products across two accounts is
prohibited nearly everywhere, and a desk that checks per account cannot see it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel


class Permission(StrEnum):
    """Four values, because three would force a lie.

    `REQUIRES_CONFIRMATION` is not a softer `UNKNOWN`: it is the state where the
    operator has read their contract, believes it is permitted, and the desk
    wants that recorded per action rather than assumed once. `UNKNOWN` is where
    nobody has looked.
    """

    ALLOWED = "allowed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    REQUIRES_CONFIRMATION = "requires_confirmation"

    @property
    def permits_automatic_action(self) -> bool:
        """Only one value does. This property is the whole safety argument."""
        return self is Permission.ALLOWED


#: Severity order, worst last. `UNKNOWN` sorts above `REQUIRES_CONFIRMATION`
#: because a question nobody has asked is a worse position than one somebody has
#: answered and wants re-confirmed.
_RANK: dict[Permission, int] = {
    Permission.ALLOWED: 0,
    Permission.REQUIRES_CONFIRMATION: 1,
    Permission.UNKNOWN: 2,
    Permission.BLOCKED: 3,
}


def worst(permissions: tuple[Permission, ...]) -> Permission:
    """The governing verdict over several. Empty means nothing was checked."""
    if not permissions:
        return Permission.UNKNOWN
    return max(permissions, key=lambda p: _RANK[p])


class OrderOrigin(StrEnum):
    """Where a firm requires order placement to originate.

    Modelled because one researched firm's API policy requires the trader's
    personal device and prohibits remote servers, and a deployment choice
    therefore becomes a compliance question. AlgoForge cannot decide whether a
    given deployment satisfies it — it can refuse to pretend the question does
    not exist.
    """

    UNSPECIFIED = "unspecified"
    PERSONAL_DEVICE_ONLY = "personal_device_only"
    ANY = "any"


class PropProgramPolicy(FrozenModel):
    """One account programme's permissions, as the operator recorded them.

    Nothing here is shipped with values. A policy that has never been edited has
    every permission `UNKNOWN`, which is the honest starting position and which
    blocks every automatic action until somebody reads a contract.
    """

    policy_id: str = ""
    #: The operator's own label. AlgoForge makes no claim about what any named
    #: firm's contract says.
    firm_label: str = Field(default="", max_length=120)
    program_label: str = Field(default="", max_length=120)

    #: May this application place orders on the account from a strategy, as
    #: opposed to copying a human's trades?
    automation: Permission = Permission.UNKNOWN
    #: May trades be copied onto this account from another account?
    copy_in: Permission = Permission.UNKNOWN
    #: May this account's trades be copied to others?
    copy_out: Permission = Permission.UNKNOWN
    #: May an intelligent layer choose which strategy runs here? Separate from
    #: `automation` because at least one firm distinguishes running a bot from
    #: an automated decision system choosing what to run.
    algorithmic_allocation: Permission = Permission.UNKNOWN
    #: May positions on this account offset another account's? Prohibited nearly
    #: everywhere, so a recorded `ALLOWED` is an unusual claim.
    cross_account_hedging: Permission = Permission.BLOCKED
    #: Copying between *different people's* accounts. Prohibited at every firm
    #: researched that addressed it.
    third_party_copy: Permission = Permission.BLOCKED

    order_origin: OrderOrigin = OrderOrigin.UNSPECIFIED
    #: Products the firm permits. `None` means not recorded, which is not "all".
    #: An empty tuple means "recorded, and it is none", which is different.
    permitted_products: tuple[str, ...] | None = None
    prohibited_products: tuple[str, ...] = ()
    #: The maximum accounts this owner may hold under the programme, where the
    #: firm caps it. Firms' caps bind long before a copier's plan limits do.
    max_owner_accounts: int | None = Field(default=None, gt=0)
    #: How the firm counts contracts. "10 micros = 1 mini" at one researched
    #: firm, which changes what a contract cap means.
    micro_contracts_per_standard: int | None = Field(default=None, gt=0)

    effective_from: date | None = None
    review_by: date | None = None
    #: Where the operator read this. Free text, left empty rather than filled
    #: with a plausible URL.
    source_note: str = ""
    recorded_by: str = ""
    recorded_at: datetime | None = None

    @model_validator(mode="after")
    def _identified(self) -> PropProgramPolicy:
        if not self.policy_id:
            object.__setattr__(
                self,
                "policy_id",
                stable_id(
                    "proppolicy",
                    {
                        "firm": self.firm_label,
                        "program": self.program_label,
                        "from": self.effective_from.isoformat()
                        if self.effective_from
                        else "",
                    },
                ),
            )
        return self

    @property
    def version(self) -> str:
        """What a decision cites. Changes whenever any permission changes."""
        return stable_id("policyver", self.model_dump(mode="json"))

    def current(self, today: date) -> bool:
        if self.effective_from is not None and today < self.effective_from:
            return False
        return not (self.review_by is not None and today > self.review_by)

    def product_permission(self, root: str) -> tuple[Permission, str]:
        upper = root.upper()
        if upper in {p.upper() for p in self.prohibited_products}:
            return Permission.BLOCKED, f"{upper} is on the recorded prohibited list"
        if self.permitted_products is None:
            return (
                Permission.UNKNOWN,
                "no permitted-product list has been recorded for this programme",
            )
        if upper in {p.upper() for p in self.permitted_products}:
            return Permission.ALLOWED, f"{upper} is on the recorded permitted list"
        return (
            Permission.BLOCKED,
            f"{upper} is not on the recorded permitted list for this programme",
        )

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["version"] = self.version
        return payload


#: The questions a policy answers, with the text shown to whoever fills it in.
#: Exported so the interface renders the same list the engine evaluates, and so
#: a reader can see that every one defaults to unknown.
POLICY_QUESTIONS: tuple[tuple[str, str], ...] = (
    (
        "automation",
        "Does this account's programme permit a strategy to place orders on it "
        "automatically?",
    ),
    ("copy_in", "May trades be copied onto this account from another of your accounts?"),
    ("copy_out", "May this account's trades be copied to your other accounts?"),
    (
        "algorithmic_allocation",
        "May an automated system choose which strategy runs on this account, and at "
        "what size?",
    ),
    (
        "cross_account_hedging",
        "May this account hold a position opposite to another of your accounts in the "
        "same or a correlated product?",
    ),
    (
        "third_party_copy",
        "May trades be copied between your account and another person's?",
    ),
)


class UseCase(StrEnum):
    """What the desk is asking permission for.

    Distinguished because firms distinguish them: copying a human's manual
    trades is a different question from running a bot, which is a different
    question again from an automated system deciding which bot runs where.
    """

    MANUAL = "manual"
    COPY_FOLLOWER = "copy_follower"
    COPY_LEADER = "copy_leader"
    STRATEGY_AUTOMATION = "strategy_automation"
    ALGORITHMIC_ALLOCATION = "algorithmic_allocation"


class CompatibilityReason(FrozenModel):
    """One check, its verdict, and why."""

    check: str
    verdict: Permission
    detail: str

    @property
    def blocking(self) -> bool:
        return not self.verdict.permits_automatic_action


class CompatibilityReport(FrozenModel):
    """Whether this account may be used this way, and on what basis."""

    account_uid: str
    use_case: UseCase
    verdict: Permission
    reasons: tuple[CompatibilityReason, ...]
    #: What the decision relied on, so it can be re-checked later.
    policy_id: str = ""
    policy_version: str = ""
    evaluated_at: datetime
    #: Set when the operator has confirmed a REQUIRES_CONFIRMATION verdict for
    #: this exact policy version. A confirmation does not survive a policy edit.
    confirmed_by: str = ""
    confirmed_at: datetime | None = None

    @property
    def permits_automatic_action(self) -> bool:
        """The one property the execution path reads.

        A confirmed `REQUIRES_CONFIRMATION` counts, and only for the policy
        version that was confirmed — which the confirmation record carries, so
        editing the policy invalidates it rather than inheriting it.
        """
        if self.verdict.permits_automatic_action:
            return True
        return bool(
            self.verdict is Permission.REQUIRES_CONFIRMATION and self.confirmed_by
        )

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(f"{r.check}: {r.detail}" for r in self.reasons if r.blocking)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["permits_automatic_action"] = self.permits_automatic_action
        return payload


class StrategyRequirements(FrozenModel):
    """What running this strategy on an account would actually need."""

    strategy_id: str = ""
    products: tuple[str, ...] = ()
    #: Order types the strategy emits. Checked against the account's capability.
    order_types: tuple[str, ...] = ()
    requires_brackets: bool = False
    max_contracts: int = Field(default=1, gt=0)
    #: True when the strategy runs unattended. A discretionary leader being
    #: copied does not.
    automated: bool = True


def evaluate_compatibility(
    *,
    account_uid: str,
    use_case: UseCase,
    policy: PropProgramPolicy | None,
    requirements: StrategyRequirements | None = None,
    capability_order_types: tuple[str, ...] = (),
    capability_max_contracts: int | None = None,
    #: The cap from the account's own `forge.prop` rule set, when one is linked.
    #: Passed in rather than read here so there is exactly one rule engine for
    #: numeric limits and this module is not a second one.
    rules_max_contracts: int | None = None,
    account_can_trade: bool | None = None,
    hosted_execution: bool = False,
    today: date | None = None,
    at: datetime | None = None,
) -> CompatibilityReport:
    """May this account be used this way? Accumulates every reason.

    `hosted_execution` is the operator telling the desk that order placement
    would originate somewhere other than their own machine. It is only ever a
    tightening input: with a policy recording `PERSONAL_DEVICE_ONLY`, hosted
    execution blocks; without such a policy the question is `UNKNOWN` rather
    than fine.
    """
    moment = at or datetime.now(UTC)
    day = today or moment.date()
    reasons: list[CompatibilityReason] = []

    if policy is None:
        reasons.append(
            CompatibilityReason(
                check="policy",
                verdict=Permission.UNKNOWN,
                detail=(
                    "no programme policy has been recorded for this account. Nothing is "
                    "known about what its contract permits, and an unrecorded rule is "
                    "not permission."
                ),
            )
        )
        return CompatibilityReport(
            account_uid=account_uid,
            use_case=use_case,
            verdict=Permission.UNKNOWN,
            reasons=tuple(reasons),
            evaluated_at=moment,
        )

    if not policy.current(day):
        reasons.append(
            CompatibilityReason(
                check="policy_currency",
                verdict=Permission.UNKNOWN,
                detail=(
                    "the recorded policy is outside its effective window, so it may no "
                    "longer describe the contract. Re-read and re-record it."
                ),
            )
        )

    reasons.append(_use_case_reason(use_case, policy))

    if account_can_trade is False:
        reasons.append(
            CompatibilityReason(
                check="account_state",
                verdict=Permission.BLOCKED,
                detail="the provider reports this account cannot trade",
            )
        )

    if policy.order_origin is OrderOrigin.PERSONAL_DEVICE_ONLY and hosted_execution:
        reasons.append(
            CompatibilityReason(
                check="order_origin",
                verdict=Permission.BLOCKED,
                detail=(
                    "the recorded policy requires order placement to originate from the "
                    "trader's own device, and this deployment places orders elsewhere"
                ),
            )
        )
    elif hosted_execution and policy.order_origin is OrderOrigin.UNSPECIFIED:
        reasons.append(
            CompatibilityReason(
                check="order_origin",
                verdict=Permission.UNKNOWN,
                detail=(
                    "orders would be placed from somewhere other than your own machine "
                    "and the programme's rule on where trading may originate has not "
                    "been recorded"
                ),
            )
        )

    if requirements is not None:
        reasons.extend(
            _requirement_reasons(
                requirements,
                policy,
                capability_order_types,
                capability_max_contracts,
                rules_max_contracts,
            )
        )

    verdict = worst(tuple(reason.verdict for reason in reasons))
    return CompatibilityReport(
        account_uid=account_uid,
        use_case=use_case,
        verdict=verdict,
        reasons=tuple(reasons),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        evaluated_at=moment,
    )


def _use_case_reason(use_case: UseCase, policy: PropProgramPolicy) -> CompatibilityReason:
    mapping: dict[UseCase, tuple[str, Permission, str]] = {
        UseCase.MANUAL: (
            "manual",
            Permission.ALLOWED,
            "a person trading their own account needs no permission from this desk",
        ),
        UseCase.COPY_FOLLOWER: (
            "copy_in",
            policy.copy_in,
            "whether trades may be copied onto this account",
        ),
        UseCase.COPY_LEADER: (
            "copy_out",
            policy.copy_out,
            "whether this account's trades may be copied to others",
        ),
        UseCase.STRATEGY_AUTOMATION: (
            "automation",
            policy.automation,
            "whether a strategy may place orders on this account automatically",
        ),
        UseCase.ALGORITHMIC_ALLOCATION: (
            "algorithmic_allocation",
            policy.algorithmic_allocation,
            "whether an automated system may choose what runs on this account",
        ),
    }
    check, verdict, description = mapping[use_case]
    detail = {
        Permission.ALLOWED: f"recorded as permitted: {description}",
        Permission.BLOCKED: f"recorded as prohibited: {description}",
        Permission.UNKNOWN: (
            f"not recorded: {description}. An unrecorded rule is not permission."
        ),
        Permission.REQUIRES_CONFIRMATION: (
            f"recorded as needing your confirmation each time: {description}"
        ),
    }[verdict]
    return CompatibilityReason(check=check, verdict=verdict, detail=detail)


def _requirement_reasons(
    requirements: StrategyRequirements,
    policy: PropProgramPolicy,
    capability_order_types: tuple[str, ...],
    capability_max_contracts: int | None,
    rules_max_contracts: int | None,
) -> list[CompatibilityReason]:
    reasons: list[CompatibilityReason] = []

    for root in requirements.products:
        verdict, detail = policy.product_permission(root)
        reasons.append(
            CompatibilityReason(check=f"product:{root.upper()}", verdict=verdict, detail=detail)
        )

    if requirements.order_types:
        if not capability_order_types:
            reasons.append(
                CompatibilityReason(
                    check="order_types",
                    verdict=Permission.UNKNOWN,
                    detail=(
                        "this account's order-type capability has not been discovered "
                        "from its provider, so it is not known whether the strategy's "
                        f"orders ({', '.join(sorted(requirements.order_types))}) can be "
                        "placed"
                    ),
                )
            )
        else:
            supported = {t.lower() for t in capability_order_types}
            missing = sorted(
                t for t in requirements.order_types if t.lower() not in supported
            )
            reasons.append(
                CompatibilityReason(
                    check="order_types",
                    verdict=Permission.BLOCKED if missing else Permission.ALLOWED,
                    detail=(
                        f"the provider does not offer {', '.join(missing)} on this account"
                        if missing
                        else "every order type the strategy needs is supported"
                    ),
                )
            )

    cap = _effective_cap(capability_max_contracts, rules_max_contracts)
    if cap is None:
        reasons.append(
            CompatibilityReason(
                check="contract_cap",
                verdict=Permission.UNKNOWN,
                detail=(
                    "no contract cap is known for this account, from either the "
                    "programme policy or the provider"
                ),
            )
        )
    elif requirements.max_contracts > cap:
        reasons.append(
            CompatibilityReason(
                check="contract_cap",
                verdict=Permission.BLOCKED,
                detail=(
                    f"the strategy would hold up to {requirements.max_contracts} "
                    f"contract(s); this account's cap is {cap}"
                ),
            )
        )
    else:
        reasons.append(
            CompatibilityReason(
                check="contract_cap",
                verdict=Permission.ALLOWED,
                detail=f"{requirements.max_contracts} of {cap} permitted contract(s)",
            )
        )
    return reasons


def _effective_cap(provider_cap: int | None, rules_cap: int | None) -> int | None:
    """The binding contract cap: the smallest of those that are known.

    The provider's own cap and the account's rule set can both name one and they
    can disagree — the firm's contract may be tighter than what the provider
    would accept, or the reverse. The binding number is the smaller, and a
    `None` from either side is not a permissive infinity: it simply does not
    participate, and if both are `None` the check reports UNKNOWN.
    """
    caps = [cap for cap in (provider_cap, rules_cap) if cap is not None]
    return min(caps) if caps else None


class DirectionGuard:
    """The cross-account hedging check, run over *all* of one owner's accounts.

    Every researched firm that addressed it prohibits holding opposite
    directions across accounts in the same or a correlated product — explicitly
    including a mini against its own micro. The check therefore operates on a
    *product group* rather than on a symbol, and it operates on the owner rather
    than on the account, because that is the level at which the rule is written
    and the level at which a desk that only ever looked at one account cannot
    see the violation it is about to create.
    """

    def __init__(self, group_of: dict[str, str] | None = None) -> None:
        #: symbol root -> product group. Supplied by the instrument catalogue.
        self._groups = {k.upper(): v for k, v in (group_of or {}).items()}

    def group(self, root: str) -> str:
        return self._groups.get(root.upper(), root.upper())

    def check(
        self,
        *,
        positions: dict[str, dict[str, int]],
        account_uid: str,
        symbol: str,
        delta: int,
        policy: PropProgramPolicy | None,
    ) -> CompatibilityReason:
        """Would this change put the owner on both sides of one product group?

        `positions` is `{account_uid: {symbol_root: signed_quantity}}` across
        every account the owner holds, including the one being changed.
        """
        if delta == 0:
            return CompatibilityReason(
                check="cross_account_direction",
                verdict=Permission.ALLOWED,
                detail="no change in exposure",
            )
        group = self.group(symbol)
        after = dict(positions.get(account_uid, {}))
        after[symbol.upper()] = after.get(symbol.upper(), 0) + delta

        directions: dict[str, set[str]] = {}
        for holder, book in positions.items():
            source = after if holder == account_uid else book
            for root, quantity in source.items():
                if quantity == 0 or self.group(root) != group:
                    continue
                directions.setdefault("long" if quantity > 0 else "short", set()).add(holder)

        if len(directions) < 2:
            return CompatibilityReason(
                check="cross_account_direction",
                verdict=Permission.ALLOWED,
                detail=f"every account holding {group} would be on the same side",
            )

        longs = ", ".join(sorted(directions.get("long", set())))
        shorts = ", ".join(sorted(directions.get("short", set())))
        detail = (
            f"this would leave the owner long {group} on {longs} and short it on "
            f"{shorts}. Correlated products count as one exposure, so a mini against "
            "its own micro is the same violation."
        )
        if policy is not None and policy.cross_account_hedging is Permission.ALLOWED:
            return CompatibilityReason(
                check="cross_account_direction",
                verdict=Permission.REQUIRES_CONFIRMATION,
                detail=detail + " The recorded policy permits it; confirm deliberately.",
            )
        return CompatibilityReason(
            check="cross_account_direction", verdict=Permission.BLOCKED, detail=detail
        )

"""Which validated strategy should this account be running, and at what size.

This is the layer none of the researched products has. All of them implement
leader → group → followers, which is a degenerate case of strategy → allocation
→ accounts: one strategy (the leader's discretion), a trivial compatibility
filter (group membership) and a proportional allocator (the ratio). The
execution fabric, the reconciliation, the instrument mapping and the risk
primitives are shared; what allocation adds is the decision.

The decision is also where the danger moves. A copier fans out one human's
trade; an allocator decides, across many accounts, what runs where — and the
firm rules on *automated decision-making* are stricter than the rules on
copying, at more than one firm explicitly so. So the architecture here is a
deterministic core with advisory intelligence bolted on the outside, and the
bolt only turns one way.

**The feasible set is computed deterministically, and the advisory layer cannot
add to it.** `feasible` applies, in order: the judge's verdict, the strategy's
own health, firm compatibility, the account's rule engine, and a risk budget
against the account's own buffer. Each produces a reason. `apply_advice` then
takes a ranking and a size from anywhere — a model, a heuristic, an operator's
preference — and *intersects* it with the feasible set, clamping every size down
to the deterministic maximum. A recommendation for an infeasible pairing is
discarded with a reason; a recommendation to size above the deterministic
ceiling is reduced to the ceiling. There is no code path by which advice widens
anything, and `tests/propdesk/test_allocation.py` asserts it on adversarial
input.

**Evidence is out-of-sample or it is not evidence.** `StrategyHealth` is built
from the judge's verdict and from forward or out-of-sample trade statistics.
An in-sample Sharpe is not accepted as a health input, because a strategy
selected on the same data that scores it has been selected on noise and the
allocator would faithfully amplify it across every account.

**Every decision cites what it relied on.** The verdict id, the policy version,
the rules id and the health as of a moment, so "why was this allocated" is
answerable six weeks later when the rule has changed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.propdesk.policy import (
    CompatibilityReport,
    Permission,
    UseCase,
)


class AllocationError(Exception):
    """The allocation could not be computed. Nothing was decided."""


class HealthGrade(StrEnum):
    """How a strategy is behaving against its own evidence.

    `UNPROVEN` is not a bad grade — it is the absence of one, and it is the
    starting state. It blocks allocation for the same reason `NOT_ASSESSED`
    blocks a prop rule: a strategy nobody has measured is not a strategy that
    passed.
    """

    HEALTHY = "healthy"
    #: Live behaviour is drifting from the out-of-sample baseline, but inside
    #: what the evidence would call ordinary variation.
    WATCH = "watch"
    #: Drifting beyond it. Allocatable only with a person's confirmation.
    DEGRADED = "degraded"
    #: The evidence no longer supports running it.
    FAILING = "failing"
    UNPROVEN = "unproven"


_HEALTH_RANK: dict[HealthGrade, int] = {
    HealthGrade.HEALTHY: 0,
    HealthGrade.WATCH: 1,
    HealthGrade.DEGRADED: 2,
    HealthGrade.UNPROVEN: 3,
    HealthGrade.FAILING: 4,
}


class StrategyHealth(FrozenModel):
    """What is known about a strategy, from evidence rather than from belief.

    Every metric is optional and every one absent reads as unknown rather than
    as neutral. The grade is computed by `grade_health` from whichever are
    present, and with none present it is `UNPROVEN`.
    """

    strategy_id: str
    #: The judge's decision, read through the same path the Evidence screen
    #: uses. `None` means never judged, which is not a pass.
    verdict: str | None = None
    verdict_id: str = ""
    #: Out-of-sample only. An in-sample figure must not be supplied here; the
    #: field name says so and `as_of_basis` records which window it came from.
    oos_sharpe: float | None = None
    oos_max_drawdown: float | None = None
    oos_trades: int | None = Field(default=None, ge=0)
    #: Live or forward statistics, when any exist.
    live_trades: int | None = Field(default=None, ge=0)
    live_expectancy: float | None = None
    #: Out-of-sample expectancy, for the drift comparison.
    baseline_expectancy: float | None = None
    #: The 95th-percentile drawdown from bootstrapped out-of-sample paths, in
    #: currency per contract. What the risk budget is actually computed against.
    expected_drawdown_p95: float | None = None
    #: Regimes the strategy's evidence covers, from the regime classifier.
    regimes_covered: tuple[str, ...] = ()
    #: The regime the market is in now, when it has been classified.
    current_regime: str = ""
    as_of: datetime | None = None
    as_of_basis: str = ""
    limitations: tuple[str, ...] = ()

    @property
    def regime_fit(self) -> str:
        """Whether the evidence covers what the market is doing now.

        Three-valued in words rather than in a boolean, because "we have not
        classified the regime" and "the evidence does not cover it" lead to
        different decisions and a `False` would conflate them.
        """
        if not self.current_regime:
            return "unclassified"
        if not self.regimes_covered:
            return "unknown"
        return "covered" if self.current_regime in self.regimes_covered else "uncovered"

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["grade"] = grade_health(self).value
        payload["regime_fit"] = self.regime_fit
        return payload


#: How far live expectancy may fall below the out-of-sample baseline before the
#: grade moves. Not a statistical claim: a threshold the operator can change,
#: and named so that it is visible rather than buried in a comparison.
WATCH_DRIFT = 0.25
DEGRADED_DRIFT = 0.5

#: Below this many out-of-sample trades, a Sharpe is an artefact of a handful of
#: outcomes. Allocating on it would be allocating on noise.
MIN_OOS_TRADES = 30


def grade_health(health: StrategyHealth) -> HealthGrade:
    """One grade from whatever evidence exists, and `UNPROVEN` from none."""
    if health.verdict is not None and health.verdict.upper() == "FAIL":
        return HealthGrade.FAILING
    if health.verdict is None or health.verdict.upper() != "PASS":
        return HealthGrade.UNPROVEN
    if health.oos_trades is not None and health.oos_trades < MIN_OOS_TRADES:
        return HealthGrade.UNPROVEN
    if health.oos_sharpe is not None and health.oos_sharpe <= 0:
        return HealthGrade.FAILING
    if health.baseline_expectancy and health.live_expectancy is not None:
        baseline = health.baseline_expectancy
        if baseline > 0:
            drift = (baseline - health.live_expectancy) / baseline
            if drift >= DEGRADED_DRIFT:
                return HealthGrade.DEGRADED
            if drift >= WATCH_DRIFT:
                return HealthGrade.WATCH
    return HealthGrade.HEALTHY


class FeasibilityCheck(StrEnum):
    VALIDATION = "validation"
    HEALTH = "health"
    COMPATIBILITY = "compatibility"
    ACCOUNT_RULES = "account_rules"
    RISK_BUDGET = "risk_budget"
    CAPACITY = "capacity"


class FeasibilityReason(FrozenModel):
    check: FeasibilityCheck
    passed: bool
    #: True when the check could not be evaluated. Never a pass; the same
    #: discipline as the pre-trade gate's blocking-when-unknown set.
    unknown: bool = False
    detail: str = ""


class AccountSnapshot(FrozenModel):
    """What the allocator needs to know about one account.

    Assembled by the caller from the account's rule assessment and the
    provider's own state, so this module reads one shape and does not know about
    either source.
    """

    account_uid: str
    display_name: str = ""
    #: `forge.prop.account.assess` said this account may trade.
    can_trade: bool | None = None
    #: Equity minus the loss floor. The number a risk budget is a fraction of.
    buffer: float | None = None
    #: How far the account has drawn down from its own peak.
    drawdown: float | None = None
    max_contracts: int | None = Field(default=None, gt=0)
    #: What the account is running now, so a change can be recognised as one.
    current_strategy_id: str = ""
    rules_id: str = ""
    #: The account rule engine's worst level, verbatim.
    rules_level: str = ""
    equity: float | None = None

    @property
    def state_known(self) -> bool:
        return self.buffer is not None and self.can_trade is not None


class AllocationConstraints(FrozenModel):
    """The deterministic ceilings. Nothing advisory may raise one.

    `risk_fraction` is the heart of it: the worst case an allocation is allowed
    to cost, as a fraction of the account's own buffer to its floor. A quarter
    is not a claim about optimal sizing; it is a default that keeps a single
    strategy's bad month from ending an account, and the operator changes it
    knowing what it means.
    """

    risk_fraction: float = Field(default=0.25, gt=0, le=1)
    #: Ceiling on contracts per account, independent of what the risk budget
    #: computes. A second, blunter limit, because a drawdown estimate is an
    #: estimate and a contract cap is not.
    max_contracts: int = Field(default=10, gt=0)
    #: How many accounts one strategy may run on at once. Correlated breach is
    #: the failure mode: twenty accounts running one strategy is one position.
    max_accounts_per_strategy: int | None = Field(default=None, gt=0)
    #: How many strategies one account may run at once.
    max_strategies_per_account: int = Field(default=1, gt=0)
    #: Allocation changes permitted per account per day, to stop churn.
    max_changes_per_day: int = Field(default=2, ge=0)
    #: Grades that may be allocated without a person confirming.
    automatic_grades: tuple[HealthGrade, ...] = (HealthGrade.HEALTHY,)
    #: Grades a person may allocate deliberately.
    confirmable_grades: tuple[HealthGrade, ...] = (
        HealthGrade.WATCH,
        HealthGrade.DEGRADED,
    )
    #: Require the strategy's evidence to cover the current regime.
    require_regime_fit: bool = False


class Candidate(FrozenModel):
    """One strategy on one account, with everything the decision rests on."""

    account_uid: str
    strategy_id: str
    feasible: bool
    #: The largest size the deterministic layer permits. Zero when infeasible.
    max_contracts: int = 0
    reasons: tuple[FeasibilityReason, ...] = ()
    health_grade: HealthGrade = HealthGrade.UNPROVEN
    #: Set when the pairing is permitted only with a person's confirmation.
    requires_confirmation: bool = False
    #: Deterministic score, 0-1. Used to order the feasible set when nothing
    #: advisory is supplied; never used to decide feasibility.
    score: float = 0.0
    detail: str = ""

    @property
    def blocking(self) -> tuple[str, ...]:
        return tuple(
            f"{r.check.value}: {r.detail}" for r in self.reasons if not r.passed
        )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Allocation(FrozenModel):
    """A decision: this account runs this strategy at this size."""

    account_uid: str
    strategy_id: str
    contracts: int = Field(gt=0)
    source: str = Field(default="deterministic", pattern=r"^(deterministic|advisory|manual)$")
    requires_confirmation: bool = False
    confirmed_by: str = ""
    rationale: str = ""
    #: What the decision cited, so it can be re-checked when a rule changes.
    verdict_id: str = ""
    policy_version: str = ""
    rules_id: str = ""
    health_as_of: datetime | None = None
    decided_at: datetime

    @property
    def allocation_id(self) -> str:
        return stable_id(
            "alloc",
            {
                "account": self.account_uid,
                "strategy": self.strategy_id,
                "contracts": self.contracts,
                "at": self.decided_at.isoformat(),
            },
        )

    @property
    def actionable(self) -> bool:
        """Whether this may be acted on without a further human step."""
        return not self.requires_confirmation or bool(self.confirmed_by)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["allocation_id"] = self.allocation_id
        payload["actionable"] = self.actionable
        return payload


class AllocationPlan(FrozenModel):
    """What the allocator proposes, and everything it refused."""

    plan_id: str
    allocations: tuple[Allocation, ...] = ()
    #: Every pairing considered, feasible or not. The refusals are the useful
    #: half: "why is this account not running anything" is the question.
    candidates: tuple[Candidate, ...] = ()
    #: Advisory recommendations that were discarded, and why.
    advice_rejected: tuple[str, ...] = ()
    #: Advisory sizes that were reduced to the deterministic ceiling.
    advice_clamped: tuple[str, ...] = ()
    generated_at: datetime
    limitations: tuple[str, ...] = ()

    def for_account(self, account_uid: str) -> Allocation | None:
        return next((a for a in self.allocations if a.account_uid == account_uid), None)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AllocationChange(FrozenModel):
    """One row of history: what changed on an account, when, and why."""

    account_uid: str
    at: datetime
    previous_strategy_id: str = ""
    strategy_id: str = ""
    previous_contracts: int = 0
    contracts: int = 0
    source: str = "deterministic"
    actor: str = ""
    rationale: str = ""

    @property
    def kind(self) -> str:
        if not self.previous_strategy_id and self.strategy_id:
            return "started"
        if self.previous_strategy_id and not self.strategy_id:
            return "stopped"
        if self.previous_strategy_id != self.strategy_id:
            return "switched"
        return "resized" if self.previous_contracts != self.contracts else "unchanged"

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["kind"] = self.kind
        return payload


class Advice(FrozenModel):
    """A recommendation from outside the deterministic layer.

    It may come from a model, a heuristic, or the operator's own preference.
    Whatever produced it, `apply_advice` treats it identically: as an *ordering
    preference and a size ceiling*, intersected with what the deterministic
    layer already permits. It cannot introduce a pairing and it cannot raise a
    size.
    """

    account_uid: str
    strategy_id: str
    #: Higher runs earlier in the ordering. Ignored for infeasible pairings.
    preference: float = 0.0
    #: The advisor's suggested size. Clamped down to the deterministic maximum;
    #: never used to raise it.
    contracts: int | None = Field(default=None, gt=0)
    rationale: str = ""
    source: str = "advisory"


class Allocator:
    """Feasibility first, ordering second, advice last and strictly narrowing."""

    def __init__(
        self,
        constraints: AllocationConstraints | None = None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.constraints = constraints or AllocationConstraints()
        self._now = now

    # ── feasibility ──────────────────────────────────────────────────────────
    def feasible(
        self,
        *,
        account: AccountSnapshot,
        health: StrategyHealth,
        compatibility: CompatibilityReport | None,
        changes_today: int = 0,
    ) -> Candidate:
        """Whether this account may run this strategy, and how large.

        Every check runs and every reason is kept. A pairing refused for three
        reasons that reports one sends the operator round the loop three times,
        which is the same argument the pre-trade gate makes.
        """
        reasons: list[FeasibilityReason] = []
        grade = grade_health(health)

        # 1. The judge. Unjudged is not a pass.
        judged = health.verdict is not None and health.verdict.upper() == "PASS"
        reasons.append(
            FeasibilityReason(
                check=FeasibilityCheck.VALIDATION,
                passed=judged,
                unknown=health.verdict is None,
                detail=(
                    f"the judge returned {health.verdict}"
                    if health.verdict
                    else "this strategy has never been judged, which is not a pass"
                ),
            )
        )

        # 2. Health.
        automatic = grade in self.constraints.automatic_grades
        confirmable = grade in self.constraints.confirmable_grades
        reasons.append(
            FeasibilityReason(
                check=FeasibilityCheck.HEALTH,
                passed=automatic or confirmable,
                unknown=grade is HealthGrade.UNPROVEN,
                detail=(
                    f"health is {grade.value}"
                    + ("" if automatic else "; a person must confirm this pairing")
                ),
            )
        )
        if self.constraints.require_regime_fit:
            fit = health.regime_fit
            reasons.append(
                FeasibilityReason(
                    check=FeasibilityCheck.HEALTH,
                    passed=fit == "covered",
                    unknown=fit in {"unclassified", "unknown"},
                    detail=(
                        f"the strategy's evidence {fit} the current regime"
                        f" ({health.current_regime or 'unclassified'})"
                    ),
                )
            )

        # 3. Firm permission.
        if compatibility is None:
            reasons.append(
                FeasibilityReason(
                    check=FeasibilityCheck.COMPATIBILITY,
                    passed=False,
                    unknown=True,
                    detail=(
                        "no compatibility has been evaluated for this account, so it is "
                        "not known whether its programme permits an automated system to "
                        "choose what runs on it"
                    ),
                )
            )
        else:
            reasons.append(
                FeasibilityReason(
                    check=FeasibilityCheck.COMPATIBILITY,
                    passed=compatibility.permits_automatic_action,
                    unknown=compatibility.verdict is Permission.UNKNOWN,
                    detail=(
                        "; ".join(compatibility.blocking_reasons)
                        or f"compatibility is {compatibility.verdict.value}"
                    ),
                )
            )

        # 4. The account's own rule engine.
        if account.can_trade is None:
            reasons.append(
                FeasibilityReason(
                    check=FeasibilityCheck.ACCOUNT_RULES,
                    passed=False,
                    unknown=True,
                    detail=(
                        "no state has been recorded for this account, so its rules "
                        "cannot be evaluated and nothing may be allocated to it"
                    ),
                )
            )
        else:
            reasons.append(
                FeasibilityReason(
                    check=FeasibilityCheck.ACCOUNT_RULES,
                    passed=account.can_trade,
                    detail=(
                        f"the rule engine reports {account.rules_level or 'ok'}"
                        if account.can_trade
                        else "the account's rule engine has it in breach; it may not trade"
                    ),
                )
            )

        # 5. The risk budget.
        contracts, risk_reason = self._budget(account, health)
        reasons.append(risk_reason)

        # 6. Churn.
        within = changes_today < self.constraints.max_changes_per_day
        reasons.append(
            FeasibilityReason(
                check=FeasibilityCheck.CAPACITY,
                passed=within or account.current_strategy_id == health.strategy_id,
                detail=(
                    f"{changes_today} allocation change(s) already today; the limit is "
                    f"{self.constraints.max_changes_per_day}"
                    if not within
                    else f"{changes_today} of {self.constraints.max_changes_per_day} "
                    "change(s) used today"
                ),
            )
        )

        blocked = [r for r in reasons if not r.passed]
        is_feasible = not blocked and contracts > 0
        return Candidate(
            account_uid=account.account_uid,
            strategy_id=health.strategy_id,
            feasible=is_feasible,
            max_contracts=contracts if is_feasible else 0,
            reasons=tuple(reasons),
            health_grade=grade,
            requires_confirmation=is_feasible and not automatic,
            score=self._score(health, account) if is_feasible else 0.0,
            detail=(
                # The risk reason names the *binding* constraint — the budget,
                # the account's cap or the allocator's ceiling — which is the
                # question an operator asks about a size. "Up to five" is not an
                # answer to "why five".
                risk_reason.detail
                if is_feasible
                else "; ".join(r.detail for r in blocked)
            ),
        )

    def _budget(
        self, account: AccountSnapshot, health: StrategyHealth
    ) -> tuple[int, FeasibilityReason]:
        """How many contracts the account's own buffer pays for.

        The binding number is the smallest of: what the risk budget buys, the
        account's contract cap, and the allocator's own ceiling. A missing input
        does not relax it — it blocks, because a size derived from an unknown
        drawdown is a number with nothing behind it.
        """
        if account.buffer is None:
            return 0, FeasibilityReason(
                check=FeasibilityCheck.RISK_BUDGET,
                passed=False,
                unknown=True,
                detail=(
                    "this account's buffer to its loss floor is not known, so there is "
                    "no budget to size against"
                ),
            )
        if account.buffer <= 0:
            return 0, FeasibilityReason(
                check=FeasibilityCheck.RISK_BUDGET,
                passed=False,
                detail=f"the account has no buffer left ({account.buffer:,.2f})",
            )
        if health.expected_drawdown_p95 is None or health.expected_drawdown_p95 <= 0:
            return 0, FeasibilityReason(
                check=FeasibilityCheck.RISK_BUDGET,
                passed=False,
                unknown=True,
                detail=(
                    "this strategy has no out-of-sample drawdown estimate, so the worst "
                    "case of an allocation to it cannot be bounded"
                ),
            )
        budget = account.buffer * self.constraints.risk_fraction
        by_risk = int(budget // health.expected_drawdown_p95)
        limits = [by_risk, self.constraints.max_contracts]
        if account.max_contracts is not None:
            limits.append(account.max_contracts)
        contracts = max(0, min(limits))
        if contracts == 0:
            return 0, FeasibilityReason(
                check=FeasibilityCheck.RISK_BUDGET,
                passed=False,
                detail=(
                    f"a {self.constraints.risk_fraction:.0%} budget of "
                    f"{account.buffer:,.2f} is {budget:,.2f}, which does not cover one "
                    f"contract's estimated {health.expected_drawdown_p95:,.2f} drawdown"
                ),
            )
        binding = (
            "the risk budget"
            if contracts == by_risk
            else "the account's contract cap"
            if account.max_contracts is not None and contracts == account.max_contracts
            else "the allocator's ceiling"
        )
        return contracts, FeasibilityReason(
            check=FeasibilityCheck.RISK_BUDGET,
            passed=True,
            detail=(
                f"{contracts} contract(s): {self.constraints.risk_fraction:.0%} of a "
                f"{account.buffer:,.2f} buffer against an estimated "
                f"{health.expected_drawdown_p95:,.2f} drawdown per contract, bound by "
                f"{binding}"
            ),
        )

    @staticmethod
    def _score(health: StrategyHealth, account: AccountSnapshot) -> float:
        """A deterministic ordering, not a probability.

        Deliberately crude and deliberately transparent: a grade term, a Sharpe
        term and a regime term, each bounded. It orders a feasible set that is
        already safe; it never decides whether something is safe. A more
        elaborate score would invite exactly the over-trust the research warns
        about, without making any allocation more permissible than this one.
        """
        grade = 1.0 - (_HEALTH_RANK[grade_health(health)] / 4.0)
        sharpe = 0.0
        if health.oos_sharpe is not None:
            sharpe = max(0.0, min(1.0, health.oos_sharpe / 2.0))
        regime = {"covered": 1.0, "uncovered": 0.0}.get(health.regime_fit, 0.5)
        # A continuing allocation scores slightly higher than an equivalent new
        # one, because churn has a cost the score would otherwise ignore.
        stability = 0.1 if account.current_strategy_id == health.strategy_id else 0.0
        return round(min(1.0, 0.5 * grade + 0.3 * sharpe + 0.2 * regime + stability), 4)

    # ── the plan ─────────────────────────────────────────────────────────────
    def plan(
        self,
        *,
        accounts: Iterable[AccountSnapshot],
        healths: Iterable[StrategyHealth],
        compatibility: dict[tuple[str, str], CompatibilityReport] | None = None,
        changes_today: dict[str, int] | None = None,
        advice: Iterable[Advice] = (),
    ) -> AllocationPlan:
        """Feasible set, deterministic ordering, then advice — narrowing only."""
        moment = self._now()
        compat = compatibility or {}
        changes = changes_today or {}
        accounts = list(accounts)
        healths = list(healths)

        candidates: list[Candidate] = []
        for account in accounts:
            for health in healths:
                candidates.append(
                    self.feasible(
                        account=account,
                        health=health,
                        compatibility=compat.get((account.account_uid, health.strategy_id))
                        or compat.get((account.account_uid, "")),
                        changes_today=changes.get(account.account_uid, 0),
                    )
                )

        feasible = [c for c in candidates if c.feasible]
        advice_list = list(advice)
        rejected, clamped, preference = self._apply_advice(feasible, advice_list)

        # Order: advisory preference where it exists, then the deterministic
        # score, then the strategy id so the result is stable rather than
        # merely deterministic-looking.
        feasible.sort(
            key=lambda c: (
                -preference.get((c.account_uid, c.strategy_id), 0.0),
                -c.score,
                c.strategy_id,
            )
        )

        health_by_id = {h.strategy_id: h for h in healths}
        allocations: list[Allocation] = []
        per_account: dict[str, int] = {}
        per_strategy: dict[str, int] = {}
        sized = {
            (a.account_uid, a.strategy_id): a.contracts
            for a in advice_list
            if a.contracts is not None
        }

        for candidate in feasible:
            account_count = per_account.get(candidate.account_uid, 0)
            if account_count >= self.constraints.max_strategies_per_account:
                continue
            strategy_count = per_strategy.get(candidate.strategy_id, 0)
            if (
                self.constraints.max_accounts_per_strategy is not None
                and strategy_count >= self.constraints.max_accounts_per_strategy
            ):
                continue

            wanted = sized.get((candidate.account_uid, candidate.strategy_id))
            contracts = candidate.max_contracts if wanted is None else min(
                wanted, candidate.max_contracts
            )
            if contracts <= 0:
                continue
            health = health_by_id[candidate.strategy_id]
            source = (
                "advisory"
                if (candidate.account_uid, candidate.strategy_id) in preference
                else "deterministic"
            )
            compat_report = compat.get((candidate.account_uid, candidate.strategy_id))
            account = next(
                a for a in accounts if a.account_uid == candidate.account_uid
            )
            allocations.append(
                Allocation(
                    account_uid=candidate.account_uid,
                    strategy_id=candidate.strategy_id,
                    contracts=contracts,
                    source=source,
                    requires_confirmation=candidate.requires_confirmation,
                    rationale=candidate.detail,
                    verdict_id=health.verdict_id,
                    policy_version=compat_report.policy_version if compat_report else "",
                    rules_id=account.rules_id,
                    health_as_of=health.as_of,
                    decided_at=moment,
                )
            )
            per_account[candidate.account_uid] = account_count + 1
            per_strategy[candidate.strategy_id] = strategy_count + 1

        limitations = [
            "An allocation is a proposal. Every order it produces passes the account "
            "rule engine and the pre-trade gate before it reaches a provider.",
            "Health is computed from out-of-sample and forward evidence only; a "
            "strategy with neither is UNPROVEN and is not allocated.",
        ]
        if any(c.requires_confirmation for c in feasible):
            limitations.append(
                "Some pairings are permitted only with your confirmation and are not "
                "actionable until you give it."
            )

        return AllocationPlan(
            plan_id=stable_id(
                "allocplan",
                {"at": moment.isoformat(), "accounts": [a.account_uid for a in accounts]},
            ),
            allocations=tuple(allocations),
            candidates=tuple(candidates),
            advice_rejected=tuple(rejected),
            advice_clamped=tuple(clamped),
            generated_at=moment,
            limitations=tuple(limitations),
        )

    def _apply_advice(
        self, feasible: list[Candidate], advice: list[Advice]
    ) -> tuple[list[str], list[str], dict[tuple[str, str], float]]:
        """Intersect advice with the feasible set. The only direction is narrower.

        Returns the rejections, the clamps and the surviving preferences. There
        is deliberately no branch here that constructs a `Candidate`: advice
        cannot bring a pairing into existence, only order and shrink the ones
        the deterministic layer already produced.
        """
        allowed = {(c.account_uid, c.strategy_id): c for c in feasible}
        rejected: list[str] = []
        clamped: list[str] = []
        preference: dict[tuple[str, str], float] = {}

        for item in advice:
            key = (item.account_uid, item.strategy_id)
            candidate = allowed.get(key)
            if candidate is None:
                rejected.append(
                    f"{item.strategy_id} on {item.account_uid}: recommended by "
                    f"{item.source}, but the deterministic layer did not find this "
                    "pairing feasible. Advice cannot create a pairing."
                )
                continue
            if item.contracts is not None and item.contracts > candidate.max_contracts:
                clamped.append(
                    f"{item.strategy_id} on {item.account_uid}: {item.source} "
                    f"recommended {item.contracts} contract(s); reduced to "
                    f"{candidate.max_contracts}, which is the deterministic maximum."
                )
            preference[key] = item.preference

        return rejected, clamped, preference


def diff_allocations(
    *,
    previous: Iterable[Allocation],
    proposed: Iterable[Allocation],
    at: datetime,
    actor: str = "",
) -> tuple[AllocationChange, ...]:
    """What would change, per account. The record the history is built from."""
    before = {a.account_uid: a for a in previous}
    after = {a.account_uid: a for a in proposed}
    changes: list[AllocationChange] = []
    for account_uid in sorted(set(before) | set(after)):
        old, new = before.get(account_uid), after.get(account_uid)
        if old is None and new is None:
            continue
        change = AllocationChange(
            account_uid=account_uid,
            at=at,
            previous_strategy_id=old.strategy_id if old else "",
            strategy_id=new.strategy_id if new else "",
            previous_contracts=old.contracts if old else 0,
            contracts=new.contracts if new else 0,
            source=new.source if new else "deterministic",
            actor=actor,
            rationale=new.rationale if new else "no longer allocated",
        )
        if change.kind != "unchanged":
            changes.append(change)
    return tuple(changes)


def use_case_for(source: str) -> UseCase:
    """Which permission question an allocation asks of a firm policy.

    A strategy the operator starts by hand asks whether automation is permitted.
    An allocator choosing it asks the stricter question — whether an automated
    system may make that decision — which at least one researched firm answers
    differently.
    """
    return (
        UseCase.ALGORITHMIC_ALLOCATION
        if source == "advisory"
        else UseCase.STRATEGY_AUTOMATION
    )

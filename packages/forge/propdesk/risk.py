"""Risk modes: who decides how large a position is, and what bounds them.

Three modes, and the difference between them is *who moves the number* — not
which limits apply. Every mode is subject to the same prop rules, the same
eight-rung desk funnel and the same pre-trade gate.

    MANUAL      I decide.
    ADAPTIVE    AlgoForge calculates.
    AI_MANAGED  AlgoForge manages within my boundaries.

**There is exactly one number this module produces.**
`forge.propdesk.allocation.AllocationConstraints.risk_fraction` — the share of
an account's buffer to its loss floor that one allocation may cost. The
allocator already turns that into contracts by dividing by the strategy's
bootstrapped drawdown and taking the smallest of that, the account's cap and its
own ceiling. So nothing here sizes anything. It moves one bounded scalar, and
every conversion from that scalar to an order goes through machinery this module
does not touch.

That is the whole containment argument, and it is worth stating plainly: an
adjuster that can only change one number, whose range the operator fixes, and
whose conversion to an order is deterministic and gated, cannot reach anything
by being wrong about the number.

**Why the boundaries are a separate record from the settings.**
`RiskBoundaries` is what the operator will not go past. `RiskSettings` is how
they would like the space inside it used. They are separate models because they
are written by different actions with different permissions: the action that
writes boundaries is `protected`, which `forge.modes.permissions` rule 2 denies
to an AI actor in every mode and every stance, before mode or stance is
consulted. A single blended record would have made "AI cannot raise its own
ceiling" a matter of careful field-level checking rather than of structure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from forge.contracts.hashing import content_hash
from forge.contracts.models import FrozenModel


class RiskError(Exception):
    """A risk configuration was refused, with a reason to show verbatim."""


class RiskMode(StrEnum):
    MANUAL = "manual"
    ADAPTIVE = "adaptive"
    AI_MANAGED = "ai_managed"


#: The one-line promise each mode makes, in the operator's words rather than the
#: implementation's. Held here rather than in the interface so the API, the
#: action registry and the screen all say the same thing.
MODE_PROMISE: dict[RiskMode, str] = {
    RiskMode.MANUAL: "I decide.",
    RiskMode.ADAPTIVE: "AlgoForge calculates.",
    RiskMode.AI_MANAGED: "AlgoForge manages within my boundaries.",
}

MODE_DETAIL: dict[RiskMode, str] = {
    RiskMode.MANUAL: (
        "You set the risk fraction and the contract ceiling. Nothing proposes a change "
        "to either. Your account's rules, the desk's refusal ladder and the pre-trade "
        "gate still apply: manual means nobody adjusts it for you, not that there are "
        "no limits."
    ),
    RiskMode.ADAPTIVE: (
        "You set an appetite and the boundaries it works inside. A deterministic "
        "function of measured account and strategy state produces the fraction. No "
        "model and no inference is involved at any point, and the same inputs always "
        "give the same number."
    ),
    RiskMode.AI_MANAGED: (
        "The adaptive calculation runs exactly as above, and an advisory layer may "
        "then reduce the result further. It cannot raise one, and it cannot move any "
        "boundary you set."
    ),
}


class AiCapability(StrEnum):
    """What the advisory layer may influence. A closed set, deliberately.

    Each of these narrows something. None of them widens anything, which is why
    the list can be short: there is no capability here that would need a
    countervailing control, because the countervailing control is that the
    output is a multiplier in [0, 1] on an already-bounded number.
    """

    POSITION_SIZING = "position_sizing"
    EXPOSURE_ADJUSTMENT = "exposure_adjustment"
    STRATEGY_ALLOCATION = "strategy_allocation"
    STRATEGY_SWITCHING = "strategy_switching"
    RISK_REDUCTION = "risk_reduction"
    RISK_INCREASE = "risk_increase"


CAPABILITY_LABEL: dict[AiCapability, str] = {
    AiCapability.POSITION_SIZING: "Position sizing",
    AiCapability.EXPOSURE_ADJUSTMENT: "Exposure adjustment",
    AiCapability.STRATEGY_ALLOCATION: "Strategy allocation",
    AiCapability.STRATEGY_SWITCHING: "Strategy switching",
    AiCapability.RISK_REDUCTION: "Risk reduction",
    AiCapability.RISK_INCREASE: "Risk increase",
}

CAPABILITY_DETAIL: dict[AiCapability, str] = {
    AiCapability.POSITION_SIZING: (
        "Propose a smaller risk fraction than the adaptive calculation reached."
    ),
    AiCapability.EXPOSURE_ADJUSTMENT: (
        "Propose reducing how much of the account is committed across strategies."
    ),
    AiCapability.STRATEGY_ALLOCATION: (
        "Reorder the allocator's feasible pairings, and reduce sizes inside them."
    ),
    AiCapability.STRATEGY_SWITCHING: (
        "Propose replacing a running strategy with another that is already validated "
        "and already feasible on this account."
    ),
    AiCapability.RISK_REDUCTION: "Apply a reduction without asking first.",
    AiCapability.RISK_INCREASE: (
        "Apply an increase without asking first, when every driver is measured and "
        "the governor permits the step. Off by default."
    ),
}


class Prohibition(FrozenModel):
    """Something the advisory layer may never do, and what stops it.

    The second field is the point. A list of prohibitions with no mechanism
    beside each one is a statement of intent, and
    `tests/propdesk/test_risk_modes.py` asserts that every `enforced_by` here
    names a module that exists and is imported by the path it claims to guard.
    """

    statement: str
    enforced_by: str
    detail: str


#: What AI may never do, each paired with the deterministic control that refuses
#: it. Rendered verbatim in the AI Management panel, so this is both the
#: enforcement inventory and the product copy — which is deliberate: two copies
#: would drift, and the one on the screen is the one people rely on.
PROHIBITIONS: tuple[Prohibition, ...] = (
    Prohibition(
        statement="Override prop-firm limits",
        enforced_by="forge.prop.account.assess",
        detail=(
            "The account rule engine is a pure function of the rules you recorded and "
            "the account's state. The desk consults it on the `account_rules` rung and "
            "nothing downstream can revisit its answer."
        ),
    ),
    Prohibition(
        statement="Override your maximum risk",
        enforced_by="forge.propdesk.risk.RiskBoundaries",
        detail=(
            "Every proposal is clamped to the boundaries after it is computed, and the "
            "action that writes boundaries is protected, which the permission policy "
            "denies to AI in every mode and stance."
        ),
    ),
    Prohibition(
        statement="Bypass the pre-trade gate",
        enforced_by="forge.execution.gate.screen",
        detail=(
            "The gate is the last rung of the desk funnel. There is no dispatch path "
            "that does not pass through it."
        ),
    ),
    Prohibition(
        statement="Deploy an unvalidated strategy",
        enforced_by="forge.propdesk.autonomy.MANDATORY_GATES",
        detail=(
            "Deployment requires a judge verdict of PASS, an out-of-sample evidence "
            "tier, and measured walk-forward and path robustness. The gate list is the "
            "same at every autonomy level."
        ),
    ),
    Prohibition(
        statement="Trade a blocked account",
        enforced_by="forge.propdesk.desk.PropDesk",
        detail=(
            "The `account_binding` and `compatibility` rungs refuse an account that is "
            "not bound, not connected, or whose programme policy is anything other "
            "than ALLOWED."
        ),
    ),
    Prohibition(
        statement="Bypass G0-G13",
        enforced_by="forge.judge.engine",
        detail=(
            "The judge's decision is read, never recomputed, by anything in this "
            "layer. An INCONCLUSIVE verdict is not a pass and is not treated as one."
        ),
    ),
    Prohibition(
        statement="Change a protected control",
        enforced_by="forge.modes.permissions",
        detail=(
            "Rule 2 of the permission policy denies every protected action to an AI "
            "actor, in every mode and on every stance, before the mode is consulted."
        ),
    ),
    Prohibition(
        statement="Invent a permission a firm has not given",
        enforced_by="forge.propdesk.policy.Permission",
        detail=(
            "A permission nobody recorded is UNKNOWN. UNKNOWN never satisfies a check "
            "that requires ALLOWED, and no code path converts one into the other."
        ),
    ),
)


class RiskBoundaries(FrozenModel):
    """What the operator will not go past, whatever anything else computes.

    Defaults are conservative and every one is finite: there is no way to
    express "no ceiling", for the same reason `forge.risk.limits.RiskProfile`
    refuses one.

    The asymmetry to notice: every field here constrains an *increase* strictly.
    `apply` in `forge.propdesk.scaling` never rate-limits a decrease, because a
    governor that delays de-risking is a governor that causes the loss it was
    installed to prevent.
    """

    #: Share of the account's buffer to its loss floor that one allocation may
    #: cost. The floor is not zero: a mode that can propose "no risk at all"
    #: silently stops trading, which is a decision the operator should make by
    #: disabling the account rather than by the meter drifting down.
    minimum_fraction: float = Field(default=0.05, gt=0, le=1)
    maximum_fraction: float = Field(default=0.25, gt=0, le=1)
    #: The largest single change, in fraction points.
    max_step: float = Field(default=0.02, gt=0, le=1)
    #: Minimum spacing between changes.
    cooldown_minutes: int = Field(default=240, ge=0)
    #: A proposal within this distance of the current value does not fire at
    #: all. Without it, a driver hovering at a threshold produces a change every
    #: evaluation, which is the oscillation §11 forbids.
    hysteresis: float = Field(default=0.005, ge=0, le=1)
    #: Total absolute movement permitted in one day.
    max_daily_change: float = Field(default=0.05, gt=0, le=1)
    #: A blunt ceiling that does not depend on any estimate. A drawdown estimate
    #: is an estimate; a contract cap is not.
    max_contracts: int = Field(default=5, gt=0)
    #: Below this share of the starting buffer, a *decrease* bypasses cooldown
    #: and step entirely.
    emergency_buffer_ratio: float = Field(default=0.35, gt=0, le=1)

    @model_validator(mode="after")
    def _coherent(self) -> RiskBoundaries:
        if self.minimum_fraction > self.maximum_fraction:
            raise ValueError(
                f"minimum risk ({self.minimum_fraction:.1%}) is above maximum "
                f"({self.maximum_fraction:.1%})"
            )
        if self.max_step > self.max_daily_change:
            raise ValueError(
                f"a single step ({self.max_step:.1%}) may not exceed the daily change "
                f"limit ({self.max_daily_change:.1%}); the step would be unreachable "
                "after the first change of the day"
            )
        return self

    @property
    def span(self) -> float:
        return self.maximum_fraction - self.minimum_fraction

    @property
    def boundaries_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))

    def clamp(self, fraction: float) -> float:
        return min(self.maximum_fraction, max(self.minimum_fraction, fraction))

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["boundaries_hash"] = self.boundaries_hash
        return payload


class ManualRisk(FrozenModel):
    """What the operator set by hand. Read only in MANUAL mode."""

    risk_fraction: float = Field(gt=0, le=1)
    max_contracts: int = Field(gt=0)
    note: str = ""


class RiskSettings(FrozenModel):
    """One account's risk configuration.

    `appetite` is 0-100 and is *not* a contract count and not a multiplier on
    one. `forge.propdesk.survival` explains what it actually selects: how far
    into the measured drawdown tail the operator sizes against, and what share
    of the buffer is at stake. Both effects depend on the shape of the
    strategy's own drawdown distribution, which is what makes the meter
    statistical rather than a slider with extra steps.
    """

    account_uid: str
    mode: RiskMode = RiskMode.MANUAL
    #: Conservative 0 <- -> 100 Aggressive.
    appetite: int = Field(default=50, ge=0, le=100)
    boundaries: RiskBoundaries = RiskBoundaries()
    manual: ManualRisk | None = None
    #: What the advisory layer may do, when the mode is AI_MANAGED. Empty in
    #: the other two modes and ignored there.
    ai_capabilities: tuple[AiCapability, ...] = ()
    #: The disclosure the operator acknowledged to reach this configuration.
    #: Enforced by `forge.propdesk.consent`, recorded here so an audit row does
    #: not have to join to find it.
    disclosure_version: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: str = ""

    @model_validator(mode="after")
    def _coherent(self) -> RiskSettings:
        if self.mode is RiskMode.MANUAL:
            if self.manual is None:
                raise ValueError("manual mode requires an explicit risk fraction")
            if not self.boundaries.minimum_fraction <= self.manual.risk_fraction:
                raise ValueError(
                    f"the manual risk fraction ({self.manual.risk_fraction:.1%}) is below "
                    f"your own minimum ({self.boundaries.minimum_fraction:.1%})"
                )
            if self.manual.risk_fraction > self.boundaries.maximum_fraction:
                raise ValueError(
                    f"the manual risk fraction ({self.manual.risk_fraction:.1%}) is above "
                    f"your own maximum ({self.boundaries.maximum_fraction:.1%})"
                )
            if self.manual.max_contracts > self.boundaries.max_contracts:
                raise ValueError(
                    f"{self.manual.max_contracts} contracts is above your own ceiling of "
                    f"{self.boundaries.max_contracts}"
                )
        if self.ai_capabilities and self.mode is not RiskMode.AI_MANAGED:
            raise ValueError(
                f"{self.mode.value} mode has no advisory layer, so it cannot carry AI "
                "capabilities; they would read as permissions that are never consulted"
            )
        if self.mode is RiskMode.AI_MANAGED and not self.disclosure_version:
            raise ValueError(
                "AI risk management requires an acknowledged disclosure; see "
                "forge.propdesk.consent"
            )
        return self

    def may(self, capability: AiCapability) -> bool:
        """Whether the advisory layer holds one capability on this account."""
        return self.mode is RiskMode.AI_MANAGED and capability in self.ai_capabilities

    @property
    def automated(self) -> bool:
        return self.mode is not RiskMode.MANUAL

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["promise"] = MODE_PROMISE[self.mode]
        payload["detail"] = MODE_DETAIL[self.mode]
        payload["automated"] = self.automated
        payload["boundaries"] = self.boundaries.as_dict()
        return payload


def capability_catalogue() -> list[dict[str, str]]:
    """Every capability with its label and what granting it actually permits."""
    return [
        {
            "capability": capability.value,
            "label": CAPABILITY_LABEL[capability],
            "detail": CAPABILITY_DETAIL[capability],
        }
        for capability in AiCapability
    ]


def prohibition_catalogue() -> list[dict[str, str]]:
    """Every prohibition with the control that enforces it."""
    return [item.model_dump(mode="json") for item in PROHIBITIONS]


def describe_modes() -> list[dict[str, str]]:
    """The three modes, as the selector renders them."""
    return [
        {
            "mode": mode.value,
            "promise": MODE_PROMISE[mode],
            "detail": MODE_DETAIL[mode],
        }
        for mode in RiskMode
    ]

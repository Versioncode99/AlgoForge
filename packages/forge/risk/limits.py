"""Risk limits, evaluated before anything runs rather than after.

A risk layer that is consulted by the execution code is only as good as the
execution code's discipline. This one is consulted by the *lifecycle*: a
strategy cannot reach `PAPER` or `DEPLOYED` without naming a profile, and the
profile is checked at that moment. So the limits exist before there is anything
to limit, which is the only ordering that is safe when the execution half does
not exist yet.

The limits are provider-neutral and expressed in the units a research artifact
already carries — contracts, currency, and counts — so nothing here depends on a
broker's schema.

`enabled=False` is a **kill switch**, not a soft preference. A disabled profile
refuses every intent, and the refusal names the profile so that "why is nothing
running" has an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge.contracts.hashing import stable_id


class RiskRefusal(Exception):
    """The intent was refused by the risk layer. Nothing was placed."""


@dataclass(frozen=True)
class RiskProfile:
    """What a strategy is permitted to do, before it is permitted to do anything.

    Every limit has a conservative default, and `unlimited` is not expressible:
    a profile with no ceiling is not a risk profile.
    """

    name: str
    max_position_contracts: int = 1
    max_order_contracts: int = 1
    max_daily_loss: float = 250.0
    max_drawdown: float = 500.0
    max_open_orders: int = 4
    instruments: tuple[str, ...] = ()
    # Paper and live are separate permissions. A profile approved for simulated
    # fills says nothing about capital.
    allow_paper: bool = True
    allow_live: bool = False
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise RiskRefusal("a risk profile must be named")
        for field_name in (
            "max_position_contracts",
            "max_order_contracts",
            "max_daily_loss",
            "max_drawdown",
            "max_open_orders",
        ):
            if float(getattr(self, field_name)) <= 0:
                raise RiskRefusal(f"{field_name} must be positive; a profile with no ceiling "
                                  "is not a risk profile")

    @property
    def profile_id(self) -> str:
        return stable_id("risk", self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "max_position_contracts": self.max_position_contracts,
            "max_order_contracts": self.max_order_contracts,
            "max_daily_loss": self.max_daily_loss,
            "max_drawdown": self.max_drawdown,
            "max_open_orders": self.max_open_orders,
            "instruments": list(self.instruments),
            "allow_paper": self.allow_paper,
            "allow_live": self.allow_live,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class ExecutionIntent:
    """What a strategy would do, described before anything does it.

    Deliberately not an order. An intent is what the risk layer evaluates; there
    is no code in this repository that turns one into a trade.
    """

    strategy_id: str
    instrument: str
    contracts: int
    live: bool
    current_position: int = 0
    open_orders: int = 0
    realised_today: float = 0.0
    drawdown: float = 0.0


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]
    profile: str

    def raise_if_refused(self) -> None:
        if not self.allowed:
            raise RiskRefusal(f"{self.profile}: {'; '.join(self.reasons)}")


def evaluate(profile: RiskProfile, intent: ExecutionIntent) -> RiskDecision:
    """Every limit, checked. Reasons accumulate rather than short-circuiting.

    A caller fixing one breach should not have to discover the next one by
    trying again — an intent that violates three limits says so once.
    """
    reasons: list[str] = []

    if not profile.enabled:
        reasons.append("profile is disabled (kill switch)")
    if intent.live and not profile.allow_live:
        reasons.append("profile does not permit live execution")
    if not intent.live and not profile.allow_paper:
        reasons.append("profile does not permit paper execution")
    if profile.instruments and intent.instrument not in profile.instruments:
        permitted = ", ".join(profile.instruments)
        reasons.append(f"{intent.instrument} is not in the permitted set ({permitted})")
    if intent.contracts <= 0:
        reasons.append("intent size must be positive")
    if intent.contracts > profile.max_order_contracts:
        reasons.append(
            f"order of {intent.contracts} exceeds max_order_contracts "
            f"{profile.max_order_contracts}"
        )
    projected = abs(intent.current_position) + intent.contracts
    if projected > profile.max_position_contracts:
        reasons.append(
            f"projected position {projected} exceeds max_position_contracts "
            f"{profile.max_position_contracts}"
        )
    if intent.open_orders >= profile.max_open_orders:
        reasons.append(
            f"{intent.open_orders} open orders is at or above max_open_orders "
            f"{profile.max_open_orders}"
        )
    # Losses are recorded as negative realised P&L; a limit is a magnitude.
    if -intent.realised_today >= profile.max_daily_loss:
        reasons.append(
            f"daily loss {-intent.realised_today:.2f} has reached max_daily_loss "
            f"{profile.max_daily_loss:.2f}"
        )
    if intent.drawdown >= profile.max_drawdown:
        reasons.append(
            f"drawdown {intent.drawdown:.2f} has reached max_drawdown "
            f"{profile.max_drawdown:.2f}"
        )

    return RiskDecision(allowed=not reasons, reasons=tuple(reasons), profile=profile.name)

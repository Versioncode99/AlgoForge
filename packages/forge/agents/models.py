from __future__ import annotations

from typing import Literal

from pydantic import Field

from forge.contracts.models import FrozenModel


class AgentRole(FrozenModel):
    role_id: Literal["RESEARCHER", "CRITIC", "RISK_AUDITOR", "ARBITER"]
    can_read_holdout: bool
    can_change_numeric_verdict: bool
    can_execute_orders: bool = False


class AgentClaim(FrozenModel):
    role_id: str
    stance: Literal["SUPPORT", "OPPOSE", "CAUTION"]
    statement: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

"""Contextual disclosures, and the record that one was actually read.

A disclaimer in Settings that nobody has opened is not informed consent; it is a
document that exists. §15 asks for the warning to appear *where the consequential
thing is being switched on*, and for the record to say which version of it the
operator saw.

Three properties make this more than a checkbox.

**The version is the text.** `Disclosure.version` is derived from the body by
content hash, so editing a sentence produces a new version and silently
invalidates every acknowledgement of the old one. There is no way to change what
somebody agreed to while keeping their agreement.

**Every factual claim names what makes it true.** A disclosure that says "your
configured maximum risk remains the hard ceiling" is making a claim about the
implementation, and `claims` records which control enforces it.
`tests/propdesk/test_consent.py` asserts each named control exists and appears
in `forge.propdesk.risk.PROHIBITIONS`, so a claim that stops being true fails a
test rather than merely becoming false on a screen.

**No legal claims.** The purpose is informed consent and transparency, not
indemnity. `test_no_disclosure_claims_legal_protection` refuses a body
containing the language of liability transfer. AlgoForge telling somebody what
it does is useful; AlgoForge telling them what they have waived is not something
this module will carry.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from forge.contracts.hashing import content_hash
from forge.contracts.models import FrozenModel


class ConsentError(Exception):
    """A consequential setting was refused for want of an acknowledgement."""


class DisclosureKey(StrEnum):
    AI_RISK_MANAGEMENT = "ai_risk_management"
    AUTONOMOUS_DEPLOYMENT = "autonomous_deployment"
    COPY_TRADING = "copy_trading"
    SIMULATED_EXECUTION = "simulated_execution"


#: Phrases that turn a disclosure into a liability document. Refused at model
#: construction, so the check cannot be forgotten by whoever writes the next one.
_LEGAL_LANGUAGE = re.compile(
    r"\b(indemnif\w*|hold harmless|waive[sd]? (?:any|all)|not liable|no liability|"
    r"disclaims? all|protects? (?:\w+ )?from liability|assume[sd]? all risk)\b",
    re.IGNORECASE,
)


class Claim(FrozenModel):
    """One factual statement a disclosure makes, and what makes it true."""

    statement: str
    enforced_by: str


class Disclosure(FrozenModel):
    """What the operator is shown before a consequential setting takes effect."""

    key: DisclosureKey
    title: str
    #: Paragraphs, in order. Rendered as written; the interface adds no prose.
    body: tuple[str, ...] = Field(min_length=1)
    #: Each checkbox the operator must tick. More than one where the setting has
    #: more than one consequence worth separating.
    acknowledgements: tuple[str, ...] = Field(min_length=1)
    confirm_label: str
    cancel_label: str = "Cancel"
    claims: tuple[Claim, ...] = ()

    @model_validator(mode="after")
    def _no_legal_claims(self) -> Disclosure:
        for paragraph in (*self.body, *self.acknowledgements):
            found = _LEGAL_LANGUAGE.search(paragraph)
            if found:
                raise ValueError(
                    f"a disclosure may not make a legal claim; found '{found.group(0)}'. "
                    "The purpose is informed consent, not indemnity."
                )
        return self

    @property
    def version(self) -> str:
        """Derived from the text, so editing the text invalidates consent."""
        return content_hash(
            {
                "key": self.key.value,
                "title": self.title,
                "body": list(self.body),
                "acknowledgements": list(self.acknowledgements),
            }
        )[:16]

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["version"] = self.version
        return payload


AI_RISK_DISCLOSURE = Disclosure(
    key=DisclosureKey.AI_RISK_MANAGEMENT,
    title="AI Risk Management",
    body=(
        "AI Risk Management can automatically adjust position sizing and exposure "
        "within the limits you configure.",
        "Statistical models and AI recommendations can be wrong. Market conditions can "
        "change and losses can occur.",
        "AlgoForge does not guarantee profits or prevent losses.",
        "AI Risk Management cannot override hard risk, account or prop-firm "
        "restrictions. Your configured maximum risk remains the hard ceiling.",
    ),
    acknowledgements=("I understand how AI Risk Management operates.",),
    confirm_label="Enable AI Risk Management",
    claims=(
        Claim(
            statement="Adjustments stay within the limits you configure.",
            enforced_by="forge.propdesk.risk.RiskBoundaries",
        ),
        Claim(
            statement="Your configured maximum risk remains the hard ceiling.",
            enforced_by="forge.propdesk.risk.RiskBoundaries",
        ),
        Claim(
            statement="It cannot override account or prop-firm restrictions.",
            enforced_by="forge.prop.account.assess",
        ),
    ),
)

AUTONOMOUS_DEPLOYMENT_DISCLOSURE = Disclosure(
    key=DisclosureKey.AUTONOMOUS_DEPLOYMENT,
    title="Autonomous Strategy Deployment",
    body=(
        "AlgoForge may automatically deploy strategies that satisfy your configured "
        "validation, compatibility and risk requirements.",
        "A strategy that passed historical validation may fail in future market "
        "conditions.",
        "Autonomous deployment can result in financial losses.",
        "Deployment remains subject to AlgoForge's validation, prop-firm policy, "
        "pre-trade and risk controls. Strategies that fail mandatory controls will not "
        "be deployed automatically.",
    ),
    acknowledgements=(
        "I understand that autonomous deployment can result in financial losses.",
        "I have reviewed my deployment and risk limits.",
    ),
    confirm_label="Enable Autonomous Deployment",
    claims=(
        Claim(
            statement="Strategies that fail mandatory controls are not deployed.",
            enforced_by="forge.propdesk.autonomy.MANDATORY_GATES",
        ),
        Claim(
            statement="Deployment remains subject to the pre-trade gate.",
            enforced_by="forge.execution.gate.screen",
        ),
    ),
)

COPY_TRADING_DISCLOSURE = Disclosure(
    key=DisclosureKey.COPY_TRADING,
    title="Copy Trading",
    body=(
        "Copying places orders on every follower account in the group, sized by the "
        "policy you configured for each one.",
        "Followers converge on the leader's net position rather than replaying its "
        "order events, so a follower can place an order you did not see the leader "
        "place — for instance to correct a divergence after a rejection or a "
        "disconnect.",
        "Some proprietary trading firms restrict or prohibit copy trading between "
        "accounts. AlgoForge does not know your firm's rules unless you record them, "
        "and a rule you have not recorded is treated as unknown rather than as "
        "permission.",
        "Every copied order passes the same account rules, refusal ladder and "
        "pre-trade gate as an order you place yourself.",
    ),
    acknowledgements=(
        "I understand that copying places orders on every follower account.",
        "I have checked whether my firm permits copy trading between accounts.",
    ),
    confirm_label="Enable Copy Trading",
    claims=(
        Claim(
            statement="An unrecorded firm rule is treated as unknown.",
            enforced_by="forge.propdesk.policy.Permission",
        ),
        Claim(
            statement="Copied orders pass the same pre-trade gate.",
            enforced_by="forge.propdesk.desk.PropDesk",
        ),
    ),
)

SIMULATED_EXECUTION_DISCLOSURE = Disclosure(
    key=DisclosureKey.SIMULATED_EXECUTION,
    title="Simulated execution",
    body=(
        "This build contains no live broker connector. Rithmic, Tradovate and ProjectX "
        "are declared with their real interfaces and refuse every command.",
        "Orders you place reach AlgoForge's own simulator. Fills are modelled from the "
        "order book the simulator maintains; they are not calibrated against any "
        "venue, and no money moves.",
        "Results from simulated execution do not establish that the same strategy "
        "would have filled, or filled at those prices, at a real venue.",
    ),
    acknowledgements=("I understand that execution here is simulated.",),
    confirm_label="Continue",
    claims=(
        Claim(
            statement="No live connector exists in this build.",
            enforced_by="forge.propdesk.adapters.declared.DeclaredAdapter",
        ),
    ),
)

DISCLOSURES: dict[DisclosureKey, Disclosure] = {
    disclosure.key: disclosure
    for disclosure in (
        AI_RISK_DISCLOSURE,
        AUTONOMOUS_DEPLOYMENT_DISCLOSURE,
        COPY_TRADING_DISCLOSURE,
        SIMULATED_EXECUTION_DISCLOSURE,
    )
}


class Acknowledgement(FrozenModel):
    """That a specific person saw a specific version, at a specific moment."""

    key: DisclosureKey
    version: str
    acknowledged_by: str = Field(min_length=1)
    acknowledged_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    #: Which boxes were ticked. Recorded individually because a disclosure with
    #: two acknowledgements is making two separate statements, and "they ticked
    #: one of them" is a different fact from "they ticked both".
    accepted: tuple[str, ...] = ()
    #: The account this was given for, when it is account-specific. Empty means
    #: it was given once for the installation.
    account_uid: str = ""

    @model_validator(mode="after")
    def _complete(self) -> Acknowledgement:
        disclosure = DISCLOSURES.get(self.key)
        if disclosure is None:
            return self
        missing = [item for item in disclosure.acknowledgements if item not in self.accepted]
        if missing:
            raise ValueError(
                f"{len(missing)} of {len(disclosure.acknowledgements)} acknowledgements "
                f"were not accepted: {missing[0]!r}"
            )
        return self

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def current_version(key: DisclosureKey) -> str:
    return DISCLOSURES[key].version


def is_current(acknowledgement: Acknowledgement) -> bool:
    """Whether the text acknowledged is still the text being shown."""
    return acknowledgement.version == current_version(acknowledgement.key)


def require(
    key: DisclosureKey,
    acknowledgements: tuple[Acknowledgement, ...],
    *,
    account_uid: str = "",
) -> Acknowledgement:
    """The current acknowledgement for this key, or a refusal saying what is missing.

    An acknowledgement for an older version is not silently accepted, and the
    refusal says which — "you agreed to an earlier version of this" is
    actionable, "not acknowledged" is not.
    """
    disclosure = DISCLOSURES[key]
    relevant = [
        item
        for item in acknowledgements
        if item.key is key and item.account_uid in {"", account_uid}
    ]
    current = [item for item in relevant if is_current(item)]
    if current:
        return max(current, key=lambda item: item.acknowledged_at)
    if relevant:
        raise ConsentError(
            f"'{disclosure.title}' has changed since it was acknowledged. It needs to be "
            f"read and accepted again before this can be enabled."
        )
    raise ConsentError(
        f"'{disclosure.title}' has not been acknowledged. It has to be read and accepted "
        f"before this can be enabled."
    )


def catalogue() -> list[dict[str, Any]]:
    """Every disclosure as the interface renders it, current version included."""
    return [DISCLOSURES[key].as_dict() for key in DisclosureKey]

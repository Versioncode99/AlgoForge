"""What an assistant may do on your behalf, once the mode that used to decide is gone.

**The problem this exists to solve.** `WorkspaceMode` carried two unrelated
jobs. One was navigation — which rail rows appear — and that job is now
`forge.product.navigation`'s. The other was authority: whether an AI actor may
start unattended work, and whether it may reach the book. Removing the mode
chooser removes the first job's reason to exist and says nothing at all about
the second, and the dangerous way to read that is "no mode, so no restriction".

So authority becomes what it always was underneath — two explicit grants — and
is stated here rather than inferred from which screen is open.

    (off, off)   the assistant researches, backtests and explains.
    (on,  off)   it may also start and stop unattended work.
    (on,  on)    it may also submit orders, inside the same deterministic
                 controls it has never been able to touch.

**The mapping is exact, and that is the point.** These three settings are the
three reachable configurations of the old system — Normal (and Prop Firm, which
enforced identically), AI on the human-in-the-loop stance, and AI on the
autonomous stance. `to_mode` names the pair each one stands for and
`forge.modes.permissions.evaluate` is called unchanged, so the policy that was
tested is the policy that runs. `tests/product/test_authority_migration.py`
asserts the ruling on *every registered action* is identical under each
profile and its legacy pair, which is what makes "removing the chooser did not
widen anything" a check rather than a claim.

**The default is exactly what the product already did, and not a round number.**
It is tempting to default a fresh installation to the narrowest profile, and it
would be wrong here, because that is not what removing the chooser changes.
`Actions.context()` has always answered "no mode entered" with *AI mode on no
stance* — the posture of a headless agent run — so the product's actual default
authority today is `unattended_work` granted and `unattended_execution` not.
`DEFAULT` reproduces that pair, so this migration moves authority neither way;
it only makes it visible and settable. Defaulting lower would have been safe and
would also have stopped the research engine from starting a campaign on its own,
which is a capability regression dressed as caution.

What stays behind a second, explicit opt-in is the only grant that reaches the
book, exactly as before. And nothing a *person* does is affected either way:
`Actor.HUMAN` is not governed by this policy at all, so starting a campaign by
pressing the button still starts it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge.modes.models import Stance, WorkspaceMode


class AuthorityError(ValueError):
    """A combination of grants that does not describe anything."""


@dataclass(frozen=True)
class AuthorityProfile:
    """The two grants, and the legacy pair each combination stands for."""

    #: May the assistant start and stop unattended work — the engine, a
    #: campaign, a specialist agent — without a person applying it?
    unattended_work: bool = False
    #: May the assistant submit and cancel orders without a person applying it?
    #: Requires `unattended_work`: an actor that may not start the loop has no
    #: coherent reason to be allowed to finish it.
    unattended_execution: bool = False

    def __post_init__(self) -> None:
        if self.unattended_execution and not self.unattended_work:
            raise AuthorityError(
                "unattended execution without unattended work is not a configuration this "
                "policy has ever had: an assistant that needs a person to start the loop "
                "cannot be the one that submits its orders"
            )

    def to_mode(self) -> tuple[WorkspaceMode, Stance | None]:
        """The `(mode, stance)` pair this profile stands for.

        The permission evaluator still takes a mode and a stance because it is
        the tested article and rewriting its signature would be rewriting the
        one thing that must not change quietly.
        """
        if not self.unattended_work:
            return WorkspaceMode.NORMAL, None
        if not self.unattended_execution:
            return WorkspaceMode.AI, Stance.HUMAN_IN_THE_LOOP
        return WorkspaceMode.AI, Stance.AUTONOMOUS

    @classmethod
    def from_mode(
        cls, mode: WorkspaceMode | str | None, stance: Stance | str | None = None
    ) -> AuthorityProfile:
        """The profile a stored mode session becomes.

        Every session written before this change reads through here, so an
        operator who left AlgoForge in AI mode on the autonomous stance comes
        back to exactly the authority they left it with rather than to the
        default — which would be a silent narrowing, and narrowing silently is
        its own kind of surprise even when it is the safe direction.
        """
        if mode is None:
            # Not `cls()`. "No mode entered" was never the narrowest posture --
            # `Actions.context()` answered it with AI mode and no stance, which
            # is this.
            return DEFAULT
        parsed = WorkspaceMode(mode)
        if parsed is not WorkspaceMode.AI:
            return cls()
        if stance is None:
            return cls(unattended_work=True)
        return cls(
            unattended_work=True,
            unattended_execution=Stance(stance) is Stance.AUTONOMOUS,
        )

    @property
    def label(self) -> str:
        if not self.unattended_work:
            return "Assisted"
        return "Unattended research" if not self.unattended_execution else "Unattended execution"

    @property
    def summary(self) -> str:
        if not self.unattended_work:
            return (
                "The assistant researches, backtests, validates and explains. Starting "
                "unattended work and anything that reaches the book wait for you."
            )
        if not self.unattended_execution:
            return (
                "The assistant also starts and stops campaigns and the research engine on "
                "its own. Anything that reaches the book still waits for you."
            )
        return (
            "The assistant runs the whole loop unattended, submission included — inside the "
            "risk engine, the pre-trade gate and the kill switch, none of which it can change."
        )

    def as_dict(self) -> dict[str, Any]:
        mode, stance = self.to_mode()
        return {
            "unattended_work": self.unattended_work,
            "unattended_execution": self.unattended_execution,
            "label": self.label,
            "summary": self.summary,
            # Named so an audit entry written today is comparable with one
            # written before the chooser was removed.
            "equivalent_mode": mode.value,
            "equivalent_stance": stance.value if stance else None,
        }


#: What a fresh installation grants.
#:
#: The pair `Actions.context()` already returns for "no mode entered". See the
#: module docstring: this is a preserved default, not a chosen one.
DEFAULT = AuthorityProfile(unattended_work=True)

#: Every configuration, in the order Settings offers them.
PROFILES: tuple[AuthorityProfile, ...] = (
    AuthorityProfile(),
    AuthorityProfile(unattended_work=True),
    AuthorityProfile(unattended_work=True, unattended_execution=True),
)


def catalogue() -> list[dict[str, Any]]:
    return [profile.as_dict() for profile in PROFILES]

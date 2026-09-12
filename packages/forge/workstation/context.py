"""What the operator is currently looking at, and which panels follow it.

Two problems are solved here, and they turned out to be the same problem.

**The first is that `link_group` was a label attached to nothing.** `Panel`
declared it with the docstring "panels sharing a link group follow each other's
symbol and timeframe"; `Workspace.linked()` wrote the tag; nothing read it; and
the workspace view drew the group's name as a badge on the panel header. So the
interface asserted a behaviour to the operator that no code implemented.

**The second is that there was no context at all.** Every screen fetched its
own subject. Selecting NQ anywhere did not make anything else about NQ, and the
context bar could name the workspace and the mode but not the instrument, the
campaign, the strategy or the account.

The resolution is deliberately *not* "changing a panel's symbol writes the new
symbol to its neighbours". That model destroys a pinned symbol the moment a
group member changes and cannot be undone. Instead the context is held once,
per link group, and a panel that belongs to a group **resolves against it at
render time**:

    a panel in a group  ->  shows the group's context
    a panel in no group ->  shows its own settings, untouched, forever

`resolve` is a pure function and writes nothing. Unlinking a panel therefore
reveals the symbol it always had, because that symbol was never overwritten --
which is what §6 of the brief means by "do NOT globally mutate every panel's
state in destructive ways; use explicit context propagation".

**Instrument names are not validated here.** This module knows nothing about
which futures exist; `forge.propdesk.instruments` owns that catalogue and the
action registry checks against it before a context is ever stored. Keeping the
check at the edge is what lets this module stay a dependency-free description of
*what is being looked at* rather than a second opinion about what exists.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from forge.contracts.models import FrozenModel

#: The context a workspace carries when a panel names no group. Panels in no
#: group do not read it; it is the workspace's own subject, and what the context
#: bar shows.
DEFAULT_GROUP = ""

#: A ceiling on named link groups, for the same reason panels have one: a
#: workspace with forty context channels is not a workspace anybody is reading.
MAX_GROUPS = 8


class Facet(StrEnum):
    """The parts of a context, named so a refusal can say which one was wrong.

    They are separate fields and not one "subject" because they are separate
    questions and conflating them is the error the previous phase found in the
    interface, where a workspace was called a mode. An account is not a
    strategy; a campaign is not an instrument; and a screen that shows one in
    the other's place is lying about what is in front of the reader.
    """

    INSTRUMENT = "instrument"
    TIMEFRAME = "timeframe"
    DATASET = "dataset"
    CAMPAIGN = "campaign"
    STRATEGY = "strategy"
    ACCOUNT = "account"


class WorkstationContext(FrozenModel):
    """What is being looked at. Every field is optional and empty means unset.

    Empty is a real value and is never filled in by inference. A context with no
    instrument means no instrument was chosen, and a panel that follows it shows
    its own setting rather than a guess -- the same rule the evidence dossier
    follows when it reports `available: false` instead of a zero.
    """

    instrument: str = ""
    timeframe: str = ""
    dataset: str = ""
    campaign_id: str = ""
    strategy_id: str = ""
    account_id: str = ""

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.instrument,
                self.timeframe,
                self.dataset,
                self.campaign_id,
                self.strategy_id,
                self.account_id,
            )
        )

    def facets(self) -> dict[str, str]:
        """The set facets only, for a surface that renders one chip per fact."""
        return {
            str(Facet.INSTRUMENT): self.instrument,
            str(Facet.TIMEFRAME): self.timeframe,
            str(Facet.DATASET): self.dataset,
            str(Facet.CAMPAIGN): self.campaign_id,
            str(Facet.STRATEGY): self.strategy_id,
            str(Facet.ACCOUNT): self.account_id,
        }

    def set(self, **changes: str) -> WorkstationContext:
        """A new context with some facets replaced.

        Rebuilt rather than `model_copy`d: `model_copy` does not re-run field
        validators, so a length bound written on this model would only ever
        apply at first construction. The previous phase shipped exactly that bug
        in the sidebar and found it with its own bounds test.
        """
        payload = self.model_dump()
        payload.update({key: value for key, value in changes.items() if value is not None})
        return WorkstationContext(**payload)

    def cleared(self, facet: Facet | str) -> WorkstationContext:
        """A new context with one facet unset. Clearing is not setting to a default."""
        field = {
            Facet.INSTRUMENT: "instrument",
            Facet.TIMEFRAME: "timeframe",
            Facet.DATASET: "dataset",
            Facet.CAMPAIGN: "campaign_id",
            Facet.STRATEGY: "strategy_id",
            Facet.ACCOUNT: "account_id",
        }[Facet(str(facet))]
        return self.set(**{field: ""})


class Resolved(FrozenModel):
    """What a panel should actually display, and where each value came from.

    `source` is carried because an operator looking at a chart showing MNQ needs
    to be able to tell whether that is the panel's own pinned symbol or the
    workspace's current context -- and because a surface that cannot tell will
    eventually render one as the other.
    """

    panel_id: str
    symbol: str = ""
    timeframe: str = ""
    #: `""` when the panel is independent, otherwise the link group it follows.
    group: str | None = None
    #: "panel" or "context". Never a guess.
    source: str = "panel"

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def resolve(panel: Any, contexts: dict[str, WorkstationContext]) -> Resolved:
    """What this panel shows, given the workspace's contexts. Writes nothing.

    A panel in no link group is independent and always shows its own settings.
    A panel in a group shows that group's context where the context sets a
    value, and falls back to its own setting where it does not -- so joining a
    group whose instrument is unset does not blank the panel.
    """
    own_symbol = str(panel.settings.get("symbol", "") or "")
    own_timeframe = str(panel.settings.get("timeframe", "") or "")
    group = getattr(panel, "link_group", None)
    if not group:
        return Resolved(
            panel_id=panel.panel_id,
            symbol=own_symbol,
            timeframe=own_timeframe,
            group=None,
            source="panel",
        )
    context = contexts.get(group) or WorkstationContext()
    symbol = context.instrument or own_symbol
    timeframe = context.timeframe or own_timeframe
    followed = bool(context.instrument or context.timeframe)
    return Resolved(
        panel_id=panel.panel_id,
        symbol=symbol,
        timeframe=timeframe,
        group=group,
        source="context" if followed else "panel",
    )


def resolve_all(
    panels: Any, contexts: dict[str, WorkstationContext]
) -> tuple[Resolved, ...]:
    return tuple(resolve(panel, contexts) for panel in panels)


def groups_in(panels: Any) -> tuple[str, ...]:
    """Every link group named by at least one panel, in a stable order."""
    return tuple(sorted({p.link_group for p in panels if getattr(p, "link_group", None)}))

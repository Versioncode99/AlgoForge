"""Turning a stated purpose into a layout.

Two halves, deliberately separated:

* **Understanding** what someone meant by "I trade NQ through a prop firm and
  scalp London and New York" is a language problem, and a model does it.
* **Building** the workspace from that understanding is not. It is a function
  from a profile to a set of panels, and it belongs here where it is
  deterministic, testable, and works with no model configured at all.

The split matters for a reason beyond tidiness. If the whole thing lived behind
a model, the product would stop working when the model was unavailable, and the
operator would be unable to reproduce what it built. Here, the same profile
always produces the same workspace, and a person can fill the profile in by hand
and get exactly what the agent would have got.

Nothing this produces is a restriction. A built workspace is an ordinary
workspace: every panel can be removed, every panel that was not chosen can be
added, and the profile is recorded as *what was asked for*, never consulted
again to decide what is allowed.
"""

from __future__ import annotations

from forge.workstation.models import GRID_COLUMNS, Panel, PanelKind, WorkspaceProfile

#: Roots this build has archives for. A profile naming something else still
#: produces a workspace -- it just does not get a chart for an instrument there
#: are no bars for, because that chart would have nothing to draw.
KNOWN_ROOTS = ("NQ", "ES", "MNQ", "MES", "GC", "MGC", "BTCUSDT", "ETHUSDT")

#: Intraday work wants a fast chart; swing work does not.
STYLE_TIMEFRAMES: dict[str, str] = {
    "scalp": "1m",
    "intraday": "5m",
    "swing": "1h",
    "position": "1d",
}

_RESEARCH_PANELS = (
    PanelKind.EXPERIMENTS,
    PanelKind.RESEARCH_MEMORY,
    PanelKind.EVIDENCE,
    PanelKind.VALIDATION,
    PanelKind.LINEAGE,
)


def timeframe_for(profile: WorkspaceProfile) -> str:
    style = (profile.style or "").lower()
    for key, timeframe in STYLE_TIMEFRAMES.items():
        if key in style:
            return timeframe
    # Neither a guess nor a failure: five minutes is the middle of the range
    # this product's datasets support, and the operator can change it in a click.
    return "5m"


def markets_for(profile: WorkspaceProfile) -> tuple[str, ...]:
    """The instruments to chart, filtered to ones there is data for."""
    wanted = tuple(dict.fromkeys(m.strip().upper() for m in profile.markets if m.strip()))
    known = tuple(m for m in wanted if m in KNOWN_ROOTS)
    # Two charts is a pair; more than four is a wall. A profile listing eight
    # markets gets the first four and a watchlist holding all of them.
    return known[:4]


class _Packer:
    """Places panels left to right, wrapping when a row runs out of columns.

    Written because the first version placed panels at computed offsets and two
    of them landed on top of each other — 3 columns wide at x=8 and x=9 in a
    12-column grid. Overlapping panels are not a rendering curiosity: on a CSS
    grid the later one simply covers the earlier, so a panel the operator asked
    for is silently invisible. Packing makes that unrepresentable.
    """

    def __init__(self, top: int = 0) -> None:
        self.x = 0
        self.y = top
        self.row_height = 0
        self.panels: list[Panel] = []

    def place(self, kind: PanelKind, width: int, height: int, **settings: object) -> None:
        width = max(1, min(width, GRID_COLUMNS))
        if self.x + width > GRID_COLUMNS:
            self.newline()
        index = 1 + sum(1 for p in self.panels if p.kind is kind)
        self.panels.append(
            Panel(
                panel_id=f"{kind.value}-{index}",
                kind=kind,
                x=self.x,
                y=self.y,
                width=width,
                height=height,
                settings=dict(settings),
            )
        )
        self.x += width
        self.row_height = max(self.row_height, height)

    def newline(self) -> None:
        if self.row_height:
            self.y += self.row_height
        self.x = 0
        self.row_height = 0

    def link(self, group: str, panel_ids: tuple[str, ...]) -> None:
        self.panels = [
            p.model_copy(update={"link_group": group}) if p.panel_id in panel_ids else p
            for p in self.panels
        ]

    def result(self) -> tuple[Panel, ...]:
        return tuple(self.panels)


def layout_for(profile: WorkspaceProfile) -> tuple[Panel, ...]:
    """The panels a profile asks for.

    Deterministic: the same profile always produces the same layout, which is
    what makes "build me another one of these for ES" a sentence with a
    checkable answer.
    """
    markets = markets_for(profile)
    timeframe = timeframe_for(profile)
    purpose = (profile.purpose or "").lower()
    wants_research = bool(profile.research) or "research" in purpose or "quant" in purpose
    wants_prop = "prop" in (profile.risk or "").lower() or "prop" in purpose

    packer = _Packer()

    # Charts first: they are why most people open a workstation, and they take
    # the top of the grid.
    charted = markets[:2]
    for symbol in charted:
        packer.place(
            PanelKind.CHART,
            GRID_COLUMNS // max(1, len(charted)),
            9,
            symbol=symbol,
            timeframe=timeframe,
        )
    if len(charted) > 1:
        # Linked only when there is more than one: a link group of one is a
        # label with nothing to synchronise.
        packer.link("linked", tuple(p.panel_id for p in packer.panels))
    packer.newline()

    if len(markets) > 1:
        packer.place(PanelKind.WATCHLIST, 3, 5, symbols=", ".join(profile.markets))

    if wants_prop:
        packer.place(PanelKind.PROP, 5, 5)

    if wants_research:
        # The evidence trail, in the order it is produced.
        for kind in _RESEARCH_PANELS[:2]:
            packer.place(kind, 4, 5)
        packer.newline()
        packer.place(PanelKind.STRATEGIES, 7, 6)
        packer.place(PanelKind.RUNS, 5, 6)
    else:
        packer.place(PanelKind.ACTIVITY, 4, 5)

    # Data health earns a place when the profile names datasets: someone who
    # said which archives they care about should be able to see their state.
    if profile.datasets:
        packer.newline()
        packer.place(PanelKind.DATA_HEALTH, GRID_COLUMNS, 5)

    panels = packer.result()
    if not panels:
        # A profile that asked for nothing recognisable still gets somewhere to
        # start, rather than an empty screen and no explanation.
        return (
            Panel(
                panel_id="strategies-1",
                kind=PanelKind.STRATEGIES,
                x=0,
                y=0,
                width=GRID_COLUMNS,
                height=8,
            ),
        )
    return panels


def describe(profile: WorkspaceProfile, panels: tuple[Panel, ...]) -> str:
    """What was built and why, in a sentence the operator can check."""
    markets = ", ".join(markets_for(profile)) or "no charted market"
    kinds = ", ".join(sorted({p.kind.value.replace("_", " ") for p in panels}))
    return (
        f"{len(panels)} panels for {markets} at {timeframe_for(profile)}: {kinds}. "
        "Everything here can be changed; nothing is locked to the profile."
    )

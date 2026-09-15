"""The sidebar as data the operator owns, rather than a property of the mode.

The problem this replaces, stated plainly: navigation was a constant of the
**mode**. `forge.modes.models` declares four `ModeDescriptor`s, each with a fixed
tuple of `Section`s, and the shell renders whichever tuple belongs to the mode
the session is in. Wanting Prop Accounts beside Research Agents therefore meant
switching modes — which swapped the entire rail, the layout and the context,
because the mode was the unit of everything.

That is the wrong unit. A person does not want "Prop Firm mode"; they want their
accounts, their NQ chart and their research campaign on one screen. So:

* :data:`CATALOGUE` is the **union** of every destination any mode offers,
  deduplicated by route. It is derived from the mode manifests rather than
  written again, so a section added to a mode appears here without an edit and
  a section removed cannot linger pointing at nothing.
* A :class:`Sidebar` is an ordered list of named groups of those destinations,
  owned by a workspace and persisted with it.
* The built-in modes keep their familiar rails, because
  :func:`default_sidebar_for` builds one from the mode's own sections. Nothing
  a current user recognises moves.

What this module deliberately does **not** do is grant access. A destination in
a sidebar is a link; whether the operator may do what is behind it remains
`forge.modes.permissions`' decision, and putting an item in a rail cannot change
that. A navigation list that could widen a capability would be a permission
system with an "add to sidebar" button.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from forge.contracts.models import FrozenModel
from forge.modes.models import MODE_ORDER, MODES, WorkspaceMode
from forge.product.navigation import DESTINATIONS, LEGACY_ROUTES, resolve

MAX_GROUPS = 20
MAX_ITEMS_PER_GROUP = 40
MAX_ITEMS = 120


@dataclass(frozen=True)
class Destination:
    """One thing a sidebar can point at.

    ``modes`` records which built-in modes offer it, which is provenance rather
    than a restriction: the whole point of the catalogue is that a destination
    from one mode can sit in a workspace built around another.
    """

    route: str
    label: str
    detail: str
    group: str
    panel_kinds: tuple[str, ...]
    modes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "label": self.label,
            "detail": self.detail,
            "group": self.group,
            "panel_kinds": list(self.panel_kinds),
            "modes": list(self.modes),
        }


def _build_catalogue() -> dict[str, Destination]:
    """Every screen a sidebar can point at, from the product navigation itself.

    It used to be the **union of the mode manifests**, deduplicated by route,
    because navigation was a property of the mode and a workspace wanting Prop
    Accounts beside Research Agents had to reach across two of them. There is one
    navigation now, so the union is the navigation — and the interesting change
    is that *tabs are destinations here too*. A rail that could only name the
    nine top-level rows could not put "Drawdown" or "Pre-trade gate" on a
    screen, which is most of what somebody builds a custom rail to do.

    Order follows the manifest, and a destination with tabs contributes itself
    first so "Trading" and "Trading · Risk" both exist and read in that order.
    """
    found: dict[str, Destination] = {}
    for destination in DESTINATIONS:
        found[destination.route] = Destination(
            route=destination.route,
            label=destination.label,
            detail=destination.detail,
            group=destination.group,
            panel_kinds=destination.panels(),
            modes=tuple(mode.value for mode in MODE_ORDER),
        )
        for tab in destination.tabs:
            # `route:tab`, not `route?tab=`. A sidebar item's route travels in a
            # URL *path* — `/workspaces/{id}/sidebar/items/{route}/pin` — and a
            # `?` in a path segment ends the path. A colon is legal there
            # (RFC 3986 §3.3) and reads as what it is.
            link = f"{destination.route}:{tab.tab}"
            found[link] = Destination(
                route=link,
                label=f"{destination.label} · {tab.label}",
                detail=tab.detail,
                group=destination.label,
                panel_kinds=tab.panel_kinds or destination.panel_kinds,
                modes=tuple(mode.value for mode in MODE_ORDER),
            )
    return found


def _entry(route: str) -> Destination:
    """The catalogue row for a route, translating an old one on the way.

    Falls back to a row describing the route itself rather than raising. A rail
    holding one unrecognised item should draw that item plainly, not fail to
    draw the workspace.
    """
    found = CATALOGUE.get(canonical(route) or route)
    if found is not None:
        return found
    return Destination(
        route=route, label=route, detail="", group="Other", panel_kinds=(), modes=()
    )


def canonical(route: str) -> str:
    """The current link for a route, including every one the modes used to offer.

    A rail saved before the mode navigation was replaced names `desk`, `charts`
    or `portfolio`. Those are still meaningful — they still name a screen — so
    they are *translated* rather than refused, and normalised on the way in so a
    rail stops carrying the old spelling the first time it is edited. Refusing
    them would delete somebody's arrangement to make a rename tidy.
    """
    if route in CATALOGUE:
        return route
    # Only a route the product *used* to offer is translated. `resolve` falls
    # back to Home for anything it does not recognise, which is right for a link
    # somebody clicked — they end up somewhere real — and wrong here: a sidebar
    # that silently accepted `nonsens` and drew a row labelled Home is a rail
    # that lies about what is on it.
    # `route?tab=x` is the link form the shell navigates with; accept it here so
    # a caller can pass either spelling and get the stored one back.
    landed = resolve(route)
    if not landed.redirected or route.partition("?")[0] in LEGACY_ROUTES:
        link = f"{landed.route}:{landed.tab}" if landed.tab else landed.route
        if link in CATALOGUE:
            return link
    return ""


CATALOGUE: dict[str, Destination] = _build_catalogue()


def known(route: str) -> bool:
    return bool(canonical(route))


def destinations() -> list[dict[str, Any]]:
    """The catalogue, for an interface offering "add an item"."""
    return [d.as_dict() for d in CATALOGUE.values()]


class SidebarError(ValueError):
    """A refused sidebar edit."""


class SidebarItem(FrozenModel):
    """One entry in a rail."""

    route: str
    #: An operator's own wording. Empty means "use the catalogue's label", which
    #: is different from having renamed it to the same thing — a renamed item
    #: keeps its wording when the catalogue's changes, and an un-renamed one
    #: follows.
    label: str = ""
    #: Pinned items are drawn above their group and survive a collapse.
    pinned: bool = False
    #: Hidden items stay in the workspace so showing one again restores its
    #: position. Removing is the destructive operation; hiding is not.
    hidden: bool = False

    @field_validator("route")
    @classmethod
    def _is_a_real_destination(cls, route: str) -> str:
        normalised = canonical(route)
        if normalised:
            return normalised
        if not known(route):
            raise ValueError(
                f"'{route}' is not a destination. A sidebar item pointing at nothing is "
                "exactly the dead control the product rules refuse."
            )
        return route

    def display_label(self) -> str:
        # `canonical` rather than a bare lookup: a rail stored before the mode
        # navigation was replaced can still be in memory under the old spelling
        # — `sidebar_from` builds one from a mapping without going through the
        # validator — and a label lookup that raised `KeyError` there took the
        # whole workspace render with it.
        entry = CATALOGUE.get(canonical(self.route) or self.route)
        return self.label or (entry.label if entry else self.route)


class SidebarGroup(FrozenModel):
    """A named, ordered set of items — "MY TRADING", "RESEARCH"."""

    group_id: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=60)
    items: tuple[SidebarItem, ...] = ()
    collapsed: bool = False

    @field_validator("items")
    @classmethod
    def _no_duplicates(cls, items: tuple[SidebarItem, ...]) -> tuple[SidebarItem, ...]:
        if len(items) > MAX_ITEMS_PER_GROUP:
            raise ValueError(f"a sidebar group holds at most {MAX_ITEMS_PER_GROUP} items")
        routes = [item.route for item in items]
        repeated = {route for route in routes if routes.count(route) > 1}
        if repeated:
            raise ValueError(f"duplicate sidebar items: {', '.join(sorted(repeated))}")
        return items

    def visible(self) -> tuple[SidebarItem, ...]:
        return tuple(item for item in self.items if not item.hidden)


class Sidebar(FrozenModel):
    """A workspace's navigation, owned by the operator.

    Immutable, like `Workspace`: every operation returns a new sidebar, so a
    refused edit leaves the caller holding exactly what it had.
    """

    groups: tuple[SidebarGroup, ...] = ()

    @field_validator("groups")
    @classmethod
    def _sane(cls, groups: tuple[SidebarGroup, ...]) -> tuple[SidebarGroup, ...]:
        if len(groups) > MAX_GROUPS:
            raise ValueError(f"a sidebar holds at most {MAX_GROUPS} groups")
        ids = [group.group_id for group in groups]
        repeated = {i for i in ids if ids.count(i) > 1}
        if repeated:
            raise ValueError(f"duplicate sidebar groups: {', '.join(sorted(repeated))}")
        total = sum(len(group.items) for group in groups)
        if total > MAX_ITEMS:
            raise ValueError(f"a sidebar holds at most {MAX_ITEMS} items")
        return groups

    # ── reads ────────────────────────────────────────────────────────────────
    def group(self, group_id: str) -> SidebarGroup | None:
        return next((g for g in self.groups if g.group_id == group_id), None)

    def require_group(self, group_id: str) -> SidebarGroup:
        found = self.group(group_id)
        if found is None:
            known_ids = ", ".join(g.group_id for g in self.groups) or "none"
            raise SidebarError(f"no sidebar group '{group_id}'. Groups: {known_ids}")
        return found

    def routes(self) -> tuple[str, ...]:
        return tuple(item.route for group in self.groups for item in group.items)

    def locate(self, route: str) -> tuple[str, int] | None:
        """Which group holds ``route``, and at what index.

        The route is canonicalised first, so a caller that still says `charts`
        finds the item stored as `markets?tab=charts`. Without this, translating
        on the way in would make every subsequent edit — pin, rename, hide,
        move, remove — fail for a rail the operator had saved under the old
        spelling, which is a worse outcome than never translating at all.
        """
        wanted = canonical(route) or route
        for group in self.groups:
            for index, item in enumerate(group.items):
                if item.route == wanted:
                    return group.group_id, index
        return None

    def has(self, route: str) -> bool:
        return self.locate(route) is not None

    # ── writes, all returning a new sidebar ──────────────────────────────────
    #
    # Every one of these goes through `_rebuilt`, and that is not incidental.
    # Pydantic's `model_copy` does **not** re-run field validators, so building
    # the new state with it would mean the duplicate-route check, the group
    # ceiling and the item ceiling only ever ran at construction — a sidebar
    # could be edited into a shape it could not have been created in, and the
    # first sign would be a rail with the same destination twice.

    def _rebuilt(self, groups: Sequence[SidebarGroup]) -> Sidebar:
        return Sidebar(groups=tuple(groups))

    @staticmethod
    def _group_with(group: SidebarGroup, **changes: Any) -> SidebarGroup:
        return SidebarGroup(
            **{
                "group_id": group.group_id,
                "label": group.label,
                "items": group.items,
                "collapsed": group.collapsed,
                **changes,
            }
        )

    def _replace_group(self, group: SidebarGroup) -> Sidebar:
        return self._rebuilt(
            [group if g.group_id == group.group_id else g for g in self.groups]
        )

    def with_group(self, group_id: str, label: str, *, position: int | None = None) -> Sidebar:
        if self.group(group_id) is not None:
            raise SidebarError(f"sidebar group '{group_id}' already exists")
        groups = list(self.groups)
        group = SidebarGroup(group_id=group_id, label=label)
        groups.insert(len(groups) if position is None else max(0, position), group)
        return self._rebuilt(groups)

    def without_group(self, group_id: str) -> Sidebar:
        self.require_group(group_id)
        return self._rebuilt([g for g in self.groups if g.group_id != group_id])

    def renamed_group(self, group_id: str, label: str) -> Sidebar:
        group = self.require_group(group_id)
        return self._replace_group(self._group_with(group, label=label))

    def collapsed_group(self, group_id: str, collapsed: bool) -> Sidebar:
        group = self.require_group(group_id)
        return self._replace_group(self._group_with(group, collapsed=bool(collapsed)))

    def reordered_groups(self, group_ids: Sequence[str]) -> Sidebar:
        """Put the groups in this order. Any omitted keep their relative order."""
        by_id = {g.group_id: g for g in self.groups}
        unknown = [g for g in group_ids if g not in by_id]
        if unknown:
            raise SidebarError(f"unknown sidebar group(s): {', '.join(unknown)}")
        ordered = [by_id[g] for g in group_ids]
        ordered.extend(g for g in self.groups if g.group_id not in set(group_ids))
        return self._rebuilt(ordered)

    def with_item(
        self,
        route: str,
        *,
        group_id: str | None = None,
        label: str = "",
        position: int | None = None,
    ) -> Sidebar:
        """Add a destination. Adding one that is already here is refused.

        Refused rather than moved, because "add" and "move" are different
        intentions and silently doing the second when asked for the first loses
        whatever the operator had arranged.
        """
        route = canonical(route) or route
        if not known(route):
            raise SidebarError(
                f"'{route}' is not a destination. Valid: {', '.join(sorted(CATALOGUE))}"
            )
        if self.has(route):
            raise SidebarError(f"'{route}' is already in this sidebar")
        target = group_id or (self.groups[0].group_id if self.groups else "")
        if not target:
            # An empty sidebar gaining its first item gets a group to put it in,
            # rather than refusing an edit that has an obvious meaning.
            return self.with_group("general", "General").with_item(
                route, group_id="general", label=label, position=position
            )
        group = self.require_group(target)
        items = list(group.items)
        item = SidebarItem(route=route, label=label)
        items.insert(len(items) if position is None else max(0, position), item)
        return self._replace_group(self._group_with(group, items=tuple(items)))

    def without_item(self, route: str) -> Sidebar:
        found = self.locate(route)
        if found is None:
            raise SidebarError(f"'{route}' is not in this sidebar")
        group_id, _ = found
        group = self.require_group(group_id)
        return self._replace_group(
            self._group_with(
                group,
                items=tuple(i for i in group.items if i.route != (canonical(route) or route)),
            )
        )

    def _map_item(self, route: str, **changes: Any) -> Sidebar:
        found = self.locate(route)
        if found is None:
            raise SidebarError(f"'{route}' is not in this sidebar")
        group = self.require_group(found[0])
        return self._replace_group(
            self._group_with(
                group,
                items=tuple(
                    SidebarItem(
                        **{
                            "route": i.route,
                            "label": i.label,
                            "pinned": i.pinned,
                            "hidden": i.hidden,
                            **changes,
                        }
                    )
                    if i.route == (canonical(route) or route)
                    else i
                    for i in group.items
                ),
            )
        )

    def renamed_item(self, route: str, label: str) -> Sidebar:
        return self._map_item(route, label=label)

    def pinned_item(self, route: str, pinned: bool) -> Sidebar:
        return self._map_item(route, pinned=bool(pinned))

    def hidden_item(self, route: str, hidden: bool) -> Sidebar:
        return self._map_item(route, hidden=bool(hidden))

    def moved_item(self, route: str, *, group_id: str, position: int | None = None) -> Sidebar:
        """Move an item to a group and a position, keeping its own settings."""
        found = self.locate(route)
        if found is None:
            raise SidebarError(f"'{route}' is not in this sidebar")
        source = self.require_group(found[0])
        item = next(i for i in source.items if i.route == (canonical(route) or route))
        target = self.require_group(group_id)
        without = self.without_item(route)
        # Re-read the target: removing may have changed it.
        target = without.require_group(group_id)
        items = list(target.items)
        items.insert(len(items) if position is None else max(0, position), item)
        return without._replace_group(self._group_with(target, items=tuple(items)))

    def as_dict(self) -> dict[str, Any]:
        """The shape the interface renders, with catalogue detail filled in."""
        return {
            "groups": [
                {
                    "group_id": group.group_id,
                    "label": group.label,
                    "collapsed": group.collapsed,
                    "items": [
                        {
                            "route": item.route,
                            "label": item.display_label(),
                            "renamed": bool(item.label),
                            "detail": _entry(item.route).detail,
                            "pinned": item.pinned,
                            "hidden": item.hidden,
                            "panel_kinds": list(_entry(item.route).panel_kinds),
                        }
                        for item in group.items
                    ],
                }
                for group in self.groups
            ]
        }


def default_sidebar_for(mode: WorkspaceMode | str) -> Sidebar:
    """The product's own rail, as an editable sidebar.

    It used to be built from one mode's sections, so a workspace created "from
    Prop Firm" opened with the Prop Firm rail and a workspace created from
    Normal opened with a different one. There is one rail now and this returns
    it — every workspace starts from the product's navigation and is then
    edited, which is what somebody building a custom rail was always doing with
    the extra step of picking a mode to start from.

    The parameter is kept and still validated so a caller that names a mode is
    refused when the mode does not exist, rather than silently getting a rail
    for something else.
    """
    descriptor = MODES[WorkspaceMode(str(mode))]
    groups: list[SidebarGroup] = []
    for section in descriptor.sections:
        group_id = section.group.lower().replace(" ", "_").replace("&", "and")[:60]
        existing = next((g for g in groups if g.group_id == group_id), None)
        item = SidebarItem(route=section.route)
        if existing is None:
            groups.append(SidebarGroup(group_id=group_id, label=section.group, items=(item,)))
        else:
            groups[groups.index(existing)] = SidebarGroup(
                group_id=existing.group_id,
                label=existing.label,
                items=(*existing.items, item),
                collapsed=existing.collapsed,
            )
    return Sidebar(groups=tuple(groups))


def sidebar_from(spec: Any) -> Sidebar:
    """Build a sidebar from a plain mapping, refusing anything unrenderable."""
    if isinstance(spec, Sidebar):
        return spec
    if not spec:
        return Sidebar()
    groups: list[SidebarGroup] = []
    for raw in spec.get("groups", []):
        items = tuple(
            SidebarItem(
                route=str(item.get("route", "")),
                label=str(item.get("label", "") or ""),
                pinned=bool(item.get("pinned", False)),
                hidden=bool(item.get("hidden", False)),
            )
            for item in raw.get("items", [])
        )
        groups.append(
            SidebarGroup(
                group_id=str(raw.get("group_id") or raw.get("label", "group")),
                label=str(raw.get("label") or raw.get("group_id", "Group")),
                items=items,
                collapsed=bool(raw.get("collapsed", False)),
            )
        )
    return Sidebar(groups=tuple(groups))

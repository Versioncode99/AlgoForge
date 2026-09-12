# Context Architecture

What the operator is looking at, which panels follow it, and why following
never overwrites anything.

Source: `packages/forge/workstation/context.py`, the `contexts` field on
`Workspace`, and the `set_context` / `clear_context` / `describe_context`
actions.

---

## The problem

Two, and they turned out to be one.

**`link_group` was a label attached to nothing.** `Panel.link_group` was
declared with the docstring *"Panels sharing a link group follow each other's
symbol and timeframe"*. `Workspace.linked()` wrote the tag. Nothing read it.
`Workspace.tsx:310` rendered the group's name as a badge on the panel header,
and the `link_panels` action's summary repeated the claim. So three places
asserted a behaviour to the operator, and no code implemented it.

**There was no context at all.** Every screen fetched its own subject.
Selecting NQ anywhere did not make anything else about NQ. The context bar could
name the workspace and the mode and could not name the instrument, the campaign,
the strategy or the account.

## The shape

One context per **link group**, held on the workspace. A panel resolves against
it at render time.

```
panel.link_group is None  ->  the panel's own settings, always
panel.link_group == "a"   ->  contexts["a"], falling back to its own settings
                              for any facet the context does not set
```

`resolve(panel, contexts) -> Resolved` is a pure function. It returns the
symbol, the timeframe, the group, and **where each value came from** —
`"panel"` or `"context"`. That last field exists because an operator looking at
a chart showing MNQ has to be able to tell whether that is the panel's own
pinned symbol or the workspace's current subject, and a surface that cannot
tell will eventually render one as the other.

## The decision the whole design rests on

**Setting a context writes nothing to any panel.**

The obvious alternative — when a panel in a group changes symbol, write the new
symbol to its neighbours — is what "link group" sounds like it should mean. It
is wrong, for one reason: it destroys the pinned value. A panel deliberately
pinned to MNQ, put in a group, loses MNQ the first time any group member moves,
and there is nothing left to restore it from.

Resolution-at-render has no such failure. The panel's `settings.symbol` is
never written when the context changes, so:

- a linked panel **displays** the active instrument;
- **unlinking reveals the symbol it was always pinned to**, unchanged.

This is what §6 of the brief means by *"do NOT globally mutate every panel's
state in destructive ways; use explicit context propagation"*. The pattern is
OpenTerminal's `useWidgetSymbol()`, generalised from one global symbol to named
groups so that §55's "Link Group A" — two groups looking at two instruments —
works.

## Facets

Six, and deliberately six fields rather than one "subject":

| Facet | Holds |
|---|---|
| `instrument` | A root from `InstrumentCatalogue`. Validated; unknown roots are refused. |
| `timeframe` | Free text, lowercased. Rides with the instrument on screen. |
| `dataset` | A dataset key. |
| `campaign_id` | Validated against the campaign store. |
| `strategy_id` | A strategy id. |
| `account_id` | An account id. |

An account is not a strategy; a campaign is not an instrument. Conflating them
is the category error the previous phase found in the interface, where the
governing workspace was labelled a mode. The context bar renders one chip per
set facet, each labelled as itself.

**Empty is a real value.** A context with no instrument means no instrument was
chosen, and a panel following it shows its own setting rather than a guess —
the same rule `dossier.py` follows when it reports `available: false` instead of
a zero. The bar renders nothing at all when nothing is set, because six chips
reading "—" is a row of controls that look broken.

## Storage

On the workspace, in a `contexts` column added by the store's existing
`_ADDED` ALTER TABLE migration with default `'{}'`.

On the workspace rather than in the browser for two reasons: a context is part
of an arrangement — reopening "NQ Lab" tomorrow should reopen it on NQ — and
the workspace store already versions, restores and exports everything else the
operator composed. `localStorage` would have been the second workspace system
§3 forbids.

A workspace saved before contexts existed has `{}`, which resolves to "no
context", which is correct: those panels keep showing exactly what they always
showed.

### Two things this uncovered

**Mutations rebuild rather than `model_copy`.** Pydantic's `model_copy` does not
re-run field validators, so the group ceiling and the empty-context sweep would
have applied only at first construction. The previous phase shipped exactly that
bug in the sidebar. `Workspace._revalidated` rebuilds, and
`test_the_group_ceiling_fires_on_a_mutation_not_only_at_construction` drives the
ceiling through a mutation rather than a constructor.

**`restore()` was discarding the sidebar.** A version records the layout —
name, template, profile, panels — and `version()` rebuilt a `Workspace` from
those columns alone. `restore()` saves whatever `version()` returns, so
restoring yesterday's panel arrangement silently dropped the rail the operator
had spent the afternoon building. Contexts would have joined it. `version()`
now carries every non-layout attribute from the workspace as it stands, which
is what its own docstring already claimed to do.

## Validation

Instrument names are **not** validated in `context.py`. The module knows nothing
about which futures exist; `forge.propdesk.instruments` owns that catalogue and
the action registry checks against it before a context is stored. Keeping the
check at the edge is what lets the module stay a dependency-free description of
*what is being looked at* rather than a second opinion about what exists.

`set_context` refuses a root the catalogue does not carry, naming the ones it
does. It also returns a `specification_warning` when the instrument's contract
specification is not individually verified — four of the twelve shipped
instruments are unverified, and the multiplier is the number a position would be
sized from.

## Honest edges

- A context set on a group no panel belongs to does nothing, and
  `set_context` says so in its result rather than reporting success.
- `link_group` still has to be set by `link_panels` for a panel to follow
  anything. Adding a panel does not put it in a group.
- Contexts are **not** yet read by the panel renderer. `describe_context`
  returns the resolved set and the context bar renders the facets; the chart
  panel still reads `settings.symbol` directly. Until that lands, a linked
  chart does not visually follow the context — see the final report's
  "remaining work".

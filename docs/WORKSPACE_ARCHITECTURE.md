# Workspace Architecture

## 1. The problem

Navigation was a constant of the **mode**. `forge.modes.models` declares four
`ModeDescriptor`s, each with a fixed tuple of `Section`s, and the shell rendered
whichever belonged to the session's mode.

So wanting prop accounts beside research agents meant switching modes — and
switching swapped the rail, the layout and the context. People responded by
keeping four windows open, which is the behaviour that told us the model was
wrong.

A mode is a good idea about **permissions**. It was a bad idea about
**navigation**.

## 2. The model now

```
Workspace
├── panels          (unchanged: 12-column grid, immutable, versioned)
├── sidebar         (new: groups of destinations the operator chose)
├── description, icon, kind, pinned
├── campaign_ids, account_ids   (links, never grants)
└── mode            (which built-in rail it descends from, if any)
```

`forge.workstation.sidebar.CATALOGUE` is the **union** of every destination any
mode offers — 49 of them — derived from the manifests rather than written again,
so a section added to a mode appears without an edit and one removed cannot
linger pointing at nothing.

A `SidebarItem` whose route is not in the catalogue is refused at construction.
A rail item pointing at nothing is exactly the dead control the product rules
forbid.

## 3. Nothing recognisable moved

`default_sidebar_for(mode)` rebuilds each built-in rail from that mode's own
sections. The four environments open exactly as they did and are now editable.

A workspace with `sidebar=None` falls back to its mode's rail. `None` and an
empty sidebar are deliberately different: the first means "I never chose", the
second means "I emptied it on purpose".

## 4. Operations

Add · remove · move · reorder · rename · group · collapse · pin · hide, on items
and on groups, plus `reset_sidebar` to rebuild the mode's default. Every one is
an action in the **shared registry**:

```
list_sidebar_destinations   describe_sidebar
add_sidebar_item            remove_sidebar_item      move_sidebar_item
rename_sidebar_item         pin_sidebar_item         hide_sidebar_item
add_sidebar_group           remove_sidebar_group     rename_sidebar_group
collapse_sidebar_group      reorder_sidebar_groups   reset_sidebar
link_campaign_to_workspace  link_account_to_workspace
describe_this_workspace     pin_workspace
```

There is **no AI-only path**. "Add the agent monitor to this workspace" asked of
the assistant and the same thing done by hand call the same function, so a
capability the interface does not have is not one an agent can invent.

`create_workspace` takes `sidebar_items`, so composing a rail is one call:
"a workspace for NQ research and prop trading" is one intention, not nine.

## 5. Immutability and validation

`Workspace` and `Sidebar` are frozen; every operation returns a new one, so a
refused edit leaves the caller holding what it had.

**Every mutation rebuilds rather than copying.** Pydantic's `model_copy` does not
re-run field validators, so building new state with it meant the duplicate-route
check and both ceilings only ever ran at construction — a rail could be edited
into a shape it could not have been created in. Caught by the bounds test.

## 6. Persistence

`workspaces.db`, migrated by `ALTER TABLE`: somebody who has arranged their
screen should not be asked to do it again. Version history, restore,
duplicate-from-version, export and import all carry the rail.

`summaries()` returns the switcher's projection — name, icon, description, kind,
pinned, links, panel count — without loading a single panel.

## 7. What a workspace still cannot do

Putting a campaign in a workspace does not start it. Putting an account there
does not connect it. Putting a destination in a rail does not grant access to
what is behind it — that remains `forge.modes.permissions`' decision. A
navigation list that could widen a capability would be a permission system with
an "add to sidebar" button.

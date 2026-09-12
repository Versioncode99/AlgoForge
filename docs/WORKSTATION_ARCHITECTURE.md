# Workstation Architecture

The shell: what governs a screen, what is on it, what is being looked at, and
how anything gets done.

This describes the **shell**. The workspace model itself — panels, the grid, the
sidebar catalogue, versioning — is `docs/WORKSPACE_ARCHITECTURE.md`, and is not
repeated here.

---

## The four kinds of fact

The single most important structural decision in this shell, and the one the
previous phase got wrong and fixed: these are four different things and must not
be run together.

| | What it is | Where it lives |
|---|---|---|
| **Mode** | What an assistant may do on your behalf | `forge.modes`, `permissions.evaluate()` |
| **Workspace** | The arrangement the operator built | `forge.workstation`, a row in the store |
| **Subject** | What is being looked at | `Workspace.contexts`, per link group |
| **Telemetry** | What the system is doing | `/health`, `/summary`, the activity log |

The interface used to call the governing workspace a mode. It now shows the
workspace's own name, with the mode beside it, smaller, labelled as the
permissions in force. The subject is a third group of chips, and the telemetry a
fourth. `ContextBar` renders the subject; it is deliberately not merged into
`.context-facts`.

## Layout

```
┌────────────┬──────────────────────────────────────────────────────────┐
│            │ context-bar: mode · workspace · section · SUBJECT · facts │
│  rail      ├──────────────────────────────────────────────────────────┤
│            │                                                          │
│  the       │  main — the section, or the workspace grid                │
│  workspace │                                                          │
│  owns it   │                                                          │
│            ├──────────────────────────────────────────────────────────┤
│ workspaces │ global-status: latest event · event drawer                │
└────────────┴──────────────────────────────────────────────────────────┘
              overlays: command palette · workspace switcher · drawer
```

The rail comes from the **workspace** when it has one of its own and from the
mode manifest otherwise. That fallback is what keeps every arrangement saved
before sidebars were ownable working.

## How anything gets done

**One registry.** `apps/api/forge_api/actions.py` holds 150 actions behind a
single `_add` registration path and a single `call()` dispatch. Four surfaces
use it and none of them has a private route:

- the interface, over `POST /api/v1/actions/<name>` and the typed routes that
  call the same handlers;
- the command palette (`docs/COMMAND_PALETTE_ARCHITECTURE.md`);
- the assistant, through its bounded tool loop;
- the MCP server, which exposes the non-mutating subset by default.

Every call goes through `forge.modes.permissions.evaluate()`, first-match-first,
so a refusal has exactly one reason and the reason is the rule that fired. The
rule that makes "AI cannot raise its own limits" a property of the code rather
than an intention:

> Protected controls. Every mode, every stance, no exception.

An action above `ActionRisk.SAFE` refuses without `confirmed`, and `confirmed`
is the operator's answer, not the caller's opinion. The HTTP action endpoint
cannot pass it — deliberately, and it was not widened for the palette.

### What this phase added to the registry

Campaign lifecycle (15 verbs) and the workstation context and instrument verbs
(5), plus `data_health`. Campaigns had thirty-odd HTTP routes and no presence in
the registry at all, so the interface could start a campaign and the assistant
could not — not because it was denied, but because the verb did not exist in the
only vocabulary it has.

None of the campaign verbs is `protected`: a campaign allocates *research*
effort, reaches no account and no money, and `priority` allocates workers and
nothing else. `archive_campaign` is `CONFIRM`, because an archive holds work
somebody did. Both are asserted as properties, so a campaign verb that later
becomes a route to an execution control is noticed.

## Panels

A closed `PanelKind` set, for the same reason the registry is closed: the agent
can add panels, and "add a panel of any kind you like" is not a bounded
capability. Adding a kind is a code change with a component behind it, which is
what stops the registry describing something that cannot render.

Kinds with no view fall through to `NotBuilt`, which says what is missing and
why — depth and an order ticket need a live quote and a venue, and this build
has neither, so nothing is drawn rather than something indistinguishable from a
connected panel.

Panels resolve their subject at render time; see `docs/CONTEXT_ARCHITECTURE.md`.
The panel **title** follows what is shown rather than what is stored, because a
header reading "MNQ 5m" over a body reading "no archive for NQ" is worse than
either being wrong on its own.

## Keyboard

| Key | Does |
|---|---|
| `Ctrl/Cmd + K` | Toggle the command palette |
| `↑` `↓` | Move the palette cursor |
| `Enter` | Run or open the selected row |
| `Escape` | Close the palette, the overlay rail, the switcher |
| `Tab` | Ordinary focus order; every control is reachable |

**Deliberately no more than this yet.** §5 of the brief suggests `Ctrl+Shift+R`,
`Ctrl+Shift+P` and others. A real conflict check rules several of them out —
`Cmd+Shift+P` is Firefox's private window, `Alt+digit` switches tabs on Windows
and Linux — and a shortcut that fights the browser is worse than no shortcut.
Shortcuts also may never be the only route to a capability (§37), so each one
needs a visible control first. This is recorded as remaining work rather than
shipped half-checked.

## Cost

Measured against the running application with 60 campaigns registered:

| | |
|---|---|
| Initial load and entering a workspace | 1,289 ms, 80 requests |
| Palette open | 229 ms |
| Palette search across 60 campaigns | 39 ms, 12 rows rendered |
| Data Health first render | 831 ms, 6 requests |
| Idle 20 s on Data Health | 5 requests |
| `list_campaigns` (60) | 21 ms median |
| `data_health` | 28 ms median |
| `describe_context` | 5 ms median |

The palette caps each group at six rows, which is what keeps search flat as
campaigns accumulate. Record queries run only while the palette is open; the
action schemas and the instrument catalogue are fetched with
`staleTime: Infinity` because the registry is fixed for the life of the process.

The five idle requests are the shell's pre-existing activity poll (5 s) and
health poll (15 s), plus Data Health's own 30 s refresh.

## Responsive

Verified at 1920, 1440, 1024 and 420 on the running application: no horizontal
overflow at any width, no console errors, the palette usable at all four.

At 420 the rail becomes an overlay behind a toggle, the telemetry chips collapse
to the first, the instrument chip drops its timeframe suffix, and Data Health's
four-column status line stacks rather than squeezing.

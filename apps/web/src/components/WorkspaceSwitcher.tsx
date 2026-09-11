import { Check, Layers, Pin, Plus, X } from 'lucide-react'
import { useState } from 'react'
import {
  useActiveWorkspace, useCreateWorkspace, useOpenWorkspace, usePinWorkspace, useWorkspaces,
  useSidebarDestinations, byGroup,
} from '../workspaces'

/* The switcher, and the reason it replaces the mode chooser.
 *
 * The four built-in environments are still here — they are good starting
 * points and people recognise them. What is gone is the idea that they are the
 * only arrangements that exist, and that reaching a feature means leaving the
 * one you are in.
 *
 * A workspace in this list is a saved arrangement: panels, a rail, linked
 * campaigns and accounts. "+ Create workspace" is deliberately the most visible
 * control after the list itself, because the whole change is worth nothing if
 * nobody finds it.
 */

const BUILT_INS: { mode: string; name: string; icon: string; detail: string }[] = [
  { mode: 'normal', name: 'Normal', icon: '◈', detail: 'Charts, strategies and a paper book.' },
  { mode: 'prop_firm', name: 'Prop Firm', icon: '◆', detail: 'Accounts, rules, drawdown and the desk.' },
  { mode: 'ai', name: 'AI', icon: '◇', detail: 'Campaigns, agents and the research record.' },
  { mode: 'hedge_fund', name: 'Hedge Fund', icon: '◼', detail: 'Portfolio, risk, the gate and approvals.' },
]

export function WorkspaceSwitcher({ onOpened }: { onOpened?: () => void }) {
  const [creating, setCreating] = useState(false)
  const workspaces = useWorkspaces()
  const active = useActiveWorkspace()
  const open = useOpenWorkspace()
  const pin = usePinWorkspace()

  const rows = workspaces.data?.workspaces ?? []
  const activeId = workspaces.data?.active ?? null

  return (
    <section className="ws-switcher" aria-label="Workspaces">
      <header>
        <Layers aria-hidden="true" />
        <h2>Workspaces</h2>
        <p>A workspace is an arrangement, not a mode. Anything can live in any of them.</p>
      </header>

      <ul className="ws-list">
        {rows.map((row) => (
          <li key={row.workspace_id} aria-current={row.workspace_id === activeId ? 'true' : undefined}>
            <button
              className="ws-open"
              onClick={() => { open.mutate(row.workspace_id); onOpened?.() }}
            >
              <span className="ws-icon" aria-hidden="true">{row.icon || row.name.slice(0, 1)}</span>
              <span className="ws-meta">
                <strong>{row.name}</strong>
                <span>{row.description || `${row.panel_count} panel${row.panel_count === 1 ? '' : 's'}`}</span>
              </span>
              {row.workspace_id === activeId && <Check className="ws-current" aria-hidden="true" />}
            </button>
            <button
              className="ws-pin"
              aria-label={row.pinned ? `Unpin ${row.name}` : `Pin ${row.name}`}
              aria-pressed={row.pinned}
              onClick={() => pin.mutate({ workspace_id: row.workspace_id, pinned: !row.pinned })}
            ><Pin aria-hidden="true" /></button>
          </li>
        ))}
        {workspaces.isSuccess && !rows.length && (
          <li className="ws-none"><p>No saved workspaces yet. Start from one of the four below, or build your own.</p></li>
        )}
      </ul>

      <button className="ws-create" onClick={() => setCreating(true)}>
        <Plus aria-hidden="true" /><span>Create workspace</span>
      </button>

      <section className="ws-builtins">
        <h3>Start from</h3>
        <div>
          {BUILT_INS.map((builtin) => (
            <button
              key={builtin.mode}
              onClick={() => setCreating(true)}
              title={`Create a workspace with the ${builtin.name} rail, then change anything.`}
            >
              <span aria-hidden="true">{builtin.icon}</span>
              <strong>{builtin.name}</strong>
              <small>{builtin.detail}</small>
            </button>
          ))}
        </div>
      </section>

      {creating && <CreateWorkspace onClose={() => setCreating(false)} onCreated={onOpened} />}
      {active.isError && <p className="ws-error" role="alert">The active workspace could not be loaded.</p>}
    </section>
  )
}

/** Creating one, with the rail composed up front.
 *
 *  Composing at creation rather than afterwards matters: "a workspace for NQ
 *  research and prop trading" is one intention, and making somebody create an
 *  empty desk and then add eight things to it is how a good idea becomes a
 *  chore nobody finishes. */
function CreateWorkspace({ onClose, onCreated }: { onClose: () => void; onCreated?: () => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [icon, setIcon] = useState('')
  const [mode, setMode] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const create = useCreateWorkspace()
  const catalogue = useSidebarDestinations()
  const groups = byGroup(catalogue.data?.destinations ?? [])

  const toggle = (route: string) =>
    setChosen((current) =>
      current.includes(route) ? current.filter((r) => r !== route) : [...current, route])

  return (
    <div className="ws-picker is-wide ws-create-dialog" role="dialog" aria-label="Create a workspace">
      <header>
        <strong>Create workspace</strong>
        <button aria-label="Cancel" onClick={onClose}><X /></button>
      </header>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (!name.trim()) return
          create.mutate(
            {
              name: name.trim(),
              description: description.trim(),
              icon: icon.trim(),
              mode,
              sidebar_items: chosen,
              activate: true,
            },
            { onSuccess: () => { onClose(); onCreated?.() } },
          )
        }}
      >
        <div className="ws-create-fields">
          <label>
            <span>Name</span>
            <input autoFocus value={name} placeholder="My Quant Desk" onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            <span>Icon</span>
            <input value={icon} placeholder="NQ" maxLength={8} onChange={(e) => setIcon(e.target.value)} />
          </label>
          <label className="ws-create-wide">
            <span>What it is for</span>
            <input
              value={description}
              placeholder="Prop accounts and NQ research on one screen"
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <label className="ws-create-wide">
            <span>Start from a rail</span>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="">Empty — I will choose everything</option>
              {BUILT_INS.map((b) => <option key={b.mode} value={b.mode}>{b.name}</option>)}
            </select>
          </label>
        </div>

        <fieldset className="ws-create-picker">
          <legend>
            Add destinations <small>{chosen.length} selected — any screen, from any mode</small>
          </legend>
          <div className="ws-picker-body">
            {groups.map((group) => (
              <section key={group.group}>
                <h3>{group.group}</h3>
                {group.items.map((destination) => (
                  <button
                    type="button"
                    key={destination.route}
                    aria-pressed={chosen.includes(destination.route)}
                    onClick={() => toggle(destination.route)}
                    title={destination.detail}
                  >
                    <strong>{destination.label}</strong>
                    <span>{destination.detail}</span>
                    {chosen.includes(destination.route) && <em>added</em>}
                  </button>
                ))}
              </section>
            ))}
          </div>
        </fieldset>

        <footer>
          {create.isError && <p role="alert">{String((create.error as Error)?.message ?? 'Could not create it.')}</p>}
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={!name.trim() || create.isPending}>
            {create.isPending ? 'Creating…' : 'Create workspace'}
          </button>
        </footer>
      </form>
    </div>
  )
}

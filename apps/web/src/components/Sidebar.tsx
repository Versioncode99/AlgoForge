import {
  ChevronDown, ChevronRight, EyeOff, FolderPlus, Pencil, Pin, PinOff,
  Plus, RotateCcw, Search, Trash2, X,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { sectionIcon } from './icons'
import {
  byGroup, useSidebarDestinations, useSidebarEdit,
  type SidebarDestination, type SidebarGroupView, type WorkspaceView,
} from '../workspaces'

/* The rail a person owns.
 *
 * What changed, and why it is worth the code: navigation used to belong to the
 * mode. Four fixed lists, one per operating environment, and the only way to
 * reach a screen from another environment was to leave the one you were in —
 * taking the layout, the context and the scroll position with you. People
 * responded by keeping four windows open.
 *
 * So the rail is data on the workspace. Any destination from any mode can sit
 * in any group, the operator names the groups, and nothing about that is
 * hard-coded here: this component renders whatever the workspace says and posts
 * every edit to the same action registry the assistant calls.
 *
 * Editing is behind a mode toggle rather than always-on. A rail that reveals a
 * delete control on hover is a rail you cannot navigate quickly, and navigating
 * is what it is mostly for.
 */

export function WorkspaceSidebar({
  workspace, route, onRoute, collapsed = false,
}: {
  workspace: WorkspaceView | null | undefined
  route: string
  onRoute: (route: string) => void
  collapsed?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [picking, setPicking] = useState<string | null>(null)
  const edit = useSidebarEdit(workspace?.workspace_id)
  const groups = workspace?.sidebar?.groups ?? []

  if (!workspace) {
    return <nav className="ws-rail is-empty" aria-label="Navigation"><p>Opening…</p></nav>
  }

  const present = new Set(groups.flatMap((group) => group.items.map((item) => item.route)))

  return (
    <nav className="ws-rail" aria-label={`${workspace.name} navigation`} data-editing={editing || undefined}>
      {groups.map((group) => (
        <SidebarGroup
          key={group.group_id}
          group={group}
          route={route}
          editing={editing}
          collapsedRail={collapsed}
          onRoute={onRoute}
          onToggle={() => edit.collapseGroup.mutate({ group_id: group.group_id, collapsed: !group.collapsed })}
          onAdd={() => setPicking(group.group_id)}
          onRemoveGroup={() => edit.removeGroup.mutate(group.group_id)}
          onRenameGroup={(label) => edit.renameGroup.mutate({ group_id: group.group_id, label })}
          onRemoveItem={(item) => edit.removeItem.mutate(item)}
          onPinItem={(item, pinned) => edit.pinItem.mutate({ route: item, pinned })}
          onHideItem={(item, hidden) => edit.hideItem.mutate({ route: item, hidden })}
          onRenameItem={(item, label) => edit.renameItem.mutate({ route: item, label })}
        />
      ))}

      {!groups.length && !editing && (
        <p className="ws-rail-empty">
          This workspace has no navigation yet. Turn on <b>Edit</b> to add what you need —
          any screen from any mode can live here.
        </p>
      )}

      <div className="ws-rail-tools">
        <button
          className="ws-rail-tool"
          aria-pressed={editing}
          onClick={() => { setEditing((on) => !on); setPicking(null) }}
        >
          <Pencil aria-hidden="true" /><span>{editing ? 'Done' : 'Edit sidebar'}</span>
        </button>
        {editing && (
          <>
            <button className="ws-rail-tool" onClick={() => setPicking('__new_group__')}>
              <FolderPlus aria-hidden="true" /><span>New group</span>
            </button>
            {workspace.sidebar_is_custom && workspace.mode && (
              <button
                className="ws-rail-tool"
                onClick={() => edit.reset.mutate()}
                title={`Rebuild the rail ${workspace.mode.replace('_', ' ')} ships with. The layout is untouched.`}
              >
                <RotateCcw aria-hidden="true" /><span>Restore default</span>
              </button>
            )}
          </>
        )}
      </div>

      {picking === '__new_group__' && (
        <NewGroup
          onCancel={() => setPicking(null)}
          onCreate={(groupId, label) => {
            edit.addGroup.mutate({ group_id: groupId, label })
            setPicking(null)
          }}
        />
      )}
      {picking && picking !== '__new_group__' && (
        <DestinationPicker
          present={present}
          onCancel={() => setPicking(null)}
          onPick={(destination) => {
            edit.addItem.mutate({ route: destination.route, group_id: picking })
            setPicking(null)
          }}
        />
      )}
    </nav>
  )
}

function SidebarGroup({
  group, route, editing, collapsedRail, onRoute, onToggle, onAdd, onRemoveGroup, onRenameGroup,
  onRemoveItem, onPinItem, onHideItem, onRenameItem,
}: {
  group: SidebarGroupView
  route: string
  editing: boolean
  collapsedRail: boolean
  onRoute: (route: string) => void
  onToggle: () => void
  onAdd: () => void
  onRemoveGroup: () => void
  onRenameGroup: (label: string) => void
  onRemoveItem: (route: string) => void
  onPinItem: (route: string, pinned: boolean) => void
  onHideItem: (route: string, hidden: boolean) => void
  onRenameItem: (route: string, label: string) => void
}) {
  const [renaming, setRenaming] = useState(false)
  // Pinned items lead, and survive a collapse — that is what pinning buys.
  const visible = group.items.filter((item) => editing || !item.hidden)
  const pinned = visible.filter((item) => item.pinned)
  const rest = visible.filter((item) => !item.pinned)
  const shown = group.collapsed ? pinned : [...pinned, ...rest]

  return (
    <section className="ws-group" data-collapsed={group.collapsed || undefined}>
      <h2>
        <button
          className="ws-group-toggle"
          aria-expanded={!group.collapsed}
          onClick={onToggle}
        >
          {group.collapsed ? <ChevronRight aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
          {renaming ? (
            <input
              className="ws-group-rename"
              defaultValue={group.label}
              autoFocus
              aria-label="Group name"
              onClick={(event) => event.stopPropagation()}
              onBlur={(event) => { onRenameGroup(event.currentTarget.value); setRenaming(false) }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') { onRenameGroup(event.currentTarget.value); setRenaming(false) }
                if (event.key === 'Escape') setRenaming(false)
              }}
            />
          ) : (
            <span>{group.label}</span>
          )}
        </button>
        {editing && !renaming && (
          <span className="ws-group-actions">
            <button aria-label={`Rename ${group.label}`} onClick={() => setRenaming(true)}><Pencil /></button>
            <button aria-label={`Add to ${group.label}`} onClick={onAdd}><Plus /></button>
            <button aria-label={`Remove ${group.label}`} onClick={onRemoveGroup}><Trash2 /></button>
          </span>
        )}
      </h2>

      {shown.map((item) => {
        const Icon = sectionIcon(item.route)
        return (
          <div className="ws-item" key={item.route} data-hidden={item.hidden || undefined}>
            <a
              href={`#${item.route}`}
              aria-current={route === item.route ? 'page' : undefined}
              title={item.detail}
              data-label={item.label}
              onClick={(event) => { event.preventDefault(); onRoute(item.route) }}
            >
              <Icon aria-hidden="true" />
              <span>{item.label}</span>
            </a>
            {editing && !collapsedRail && (
              <span className="ws-item-actions">
                {/* No drag grip. The model and the API both take a position, so
                  * drag-and-drop is implementable — but an icon with a grab
                  * cursor and no handler behind it is a control that lies, and
                  * the product rules refuse those. Moving between groups is the
                  * button below; reordering within one is not offered yet
                  * rather than offered and inert. */}
                <button
                  aria-label={item.pinned ? `Unpin ${item.label}` : `Pin ${item.label}`}
                  aria-pressed={item.pinned}
                  onClick={() => onPinItem(item.route, !item.pinned)}
                >{item.pinned ? <PinOff /> : <Pin />}</button>
                <button
                  aria-label={item.hidden ? `Show ${item.label}` : `Hide ${item.label}`}
                  aria-pressed={item.hidden}
                  onClick={() => onHideItem(item.route, !item.hidden)}
                ><EyeOff /></button>
                <button
                  aria-label={`Rename ${item.label}`}
                  onClick={() => {
                    const next = window.prompt(`Label for ${item.label}`, item.label)
                    if (next != null && next.trim()) onRenameItem(item.route, next.trim())
                  }}
                ><Pencil /></button>
                <button aria-label={`Remove ${item.label}`} onClick={() => onRemoveItem(item.route)}><X /></button>
              </span>
            )}
          </div>
        )
      })}

      {editing && !shown.length && <p className="ws-group-empty">Empty. Use + to add a destination.</p>}
    </section>
  )
}

function NewGroup({ onCreate, onCancel }: {
  onCreate: (groupId: string, label: string) => void
  onCancel: () => void
}) {
  const [label, setLabel] = useState('')
  const groupId = label.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').slice(0, 60)
  return (
    <div className="ws-picker" role="dialog" aria-label="New sidebar group">
      <header><strong>New group</strong><button aria-label="Cancel" onClick={onCancel}><X /></button></header>
      <form
        onSubmit={(event) => { event.preventDefault(); if (groupId) onCreate(groupId, label.trim()) }}
      >
        <label>
          <span>Name</span>
          <input
            autoFocus
            value={label}
            placeholder="MY TRADING"
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        <button type="submit" disabled={!groupId}>Create</button>
      </form>
    </div>
  )
}

/** The catalogue, searchable, with what is already in the rail marked.
 *
 *  Grouped by the destination's own group so the picker reads like the thing it
 *  feeds, and marked rather than filtered: "Charts is already here" is more
 *  useful than Charts silently not appearing. */
function DestinationPicker({ present, onPick, onCancel }: {
  present: Set<string>
  onPick: (destination: SidebarDestination) => void
  onCancel: () => void
}) {
  const [query, setQuery] = useState('')
  const catalogue = useSidebarDestinations()
  const groups = useMemo(() => {
    const all = catalogue.data?.destinations ?? []
    const needle = query.trim().toLowerCase()
    const matching = needle
      ? all.filter((d) =>
          d.label.toLowerCase().includes(needle) ||
          d.detail.toLowerCase().includes(needle) ||
          d.group.toLowerCase().includes(needle))
      : all
    return byGroup(matching)
  }, [catalogue.data, query])

  return (
    <div className="ws-picker is-wide" role="dialog" aria-label="Add a destination">
      <header>
        <strong>Add to sidebar</strong>
        <button aria-label="Cancel" onClick={onCancel}><X /></button>
      </header>
      <label className="ws-picker-search">
        <Search aria-hidden="true" />
        <input
          autoFocus
          value={query}
          placeholder="Search every screen…"
          aria-label="Search destinations"
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <div className="ws-picker-body">
        {catalogue.isPending && <p className="muted">Loading the catalogue…</p>}
        {catalogue.isError && <p className="muted">The catalogue could not be loaded.</p>}
        {!groups.length && catalogue.isSuccess && <p className="muted">Nothing matches “{query}”.</p>}
        {groups.map((group) => (
          <section key={group.group}>
            <h3>{group.group}</h3>
            {group.items.map((destination) => {
              const already = present.has(destination.route)
              return (
                <button
                  key={destination.route}
                  disabled={already}
                  onClick={() => onPick(destination)}
                  title={destination.detail}
                >
                  <strong>{destination.label}</strong>
                  <span>{destination.detail}</span>
                  {already && <em>already here</em>}
                </button>
              )
            })}
          </section>
        ))}
      </div>
    </div>
  )
}

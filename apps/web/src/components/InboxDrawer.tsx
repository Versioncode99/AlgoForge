/* Where work that finished while you were elsewhere waits.
 *
 * A drawer rather than a screen, and global rather than per mode, because the
 * question it answers -- "did that finish?" -- arrives while you are in the
 * middle of something else. Sending someone to a destination to find out would
 * lose the thing they were doing, which is the same cost as not telling them.
 *
 * What it shows is that work happened and how to get back to its subject. Never
 * what the work found: the result lives in its artifact, and a second copy here
 * could disagree with it.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Inbox as InboxIcon, X } from 'lucide-react'
import { deleteJson, getJson, postJson } from '../api'
import { OUTCOME_TONE, describeItem, destination, type InboxItem, type InboxPayload } from '../inbox'

/** Polled: arrival is a server-side event and there is no socket to hear it on.
 *  Fifteen seconds is well inside how long the work being waited on takes. */
const REFRESH_MS = 15_000

export function useInbox() {
  return useQuery({
    queryKey: ['inbox'],
    queryFn: () => getJson<InboxPayload>('/inbox'),
    refetchInterval: REFRESH_MS,
  })
}

export function InboxDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const client = useQueryClient()
  const inbox = useInbox()
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['inbox'] })
  }

  const read = useMutation({
    mutationFn: (itemId: string) => postJson(`/inbox/${itemId}/read`),
    onSuccess: refresh,
  })
  const readAll = useMutation({
    mutationFn: () => postJson('/inbox/read-all'),
    onSuccess: refresh,
  })
  const dismiss = useMutation({
    mutationFn: (itemId: string) => deleteJson(`/inbox/${itemId}`),
    onSuccess: refresh,
  })

  const items = inbox.data?.items ?? []

  return (
    /* `inert` rather than `aria-hidden`: the drawer holds focusable controls,
     * and hiding a subtree from assistive technology while leaving its buttons
     * in the tab order is worse than either. Same reason as the event drawer. */
    <aside
      className="inbox-drawer"
      data-open={open ? 'yes' : 'no'}
      aria-label="Finished work"
      inert={!open}
    >
      <header>
        <InboxIcon aria-hidden="true" />
        <strong>Finished work</strong>
        <span>{items.length === 1 ? '1 item' : `${items.length} items`}</span>
        <button type="button" onClick={() => readAll.mutate()} disabled={!inbox.data?.unread}>
          Mark all read
        </button>
        <button type="button" onClick={onClose}>Close</button>
      </header>

      {items.length === 0 && (
        <p className="state">
          Nothing has finished yet. Anything that runs in the background lands here when it
          ends — including the runs that fail, which is the half worth keeping.
        </p>
      )}

      <ul className="inbox-list">
        {items.map((item) => (
          <InboxRow
            key={item.item_id}
            item={item}
            onRead={() => read.mutate(item.item_id)}
            onDismiss={() => dismiss.mutate(item.item_id)}
          />
        ))}
      </ul>
    </aside>
  )
}

function InboxRow({
  item,
  onRead,
  onDismiss,
}: {
  item: InboxItem
  onRead: () => void
  onDismiss: () => void
}) {
  const href = destination(item)
  return (
    <li className="inbox-row" data-unread={item.read_at ? undefined : 'yes'}>
      <span className={`inbox-dot tone-${OUTCOME_TONE[item.outcome]}`} aria-hidden="true" />
      <div className="inbox-body">
        <strong>{item.label || item.kind}</strong>
        <span className="inbox-detail">{describeItem(item)}</span>
        <span className="inbox-meta mono">{item.kind} · {item.arrived_at.replace('T', ' ').slice(0, 16)}</span>
      </div>
      <div className="inbox-actions">
        {href ? (
          <a className="inbox-open" href={href} onClick={onRead}>Open</a>
        ) : (
          /* The job recorded no subject, so there is nowhere honest to send
             anyone. Said plainly rather than as a button that guesses. */
          <span className="inbox-open disabled">No destination</span>
        )}
        {!item.read_at && (
          <button type="button" aria-label={`Mark ${item.label} read`} onClick={onRead}>
            <Check size={13} />
          </button>
        )}
        <button type="button" aria-label={`Dismiss ${item.label}`} onClick={onDismiss}>
          <X size={13} />
        </button>
      </div>
    </li>
  )
}

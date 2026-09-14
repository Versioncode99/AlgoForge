import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WindowGroups } from './WindowGroups'

/* The surface the shell had been waiting for.
 *
 * The bridge is installed on `window` exactly as the preload installs it, so
 * these exercise the same detection path the real application takes -- the
 * point being that "no shell" is a first-class answer rather than a crash.
 */

type Bridge = {
  desktop: true
  workspaces: Record<string, ReturnType<typeof vi.fn>>
}

function install(overrides: Record<string, unknown> = {}): Bridge {
  const bridge = {
    desktop: true as const,
    workspaces: {
      open: vi.fn(),
      list: vi.fn(async () => ({
        self: 2,
        windows: [
          { windowId: 1, workspaceId: 'ws_desk', groupId: 'wsg_1' },
          { windowId: 2, workspaceId: 'ws_lab', groupId: 'wsg_1' },
          { windowId: 3, workspaceId: 'ws_charts', groupId: null },
        ],
      })),
      close: vi.fn(),
      group: vi.fn(async () => ({ groupId: 'wsg_2', windowIds: [2, 3] })),
      ungroup: vi.fn(async () => ({ groupId: 'wsg_1' })),
      restoreSession: vi.fn(),
      ...overrides,
    },
  }
  ;(window as { algoforge?: unknown }).algoforge = bridge
  return bridge as Bridge
}

afterEach(() => {
  delete (window as { algoforge?: unknown }).algoforge
})

const names = new Map([
  ['ws_desk', 'NQ Prop Desk'],
  ['ws_lab', 'Research Lab'],
  ['ws_charts', 'Charts'],
])

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <WindowGroups names={names} />
    </QueryClientProvider>,
  )
}

describe('window groups', () => {
  it('explains itself in a browser instead of offering controls that cannot work', () => {
    mount()
    expect(screen.getByText(/need the desktop application/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Group selected/ })).not.toBeInTheDocument()
  })

  it('names windows by their workspace and marks the one you are in', async () => {
    install()
    mount()
    expect(await screen.findByText('NQ Prop Desk')).toBeInTheDocument()
    expect(screen.getByText('Charts')).toBeInTheDocument()
    expect(screen.getByText('this window')).toBeInTheDocument()
  })

  it('separates what is grouped from what is not', async () => {
    install()
    mount()
    expect(await screen.findByText('NQ Prop Desk · Research Lab')).toBeInTheDocument()
    expect(screen.getByText('Not grouped')).toBeInTheDocument()
  })

  it('refuses to group one window, and says why before you press it', async () => {
    install()
    mount()
    await screen.findByText('Charts')
    const boxes = screen.getAllByRole('checkbox')
    fireEvent.click(boxes[boxes.length - 1])
    expect(screen.getByText('A group needs at least two windows.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Group selected/ })).toBeDisabled()
  })

  it('will not regroup a selection that is already exactly one group', async () => {
    install()
    mount()
    await screen.findByText('NQ Prop Desk')
    const boxes = screen.getAllByRole('checkbox')
    fireEvent.click(boxes[0])
    fireEvent.click(boxes[1])
    expect(screen.getByText('These windows are already one group.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Group selected/ })).toBeDisabled()
  })

  it('sends the selected ids to the shell and reports what it did', async () => {
    const bridge = install()
    mount()
    await screen.findByText('NQ Prop Desk')
    const boxes = screen.getAllByRole('checkbox')
    fireEvent.click(boxes[1])
    fireEvent.click(boxes[2])
    fireEvent.click(screen.getByRole('button', { name: /Group selected/ }))
    await waitFor(() => expect(bridge.workspaces.group).toHaveBeenCalledWith([2, 3]))
    expect(await screen.findByText(/close and restore together/)).toBeInTheDocument()
  })

  it('says so plainly when the shell refuses the grouping', async () => {
    const bridge = install({
      group: vi.fn(async () => {
        throw new Error('a group needs at least two windows')
      }),
    })
    mount()
    await screen.findByText('NQ Prop Desk')
    const boxes = screen.getAllByRole('checkbox')
    fireEvent.click(boxes[1])
    fireEvent.click(boxes[2])
    fireEvent.click(screen.getByRole('button', { name: /Group selected/ }))
    await waitFor(() => expect(bridge.workspaces.group).toHaveBeenCalled())
    expect(await screen.findByText(/shell refused/)).toBeInTheDocument()
  })

  it('takes one window out of its group', async () => {
    const bridge = install()
    mount()
    const button = await screen.findByLabelText('Take Research Lab out of its group')
    fireEvent.click(button)
    await waitFor(() => expect(bridge.workspaces.ungroup).toHaveBeenCalledWith(2))
  })

  it('offers no ungroup for a window that is not in a group', async () => {
    install()
    mount()
    await screen.findByText('Charts')
    expect(screen.queryByLabelText('Take Charts out of its group')).not.toBeInTheDocument()
  })

  it('says grouping does not move anything, because it does not', async () => {
    install()
    mount()
    expect(await screen.findByText(/It does not move them/)).toBeInTheDocument()
  })
})

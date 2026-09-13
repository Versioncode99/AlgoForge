import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { CHANNEL_NAMES, VISIBILITY_CHANNEL, validate } from './ipc-contract.js'

/* The boundary between a renderer and the main process.
 *
 * The failure this table exists to prevent is not an exploit in the usual
 * sense. It is a generic `invoke(channel, args)` door that looks like one small
 * surface and is every surface, because whatever validates it has to be written
 * per case anyway and nothing forces it to exist.
 */

describe('the channel set', () => {
  it('is closed, and refuses anything outside it', () => {
    expect(validate('fs:read', { path: '/etc/passwd' }).ok).toBe(false)
    expect(validate('', {}).ok).toBe(false)
    expect(validate('workspace:open ', { workspaceId: 'a' }).ok).toBe(false)
  })

  it('holds only window-management verbs', () => {
    /* Everything else the renderer needs it gets over HTTP, through the same
     * action registry and the same permission policy as every other surface.
     * A channel here that touched a file or a credential would be a second way
     * in with none of that. */
    for (const name of CHANNEL_NAMES) {
      expect(name.startsWith('workspace:')).toBe(true)
    }
    const forbidden = ['fs', 'file', 'path', 'exec', 'shell', 'db', 'sql', 'token', 'key', 'secret']
    for (const name of CHANNEL_NAMES) {
      for (const word of forbidden) expect(name).not.toContain(word)
    }
  })
})

describe('payloads', () => {
  it('accepts a well-formed call and returns only the declared fields', () => {
    const result = validate('workspace:open', { workspaceId: 'ws_a', intent: 'new' })
    expect(result).toEqual({ ok: true, payload: { workspaceId: 'ws_a', intent: 'new' } })
  })

  it('refuses a field nobody declared rather than ignoring it', () => {
    /* A handler reading only what it knows about accepts the rest silently, and
     * the day a later version reads one of those fields it is reading something
     * an earlier renderer was allowed to send. */
    const result = validate('workspace:open', { workspaceId: 'ws_a', __proto__: {}, cwd: '/' })
    expect(result.ok).toBe(false)
    expect(result.reason).toContain('cwd')
  })

  it('refuses a missing required field, naming it', () => {
    const result = validate('workspace:open', {})
    expect(result.ok).toBe(false)
    expect(result.reason).toContain('workspaceId')
  })

  it('refuses the wrong type rather than coercing it', () => {
    expect(validate('workspace:open', { workspaceId: 42 }).ok).toBe(false)
    expect(validate('workspace:close-window', { windowId: '1' }).ok).toBe(false)
    expect(validate('workspace:close-window', { windowId: 1.5 }).ok).toBe(false)
    expect(validate('workspace:close-window', { windowId: -1 }).ok).toBe(false)
  })

  it('refuses an intent that is not one of the three', () => {
    expect(validate('workspace:open', { workspaceId: 'a', intent: 'somewhere' }).ok).toBe(false)
    for (const intent of ['auto', 'current', 'new']) {
      expect(validate('workspace:open', { workspaceId: 'a', intent }).ok).toBe(true)
    }
  })

  it('refuses a rectangle that is not one', () => {
    const ok = { x: 0, y: 0, width: 800, height: 600 }
    expect(validate('workspace:remember-bounds', { windowId: 1, bounds: ok }).ok).toBe(true)
    for (const bad of [
      { x: 0, y: 0, width: 0, height: 600 },
      { x: 0, y: 0, width: 800 },
      { x: 'a', y: 0, width: 800, height: 600 },
      { x: 0, y: 0, width: Infinity, height: 600 },
      null,
    ]) {
      expect(validate('workspace:remember-bounds', { windowId: 1, bounds: bad }).ok).toBe(false)
    }
  })

  it('bounds the size of a group request', () => {
    /* An unbounded list is a way to make the main process do unbounded work
     * from a renderer. */
    expect(validate('workspace:group', { windowIds: [1] }).ok).toBe(false)
    expect(validate('workspace:group', { windowIds: [1, 2] }).ok).toBe(true)
    const many = Array.from({ length: 33 }, (_, i) => i)
    expect(validate('workspace:group', { windowIds: many }).ok).toBe(false)
  })

  it('treats a missing payload as an empty one for channels that take nothing', () => {
    expect(validate('workspace:windows').ok).toBe(true)
    expect(validate('workspace:windows', null).ok).toBe(true)
    expect(validate('workspace:windows', {}).ok).toBe(true)
  })

  it('refuses a payload that is not an object', () => {
    expect(validate('workspace:open', 'ws_a').ok).toBe(false)
    expect(validate('workspace:open', ['ws_a']).ok).toBe(false)
    expect(validate('workspace:open', 7).ok).toBe(false)
  })
})

describe('the preload under a sandbox', () => {
  /* `sandbox: true` preloads may require only `electron` and a few polyfilled
   * builtins. A local require throws *before* exposeInMainWorld runs, so the
   * symptom is not a missing constant -- it is the whole bridge absent, every
   * window verb dead, and the interface concluding it is in a browser.
   *
   * This shipped once. The unit tests all passed, because none of them runs a
   * real sandboxed preload; it took launching Electron to see it. These two
   * assertions are cheap and would have caught it in milliseconds. */

  // Read as text rather than imported: importing it would run it, and a
  // preload outside Electron has no contextBridge to talk to.
  const here = dirname(fileURLToPath(import.meta.url))
  const preload = readFileSync(join(here, 'preload.js'), 'utf8')

  it('requires nothing but electron', () => {
    const required = [...preload.matchAll(/require\(['"]([^'"]+)['"]\)/g)].map((m) => m[1])
    expect(required).toEqual(['electron'])
  })

  it('its copy of the visibility channel still matches the contract', () => {
    const declared = preload.match(/const VISIBILITY_CHANNEL = '([^']+)'/)
    expect(declared?.[1]).toBe(VISIBILITY_CHANNEL)
  })
})

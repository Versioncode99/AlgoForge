'use strict'

/**
 * The only things a renderer may ask the main process to do.
 *
 * The shape this avoids is `ipcMain.handle('invoke', (_, channel, args) => ...)`
 * — one generic door with the real channel in the payload. It looks like one
 * small surface and is actually every surface, because whatever validation sits
 * behind it has to be written per case anyway and nothing forces it to exist.
 *
 * So: a closed set of channels, each with a declared payload shape, validated
 * here and nowhere else. Three properties make it worth having as its own
 * module:
 *
 * **It fails closed.** An unknown channel is refused. A channel added to the
 * preload but not to this table does not work, which is the correct direction
 * for a mistake to fail — the alternative is a channel that works and was never
 * specified.
 *
 * **Unexpected keys are refused rather than ignored.** A handler reading only
 * the fields it knows about will happily accept a payload carrying others, and
 * the moment a later version reads one of them it is reading something an
 * earlier renderer was allowed to send. Rejecting the whole payload keeps the
 * contract the contract.
 *
 * **None of these channels touch the filesystem, the database or a credential.**
 * They are window-management verbs: which workspace goes where, and where the
 * windows sit. Everything else the renderer needs it already gets over HTTP from
 * the API, through the same action registry and the same permission policy as
 * every other surface — which is what stops the desktop shell becoming a second
 * way in.
 */

/** Field validators. Small on purpose: this is a boundary, not a schema library. */
const is = {
  workspaceId: (v) => typeof v === 'string' && v.length > 0 && v.length <= 200,
  windowId: (v) => Number.isInteger(v) && v >= 0,
  intent: (v) => v === 'auto' || v === 'current' || v === 'new',
  bounds: (v) =>
    v !== null &&
    typeof v === 'object' &&
    ['x', 'y', 'width', 'height'].every((k) => typeof v[k] === 'number' && Number.isFinite(v[k])) &&
    v.width > 0 &&
    v.height > 0,
  windowIds: (v) =>
    Array.isArray(v) && v.length >= 2 && v.length <= 32 && v.every((n) => Number.isInteger(n) && n >= 0),
}

/**
 * channel -> {required, optional}
 *
 * Named for what the operator is doing rather than for the mechanism, so a
 * reader can tell whether the set is complete by reading it.
 */
const CHANNELS = {
  'workspace:open': { required: { workspaceId: is.workspaceId }, optional: { intent: is.intent } },
  'workspace:close-window': { required: { windowId: is.windowId }, optional: {} },
  'workspace:remember-bounds': {
    required: { windowId: is.windowId, bounds: is.bounds },
    optional: {},
  },
  'workspace:group': { required: { windowIds: is.windowIds }, optional: {} },
  'workspace:ungroup': { required: { windowId: is.windowId }, optional: {} },
  'workspace:windows': { required: {}, optional: {} },
  'workspace:restore-session': { required: {}, optional: {} },
}

const CHANNEL_NAMES = Object.freeze(Object.keys(CHANNELS))

/**
 * Check one call.
 *
 * Returns `{ok: true, payload}` with a *rebuilt* payload containing only the
 * declared fields, so a handler cannot accidentally read something that was not
 * validated even if the table and the handler drift.
 */
function validate(channel, payload) {
  const spec = CHANNELS[channel]
  if (!spec) {
    return { ok: false, reason: `unknown channel '${channel}'` }
  }
  if (payload === undefined || payload === null) payload = {}
  if (typeof payload !== 'object' || Array.isArray(payload)) {
    return { ok: false, reason: `'${channel}' takes an object payload` }
  }

  const allowed = new Set([...Object.keys(spec.required), ...Object.keys(spec.optional)])
  const extra = Object.keys(payload).filter((key) => !allowed.has(key))
  if (extra.length) {
    return { ok: false, reason: `'${channel}' does not take ${extra.sort().join(', ')}` }
  }

  const clean = {}
  for (const [field, check] of Object.entries(spec.required)) {
    if (!(field in payload)) return { ok: false, reason: `'${channel}' needs ${field}` }
    if (!check(payload[field])) return { ok: false, reason: `'${channel}' got an invalid ${field}` }
    clean[field] = payload[field]
  }
  for (const [field, check] of Object.entries(spec.optional)) {
    if (!(field in payload)) continue
    if (!check(payload[field])) return { ok: false, reason: `'${channel}' got an invalid ${field}` }
    clean[field] = payload[field]
  }
  return { ok: true, payload: clean }
}

module.exports = { CHANNELS, CHANNEL_NAMES, validate }

/* A hash that carries a destination *and* what to open there.
 *
 * The application routes on `window.location.hash`, and until now the whole
 * hash was the route. That worked while a link was only ever `#strategies`, and
 * broke the moment anything wanted to say *which* strategy: `#strategies?
 * strategy=abc` matched no section, so the shell's correction effect decided it
 * was an unknown route and replaced it with the mode's first section.
 *
 * The failure is worse than a link that does nothing, because a link that does
 * nothing leaves you where you were. This one sent you somewhere else and
 * looked deliberate — the chat offered an artifact, the artifact offered a
 * button, the button went to Overview, and nothing anywhere said why.
 *
 * So the route and its parameters are separated once, here, and the rest of the
 * application keeps comparing routes to routes.
 */

/** A destination, and what it should open when it gets there. */
export type Route = {
  /** The section id, which is what the manifest lists. */
  path: string
  /** Everything after `?`. Empty when the link names no target. */
  params: Record<string, string>
}

/**
 * Split a hash into where to go and what to open.
 *
 * Takes the hash with or without its leading `#`, because callers get it both
 * ways — `location.hash` keeps it and `route` state has already dropped it, and
 * a helper that only accepts one of those spellings is a bug waiting for the
 * other caller.
 */
export function parse(hash: string): Route {
  const cleaned = hash.startsWith('#') ? hash.slice(1) : hash
  const split = cleaned.indexOf('?')
  if (split < 0) return { path: cleaned, params: {} }

  const path = cleaned.slice(0, split)
  const params: Record<string, string> = {}
  for (const [key, value] of new URLSearchParams(cleaned.slice(split + 1))) {
    // First wins. A repeated key is a malformed link rather than a list, and
    // silently taking the last one makes the malformation invisible.
    if (!(key in params)) params[key] = value
  }
  return { path, params }
}

/** Build a hash from a destination and its targets. Omits empty values. */
export function format(path: string, params: Record<string, string> = {}): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value)
  }
  const query = search.toString()
  return query ? `#${path}?${query}` : `#${path}`
}

/**
 * The section id alone, for comparing against a manifest.
 *
 * Named for the question the shell asks rather than as `parse(x).path`, because
 * every place that forgets to strip the parameters is a place a deep link
 * silently redirects.
 */
export function routeOf(hash: string): string {
  return parse(hash).path
}

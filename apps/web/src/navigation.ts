import { useQuery } from '@tanstack/react-query'
import { getJson } from './api'

/* The product's navigation, as the interface sees it.
 *
 * Every shape here mirrors `forge.product.navigation`. The shell holds no copy
 * of the destinations, the tabs or the legacy route map: it renders what the
 * manifest says, so a tab added in the backend appears here without an edit and
 * one removed cannot linger in a rail pointing at nothing.
 *
 * `resolve` is the one thing implemented twice, and deliberately. The server
 * owns the *declaration*; the client owns *applying* it, because a hash change
 * that had to wait for a round-trip would put a spinner between pressing a rail
 * row and seeing the screen. The declaration is fetched once — it is a build
 * artefact, not live state — and applied locally from then on.
 */

export type Tab = {
  tab: string
  label: string
  detail: string
  panel_kinds: string[]
  advanced: boolean
}

export type Destination = {
  route: string
  label: string
  detail: string
  group: string
  panel_kinds: string[]
  tabs: Tab[]
  default_tab: string
}

export type Navigation = {
  destinations: Destination[]
  groups: string[]
  legacy_routes: Record<string, string>
}

export type Located = {
  route: string
  tab: string
  /** True when the link named something this product no longer has and was
   *  translated. The shell rewrites the hash, so the reader can see where they
   *  actually ended up rather than wondering why the link "did nothing". */
  redirected: boolean
  params: Record<string, string>
}

export function useNavigation() {
  return useQuery({
    queryKey: ['navigation'],
    // A declaration, not live state: it changes when the application is
    // rebuilt. Refetching it on an interval would be requests a minute for a
    // constant.
    staleTime: Infinity,
    queryFn: () => getJson<Navigation>('/navigation'),
  })
}

/** Split a hash into its route, its tab and whatever else the link carried. */
function split(link: string): { route: string; tab: string; params: Record<string, string> } {
  const cleaned = link.startsWith('#') ? link.slice(1) : link
  const cut = cleaned.indexOf('?')
  if (cut < 0) {
    // `route:tab` is the path-safe spelling a saved sidebar stores, because a
    // sidebar item's route travels in a URL path where `?` would end it.
    const colon = cleaned.indexOf(':')
    if (colon > 0) {
      return { route: cleaned.slice(0, colon), tab: cleaned.slice(colon + 1), params: {} }
    }
    return { route: cleaned, tab: '', params: {} }
  }
  const params: Record<string, string> = {}
  for (const [key, value] of new URLSearchParams(cleaned.slice(cut + 1))) {
    // First wins. A repeated key is a malformed link rather than a list, and
    // taking the last one silently makes the malformation invisible.
    if (!(key in params)) params[key] = value
  }
  const tab = params.tab ?? ''
  delete params.tab
  return { route: cleaned.slice(0, cut), tab, params }
}

/** Build a hash from a destination, a tab and any targets. Omits empty values. */
export function format(route: string, tab = '', params: Record<string, string> = {}): string {
  const search = new URLSearchParams()
  if (tab) search.set('tab', tab)
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value)
  const query = search.toString()
  return query ? `#${route}?${query}` : `#${route}`
}

/**
 * Where a link lands, given the manifest.
 *
 * Returns `null` while the manifest is still loading rather than guessing: a
 * guess here rewrites the hash, and rewriting a deep link to Home before the
 * navigation has arrived is exactly the bug the old shell had.
 */
export function locate(link: string, nav: Navigation | undefined): Located | null {
  // Not just `!nav`. A manifest that arrived without destinations is a manifest
  // this shell cannot route against, and guessing would rewrite the hash — a
  // deep link becoming Home on every refresh is the bug this whole path exists
  // to avoid.
  if (!nav?.destinations?.length) return null
  const { route, tab, params } = split(link)
  const byRoute = new Map(nav.destinations.map((d) => [d.route, d]))

  let current = route
  let asked = tab
  let redirected = false

  if (!byRoute.has(current)) {
    const target = nav.legacy_routes[current]
    if (target === undefined) {
      return { route: 'home', tab: 'summary', redirected: true, params }
    }
    redirected = true
    const translated = split(target)
    current = translated.route
    if (translated.tab) asked = translated.tab
  }

  const destination = byRoute.get(current)!
  if (!destination.tabs.length) return { route: current, tab: '', redirected, params }
  if (asked && destination.tabs.some((t) => t.tab === asked)) {
    return { route: current, tab: asked, redirected, params }
  }
  // A tab this destination does not have is a moved link, not a missing screen.
  return {
    route: current,
    tab: destination.default_tab,
    redirected: redirected || Boolean(asked),
    params,
  }
}

/** The rail, grouped, in manifest order. */
export function railGroups(nav: Navigation | undefined): { group: string; items: Destination[] }[] {
  if (!nav) return []
  return nav.groups
    .map((group) => ({
      group,
      items: nav.destinations.filter((d) => d.group === group),
    }))
    .filter((entry) => entry.items.length > 0)
}

import { useQuery } from '@tanstack/react-query'
import { getJson } from './api'

/* Explanation and progressive disclosure, as the interface reads them.
 *
 * Everything here is a *declaration* fetched from the API rather than a
 * constant in this file, for the reason `forge.modes` gives for the mode
 * manifest: an agent asked "what can the operator see right now" has to be able
 * to answer without a person, and two copies of a mapping is a mapping that
 * drifts.
 */

export type ExpertiseLevel = 'guided' | 'advanced' | 'quant'

export type SurfaceRow = {
  surface: string
  label: string
  always_visible: boolean
}

export type LevelDescriptor = {
  level: ExpertiseLevel
  label: string
  detail: string
  surfaces: SurfaceRow[]
}

export type IntentDescriptor = {
  intent: string
  label: string
  detail: string
  actions: string[]
  sections: Record<string, string[]>
  modes: string[]
  caveat: string
}

export type MetricEntry = {
  key: string
  name: string
  what: string
  why: string
  definition: string
  caveats: string[]
  computed_by: string
  threshold: number | null
  threshold_source: string
  lower_is_better: boolean
  unit: string
}

export type Interpretation = {
  key: string
  value: number | null
  reading: 'meets' | 'below' | 'not_comparable' | 'no_standard'
  sentence: string
  caveats: string[]
  threshold: number | null
}

export type PassportSection = {
  kind: string
  title: string
  measured: boolean
  summary: string
  points: string[]
  metrics: { key: string; value: number | null; basis: string; explained: Interpretation }[]
  what_would_measure_it: string
  source: string
  depth: ExpertiseLevel
}

export type Passport = {
  strategy_id: string
  name: string
  stage: string
  sections: PassportSection[]
  limitations: string[]
  labels: string[]
  unmeasured: string[]
}

const key = (...parts: unknown[]) => ['explain', ...parts]

export function useExpertise() {
  return useQuery({
    queryKey: key('expertise'),
    // A declaration: it changes when the application is rebuilt.
    staleTime: Infinity,
    queryFn: () =>
      getJson<{ levels: LevelDescriptor[]; always_visible: string[] }>('/modes/expertise'),
  })
}

export function useIntents(mode?: string) {
  return useQuery({
    queryKey: key('intents', mode ?? 'all'),
    staleTime: Infinity,
    queryFn: () =>
      getJson<{ intents: IntentDescriptor[] }>(
        mode ? `/modes/intents?mode=${encodeURIComponent(mode)}` : '/modes/intents',
      ),
  })
}

export function useMetricGlossary(keys?: string[]) {
  const query = keys?.length ? `?keys=${encodeURIComponent(keys.join(','))}` : ''
  return useQuery({
    queryKey: key('metrics', keys?.join(',') ?? 'all'),
    staleTime: Infinity,
    queryFn: () => getJson<{ metrics: MetricEntry[] }>(`/explain/metrics${query}`),
  })
}

export function usePassport(strategyId: string, depth: ExpertiseLevel = 'advanced') {
  return useQuery({
    queryKey: key('passport', strategyId, depth),
    enabled: Boolean(strategyId),
    queryFn: () => getJson<Passport>(`/explain/passport/${strategyId}?depth=${depth}`),
  })
}

/** Which surfaces one level shows, as a set the caller can test against. */
export function surfacesFor(levels: LevelDescriptor[] | undefined, level: ExpertiseLevel) {
  const found = levels?.find((item) => item.level === level)
  return new Set(found?.surfaces.map((surface) => surface.surface) ?? [])
}

const STORAGE_KEY = 'algoforge.expertise'

/** The operator's chosen depth, remembered per browser.
 *
 * A preference, not a permission: it changes what is on screen and nothing
 * about what the system will do. Reading it is wrapped because a private window
 * with site data blocked throws rather than returning null.
 */
export function readExpertise(): ExpertiseLevel {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'guided' || stored === 'advanced' || stored === 'quant') return stored
  } catch {
    // No stored preference reachable. Advanced is the honest default: it is
    // what the rest of this application already assumes.
  }
  return 'advanced'
}

export function writeExpertise(level: ExpertiseLevel): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, level)
  } catch {
    // A preference that cannot be stored still applies for this session.
  }
}

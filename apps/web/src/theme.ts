/* Appearance: the one place that decides how the workstation looks and sounds.
 *
 * Everything here writes attributes on `<html>` and nothing else. A component
 * never asks what the theme is — it reads a token, and the token resolves
 * differently because `data-theme` changed. That is what makes a theme
 * addable without touching a component.
 *
 * ── where it is stored ──────────────────────────────────────────────────────
 * Server-side, in the operator settings the application already keeps. Not a
 * scattering of localStorage keys: an operator who configures the workstation
 * on one machine should find it configured on the next, and there is already a
 * store whose job is exactly this.
 *
 * localStorage is used for one thing only — remembering the last applied
 * appearance so the first paint after a reload is not the default theme
 * flashing to the chosen one while the settings request is in flight. It is a
 * cache of a server value, never the source of truth, and it is read inside a
 * try/catch because a private window makes it throw.
 */

import { useEffect, useState } from 'react'

import { getJson, patchJson } from './api'

export type ThemeKey = string
export type Appearance = {
  theme: ThemeKey
  accent: string
  density: string
  motion: string
  sound_enabled: boolean
  sound_volume: number
}

export type AppearanceOption = { key: string; label: string; detail: string }
export type AppearanceOptions = {
  themes: AppearanceOption[]
  accents: AppearanceOption[]
  densities: AppearanceOption[]
  motions: AppearanceOption[]
}

export const DEFAULT_APPEARANCE: Appearance = {
  theme: 'graphite',
  accent: 'silver',
  density: 'compact',
  motion: 'standard',
  sound_enabled: false,
  sound_volume: 0.35,
}

/** The single browser-storage key this application uses, and what it is for. */
const CACHE_KEY = 'algoforge.appearance'

/** Fired after the document attributes change, for anything CSS cannot reach. */
export const APPEARANCE_EVENT = 'algoforge:appearance'

/** Write the appearance onto the document. The whole of theming is these four lines.
 *
 * The event afterwards is for canvases. A chart is painted by a library that
 * takes colours as strings, so it does not inherit and does not repaint when a
 * custom property changes — it has to be told. Everything drawn in CSS needs
 * none of this.
 */
export function applyAppearance(appearance: Appearance): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.dataset.theme = appearance.theme
  root.dataset.accent = appearance.accent
  root.dataset.density = appearance.density
  root.dataset.motion = appearance.motion
  try {
    window.dispatchEvent(new CustomEvent(APPEARANCE_EVENT, { detail: appearance }))
  } catch {
    /* No window (tests, SSR). The attributes are what matter. */
  }
}

/** The last appearance applied, so the first paint is not a flash of the default. */
export function cachedAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return DEFAULT_APPEARANCE
    const parsed = JSON.parse(raw) as Partial<Appearance>
    return { ...DEFAULT_APPEARANCE, ...parsed }
  } catch {
    // A private window, cleared site data, or storage disabled. The default is
    // correct in all three; this is a cache, not the record.
    return DEFAULT_APPEARANCE
  }
}

function cache(appearance: Appearance): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(appearance))
  } catch {
    /* Never worth failing a theme change over. */
  }
}

export async function loadAppearance(): Promise<Appearance & { options: AppearanceOptions }> {
  const data = await getJson<Appearance & { options: AppearanceOptions }>('/settings/appearance')
  const appearance: Appearance = {
    theme: data.theme,
    accent: data.accent,
    density: data.density,
    motion: data.motion,
    sound_enabled: data.sound_enabled,
    sound_volume: data.sound_volume,
  }
  applyAppearance(appearance)
  cache(appearance)
  return data
}

export async function saveAppearance(
  patch: Partial<Appearance>,
): Promise<Appearance & { options: AppearanceOptions }> {
  const data = await patchJson<Appearance & { options: AppearanceOptions }>(
    '/settings/appearance',
    patch,
  )
  const appearance: Appearance = {
    theme: data.theme,
    accent: data.accent,
    density: data.density,
    motion: data.motion,
    sound_enabled: data.sound_enabled,
    sound_volume: data.sound_volume,
  }
  applyAppearance(appearance)
  cache(appearance)
  return data
}

/** Resolve a design token to its current value, for anything that cannot use CSS.
 *
 * Charts are drawn to a canvas by a library that takes colours as strings, so
 * they cannot inherit. This is the bridge, and the fallback matters: it is read
 * during server-side and test rendering where there is no computed style, and a
 * chart that fell back to nothing would draw invisible axes.
 */
export function token(name: string, fallback: string): string {
  if (typeof window === 'undefined' || typeof document === 'undefined') return fallback
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    return value || fallback
  } catch {
    return fallback
  }
}

/** Whether motion should be suppressed, from the setting or the OS. */
export function motionReduced(): boolean {
  if (typeof document === 'undefined') return false
  if (document.documentElement.dataset.motion === 'reduced') return true
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** A value that changes whenever the appearance does.
 *
 * Used as part of a chart's React `key`, so the chart is rebuilt with the new
 * palette rather than keeping the colours it was constructed with. Cheap: it
 * only changes when somebody changes a setting.
 */
export function useAppearanceVersion(): string {
  const [version, setVersion] = useState(() =>
    typeof document === 'undefined'
      ? 'graphite'
      : `${document.documentElement.dataset.theme ?? 'graphite'}:${
          document.documentElement.dataset.accent ?? 'silver'
        }`,
  )
  useEffect(() => {
    const onChange = (event: Event) => {
      const detail = (event as CustomEvent<Appearance>).detail
      setVersion(`${detail?.theme ?? 'graphite'}:${detail?.accent ?? 'silver'}`)
    }
    window.addEventListener(APPEARANCE_EVENT, onChange)
    return () => window.removeEventListener(APPEARANCE_EVENT, onChange)
  }, [])
  return version
}

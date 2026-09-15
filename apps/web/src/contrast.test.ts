/* Text contrast, computed from the tokens rather than observed on a screen.
 *
 * `tests/e2e/accessibility.spec.ts` runs axe against the rendered application,
 * which is the real audit and the one that counts. It has one weakness: it can
 * only see what is on screen. The provenance tier pill that says a result came
 * from SYNTHETIC data measured 3.33:1 — below WCAG AA — and the audit passed
 * for weeks because the strategy library on the machine running it was empty.
 * It failed the first time a run happened to leave a strategy behind.
 *
 * So the palette is also checked directly, here, with no browser and no state.
 * Every text token is measured against every surface it can legitimately sit on,
 * in all three themes, and the ratios are arithmetic from the hex values rather
 * than a rendering. A token that fails is a token that would fail on a screen
 * nobody happened to open.
 *
 * AA, not AAA: 4.5:1 for normal text. Everything here is normal text — the
 * interface has no headline type large enough to earn the 3:1 exemption, and
 * claiming it for a 10px uppercase label would be the exemption doing the
 * opposite of its job.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

const TOKENS = readFileSync(join(process.cwd(), 'src/styles/tokens.css'), 'utf8')

const MINIMUM = 4.5

/** The three themes, by the selector that opens each block. */
const THEMES: Record<string, string> = {
  graphite: ':root {',
  silver: ':root[data-theme="silver"] {',
  contrast: ':root[data-theme="contrast"] {',
}

/** Text tokens, and the surfaces each is allowed to appear on.
 *
 * `--bg-0` through `--bg-3` are the four surface steps: the page, a panel, a
 * raised panel, a popover. Text tokens are used across all of them, so each is
 * measured against all of them and the worst pairing is the one that has to
 * pass. Listing the surfaces rather than assuming one is the point: `--fg-3`
 * passed on `--bg-0` while failing on `--bg-3`, and the rail headings that wore
 * it sat on the lighter one.
 */
const TEXT = [
  '--fg-0',
  '--fg-1',
  '--fg-2',
  '--fg-3',
  '--tier-holdout',
  '--tier-oos',
  '--tier-insample',
  '--tier-synthetic',
]

const SURFACES = ['--bg-0', '--bg-1', '--bg-2', '--bg-3']

/** The tokens defined inside one theme's block. */
function palette(theme: string): Record<string, string> {
  const opener = THEMES[theme]
  const start = TOKENS.indexOf(opener)
  if (start < 0) throw new Error(`tokens.css has no ${theme} block (${opener})`)
  const end = TOKENS.indexOf('\n}', start)
  const block = TOKENS.slice(start, end)
  const found: Record<string, string> = {}
  for (const [, name, value] of block.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    found[name] = value
  }
  return found
}

/* The base theme defines every token; the other two redefine only colours, so
 * a token a theme does not restate is inherited from the base. */
const BASE = palette('graphite')

function resolve(theme: string, token: string): string {
  const own = palette(theme)[token]
  if (own) return own
  const inherited = BASE[token]
  if (!inherited) throw new Error(`no theme defines ${token}`)
  return inherited
}

function channel(value: number): number {
  return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
}

/** Relative luminance, WCAG 2.1 §1.4.3. */
function luminance(hex: string): number {
  const raw = hex.replace('#', '')
  const full =
    raw.length === 3
      ? raw
          .split('')
          .map((c) => c + c)
          .join('')
      : raw.slice(0, 6)
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (high + 0.05) / (low + 0.05)
}

describe('every text token is readable on every surface it can sit on', () => {
  test.each(Object.keys(THEMES))('%s', (theme) => {
    const failures: string[] = []
    for (const token of TEXT) {
      const foreground = resolve(theme, token)
      for (const surface of SURFACES) {
        const background = resolve(theme, surface)
        const ratio = contrast(foreground, background)
        if (ratio < MINIMUM) {
          failures.push(
            `${token} (${foreground}) on ${surface} (${background}): ` +
              `${ratio.toFixed(2)}:1, needs ${MINIMUM}:1`,
          )
        }
      }
    }
    expect(failures.sort().join('\n'), `${theme} has unreadable text`).toBe('')
  })

  test('the four provenance tiers stay distinguishable from each other', () => {
    /* Contrast against the background is not the whole claim. NOT_TESTED,
     * SYNTHETIC, VALIDATION_OOS and HOLDOUT mean four different things about
     * whether a number can be believed, and a palette that made two of them the
     * same colour would erase the distinction while passing every contrast
     * check above. */
    for (const theme of Object.keys(THEMES)) {
      const tiers = [
        '--tier-holdout',
        '--tier-oos',
        '--tier-insample',
        '--tier-synthetic',
      ].map((token) => resolve(theme, token))
      expect(new Set(tiers).size, `${theme} reuses a colour across provenance tiers`).toBe(
        tiers.length,
      )
    }
  })

  test('the greys stay ordered, so the hierarchy still reads as one', () => {
    /* Raising a failing tone is the fix; raising it past the tone above it is a
     * different bug. `--fg-0` is the loudest and `--fg-3` the quietest, and the
     * ordering has to survive every contrast correction. */
    for (const theme of Object.keys(THEMES)) {
      const ground = resolve(theme, '--bg-0')
      const ratios = ['--fg-0', '--fg-1', '--fg-2', '--fg-3'].map((token) =>
        contrast(resolve(theme, token), ground),
      )
      for (let i = 1; i < ratios.length; i += 1) {
        expect(
          ratios[i],
          `${theme}: --fg-${i} is not quieter than --fg-${i - 1} ` +
            `(${ratios[i].toFixed(2)} vs ${ratios[i - 1].toFixed(2)})`,
        ).toBeLessThan(ratios[i - 1])
      }
    }
  })
})

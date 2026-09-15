/* The stylesheets, checked against the components that use them.
 *
 * Removing a screen removes its markup and leaves its CSS, and the CSS is what
 * ships. Before this test the bundle carried a stylesheet for the mode chooser
 * that Phase 1 deleted, the wiring for a "neural map" nothing renders, and two
 * looping animations — `.af-blink`, `.af-halo` — that no element wears. None of
 * it was visible in review, because dead CSS looks exactly like CSS.
 *
 * So the rule is mechanical: every class a stylesheet defines must appear
 * somewhere in the TypeScript, or be named below as one that is composed at run
 * time. The allow-list is short and each entry says where the name is built,
 * which is the part a reviewer can check.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

// Read from disk rather than through Vite's `?raw` glob: Vite processes CSS
// even when it is asked for raw text, and a test about what a stylesheet
// contains has to read the stylesheet.
const ROOT = join(process.cwd(), 'src')
const STYLES = join(ROOT, 'styles')

function read(path: string): string {
  return readFileSync(path, 'utf8')
}

function walk(directory: string, out: string[] = []): string[] {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) walk(path, out)
    else if (/\.tsx?$/.test(entry.name) && entry.name !== 'styles.test.ts') out.push(path)
  }
  return out
}

const SHEET_SOURCE: Record<string, string> = Object.fromEntries(
  readdirSync(STYLES)
    .filter((name) => name.endsWith('.css'))
    .map((name) => [`./styles/${name}`, read(join(STYLES, name))]),
)

const CODE_SOURCE: Record<string, string> = Object.fromEntries(
  walk(ROOT).map((path) => [`./${path.slice(ROOT.length + 1)}`, read(path)]),
)

/** Class names assembled from a value at run time, so no literal exists.
 *
 * Each key is the prefix, and the value is where the rest of the name comes
 * from. A prefix here is a promise that the suffixes are bounded by a type —
 * not a general licence to define classes nothing uses.
 */
const COMPOSED: Record<string, string> = {
  'is-': 'severity, status and stance values: `is-${item.severity.toLowerCase()}` in Evidence.tsx and PanelBody.tsx',
  'tone-': 'tone from a lookup table: `tone-${STATUS_TONE[...]}` in Orchestrator.tsx and InboxDrawer.tsx',
  'hm-': 'health-matrix severity: `hm-${finding.severity}` in HealthMatrix.tsx',
  'objection-': 'objection state: `objection-${...}` in Evidence.tsx',
}

/** Names that appear only in the explanatory comments at the top of a
 * stylesheet, recording what an earlier shell used to define. They are prose
 * about deleted code, not selectors. */
const DOCUMENTED = new Set([
  'logbar',
  'logline',
  'rail-group',
  'rail-head',
  'rail-row',
  'topbar',
  'topstats',
  'view-head',
])

const CODE = Object.entries(CODE_SOURCE)
  .filter(([path]) => !path.endsWith('styles.test.ts'))
  .map(([, text]) => text)
  .join('\n')

const SHEETS: string[] = Object.keys(SHEET_SOURCE)
  .map((path) => path.split('/').pop() as string)
  .sort()

function sheet(name: string): string {
  return SHEET_SOURCE[`./styles/${name}`]
}

/** Selectors only, with comment bodies removed so prose cannot count as use. */
function definedClasses(css: string): Set<string> {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, ' ')
  const found = new Set<string>()
  for (const match of withoutComments.matchAll(/\.([a-zA-Z][\w-]*)/g)) found.add(match[1])
  return found
}

describe('the stylesheets carry nothing the application does not render', () => {
  test('every stylesheet is reachable from the entry point', () => {
    /* A stylesheet nobody imports is dead in a way the class check cannot see:
     * its classes may all be rendered and none of its rules ever apply. */
    const entry = CODE_SOURCE['./main.tsx']
    const imported = Object.values(SHEET_SOURCE).join('\n')
    for (const name of SHEETS) {
      const reachable =
        entry.includes(`./styles/${name}`) || imported.includes(`@import './${name}'`)
      expect(reachable, `${name} is imported by neither main.tsx nor another sheet`).toBe(true)
    }
  })

  test.each(SHEETS)('%s defines no class the code never names', (name: string) => {
    const classes = definedClasses(sheet(name))
    const orphans = [...classes].filter((name) => {
      if (DOCUMENTED.has(name)) return false
      if (Object.keys(COMPOSED).some((prefix) => name.startsWith(prefix))) return false
      // A word-boundary match: `.strat-list` must not be satisfied by
      // `strat-list-item` appearing somewhere in a component.
      return !new RegExp(`\\b${name.replace(/-/g, '-')}\\b`).test(CODE)
    })
    expect(
      orphans.sort(),
      `${name} defines classes nothing renders. Delete them, or render them.`,
    ).toEqual([])
  })

  test('the composed prefixes are each still used to build a class name', () => {
    /* An allow-list entry that outlives its call site is how the rule above
     * gets quietly widened. */
    for (const prefix of Object.keys(COMPOSED)) {
      expect(CODE, `nothing builds a \`${prefix}…\` class any more`).toMatch(
        new RegExp('`[^`]*' + prefix.replace('-', '-') + '\\$\\{'),
      )
    }
  })

  test('no stylesheet defines a looping animation nothing wears', () => {
    /* `infinite` on an element nobody renders is invisible; `infinite` on one
     * that is rendered is a decision somebody should have to make on purpose. */
    for (const name of SHEETS) {
      const css = sheet(name).replace(/\/\*[\s\S]*?\*\//g, ' ')
      for (const [, selector] of css.matchAll(
        /([^{}]+)\{[^{}]*animation:[^;}]*\binfinite\b[^;}]*[;}]/g,
      )) {
        for (const found of selector.matchAll(/\.([a-zA-Z][\w-]*)/g)) {
          if (Object.keys(COMPOSED).some((prefix) => found[1].startsWith(prefix))) continue
          expect(CODE, `${name}: .${found[1]} loops forever and nothing renders it`).toMatch(
            new RegExp(`\\b${found[1]}\\b`),
          )
        }
      }
    }
  })

  test('every animation honours prefers-reduced-motion', () => {
    /* Motion is the one place where a stylesheet can hide information: an
     * element that starts at `opacity: 0` and is animated in is invisible to
     * anyone whose system suppresses the animation. `motion.css` collapses each
     * of its animations to the finished state, and this checks that the
     * reduced-motion block still names every class that animates. */
    const css = sheet('motion.css')
    const reduced = css
      .split('@media (prefers-reduced-motion: reduce)')
      .slice(1)
      .join('\n')
    expect(reduced, 'motion.css has no reduced-motion block').not.toEqual('')
    const animated = new Set<string>()
    const outsideReduced = css.split('@media (prefers-reduced-motion: reduce)')[0]
    for (const [, selector] of outsideReduced.matchAll(/([^{}]+)\{[^{}]*animation:[^;}]*[;}]/g)) {
      for (const found of selector.matchAll(/\.([a-zA-Z][\w-]*)/g)) animated.add(found[1])
    }
    // A universal reset covers everything that is not named. It has to exist,
    // because naming every animating class is a list that goes stale silently.
    expect(reduced, 'motion.css has no universal reduced-motion reset').toMatch(
      /\*, \*::before, \*::after/,
    )
    const unguarded = [...animated].filter((name) => !reduced.includes(name))
    // Every *looping* animation needs its own rule, because the universal reset
    // sets `animation-iteration-count: 1` and a loop that conveys ongoing work
    // — a spinner — would freeze and keep claiming the work is running.
    const looping = [...outsideReduced.matchAll(
      /([^{}]+)\{[^{}]*animation:[^;}]*\binfinite\b[^;}]*[;}]/g,
    )].flatMap(([, selector]) =>
      [...selector.matchAll(/\.([a-zA-Z][\w-]*)/g)].map((found) => found[1]),
    )
    const frozen = looping.filter((name) => !reduced.includes(name))
    expect(frozen.sort(), 'these loop and the universal reset would freeze them').toEqual([])
    expect(unguarded.filter((name) => looping.includes(name))).toEqual([])
  })
})

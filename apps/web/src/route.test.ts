import { describe, expect, it } from 'vitest'
import { format, parse, routeOf } from './route'

/* The bug this exists to stop is not a dead link. It is a link that goes
 * somewhere else and looks deliberate: `#strategies?strategy=abc` matched no
 * section, so the shell decided it was an unknown route and replaced it with
 * the mode's first one. The chat offered an artifact, the artifact offered a
 * button, and the button went to Overview.
 */

describe('parse', () => {
  it('a plain route has no parameters', () => {
    expect(parse('strategies')).toEqual({ path: 'strategies', params: {} })
  })

  it('takes the hash with or without its leading marker', () => {
    /* location.hash keeps the #, route state has already dropped it, and a
     * helper that accepts only one spelling is a bug waiting for the other
     * caller. */
    expect(parse('#strategies')).toEqual(parse('strategies'))
  })

  it('separates the destination from what to open there', () => {
    expect(parse('#strategies?strategy=abc')).toEqual({
      path: 'strategies',
      params: { strategy: 'abc' },
    })
  })

  it('carries more than one parameter', () => {
    expect(parse('#strategies?strategy=abc&pane=port').params).toEqual({
      strategy: 'abc',
      pane: 'port',
    })
  })

  it('decodes what the link encoded', () => {
    expect(parse('#trades?strategy=a%20b%2Fc').params.strategy).toBe('a b/c')
  })

  it('a repeated key keeps the first, so a malformed link stays visible', () => {
    expect(parse('#x?a=1&a=2').params.a).toBe('1')
  })

  it('an empty query leaves the route intact', () => {
    expect(parse('#strategies?')).toEqual({ path: 'strategies', params: {} })
  })

  it('an empty hash is an empty route, not a crash', () => {
    expect(parse('')).toEqual({ path: '', params: {} })
    expect(parse('#')).toEqual({ path: '', params: {} })
  })

  it('a value containing an equals sign survives', () => {
    expect(parse('#x?token=a=b').params.token).toBe('a=b')
  })
})

describe('routeOf', () => {
  it('is what the manifest gets compared against', () => {
    /* Every place that forgets to strip the parameters is a place a deep link
     * silently redirects, which is why this has a name of its own. */
    expect(routeOf('#strategies?strategy=abc')).toBe('strategies')
    expect(routeOf('strategies')).toBe('strategies')
  })
})

describe('format', () => {
  it('builds a bare hash when there is nothing to open', () => {
    expect(format('strategies')).toBe('#strategies')
    expect(format('strategies', {})).toBe('#strategies')
  })

  it('omits empty values rather than emitting a key with nothing after it', () => {
    expect(format('strategies', { strategy: '' })).toBe('#strategies')
  })

  it('encodes what needs encoding', () => {
    expect(format('trades', { strategy: 'a b/c' })).toBe('#trades?strategy=a+b%2Fc')
  })

  it('round-trips through parse', () => {
    const params = { strategy: 'nq momentum/1', pane: 'port' }
    expect(parse(format('strategies', params)).params).toEqual(params)
  })
})

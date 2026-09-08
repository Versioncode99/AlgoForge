import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { playSound, sound, SOUND_EVENTS } from './sound'

/* Sound must never be able to break a research session.
 *
 * The tests that matter here are the failure ones. A browser with no Web Audio,
 * a context the user has not gestured into, a device with no output — every one
 * of those has to be silence, not an exception thrown into whichever component
 * was doing real work when it happened. Sound is the least important thing in
 * this application and it sits inside the most important ones.
 */

type Recorder = {
  created: number
  started: number
  /** Gains on the node that reaches the destination — the level that is
   *  actually heard. The overtone's own gain is a mixing ratio applied before
   *  the master, so asserting a ceiling against it measures nothing. */
  outputGains: number[]
  /** Every gain, master and internal, for tests about the chain's shape. */
  gains: number[]
  frequencies: number[]
}

function stubAudio(): Recorder {
  const recorder: Recorder = {
    created: 0,
    started: 0,
    outputGains: [],
    gains: [],
    frequencies: [],
  }

  class FakeParam {
    constructor(private readonly sink: number[]) {}
    setValueAtTime(value: number) {
      this.sink.push(value)
      return this
    }
    exponentialRampToValueAtTime(value: number) {
      this.sink.push(value)
      return this
    }
  }

  const output = {}

  class FakeNode {
    readonly values: number[] = []
    gain = new FakeParam(this.values)
    frequency = new FakeParam(recorder.frequencies)
    type = 'sine'
    connect(target: unknown) {
      // A node wired straight to the destination is the master: its gain is the
      // level a listener hears.
      if (target === output) recorder.outputGains.push(...this.values)
      recorder.gains.push(...this.values)
      return this
    }
    start() {
      recorder.started += 1
    }
    stop() {}
  }

  class FakeContext {
    state = 'running'
    currentTime = 0
    destination = output
    constructor() {
      recorder.created += 1
    }
    createGain() {
      return new FakeNode()
    }
    createOscillator() {
      return new FakeNode()
    }
    resume() {
      return Promise.resolve()
    }
    suspend() {
      return Promise.resolve()
    }
  }

  vi.stubGlobal('AudioContext', FakeContext as unknown as typeof AudioContext)
  return recorder
}

beforeEach(() => {
  vi.unstubAllGlobals()
  // The manager caches a context and latches a broken flag, so each test needs
  // a fresh one. Re-importing is the only way to reset a module singleton.
  vi.resetModules()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function freshSound() {
  vi.resetModules()
  return await import('./sound')
}

describe('sound is off until asked for', () => {
  it('plays nothing while disabled', async () => {
    const recorder = stubAudio()
    const mod = await freshSound()
    mod.sound.configure({ enabled: false, volume: 1 })
    expect(mod.sound.play('command.success')).toBe(false)
    expect(recorder.started).toBe(0)
  })

  it('plays nothing at zero volume, even when enabled', async () => {
    const recorder = stubAudio()
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 0 })
    expect(mod.sound.play('command.success')).toBe(false)
    expect(recorder.started).toBe(0)
  })

  it('plays once enabled', async () => {
    const recorder = stubAudio()
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 0.5 })
    expect(mod.sound.play('command.success')).toBe(true)
    expect(recorder.started).toBeGreaterThan(0)
  })
})

describe('failure is silence, never an exception', () => {
  it('survives a browser with no Web Audio at all', async () => {
    vi.stubGlobal('AudioContext', undefined)
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 1 })
    expect(() => mod.playSound('error')).not.toThrow()
    expect(mod.sound.play('error')).toBe(false)
  })

  it('survives a constructor that throws', async () => {
    vi.stubGlobal(
      'AudioContext',
      class {
        constructor() {
          throw new Error('no audio device')
        }
      } as unknown as typeof AudioContext,
    )
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 1 })
    expect(() => mod.playSound('research.complete')).not.toThrow()
    expect(mod.sound.play('research.complete')).toBe(false)
  })

  it('survives an oscillator that throws mid-play', async () => {
    vi.stubGlobal(
      'AudioContext',
      class {
        state = 'running'
        currentTime = 0
        destination = {}
        createGain() {
          return {
            gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} },
            connect() {},
          }
        }
        createOscillator(): never {
          throw new Error('exhausted')
        }
        resume() {
          return Promise.resolve()
        }
      } as unknown as typeof AudioContext,
    )
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 1 })
    expect(() => mod.playSound('panel.open')).not.toThrow()
  })

  it('stops retrying once the audio stack is known broken', async () => {
    let attempts = 0
    vi.stubGlobal(
      'AudioContext',
      class {
        constructor() {
          attempts += 1
          throw new Error('nope')
        }
      } as unknown as typeof AudioContext,
    )
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 1 })
    for (let i = 0; i < 20; i += 1) mod.playSound('panel.open')
    // One attempt, not twenty. A broken audio stack must not cost a
    // constructor call per interaction for the rest of the session.
    expect(attempts).toBe(1)
    expect(mod.sound.isEnabled).toBe(false)
  })

  it('survives a resume that rejects, which is the autoplay policy', async () => {
    vi.stubGlobal(
      'AudioContext',
      class {
        state = 'suspended'
        currentTime = 0
        destination = {}
        createGain() {
          return {
            gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} },
            connect() {},
          }
        }
        createOscillator() {
          return {
            frequency: { setValueAtTime() {} },
            type: 'sine',
            connect() {},
            start() {},
            stop() {},
          }
        }
        resume() {
          return Promise.reject(new Error('gesture required'))
        }
      } as unknown as typeof AudioContext,
    )
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 1 })
    expect(() => mod.playSound('workspace.save')).not.toThrow()
  })
})

describe('the sounds themselves', () => {
  it('has one voice for every event it exports', async () => {
    const recorder = stubAudio()
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 0.5 })
    for (const event of mod.SOUND_EVENTS) {
      expect(mod.sound.play(event)).toBe(true)
      // Coalescing is per event, so a different event always plays.
      recorder.started = 0
    }
  })

  it('coalesces a repeat of the same event', async () => {
    const recorder = stubAudio()
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 0.5 })
    expect(mod.sound.play('panel.open')).toBe(true)
    // A list that opens eight panels at once is one event, not a chord.
    expect(mod.sound.play('panel.open')).toBe(false)
    expect(recorder.created).toBe(1)
  })

  it('never exceeds the quiet ceiling, whatever the volume says', async () => {
    const recorder = stubAudio()
    const mod = await freshSound()
    mod.sound.configure({ enabled: true, volume: 1 })
    mod.sound.play('validation.complete')
    // A slider at 100% is still interface feedback, not a notification chime.
    expect(recorder.outputGains.length).toBeGreaterThan(0)
    expect(Math.max(...recorder.outputGains)).toBeLessThanOrEqual(0.15)
    // And the pitches are audible interface tones rather than a siren.
    expect(Math.max(...recorder.frequencies)).toBeLessThan(2000)
  })

  it('does not offer a sound for hover, click or keystroke', () => {
    // The events that make an application exhausting, and the fastest way to
    // get the whole system turned off — taking the useful four with it.
    for (const banned of ['hover', 'click', 'keypress', 'keystroke', 'tick', 'scroll']) {
      expect(SOUND_EVENTS.some((event) => event.includes(banned))).toBe(false)
    }
  })

  it('exports a play function that is safe with no audio globals at all', () => {
    vi.stubGlobal('AudioContext', undefined)
    sound.configure({ enabled: true, volume: 1 })
    expect(() => playSound('error')).not.toThrow()
  })
})

/* Interface sound. Off by default, synthesised, and unable to break anything.
 *
 * ── why synthesis ──────────────────────────────────────────────────────────
 * No files. Nothing is downloaded, nothing is licensed, nothing ships as an
 * asset, and every sound is a few lines of arithmetic that can be read and
 * argued with. A sample would also be a fixed timbre; these are shaped from the
 * same two-oscillator recipe, which is what makes six events sound like one
 * instrument rather than six unrelated noises.
 *
 * ── what it sounds like ────────────────────────────────────────────────────
 * Short (60-180ms), quiet, and built on a sine with a soft attack and an
 * exponential release. No square waves, no noise bursts, no pitch sweeps: those
 * read as a videogame. The pitches are drawn from one minor-sixth interval set
 * so two events firing close together do not clash.
 *
 * ── what it does not do ────────────────────────────────────────────────────
 * There is no sound for hover, for an ordinary click, for a keystroke, or for
 * chart movement. Those are the sounds that make an application exhausting, and
 * the fastest way to get an operator to turn the whole system off — taking the
 * four events that were worth hearing with it.
 *
 * ── failure ────────────────────────────────────────────────────────────────
 * Every path is wrapped. A browser that has no Web Audio, a context the user
 * has not gestured into yet, a device with no output — all of them are silence,
 * never an exception. Sound is the least important thing in a research
 * workstation and must never be able to interrupt the most important.
 */

export type SoundEvent =
  | 'panel.open'
  | 'panel.close'
  | 'workspace.switch'
  | 'workspace.save'
  | 'command.success'
  | 'research.complete'
  | 'validation.complete'
  | 'error'

type Voice = {
  /** Fundamental, in Hz. */
  hz: number
  /** Second oscillator as a ratio of the fundamental. Silent at 0. */
  ratio: number
  /** Seconds. Nothing here is longer than a fifth of a second. */
  length: number
  /** Relative loudness before the master volume. */
  gain: number
}

/* Pitches are a minor sixth apart (about 1.6) or an octave, so any two of these
 * landing together are consonant. Completion events sit higher than the actions
 * that start them, which is the one piece of meaning the pitch carries. */
const VOICES: Record<SoundEvent, Voice> = {
  'panel.open': { hz: 520, ratio: 0, length: 0.07, gain: 0.5 },
  'panel.close': { hz: 390, ratio: 0, length: 0.07, gain: 0.45 },
  'workspace.switch': { hz: 440, ratio: 1.5, length: 0.09, gain: 0.5 },
  'workspace.save': { hz: 660, ratio: 1.5, length: 0.12, gain: 0.55 },
  'command.success': { hz: 700, ratio: 2, length: 0.09, gain: 0.5 },
  'research.complete': { hz: 587, ratio: 1.5, length: 0.16, gain: 0.6 },
  'validation.complete': { hz: 784, ratio: 1.5, length: 0.18, gain: 0.6 },
  // The only descending one. An error should not be louder than a success —
  // it should be lower, which is heard as different without being an alarm.
  error: { hz: 233, ratio: 0.75, length: 0.14, gain: 0.55 },
}

/* Nothing above this reaches the output, whatever the volume setting says. A
 * slider at 100% is still an interface sound, not a notification chime. */
const CEILING = 0.14

/* Two of the same event inside this window is one event. Without it, a list
 * that opens eight panels at once is a chord nobody asked for. */
const COALESCE_MS = 60

class SoundManager {
  private context: AudioContext | null = null
  private enabled = false
  private volume = 0.35
  private lastPlayed = new Map<SoundEvent, number>()
  /** Set once anything throws, so a broken audio stack is not retried per click. */
  private broken = false

  configure(options: { enabled: boolean; volume: number }): void {
    this.enabled = options.enabled
    this.volume = Math.min(1, Math.max(0, options.volume))
    if (!this.enabled) this.suspend()
  }

  get isEnabled(): boolean {
    return this.enabled && !this.broken
  }

  private ensureContext(): AudioContext | null {
    if (this.broken) return null
    if (this.context) return this.context
    try {
      const Ctor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!Ctor) {
        this.broken = true
        return null
      }
      this.context = new Ctor()
      return this.context
    } catch {
      // No Web Audio, or the browser refused to create a context. Silence from
      // here on; never an exception into a caller that was doing real work.
      this.broken = true
      return null
    }
  }

  /** Play one event. Always safe: returns false rather than throwing. */
  play(event: SoundEvent): boolean {
    if (!this.enabled || this.broken || this.volume <= 0) return false
    const voice = VOICES[event]
    if (!voice) return false

    const now = Date.now()
    const previous = this.lastPlayed.get(event) ?? 0
    if (now - previous < COALESCE_MS) return false
    this.lastPlayed.set(event, now)

    try {
      const context = this.ensureContext()
      if (!context) return false
      // Autoplay policy: a context created before a user gesture starts
      // suspended. Resuming is a promise that can reject, and a rejection here
      // is not an error the application should ever see.
      if (context.state === 'suspended') void context.resume().catch(() => undefined)

      const start = context.currentTime
      const end = start + voice.length
      const master = context.createGain()
      const peak = Math.min(CEILING, CEILING * this.volume * voice.gain * 2)
      // A ramp rather than a step: a gain that jumps produces a click, which is
      // the single thing that makes synthesised UI sound cheap.
      master.gain.setValueAtTime(0.0001, start)
      master.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), start + 0.012)
      master.gain.exponentialRampToValueAtTime(0.0001, end)
      master.connect(context.destination)

      const oscillators: OscillatorNode[] = []
      const fundamental = context.createOscillator()
      fundamental.type = 'sine'
      fundamental.frequency.setValueAtTime(voice.hz, start)
      fundamental.connect(master)
      oscillators.push(fundamental)

      if (voice.ratio > 0) {
        const overtone = context.createOscillator()
        overtone.type = 'sine'
        overtone.frequency.setValueAtTime(voice.hz * voice.ratio, start)
        const overtoneGain = context.createGain()
        // Quiet enough to be timbre rather than a second note.
        overtoneGain.gain.setValueAtTime(0.32, start)
        overtone.connect(overtoneGain)
        overtoneGain.connect(master)
        oscillators.push(overtone)
      }

      for (const oscillator of oscillators) {
        oscillator.start(start)
        oscillator.stop(end + 0.02)
      }
      return true
    } catch {
      this.broken = true
      return false
    }
  }

  /** Release the audio device when sound is turned off. */
  suspend(): void {
    try {
      void this.context?.suspend()
    } catch {
      /* Nothing to do, and nothing worth telling anyone. */
    }
  }
}

export const sound = new SoundManager()

/** Play an event. The function every component calls; it can never throw. */
export function playSound(event: SoundEvent): void {
  try {
    sound.play(event)
  } catch {
    /* Belt and braces. A sound must not be able to break a research session. */
  }
}

export const SOUND_EVENTS = Object.keys(VOICES) as SoundEvent[]

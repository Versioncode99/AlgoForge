import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Palette, Volume2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { playSound, sound, SOUND_EVENTS } from '../sound'
import {
  type Appearance,
  type AppearanceOptions,
  applyAppearance,
  loadAppearance,
  saveAppearance,
} from '../theme'

/* Settings → Appearance.
 *
 * Two things about how this behaves are deliberate.
 *
 * **It previews before it saves.** Every control applies the change to the
 * document immediately and sends the patch afterwards. Choosing a theme from a
 * dropdown and then finding out what it looks like is a worse way to choose a
 * theme than seeing it, and there is nothing destructive to guard against — the
 * previous value comes back if the save fails.
 *
 * **Sound is off until it is turned on, and turning it on plays one.** A
 * volume slider with nothing to hear is a slider you set by guessing. Moving it
 * plays the quietest event in the set, once, so the number means something.
 */

type Payload = Appearance & { options: AppearanceOptions }

function Choice({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string
  value: string
  options: { key: string; label: string; detail: string }[]
  onChange: (next: string) => void
  disabled?: boolean
}) {
  const chosen = options.find((item) => item.key === value)
  return (
    <label className="appearance-row">
      <span className="appearance-label">{label}</span>
      <select
        value={value}
        disabled={disabled}
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((item) => (
          <option key={item.key} value={item.key}>
            {item.label}
          </option>
        ))}
      </select>
      <small className="appearance-detail">{chosen?.detail ?? ''}</small>
    </label>
  )
}

export function AppearancePanel() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const appearance = useQuery({ queryKey: ['appearance'], queryFn: loadAppearance })
  // Held locally so the slider is smooth: a controlled input that waits for a
  // round trip per pixel is unusable.
  const [volume, setVolume] = useState<number | null>(null)

  const data = appearance.data
  useEffect(() => {
    if (data) sound.configure({ enabled: data.sound_enabled, volume: data.sound_volume })
  }, [data])

  const save = useMutation({
    mutationFn: (patch: Partial<Appearance>) => saveAppearance(patch),
    onSuccess: (next) => {
      setError(null)
      sound.configure({ enabled: next.sound_enabled, volume: next.sound_volume })
      qc.setQueryData(['appearance'], next)
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (err: Error, _patch, _context) => {
      setError(err.message)
      // Put the document back the way the server still believes it is, so a
      // rejected value cannot leave the interface in a state nothing recorded.
      if (data) applyAppearance(data)
      qc.invalidateQueries({ queryKey: ['appearance'] })
    },
  })

  if (!data) {
    return (
      <div className="panel">
        <header>
          <h2>Appearance</h2>
        </header>
        <div className="panel-body">
          <p className="sub">Loading…</p>
        </div>
      </div>
    )
  }

  /** Apply now, persist after. The preview is the point. */
  const change = (patch: Partial<Appearance>) => {
    applyAppearance({ ...data, ...patch })
    save.mutate(patch)
  }

  const current = { ...data, sound_volume: volume ?? data.sound_volume }

  return (
    <div className="panel">
      <header>
        <h2>Appearance</h2>
        <div className="panel-actions">
          <span className="sub">
            <Palette /> Applies immediately · stored with your operator settings
          </span>
        </div>
      </header>

      {error && <p className="warning bad">{error}</p>}

      <div className="panel-body appearance-grid">
        <Choice
          label="Theme"
          value={data.theme}
          options={data.options.themes}
          disabled={save.isPending}
          onChange={(theme) => change({ theme })}
        />
        <Choice
          label="Accent"
          value={data.accent}
          options={data.options.accents}
          disabled={save.isPending}
          onChange={(accent) => change({ accent })}
        />
        <Choice
          label="Density"
          value={data.density}
          options={data.options.densities}
          disabled={save.isPending}
          onChange={(density) => change({ density })}
        />
        <Choice
          label="Motion"
          value={data.motion}
          options={data.options.motions}
          disabled={save.isPending}
          onChange={(motion) => change({ motion })}
        />

        <div className="appearance-row">
          <span className="appearance-label">Sound</span>
          <button
            type="button"
            className={data.sound_enabled ? 'btn primary' : 'btn'}
            aria-pressed={data.sound_enabled}
            onClick={() => {
              const next = !data.sound_enabled
              sound.configure({ enabled: next, volume: data.sound_volume })
              // One sound on the way on, so "enabled" is audible rather than a
              // claim. Nothing on the way off, for obvious reasons.
              if (next) playSound('command.success')
              change({ sound_enabled: next })
            }}
          >
            <Volume2 /> {data.sound_enabled ? 'Enabled' : 'Disabled'}
          </button>
          <small className="appearance-detail">
            {SOUND_EVENTS.length} events, all under a fifth of a second. Nothing plays on hover,
            on an ordinary click, or on a keystroke.
          </small>
        </div>

        <div className="appearance-row">
          <span className="appearance-label">Volume</span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            aria-label="Sound volume"
            disabled={!data.sound_enabled}
            value={Math.round(current.sound_volume * 100)}
            onChange={(event) => {
              const next = Number(event.target.value) / 100
              setVolume(next)
              sound.configure({ enabled: data.sound_enabled, volume: next })
            }}
            onMouseUp={() => {
              if (volume === null) return
              playSound('panel.open')
              change({ sound_volume: volume })
              setVolume(null)
            }}
            onKeyUp={() => {
              if (volume === null) return
              playSound('panel.open')
              change({ sound_volume: volume })
              setVolume(null)
            }}
          />
          <small className="appearance-detail">
            {Math.round(current.sound_volume * 100)}% of an already quiet ceiling.
          </small>
        </div>
      </div>
    </div>
  )
}

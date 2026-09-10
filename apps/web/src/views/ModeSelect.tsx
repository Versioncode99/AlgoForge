import { useState } from 'react'
import { ArrowRight, LockKeyhole } from 'lucide-react'
import { Wordmark } from '../components/Logo'
import {
  STANCE_DETAIL,
  STANCE_LABEL,
  type ModeDescriptor,
  type ModeKey,
  type Stance,
  useEnterMode,
  useModes,
} from '../modes'

/* The opening screen: what are you here to do?
 *
 * Deliberately not a pricing page. There are no tiers, nothing is locked, and
 * every mode reaches the same judge and the same data — so the screen sells
 * nothing and explains four workflows instead. What each panel carries is the
 * information a person actually chooses on: what the environment is for, and
 * which sections it opens with.
 *
 * The Hedge Fund panel is the only one that asks a second question, because it
 * is the only mode where the answer changes what an agent may do without you.
 * It is asked here rather than after entry: choosing it inside the mode would
 * mean the mode opens on a stance nobody picked.
 */

const ORDER: ModeKey[] = ['normal', 'prop_firm', 'ai', 'hedge_fund']

export function ModeSelect() {
  const modes = useModes()
  const enter = useEnterMode()
  const [stance, setStance] = useState<Stance>('human_in_the_loop')
  const [pending, setPending] = useState<ModeKey | null>(null)

  const open = (mode: ModeDescriptor) => {
    setPending(mode.mode)
    enter.mutate(
      { mode: mode.mode, stance: mode.stances.length ? stance : null },
      { onSettled: () => setPending(null) },
    )
  }

  const byKey = new Map((modes.data?.modes ?? []).map((mode) => [mode.mode, mode]))
  const ordered = ORDER.map((key) => byKey.get(key)).filter(Boolean) as ModeDescriptor[]

  return (
    <div className="mode-select">
      <header className="mode-select-head">
        <Wordmark />
        <h1>Choose your workspace</h1>
        <p>
          One platform, four operating environments. Every one reaches the same data, the same
          strategies and the same judge — what changes is what the screen is arranged around, and
          how much an assistant may do on your behalf.
        </p>
      </header>

      {modes.isPending && (
        <p className="mode-select-state" role="status">
          Reading the workspace manifest…
        </p>
      )}
      {modes.isError && (
        <p className="mode-select-state is-error" role="alert">
          The API is not answering, so the workspaces cannot be listed. Nothing is lost — they
          reappear as soon as it does.
        </p>
      )}

      <div className="mode-grid">
        {ordered.map((mode, index) => (
          <ModePanel
            key={mode.mode}
            mode={mode}
            index={index + 1}
            stance={stance}
            onStance={setStance}
            busy={pending === mode.mode}
            disabled={enter.isPending}
            onOpen={() => open(mode)}
          />
        ))}
      </div>

      {enter.isError && (
        <p className="mode-select-state is-error" role="alert">
          {(enter.error as Error).message}
        </p>
      )}

      <footer className="mode-select-foot">
        <span>
          <LockKeyhole aria-hidden="true" /> Paper only
        </span>
        <span>
          No broker, venue or order-routing vendor is connected. Fills are modelled locally and
          labelled simulated wherever they appear.
        </span>
      </footer>
    </div>
  )
}

function ModePanel({
  mode,
  index,
  stance,
  onStance,
  busy,
  disabled,
  onOpen,
}: {
  mode: ModeDescriptor
  index: number
  stance: Stance
  onStance: (value: Stance) => void
  busy: boolean
  disabled: boolean
  onOpen: () => void
}) {
  // Groups rather than every section. Twelve labels is a table of contents; six
  // groups is a description of the environment, which is what a person is
  // choosing between.
  const groups: string[] = []
  for (const section of mode.sections) {
    if (!groups.includes(section.group)) groups.push(section.group)
  }

  return (
    <section className="mode-panel" data-mode={mode.mode}>
      <div className="mode-panel-body">
        <span className="mode-index">{String(index).padStart(2, '0')}</span>
        <h2>{mode.name}</h2>
        <p className="mode-tagline">{mode.tagline}</p>
        <p className="mode-purpose">{mode.purpose}</p>

        <ul className="mode-groups">
          {groups.map((group) => (
            <li key={group}>{group}</li>
          ))}
        </ul>

        {mode.stances.length > 0 && (
          <fieldset className="mode-stance">
            <legend>Operating stance</legend>
            <div className="mode-stance-choices" role="radiogroup" aria-label="Operating stance">
              {mode.stances.map((option) => (
                <label key={option} data-selected={stance === option ? 'yes' : undefined}>
                  <input
                    type="radio"
                    name="stance"
                    value={option}
                    checked={stance === option}
                    onChange={() => onStance(option)}
                  />
                  <span>{STANCE_LABEL[option]}</span>
                </label>
              ))}
            </div>
            <p>{STANCE_DETAIL[stance]}</p>
          </fieldset>
        )}

        {mode.limitations.length > 0 && (
          <ul className="mode-limits">
            {mode.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        )}
      </div>

      <button className="mode-open" onClick={onOpen} disabled={disabled}>
        <span>{busy ? 'Opening…' : `Open ${mode.name}`}</span>
        <ArrowRight aria-hidden="true" />
      </button>
    </section>
  )
}

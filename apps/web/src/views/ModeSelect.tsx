import { useState } from 'react'
import { ArrowRight, LockKeyhole } from 'lucide-react'
import { Wordmark } from '../components/Logo'
import {
  type ExpertiseLevel,
  type IntentDescriptor,
  readExpertise,
  useExpertise,
  useIntents,
  writeExpertise,
} from '../explain'
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
 *
 * Two bands sit around the grid.
 *
 * **What do you want to do?** Each option is a *route into the action
 * registry*, chosen from a closed list the API publishes — not a sentence
 * parsed by a model. Picking one enters the mode it belongs to and lands on the
 * section it names, and it shows its caveat before it does either: "no live
 * broker connector exists in this build" belongs at the front door, not three
 * screens in.
 *
 * **How much do you want to see?** Guided, Advanced and Quant over one engine.
 * A preference, not a permission: it changes what is on screen and nothing
 * about what the system will do, and no level hides a refusal.
 */

const ORDER: ModeKey[] = ['normal', 'prop_firm', 'ai', 'hedge_fund']

export function ModeSelect() {
  const modes = useModes()
  const enter = useEnterMode()
  const intents = useIntents()
  const expertise = useExpertise()
  const [stance, setStance] = useState<Stance>('human_in_the_loop')
  const [pending, setPending] = useState<ModeKey | null>(null)
  const [depth, setDepth] = useState<ExpertiseLevel>(() => readExpertise())
  const [intent, setIntent] = useState<IntentDescriptor | null>(null)

  const open = (mode: ModeDescriptor, route?: string) => {
    setPending(mode.mode)
    // The hash is set *before* entering, not in a success callback. Entering a
    // mode unmounts this screen, and React Query drops the callbacks passed to
    // `mutate` once the component that called it is gone — so a success handler
    // here never ran, and an intent silently landed wherever the hash already
    // pointed. Nothing reads the hash while this screen is up, and the shell's
    // own correction keeps a route the entered mode actually has.
    if (route) window.location.hash = route
    enter.mutate(
      { mode: mode.mode, stance: mode.stances.length ? stance : null },
      { onSettled: () => setPending(null) },
    )
  }

  const chooseDepth = (level: ExpertiseLevel) => {
    setDepth(level)
    writeExpertise(level)
  }

  const followIntent = (chosen: IntentDescriptor) => {
    // The first mode the intent has a route in, which is the manifest's own
    // order rather than a preference expressed here.
    const modeKey = chosen.modes[0] as ModeKey | undefined
    const target = modeKey ? byKeyFor(modes.data?.modes)?.get(modeKey) : undefined
    if (!target || !modeKey) return
    open(target, chosen.sections[modeKey]?.[0])
  }

  const ordered = ORDER.map((key) => byKeyFor(modes.data?.modes)?.get(key)).filter(
    Boolean,
  ) as ModeDescriptor[]
  const offered = intents.data?.intents ?? []
  const depths = expertise.data?.levels ?? []

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

      {/* Both bands render only once their declaration has arrived in the
        * shape they expect. An API answering with something else must not take
        * down the screen somebody uses to get in. */}
      {offered.length > 0 && (
        <section className="front-door" aria-label="What do you want to do?">
          <h2>What do you want to do?</h2>
          <ul>
            {offered.map((item) => (
              <li key={item.intent}>
                <button
                  type="button"
                  data-active={intent?.intent === item.intent ? 'yes' : undefined}
                  onClick={() => setIntent(intent?.intent === item.intent ? null : item)}
                >
                  {item.label}
                </button>
              </li>
            ))}
          </ul>
          {intent && (
            <div className="front-door-detail">
              <p>{intent.detail}</p>
              {intent.caveat && <p className="front-door-caveat">{intent.caveat}</p>}
              <p className="front-door-actions">
                Runs: {intent.actions.join(', ')}
              </p>
              <button
                type="button"
                className="front-door-go"
                disabled={enter.isPending}
                onClick={() => followIntent(intent)}
              >
                Start in <b>{modeName(modes.data?.modes, intent.modes[0])}</b>
                <ArrowRight aria-hidden="true" />
              </button>
            </div>
          )}
        </section>
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

      {depths.length > 0 && (
        <section className="depth-band" aria-label="How much do you want to see?">
          <h2>How much do you want to see?</h2>
          <p>
            The same engine and the same refusals at every depth. Nothing below is
            removed by choosing a shallower one — refusals, limitations, what was not
            measured and what your firm has not permitted appear at all three.
          </p>
          <ul>
            {depths.map((level) => (
              <li key={level.level}>
                <button
                  type="button"
                  data-active={depth === level.level ? 'yes' : undefined}
                  onClick={() => chooseDepth(level.level)}
                >
                  <b>{level.label}</b>
                  <span>{level.detail}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
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

/** A mode's own display name, from the manifest rather than from a transform.
 *
 * `prop_firm` title-cased is "Prop Firm" by luck; `ai` is "Ai", which is wrong.
 * The manifest already carries the name, so use it.
 */
function modeName(descriptors: ModeDescriptor[] | undefined, key: string | undefined) {
  if (!key) return ''
  return byKeyFor(descriptors).get(key as ModeKey)?.name ?? key.replace(/_/g, ' ')
}

function byKeyFor(descriptors: ModeDescriptor[] | undefined) {
  return new Map((descriptors ?? []).map((mode) => [mode.mode, mode]))
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

# Risk modes, controlled scaling and autonomous deployment

*Plan, written before the code. Companion to
`docs/2026-09-10-prop-desk-architecture.md`, which covers the execution fabric
and copy engine this layer sits on top of.*

## The question this layer answers

Phase 1 answered *where does an order go and what refuses it*. This one answers
*how large should it be, who decided that, and what was that decision allowed to
touch*.

The product statement is three sentences, and the architecture has to make each
one literally true rather than approximately true:

> **MANUAL** — I decide.
> **ADAPTIVE** — AlgoForge calculates.
> **AI RISK MANAGEMENT** — AlgoForge manages within my boundaries.

"Within my boundaries" is the load-bearing phrase. A boundary that an automated
adjuster can widen is not a boundary, so the boundaries live in a record the
adjuster cannot write, and the action that writes them is `protected` — which
`forge.modes.permissions` rule 2 denies to an AI actor in every mode and stance,
before mode or stance is even consulted.

## Where risk actually enters the existing system

There is already exactly one number that turns evidence into size:
`AllocationConstraints.risk_fraction` in `forge.propdesk.allocation`. The
allocator multiplies the account's buffer to its loss floor by that fraction,
divides by the strategy's bootstrapped p95 drawdown per contract, and takes the
smallest of that, the account's contract cap and its own ceiling.

So this layer does not invent a sizing path. **It produces `risk_fraction`, and
nothing else.** Everything downstream — contracts, feasibility, the eight-rung
desk funnel, the pre-trade gate — is unchanged and still authoritative. That
single integration point is what keeps the AI from being able to route around
anything: there is one number it can influence, its range is fixed by the
operator, and the conversion from that number to a live order passes through the
same deterministic machinery a manually-entered fraction does.

## 1. Risk modes (`propdesk/risk.py`)

```
RiskMode      MANUAL | ADAPTIVE | AI_MANAGED
```

- **MANUAL** — the operator sets `risk_fraction` and the contract ceiling
  directly. Nothing proposes a change. Prop rules and the desk funnel still
  apply: manual means *nobody adjusts it for you*, not *no limits*.
- **ADAPTIVE** — the operator sets an appetite (0–100) and boundaries. A
  deterministic function of measured state produces the fraction. No model, no
  inference, no language model anywhere in the path.
- **AI_MANAGED** — the same deterministic function runs, and an advisory layer
  may additionally *narrow* the result. It may never widen one.

`RiskBoundaries` is the operator's contract with the machine:

| field | meaning |
| --- | --- |
| `minimum_fraction` / `maximum_fraction` | the hard floor and ceiling. Nothing proposes outside them. |
| `max_step` | the largest single change. |
| `cooldown_minutes` | minimum spacing between changes. |
| `hysteresis` | a proposal closer than this to the current value does not fire at all. |
| `max_daily_change` | total absolute movement permitted in a day. |
| `max_contracts` | a blunt second ceiling that does not depend on any estimate. |
| `emergency_buffer_ratio` | below this share of the starting buffer, de-risking bypasses cooldown and step. |

Every one of these constrains *increases* strictly and *decreases* leniently.
That asymmetry is deliberate and is asserted by tests: **de-risking is never
rate-limited; risk increases always are.**

## 2. The appetite meter is not a contract-size dial (`propdesk/survival.py`)

A meter that maps position 52 to "2 contracts" is a slider with extra steps. The
appetite has to change *what question is being answered*, not scale an answer.

It does two things, both statistical:

1. **It selects how far into the tail the operator sizes against.** Conservative
   sizes against the 99th-percentile bootstrapped drawdown; aggressive against
   the 75th. On a strategy with a thin tail those are nearly the same number, and
   on a fat-tailed one they differ by a factor of several — so the meter's effect
   depends entirely on the strategy's measured drawdown distribution, which is
   the property that makes it statistical rather than cosmetic.
2. **It selects the share of the buffer at stake**, between the operator's own
   `minimum_fraction` and `maximum_fraction`.

The distribution comes from a stationary block bootstrap over the strategy's
out-of-sample per-contract daily PnL — the same method
`forge.prop.engine._block_bootstrap` already uses, so streaks survive resampling
rather than being averaged away by an i.i.d. draw.

**When the series is absent or too short, there is no band.** The result is
`Unmeasurable` with the reason, and the fraction falls to `minimum_fraction`.
It does not fall back to a default, an assumption, or a Gaussian.

## 3. Drivers can only cut (`propdesk/scaling.py`)

The band gives a target. Eight drivers then multiply it, each in `[0, 1]`:

`buffer`, `strategy_health`, `realised_volatility`, `regime_fit`,
`correlation`, `prop_constraint`, `news`, `evidence`.

Two rules make this safe and make §11's "no naive balance ↑ = risk ↑" true:

- **A driver's factor is at most 1.** No driver can raise the target. Risk rises
  only when the *band itself* rises — because the buffer grew, or the evidence
  improved — and then only as fast as the governor permits.
- **An increase requires every driver to be measured. A decrease requires
  none.** An unmeasured input is never a reason to take more risk, and never an
  obstacle to taking less.

Balance appears nowhere as a driver. It enters only through `buffer`, which is a
*constraint* — distance to the loss floor — and through the band, where a larger
buffer pays for more contracts at the same fraction. Equity growth alone, with a
degraded strategy or an unmeasured regime, moves nothing.

## 4. The governor (`propdesk/scaling.py`)

Between proposal and application sit, in order: hysteresis, boundaries, step,
cooldown, daily cap. Each records itself when it binds, because "I wanted 1.2%
and your step limit allowed 0.9%" is the most useful sentence the panel can
show and it is only available if the clamp is data.

Emergency de-risking is a separate path: when the buffer falls below
`emergency_buffer_ratio`, or the account rule engine reports a breach, a
*decrease* skips cooldown and step entirely. It still cannot go below zero and
it still cannot become an increase.

## 5. "Why did AlgoForge change my risk?" (`propdesk/scaling.py`)

Not a generated paragraph. `RiskProposal` carries the drivers that were
measured, each with its observed value and its effect, the driver that bound,
the governor clamp that applied, and the before/after contract counts computed
through the same allocator the desk uses. The sentence the screen shows is
assembled from those fields. There is no code path that produces an explanation
without a proposal to explain, and `test_no_rationale_exists_without_a_measured_driver`
asserts it.

## 6. AI permissions (`propdesk/risk.py`)

An explicit, closed set of what the advisory layer may touch, and a closed set of
what it may never touch. The second list is not aspirational prose: each entry
names the mechanism that enforces it, and a test asserts the mechanism exists.

| AI may never | enforced by |
| --- | --- |
| override prop-firm limits | `forge.prop.account.assess`, consulted in the desk's `account_rules` rung |
| override the operator's maximum risk | `RiskBoundaries.maximum_fraction`, applied after every proposal |
| bypass the pre-trade gate | `forge.execution.gate.screen`, the desk funnel's last rung |
| deploy an unvalidated strategy | `AutonomyEngine` mandatory gates + `forge.execution.lifecycle` |
| trade a blocked account | the desk's `compatibility` and `account_binding` rungs |
| bypass G0–G13 | `forge.judge` verdict required by the deployment gates |
| change a protected control | `forge.modes.permissions` rule 2 |

## 7. Autonomous deployment (`propdesk/autonomy.py`)

```
AutonomyLevel   OFF | APPROVAL_REQUIRED | FULLY_AUTONOMOUS
```

The mandatory gate list is identical at all three levels. The level changes only
*what happens when every gate passes*: OFF records the recommendation and stops;
APPROVAL_REQUIRED submits it to the existing
`forge.hedgefund.approvals.ApprovalQueue` — which already executes as the
operator, re-validates against present state, and expires rather than running
late; FULLY_AUTONOMOUS proceeds.

A failed gate blocks at every level. There is no level at which a gate is
skipped, and `test_full_autonomy_does_not_shorten_the_gate_list` asserts the two
lists are the same object.

Note the standing consequence: `forge.execution.lifecycle.LIVE_EXECUTION_AVAILABLE`
is `False`, and `check_transition` refuses `DEPLOYED` outright. Autonomous *live*
deployment is therefore not merely unimplemented in this build — it is refused by
a control this layer does not own and cannot modify.

## 8. Consent and disclosure (`propdesk/consent.py`)

Versioned disclosures with the operator's acknowledgement recorded against the
version they actually saw. Enabling AI risk management or autonomous deployment
without a current acknowledgement is refused, and a disclosure whose text
changes gets a new version, which invalidates the old acknowledgement.

The text states what the system does and what it cannot do. It makes no claim
about liability, and `test_no_disclosure_claims_legal_protection` asserts that.
Each disclosure carries the source of every factual claim it makes, so a claim
that stops being true fails a test rather than merely becoming false.

## 9. Audit (`propdesk/store.py`, extending the phase-1 append-only tables)

One row per consequential decision: operator, account, action, previous and new
state, the risk configuration, the strategy, its validation state, the policy
state, the automation mode, the reason, the timestamp, the disclosure version
acknowledged, the approval status, the system decision, and the resulting
execution state. Written through the existing append-only path, which has no
update and no delete.

Secrets do not appear. The credential abstraction from phase 1 makes that
structural: a `Secret` refuses to serialise, and `CredentialRecord` refuses a
public field named like one.

## 10. Progressive disclosure and explanation

- `forge/modes/expertise.py` — `GUIDED | ADVANCED | QUANT`, with a manifest of
  what each level surfaces, declared server-side for the same reason the mode
  manifest is: so an agent asked "what can I see" does not need a person.
- `forge/modes/intents.py` — the intent front door. Each intent maps to
  *registered actions*; nothing conversational, nothing invented.
- `forge/explain/metrics.py` — what a metric is, why it matters, what this value
  means here, and its caveats, keyed to the metrics AlgoForge actually computes.
- `forge/explain/passport.py` — the Strategy Passport, composed from the verdict,
  the explained verdict, validation, lineage, data provenance and deployment
  state. It composes; it computes nothing.
- `forge/explain/why.py` — a closed set of answerable questions, each answered
  from state that was passed in. A question with no state to answer it returns
  "not measured", never a plausible sentence.
- `forge/explain/preview.py` — §26's "check my work": what is about to run,
  stated before it runs.

## What this plan deliberately does not do

- It does not add a second risk engine. `forge.risk.limits` and
  `forge.risk.portfolio` are untouched and still bind.
- It does not give the advisory layer a numeric output that is trusted. It gives
  it a multiplier in `[0, 1]` on a number that is already bounded.
- It does not make any claim about live trading. Nothing here creates a
  connector, and §7 above records the control that refuses one.

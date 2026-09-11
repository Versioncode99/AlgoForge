# Prop Desk implementation progress

A running log, updated as work lands rather than written at the end. Each phase
records what changed, what was tested, what remains, and whether anything is
blocked.

Branch: `claude/wonderful-goldberg-twi9xv`. Not merged to `main`.

---

## Phase 0 — repository archaeology

**Status: complete.**

The point of this pass was to find what already exists, so the risk/automation
layer extends AlgoForge rather than growing a second one beside it. Findings,
with the claim verified against the code rather than against a previous
report:

### Deterministic controls that must not be weakened

| Control | Where | What it does |
| --- | --- | --- |
| Judge G0–G13 | `forge/judge/engine.py` | Fourteen gates; `PASS` / `FAIL` / `INCONCLUSIVE`. G0 data receipt, G1 preregistration, G2 implementation, G3 sample, G4 OOS expectancy, G5 deflated Sharpe, G6 robustness, G7 engine consistency, G8 risk, G9 mechanism, G10 tier, G11 PBO/CSCV, G12 walk-forward, G13 CPCV paths. |
| Pre-trade gate | `forge/execution/gate.py` | Screens every proposed order. Reached by the Prop Desk funnel's last rung. |
| Risk profiles | `forge/risk/limits.py` | `enabled=False` is a kill switch, not a preference. `unlimited` is not expressible. |
| Portfolio risk | `forge/risk/portfolio.py` | Exposure, leverage, concentration, tail. |
| Prop account rules | `forge/prop/account.py` | `assess()` — trailing floor, daily loss, consistency, session windows, custom limits. Three-valued: a rule that cannot be assessed reports `NOT_ASSESSED`. |
| Lifecycle | `forge/execution/lifecycle.py` | `DRAFT → … → APPROVED → PAPER → DEPLOYED`. No edge from `VALIDATED` straight to `PAPER`. `Authorization` has no defaults and no "system" actor. `DEPLOYED` is refused outright while no connector exists. |
| AI permissions | `forge/modes/permissions.py` | Pure function of (actor, mode, stance, action facts). Rule 2 denies every `protected` action to AI in every mode and stance. |
| Prop Desk funnel | `forge/propdesk/desk.py` | Eight recorded rungs ending at the pre-trade gate. |

### Infrastructure this phase will reuse rather than duplicate

- **`forge.hedgefund.approvals.ApprovalQueue`** — already implements exactly what
  §14's `APPROVAL REQUIRED` mode needs: durable requests, a TTL so a stale
  proposal expires rather than running late, execution *as the operator* at
  approval time, and no capability carried by the request itself. The autonomy
  layer routes through this; it does not grow a second queue.
- **`forge.hedgefund.audit.AuditLog`** — the §17 audit trail extends this.
- **`forge.modes`** — `Section`, `ModeDescriptor`, `PanelKind` and the
  `permissions.evaluate` policy. New screens are sections in the existing
  manifest; new AI boundaries are rules in the existing policy, not a parallel one.
- **`forge.judge.explain`** — already turns a verdict into ranked findings with
  "what would actually address it", and already refuses suggestions that game
  the test. §26/§27 explanation work builds on this rather than restating it.
- **`forge.propdesk`** (phase 1, shipped) — identity, credentials, instruments,
  orders, fabric, reconcile, copy, policy, allocation, news, desk, store.
- **`forge_api.actions`** — one registry, `protected` / `ActionRisk` already
  wired to the permission policy.

### Gaps this phase fills

1. No notion of a **risk mode**. Risk today is a static `RiskProfile` plus prop
   rules. There is no MANUAL / ADAPTIVE / AI-managed distinction and no appetite
   meter.
2. No **risk scaling** at all — nothing that proposes a change to per-trade risk
   from observed state, and therefore nothing that could oscillate or ratchet.
3. No **user-set boundaries** on what an automated adjuster may do.
4. No **explanation of a risk change**, because there are no risk changes.
5. **Autonomy is binary** — `Stance.AUTONOMOUS` exists only in Hedge Fund mode
   and only for order submission. There is no OFF / APPROVAL / AUTONOMOUS
   control over *deployment*, and no mandatory gate list attached to one.
6. No **consent or disclosure ledger**. Nothing records that a person was shown
   a warning, which version, and acknowledged it.
7. No **expertise level**. Every screen shows every control to everybody.
8. No **metric glossary** derived from the code that computes the metrics.
9. No **strategy passport** — the evidence exists across five stores and is
   never composed into one document.

### Verified claims that were worth checking

- `LIVE_EXECUTION_AVAILABLE` is `False` and `check_transition` refuses
  `DEPLOYED` with a reason naming the missing connector. Live deployment is not
  merely unimplemented; it is actively refused.
- `permissions.evaluate` rule 2 (`protected`) fires before the mode and stance
  are consulted, so "AI cannot raise its own limits" is structural and not a
  list that could be forgotten.
- `ApprovalQueue.approve` executes through the registry as the operator, so an
  approved action re-validates against present state.

---

## Phase 1 — execution fabric and copy engine

**Status: complete, merged to the branch** (commit `771d1bf`, PR #5).

See the PR body and `docs/2026-09-10-prop-desk-architecture.md`. 417 new tests;
full suite 2029 passing; ruff, mypy and tsc clean.

---

## Phase 2 — risk modes, controlled scaling, autonomy and consent

**Status: domain, explanation and API layers complete. UI still to come.**

Plan: `docs/2026-09-11-risk-autonomy-architecture.md`, written before the code.

### What changed

| file | what it holds |
| --- | --- |
| `packages/forge/propdesk/risk.py` | `RiskMode` (MANUAL / ADAPTIVE / AI_MANAGED), `RiskBoundaries`, `ManualRisk`, `RiskSettings`, `AiCapability`, and `PROHIBITIONS` — each prohibition paired with the module that enforces it. |
| `packages/forge/propdesk/survival.py` | The statistical derivation behind the appetite meter: a stationary block bootstrap over out-of-sample per-contract daily PnL, quantiles of the worst drawdown per 21-day horizon, and a band. `Unmeasurable` when the series is absent or too short. |
| `packages/forge/propdesk/scaling.py` | Eight drivers, each `[0, 1]`; the governor (hysteresis, boundaries, step, cooldown, daily cap, emergency); `RiskProposal`; `explain`. |
| `packages/forge/propdesk/autonomy.py` | `AutonomyLevel`, thirteen `MANDATORY_GATES`, `DeploymentFacts`, `evaluate`. |
| `packages/forge/propdesk/consent.py` | Four versioned disclosures, acknowledgements, `require`. |
| `packages/forge/propdesk/audit.py` | `ConsequentialRecord` with every §17 field, and a validator that scrubs secret-shaped keys on the way in. |
| `packages/forge/propdesk/store.py` | Six new tables — three config, three append-only. `SCHEMA_VERSION` 2. |
| `packages/forge/propdesk/__init__.py` | Re-exports, with `Acknowledgement` aliased to `DisclosureAcknowledgement` to avoid the fabric's own. |

### The three properties this phase exists to make true

1. **A driver can only cut.** Every driver's effect is in `[0, 1]`, so risk never
   rises *because* something fired. It rises only when the measured band rises.
   `test_every_driver_effect_is_at_most_one` samples the states the drivers
   branch on and asserts it.
2. **An increase needs every driver measured; a decrease needs none.** An
   unmeasured input is never a reason to take more risk and never an obstacle to
   taking less. Two tests, one each way.
3. **De-risking is never delayed.** Cooldown and the daily cap apply to
   increases only; an emergency decrease skips the step limit as well.
   `test_a_decrease_inside_the_cooldown_is_not_held` is the one to read.

Balance is not a driver. It enters only through the buffer (a constraint) and
through the band (where a bigger buffer pays for more contracts at the *same*
fraction). `test_balance_alone_is_not_a_driver` holds §11's headline refusal.

### What was tested

- `tests/propdesk/test_risk_modes.py` — 22 tests, including two that import
  every `enforced_by` in `PROHIBITIONS` and assert the module and attribute
  exist, so the prohibition list cannot become aspirational.
- `tests/propdesk/test_survival.py` — 16 tests.
- `tests/propdesk/test_risk_scaling.py` — 47 tests.
- `tests/propdesk/test_consent.py` — 20 tests, including
  `test_no_disclosure_claims_legal_protection` and one that checks each
  disclosure's claims are backed by a listed prohibition.
- `tests/propdesk/test_autonomy.py` — 24 tests.
- `tests/propdesk/test_desk_store.py` — 10 more, including one that reads the
  SQLite file as bytes and asserts no secret-shaped value survived.

`ruff` and `mypy --strict` clean across 170 source files.

### What remains in phase 2

The Prop Desk UI panels (§19–§22); visual verification (§35); the security audit
(§37); the final report (§39).

### Blocked

Nothing.

---

## Phase 3 — explanation and progressive disclosure

**Status: complete** (commit `b75b92d`).

`forge/explain/` — `metrics` (twelve entries, each naming the function that
computes it and reading its threshold from the module that enforces it), `why`
(seven closed questions, each declining when it has no state), `preview` (what
an experiment is about to do, plus data availability), `passport` (the evidence
for a strategy, composed from five stores, at three depths).

`forge/modes/expertise.py` — Guided / Advanced / Quant, with `ALWAYS_VISIBLE`
naming what appears at every level: refusals, limitations, unmeasured gates, the
simulation notice, firm permissions and the disclosures.

`forge/modes/intents.py` — the intent front door, mapped onto registered
actions. Writing the test found two real bugs: two intents routed a Normal-mode
operator to a section only AI and Hedge Fund have. Sections are keyed by mode now.

98 tests. Nothing blocked.

---

## Phase 4 — the API surface

**Status: complete.**

### What changed

- `apps/api/forge_api/propdesk.py` — the risk, autonomy, consent, audit and
  "why" service methods, plus ten new routes. 32 `/api/v1/propdesk` paths.
- `apps/api/forge_api/actions.py` — ten new registered actions. Six of them
  write and are `protected`; two evaluate and are `mutating` (they append to the
  record, the same treatment `propdesk_reconcile` already gets); four read.
- `apps/api/forge_api/control.py` — `desk_evidence`, one supplier that reads a
  strategy's spec, its out-of-sample runs and its validation evidence. The
  out-of-sample daily series is aggregated from the trades of out-of-sample runs
  only, by the day each closed: an in-sample run's trades must never reach the
  drawdown bootstrap, since the whole point of sizing against a bootstrapped
  drawdown is that the drawdown was not fitted.
- `tests/api/test_mcp_server.py` — four new reads in the expected tool set.

### Two bugs the tests found

1. **Consent was checked after validation.** `RiskSettings` requires a
   disclosure version for AI_MANAGED, so validating first produced "a version is
   missing" — true, and useless — instead of "this disclosure has not been
   acknowledged", which is what the operator has to act on. The service now
   resolves the acknowledgement and injects the version before validating.
2. **A recorded proposal could not be read back.** The store held `as_dict()`,
   whose derived keys `FrozenModel` refuses on the way in, so
   `why_did_risk_change` could not validate its own record. The row now holds
   `model_dump`; rendering happens on the way out.

### What was tested

`tests/api/test_propdesk_api.py` — 21 more tests, including one that takes each
of the four risk-writing actions through `forge.modes.permissions.evaluate` for
every mode and stance and asserts DENY.

Full `tests/api`, `tests/propdesk`, `tests/modes` and `tests/explain` green;
ruff and mypy clean across 177 files.

### Blocked

Nothing.

---

## Phase 5 — the Prop Desk UI, and running it

**Status: complete.**

### What changed

- `apps/web/src/propdesk.ts` — risk, autonomy, consent, audit and deployment
  types; `useRisk`, `useDisclosures`, `useDeskAudit`; five new mutations.
- `apps/web/src/views/PropDesk.tsx` — `RiskSection` and `AiSection`, plus
  `DisclosureDialog`, `AppetiteMeter` and `DriverTable`.
- `apps/web/src/components/PanelBody.tsx` — `desk_risk` and `desk_ai` panels.
- `apps/web/src/styles/propdesk.css`, `apps/web/src/App.tsx`,
  `packages/forge/workstation/models.py`, `packages/forge/modes/models.py`.
- `apps/web/src/views/propdesk-risk.test.tsx` — 11 tests.

### Visual verification (§35)

The application was built and run: API on 8123, Vite on 5173, against a seeded
workspace with two simulated accounts, a 50k rule set, a recorded firm policy
and a declared Rithmic connection. Every Prop Desk section was opened in
Chromium, screenshotted full-page, and its rendered text read back. The
disclosure flow was driven end to end — choose AI risk management, read the
warning, tick the statement, confirm — and the acknowledgement, the version and
the audit rows were then read back out of the running API.

**Running it found four bugs that the fixture-backed tests did not.**

1. **The Risk screen crashed.** `Cannot read properties of undefined (reading
   'map')`. A stored proposal round-trips through the database as the canonical
   model, and the screen reads derived keys — each driver's label, whether it
   cut, the assembled explanation — which the row deliberately does not hold.
   Fixed by rendering on the way out (`_rendered`), with a regression test; the
   screen now also tolerates a row an older schema wrote.
2. **The rules driver knew three level names, and the rule engine has five.**
   `forge.prop.account.Level` is `ok / caution / warning / breach /
   not_assessed`; the driver's map used invented `OK / WATCH / BREACH`, so a
   *caution* or *warning* account read as **unmeasured** rather than as a reason
   to cut. Now keyed to the enum itself, with a test that iterates every member.
3. **A sparse rule set locked risk at the minimum forever.**
   `AccountAssessment.level` is the worst over *every* rule and `NOT_ASSESSED`
   sorts above `OK` there — correct for the account screen, wrong here. A rule
   set with a maximum loss and a daily loss limit leaves seven optional rules
   reporting "not configured", so the worst level was permanently
   `NOT_ASSESSED`, the driver permanently unmeasured, and risk could never rise.
   The service now reads the worst level among rules that were *configured*, and
   still treats a configured-but-uncheckable rule as unknown. Four tests.
4. **Two heading and layout slips** — the mode promise rendered upper-case in a
   heading meta slot, and the appetite number floated without its label.

### Security audit (§37)

- Every desk API response scanned for secret-shaped keys: eleven endpoints,
  clean.
- The running SQLite file scanned across all 19 tables for
  password/secret/api-key/token/passphrase patterns: zero matches.
- API and Vite logs: zero matches.
- Git history across the whole branch for `sk-…`, `AKIA…` and PEM private-key
  headers: zero matches.
- Two new tests in `tests/propdesk/test_credentials.py` plant five secret-shaped
  keys in an audit record, write it, and read the database back **as bytes** —
  the only check a post-hoc redaction cannot satisfy.

### What was tested

Full suite: **2292 passed**. `ruff` and `mypy --strict` (177 files) clean.
`tsc --noEmit` clean, `vitest` **79 passed**.

### Blocked

Nothing.

---

## Phase 6 — the front door, the explanation API, and the end-to-end funnel

**Status: complete.**

### What changed

- Six HTTP routes over the explanation layer: `/modes/expertise`,
  `/modes/intents`, `/explain/metrics`, `/explain/metrics/{key}`,
  `/explain/questions`, `/explain/passport/{id}`.
- `apps/api/forge_api/dossier.py` — `latest_verdict` extracted from
  `build_dossier`, so the Strategy Passport reads the verdict the Evidence
  screen shows rather than computing a second one.
- `apps/web/src/explain.ts` and two bands on the opening screen: "What do you
  want to do?" and "How much do you want to see?".
- `tests/propdesk/test_funnel.py` — twelve end-to-end tests.
- `tests/api/test_explain_api.py` — 23 tests.
- `apps/web/src/views/frontdoor.test.tsx` — 6 tests.
- `evaluate_risk` gained an optional `strategy_id`, so an account not yet
  running anything can still be shown what a candidate strategy would cost.

### Three more bugs, two found by driving the running application

1. **The front door navigated nowhere.** React Query drops the callbacks passed
   to `mutate` once the calling component unmounts, and entering a mode unmounts
   the opening screen. Setting the hash first exposed a second cause: the
   shell's correction effect compares the hash against `sections`, which on the
   opening screen still describes whichever mode the session last carried — so a
   route chosen for the mode about to be entered was found unknown and rewritten,
   every time. It now waits for a mode to actually be entered.
2. **An unjudged strategy's passport had no "what did not hold" section.** The
   four verdict-derived sections were built only when a verdict existed — the
   exact shape the module exists to avoid.
3. **A chicken-and-egg in the risk band**, found by the funnel test: the band
   needs a strategy, which came only from the allocation, which does not exist
   before the first allocation. The caller can now name one.

### What was tested

Full suite **2330 passed**; 718 of those are new in this work. `ruff` and
`mypy --strict` clean across 177 files. `tsc --noEmit` clean, `vitest` **86
passed**.

### Blocked

Nothing.

---

## Final state

| | |
| --- | --- |
| Branch | `claude/wonderful-goldberg-twi9xv`, not merged to `main` |
| Commits | 7 |
| Files changed | 85 |
| Python tests | 2330 passed |
| Frontend tests | 86 passed |
| New tests | 718 |
| Prop Desk routes / actions | 32 / 34 |
| Explanation routes | 6 |
| Live broker connectors | **0** — and refused by `forge.execution.lifecycle` |

The full report is `docs/PROP_DESK_IMPLEMENTATION_REPORT.md`.

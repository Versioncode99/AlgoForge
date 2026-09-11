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

**Status: domain layer complete. API, UI and explanation layers still to come.**

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

API routes and registered actions; the explanation layer (§24–§32); the Prop
Desk UI panels (§19–§22); visual verification (§35); the security audit (§37).

### Blocked

Nothing.

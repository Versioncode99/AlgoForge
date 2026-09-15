# Horizon — verification report

Every claim below is labelled **VERIFIED**, **PARTIAL**, **BLOCKED** or **NOT
TESTED**, and each carries the command or the file that supports it. Where a
thing could not be checked in this environment, the label says so rather than
the claim being softened.

Branch `Horizon`, from `origin/main` at `4e2025b`. Eight commits,
138 files, +17,215 / −3,193.

---

## 1 · The short version

Horizon removed a mode chooser that decided two unrelated things at once, wired
a Rithmic connector that can read and cannot trade, made a rules directory that
had never been readable into configuration, and ran an accessibility audit that
found real violations. It also deleted a good deal of code that looked finished
and was not.

The thing worth reading is §11: the audit found **eight** things that presented
as working and were not. Six predate this branch; two were introduced by it, and
one of those is a real defect in the Rithmic guard that a vacuous assertion had
been hiding.

---

## 2 · Phases

| Phase | State | Evidence |
|---|---|---|
| 0 — audit and branch | **VERIFIED** | `docs/HORIZON_AUDIT.md`, measured before any edit |
| 1 — one product hierarchy | **VERIFIED** | `forge.product.navigation`, `authority.spec.ts` |
| 2 — Chat redesign | **VERIFIED** | `views/Chat.tsx`, `chat.test.tsx`, `chat.spec.ts` |
| 3 — Chat performance | **VERIFIED** | `docs/HORIZON_CHAT_PERFORMANCE.md` + raw JSON |
| 4 — Campaign discoverability | **VERIFIED** | manifest sweep in `workstation.spec.ts`; delete wired in §11 |
| 5 — research time scope | **VERIFIED** | `engine.py::_bars_for`, `test_timescope.py` |
| 6 — model routing | **VERIFIED** | `model_choice.choose`, `test_model_routing_consumers.py` |
| 7 — anti-slop + accessibility | **VERIFIED** | `accessibility.spec.ts`, `contrast.test.ts`, `styles.test.ts` |
| 8 — Rithmic foundation | **PARTIAL** — see §7 | `forge.propdesk.rithmic`, 4 test files |
| 9 — generic prop desk | **VERIFIED** | `forge.prop.catalogue`, `docs/HORIZON_PROPDESK_ROADMAP.md` |
| 10 — full verification | **VERIFIED** | this document, §3 |

---

## 3 · The verification matrix

Run on this checkout, Linux x86-64, Python 3.13.12, Node 22.

| Check | Command | Result |
|---|---|---|
| Python lint | `ruff check apps packages tests` | **VERIFIED** — clean |
| Python types | `mypy packages apps/api` (strict) | **VERIFIED** — 225 files, clean |
| Python tests | `pytest -q` | **VERIFIED** — exit 0 |
| Web types | `tsc --noEmit` | **VERIFIED** — clean |
| Web tests | `vitest run` | **VERIFIED** — 44 files, 523 tests |
| Production build | `vite build` | **VERIFIED** — 242.85 kB CSS (38.33 kB gzip) |
| Browser suite | `playwright test` | **VERIFIED** — 58 passed, 7 skipped |
| Accessibility | `playwright test accessibility.spec.ts` | **VERIFIED** — 9 destinations, WCAG 2.1 AA, no allow-list |
| Desktop widths | 1280 / 1440 / 1920 / 2560 | **VERIFIED** — no horizontal overflow |
| Narrow widths | 1024 / 768 / 480 | **VERIFIED** — no horizontal overflow |
| Secret scan | diff scan, §9 | **VERIFIED** — nothing found |
| CI, Linux | GitHub Actions | **VERIFIED** — green |
| CI, Windows | GitHub Actions | **PARTIAL** — one failure found and fixed; re-run in flight at the time of writing |
| Electron suite | `vitest` includes `apps/desktop/ipc-contract.test.js` | **PARTIAL** — the IPC contract runs; no packaged app was launched |

**The seven skipped browser tests are named, and each has a counterpart that
runs.** They need market data this machine does not have. Each one skips with
the blocker stated, and beside each is a test that runs in exactly that case and
asserts the refusal is what appears — `the refusal is drawn instead of a
substitute series`, `a chart panel with no archive says so`, `a run with no
archive is refused with the reason, not started and failed`. There is no
configuration in which those files check nothing.

---

## 4 · What was removed, and what it cost

| | before | after |
|---|---|---|
| CSS shipped | 259.23 kB (40.99 kB gzip) | 242.85 kB (38.33 kB gzip) |
| Stylesheets | 23 | 23 — `modes.css` (441 lines) deleted, `horizon.css` added for the new shell |
| Client modules | — | `modes.ts` and `explain.ts` deleted, 279 lines |
| `--fg-3` contrast on a panel | 3.04:1 | 4.51:1 |
| `--tier-synthetic` contrast | 3.33:1 | 4.58:1 |
| axe violations across 9 destinations | 2 rules × 9 screens | 0 |

No backend capability was removed. `forge.explain`, `forge.modes.permissions`
and every `/modes/*` route remain and are used — only unused *client* code went.

---

## 5 · Permissions: the thing that must not have widened

Removing the visible AI mode must not widen what an assistant may do.

**VERIFIED.** `forge.product.authority` maps onto the untouched
`forge.modes.permissions.evaluate`. The default is unattended work without
unattended execution — the same answer the old default mode gave.
`authority.spec.ts` asserts it against the running API, and
`test_no_action_opens_a_prop_account_from_a_rule_file` holds the new boundary:
an assistant can read the rule catalogue and cannot open an account against one,
because that fixes the contract a desk holds an account to.

`set_authority` is `protected` in every configuration — the one control an
assistant must never operate is the one saying what an assistant may do.

---

## 6 · Temporal integrity and provenance

**VERIFIED.** The four tiers stay distinct and nothing is defaulted upward.

- A campaign's date range reaches the engine, and the *partitions travel with
  the bars* (`engine.py::_bars_for`). Handing a cycle scoped bars while it split
  the default window would have run the backtest on the wrong development
  partition and reported it against the scope — silently, on real data only.
- `HOLDOUT` is assigned only to runs that execute against the holdout slice,
  with `BURN_ONCE` and a split receipt.
- `test_synthetic_data_is_never_eligible`,
  `test_holdout_can_be_consumed_once_per_lineage` and
  `test_a_holdout_overlapping_the_train_window_is_refused` pin the invariants.
- `contrast.test.ts` now also holds that the four tiers are four distinct
  colours, after `SYNTHETIC` — the tier meaning "nobody traded this data" — was
  found unreadable at 3.33:1.

---

## 7 · Rithmic: what is true

**PARTIAL, and the partition is the point.**

**VERIFIED** — built and tested here:

- A WebSocket client on the standard library, exercised against a real loopback
  socket: fragmentation, control frames, a half-sent frame, a peer that
  vanishes, an implausible length prefix. It refuses plaintext `ws://` off the
  loopback and refuses to disable certificate verification.
- A vocabulary loader that reads template ids out of the operator's own compiled
  descriptors. Nothing is transcribed and nothing from the licensed archive is
  committed.
- A per-plant connection state machine, heartbeats, correlation, multi-part
  responses, sequence gaps, jittered reconnection, resubscription.
- The adapter reaches the production path: `adapter_for` serves it when the SDK
  is installed and a broker can resolve a password, and the refusing adapter
  otherwise.

**BLOCKED** — cannot be done in this environment and is not claimed:

- No capability is reported above `IMPLEMENTED_NOT_CONNECTED`. Nothing has
  connected to a Rithmic gateway. `VERIFIED_READ_ONLY_IN_RITHMIC_TEST` and
  `VERIFIED_ORDER_LIFECYCLE_IN_RITHMIC_TEST` are set nowhere in the codebase.
- `place`, `modify`, `cancel` and `flatten` refuse to send. A first order in
  Rithmic Test is a separate step a person confirms, naming the account,
  instrument, side, quantity, order type, price fields and how it will be
  cancelled.
- Rithmic Test requires a prior interactive login through R\|Trader to accept
  the digital agreements. Not available here, and not bypassable.

**No order was submitted, modified, cancelled or flattened in any environment.**

---

## 8 · The licence boundary

**VERIFIED.** `docs/ADR-0001-rithmic-transport.md` records the archives'
SHA-256s, the PDF/`change_log` version discrepancy, and the conclusion.

- No `.proto` file is committed.
- No generated `*_pb2.py` is committed; `.gitignore` carries the pattern, and
  `test_no_generated_module_is_written_into_the_repository` asserts the source
  tree holds none.
- `vendor/rithmic/` is ignored.
- The vocabulary tests use `.proto` files written for the test in the same
  shape, not copies of Rithmic's.

---

## 9 · Secrets

**VERIFIED.** The diff was scanned for credential-shaped assignments, for
`vendor/`, `_pb2`, `.proto` and archive paths, and for hard-coded gateway hosts.
Nothing found. The only credential-like strings in the tree are test fixtures
(`hunter2`, `not-a-real-password`) used to assert that a password is *not*
retained or logged — `test_the_password_is_not_retained_anywhere` and the
redaction tests.

No credential file was read, printed or imported. No Rithmic documentation was
copied into the repository, a commit message, or this report.

---

## 10 · Branch and repository hygiene

**VERIFIED.** No destructive git operation was run: no `gc`, no `maintenance`,
no pack deletion, no history rewrite. No branch was deleted.
`claude/algoforge-research-expansion-s1sznx` was compared by content, found
superseded, and **retained** — `docs/HORIZON_AUDIT.md` carries the table.

**One thing to flag.** The specification asked for a branch named exactly
`Horizon`; the session harness names `claude/vibrant-edison-j5ye3i`. Both refs
point at the same commit, and the pull request is from `Horizon`.

---

## 11 · The independent audit

A separate pass over the finished work, looking for things that present as
working and are not. Six findings. **Five predate this branch.**

| # | Finding | Age |
|---|---|---|
| 1 | The panel picker offered 23 of the 35 panel kinds. Twelve implemented panels — approvals, audit, pre-trade gate, portfolio and the desk's eight — could only appear if a template seeded one. | pre-existing |
| 2 | `DELETE /campaigns/{id}` existed, refused correctly while running, kept the research, and nothing in the product could reach it. | pre-existing |
| 3 | `rules/` shipped four rule files and nothing could read them; their field names had drifted from the model they were meant to produce. | pre-existing |
| 4 | `src/modes.ts` and `src/explain.ts` — 279 lines of client code for screens that do not exist. `modes.ts` was this branch's doing. | 1 of 2 new |
| 5 | `PATCH /conversations/{id}` existed and no control reached it: a thread's title is derived from its first message and could not be corrected. | pre-existing |
| 6 | "PAPER ONLY" lost the sentence explaining it when the chooser was removed. Two words alone read as a setting somebody could switch off. | **new, this branch** |
| 7 | Four assertions of the form `assert X or True`, which pass whatever X is. One asserted the *opposite* of the design and `or True` kept it quiet. | 3 pre-existing, 1 new |
| 8 | `RithmicAdapter._require_session` refused on `session.ready` alone, and a degraded plant leaves `ready` **true**. Its message named degraded plants in a branch that could only be reached when there were none — so a plant missing heartbeats produced a snapshot that reconciliation uses to *replace* local state, arriving complete. | **new, this branch** |

Each is fixed and tested. Finding 6 is the one worth dwelling on: it is a safety
claim that got quietly weaker, and it was introduced by this branch's own
simplification. The test diff shows it as an assertion deleted alongside the
screen it described — which is exactly how a removal takes something with it.

Four further sweeps found nothing:

- **Unconditional skips** — none. Every `pytest.skip` is conditional with a
  stated reason; no `xfail`; no `.skip` in vitest or Playwright.
- **Unfailable assertions** — none left. The four `or True` assertions are
  finding 7; nothing else in the suite can pass regardless of its subject.
- **Swallowed exceptions** — every `except: pass`/`continue` either records the
  problem, is a documented shutdown path, or skips one unreadable record out of
  a listing that counts them.
- **Inert UI controls** — no no-op handlers, no `href="#"`, no TODOs.
- **Over-claiming** — no capability is set to a `VERIFIED_*` state anywhere; the
  only matches for "Rithmic support/connected/live" are docstrings *warning
  against* the phrasing.

---

## 12 · What is not verified

Stated plainly, because a report that only lists successes is not a report.

- **No live Rithmic connection.** §7.
- **No order of any kind, in any environment.** §7.
- **Windows CI** had one failure — a test of mine that assumed a stdlib SSL
  context, where pip's vendored `truststore` replaces it. Fixed in `444d2b1`;
  the re-run had not finished when this was written.
- **No packaged Electron app was launched.** The IPC contract test runs; the
  desktop shell was not built or started.
- **Visual regression** is nine full-page screenshots at 1440×900 in
  `artifacts/qa/`, captured from the shipped manifest. There is no baseline to
  diff against, so they are evidence, not a test.
- **Chat performance** excludes the provider by design; the figures are local
  work only, and the method says so.
- **Reference-project classification** could not be produced. The two archives
  supplied were the Rithmic SDK — classified in the ADR with SHA-256s — and a
  snapshot of AlgoForge at the same baseline commit this branch started from.
  There were no third-party reference projects in this container to classify.

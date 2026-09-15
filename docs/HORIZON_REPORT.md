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
| Web tests | `vitest run` | **VERIFIED** — 44 files, 526 tests |
| Production build | `vite build` | **VERIFIED** — 242.85 kB CSS (38.33 kB gzip) |
| Browser suite | `playwright test` | **VERIFIED locally** — 59 passed, 7 skipped. CI does **not** run this suite: the `web` job runs typecheck, build and `vitest` only, so every browser claim here rests on a local run |
| Accessibility | `playwright test accessibility.spec.ts` | **VERIFIED** — 9 destinations, WCAG 2.1 AA, no allow-list |
| Desktop widths | 1280 / 1440 / 1920 / 2560 | **VERIFIED** — no horizontal overflow |
| Narrow widths | 1024 / 768 / 480 | **VERIFIED** — no horizontal overflow |
| Secret scan | diff scan, §9 | **VERIFIED** — nothing found |
| CI, Linux | GitHub Actions | **VERIFIED** — green |
| CI, Windows | GitHub Actions | **VERIFIED** — green on `0ceead6`: 3687 passed, 5 skipped, on two independent runs of that head |
| Electron shell | `playwright test --config playwright.electron.config.ts` | **VERIFIED** — 20 passed under Xvfb: a real main process, real windows, the real preload bridge. The shell was launched and its first window read: the grid is `52px 41px 867px`, the bridge is present, no console errors. No **packaged** (`electron-builder`) installer was produced |

**Windows CI took two attempts, and the first was a wrong diagnosis.** A test
of mine asked a TLS context to enumerate its own trust store. That fails on
Windows because pip's vendored `truststore` is injected into `ssl`, and the
first fix (`444d2b1`) assumed what it replaced was `ssl.create_default_context()`
and built the context directly instead. What truststore replaces is the context
*class*, so `ssl.SSLContext(...)` returns its class too and the call raised
exactly as before — an earlier draft of this report recorded that fix as done,
and it was not. `0ceead6` tests through `load_verify_locations`, which
truststore delegates to a real OpenSSL context, on a certificate and on a file
of prose so the assertion cannot pass vacuously. The failure reproduces on Linux
by injecting truststore before collection, which is how the second fix was
checked before it was pushed rather than after.

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
working and are not. Fifteen findings. The last seven are the ones that matter
most, because this audit found none of them.

| # | Finding | Age |
|---|---|---|
| 1 | The panel picker offered 23 of the 35 panel kinds. Twelve implemented panels — approvals, audit, pre-trade gate, portfolio and the desk's eight — could only appear if a template seeded one. | pre-existing |
| 2 | `DELETE /campaigns/{id}` existed, refused correctly while running, kept the research, and nothing in the product could reach it. | pre-existing |
| 3 | `rules/` shipped four rule files and nothing could read them; their field names had drifted from the model they were meant to produce. | pre-existing |
| 4 | `src/modes.ts` and `src/explain.ts` — 279 lines of client code for screens that do not exist. `modes.ts` was this branch's doing. | 1 of 2 new |
| 5 | `PATCH /conversations/{id}` existed and no control reached it: a thread's title is derived from its first message and could not be corrected. | pre-existing |
| 6 | "PAPER ONLY" lost the sentence explaining it when the chooser was removed. Two words alone read as a setting somebody could switch off. | **new, this branch** |
| 7 | Four assertions of the form `assert X or True`, which pass whatever X is. One asserted the *opposite* of the design and `or True` kept it quiet. | 3 pre-existing, 1 new |
| 10 | `AgentService._run` caught every exception, recorded it, and then **returned** the record. A returned value is a completed job, so the registry marked it `DONE`: the inbox filed a specialist that died on an `HTTPStatusError` as finished work, the orchestrator passed its "could not complete the task" summary forward as a step result, and the research loop's stop-on-failure branch never fired. | **new, this branch** |
| 9 | The shell grid was broken and every screen rendered blank. `workstation.css` still declared a third row for the status bar Horizon removed; `horizon.css` restated the corrected rows in a block that appeared **twice**, and is imported first, so at equal specificity the stale declaration won. With `.workstation-main { grid-row: 3 }` the content landed in the vestigial 34px row and the tab row took the whole viewport. | **new, this branch** |
| 11 | The inbox's Open button on a finished prop matrix pointed at `#prop`, which is a **panel kind, not a route**. The shell found no such destination and rewrote the link to Home, so the one control on a finished matrix moved the operator away from their work and looked deliberate doing it — which is what `destination()`'s own docstring calls worse than offering no button. `#actions` sent a finished mission to the action registry rather than to the missions screen. | **new, this branch** |
| 12 | The status bar was removed from the shell and its height token was not. Two rules still reserved 34px for it, so the inbox drawer stopped short of the bottom edge, floating above a strip of chrome nothing draws. | **new, this branch** |
| 13 | "Skip to content" navigated to Home from every screen that was not Home. The shell routes on `window.location.hash` and the skip link's href is a fragment on the same page; `locate` sends every hash it does not recognise to Home, so the landmark id never needed to *collide* with a route — being unknown was enough. The earlier fix renamed it from `workspace` to `main-content`, which removed the collision and left the navigation. | **new, this branch** |
| 14 | The book loop's Alpha stage opened `#alpha`, which is not a route. `StageSpec.route` says in its own docstring that a stage which cannot be opened is decoration; the stage holding the candidate signals was. | pre-existing |
| 15 | `rithmic.normalise.timestamp` returned `datetime.now(UTC)` for a message carrying no time, so an account or position snapshot the provider never stamped came back stamped with this machine's clock — and an hour-old snapshot reads as current to anything asking how old the account state is. Every other field in that module leaves an unreported value `None` and says why. | pre-existing |
| 8 | `RithmicAdapter._require_session` refused on `session.ready` alone, and a degraded plant leaves `ready` **true**. Its message named degraded plants in a branch that could only be reached when there were none — so a plant missing heartbeats produced a snapshot that reconciliation uses to *replace* local state, arriving complete. | **new, this branch** |

Each is fixed and tested. Finding 6 is the one worth dwelling on: it is a safety
claim that got quietly weaker, and it was introduced by this branch's own
simplification. The test diff shows it as an assertion deleted alongside the
screen it described — which is exactly how a removal takes something with it.

**Findings 9 through 15 were all found by another session working on this
branch, not by this audit.** Findings 4, 11, 13 and 14 are one family: a link
that resolves to Home. `resolve` sends every route it does not recognise
there, by design, so an internal link naming nothing is indistinguishable
from one naming Home deliberately — and four separate surfaces minted one.
Three tests guarded the *collision* case for the skip link and none could
see it, because a collision was never required. Each is now checked by
resolving the link against the shipped manifest rather than by reading it.

Finding 10 is false completion at runtime — the exact failure mode this audit
was commissioned to hunt — reported as success to three separate consumers. Finding 9 is worse in one respect: the
application did not render. Every check this report lists passed while it did not render, because
every one of them tests structure rather than geometry: `pytest` and `vitest`
run in jsdom, which has no layout; the axe pass and the manifest-reachability
specs assert that elements exist and are reachable, not where they are on the
page; `tsc` and `vite build` never evaluate cascade order. The nine screenshots
in `artifacts/qa/` would have shown it immediately — tabs stranded mid-page above
an empty screen — and they were captured without being looked at. Evidence nobody
reads is not evidence. The regression guard now in `workstation.spec.ts`
measures the geometry; it fails against the pre-fix stylesheets, which is how it
was checked.

Five further sweeps found nothing — with the caveat above, that a sweep only
covers what it is pointed at, and two of them have now been corrected because
the thing they were pointed at was not the thing that was wrong:

- **Unconditional skips** — none. Every `pytest.skip` is conditional with a
  stated reason; no `xfail`; no `.skip` in vitest or Playwright.
- **Unfailable assertions** — none left. The four `or True` assertions are
  finding 7; nothing else in the suite can pass regardless of its subject.
- **Swallowed exceptions** — the sweep looked for `except: pass`/`continue`
  and found none that hide a fact. It was the wrong shape to look for.
  Finding 10 is `except` → record → **return**, which reports the failure
  honestly to a log and then hands the caller a value that means success.
  A pattern-matched sweep finds the pattern it was given, and this report
  previously presented that as a clean result.
- **Inert UI controls** — the sweep looked for no-op handlers, `href="#"` and
  TODOs, and found none. It was the wrong shape, in the same way the exception
  sweep above was. Finding 11 is a control that is not inert at all: it is
  wired, it navigates, and it navigates somewhere unrelated. `#prop` reads
  exactly like the working links beside it and names nothing, and its unit test
  asserted the string rather than that it led anywhere. A link is now checked
  by resolving it against the shipped manifest
  (`test_every_place_the_inbox_can_send_somebody_exists`), which fails on the
  old code.
- **Over-claiming** — no capability is set to a `VERIFIED_*` state anywhere; the
  only matches for "Rithmic support/connected/live" are docstrings *warning
  against* the phrasing.

---

## 12 · What is not verified

Stated plainly, because a report that only lists successes is not a report.

- **No live Rithmic connection.** §7.
- **No order of any kind, in any environment.** §7.
- **No packaged Electron app was built.** The shell itself is no longer
  unverified: it was launched under Xvfb against the real API, its window was
  read, and `tests/electron/` ran green — 20 tests over real windows, the real
  preload bridge and the real session file. What has still never been produced
  or run is an `electron-builder` NSIS installer, which is the artifact an
  operator would actually receive.
- **Visual regression** is nine full-page screenshots at 1440×900 in
  `artifacts/qa/`, captured from the shipped manifest. There is no baseline to
  diff against, so they are evidence, not a test.
- **Chat performance** excludes the provider by design; the figures are local
  work only, and the method says so.
- **Reference-project classification** could not be produced. The two archives
  supplied were the Rithmic SDK — classified in the ADR with SHA-256s — and a
  snapshot of AlgoForge at the same baseline commit this branch started from.
  There were no third-party reference projects in this container to classify.

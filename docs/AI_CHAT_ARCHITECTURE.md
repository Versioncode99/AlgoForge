# AI Chat — audit, architecture, and what was deliberately not built

The phase brief asked for a first-class conversational research environment:
a chat that is a *panel* in the user's workspace rather than a mode, that uses
the real AlgoForge quantitative infrastructure rather than a parallel one, whose
conversations persist, and whose claims stay traceable.

This document records what already existed, what was built, and — at least as
importantly — what was found to exist already and therefore not rebuilt.

---

## 1. Forensic audit: what was already here

| Capability | State before | Modules |
| --- | --- | --- |
| Bounded tool loop over a closed verb set | **Existed** | `forge_api.assistant` — 154 registered actions, schema-validated arguments, honest refusals, a no-credential deterministic path |
| Action registry with permissions | **Existed** | `forge_api.actions`, `forge.modes.permissions` — pure function of actor/mode/stance/action, first-match-wins, named refusals |
| Conversation persistence | **Missing** | none — the console thread lived in a React `useState` and died with the tab |
| New chat / history / search | **Missing** | none |
| Conversation context | **Missing** | none |
| Artifacts in a reply | **Missing** | none |
| Chat as a workspace panel | **Declared, not built** | `PanelKind.AGENT` existed, seeded by the AI template, rendering "not built yet" |
| Strategy IR | **Existed** | `forge.strategy.ir` — canonical, validated, compiled, lookahead-refusing |
| Python export with verification | **Existed** | `forge.strategy.export` — generates and *executes*, comparing ledgers |
| Pine / NinjaScript generation | **Deliberately absent** | `describe_target` returned coverage and no code |
| Cross-platform equivalence analysis | **Missing** | none |
| Regime analysis | **Existed** | `forge.analytics.regime` — 2×2 with exposure, trade share, transitions, concentration |
| Monte Carlo / resampling | **Existed** | `forge.analytics.resample`, `forge.prop.engine`, `forge.propdesk.survival` |
| Parameter surfaces | **Existed** | `forge.research.parameter_surface` |
| Model routing | **In flight** | open on another branch; not duplicated here |
| Background/async work | **Existed** | `forge_api.jobs` registry, `forge_api.research_loop`, campaigns with start/stop/pause/resume, `forge_api.orchestrator` missions |
| Workspaces, panels, persistence, link groups | **Existed** | `forge.workstation` |
| Multi-window | **Missing** | `apps/desktop/main.js` is single-window with no IPC surface at all |

Two audit findings changed the plan materially.

**The tool loop already existed and was good.** The temptation was to build a
"chat agent". There was already a bounded loop over a closed registry with
honest refusal reporting and a deterministic fallback. Building a second one
would have created a second permission surface — which is the specific way a
chat panel becomes a way around a policy without looking like one. So the chat
service *wraps* it and adds persistence, and the registry stays the only way in.

**A scheduler already existed.** The brief explicitly warns against building a
second one. AlgoForge has jobs, a research loop, and campaigns that start, stop,
pause and resume — all already reachable from the action registry and all already
governed by the `AUTOMATION` permission set. Durable chat-initiated work is
therefore a campaign, not a new queue.

---

## 2. What was built

### 2.1 `forge.conversation` — the durable thread

SQLite, beside the other application databases. Deleting it costs the operator
their dialogue history and not one strategy, verdict, experiment or holdout —
which is the test the storage rules apply, and it passes in both directions:
nothing here writes to a research store, and nothing here is read by the judge.

Shapes: `Conversation`, `Turn`, `ToolCall`, `Artifact`, `AttachedContext`.

### 2.2 Provenance, derived rather than asserted

The central design decision of the whole phase.

A chat transcript is where AlgoForge's core distinction is easiest to lose:

- *"I'm sure the edge is volatility dependent."* — a stated belief
- *"The edge appears volatility dependent."* — a model's sentence
- *"G6 failed on the high-volatility fold."* — a judged result

Three paragraphs of similar prose, meaning entirely different things. A store
that keeps them identically will eventually quote the first back as the third.

So every turn carries a `Provenance`, and it is computed from **how the sentence
was made**:

```
model prose            -> MODEL_PROSE
rendered action result -> ACTION_RESULT
ledger summary         -> DETERMINISTIC
```

A model can produce the first. It can never produce the third, because the third
is only minted on the code path where no model ran. That asymmetry is the whole
guarantee.

Note what this means for a *grounded* answer. The assistant may run six actions
and every figure in the reply may have come back from one — the reply is still
`MODEL_PROSE`, because the sentence is the model's. The actions produced the
facts; they are recorded separately as tool calls and artifacts, and a reader
who wants the number goes to the artifact.

There is no `set_provenance`, no `promote`, no `mark_as_evidence`, and no edit to
a turn's text at all. `test_the_store_offers_no_way_to_promote_a_turn` asserts
that **absence**, because the absence is the guarantee.

### 2.3 Artifacts carry references, never numbers

Derived from the calls that succeeded, by a table, rather than by asking the
model what to attach. Two reasons and the second matters more:

- a copied number goes stale silently;
- a copied number **can be wrong in the first place** — a model that writes
  figures into a stored artifact has fabricated evidence a later reader cannot
  distinguish from a computed one.

Identifiers are taken from the arguments the registry validated, so an artifact
cannot reference something that was never touched. Only successful calls produce
one: an artifact for a refused action is a link to a result that does not exist,
which reads as the work having been done.

### 2.4 Refusal is not failure

They arrive from the registry indistinguishable — both `ok: False` with a
message — and mean opposite things: the permission boundary working, against
something broken. Told apart by the words `forge.modes.permissions` produces,
and an unrecognised message is recorded as a **failure**, which is the safer
mistake: a failure invites a look, a refusal invites a shrug.

Rendered distinctly in the panel, in the mark, the word and the colour. One grey
"error" row for both would teach somebody to ignore both.

### 2.5 Context is explicit, visible and scoped

What a conversation can see is what was attached to it. There is no ambient
"everything the user has", so one conversation cannot reach another's account
state — isolation is a property of the data model rather than of every caller
remembering to filter. The attachment is rendered as a chip the operator can read
and remove.

The subject passed to the model is deliberately shallow — kind, identifier,
label. Resolving each attachment into its full state would put an account's
balances into every prompt whether or not the question needed them.

### 2.6 The panel

`PanelKind.AGENT` was registered, seeded by the AI workspace template, and
rendering "not built yet". It is the conversation now. The console route is the
*same component* at full width rather than a second implementation that would
drift into a different product with the same name.

No bubbles. The previous console gave the operator a rounded speech shape and
the assistant a bordered card — a messaging app's vocabulary, saying "two people
are talking" where the honest thing to say is "you asked, and this is what the
system produced, and how". A message is a paragraph with a label, and the label
is the part that matters.

### 2.7 Cross-platform porting

Documented in full in the module docstring; the short version is that the
existing exporter refused to emit Pine on sound reasoning (nothing here can check
it), which is right and slightly too blunt — the trader writes it by hand
instead, badly. So porting generates, and pays for generating by classifying
every feature, condition, exit, parameter and execution assumption as
`EQUIVALENT` / `APPROXIMATED` / `UNSUPPORTED` with the reason, and capping the
claim:

```
VERIFIED     executed on both sides, ledgers matched. Python only, and only by
             handing in the comparison — no path reaches it by inspection.
STRUCTURAL   every element maps natively; nothing ran it.
APPROXIMATE  at least one named difference that can change results.
INCOMPLETE   at least one element has no representation at all.
```

The phrase "logic preserved" appears nowhere below `VERIFIED`.

Three real bugs in the generated Pine were found by reading the output rather
than by a test passing: `ta.atr(14.0)` does not compile (lengths are
`simple int`), `ta.dmi(n,n).adx` is not Pine (it returns a tuple), and a stop on
a shifted feature read the wrong bar.

---

## 3. What was deliberately not built

| Not built | Why |
| --- | --- |
| A second agent loop | One already exists over the registry. A second would be a second permission surface. |
| A second scheduler / research queue / inbox | Jobs, the research loop and campaigns already exist and are already permissioned. The brief explicitly warns against this. |
| A knowledge graph | `forge.research` already carries lineage, memory and provenance. The brief says not without evidence it is needed; no such evidence appeared. |
| Arbitrary code execution from chat | There is no arbitrary-code verb in the registry and none was added. |
| Conversation memory that feeds research | A conversational claim must never become evidence. The provenance model exists to make that impossible, not to make it configurable. |
| Multiple simultaneous chat panels as a distinct feature | Panels already duplicate; conversation identity is already separate from placement. Nothing extra was needed, and the brief warns against building it merely because it is possible. |
| NinjaScript / MQL5 code generation | No C# compiler and no MetaEditor here. Analysed and reported instead. |
| Streaming token output | The assistant's loop is request/response and the honest progress signal is per-action, not per-token. Faking a stream would be faking progress. |

---

## 4. The safety boundary, restated

The chat adds **no new action surface**. It calls `forge_api.actions` as
`Actor.AI`, through `forge.modes.permissions`, which is a pure function of actor,
mode, stance and action facts — none of which a model can set. Protected controls
are denied in every mode and every stance. Reaching the book requires one
configuration reached by two explicit opt-ins, and even there the order passes
the deterministic pre-trade gate.

Asked politely, in a sentence, from a chat panel, none of that changes.
`test_asking_for_a_protected_control_does_not_produce_one` is the assertion.

---

## 5. Remaining work

| Item | Status |
| --- | --- |
| Multi-window workspaces (Electron IPC, window registry) | Not started — `apps/desktop/main.js` has no IPC surface |
| Point estimates adjacent to their own tails | Future work, per the QuantPad research |
| Scrubbable equity-path fan | Future work |
| Opening-context auto-attachment from chart/strategy surfaces | The panel accepts an `opening` context; the call sites do not pass one yet |
| Artifact deep links for `port`, `resample`, `parameter_surface` | Reported honestly as "no panel yet" rather than linking nowhere |

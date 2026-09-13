# TradingAgents → AlgoForge adaptation report

What is being adapted, where it lands, what it costs, and what was deliberately
left alone. Doc 1 §41 asks for concrete engineering decisions rather than
"TradingAgents inspired our architecture" — so every entry names a module, a
test, and a failure it prevents.

Inputs: `TRADINGAGENTS_AUDIT.md` (what was read) and
`TRADINGAGENTS_GAP_MATRIX.md` (the disposition). This file is the decision record.

---

## 1. The relevance test, applied

Doc 1 opens with twelve questions every adaptation must answer. Applied to the
five gaps the matrix identifies, only those five survive. Everything else in the
upstream either already exists in AlgoForge in a stronger form (14 items) or
fails question 2 — *does AlgoForge actually have this problem?* — outright.

Worth stating plainly, because it is the point of §57: **this audit makes
AlgoForge better in five specific places and does not make it bigger anywhere.**
No new package, no new dependency, no new agent, no new persistence layer. Four
of the five changes are edits to files that already exist.

---

## 2. Adaptation 1 — configuration-aware checkpoint identity (D1 §15)

**Problem.** `resume_campaign()` in `apps/api/forge_api/campaigns.py` addresses a
checkpoint by campaign ID. Nothing prevents a resume under a materially different
temporal scope, dataset, construction-vocabulary version or agent configuration
from attaching to state that was built under the old one. The campaign then
reports continuation, and the result is a plausible, wrong answer of exactly the
class Part 9 of the current directive asks to hunt for.

**Upstream pattern.** `graph/checkpointer.py::thread_id(ticker, date, signature)`
— fold the shape-affecting choices into the identity so an incompatible resume
cannot address the old state at all.

**Adaptation, not a copy.** AlgoForge's identity components are its own:
campaign, research plan fingerprint, temporal scope, dataset identity,
construction-vocabulary version, and the agent/role configuration. Notably *not*
the model assignment — Doc 1 §15 warns against over-binding nondeterministic
information, and swapping a model does not invalidate completed experiments.

The fail-safe property is what makes this worth copying: an incompatible resume
does not need to be *detected*. It computes a different identity and therefore
starts clean. Detection logic can be wrong; addressability cannot.

**Backwards compatibility.** Upstream keeps the legacy ID when `signature` is
empty. AlgoForge takes the same route: existing checkpoints keep resolving, and
only new ones carry the compatibility component.

**Tests required.** Doc 1 §15 lists nine: crash, restart, changed temporal scope,
changed model configuration (must *not* invalidate), changed agent configuration,
changed research plan, changed vocabulary version, partial experiment, duplicate
resume, resume after completion. Each asserts no duplicated events, messages or
experiments, and no false continuation.

**Lands in:** `forge_api/campaigns.py`, `research/campaign.py`. **Phase 2.**

---

## 3. Adaptation 2 — model capability contract (D1 §9)

**Problem.** `catalog.py` knows a model's tier and whether it is servable. It
does not know whether the model supports structured output, tool calling, what
its context window is, or whether it streams. Those assumptions are distributed
across callers, so nothing can answer "can this model do X?" before dispatch.

**Upstream pattern.** `llm_clients/capabilities.py` — a frozen dataclass of
capability flags plus a lookup, so adding a model is a table edit.

**Adaptation.** A capability record on AlgoForge's existing catalogue entries
rather than a new registry (Doc 1 §40 — do not create a second model registry).
`model_routing.resolve()` then consults it during selection, so a role needing
structured output is never *assigned* a model that cannot produce it. That is
strictly better than upstream's try-and-catch fallback in
`agents/utils/structured.py`: the check moves from call time to selection time.

**Cost.** The table needs maintaining as providers change. Accepted — the
alternative is the same knowledge scattered implicitly, which also needs
maintaining but cannot be reviewed in one place.

**Lands in:** `forge_api/catalog.py`, `forge_api/model_routing.py`. **Phase 7.**

---

## 4. Adaptation 3 — failure classes sized by reaction (D1 §10)

**Problem.** Retries do not distinguish transient failure from authentication
failure from an unsupported capability. Doc 1 §10's own rule — *never use retries
to hide systemic failures* — is not currently enforceable, because the code
cannot tell the cases apart.

**Upstream pattern.** `dataflows/errors.py`, and specifically its sizing rule:
*the number of types is the number of distinct router reactions, not the number
of human-describable causes.*

**This corrects the directive.** Doc 1 §10 lists seven failure classes and reads
like a request for seven types. Applying the upstream rule, AlgoForge needs
**four**, because that is how many distinct behaviours exist:

| Type | Reaction |
| --- | --- |
| `TransientFailure` | Bounded retry, then fall back |
| `NotEntitled` (auth, quota, policy) | Do not retry; surface |
| `IncompatibleModel` | Re-route to a capable model — the capability table above makes this reachable |
| `Unusable` (invalid output) | One correction attempt, then REVIEW |

Rate limit collapses into `TransientFailure` with a different backoff *parameter*,
not a different type — the reaction is the same. Safety/policy failure collapses
into `NotEntitled`: never circumvented, never retried. This is a smaller design
than the directive literally asks for and a more correct one, which the audit
records as a finding rather than an omission.

**Lands in:** `forge_api/model_routing.py`, provider call sites. **Phase 7.**

---

## 5. Adaptation 4 — an explicit REVIEW outcome (D1 §7)

**Problem.** AlgoForge validates structurally, but has no representation for
"this output could not be safely interpreted." The risk is upstream's #1170
exactly: a parse failure degrading into a plausible neutral value.

**Upstream pattern.** `signal_processing.py` returns `REVIEW`; `rating.py`
exposes `is_review()` so consumers must handle it explicitly rather than
pattern-matching a string.

**Adaptation.** AlgoForge already has the right instinct in
`forge.analytics.reading`, whose `Standing` ladder downgrades to `DESCRIPTIVE`
rather than asserting. The gap is that agent outputs have no equivalent. A
`REVIEW` outcome alongside the existing verdicts, which is never coercible to a
pass, a fail, or an inconclusive result.

**Lands in:** `research/models.py`, `research/validation.py`. **Phase 7.**

---

## 6. Adaptation 5 — role-level output bounds (D1 §11)

**Problem.** `SAFETY_LIMITS` pins one global 1,600-token response ceiling. A
role that reasons at length and a role that returns a verdict share it.

**Adaptation.** Per-role ceilings on `RoleRouting`, defaulting to the global
value so nothing changes until a role is configured. The global limit stays a
hard safety limit — it is not budget, and `BudgetSettings.enforced` must not
switch it off. The existing separation in `settings_store.py` already makes that
distinction visible; this extends it rather than blurring it.

**Lands in:** `forge_api/model_routing.py`, `forge_api/settings_store.py`.
**Phase 7.**

---

## 7. Also adopted: the undated-source rule

`dataflows/date_window.py` keeps an undated item **only when the window reaches
the present**, on the grounds that in a historical run you cannot prove it isn't
future. AlgoForge's external research retrieval has no explicit rule for an
undated source today. This is a one-line default with a real temporal-integrity
consequence, and it is folded into the external-research hardening work rather
than tracked separately.

---

## 8. Security implications

None of the five adaptations widens a surface.

- Checkpoint identity **narrows** what a resume can reach.
- The capability contract and failure taxonomy are internal routing decisions;
  neither is reachable from external content.
- `NotEntitled` explicitly removes a retry path, which is a reduction.
- REVIEW adds a terminal state that cannot be coerced into a verdict.
- Role-level bounds only lower ceilings.

The deterministic authority boundary is untouched: no adaptation gives a model a
new capability, and none of them is an input to `forge.modes.permissions`.

## 9. Performance implications

Measurable, and small:

- A capability check at selection time replaces a failed provider call plus a
  fallback — strictly fewer round trips on incompatible pairings.
- `NotEntitled` eliminates retry storms against a provider that will never
  succeed, which is the largest latency win available here.
- Checkpoint identity adds one hash per resume.
- Role-level bounds reduce worst-case tokens.

No adaptation adds an LLM call. Two remove one.

## 10. Remaining opportunities, not taken now

| Opportunity | Why deferred |
| --- | --- |
| Provider reasoning-tier knobs (`thinking_level`, `reasoning_effort`) | Provider-churn-prone and no AlgoForge role currently needs a tier the catalogue cannot express. Revisit when one does. |
| Structured-output conformance smoke script | Cheap, but meaningless until the capability table exists. Follows adaptation 2. |
| Behaviour-based routing in the *data* provider layer | The `errors.py` sizing principle applies to `data/live.py` as well as to models. Grouped with the Doc 1 §34 data-fabric work rather than done piecemeal. |

---

## 11. What this changes about the previous phase's claims

The previous phase implemented Doc 1 §4–§24 before this audit existed, which
inverted §53's ordering. Having now done the comparison, the honest finding is
mixed:

**Upheld.** AlgoForge's research-side capabilities — role specialisation, dynamic
selection, novelty, allocation, frontier, effectiveness, construction grammar —
are equal or stronger than upstream's in every case inspected. 14 capabilities
classified KEEP_EXISTING. The downstream work was not wrong.

**Not upheld.** The claim that those adaptations were *justified by an audit* was
not true when it was made, because there was no audit. It is true now, and the
gap matrix is the evidence. Two gaps (checkpoint identity, capability contract)
would have been found earlier had the order been followed, and one directive
requirement (§10's seven failure classes) turns out to have been over-specified
in a way only the comparison reveals.

That is the concrete cost of running Phase A late, recorded here rather than
smoothed over.

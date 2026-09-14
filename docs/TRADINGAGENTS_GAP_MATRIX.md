# TradingAgents → AlgoForge gap matrix

Every meaningful upstream capability, classified exactly once, per Doc 1 §2.
Evidence is the AlgoForge module that already covers it, or the gap that remains.

Upstream: `TauricResearch/TradingAgents` @ `be952b8`. AlgoForge: branch
`claude/zen-hawking-nm63gx`, verified against the working tree rather than
against prior reports.

**Classification counts:** KEEP_EXISTING 14 · ADAPT 4 · IMPORT_PATTERN 3 ·
EXTEND_EXISTING 4 · REPLACE 0 · REJECT 8 · DEFER 2.

`REPLACE` is empty. Doc 1 says it should be rare; on inspection, nothing upstream
is materially better than the AlgoForge equivalent at the same job. The gaps are
where AlgoForge has *no* equivalent, not where it has a worse one.

---

## Orchestration and agents

| Capability | Class | Evidence / rationale |
| --- | --- | --- |
| LangGraph state graph | **REJECT** | AlgoForge orchestration is deterministic (`research/orchestration.py`, `campaign.py`). A graph framework would be a second authority over research flow — Doc 1 §3. |
| Specialised analyst roles | **KEEP_EXISTING** | `research/agents.py` — 10 roles with `ROLE_PURPOSE` and `ROLE_BUCKETS`, versus upstream's 5 fixed analysts. AlgoForge's are research-shaped, not ticker-shaped. |
| Dynamic specialist selection | **KEEP_EXISTING** | `research/routing.py` `route()`/`candidates()` selects per question and is auditable via `as_dict()`. Upstream runs a fixed analyst set every time — AlgoForge is ahead here. |
| Bull/bear researcher debate | **ADAPT** | Take deliberate opposition; reject three prompt personas. Needs a structured objection schema a validator can act on. → adaptation report §3. |
| Three-way risk debate (aggressive/conservative/neutral) | **REJECT** | Risk in AlgoForge is deterministic (`forge/risk/`, `propdesk/risk.py`) with hard limits. A debate over risk is precisely the authority inversion Doc 1 §3 forbids. |
| `Reflector` — LLM reflects on decisions | **REJECT** | `research/effectiveness.py` measures role contribution deterministically. Reflection over prose produces prose. |
| Conditional graph edges | **KEEP_EXISTING** | `research/plan.py` + `frontier.py` already gate progression on evidence rather than on round counts. |
| Trader / portfolio-manager node | **REJECT** | AlgoForge's equivalent is the gate ladder plus allocation, which carry evidence requirements a single rating cannot. |

## Model and provider layer

| Capability | Class | Evidence / rationale |
| --- | --- | --- |
| Per-role model configuration | **KEEP_EXISTING** | `apps/api/forge_api/model_routing.py` (620 lines) — `Role`, `RoleRouting`, `resolve()`, with a UI. Upstream has only quick/deep; AlgoForge is substantially ahead. |
| **Declarative model capability table** | **IMPORT_PATTERN** | **Gap.** `llm_clients/capabilities.py` declares tool-choice / json-mode / json-schema / preferred method as data. AlgoForge's `catalog.py` carries tier and servability only, and capability assumptions live at call sites. Doc 1 §9. |
| Fallback chain | **EXTEND_EXISTING** | `model_routing.resolve()` already does assigned → role fallback → global → smart, with the reason recorded. Extend with failure-class awareness, below. |
| **Error taxonomy sized by router reaction** | **IMPORT_PATTERN** | **Gap.** `dataflows/errors.py`. AlgoForge retries without distinguishing transient from authentication from unsupported-capability. Doc 1 §10 — but see the note on sizing in the audit. |
| Retry budget / `llm_max_retries` | **EXTEND_EXISTING** | Bounded retries exist; they are not failure-class aware. Same work item as above. |
| Global max-tokens setting | **EXTEND_EXISTING** | `SAFETY_LIMITS` pins a 1,600-token response ceiling globally. Doc 1 §11 wants role-level bounds. |
| Model catalogue + validators | **KEEP_EXISTING** | `catalog.py` plus the provider restriction in `SAFETY_LIMITS`. Upstream's "custom-only for fast-moving providers" is a UI nicety, not an architectural gap. |
| Content normalisation across providers | **KEEP_EXISTING** | `model_text.py` already normalises block-list responses. |
| Provider-specific reasoning knobs | **DEFER** | `google_thinking_level`, `openai_reasoning_effort`. Useful, provider-churn-prone, no current AlgoForge caller needs it. Revisit when a role demands a reasoning tier the catalogue cannot express. |

## Structured output and validation

| Capability | Class | Evidence / rationale |
| --- | --- | --- |
| Typed Pydantic agent outputs | **KEEP_EXISTING** | `FrozenModel` throughout; `research/models.py`, `conversation/models.py`. |
| Structured-output binding with free-text fallback | **ADAPT** | `agents/utils/structured.py` falls back to free text when the provider can't do structured output. AlgoForge should bind the *capability check* (above) to this rather than try-and-catch. |
| **INVALID → REVIEW, never a fabricated verdict** | **IMPORT_PATTERN** | **Gap.** `signal_processing.py` returns `REVIEW`. AlgoForge has no explicit representation for "this output could not be safely interpreted". Doc 1 §7. |
| Deterministic extraction instead of a second LLM call | **KEEP_EXISTING** | Already the house style — `analytics/reading.py`, `bars.ts` merge, novelty fingerprints. |

## Temporal integrity

| Capability | Class | Evidence / rationale |
| --- | --- | --- |
| Point-in-time gate on memory | **KEEP_EXISTING** | `research/timescope.py` + provenance model. AlgoForge's is broader: it covers evidence, findings and campaign decisions, not just a lessons log. |
| FRED vintage pinning (`realtime_start`/`realtime_end`) | **KEEP_EXISTING** | AlgoForge does not consume revision-prone macro series. The *principle* — a later revision must not leak backwards — is already enforced in `data/freshness.py` and the vault mirror. |
| UTC-normalised exclusive-upper-bound date windows | **KEEP_EXISTING** | `market.py::_instant()` resolves naive stamps to UTC; chart cursors are exclusive by construction. |
| Undated item kept only when the window reaches the present | **ADAPT** | A sharper default than AlgoForge's. Worth applying to external research retrieval, where an undated source currently has no explicit rule. |
| **Graph-shape-aware checkpoint identity** | **IMPORT_PATTERN** | **Gap, and the highest-risk one.** `graph/checkpointer.py`. `resume_campaign()` binds to campaign ID only. Doc 1 §15. |

## Data layer

| Capability | Class | Evidence / rationale |
| --- | --- | --- |
| Vendor adapters | **REJECT** | Equities/social vendors; AlgoForge is futures bars. `data/live.py` + `MarketDataCache` already cover its providers. |
| Router reacting by behaviour not vendor | **EXTEND_EXISTING** | The pattern is right and AlgoForge's provider layer should adopt it — same work item as the error taxonomy. Doc 1 §34. |
| Deterministic market-data verification snapshot | **KEEP_EXISTING** | `analytics/reading.py` is the stronger form: AlgoForge computes the finding rather than handing a model numbers and asking it to stay honest. |
| OHLCV cache freshness | **KEEP_EXISTING** | `data/freshness.py`. Its *enforcement* gap is an AlgoForge issue (Doc 1 §35), not something upstream solves better. |
| Prediction-market / Reddit / StockTwits sources | **REJECT** | No defensible role in a systematic futures research loop. |

## Everything else

| Capability | Class | Evidence / rationale |
| --- | --- | --- |
| Append-only markdown decision log | **REJECT** | `research/knowledge.py` is SQLite with provenance and supersession. |
| HTML-comment record separator | **KEEP_EXISTING** | Neat, but AlgoForge does not serialise records into prose. |
| CLI / i18n / Docker | **REJECT** | No architectural bearing. |
| Reporting layer | **KEEP_EXISTING** | `explain/` and the dossier already exceed it. |
| Config precedence (env → file → default) | **KEEP_EXISTING** | `settings_store.py` with `SAFETY_LIMITS` separation is stricter. |
| Smoke script for structured output | **DEFER** | A cheap provider-conformance check. Worth having once the capability table exists; pointless before it. |

---

## The gaps this matrix identifies

Five items are classified `IMPORT_PATTERN` or flagged **Gap** above, and they are
the entire actionable output of this audit:

| # | Gap | Directive | Risk if left |
| --- | --- | --- | --- |
| 1 | Checkpoint identity is not configuration-aware | D1 §15 | A campaign resumes onto state built under an incompatible scope or vocabulary and reports it as continuation. Silent wrong answer. |
| 2 | No declarative model capability contract | D1 §9 | Capability assumptions drift across call sites; a model that cannot do structured output fails at the call rather than at selection. |
| 3 | Failure classes not distinguished | D1 §10 | Authentication failure retries like a rate limit. Retries hide systemic failure — the thing §10 explicitly forbids. |
| 4 | No INVALID/REVIEW representation | D1 §7 | An unparseable output degrades into a plausible neutral verdict. |
| 5 | Output bounds are global, not per role | D1 §11 | One role can consume the response budget for every role. |

Items 1 and 3 are correctness risks. Items 2, 4 and 5 are contract gaps that
become correctness risks under provider change.

Disposition and sequencing: `TRADINGAGENTS_ADAPTATION_REPORT.md`.

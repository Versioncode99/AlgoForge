# TradingAgents — forensic audit

What was actually read, what was found, and what the findings mean for AlgoForge.

**Upstream inspected:** `TauricResearch/TradingAgents` at `be952b8`, committed
2026-09-07. 145 Python files, 8,520 lines in the `tradingagents` package, 61 test
modules. Read directly from a clone, not from the README or the architecture
diagram — the directive is explicit that the documentation is not the evidence.

**Scope of this document.** Doc 1 §1 asks for a module-by-module inspection and
§56 asks what was inspected, what was learned, what was missed initially, and
what was rejected. That is this file. The capability-by-capability disposition is
in `TRADINGAGENTS_GAP_MATRIX.md`; the engineering decisions and where they land
are in `TRADINGAGENTS_ADAPTATION_REPORT.md`.

---

## 1. What the repository actually is

TradingAgents is an LLM-agent orchestration framework over LangGraph. A run is a
graph of analyst → researcher → trader → risk nodes for one ticker on one date,
producing a five-tier rating. Its structure:

| Area | Lines | What lives there |
| --- | --- | --- |
| `dataflows/` | 3,211 | Vendor adapters (Alpha Vantage, FRED, yfinance, Reddit, StockTwits, Polymarket), the routing layer, the error taxonomy, date-window filtering |
| `graph/` | 1,278 | LangGraph setup, conditional edges, checkpointing, signal extraction, reflection |
| `llm_clients/` | 1,168 | Per-provider clients, the capability table, the model catalogue, validators |
| `agents/utils/` | 1,101 | Tool definitions, memory log, structured-output helper, market-data verification |
| `agents/analysts/` | 470 | Market, news, fundamentals, sentiment, social |
| `agents/` (schemas) | 405 | Typed agent outputs |
| `agents/risk_mgmt/` | 194 | Aggressive / conservative / neutral debators |
| `agents/managers/` | 165 | Research manager, portfolio manager |
| `agents/researchers/` | 130 | Bull / bear |

The headline architecture — many agents debating — is the least transferable part
of the repository. AlgoForge is not a decision-per-ticker system; it is a research
→ evidence → validation → allocation → risk → execution pipeline where the
deterministic layer is authoritative. Importing the graph would import a second
source of truth.

**The transferable value is almost entirely in the small utilities**, which is
what §1 warned would be true ("look for small utilities that solve subtle
problems"). Six of them are genuinely good and are the substance of this audit.

---

## 2. The six findings that matter

### 2.1 Declarative per-model capability table — `llm_clients/capabilities.py`

A frozen `ModelCapabilities` dataclass carrying `supports_tool_choice`,
`supports_json_mode`, `supports_json_schema`, `preferred_structured_method`, plus
two quirk flags (`requires_reasoning_content_roundtrip`,
`requires_reasoning_split`). Clients consult `get_capabilities(model_name)`
instead of branching on model names.

The comments record *why each flag exists*, tied to a specific provider failure:
DeepSeek thinking models accept `tools` but 400 on `tool_choice`; MiniMax M2.x
restricts `tool_choice` to an enum and needs `reasoning_split` so the `<think>`
block lands in `reasoning_details` rather than polluting `content`.

The design property worth taking: **adding a model means editing a table, not
editing client code.** The alternative — which AlgoForge currently has — is that
capability assumptions are distributed across call sites and nothing can answer
"can this model do X?" without reading every caller.

### 2.2 Graph-shape-aware checkpoint identity — `graph/checkpointer.py`

Thirteen lines that solve the problem Doc 1 §15 describes:

```python
def thread_id(ticker: str, date: str, signature: str = "") -> str:
    base = f"{ticker.upper()}:{date}"
    if signature:
        base = f"{base}:{signature}"
    return hashlib.sha256(base.encode()).hexdigest()[:16]
```

`signature` comes from `TradingAgentsGraph._run_signature(asset_type)`, which
folds in the run choices that change the graph's shape. A resume under a
different configuration computes a different thread ID and therefore cannot reach
the old checkpoint — it starts clean instead of silently continuing incompatible
state.

The insight is not the hash. It is **that checkpoint identity is a function of
configuration, not just of the job**, and that the mechanism is
fail-safe-by-construction: an incompatible resume doesn't need to be detected and
rejected, because it cannot address the old state in the first place.

Omitting `signature` preserves the legacy ID, so the change was backwards
compatible — a migration pattern worth copying.

### 2.3 Error taxonomy sized by reaction, not by cause — `dataflows/errors.py`

```
VendorError
├── NoMarketDataError          no usable rows (empty OR stale)
├── VendorRateLimitError       transient throttle -> next vendor
└── VendorNotConfiguredError   missing key -> vendor unavailable
```

The docstring states the principle outright: *"The number of types is the number
of distinct router reactions, not the number of human-describable causes: empty
and stale data get identical handling, so they share `NoMarketDataError` and
differ only in the free-text `detail`."*

This is the correct answer to Doc 1 §10, which lists seven failure classes and
risks being read as "build seven exception types." The right count is however
many the *router* distinguishes. A taxonomy larger than the set of behaviours is
a maintenance cost with no decision attached.

### 2.4 INVALID never becomes a verdict — `graph/signal_processing.py` + `rating.py`

`process_signal` returns one of the five ratings **or `REVIEW`**:

> *"An unrecognizable decision yields `REVIEW` rather than a fabricated `Hold`, so
> a parsing failure is visible instead of masquerading as a tradeable neutral
> signal (#1170)."*

This is exactly Doc 1 §7's requirement, and the failure mode it prevents is the
dangerous one: `Hold` is a plausible, safe-looking, *wrong* value that a parse
failure degrades into silently. Note also that the module deleted its own LLM
call — the structured output made the rating parseable deterministically, so the
second model invocation became dead weight (Doc 1 §51).

### 2.5 Point-in-time gate on memory — `agents/utils/memory.py`

`get_past_context(..., as_of=None)` keeps an entry only when it stores a
resolution date on or before `as_of`:

```python
entries = [e for e in entries if e.get("resolved") and e["resolved"] <= as_of]
```

An entry with no recorded resolution is excluded, not included — the gate fails
closed. `as_of=None` disables filtering for live runs, which is the same
backwards-compatible-by-default shape as the checkpoint signature.

This is Doc 1 §14 and §16 in eleven lines: a lesson learned from an outcome that
had not yet happened cannot leak into a historical run.

### 2.6 Deterministic verification snapshot against confabulation — `market_data_validator.py`

> *"The market analyst is an LLM that can confabulate exact numbers — citing a
> Bollinger band or a 'historically validated bounce' that the underlying data
> doesn't support (#830)."*

The fix is not a better prompt. It computes a ground-truth snapshot — latest
OHLCV row on or before the analysis date, a *fixed* indicator set so the shape is
identical every run — and instructs the analyst to treat it as authoritative for
any exact numeric claim.

AlgoForge's equivalent instinct is already stronger (`forge.analytics.reading`
produces findings by arithmetic rather than asking a model to summarise a table),
but the specific pattern of *handing the model the deterministic numbers rather
than hoping it doesn't invent them* generalises.

Two supporting details in the same area are worth noting: `date_window.py`
normalises every timestamp to UTC, makes the upper bound exclusive at midnight
after `end` so an item stamped exactly then cannot leak, and keeps an undated
item **only when the window reaches the present** — because in a backtest you
cannot prove it isn't future. And `memory.py` uses an HTML comment
(`<!-- ENTRY_END -->`) as its record separator on the explicit grounds that it
cannot appear in LLM prose output. Both are small, and both are the kind of
edge-case reasoning that only shows up on a real read of the source.

---

## 3. What was rejected, and why

| Capability | Why not |
| --- | --- |
| LangGraph / the agent graph itself | AlgoForge's orchestration is deterministic and already exists. Importing a graph framework creates a second source of truth over research flow — Doc 1 §3 forbids exactly this. |
| Bull/bear and three-way risk debate as *architecture* | The underlying idea (deliberate opposition) is worth adapting; the implementation is three prompt personas whose disagreement is stylistic. AlgoForge needs structured objections that a deterministic validator can act on, not more prose. |
| `Reflector` (LLM reflects on its own decisions) | Reflection over model prose produces model prose. AlgoForge already measures agent contribution deterministically in `research/effectiveness.py`, which is a stronger answer to the same question. |
| Markdown append-only memory log | AlgoForge's research knowledge store is SQLite with provenance, supersession and retraction. The markdown log is a downgrade; only its point-in-time *gate* is worth taking. |
| Vendor adapters (Alpha Vantage, Reddit, StockTwits, Polymarket) | Different asset class, different data model. AlgoForge's provider layer is futures/bars-oriented and already exists. |
| Five-tier rating + `SignalProcessor` | AlgoForge has G0–G13, which is a gate ladder with evidence requirements rather than a single ordinal label. Replacing it would be a large regression. |
| CLI / i18n / Docker layer | No bearing on AlgoForge's architecture. |

---

## 4. What I initially missed

Doc 1 §56 asks this explicitly, and the honest answer is that the first pass
looked at the parts the README advertises — the multi-agent graph, the debate
structure, the provider list — and concluded there was little to take. That
reading was wrong in an instructive way: it evaluated the repository by its
architecture rather than by its bug fixes.

Every one of the six findings above exists because something broke and someone
fixed it carefully. Four cite issue numbers (#678, #830, #1089, #1126/#1220,
#1170, #1251). The value in the repository is concentrated in the commits that
respond to a specific production failure, and those are invisible from the
architecture diagram.

The second thing missed on a first pass: **`capabilities.py` and `errors.py` are
the same idea applied to different layers** — declare the variation as data,
sized by the number of distinct behaviours, so adding a case is a table edit
rather than a code edit. Read separately they look like two small utilities. Read
together they are a design principle AlgoForge can apply in more than the two
places TradingAgents applies it.

---

## 5. What this audit does to the downstream adaptations

Doc 1 §53 sequences Phase A (audit) before Phases C–D (implementation). That
ordering was inverted in the previous phase: model routing, the construction
grammar, novelty, allocation and agent effectiveness were all built before this
audit existed.

This audit does not retroactively invalidate that work — inspected against the
upstream, AlgoForge's implementations of the research-side capabilities are
equal or stronger in every case examined (see the gap matrix). But two
conclusions land differently than they would have if the audit had come first:

1. **Capability registry (§9) and checkpoint identity (§15) were the two gaps
   the upstream would have exposed immediately**, and both remained open. Doing
   the audit first would have found them before the downstream work, not after.
2. **The failure-class taxonomy (§10) was over-specified in the directive.**
   `errors.py` shows the correct size is the number of router reactions. Building
   seven exception types would have been the wrong answer to a requirement that
   reads like it asks for seven.

Both are now scheduled — see the adaptation report.

---

## 6. Provenance and licensing

The upstream is read-only reference. No source, prompt, schema or asset was
copied into AlgoForge. Where a pattern was adapted, the adaptation report names
the AlgoForge module that implements it independently against AlgoForge's own
models, and the upstream file that prompted it. Issue numbers cited above are
upstream's own, quoted from source comments as evidence of what each fix was for.

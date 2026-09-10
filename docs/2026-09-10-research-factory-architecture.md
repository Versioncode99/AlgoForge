# The autonomous research factory — architecture map and plan

Date: 2026-09-10
Branch: `claude/cloud-environment-overview-o7an7t`, from `main` @ `c0c6247`
Method: execution tracing. Every claim below was checked by following imports
and call sites, not by reading prose.

---

## Part 1 — What the autonomous engine actually does

### The real execution path

`POST /engine/start` → `AutonomousEngine.start()` (`apps/api/forge_api/engine.py:200`)
spawns `workers` threads, each running `_loop(worker)` → `_cycle(rng, bars, real, worker)`.

`_cycle` (`engine.py:471`) is the whole of the search:

```
policy      = POLICIES[worker % 8]              # experiments.py:36, a fixed tuple
pool        = [k for k in TEMPLATES if family matches policy]
template    = TEMPLATES[rng.choice(pool)]        # <- the only generative act
params      = uniform draw from each ParameterSpec's declared [low, high, step]
              or, for `neighbourhood`/`ablation`, one axis moved off the best prior attempt
              or, for `replication`, every parameter at its default
```

Everything after that line is excellent and stays:

| Stage | Where |
|---|---|
| neighbourhood prune | `ResearchMemory.prune` (`forge/memory/research.py`) |
| exact-repeat refusal | `Experiments.reserve` (`forge_api/experiments.py`) |
| pre-registration freeze | `_freeze_preregistration` (`engine.py:1230`) |
| strategy written to disk | `StrategyLibrary.create_from_template` |
| conformance (G2) | `refresh_conformance` |
| determinism (G7) | `check_determinism` over 6k bars |
| chronological split | `chronological_split` → DEVELOPMENT / VALIDATION / HOLDOUT |
| mechanism control (G9) | `mechanism_for` → `entry_timing_control` |
| validation evidence | `run_validation` → PBO / walk-forward / CPCV |
| verdict | `Judge().evaluate` — G0–G13 |
| burn-once holdout | `ResearchLedger.consume` |
| prop simulation | `simulate_prop_paths` |
| snapshot | `RunSnapshots.write` |

### Why it behaves as a parameter search

The diagnosis in the brief is correct, and the cause is one line.

1. **`TEMPLATES` is a fixed dict.** Twelve shipped Python templates plus whatever
   `TemplateStore` loaded at boot. The engine reads it and never writes to it.
2. **`FamilyRegistry` is runtime-extensible and the engine never calls it.**
   `create_family` and `create_template` exist as *actions* — reachable from the
   console assistant and the HTTP API — but nothing in `_cycle` reaches them.
3. **There is no hypothesis object.** A hypothesis is a *string field on a
   template*. Two hundred parameter draws against `momentum_breakout` produce two
   hundred experiments carrying the same `hypothesis` text, and nothing in the
   system can tell that they are one question asked two hundred times.
4. **Agent proposals are template-bound.** `AgentService.propose` validates
   `raw["template"] in TEMPLATES` and returns `{template, parameters, hypothesis,
   source_ids}`. The specialist can pick a different template; it cannot invent one.
5. **Failures prune, they do not generate.** `_remember` writes a `FailureClass`
   into `ResearchMemory`, which correctly declines to re-test the neighbourhood.
   Nothing reads a failure and asks *what does this suggest?*
6. **Web research is disconnected from the search.** `ResearchLoop` runs Crossref
   queries on a rota into `ResearchLibrary`, and `_cycle` consults it only to
   attach `research_sources` to a strategy already chosen for other reasons.
7. **There is no budget model.** Eight workers draw uniformly forever. 100% of
   compute is category A (same hypothesis, different parameters).

### The load-bearing discovery

`packages/forge/strategy/ir.py` already contains a **declarative strategy IR**
with a closed feature set, and `packages/forge/strategy/export.py` renders it to
guarded Python that is verified to produce a bit-identical ledger:

```
StrategyDefinition (data)
  → validate_definition()          # well-formedness, warmup sufficiency
  → to_python()                    # real module: entry_signal / exit_signal
  → assert_safe()                  # no fs, net, subprocess, dynamic exec
  → verify_python(defn, bars, spec)  # IR ledger == generated-Python ledger
  → StrategyLibrary.create_from_definition()
```

This is the entire safety story for autonomous structural discovery, and it is
already written, already tested (`tests/strategy/test_ir.py`), and already
shipping twelve blueprints through it. **An agent composing a
`StrategyDefinition` is composing data, never code.** No new sandbox is needed
and none will be built.

### Naming hazard found during the audit

`Workspace` means two unrelated things:

* `forge.vault.location.Workspace` — a **place on disk** (storage root).
* `forge.workstation.models.Workspace` — a **screen layout** (panels on a grid).

`AutonomousEngine.__init__` takes the first. `Actions.__init__` takes both, as
`workspace` and `workspaces`. Both survive; nothing is renamed in this change,
but every new module says which one it means.

---

## Part 2 — What is reused, and what is new

### Reused unchanged (no parallel implementation)

`Judge` (G0–G13) · `run_validation` · `chronological_split` · `ResearchLedger`
· `ResearchMemory` · `Experiments` · `RunSnapshots` · `Preregistration`
· `FamilyRegistry` · `TemplateStore` · `StrategyLibrary` · the strategy IR and
its exporter · `ResearchKnowledge` · `ResearchLibrary` · `Actions` registry
· `WorkspaceStore` and the `Panel`/`Workspace` models · `ActivityLog`.

### New modules

| Module | Responsibility |
|---|---|
| `forge/research/frontier.py` | The nine-state research frontier, with provenance, reason and a transition log |
| `forge/research/hypotheses.py` | The hypothesis graph: durable nodes and typed edges, queryable |
| `forge/research/novelty.py` | Deterministic similarity; classifies a proposal as PARAMETER / STRUCTURAL / HYPOTHESIS / MECHANISM / FAMILY |
| `forge/research/allocation.py` | The research budget model and its adaptive re-weighting |
| `forge/research/information.py` | Novelty × evidence gap ÷ cost; ranks what to spend compute on |
| `forge/research/followup.py` | Failure and finding → derived, falsifiable hypotheses |
| `forge/research/synthesis.py` | Hypothesis + mechanism → `StrategyDefinition`, composed from the IR's closed feature set |
| `forge/research/campaign.py` | Campaign objective, budgets, stopping criteria, progress |
| `forge/research/literature.py` | arXiv + Crossref retrieval with strict provenance and claim extraction |
| `forge_api/director.py` | The one place that decides what the engine tries next |
| `forge_api/campaigns.py` | HTTP surface for campaigns, frontier, hypotheses, promotion queue |

### The single integration point

`_cycle` currently begins with a template draw. It will instead begin with:

```python
candidate = self.director.next_candidate(rng, worker, policy)
```

`ResearchDirector.next_candidate` returns a `Candidate` carrying
`template_key`, `parameters`, `hypothesis_id`, `search_kind`, `frontier_item_id`
and `research_sources`. Producing one may, when the allocator draws a discovery
bucket, first register a family and a template — through `FamilyRegistry.create`
and `TemplateStore.create`, the existing objects, with the existing checks.

Everything downstream of that line is untouched. If no director is attached, the
old random draw remains as the fallback, so the engine keeps working with no
campaign running.

---

## Part 3 — Design decisions worth stating

**The frontier never prunes.** `UNTESTED` is not `FAILED`, and only
`ResearchMemory` — which acts on a classified `FailureClass` — may decline to
spend compute. The frontier is a map of what is known; the memory is the only
thing with a veto. Merging them would let "nobody looked" delete candidates.

**A finding is never evidence.** `ResearchKnowledge` already enforces this
(promotion requires the statement to appear verbatim in a stored artifact, and
`is_evidence` is `False`). Derived hypotheses inherit it: a follow-up question
generated from a failure enters the graph as `UNTESTED`, never as a result.

**Novelty is deterministic.** Similarity is Jaccard over character shingles and
over structural feature sets, not an embedding. It runs offline, gives the same
answer twice, and can be tested. A model may *propose*; the deterministic layer
decides whether the proposal is new.

**Generated families must earn admission.** propose → novelty check → data-capability
check → synthesise definition → validate IR → render Python → static guard →
IR/Python agreement → smoke test on synthetic bars → conformance → lookahead →
cheap pilot → only then into the catalogue. A failure at any step is recorded on
the frontier as a reason, not swallowed.

**No fabricated citations.** `literature.py` only ever stores what a fetch
returned: URL, title, source, date when the API supplied one, retrieval
timestamp, and claims extracted from the returned abstract by span, never
paraphrase. With no network, the module returns nothing and the frontier item is
marked `BLOCKED_BY_DATA` with the reason. It does not invent a paper.

**Data capability is checked before a hypothesis is scheduled, not after.**
`Family.blocked_by` already computes this from `SUPPORTED_DATA`. A hypothesis
needing L2, options, ticks or news becomes `BLOCKED_BY_DATA` with the missing
capability named, and never consumes an experiment slot.

---

## Part 4 — Risks and migrations

| Risk | Mitigation |
|---|---|
| A second architecture growing beside the first | The director is the only new decision point; every execution stage is the existing one |
| Generated templates degrading the catalogue | `TemplateStore.create` already guards, smoke-tests and rejects; admission additionally requires a pilot |
| Trial inflation gaming the judge | Every generated candidate still increments `Experiments.count`, which is what G5's deflation reads. Discovery cannot launder a trial count |
| Novelty theatre — `mean_reversion_2` | `novelty.py` compares mechanism text, feature sets and parameter roles, and a proposal below the structural threshold is refused as PARAMETER, with the nearest neighbour named |
| Thread safety | New stores follow the existing pattern: one SQLite file, `closing(connect())`, short transactions, `threading.RLock` for in-memory caches |
| Schema migration | Additive only, via the `_migrate` idiom already used by `Experiments`. Existing workspaces open without ceremony |

No destructive migration is required. Every new store is a new file in the
workspace data root; no existing table changes shape.

---

## Part 5 — Delivery order

1. Frontier, hypothesis graph, novelty — the substrate, with tests.
2. Allocation and information value.
3. Follow-up generation from failures.
4. Synthesis: hypothesis → `StrategyDefinition` → template admission.
5. Literature retrieval with provenance.
6. Campaign store, director, promotion queue.
7. Engine integration at the one line.
8. HTTP surface and the campaign event stream.
9. Workspace manager: default, export/import, version history, autosave.
10. UI: the research campaign view and the workspace manager.
11. The commercial-viability integration test.

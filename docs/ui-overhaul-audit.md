# AlgoForge UI overhaul audit

Date: 2026-09-07  
Branch: `main`  
Scope: desktop research workstation, preserving backend semantics

## Baseline findings

| Area | Existing strength | Gap addressed by this overhaul |
| --- | --- | --- |
| Research lifecycle | Missions, experiments, strategies, validation, evidence, lineage, research loop and MCP actions already exist | Top-level navigation presented them as an undifferentiated row of tabs |
| Evidence integrity | Dossiers expose labels, hashes, gates, caveats and explicit absence reasons | Overview used marketing copy and decorative motion rather than lifecycle status |
| Agent control | Dynamic role discovery, worker controls, bounded dispatch and citations exist | Full-screen orbital diagram over-emphasised agents and was fragile at changing role counts |
| Validation | CPCV, walk-forward, robustness and prop Monte Carlo are implemented as distinct capabilities | These distinctions were not reinforced in global navigation and context |
| Data | Dataset and research endpoints exist | No first-class, read-only Data Health workspace |
| Runs and memory | Run records and research memory endpoints exist | They were supporting details rather than navigable first-class records |
| Accessibility | Focus styles and reduced-motion base rules exist | Tab buttons did not provide stable deep links or global keyboard search |
| Performance | Views are lazy loaded and query-cached | Decorative SVG animation and broad visual effects added work without information value |

## Research-derived product decisions

- Adopt Qanat's graph-led inspection and shared service-layer principle, not its execution engine.
- Adopt Auto-Quant-V2's durable hierarchy of workspace, research question, experiment, immutable run, review and dossier.
- Adopt WSB Alpha's fail-closed research sequence and explicit abandonment evidence, not its source code or execution model.
- Keep AlgoForge's existing engine. New screens are read-only projections over current APIs plus existing bounded actions.

## Implemented information architecture

- Research: Overview, Missions, Experiments, Strategies, Runs
- Validation: Validation Lab, Evidence
- Research Memory: Memory, Lineage
- Data: Data Health, Research Library
- Autonomous: Engine Pipeline, Agent Command
- System: Prop Simulation, Console, Settings

## Delivered in the second pass

| Area | Change | Evidence |
| --- | --- | --- |
| Typography | Fonts self-hosted (`@fontsource-variable/inter`, `@fontsource/ibm-plex-mono`). The previous build reached a Google Fonts CDN at runtime, so a private local-first application both called off-box and fell back to Consolas whenever offline. | `base.css`, `main.tsx` |
| Type roles | 79 label-sized declarations moved off the monospace face onto Inter; mono is now data only — figures, hashes, ids, timestamps, code. Label floor raised from 9px to 10/11px. | `styles/*.css`, `tokens.css` |
| Overview | Marketing hero replaced by a mission strip, an eight-figure research counter band, an evidence-ranked candidate leaderboard, a live research timeline and a research-memory summary. | `views/Overview.tsx` |
| Evidence ranking | Candidates rank by evidence tier before result. A run computed under legacy price-point units ranks below every current measurement instead of above genuine holdout evidence. | `Overview.tsx`, `StrategyCatalogue.tsx` |
| Strategies | Card list replaced by a windowed, sortable, filterable catalogue over all 399 records with multi-select comparison; the detail pane is one click away instead of the landing state. | `views/StrategyCatalogue.tsx` |
| Global search | `Ctrl+K` indexes views, strategies, experiments, runs, constraints and datasets, grouped, with arrow-key navigation. Record queries only run while the palette is open. | `components/CommandPalette.tsx` |
| Copy | Slogan headlines removed from Experiments, Evidence, Validation, Runs, Memory, Data Health and Research Library; each replaced by one informational line. The context bar already names the view. | `views/*.tsx` |
| Class collision | The engine's session counters shared the class name `.counter` with the new overview band, so two stylesheets fought over one element. Renamed and compacted to a single strip. | `Engine.tsx`, `research.css` |
| Performance | `/strategies` re-read and re-parsed every trade of every strategy's newest run — 399 full artifacts per request — to take `len(trades)`. Added a memoised projection: **6.4s → 0.22s warm**. | `activity.py`, `strategies.py` |

## Outstanding — not delivered

These remain from the brief and are not started or only partly done:

- **Search-space visualisation (§12, §33)** — not built. Research memory currently holds zero
  constraints on this machine, so the region/pruning view has nothing real to draw.
- **Lineage graph (§11)** — not built. `/experiments/{id}/lineage` exists and the route renders,
  but the visual DAG is still a list. Zero experiments recorded here.
- **Data health matrix (§21)** — the dataset registry is real; the completeness / gap /
  timestamp-integrity matrix is not built.
- **Error taxonomy (§39)** — errors are honest but do not yet use the named AlgoForge classes.
- **Cold start** — the first `/strategies` call after an API restart still takes ~52s while the
  projection cache fills. Warm calls are instant. The Overview stays readable throughout because
  its counters no longer block on that payload, but the first Strategies visit shows a loading
  state for that long.

## Acceptance gates

- Stable deep links and `Ctrl+K` navigation.
- No blank Agent Command route; failures terminate in a recoverable error boundary.
- Real records or explicit loading, empty, error, or blocked states.
- No replacement engine and no duplicated execution path.
- Responsive at 1280, 1440, 1920, 2560 and 4K desktop widths.
- Reduced-motion support and semantic keyboard navigation.
- Frontend tests/build, backend lint/type/tests, browser smoke, and remote Git equality recorded at closeout.

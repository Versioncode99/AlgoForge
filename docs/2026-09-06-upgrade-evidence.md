# AlgoForge desktop and quant research upgrade — 6 September 2026

Status: implemented and locally verified; paper research only. No claim that this audit proves the absence of all bugs, that a new strategy has an edge, or that a competitor has been beaten. Code changes remain local and reviewable.

## What changed

The PC workstation now has Agent Command: eight selectable specialists, animated neural connections driven by task activity, current assignment/tool/duration/model, operational conclusions, source links, pause/resume and directed tasks. Research Brain searches scholarly references and shows provenance and replication gaps. The experiment queue shows bounded model proposals. Eight compute lanes show their actual policy, stage and candidate. Overview, Research Lab, Strategies, Prop Firm, Console and Settings share revised typography, surfaces, navigation and motion. PC verification covers 1280, 1440, 1920 and 2560 pixel windows; phone support is not a delivery target.

Specialist roles are research, hypothesis, strategy engineering, validation, risk, post-mortem, bulk and console. Each receives a quant-specific mission, evidence rules and tools. Current tasks and operational summaries are visible; private model reasoning is not exposed. Console conversations remain a separate interactive route; the command-page console specialist runs directed/automatic review tasks.

Eight engine policies replace four identical random-search streams: literature, momentum, reversion, volatility, parameter neighbourhood, parameter ablation, exploration and default-template replication. The last policy means reproduction of a local template, not reproduction of a published study. Model proposals are constrained to registered templates and finite, on-grid parameters. Arbitrary model or downloaded Python is not executed. Development results, parameters, costs and failures persist in SQLite and guide later variants. Attempts are deduplicated against data, code catalog and calculation version. Families are no longer permanently retired merely for repeatedly missing evidence.

The research scout uses Crossref's public scholarly API with bounded requests. It retains DOI links, metadata and abstracts when provided, with UNREVIEWED status. Eight curated primary references are seeded. Finding a title or abstract does not mean reading a full paper. Hypothesis/engineer roles can turn these sources into cited experiments; full-paper methods review remains a separate research requirement.

Three runnable statistical adaptations were added: multi-horizon volatility-normalized momentum, variance-ratio-gated reversion, and AR(1)/OU half-life reversion. There are now twelve executable templates. Dealer-exposure walls and factor-residual statistical arbitrage remain explicitly locked by missing options-positioning or aligned multi-asset data.

## Supplied material compared with AlgoForge

All four supplied files were read as source material, not operating instructions. `transcript.txt` and the SRT repeat the same 3PO-style demonstration. Their attractive strategy map and paper-to-code-to-validation narrative establish product ideas, not independently audited performance. We adopted the observable research pipeline and connected task interface. Promotional claims that a system can be sold or find many profitable strategies do not establish superiority.

`transcript 2.txt` describes finding an SSRN VWAP paper, inspecting the method and implementing a backtest. The useful principle is source → falsifiable hypothesis → explicit adaptation → costs → independent testing. AlgoForge now records source IDs with proposals and strategies. Its intraday futures adaptations still differ from the papers' instruments, sampling, portfolio construction and execution assumptions.

The ATrain transcript describes IV walls and dealer exposure, including strong containment/reversal claims. Options-implied range coverage, whole-path containment and conditional trading success are different probabilities. Dealer positioning also cannot be inferred from OHLCV alone. These claims remain unverified; an OHLCV approximation is not labelled an exposure strategy. Timestamped options surfaces, positioning assumptions and prospective levels are prerequisites.

## Research basis and counter-evidence

| Primary source | Application and limitation |
| --- | --- |
| [Moskowitz, Ooi and Pedersen — Time Series Momentum](https://www.aqr.com/insights/research/journal-article/time-series-momentum) | Economic motivation for trend persistence; an intraday single-contract signal is not a replication of their cross-market study. |
| [Moreira and Muir — Volatility Managed Portfolios](https://www.nber.org/papers/w22208) | Separate risk scaling from signal quality. A volatility filter is not the same as a volatility-managed portfolio. |
| [Lo and MacKinlay — stock-price random-walk tests](https://www.nber.org/papers/w2168) | Variance ratios motivate a diagnostic, not a ready-made futures reversal edge. |
| [Lo and MacKinlay — reappraisal of short-horizon contrarian profits](https://www.nber.org/papers/w2795) | Counter-evidence: apparent reversal profits require mechanism and cross-security analysis, not just a profitable z-score rule. |
| [Avellaneda and Lee — Statistical Arbitrage in the US Equities Market](https://math.nyu.edu/inmemoriam/avellaneda/AvellanedaLeeStatArb20090616.pdf) | Residual mean reversion requires aligned assets and factor construction. Our single-series OU diagnostic is explicitly an adaptation. |
| [Concretum author paper index](https://concretumgroup.com/papers/) | Reference intake for VWAP/intraday work; match original instruments, costs and timing before calling it replication. |
| [Bailey and López de Prado — Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | Selection and non-normal returns make the best backtest misleading. More trials require stronger evidence. |
| [Bailey et al. — Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | Compare selection behaviour across partitions; a single chosen winner is insufficient. |

## Material audit fixes

- Futures point P&L was treated as dollars. The runtime now resolves actual dataset contract units: NQ $20/point, MNQ $2, ES $50, MES $5, GC $100 and MGC $10. Slippage uses the actual tick size and multiplier. Sources: [CME NQ specifications](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.contractSpecs.html?videoId=6398486112112), [CME micro equity contracts](https://www.cmegroup.com/education/courses/micro-e-mini-futures/micro-e-mini-futures-products-overview), [CME gold specifications](https://www.cmegroup.com/articles/files/2023/a-golden-opportunity-revisiting-gold-futures-and-options-webinar-slides.pdf). Unknown contracts fail closed.
- Open positions could disappear at the end of a sample; final fills now settle and carry an explicit end-of-data reason.
- Results now carry `contract-units-v2`. Legacy calculations are excluded from new judge/prop research claims and shown as requiring rerun. Original artifacts are preserved.
- Manual validation previously accepted the entire dataset. Parameter validation now uses development bars only; saved evidence is bound to code hash, split ID and calculation version. Stale or unmatched evidence is absent, not favourable.
- The engine now invokes the validation stack for viable candidates before requesting a final verdict. Its automated validation grid is deliberately small (candidate plus one neighbour), not an exhaustive plateau analysis.
- Stop/restart races, deletion of active candidates, reused strategy IDs and missing persisted candidate parameter defaults were repaired. Activity/index writes are synchronized; malformed results fail safely.
- Bar arrays are read-only and private attribute access/overbroad imports are rejected. This static guard is not an OS security sandbox; strategy source remains a trusted-local editing surface.
- Session-boundary lookup now uses binary search. Eight workers share loaded data and partitions. Lazy views and modular charts reduced initial JS from approximately 1.46 MB to 256 KB uncompressed. The separately loaded strategy/chart chunk remains about 604 KB and triggers a build-size advisory.
- Settings no longer claim dollar limits halt work without billing metering. Specialist limits actually enforced are 48 calls per UTC day, two simultaneous model requests and 1,600 requested output tokens. Stored USD values are explicitly planning preferences.

## Verification evidence

- Backend: 216 pytest tests pass; ruff and strict mypy pass (69 source modules).
- Frontend: seven Vitest tests and production build pass.
- Browser: fourteen desktop Playwright checks pass, including real backtest/job completion, strategy editing guards, prop matrix, navigation, specialist task controls, research filtering, reduced motion and four PC window sizes. No page errors in the specialist control flow.
- Live Crossref search: eight references returned in 1.41 seconds, stored in the isolated QA library. Receipt: `artifacts/scholar-live-check.json`.
- Live configured provider: `glm-5.3-flash` returned a valid source-linked momentum experiment in 12.58 seconds. Receipt: `artifacts/model-smoke/report.json`.
- Eight-worker real NQ smoke: 8 cycles, 8 candidates, 11 backtests, 0 engine errors; the live proposal was dispatched. All 8 candidates rejected; no holdout pass. Receipt: `artifacts/eight-worker-smoke-1788721321/report.json`. This is a functional smoke on 8,000 bars, not a full-history experiment or independent performance study.
- Desktop image: `artifacts/agent-command-pc-1920.png`.

## Remaining empirical boundaries

Run fresh development and validation experiments before using old statistics. Fills are next-open models with commissions and slippage, not exchange-microstructure or NinjaTrader-calibrated executions. Intratrade mark-to-market drawdown, full-paper replication, broad parameter robustness and prospective strategy verification require further evidence. Eight workers improve exploration capacity; they cannot manufacture edge. No live orders, broker connection, deployment or profitability promise is included.

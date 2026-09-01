# AlgoForge ten-wave vertical slice — release evidence

Date: 2026-09-01

Status: working local research vertical slice; not a production quant engine.

## Delivered

- Immutable preregistration, run, calculation-trace, and decision contracts backed by SQLite.
- Point-in-time market-data contracts, provenance receipts, G0 validation, and sample MNQ fixtures.
- Sweep/truth-tier separation and a deterministic evidence judge with auditable metric traces.
- Fixed-seed stationary-block risk simulation, regimes, VaR/CVaR, and explicit sparse-tail warnings.
- Separate challenge and funded rule fixtures for Topstep/Lucid-shaped $50K research accounts.
- Four partitioned, hash-chained memory lanes and cited Researcher/Critic/Risk Auditor debate.
- ForgeKeeper candidate licence lanes, protected paths, canary gates, immutable releases, and human activation.
- React research workstation with Overview, Verdict, Regimes, Risk, Prop Firm, Agents, and Evolution surfaces.
- Ten standalone wave prompts, each mechanically verified at exactly 250 whitespace-delimited words.

## Verification receipt

- Ruff: PASS, 31 Python source modules checked.
- mypy strict: PASS, 31 Python source modules checked.
- pytest: PASS, 27 tests.
- Vitest: PASS, 1 component/integration test.
- Playwright: PASS, 5 desktop/mobile interaction tests; 1 expected mobile screenshot skip.
- Production frontend build: PASS.
- API smoke: PASS on `127.0.0.1:8765`.
- Web smoke: PASS on `127.0.0.1:5173`.
- Visual inspection: PASS after correcting mobile navigation labels, missing win-rate output, and doubled prop equity.

## Deliberate boundaries

`[VERIFIED]` Capability policy disables live orders, account creation, payment, KYC, and production broker credentials. Agent text cannot mutate judge values. ForgeKeeper cannot activate a release without a passing canary and explicit human approval.

`[UNCONFIRMED]` Bundled Topstep/Lucid rule files are intentionally marked unverified, expire immediately after the research date, and are runnable only through an explicit research override. Official current contracts must replace them before reliance.

`[UNVERIFIED]` No real Databento, NinjaTrader, exchange, prop-firm, OOS, or forward stream has been imported. All displayed results are sample, synthetic, and uncalibrated.

`[PARTIAL]` Statistical gates are a deterministic vertical slice, not the complete CPCV/DSR/PBO/SPA/holdout architecture. The full research architecture remains the target specification.

## Known technical debt

- The ECharts bundle is about 1.39 MB minified; route/chart code-splitting is still required.
- FastAPI's current TestClient emits an upstream httpx deprecation warning.
- The release supervisor stores local pointer files but does not yet run an isolated container canary.
- ForgeKeeper repo scouting has discovery fixtures, not a scheduled GitHub scanner.
- Data adapters are interfaces and fixtures; production-grade Databento/NinjaTrader adapters remain gated.

## Start and verify

Run `scripts/Start-AlgoForge.ps1` and open `http://127.0.0.1:5173`. Run `scripts/Test-AlgoForge.ps1` for the deterministic verification suite. Run `npm run test:e2e` while the local services are active for browser interaction coverage.

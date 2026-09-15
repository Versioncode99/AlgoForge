# Horizon — Phase 0 audit

Everything below was measured on this checkout before any file was edited, and
the commands are recorded so a reader can repeat them rather than trust them.

## Repository snapshot

| Fact | Value |
| --- | --- |
| Remote | `https://github.com/Versioncode99/AlgoForge` |
| `git rev-parse HEAD` at start | `4e2025b6f9121463bf50a42c7e1daea083620b76` |
| `git rev-parse origin/main` | `4e2025b6f9121463bf50a42c7e1daea083620b76` |
| `git ls-remote origin refs/heads/main` | `4e2025b6f9121463bf50a42c7e1daea083620b76` |
| Working tree at start | clean |
| Implementation branch | `Horizon`, created from verified `origin/main` |

`.playwright-mcp/` did not exist in this checkout (a fresh clone), so there was
nothing user-owned to preserve. Nothing was deleted from `.git`, no pack files
were touched, and no `git gc`, `git maintenance` or repair command was run.

## Branch reconciliation

Measured with `git merge-base`, `git rev-list --count`, `git merge-base
--is-ancestor`, `git cherry` and a file-by-file content comparison.

| Branch | Head | Ahead | Behind | Ancestor of main | `git cherry` unique | Decision | Reason |
| --- | --- | ---: | ---: | --- | ---: | --- | --- |
| `claude/algoforge-refactor-ui-aht1p6` | `80b70c9` | 0 | 111 | yes | 0 | retain | fully merged; deleting it is the owner's call, not this branch's |
| `claude/algoforge-research-expansion-s1sznx` | `fd162a1` | 8 | 51 | **no** | **8** | **retain, superseded** | see below |
| `claude/cloud-environment-overview-o7an7t` | `4874307` | 0 | 100 | yes | 0 | retain | fully merged |
| `claude/four-mode-workspace-system-ndvaqr` | `1c0d9da` | 0 | 108 | yes | 0 | retain | fully merged |
| `claude/nifty-ritchie-1rf1tk` | `24b9491` | 0 | 82 | yes | 0 | retain | fully merged |
| `claude/wonderful-goldberg-twi9xv` | `5656a19` | 0 | 92 | yes | 0 | retain | fully merged |
| `claude/zen-hawking-nm63gx` | `0122fcc` | 0 | 1 | yes | 0 | retain | identical tree to main |
| `feat/autoresearch-v2` | `401fe2a` | 0 | 53 | yes | 0 | retain | fully merged |
| `feat/industry-standard-validation` | `fd40876` | 47 | 154 | **no** | **47** | **retain, unreviewed** | *no merge base with main at all* — unrelated history. Not reconciled here and not deleted. |
| `feat/openterminal-workstation-integration` | `b6b5f8c` | 0 | 74 | yes | 0 | retain | fully merged |

**Nothing was deleted.** No branch decision in this work removes a ref.

### `claude/algoforge-research-expansion-s1sznx`, examined per commit

The pre-implementation audit flagged this branch as carrying eight commits that
`git cherry` reports as unique, on research vocabulary, research grammar,
retrieved-claim conversion, model-routing settings, campaign output persistence,
experiment accounting and an architecture review — and asked that it not be
deleted until each capability was mapped onto main.

It was mapped by content, not by name. Every substantive file the branch added
is **byte-identical on main**:

| File | Branch | main | Comparison |
| --- | ---: | ---: | --- |
| `packages/forge/research/grammar.py` | 1491 | 1491 | identical (sha256) |
| `packages/forge/research/leads.py` | 458 | 458 | identical |
| `packages/forge/research/mechanisms.py` | 379 | 379 | identical |
| `packages/forge/research/effectiveness.py` | 223 | 223 | identical |
| `packages/forge/strategy/primitives.py` | 691 | 691 | identical |
| `apps/web/src/views/Vocabulary.tsx` | 185 | 185 | identical |
| `apps/web/src/views/AgentEffectiveness.tsx` | 79 | 79 | identical |
| `tests/research/test_grammar.py` | 497 | 497 | identical |
| `scripts/campaign_comparison.py` | 354 | 354 | identical |
| `scripts/measure_vocabulary.py` | 302 | 302 | identical |
| `docs/RESEARCH_EXPANSION_REPORT.md` | 416 | 416 | identical |
| `apps/web/src/views/ModelRouting.tsx` | 420 | **585** | **main is ahead** — it carries the routing-recommendations panel the branch never had |

**Conclusion: the branch is fully superseded.** `git cherry` reports its commits
as unique because the work reached main through a squash merge, which rewrites
the commit ids while preserving the trees — exactly the case the audit warned
not to infer obsolescence from, and exactly the case a content comparison
settles. There is no regression, no still-useful work, and one file where main
is strictly ahead. It is **retained** regardless; recovery reference
`fd162a1e2842c4661209e0181523c831bf28e970`.

`feat/industry-standard-validation` is a different matter: it shares **no merge
base with main**, so it is a parallel history rather than a stale branch. It is
out of scope for this work and is retained untouched. Recovery reference
`fd4087634ef0cfa4ef55ccdd93e004c17944c2cb`.

## Baseline verification, before any edit

| Check | Command | Result |
| --- | --- | --- |
| Ruff | `.venv/bin/python -m ruff check .` | pass |
| Mypy (strict) | `.venv/bin/python -m mypy` | pass, 209 source files |
| Pytest | `.venv/bin/python -m pytest -q` | **pass**, 9m23s, exit 0 |
| Vitest | `npm --prefix apps/web run test` | pass, 38 files, 436 tests |
| Typecheck | `npm --prefix apps/web run typecheck` | pass |
| Build | `npm --prefix apps/web run build` | pass |

### The reported baseline failure did not reproduce — and was real anyway

The pre-implementation audit recorded
`tests/api/test_agent_proposals.py::test_an_unknown_template_names_the_ones_that_exist`
failing, with the refusal naming only generated displacement-reversion templates
and omitting `momentum_breakout`. **The full suite passed on this checkout**
(exit 0, above), so the failure did not reproduce here.

It was still a real defect, and the audit's description named its cause exactly.
`AgentService.propose` answered an unknown template with
`", ".join(sorted(TEMPLATES)[:12])`. `TEMPLATES` is a process-wide dictionary
that `forge_api.director`, `forge_api.catalog` and `forge_api.actions` register
generated templates into at run time. On a fresh process the twelve
alphabetically-first keys *are* the whole shipped catalogue and the test passes;
after a campaign has composed a few hundred variants with machine-generated
names, the twelve are all generated ones and the refusal names nothing the
proposer was meant to ask for. In a full pytest session whether that had
happened depended on which tests ran first — an order-dependent failure whose
cause was the production message, not the test.

Fixed in `packages/forge/strategy/catalogue.py`: the refusal leads with the
shipped catalogue, counts the generated remainder rather than truncating into
it, and offers a near match. `test_a_generated_catalogue_does_not_displace_the_
shipped_names` registers two hundred `aaa_`-prefixed templates and asserts the
shipped names survive, which reproduces the reported failure deterministically.
The same unbounded listing in `actions.py` (`', '.join(sorted(TEMPLATES))`,
several hundred names in one error string) was fixed with it.

## Observed debt, carried forward

| Item | Status |
| --- | --- |
| React `act(...)` warnings in web tests | present at baseline; see the verification section of the final report |
| ECharts zero-width/height warnings | present at baseline |
| Production chunks over 500 kB | present at baseline; `AnalysisChart` 578 kB, `installCanvasRenderer` 586 kB, `index` 602 kB |
| CI runs no browser or Electron E2E | unchanged by this work |

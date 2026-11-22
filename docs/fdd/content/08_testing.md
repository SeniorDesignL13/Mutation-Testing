# 18. Test Plan and Results

## 18.1 Strategy

Testing in MTLJ has an unusual second layer. The code must be correct like any software, and the ground truth it produces must also be correct, or every result is wrong even when every test passes. The test plan therefore covers both. {tab:test_levels} lists the levels.

Table: Test levels {#test_levels}
| Level | What it checks | How | When it runs |
|---|---|---|---|
| Unit | Each operator, data model, loader, statistic, and engine part in isolation | pytest, one test module per source module, mirroring the source layout | Every pull request and push to main |
| Contract | Every judge adapter behaves the same way: score type and range, empty context, identity fields, verdict rules, no hidden caching of variance | One shared parametrized suite run against every adapter | Every pull request |
| Statistical | Every formula against synthetic data with a known answer, and against the feasibility study's raw scores | Seeded simulations; the spike's tables reproduced exactly | Every pull request |
| Integration | The engine end to end, from records to results.json, against a stub adapter and against recorded DeepEval responses | Replay of 765 recorded scorings with the network blocked | Every pull request |
| Live integration | A real call to DeepEval on gpt-4o-mini | Opt-in pytest marker (`-m integration`) and an API key | Before releases and after DeepEval upgrades |
| Database | Connection, ORM round trip, and that the models match the migrations | Tests against the same PostgreSQL 16 image as Docker Compose | Every pull request |
| Frontend | Lint, type checking, and a production build | ESLint, vue-tsc, `nuxt build` | Every pull request |
| End-to-end (SD2) | Dashboard views against a seeded database, including accessibility checks | Browser automation with axe-core | Every pull request touching the dashboard |
| Ground truth | Whether edits really change or preserve meaning as labeled | Human validation study (Section 12) | Once before the full audit, again after any operator fix |
| Pilot audits | The whole pipeline on a small real run before every large one | `mtlj audit run --limit 20` on gold and corpus records | Before each full audit |

## 18.2 Continuous Integration

Every pull request runs two CI jobs on GitHub Actions. The Python job checks that `uv.lock` matches `pyproject.toml`, installs the locked dependencies, starts PostgreSQL with Docker Compose, then runs Ruff linting, Ruff format checking, and the full pytest suite. The frontend job installs from the lockfile and runs ESLint, type checking, and a production build. A pull request cannot merge to `main` without one approving review and both jobs passing. Guard tests (`tests/test_ci_guard.py`) make sure that no test in CI can call a real judge: tests that need a live model must carry the integration marker, and the network is blocked during replay tests. The response cache and recorded fixtures mean CI never needs an API key and never pays for a model call.

## 18.3 Results

{tab:test_results} shows the results of the CI run on `main` at commit `1aaeeea` (November 19, 2026, the merge of the numeric perturbation operator). All 1,127 selected tests passed in 68.9 seconds.

> NOTE: Sprint 4 added tests for the new operators, the validity filter, the Ragas adapter, resume, and rate limiting. Replace this table with the CI run on main just before submission (the per-file counts come from the pytest output in the CI log). One test, the live DeepEval call, was deselected by design because it needs an API key. {fig:tests_chart} summarizes the counts by area.

![Passing tests by area in the CI run of November 19, 2026.](diagrams/chart_tests.png){width=5.2 #tests_chart}

Table: Test results by module (CI run on main, November 19, 2026) {#test_results}
| Test module | Tests | Passed | Failed |
|---|---|---|---|
| `tests/api/test_health.py` | 1 | 1 | 0 |
| `tests/api/test_migrations.py` | 1 | 1 | 0 |
| `tests/api/test_testing_routes.py` | 4 | 4 | 0 |
| `tests/cli/test_cli.py` | 44 | 44 | 0 |
| `tests/data/test_mutants.py` | 112 | 112 | 0 |
| `tests/data/test_ragtruth.py` | 28 | 28 | 0 |
| `tests/data/test_records.py` | 14 | 14 | 0 |
| `tests/engine/test_audit.py` | 22 | 22 | 0 |
| `tests/engine/test_audit_config.py` | 43 | 43 | 0 |
| `tests/engine/test_battery_config.py` | 25 | 25 | 0 |
| `tests/engine/test_cache.py` | 35 | 35 | 0 |
| `tests/engine/test_fixtures.py` | 42 | 42 | 0 |
| `tests/engine/test_generator.py` | 25 | 25 | 0 |
| `tests/engine/test_results.py` | 56 | 56 | 0 |
| `tests/engine/test_retry.py` | 76 | 76 | 0 |
| `tests/engine/test_runner.py` | 49 | 49 | 0 |
| `tests/judges/deepeval/test_faithfulness.py` | 18 | 18 | 0 |
| `tests/judges/deepeval/test_faithfulness_replay.py` | 1 | 1 | 0 |
| `tests/judges/test_base.py` | 58 | 58 | 0 |
| `tests/judges/test_callable.py` | 20 | 20 | 0 |
| `tests/judges/test_contract.py` | 63 | 63 | 0 |
| `tests/operators/degrading/test_entity_swap.py` | 20 | 20 | 0 |
| `tests/operators/degrading/test_numeric_perturbation.py` | 61 | 61 | 0 |
| `tests/operators/preserving/test_reformatting.py` | 80 | 80 | 0 |
| `tests/operators/preserving/test_verbosity_padding.py` | 4 | 4 | 0 |
| `tests/operators/preserving/test_verbosity_padding_levels.py` | 23 | 23 | 0 |
| `tests/operators/test_registry.py` | 26 | 26 | 0 |
| `tests/stats/test_bootstrap.py` | 34 | 34 | 0 |
| `tests/stats/test_correction.py` | 25 | 25 | 0 |
| `tests/stats/test_detection.py` | 22 | 22 | 0 |
| `tests/stats/test_dose_response.py` | 37 | 37 | 0 |
| `tests/stats/test_paired.py` | 15 | 15 | 0 |
| `tests/stats/test_spurious.py` | 16 | 16 | 0 |
| `tests/stats/test_variance.py` | 14 | 14 | 0 |
| `tests/test_ci_guard.py` | 7 | 7 | 0 |
| `tests/test_record_fixtures.py` | 6 | 6 | 0 |
| **Total** | **1,127** | **1,127** | **0** |

The single replay test in `test_faithfulness_replay.py` is worth noting because it covers the most ground. It scores all 20 gold-set records and their 133 mutants five times each through the full runner, using the 765 recorded DeepEval responses, and checks the results without touching the network.

## 18.4 Acceptance Tests

{tab:acceptance} maps each user story to the test that shows it is met. Stories whose features are planned for Senior Design II have planned acceptance tests.

Table: Acceptance tests by user story {#acceptance}
| Story | Acceptance test | Result |
|---|---|---|
| US-01 | A new adapter passes the shared contract suite; the DeepEval adapter passes it and the replay test | Pass |
| US-02 | Loading corpus v1 verifies the checksum and yields 919 records; a record with a blank field is rejected | Pass |
| US-03 | A battery file selects operators and intensities; an unknown operator name stops the run with exit code 2 | Pass |
| US-04 | Regenerating any mutant from its record gives byte-identical text; a changed operator version is refused | Pass |
| US-05 | A run with `--samples 5` stores five scorings per text, and a later run with more samples calls the judge only for the new ones | Pass |
| US-06 | A run killed halfway keeps every finished score on disk, and `mtlj audit resume` completes it without rescoring | Pass |
| US-07, US-08 | On the feasibility data, the statistics reproduce the documented tables (12 of 20 detected; pooled σ 0.054) | Pass |
| US-09 | `mtlj audit run --dataset gold --limit 3` completes and prints the summary; `audit show` reprints it | Pass |
| US-10, US-11 | The profile and comparison views render a seeded run correctly, pass axe-core checks, and work by keyboard | Planned (SD2) |
| US-12 | The validation study meets the bars in {tab:validation} | Planned (SD2) |
| US-13 | The test suite passes with the network blocked | Pass |
| US-14 | A second identical run is answered entirely from the cache, with a 100% hit rate in the run summary | Pass |

## 18.5 Senior Design II Test Plan

Senior Design II adds three kinds of tests. API tests will call every results endpoint against a database seeded from the gold-set run directory, checking response shapes against the Pydantic schemas, pagination, filters, and error codes. Dashboard end-to-end tests will load each view in a headless browser against the same seeded API, check that the numbers shown match `results.json`, and run axe-core accessibility checks. A failure there blocks the merge like any other test. Finally, the pilot audits act as system tests: each is run on 20 records before any full audit, and its results are reviewed by the Product Owner before the larger run starts.

## 18.6 Defect Handling

A defect found in review or testing gets a Jira bug linked to the story it affects and a failing test that reproduces it, which must pass before the fix merges. A defect in an operator also requires a version bump (Section 8.1), so results produced before and after the fix can never be confused.

# 19. Legal and Ethical Considerations

## 19.1 Data Licensing and Use

MTLJ uses only public research data. RAGTruth is released under the MIT license, which permits the project's use. Its question-answering records are built on MS MARCO {ref:msmarco}, whose terms allow non-commercial research use, and MTLJ's use (academic research and an open-source tool) falls within them. The frozen corpus is committed to the repository with a manifest recording its source and filter chain, and any future redistribution of the corpus outside the repository will be checked against both licenses. The corpus contains web-search questions and model answers. It holds no information about the team's users, and MTLJ collects no personal data.

## 19.2 Software Licenses

Every dependency is open source under a permissive license (MIT, BSD, or Apache 2.0), including DeepEval, Ragas, spaCy, FastAPI, Nuxt, and PrimeVue. Llama 3.1, if used as a local judge, is distributed under Meta's community license {ref:llama}, which permits research use and requires attribution. The repository does not yet have a license file. The team intends to release MTLJ under the MIT license so others can use and extend it, and will add the file before the end of Senior Design I.

> NOTE: The team needs to agree on the license (MIT is proposed) and add a LICENSE file to the repository.

## 19.3 Third-Party Model Services

Hosted judge calls send the corpus text (public questions, passages, and answers) to OpenAI under its API terms {ref:openai}. No private or personal data is ever sent. The team's API keys are personal and are kept out of the repository, and each key should have a monthly spending limit set on the provider's dashboard.

## 19.4 Responsible Reporting of Judge Weaknesses

The project's main output is a public statement about where specific, named open-source judges fail. That carries a responsibility to be accurate and fair:

- **Report what was measured, with its limits.** Every rate is published with its interval, its comparison against chance, and the corpus, model, and settings it came from. Findings are framed as properties of a judge configuration on this corpus, not as general verdicts on a project.
- **Report results that do not support the pitch.** The team agreed in advance to publish results as measured even if judges turn out to be good, so the conclusions cannot be chosen after the fact.
- **Give maintainers notice.** Before publishing findings about a framework, the team will open an issue with that framework's maintainers describing the weakness and how to reproduce it with MTLJ. Every result is reproducible from its run directory.
- **Avoid misuse.** MTLJ shows which edits slip past a judge. In principle someone could use that knowledge to craft outputs that pass a weak judge. The same knowledge is what developers need to fix or replace the judge, the edits are simple and already documented in the research literature, and the tool tests judges rather than attacking deployed systems, so the team judges the benefit to outweigh the risk.

## 19.5 Use of AI in This Project

Generative AI is excluded from making ground truth by design (NFR-01). Team members used AI coding and writing assistants during development and in preparing this document, as permitted by the course. Every contribution was reviewed and, where needed, rewritten by team members, and every number in this document was checked against the repository, the Jira exports, or the CI logs.

# 20. Budget

MTLJ has no external sponsor, so every cost is paid by the team, and the design keeps costs low on purpose. Local models, the response cache, and recorded fixtures all exist partly to avoid paying for the same call twice. {tab:budget} lists the costs.

Table: Project budget {#budget}
| Item | Purpose | Cost so far | Expected for SD2 | Notes |
|---|---|---|---|---|
| OpenAI API (gpt-4o-mini) | Feasibility study, fixture recording, development, pilots, first full audit | About $100 (estimate) | About $150 | One full re-run after validation (about $50 per judge configuration), plus pilots and reruns of failed calls |
| Local inference (Ollama) | Free overnight audits | $0 | $0 | Team members' existing hardware |
| GitHub (repository, Actions, Dependabot) | Code hosting and CI | $0 | $0 | Free for public repositories |
| Jira and Confluence | Backlog, sprints, documentation | $0 | $0 | Free plan, up to 10 users |
| Figma | Mockups | $0 | $0 | Education plan |
| Discord | Daily communication | $0 | $0 | Free |
| Hosting | Public dashboard | $0 | $0 to $120 | $0 for a static site; up to about $20 per month for six months if a server is needed |
| **Total** | | **About $100** | **About $150 to $270** | |

> NOTE: Replace the "cost so far" estimate with the actual OpenAI spend from the billing page (it now includes the pilots and the first full audit), and record who pays for API usage.

The largest uncertainty is how many full audits the study needs. The cost scales linearly with the number of judge configurations, the number of samples per text, and the number of operators in the battery. Running both DeepEval settings on a local model instead of gpt-4o-mini would remove most of the API cost, at the price of longer runs.

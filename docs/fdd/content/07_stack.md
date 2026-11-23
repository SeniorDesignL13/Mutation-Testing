# 15. Technology Stack

## 15.1 Stack Summary

{tab:stack} lists every layer of the system, what it is used for, and why it was chosen. Versions are the ones pinned in the lockfiles at the end of Senior Design I.

Table: Technology stack {#stack}
| Technology | Used for | Why |
|---|---|---|
| Python 3.12, uv | Language, dependencies, virtual environments | Every judge framework is in Python; uv's lockfile keeps four machines and CI identical, and installs in seconds |
| Pydantic 2 | Data models and validation | One definition of each record, mutant, evaluation, and result, shared everywhere and validated at every boundary |
| Typer | CLI | Type-hinted commands with generated help; pairs naturally with Pydantic |
| asyncio, httpx, thread pool | Execution | Many judge calls at once without blocking; synchronous judge libraries run in worker threads |
| spaCy 3.8 (en_core_web_sm), NLTK | Operators | Named-entity recognition, noun phrases, and sentence splitting with a small, fast, deterministic model |
| NumPy, SciPy, statsmodels | Statistics | Bootstrap, binomial tests, Wilson intervals, likelihood fitting, and multiple-comparison correction |
| DeepEval 4.2.8, Ragas | Audit targets | The judges teams actually run; both adapters done |
| OpenAI API (gpt-4o-mini) | Hosted judge model | Cheap, widely used default for DeepEval |
| Ollama, vLLM | Local inference | Free local models; vLLM for larger batches if needed |
| SQLite | Response cache | Single-file key-value store, no service needed |
| PostgreSQL 16, SQLAlchemy 2 (async), asyncpg, Alembic | Results database | Relational integrity, JSONB, async access, and tracked migrations |
| FastAPI, Uvicorn | Web API | Async, typed, automatic OpenAPI documentation, built on Pydantic |
| Nuxt 4, Vue 3, PrimeVue 4, TypeScript | Dashboard | See {tab:fe_choice} |
| Plotly.js | Charts | Interval bands and error bars built in |
| Docker Compose | Development environment | One command starts the database, API, frontend, and optional Ollama |
| GitHub, GitHub Actions, Dependabot | Repository, CI, dependency updates | Branch protection, lint and tests on every pull request, automatic dependency updates |
| pytest, pytest-asyncio, Ruff | Testing and code quality | Fast tests, async support, and one tool for linting and formatting |
| ESLint, vue-tsc | Frontend checks | Style and type checking in CI |
| Jira, Confluence, Figma, Discord | Process | Backlog and sprints, documentation, mockups, and daily communication |
| PlantUML, Matplotlib | Documentation | Diagrams and charts in this document, generated from text so they can be reviewed and versioned |

## 15.2 Alternatives Considered

{tab:alternatives} records the main alternatives the team weighed for the core of the system. Database and frontend alternatives are in {tab:dbchoice} and {tab:fe_choice}.

Table: Core alternatives considered {#alternatives}
| Decision | Alternatives | Chosen and why |
|---|---|---|
| Package manager | pip with requirements files, Poetry, uv | uv: one tool for Python versions, environments, and a cross-platform lockfile, much faster than the others |
| Data models | dataclasses, attrs, Pydantic | Pydantic: validation and JSON serialization in one place, shared with FastAPI |
| Concurrency | Threads only, multiprocessing, asyncio | asyncio with a thread pool: cheap coordination of many waiting calls, while blocking judge libraries still work |
| NLP for operators | spaCy, Stanza, NLTK only, an LLM | spaCy small model: fast and deterministic. An LLM was ruled out because no language model may write edits (NFR-01) |
| Statistics | Hand-written, SciPy and statsmodels, R | SciPy and statsmodels: well-tested implementations in the same language as the rest of the system |
| CLI framework | argparse, Click, Typer | Typer: type hints become options and help text with little code |
| Test framework | unittest, pytest | pytest: fixtures, parametrization, and async support |
| Linting | flake8 with black and isort, Ruff | Ruff: one fast tool for all three jobs |

## 15.3 Key Design Decisions

Several decisions shaped the design, and some changed earlier plans. {tab:decisions} records them with their dates so the reasoning is not lost in chat threads. Each statistical decision is also recorded in `docs/metrics.md`.

Table: Key design decisions {#decisions}
| When | Decision | Why |
|---|---|---|
| Sep 2026 | No language model anywhere in making ground truth | A model-made label would bring back the trust problem the project exists to remove |
| Sep 2026 | PostgreSQL from the start instead of SQLite | The design already committed to it, Docker makes it one command, and switching later would be rework |
| Oct 2026 | RAGTruth as the corpus | Long answers that can be padded and reformatted, plus human hallucination labels |
| Oct 2026 | Entity swaps must contradict the source | DeepEval counts claims the context never mentions as faithful, so other swaps would not move the score |
| Oct 20, 2026 | Store claim and verdict counts with every score | The feasibility study showed many misses were contradictions marked "unclear" |
| Oct 20, 2026 | Five samples per text as the default | Entity swaps moved scores about three times the noise floor in the feasibility study |
| Oct 20, 2026 | Pooled variance for the noise floor | Noise was concentrated in a few records, which averaging standard deviations understates |
| Oct 23, 2026 | Verdict counts may add up to less than the claim count | DeepEval skipped a verdict in about 5% of calls in the study |
| Nov 4, 2026 | Spurious sensitivity is two-sided and read against α | A judge that rewards harmless edits is as biased as one that punishes them; noise alone flags about 5% |
| Nov 6, 2026 | Wilson and record-level bootstrap intervals, with the headline chosen by size | The bootstrap captures uncertainty in τ but covers poorly below 100 mutants |
| Nov 6, 2026 | Benjamini-Hochberg across all tests in a run | An audit is a profile of many tests; controlling the share of false findings keeps power |
| Nov 2026 | Run directories are the source of truth; the database indexes them | Delivered a working audit before the schema; runs stay reproducible without a server |
| Nov 2026 | Operators carry an integer version that is part of every mutant ID | Fixes to an operator can never silently change results or mix with old mutants |
| Oct 2026 | Dashboard is read-only; audits start from the CLI | Keeps the dashboard off the critical path for the research result |

# 16. Large Language Models

## 16.1 Where Language Models Are Used

A generative model is used in exactly one place: as the judge under test. No language model writes edits, filler text, or expected labels. This held even for the feasibility study, where all sixty edits were written by hand. MTLJ does not train or fine-tune any model, so there is no training data, training run, or training accuracy in the usual sense. The "accuracy" that matters is the judge's, which is exactly what MTLJ measures.

One other language tool takes part, but it is not generative. The operators use spaCy's small English pipeline, `en_core_web_sm` 3.8 {ref:spacy}, to find entities, sentences, and noun phrases. It is a pretrained statistical model distributed by Explosion AI under the MIT license, about 12 MB on disk, and it runs on a CPU in milliseconds per answer. Its published named-entity accuracy is in the mid-80s (F-score) on the OntoNotes benchmark. The gold-set mislabels described in Section 8.9 show what that error rate looks like in practice, which is why the validity filter and the human review exist.

> NOTE: Confirm the en_core_web_sm 3.8 entity F-score and model size on the spaCy models page before submitting.

## 16.2 Judge Models

Judges are chosen because teams actually use them, not because they are the best. The main target is DeepEval's faithfulness metric: it is widely used, and its claim-by-claim design lines up well with the operators. Ragas faithfulness is the second target, because it computes nominally the same metric a different way. The team selected the judges for the study in Sprint 4 (MTLJ-88); {tab:judges_selected} in Section 11.5 lists them with the reason for each. The model behind a judge is treated as part of its identity. {tab:models} lists the models in use or planned.

Table: Judge models {#models}
| Model | Source | Role | How it runs | Approximate resources and cost |
|---|---|---|---|---|
| gpt-4o-mini | OpenAI API {ref:openai} | Default hosted judge model; used in the feasibility study and recorded fixtures | Hosted, called through DeepEval | No local memory; about $0.15 per million input tokens and $0.60 per million output tokens |
| Llama 3.1 8B Instruct, 4-bit quantized (Q4_K_M) | Meta {ref:llama}, through the Ollama library {ref:ollama} | Local judge model for free overnight audits | Ollama container on a team member's machine | About a 4.9 GB download; roughly 6 to 8 GB of GPU or system memory |
| Llama 3.2 3B (development) | Meta, through Ollama | Small model for testing the Ollama integration | Ollama container | About a 2 GB download; runs on most laptops |

> NOTE: Confirm the current gpt-4o-mini prices and the Ollama download sizes before submitting, and make this table match the judges in Section 11.5.

## 16.3 Speed, Cost, and Memory in Practice

The measured numbers so far come from gpt-4o-mini. The README's three-record pilot (3 originals and 20 mutants, 5 scorings each, 115 scorings in total) takes about four minutes and costs under fifteen cents at the default concurrency of four. That is about 0.13 cents per scoring, and about two seconds of wall-clock time per scoring with four calls in flight. Each DeepEval faithfulness scoring makes several model calls (claim extraction, then a verdict for each claim), which is why a single scoring takes several seconds.

The pilot audits (Section 11.4) measured more precisely. Across 765 recorded DeepEval scorings, a scoring took 8.46 seconds on average (median 8.30, 90th percentile 10.89), driven mostly by how many claims the answer has. A full corpus audit is 34,885 scorings per judge configuration: at most about $46 with gpt-4o-mini, and about 20.5 hours at four concurrent calls or 10.2 at eight, if the provider does not throttle. The replay process peaked at 283 MB of memory. Once the response cache is wired into the CLI (Section 9.3), a rerun of unchanged scorings costs nothing. A local quantized 8B model costs nothing per call but is slower per call on consumer hardware, so overnight runs are the expected pattern. The Ollama integration (MTLJ-92) is complete, and the pilot audits measured local throughput and memory on the team's machines.

> NOTE: Add the measured local numbers (seconds per scoring and memory used for the local model, and the hardware) and the live pilots' measured cost per scoring.

## 16.4 Prompt Shaping

The team deliberately does not touch the prompts of framework judges. Each judge is used exactly as shipped, at a pinned version, because the goal is to test judges the way teams run them. Editing the prompt would mean testing a different judge. Settings that teams can legitimately choose are treated as test conditions instead. DeepEval, for example, counts claims the context does not mention as faithful by default, and its `penalize_ambiguous_claims` option changes that, so the team runs both settings and reports the difference. For a custom judge, the prompt text is part of the judge's identity, so a prompt edit counts as a new judge version, which is what makes before-and-after comparisons possible. The judge receives three things for each text: the question, the source passages as context, and the answer. Only the answer is ever edited.

## 16.5 Handling Randomness

Judges do not always give the same score for the same input, even at low temperature. Instead of hiding that, MTLJ measures it. Every original and every edit is scored N times with the same settings, and a change only counts if it is larger than the judge's own measured variation on the originals (Section 10). Newer DeepEval versions offer a lower-variance scoring mode. Audits use the standard mode, because that is what teams deploy, and its variance is what MTLJ is built to measure.

# 17. Hosting and Deployment

## 17.1 Where the System Runs Now

MTLJ is a development and research tool, and in Senior Design I it runs entirely on the team's machines. One command, `docker compose up`, starts three containers: PostgreSQL 16 on port 5432, the FastAPI service on port 8000, and the Nuxt frontend on port 3000. A fourth container, Ollama on port 11434, starts only with the `models` profile. Code is mounted into the containers, so saving a file reloads the API and the website within seconds. The command-line audit runs in a uv-managed environment on the host and writes run directories and the response cache under `runs/`. Hosted judge calls go directly from the CLI to the OpenAI API, using a key kept in an untracked `.env` file.

The repository is hosted on GitHub as a public repository. GitHub Actions runs CI on every pull request and on every push to `main`, using the same PostgreSQL image as Docker Compose, so tests run against the real database. Dependabot opens pull requests for Python and npm dependencies weekly, and for Docker images and GitHub Actions monthly. Testing routes can be switched off with a setting, so a public deployment would expose only the results API. There is no deployed server and no remote database connection string yet. {fig:deploy} shows the current and target deployments.

![Deployment diagram: the current local setup, GitHub, and the target host for Senior Design II.](diagrams/deploy.png){width=6.4 #deploy}

## 17.2 Where the System Will Be Hosted

The dashboard needs to be reachable by the instructor, reviewers, and the wider community at the end of Senior Design II. Because every route is read-only and the results are public, the hosting can stay simple and nearly free. The team will choose between two options in Sprint 6, summarized in {tab:hosting}.

Table: Hosting options for Senior Design II {#hosting}
| Option | How it works | Cost | Trade-off |
|---|---|---|---|
| A. Static dashboard | Nuxt builds a static site that reads results files bundled at build time, published on GitHub Pages | Free | No server or database to run; adding a run means rebuilding the site |
| B. Application host with managed database | The API and frontend containers run on a container host, backed by a managed PostgreSQL instance; the connection string is stored as a platform secret | Free tier to about $20 per month | Supports filtering and comparing arbitrary runs; one more service to maintain |

The current recommendation is to publish option A for the final study results, because it has no running costs and nothing to secure, and to keep option B as the full local stack in Docker Compose. If the team decides the comparison view needs arbitrary run pairs online, it will deploy option B with a free-tier managed PostgreSQL instance. Either way, deployments will run from GitHub Actions on a tagged release, so the hosted version always matches a reviewed commit.

> NOTE: Record the team's hosting decision (option A or B, and which provider) here once it is made in Sprint 6.

## 17.3 Secrets and Configuration

All configuration comes from environment variables with working local defaults, read through pydantic-settings. API keys (`OPENAI_API_KEY`) and any production `DATABASE_URL` are never committed: locally they live in an untracked `.env` file, and in hosting they will be platform secrets. CI needs no API key, because integration tests replay recorded fixtures, and the live test that calls a real judge is opt-in. The CI workflow runs with read-only repository permissions.

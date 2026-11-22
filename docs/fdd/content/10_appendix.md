# Appendix A. Handoff Guide

This appendix is for a team that inherits MTLJ without access to its authors. With this document and the repository, a new team should be able to set up, run an audit, and extend the system within a day.

## A.1 Getting Started

1. Install Docker Desktop and Git. On Windows, clone into a short path such as `C:\code`, because some packages have very long file paths.
2. Clone the repository (`github.com/bduffaut/Mutation-Testing`) and run `docker compose up` from its root. The first start takes a few minutes.
3. Open `http://localhost:3000`. The stack-status page should show green checks for the API, the database, and the outbound network. The API's interactive documentation is at `http://localhost:8000/docs`.
4. To run audits, install uv, run `uv sync`, and add `OPENAI_API_KEY` to a `.env` file in the repository root.
5. Run `uv run mtlj audit run --dataset gold --limit 3 --output runs/example`. It scores 3 records and their mutants five times each (115 scorings, about four minutes, under fifteen cents) and prints the summary.
6. Before every pull request, run `uv run python scripts/check.py`, which formats, lints, and tests the code the same way CI does.

## A.2 Repository Map

Table: Repository layout {#repo}
| Path | Contents |
|---|---|
| `src/mtlj/operators/` | Mutation operators: `degrading/` and `preserving/`; the registry finds new files automatically |
| `src/mtlj/judges/` | The `Judge` base class, the callable adapter, and the DeepEval adapter |
| `src/mtlj/engine/` | Generator, runner, retries, cache, fixtures, audit orchestration, configuration, and results |
| `src/mtlj/stats/` | Paired deltas, detection rate, spurious sensitivity, variance, bootstrap, correction, and dose-response |
| `src/mtlj/data/` | Record and mutant models and the RAGTruth loader |
| `src/mtlj/cli/` | The `mtlj` command |
| `src/mtlj/api/` | The FastAPI application, database session, and models |
| `frontend/` | The Nuxt and PrimeVue website |
| `tests/` | Tests, laid out like `src/mtlj/`; `tests/fixtures/` holds the gold set and recorded judge scores |
| `corpus/v1/` | The frozen corpus, its checksum, and its manifest |
| `configs/` | Example audit and battery files |
| `migrations/` | Alembic database migrations |
| `docs/` | Developer guide, contribution rules, metric definitions, operator reference, results format, fixtures, corpus notes, and this document's sources |
| `spike/` | The feasibility study's data and scripts |

## A.3 Extending the System

**Adding an operator.** Create a file in `src/mtlj/operators/degrading/` or `preserving/` with a subclass of `MutationOperator`. Give it metadata (a unique name, family, expected direction, `version=1`, and its parameters) and implement `apply`. It must be deterministic for a given seed, must return the text unchanged with `changed=False` when it cannot apply, and must record what it changed in `details`. Add a test module that mirrors the file, and a section in `docs/operators.md` explaining what intensity means for it and its default levels. It must not call any language model.

**Adding a judge.** Subclass `Judge`, set its name, model, version, and score range, and implement `score_raw`. Return a `RawScore` with verdict counts if the judge reports them, and `None` for the counts if it does not. Never invent them. Add the adapter to the contract test parameters, which run the shared checks against it. Record a fixture file for it if integration tests should replay it.

**Adding a statistic.** Write the definition, a worked example on real data, and the decisions behind it in `docs/metrics.md`, and have the team ratify it before implementing it. Then implement it in `src/mtlj/stats/`, test it on synthetic data with a known answer, and add its fields to the results file following the versioning rules in Section 13.5.

**Changing the corpus.** Never edit `corpus/v1/`. Create `corpus/v2/` with its own script, manifest, and checksum, and record what changed and why.

## A.4 Where to Find Things

Table: Project documentation {#docs}
| Document | What it covers |
|---|---|
| `README.md` | Getting started, running an audit, and the repository layout |
| `docs/DEVELOPER.md` | Running Python locally, adding packages, tables, routes, and pages, and troubleshooting |
| `docs/CONTRIBUTING.md` | Jira and GitHub workflow, Git step by step, pull requests, code style, naming, and tests |
| `docs/metrics.md` | Every statistic: definition, worked example, decisions, and code |
| `docs/operators.md` | Every operator: intensities, default levels, rules, and versioning |
| `docs/results.md` | The run directory and the results.json format |
| `docs/fixtures.md` | Recording and replaying judge responses |
| `docs/corpus-v1.md` | How corpus v1 was chosen, filtered, and frozen |
| `docs/spike-effect-size.md` | The feasibility study's method, results, and data |
| `docs/fdd/` | This document's text, diagrams, charts, and build script |

# Appendix B. Glossary

Table: Glossary {#glossary}
| Term | Meaning |
|---|---|
| Adapter | A small class that wraps a judge behind MTLJ's common `Judge` interface |
| Battery | The list of operators and intensities an audit runs |
| Degrading operator | An edit that makes an answer worse, so a good judge's score should drop |
| Detection rate | The share of testable degrading mutants whose score dropped by more than the threshold |
| Dose-response curve | A curve fitted through an operator's hit rate at each intensity; its onset is where the judge starts to react |
| Gold set | The 20 corpus records checked by hand in the feasibility study, used for development and tests |
| Intensity | A number from 0 to 1 that sets how strong an operator's edit is |
| Judge | An LLM-based scoring function, such as DeepEval's faithfulness metric |
| Mutant | An edited copy of a known-good answer, with full provenance |
| Noise floor (σ) | The judge's pooled run-to-run standard deviation on unchanged originals |
| Paired delta | An original's mean score minus its mutant's mean score |
| Preserving operator | An edit that changes an answer's form but not its quality, so a good judge's score should not move |
| Provenance | Everything needed to explain and regenerate a mutant: record, operator and version, intensity, parameters, seed, and what changed |
| RAG | Retrieval-augmented generation: answering a question from retrieved passages |
| Spurious sensitivity | The share of testable preserving mutants whose score moved, in either direction, by more than the threshold |
| Threshold (τ) | The smallest change in mean score that counts as a reaction, derived from the noise floor and N |
| Validity filter | A check that rejects degrading mutants that are garbled rather than contradictory |

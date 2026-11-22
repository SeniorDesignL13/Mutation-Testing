# 5. User Stories and Use Cases

## 5.1 Users

The primary user is a developer or machine-learning engineer who runs an LLM application and already uses a judge in the team's workflow. This user knows Python, works from a terminal and a CI pipeline, and cares more about whether the judge gets things right than about how it works inside. A secondary user is someone comparing judge options before adopting one, which is also the role the team itself plays in the Senior Design II study. Two supporting roles appear in the use cases: the team annotator, who reviews samples of edits during the validation study, and the CI pipeline, which will run audits automatically when a judge's prompt changes. The judge backend (a hosted model API or a local Ollama server) is an external actor that the system calls but does not control.

![Overall use case diagram. Solid ovals work today from the command line; dashed ovals are planned for Senior Design II.](diagrams/uc_overall.png){width=6.3 #uc_overall}

## 5.2 Primary User Stories

Requirements live in Jira as tasks grouped under fourteen epics, each with a short reason it exists and a "Delivered when" checklist. {tab:stories} lists the main user stories in third person, with the condition that marks each one done and its status at the end of Senior Design I.

Table: Primary user stories {#stories}
| ID | User story | Done when | Status |
|---|---|---|---|
| US-01 | An ML engineer wraps an existing judge in an adapter so it can be audited without rewriting it. | The adapter returns a score plus judge name, model, and version, and passes the shared contract tests. | Done |
| US-02 | An ML engineer audits against known-good data, so any failure is the judge's fault and not the data's. | The corpus is frozen and checksummed; records with an empty question, answer, or passage are rejected. | Done |
| US-03 | An ML engineer picks which edits and strengths to run, to focus on the mistakes that matter. | Each operator declares its family, parameters, and expected direction; a battery file selects operators and intensities. | Done |
| US-04 | An ML engineer can reproduce any edited answer exactly, to debug a surprising result. | The same seed, settings, and operator version give a byte-identical result; full provenance is saved. | Done |
| US-05 | An ML engineer gets several scores per item, to tell a real weakness from noise. | The sample count is configurable and every sample is stored. | Done |
| US-06 | An ML engineer does not lose a night of compute when a long audit crashes. | Scores are saved as they finish; a resumed run skips completed work. | Done |
| US-07 | An ML engineer sees how many real errors the judge caught. | Detection rate per operator and intensity, against the judge's own noise, with a 95% interval. | Done |
| US-08 | An ML engineer sees how often the judge reacted to harmless edits. | Spurious sensitivity per operator and intensity, in the same form, split into penalized and rewarded. | Done |
| US-09 | An ML engineer runs audits from the command line, locally or in CI. | Run, show, and resume commands work; every setting is checked before any judge call. | Done |
| US-10 | An ML engineer sees a judge's strengths and weaknesses in one place. | A profile view shows per-operator rates, intervals, and missed examples. | Designed (SD2) |
| US-11 | An ML engineer compares two judges, or two versions of one, before switching. | A side-by-side view shows the differences in each rate with intervals. | Designed (SD2) |
| US-12 | A reviewer can trust that the generated labels are actually correct. | At least 100 edits reviewed by two people each; weak operators fixed or dropped. | Planned (SD2) |
| US-13 | An ML engineer can run integration tests without paying for model calls. | Judge responses can be recorded once and replayed offline in CI. | Done |
| US-14 | An ML engineer reruns an audit without paying twice for identical calls. | A content-addressed cache answers repeated scorings. | Done |

## 5.3 Use Case: Running an Audit

{fig:uc_audit} breaks the "Run an audit" use case into its parts. The engineer chooses a dataset: the 20-record gold set used in tests, the full frozen corpus, or a file of their own records. They choose an operator battery and the intensities to run, and set the number of samples, the seed, and how many judge calls may run at once. All of these settings can come from a TOML configuration file, and options given on the command line override the file. Validation of every setting is included in each of these choices: a mistyped key, a value of the wrong type, or a battery that does not load stops the command with exit code 2 and a message that names the file and key it came from. This happens before any money is spent.

![Detailed use cases for running an audit.](diagrams/uc_audit.png){width=6.4 #uc_audit}

Scoring includes generating the mutants with full provenance and writing the run directory. Two behaviors extend it. Transient failures such as rate limits and timeouts are retried with exponential backoff, and a call already in the response cache or in a recorded fixture file is answered without contacting the judge. A run that would make more than 1,000 scorings asks the engineer to confirm. Where nobody can answer, such as in CI, the engineer must pass `--yes`, or the command prints "Aborted." and exits before any scoring. Afterwards `mtlj audit show` prints any run's results again.

The main alternate flows are a judge that goes down partway through (the run stops after twenty consecutive failures and is marked aborted, with every score so far kept on disk) and an interruption by the user (Ctrl+C, SIGTERM, or SIGHUP), which is handled the same way. An aborted run reports no rates, because they would mix scored mutants with ones never reached.

## 5.4 Use Cases: Dashboard and Validation Study

{fig:uc_review} shows the Senior Design II use cases. In the dashboard, the engineer opens a run's judge profile, compares two runs, reads dose-response curves, and opens the mutant explorer from any row of the "mutants the judge got wrong" table to see the original and edited answers, the context, every repeated score, and the provenance. Every view reads the same `results.json` and run files that the command line writes, so the dashboard never starts or changes an audit.

![Use cases for the dashboard and the validation study (Senior Design II).](diagrams/uc_review.png){width=6.4 #uc_review}

In the validation study, the Product Owner draws a stratified sample of at least 100 edits across every operator and intensity with a fixed seed. Annotators independently label whether each edit's expected direction actually holds, and agreement is computed per operator family. Operators that fall below the agreed bar are fixed and rechecked, or dropped, before the full audit. Section 12 describes the study.

## 5.5 Use Case Discussion

Several use cases are worth discussing beyond the diagrams, because they shaped the design.

**Comparing a hosted judge with a local one.** A team paying for gpt-4o-mini wants to know whether a quantized Llama model served by Ollama would grade as well. The two runs use the same corpus, battery, seed, and sample count, so their mutants are identical. Only the judge configuration differs, and the comparison view can show the difference in each rate with an interval. This is why the judge's model and quantization are treated as part of its identity and are saved with every run.

**A prompt change as a regression test.** A team that maintains a custom judge prompt wants each prompt edit to be checked like code. Because the prompt text is part of the judge's identity, an edited prompt is a new judge version. Running the same audit before and after the change and comparing the results is the intended workflow, and the planned GitHub Action would fail a pull request when the detection rate drops or spurious sensitivity rises beyond the interval.

**Investigating a surprising result.** When a judge appears to miss most numeric changes, the engineer needs to see examples. Every mutant carries its operator name and version, intensity, seed, and a record of exactly which span changed. The engineer can regenerate the mutant from the record and confirm it is the same text, open the stored judge verdicts to see whether the judge marked the changed claim as "unclear" or did not extract it at all, and rerun only that record.

**A long overnight audit.** A full corpus audit is tens of thousands of scorings. The engineer needs it to survive network failures and rate limits without supervision. Retries, streaming writes, and the cache handle transient failures; rate limiting keeps the run inside the provider's limits; and if the run still stops, `mtlj audit resume` continues it from its directory without rescoring anything already on disk. The team's own first full audit ran this way in Sprint 4.

# 6. Requirements Specification

This section is the formal requirements specification (Jira MTLJ-71). Each requirement has an identifier, a priority, the user stories it comes from, the Jira epic and tasks that implement it, how it is verified, and its status at the end of Senior Design I (Sprint 4). "MVP" means required for the project to count as done at the end of Senior Design II. "Stretch" means planned if time allows.

## 6.1 Functional Requirements

Table: Functional requirements {#fr}
| ID | Requirement | Priority | Stories | Jira | Verified by | Status |
|---|---|---|---|---|---|---|
| FR-01 | Load the RAGTruth question-answering set, keep only GPT-4 answers that human annotators marked hallucination-free, and convert them to the standard record format. | MVP | US-02 | MTLJ-40, 41, 42 | Loader unit tests; checksum test | Done |
| FR-02 | Verify the corpus file against its published checksum before use, reject incomplete records, and run sanity checks on the loaded corpus. | MVP | US-02 | MTLJ-43, 44 | Unit tests | Done |
| FR-03 | Every operator declares its name, family, expected direction, parameters, and an integer version. | MVP | US-03, 04 | MTLJ-59, 101 | Registry tests | Done |
| FR-04 | Provide entity substitution and numeric perturbation as degrading operators. | MVP | US-03 | MTLJ-60, 62 | Operator unit tests | Done |
| FR-05 | Provide reformatting and verbosity padding as preserving operators. | MVP | US-03 | MTLJ-78, 81 | Operator unit tests | Done |
| FR-06 | Every operator accepts an intensity in [0, 1] and documents what it means. | MVP | US-03 | MTLJ-76 | Unit tests; docs/operators.md | Done |
| FR-07 | Discover operators automatically and select them by name from a battery file. | MVP | US-03 | MTLJ-82, 84 | Registry and battery tests | Done |
| FR-08 | Generate mutants with full provenance, deterministic per-mutant seeds, and stable IDs that include the operator version. | MVP | US-04 | MTLJ-29, 83, 85, 101 | Generator tests; byte-identical regeneration test | Done |
| FR-09 | Pass every degrading mutant through a validity filter and log each operator's rejection rate. | MVP | US-12 | MTLJ-63, 64 | Unit tests; validation study | Done |
| FR-10 | Wrap any judge behind one adapter interface that returns a normalized score, identity, and optional verdict counts. | MVP | US-01 | MTLJ-69, 66 | Contract test suite | Done |
| FR-11 | Provide a DeepEval faithfulness adapter and a plain Python callable adapter. | MVP | US-01 | MTLJ-67, 68 | Contract tests; replay test | Done |
| FR-12 | Score every original and every mutant N times concurrently, storing every sample. | MVP | US-05 | MTLJ-95, 97 | Runner tests | Done |
| FR-13 | Retry transient judge failures with exponential backoff and stop a run after repeated consecutive failures. | MVP | US-06 | MTLJ-94 | Retry tests | Done |
| FR-14 | Write each score to disk as soon as it returns. | MVP | US-06 | MTLJ-24 | Kill-midway test | Done |
| FR-15 | Resume an interrupted audit from its run directory without rescoring completed work. | MVP | US-06, 09 | MTLJ-56, 91 | Integration test | Done |
| FR-16 | Cache judge responses by content so identical calls are not paid for twice. | MVP | US-14 | MTLJ-93 | Cache tests | Done |
| FR-17 | Record judge responses once and replay them offline for tests. | MVP | US-13 | MTLJ-90 | Fixture tests; CI replay test | Done |
| FR-18 | Limit the rate of judge calls to a configured budget. | MVP | US-09 | MTLJ-96 | Runner tests | Done |
| FR-19 | Serve judges from local models through Ollama as well as hosted APIs. | MVP | US-01 | MTLJ-92 | Live integration test | Done |
| FR-20 | Compute paired deltas, the pooled noise floor, detection rate, and spurious sensitivity per operator and intensity. | MVP | US-07, 08 | MTLJ-33, 34, 35, 38 | Statistics tests on synthetic and spike data | Done |
| FR-21 | Report Wilson and record-level bootstrap intervals for every rate. | MVP | US-07, 08 | MTLJ-39 | Statistics tests | Done |
| FR-22 | Apply a Benjamini-Hochberg correction across a run's tests. | Stretch (done) | US-07, 08 | MTLJ-36 | Statistics tests | Done |
| FR-23 | Fit a dose-response curve and onset for operators run at several intensities. | Stretch (done) | US-07, 08 | MTLJ-37 | Statistics tests | Done |
| FR-24 | Write a versioned, deterministic `results.json` plus the run's inputs and settings. | MVP | US-09 | MTLJ-32 | Results tests | Done |
| FR-25 | Provide `mtlj audit run` and `mtlj audit show` commands with a TOML configuration file that is fully validated before any judge call. | MVP | US-09 | MTLJ-57, 58 | CLI tests | Done |
| FR-26 | Store runs, mutants, and evaluations in PostgreSQL with tracked migrations. | MVP | US-10, 11 | MTLJ-25 to 28 | Migration test; repository tests | To do |
| FR-27 | Serve audit results through read-only FastAPI endpoints. | MVP | US-10, 11 | MTLJ-55 | API tests | To do |
| FR-28 | Provide dashboard views for a judge profile and a comparison of two runs. | MVP | US-10, 11 | MTLJ-51, 52, 54 | End-to-end tests; usability check | To do |
| FR-29 | Provide a dose-response view and a mutant explorer. | Stretch | US-10 | MTLJ-53 | End-to-end tests | To do |
| FR-30 | Support a stratified annotation sample, two-reviewer labeling, and agreement statistics. | MVP | US-12 | MTLJ-45 to 50 | Validation study report | To do |
| FR-31 | Provide negation insertion and sentence reordering operators with a pronoun-reference safety check. | Stretch (done) | US-03 | MTLJ-61, 79, 80 | Operator unit tests | Done |
| FR-32 | Provide a Ragas faithfulness adapter. | Stretch (done) | US-01 | MTLJ-65 | Contract tests | Done |

## 6.2 Non-Functional Requirements

Table: Non-functional requirements {#nfr}
| ID | Quality | Requirement | How it is checked |
|---|---|---|---|
| NFR-01 | Clean ground truth | No language model makes any edit, any filler text, or any expected label. | Code review; operators depend only on spaCy, NLTK, and fixed word lists |
| NFR-02 | Reproducibility | The same seed, settings, and operator version always give the same mutants, and the same scores always give a byte-identical `results.json`. | Determinism tests; results file holds no timestamps |
| NFR-03 | Framework neutrality | Code specific to one judge framework lives only in its adapter. | Package layout; contract tests |
| NFR-04 | Low cost | An audit can run entirely on local hardware with no per-call cost; reruns reuse cached scores. | Ollama integration; cache hit rate in the run summary |
| NFR-05 | Crash safety | A crash loses at most the scores that were in flight. | Kill-midway test |
| NFR-06 | Safety before spending | Every setting is validated before the first judge call, and runs above 1,000 scorings require confirmation. | CLI tests |
| NFR-07 | Easy setup | A new developer has a working environment in under 15 minutes with only Docker and Git installed. | Setup guide tested on a fresh machine (MTLJ-19) |
| NFR-08 | Quality gates | Every pull request runs linting, formatting checks, and tests in CI, and nothing merges without one approving review and a passing build. | Branch protection (MTLJ-21) |
| NFR-09 | Performance | The CI test suite finishes in under five minutes; the runner keeps a configurable number of judge calls in flight. | CI timing (about 69 seconds for 1,127 tests) |
| NFR-10 | Honest statistics | Every rate has an interval, rates are read against chance, and results are reported as measured even when they do not support the project's pitch. | Results schema; team agreement |
| NFR-11 | Accessibility | The dashboard meets WCAG 2.2 level AA for color contrast, keyboard use, and text alternatives for charts. | Automated checks and manual review (Section 14.6) |
| NFR-12 | Security | API keys come only from environment variables or an untracked `.env` file; CI runs with read-only permissions; dependencies are updated by Dependabot. | Repository configuration; review |
| NFR-13 | Maintainability | Python code passes Ruff linting and formatting; every module has tests that mirror the source layout. | CI |

## 6.3 Constraints and Assumptions

- **Team and time.** Four members across two semesters, each working part time alongside other courses. The team originally planned for five or six members, and the MVP was scoped for four.
- **Budget.** No external sponsor funds the project. Hosted model calls are paid by the team, which favors local models and caching (Section 20).
- **Language.** Python is required for the core, because every judge framework in scope is a Python library.
- **Data.** Only public, research-licensed data is used. No personal or user data is collected or processed.
- **Judges.** Framework judges are used exactly as shipped at pinned versions. Their prompts are never edited, since that would mean testing a different judge.
- **Assumption.** Human-verified clean answers are a valid starting point, so a degrading edit makes an answer worse and a preserving edit leaves its quality unchanged. The validation study tests this assumption directly.

## 6.4 Traceability

{tab:trace} traces each epic to the requirements it implements and the test modules that verify them, so a reader can follow any requirement from story to code to test.

Table: Traceability from epics to requirements and tests {#trace}
| Epic | Requirements | Test modules |
|---|---|---|
| MTLJ-2 Core Data Model and Persistence | FR-08, FR-14, FR-26 | tests/data/test_mutants.py, tests/api/test_migrations.py |
| MTLJ-3 Dataset Acquisition and Curation | FR-01, FR-02 | tests/data/test_ragtruth.py, tests/data/test_records.py, dataset sanity-check tests |
| MTLJ-4 Project Infrastructure and Tooling | NFR-07, NFR-08, NFR-13 | tests/test_ci_guard.py |
| MTLJ-6 Dashboard | FR-27, FR-28, FR-29, NFR-11 | tests/api (to be extended), frontend checks |
| MTLJ-7 Validation Study | FR-30 | Validation study report |
| MTLJ-8 Degrading Operators | FR-04, FR-09, FR-31 | tests/operators/degrading |
| MTLJ-9 Preserving Operators | FR-05, FR-06, FR-31 | tests/operators/preserving |
| MTLJ-10 Adapter Layer | FR-10, FR-11, FR-32 | tests/judges |
| MTLJ-11 Analysis and Statistics | FR-20 to FR-24 | tests/stats, tests/engine/test_results.py |
| MTLJ-12 Command Line Interface | FR-15, FR-25 | tests/cli/test_cli.py, tests/engine/test_audit_config.py |
| MTLJ-13 Mutation Engine | FR-03, FR-07, FR-08 | tests/operators/test_registry.py, tests/engine/test_generator.py, tests/engine/test_battery_config.py |
| MTLJ-14 Execution Engine | FR-12 to FR-19 | tests/engine/test_runner.py, test_retry.py, test_cache.py, test_fixtures.py |
| MTLJ-5 Empirical Audit and Findings | Objective 5 | Pilot and full audit run directories; findings report (Sections 11.4 and 11.5) |

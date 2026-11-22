# 7. System Architecture

## 7.1 Overview

MTLJ is a Python package, `mtlj`, with a command-line interface on top and a web API and dashboard beside it. The components are loosely coupled and all validate against the same Pydantic models. A record, a mutant, an evaluation, and a result are each defined exactly once, and the command line, the engines, the cache, the fixtures, and the API all use that single definition. This avoids a common integration bug in which two parts of a system disagree about what a mutant or a score contains.

{fig:arch} shows the components and how data moves between them.

![Component architecture. The dashboard and database are built in Senior Design II.](diagrams/comp_arch.png){width=6.4 #arch}

The core engine has five parts:

- **RAGTruth loader** (`mtlj.data.ragtruth`) converts source data into the standard record format and checks the corpus checksum.
- **Operator registry** (`mtlj.operators`) holds the edits that can be applied to an answer and discovers new operators automatically.
- **Mutation generator** (`mtlj.engine.generator`) applies a battery of operators across the records and records where every edited answer came from.
- **Async judge runner** (`mtlj.engine.runner`) sends originals and edits to the judge through an adapter, retries transient failures, consults the cache or recorded fixtures, and streams every scoring to disk.
- **Statistics** (`mtlj.stats`) turns the stored scores into rates, intervals, corrections, and curves, and `mtlj.engine.results` writes them to the versioned results file.

## 7.2 Design Rules

Four rules shape the architecture and should be preserved by anyone who continues the project:

1. **Judge-specific code stays behind an adapter.** When a framework update breaks something, it breaks one file, and the shared contract tests catch it.
2. **Audits start only from the command line.** The dashboard and API read results; they never start, change, or delete an audit. A delay in the dashboard therefore cannot hold up the research.
3. **Files are the source of truth for a run.** Every run leaves a self-contained directory: its settings, its battery, its exact inputs, every scoring, and the results. The database planned for Senior Design II indexes and serves those results; it does not replace them. A run can always be reproduced or re-analyzed from its directory alone.
4. **Nothing generative makes ground truth.** Operators depend only on spaCy, NLTK, and fixed word lists. A language model appears in exactly one place: inside the judge under test.

## 7.3 Run Lifecycle

{fig:state_run} shows the states of an audit run. Validation happens before anything else, so a bad setting costs nothing. Generation is fast and local. Scoring is the only stage that spends money or time on the judge. A run ends in one of three statuses that are written into `results.json`: `complete`, `aborted` (the judge went down or the user interrupted it), or `insufficient_data` (no original had the two or more scores needed to measure noise).

![State diagram of an audit run.](diagrams/state_run.png){width=4.6 #state_run}

## 7.4 Interaction Between Components

{fig:seq_audit} shows one audit run as a sequence diagram. The CLI loads and validates the configuration, generates mutants, and counts the planned scorings. It then hands the work to the runner, which keeps up to four judge calls in flight by default. For each task (an original or a mutant, at a sample index), the runner first asks the cache or fixture store for a score. Only on a miss does it call the adapter, which calls the judge backend. Every result, success or failure, is written to the sink as an `Evaluation`. When all tasks finish, the CLI computes the results and prints the summary.

[[LANDSCAPE]]

![Sequence diagram of one audit run.](diagrams/seq_audit.png){width=9 #seq_audit}

![Class diagram of the judge and operator interfaces.](diagrams/class_core.png){width=9 #class_core}

[[PORTRAIT]]

## 7.5 Core Classes

{fig:class_core} shows the two extension points of the system. `Judge` is an abstract base class with a single method to implement, `score_raw(input, output, context)`. It returns either a number or a `RawScore` with verdict counts and a claim count. The base class normalizes the score to [0, 1] with higher meaning better, checks it is in range, and attaches the judge's identity, so every adapter returns the same `JudgeResult`. `MutationOperator` is the other extension point. Each operator carries `OperatorMetadata` (name, family, expected direction, version, and parameters) and implements `apply(text, context, intensity, seed)`, returning a `MutationResult` that says whether the text changed and records the details. The `OperatorRegistry` finds every operator in the `mtlj.operators` package, so adding an operator means adding a file and nothing else.

{fig:class_data} shows the data models. A `DatasetRecord` holds one question, its passages, and its known-good answer. A `Mutant` refers to its record by ID and carries its full provenance. Its `regenerate` method rebuilds the mutant from the record and refuses if the operator version has changed since. An `Evaluation` is one scoring of one text at one sample index, and a `RunSummary` counts what happened during a run.

![Class diagram of the data models.](diagrams/class_data.png){width=4.4 #class_data}

# 8. Mutation Operators and the Mutation Engine

## 8.1 Operator Families

Operators belong to one of two families. A **degrading** operator breaks the answer, so the judge's score should go down. A **preserving** operator changes only the form of the answer, so the score should stay the same. Every operator takes an intensity between 0 and 1 that sets how strong the change is. Running an operator at several intensities gives a curve rather than a single pass or fail. If an operator cannot apply to a record, for example a number change on an answer with no numbers, it returns the text unchanged, and the generator logs the skip with its reason.

{tab:operators} lists the full catalog with what intensity means for each operator, its default levels, and its status. The first six rows are the MVP. Negation insertion and sentence reordering were stretch goals and were completed in Sprint 4. Citation fabrication and orthographic noise remain stretch goals.

Table: Mutation operator catalog {#operators}
| Operator | Family | Intensity means | Default levels | Status |
|---|---|---|---|---|
| `entity_swap` | Degrading | Up to ceil(5 x intensity) claims changed | 0.2, 0.6, 1.0 (1, 3, 5 claims) | Done (MVP) |
| `numeric_perturbation` | Degrading | One number moved by intensity x 50% (years and days: up to ceil(10 x intensity)) | See docs/operators.md | Done (MVP) |
| `verbosity_padding` | Preserving | round(400 x intensity) filler words | 0.125, 0.25, 0.5, 1.0 (50 to 400 words) | Done (MVP) |
| `reformat_to_bullets` | Preserving | All or nothing | 1.0 | Done (MVP) |
| `reformat_to_markdown` | Preserving | All or nothing | 1.0 | Done (MVP) |
| `reformat_to_prose` | Preserving | All or nothing | 1.0 | Done (MVP) |
| Negation insertion | Degrading | Number of claims negated | See docs/operators.md | Done (stretch) |
| Citation fabrication | Degrading | Number of false attributions | Planned | Stretch |
| Sentence reordering | Preserving | Share of sentences moved | See docs/operators.md | Done (stretch) |
| Orthographic noise | Preserving | Typo rate | Planned | Stretch |

Every operator also declares a version, a whole number starting at 1. Each mutant saves the version that made it, and the version is part of the mutant's ID. Any change that can alter an operator's output for the same input, intensity, and seed requires a version bump. Old and new code therefore never give two different texts the same ID, crash recovery can never mix mutants from before and after a change, and every finding can be traced to the exact operator code behind it.

## 8.2 What the Edits Look Like

{tab:examples} shows what each MVP operator does to a sentence. The entity substitution row is a real mutant from the 20-record gold set (record 14922); the others are short illustrations written for this document. In the preliminary design document, the entity example changed "5 minutes" to "2 minutes," which is really a numeric change. The example below swaps a named organization instead, so the two degrading operators are clearly distinct.

Table: Example edits for each MVP operator {#examples}
| Operator | Original | Edited |
|---|---|---|
| Entity substitution | Make sure the uTorrent program is closed and not running in the background before starting this process. | Make sure the Scan Leftovers program is closed and not running in the background before starting this process. ("Scan Leftovers" is taken from elsewhere in the same context, so it is plausible but contradicts the source.) |
| Numeric perturbation | The bridge is 1,280 meters long. | The bridge is 1,410 meters long. (A 10% change to a number the source states; format and units are kept.) |
| Reformatting (bullets) | Preheat the oven, season the fish, then sear it skin-side down. | A three-item bulleted list with exactly the same words and nothing added. |
| Verbosity padding | Preheat the oven to 350 degrees. | The same sentence followed by neutral filler built from phrases already in the source, such as "As the passage notes, the oven and the timing are part of the process described here." (About 20 of the 50 to 400 added words shown.) |


## 8.3 Entity Substitution

Entity substitution is the main degrading operator and the cleanest source of ground truth. It uses spaCy {ref:spacy} to find people, organizations, places, and dates in the answer, and replaces each chosen one with another of the same type. {fig:entity_activity} shows the algorithm.

![Activity diagram of the entity substitution operator.](diagrams/act_entity.png){width=4.6 #entity_activity}

The key rule came from studying DeepEval. A claim that the context never mentions receives an "unclear" verdict and still counts as faithful. A swap to something the source never discussed would therefore not move the score, and the judge would be right to ignore it. The operator follows these rules so that every swap is a real contradiction:

- Only entities that the source text supports are changed.
- The replacement has the same type and format. A year stays a year, and a month stays a month.
- The replacement never appears in the source sentence that supports the original, so no passage can back it up.
- At most one place is changed in each sentence, and entities that appear only once in the answer are preferred.
- Replacements come first from other entities in the same source text, and from a short fixed list only when the source offers none.

Every swap is recorded with the original and new text, the position, the sentence it belongs to, and where the replacement came from. Intensity sets the maximum number of claims changed (1, 3, or 5 at the default levels). An answer with fewer swappable entities gets fewer changes, and when two intensities end up changing the same number of claims, only the lower one is kept.

## 8.4 Numeric Perturbation

Numeric perturbation changes one number that the context states (an integer, decimal, percentage, year, or day of the month) so the answer disagrees with its source. Only numbers that also appear in the context are changed, compared by value, so "1,200" in the answer matches "1200" in the context. {tab:numeric} shows the size of the change.

Table: Size of a numeric perturbation by intensity {#numeric}
| Kind of number | Example | At intensity 0.2 | At intensity 1.0 |
|---|---|---|---|
| Integer, decimal, percentage | 324 metres, $3.40, 12.5% | plus or minus 10% | plus or minus 50% |
| Year, day of the month | 1932, March 5 | plus or minus 2 | plus or minus 10 |

The operator follows four rules. It changes one number per mutant, preferring numbers that appear once. It keeps the format: the same number of decimal places, thousands separators only if the original had them, and currency symbols, percent signs, and units untouched. The new value matches no number anywhere in the context, stays above zero, keeps a percentage at or below 100, and keeps a day between 1 and 28. It leaves alone numbers joined to other text, such as "COVID-19," "3rd," "10:30," or "5km," and numbers written as words. Every change is recorded in the mutant's details. The operator is complete and tested. Adding it to the default battery was a separate decision because it changes what an audit runs and costs, and the team made that decision with the pilot audits in Sprint 4.

## 8.5 Reformatting

The three reformatting operators change the layout of an answer without changing any word, so no claim is added, removed, or altered. `reformat_to_bullets` puts one bullet per sentence, `reformat_to_markdown` makes a numbered list with each item's first noun phrase in bold, and `reformat_to_prose` joins a list into one paragraph. Every conversion is checked against the original text, and if any word would change, the operator raises an error instead of returning a mutant. Intensity is all or nothing. Because most corpus answers are already prose, `reformat_to_prose` applies only to the few that are lists.

## 8.6 Verbosity Padding

Padding adds 50, 100, 200, or 400 words of filler after the original answer. Judges are widely suspected of rewarding or punishing length, and padding is how the project measures that. The filler must add no new claims, or the edit would stop being harmless. It is built from two safe sources: noun phrases that already appear in the source context, and a hand-reviewed list of neutral connecting phrases. It contains no digits, so it cannot introduce a number. A greedy search picks sentences that bring the added length to within 10% of the target.

## 8.7 Negation Insertion

Negation insertion (MTLJ-61) flips a claim that the source supports, so the answer now states the opposite of what the context says. It is the most direct contradiction an operator can make, and it tests whether a judge reads the meaning of a claim or only matches its words, since a negated sentence shares almost every word with the original. Intensity sets how many claims are negated. The operator only negates sentences whose claim appears in the context, inserts or removes a negation at the main verb, and records each sentence it changed with its original and new text. Like every degrading operator, its output passes through the validity filter.

## 8.8 Sentence Reordering

Sentence reordering (MTLJ-79) moves sentences within the answer without changing any of them. Intensity sets the share of sentences moved. A reorder is only harmless if no sentence depends on the one before it, so the operator runs a pronoun-reference safety check first (MTLJ-80): a sentence that opens with or relies on a pronoun or a phrase such as "this" or "it" that refers back to an earlier sentence stays where it is. Sentences that pass the check are shuffled with the mutant's seed, and the new order is recorded.

> NOTE: Check the negation and reordering descriptions against the implementations and docs/operators.md (default levels, which verbs and pronouns are handled), and correct any detail that differs.

## 8.9 Validity Filter

Every degrading edit passes through a validity filter (MTLJ-63) before it can be used. The gold set shows why it is needed. spaCy's small English model occasionally mislabels a token as an entity: in the gold-set mutants, "Worcestershire" (in "Worcestershire sauce") and the verb "Pat" (in "Pat the salmon dry") were tagged as people and swapped for "James" and "Thomas." Edits like these change the text without producing a clean contradiction of the source. The filter rejects them, along with other garbled output such as a swap that broke the grammar of a sentence, since those would make the score drop for the wrong reason. Each operator logs how often it is rejected (MTLJ-64), and the rejection counts are saved with the run, so a high rate shows up as a signal to fix the operator.

> NOTE: Describe the filter's actual checks here once confirmed against the implementation.

## 8.10 The Mutation Engine

The generator applies a battery to a list of records. A battery is a list of operators, each with the intensities to run, loaded from the default or from a TOML file. For each record, operator, and intensity, the generator derives a seed from the base seed, the record ID, the operator name, and the intensity. Any single mutant can therefore be regenerated without regenerating the others, and adding a record never changes the mutants of the records already there. {tab:provenance} lists what each mutant saves.

Table: Provenance saved with every mutant {#provenance}
| Field | Why it is saved |
|---|---|
| `mutant_id` | Stable ID derived from all the inputs below, including the operator version |
| `record_id` | Links the edit back to its original for the paired comparison |
| `operator_name`, `operator_version`, `operator_family` | Which code made the edit and which family it belongs to |
| `intensity`, `parameters`, `seed` | Everything needed to recreate the edit exactly |
| `expected_direction` | Down for degrading edits, unchanged for preserving ones |
| `original_text`, `mutated_text`, `changed` | The texts themselves and whether anything changed |
| `details` | Operator-specific record of what changed, such as each substitution's span and source |

The generator returns the originals, the mutants, and the skipped edits with reasons. It writes them to `originals.jsonl`, `mutants.jsonl`, and `skipped.jsonl` in the run directory, with a `run.json` manifest that records the seed, the battery, and the library versions behind the mutants.

# 9. Execution Engine

## 9.1 The Async Runner

A full audit means tens of thousands of judge calls, so speed and robustness matter. The runner builds a task for every original and every mutant at every sample index and runs them with a fixed number of asynchronous workers (four by default, set with `--concurrency`). Judges such as DeepEval are synchronous Python code, so each call runs in a thread pool while the event loop coordinates the workers. Originals are scored at the same rate as mutants, which the paired statistics require.

Each finished scoring becomes an `Evaluation` and is written immediately to `evaluations.jsonl`. {tab:evaluation} describes its fields. Failures are written too, with their error type and whether they were transient, so a run's record of what happened is complete.

Table: Fields of one evaluation (one line of evaluations.jsonl) {#evaluation}
| Field | Type | Meaning |
|---|---|---|
| `kind` | "original" or "mutant" | Which kind of text was scored |
| `target_id`, `record_id` | string | The mutant ID (or record ID for an original) and its record |
| `sample_index` | integer | Which of the N repeated scorings this is |
| `judge_name`, `model`, `version` | string | Exactly which judge produced the score |
| `status` | "ok" or "error" | Whether the scoring succeeded |
| `score`, `raw_score` | float or null | Normalized score in [0, 1] and the judge's own value |
| `verdict_yes`, `verdict_borderline`, `verdict_no`, `n_claims` | integer or null | Claim-level detail, when the judge reports it |
| `latency_ms`, `cached`, `attempts` | number, boolean, integer | Timing, whether the cache answered, and how many tries it took |
| `error_type`, `error`, `error_kind` | string or null | For failures: the exception, its message, and transient or permanent |
| `operator_name`, `intensity` | string, float or null | For mutants: which operator and intensity |

## 9.2 Retries and Failure Handling

Some failures have nothing to do with the text being scored: a timeout, a dropped connection, a rate limit, or a server error. The retry module sorts every failure into one of two kinds. Transient failures are worth another try. They include timeouts, connection errors, and the HTTP statuses 408, 409, 429, and 5xx. Permanent failures would fail the same way again: a bad API key, a bad request, a missing model, a score out of range, or a bug in an adapter. A 429 whose error code says the account is out of credit is also treated as permanent, since waiting will not refill it. {tab:retry} lists the policy.

Table: Retry policy defaults {#retry}
| Setting | Default | Effect |
|---|---|---|
| Maximum attempts | 6 | A call is tried up to six times |
| Base delay | 1 second | Wait before the first retry |
| Multiplier | 2 | Each retry waits twice as long as the one before |
| Maximum delay | 60 seconds | No single wait exceeds this |
| Jitter | up to 50% | Calls that failed together do not retry together |
| Retry-After | honored | A server's requested wait raises the delay, still capped |
| Consecutive failures | 20 | After 20 failures in a row the run stops and is marked aborted |

With the defaults, a call waits at most 31 seconds in total between its tries, and 23 seconds on average.

## 9.3 Response Cache and Recorded Fixtures

Developing an operator or a metric means running the same audit many times, and most of its calls are identical from one run to the next. The response cache answers a scoring from storage instead of calling the judge again. Its key is the SHA-256 hash of a canonical JSON document holding the judge's full identity and settings, the exact input, output, and context, and the sample index. Including the sample index matters: each of a run's N samples of a text is its own entry, so a rerun gets back the same N samples, and a run with more samples only calls the judge for the new ones. Failures are never cached. The cache is a single SQLite file that several runs can share.

Recorded fixtures (MTLJ-90) apply the same idea to testing. The DeepEval judge's scores for the 20 gold-set records and their 133 default-battery mutants, five samples each (765 scorings), are committed with the tests. The test suite and CI replay them with the network blocked, so integration tests cost nothing, need no API key, and give the same results every time.

## 9.4 Local and Hosted Judges

Judges can run against a hosted API or against a local model served by Ollama {ref:ollama} through the same interface. Docker Compose starts an Ollama container on request (`docker compose --profile models up`). The Ollama integration (MTLJ-92) is complete. Local runs make large overnight audits free. The model and its quantization are saved with every run, so results from different setups are never mixed. Section 16 covers model choice.

## 9.5 Resuming and Rate Limiting

A run that stops early, because the judge went down or someone pressed Ctrl+C, can be continued with `mtlj audit resume <run directory>` (MTLJ-56, MTLJ-91). The resume command reads the run's saved settings, battery, and exact inputs from its directory, rebuilds the task list, and skips every task that already has a successful scoring in `evaluations.jsonl`. Because the run directory holds the exact mutants that were generated, a resumed run never mixes mutants from a different operator version, and the finished run is identical to one that never stopped.

Rate limiting (MTLJ-96) keeps a run inside the judge provider's limits. Besides the cap on calls in flight (`--concurrency`), the runner can cap the number of calls started per minute. That turns rate-limit errors from a common event into a rare one, so retries are spent on real failures.

> NOTE: Add the exact resume and rate-limit options (names and defaults) from the implementation.

## 9.6 Judge Adapters

Three adapters are complete. The **callable adapter** wraps any Python function that returns a number, which is also how the tests use a stub judge without paying for model calls. The **DeepEval faithfulness adapter** runs DeepEval's `FaithfulnessMetric` with a chosen model and settings and reports the claim count and the yes, unclear, and no verdict counts along with the score. The **Ragas faithfulness adapter** (MTLJ-65) runs Ragas's faithfulness metric, which computes nominally the same quantity a different way. That makes DeepEval against Ragas a direct test of whether two implementations of one metric behave alike.

Every adapter passes the same contract test suite, which checks the score's type and range, empty-context handling, the identity fields, the verdict-count rules, and that repeated calls are not silently served from an internal cache, which would hide the judge's variance. A judge that does not report verdicts must return none rather than invent counts, and verdict counts may add up to less than the claim count, as the feasibility study showed DeepEval sometimes does.

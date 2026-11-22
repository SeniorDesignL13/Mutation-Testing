[[TOC]]

# 1. Executive Summary

## 1.1 Purpose

Teams that build products on large language models (LLMs) increasingly grade those products with another model. These graders, called LLM judges, read a question, some retrieved source text, and an answer, then return a score for qualities such as faithfulness to the sources. DeepEval, Ragas, and Promptfoo all ship judges of this kind, and many companies write their own. The scores decide whether a build passes, whether a prompt change is merged, and whether a cheaper model is good enough to replace an expensive one. Yet almost nobody checks whether the judges themselves are right. Checking a judge normally means paying people to label data, which is slow and has to be repeated every time the judge changes, or asking a stronger model to grade the grader, which only moves the trust problem one step further away. Mutation Testing for LLM Judges (MTLJ) exists to answer a simple question cheaply and repeatably: does this judge notice real mistakes, and does it leave harmless changes alone?

## 1.2 The Solution and Its Key Features

MTLJ borrows mutation testing from software engineering. It starts from 919 answers in the RAGTruth benchmark {ref:ragtruth} that human annotators confirmed contain no hallucinations. Small, seeded Python operators then edit those answers on purpose. Degrading operators break an answer, for example by swapping a name or changing a number so that it contradicts the source, and a good judge should lower its score. Preserving operators change only how the answer looks, for example by turning prose into bullets or adding neutral filler, and a good judge should leave the score alone. Because a script made each edit, the correct reaction is known in advance. No language model takes part in writing edits or deciding what the right outcome is.

Three features matter most to a user:

- **Ground truth without labeling.** Every edit records exactly what changed, which operator version made it, and the seed that reproduces it byte for byte, so any surprising result can be inspected and rerun.
- **Statistics that respect judge noise.** Judges do not give the same score twice. MTLJ scores every original and every edit several times, measures the judge's own run-to-run wobble, and only counts a change that is larger than that wobble. It reports two headline numbers per operator: the detection rate (how often real errors were caught) and spurious sensitivity (how often harmless edits moved the score), each with a 95% confidence interval, a multiple-comparison correction, and a dose-response curve across edit strengths.
- **Works with any judge, cheaply.** A judge is wrapped behind one small adapter interface. Audits run from a single command, stream every score to disk as it arrives, reuse cached results so reruns cost nothing, and can use free local models through Ollama.

## 1.3 Value and Results So Far

The value is a measurement that teams do not currently have. A ten-minute pilot on three records costs under fifteen cents with gpt-4o-mini, and a full audit of one judge configuration on the whole corpus (about 35,000 scorings) is estimated at roughly fifty dollars of hosted model time, or nothing on local hardware. That is far below the cost of building and maintaining a human-labeled test set for every judge version.

The core idea has already been shown to work. In a feasibility study of 800 scorings, DeepEval's faithfulness judge {ref:deepeval} moved its score about three times more than its normal noise when a name was swapped to contradict the source, while padding and reformatting did not move it. The same study found a real weakness: the judge missed 8 of 20 contradictions, or 40%, mostly by calling the changed claim "unclear" rather than wrong. It also flagged at least one claim as unfaithful in 32% of scorings of answers that humans had verified as clean.

By the end of Senior Design I (Sprint 4, November 24, 2026), the team had delivered a complete command-line audit system and used it. The corpus is frozen, checksummed, and sanity-checked. Six mutation operators are implemented and tested: entity substitution, numeric perturbation, and negation insertion, which break an answer, and reformatting, verbosity padding, and sentence reordering, which do not. Degrading edits pass through a validity filter that logs each operator's rejection rate. DeepEval and Ragas faithfulness adapters, local models through Ollama, an asynchronous runner with rate limiting, retries, a response cache, recorded test fixtures, resumable runs, and every planned statistic are done. The team ran pilot audits, selected the judges for the study, and completed a first full audit, summarized in Section 11.4. The continuous integration (CI) pipeline ran 1,127 automated tests on every pull request as of November 19, 2026.

## 1.4 Scope, Schedule, Team, and Technology

The minimum viable product (MVP), which defines "done" at the end of Senior Design II, covers the frozen corpus, the DeepEval and plain-Python adapters, the four core operators (entity substitution, numeric perturbation, reformatting, and verbosity padding) with a validity filter, the full execution engine including resume, all core statistics, the command-line interface, a dashboard with profile and comparison views, a human validation study of at least 100 edits, and an audit of at least two published judges. Everything except the dashboard and the validation study is already done. Three stretch goals are also done: negation insertion, sentence reordering, and the Ragas adapter. The remaining stretch goals are citation fabrication and orthographic noise operators, a Promptfoo adapter, extra dashboard views, and a GitHub Action that fails a pull request when a prompt change makes a judge worse.

The schedule has twelve two-week sprints. Senior Design I closed with pilot audits and a first full audit in November 2026. Senior Design II builds the database-backed results service and dashboard in January and February 2027, runs the validation study through early March, re-runs the full audit with the validated operators in March, and writes up the findings by late April 2027.

The team has four members. Ethan Goedelman is Product Owner and leads experimental design and statistics. Ben Duffaut is Scrum Master and leads the full stack. Gia Do leads the mutation operators. Nathaniel Frank leads the execution engine and adapters. Each member also has a secondary area. The system is written in Python 3.12 with Pydantic, Typer, asyncio, spaCy, and NumPy/SciPy/statsmodels. It uses PostgreSQL 16 through SQLAlchemy and Alembic, FastAPI, and Nuxt 4 with PrimeVue for the dashboard, all started with one Docker Compose command and checked by GitHub Actions.

## 1.5 Recommendation

The design is complete enough for implementation to continue without open architectural questions. The core measurement is validated, and the command-line audit system is complete and has already produced a first full audit. The team recommends moving into Senior Design II on the current plan, with the validation of the ground truth treated as non-negotiable. That validation is what makes every result defensible, and it must not be cut even if schedule pressure forces other stretch goals out.

# 2. Problem Statement

## 2.1 Who Has the Problem

Developers and machine-learning engineers who run LLM applications rely on automated judges to make release decisions. A typical example is a retrieval-augmented generation (RAG) system, which retrieves passages related to a user's question and asks a model to answer from them. The standard quality check for such a system is faithfulness: the share of claims in the answer that the retrieved passages actually support. DeepEval {ref:deepeval} computes it by having a model list the claims in the answer and mark each one yes, no, or unclear against the context. Ragas {ref:ragas} does something similar with a different breakdown, and Promptfoo {ref:promptfoo} lets teams write their own grading rubric. In each case the number that comes out is treated as a measurement and is used to gate releases, compare prompts, and choose models.

## 2.2 What Is Going Wrong

A judge is itself a language model, so it can fail in the same ways as the system it grades. It can miss a contradiction, reward long answers, penalize bullet points, or give different scores to identical input. Research has documented position bias, length bias, and self-preference in LLM judges {ref:zheng, wang, panickssery}, but most of that work studies side-by-side comparisons of two answers. The single-answer faithfulness judges that teams actually place in their continuous-integration pipelines receive much less scrutiny.

The obstacle is ground truth. To know whether a judge is right, someone has to know the right answer. One option is to have people label a dataset and measure agreement with the judge. That is slow and expensive, and it has to be repeated whenever the judge's prompt, model, framework version, or settings change, which in practice happens every few weeks. The other option is to have a stronger model grade the judge. That moves the question of whom to trust one step further away without answering it.

The available tooling does not close this gap. The major evaluation frameworks treat judge randomness as something to hide rather than measure. A common pattern is a flag that marks a flaky test so its failures are ignored. Common advice for false alarms is to loosen the pass threshold until they stop. None of the tools the team reviewed can tell a developer how often a given judge catches a given kind of mistake, or how much harmless variation it tolerates before it starts docking points.

## 2.3 Why It Matters

When a judge is wrong, the cost lands on the product. A judge that misses contradictions lets unfaithful answers ship, which in domains such as health, finance, or customer support exposes users to wrong information and the company to liability. A judge that punishes harmless formatting or length changes blocks good changes, wastes engineering time on false alarms, and pushes teams to loosen thresholds until the gate no longer protects anything. A judge whose scores drift between versions makes historical comparisons meaningless. In the team's own feasibility study, a widely used faithfulness judge missed 40% of deliberately inserted contradictions and flagged answers that humans had verified as clean in 32% of scorings. A team relying on that judge would not know either fact.

## 2.4 Problem Statement

Developers who maintain LLM applications depend on LLM judges to decide what ships, but they have no inexpensive, repeatable way to measure how reliably a given judge catches real errors or how often it penalizes changes that do not affect quality. The measurements that do exist require labeled data or another model's opinion, both of which are too slow or too untrustworthy to repeat every time the judge changes.

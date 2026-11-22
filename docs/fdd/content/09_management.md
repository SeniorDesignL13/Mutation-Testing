# 21. Project Management

## 21.1 Team and Roles

Roles come from the signed team contract. Everyone has a primary and a secondary area, so every part of the system has at least two people who understand it. {tab:roles} lists them.

Table: Team roles {#roles}
| Member | Primary role | Secondary role | Main contributions so far |
|---|---|---|---|
| Ethan Goedelman | Product Owner; experimental design and statistics | Mutation operators | Project proposal, feasibility study, corpus selection and freeze, every statistical definition and its implementation, response cache, retry handling |
| Ben Duffaut | Scrum Master; full stack (CLI, database, dashboard) | Execution engine | Repository, CI, Docker Compose and setup guide, data models, corpus loader, contract tests, async runner, audit command, results file, configuration files, recorded fixtures, Figma mockups |
| Gia Do | Mutation operators | Experimental design | Record format, operator interface, padding source, entity substitution, verbosity padding, reformatting, numeric perturbation, operator versions, operator registry, seeding, battery configuration, mutant generation |
| Nathaniel Frank | Execution engine and adapters | Full stack (CLI, database, dashboard) | Judge adapter interface, callable adapter, DeepEval faithfulness adapter, repeated sampling, Ollama integration |

> NOTE: Each member should check their "main contributions" row against Jira and GitHub before submitting.

## 21.2 Agile Process

The team runs Scrum {ref:scrum} in two-week sprints, tracked in Jira. The Product Owner owns the backlog and its priorities, the Scrum Master runs the ceremonies and removes blockers, and the whole team estimates and commits to each sprint's work.

**Sprint planning** happens at the start of each sprint, at the weekly in-person meeting. The team reviews the carry-over from the last sprint, the Product Owner proposes the highest-priority backlog items, and the team estimates any unpointed items in story points (Fibonacci scale) and commits to a set that fits its recent velocity. Every item pulled into a sprint has a short reason it exists, a "Delivered when" checklist, an assignee, and a point estimate.

**Daily standups** are asynchronous, posted in a Discord channel. Each member writes what they finished, what they are working on next, and anything blocking them. This is different from a co-located team's spoken standup, but it suits four students with different class schedules, and it leaves a written record that the Scrum Master reviews daily.

**Weekly meetings** are held in person, usually Tuesday or Thursday before class, with an agenda and shared notes in Confluence (MTLJ-73). They cover progress against the sprint, blocked items, decisions that need the whole team (such as each statistical definition, which was ratified at a meeting before it was implemented), and a check of Jira status, since tickets have occasionally moved columns by accident.

**Sprint review and retrospective** happen at the meeting that closes each sprint. In the review, the team walks through what merged, demonstrates working features from the command line, and moves unfinished items to the next sprint or back to the backlog. In the retrospective, the team discusses what to keep and what to change. The "lessons learned" in Section 21.7 came out of these retrospectives.

> NOTE: Confirm the meeting days, the planning and review format, and the standup channel details match what the team actually does.

## 21.3 Definition of Done and Workflow

A story only moves to Done (MTLJ-20) when it is merged through a reviewed pull request, CI is green, it has tests or the ticket explains why not, every item on its checklist is met, and anything a teammate needs to use it is documented.

Jira is linked to GitHub. Moving a task to In Progress creates its branch, named after the ticket key, and commits and pull requests that mention the key appear on the ticket. Pull requests use a template that asks for the ticket, what changed, and how it was tested. Nothing merges to `main` without one approving review and a passing build. The repository's `docs/CONTRIBUTING.md` explains this flow step by step for new contributors.

## 21.4 Epics

The backlog is split into fourteen epics that match the system's components and research activities. {fig:epics_chart} and {tab:epics} show where each stands on November 22, 2026: 50 of the 85 tasks are done, 3 are in progress, and 32 are still to do, most of them in the Senior Design II epics (dashboard, validation study, and empirical audit).

![Task status by epic on November 22, 2026.](diagrams/chart_epics.png){width=5.9 #epics_chart}

Table: Epics and task status (Jira export of November 10 updated with work merged since) {#epics}
| Key | Epic | Tasks | Done | Active | To do |
|---|---|---|---|---|---|
| MTLJ-1 | Documentation and Course Deliverables | 6 | 1 | 2 | 3 |
| MTLJ-2 | Core Data Model and Persistence | 9 | 5 | 0 | 4 |
| MTLJ-3 | Dataset Acquisition and Curation | 5 | 4 | 0 | 1 |
| MTLJ-4 | Project Infrastructure and Tooling | 8 | 8 | 0 | 0 |
| MTLJ-5 | Empirical Audit and Findings | 4 | 0 | 0 | 4 |
| MTLJ-6 | Dashboard | 6 | 1 | 0 | 5 |
| MTLJ-7 | Validation Study | 6 | 0 | 0 | 6 |
| MTLJ-8 | Degrading Operators | 6 | 3 | 0 | 3 |
| MTLJ-9 | Preserving Operators | 6 | 4 | 0 | 2 |
| MTLJ-10 | Adapter Layer | 5 | 4 | 0 | 1 |
| MTLJ-11 | Analysis and Statistics | 9 | 9 | 0 | 0 |
| MTLJ-12 | Command Line Interface | 3 | 2 | 0 | 1 |
| MTLJ-13 | Mutation Engine | 4 | 4 | 0 | 0 |
| MTLJ-14 | Execution Engine | 8 | 5 | 1 | 2 |
| | **Total** | **85** | **50** | **3** | **32** |

![The MTLJ epics in Jira.](diagrams/jira_epics.png){width=5.5 #jira_epics}

## 21.5 Sprint Reviews

{fig:velocity} shows the points each sprint committed to and completed. Velocity rose quickly as the infrastructure built in Sprint 1 paid off.

![Story points committed and completed in Sprints 1 to 3.](diagrams/chart_velocity.png){width=5.2 #velocity}

**Sprint 1 (September 29 to October 13).** The goal was to be able to run a judge locally, understand the data, and find out whether the core measurement works. The team planned 15 items (41 points) and finished 11 (26 points): all the infrastructure, the setup guide, the Definition of Done, the dataset survey, the standard record format, the operator interface, and the filler source. The feasibility study finished and went to review. The main takeaway was that the team had more capacity than expected, so Sprint 2 was planned bigger.

**Sprint 2 (October 13 to 27).** The sprint committed to 23 items (89 points), building the first working piece of each engine. The team completed 15 items (about 52 points): the record and mutant models, the loader, the frozen corpus, the adapter interface, the callable adapter and contract tests, the paired comparison, detection rate, variance estimation, the first two operators, and mutant generation. Eight items (37 points), including the async runner, the DeepEval adapter, and the bootstrap intervals, carried into Sprint 3. The over-commitment was a lesson in itself: points in review are not points done.

**Sprint 3 (October 27 to November 10).** The sprint committed to 20 items (83 points), including the carry-over, and completed 19 (78 points). It delivered the DeepEval adapter, the async runner, repeated sampling, retries, the response cache, recorded fixtures, every remaining statistic (spurious sensitivity, bootstrap intervals, Benjamini-Hochberg correction, and dose-response curves), the operator registry, seeding, battery configuration, the reformatting operators, the audit run command, the results file, and configuration files. Only the Ollama integration (5 points) carried over. By the end of Sprint 3, the command-line audit worked end to end.

> NOTE: Sprint 2 and 3 totals were reconstructed from the Jira exports and merged pull requests. Confirm them against Jira's sprint reports.

## 21.6 Current Sprint and Senior Design II Sprints

**Sprint 4 (November 10 to 24)** is in progress. Its goal is to complete the MVP operator set and the design documentation. {tab:sprint4} lists its known items, and {fig:jira_sprint} shows the sprint board.

Table: Sprint 4 backlog (in progress) {#sprint4}
| Key | Item | Assignee | Points | Status |
|---|---|---|---|---|
| MTLJ-62 | Implement numeric perturbation operator | Gia Do | (see Jira) | Done |
| MTLJ-101 | Add operator version to mutant provenance and IDs | Gia Do | 2 | Done |
| MTLJ-92 | Integrate Ollama for local judge serving (carried over) | Nathaniel Frank | 5 | In progress |
| MTLJ-70 | Write the SD1 design document | Ethan Goedelman | 8 | In progress |
| MTLJ-71 | Write the requirements specification | Ethan Goedelman | 5 | In progress |
| MTLJ-75 | Prepare SD1 presentation materials | Ethan Goedelman | 5 | Moving to SD2 |

> NOTE: Complete this table from the Jira sprint board (all Sprint 4 items, assignees, and points). MTLJ-75 should move to the first SD2 sprint, since presentations happen at the start of Senior Design II.

![Jira Sprint 4 backlog and board.](diagrams/jira_sprint.png){width=5.5 #jira_sprint}

{tab:sprints} shows the plan for the remaining sprints. Sprint 5 closes Senior Design I, and Sprints 6 to 12 make up Senior Design II, with one longer sprint over spring break. Plans can change at each sprint planning, but the milestones in {tab:milestones} anchor them.

Table: Sprint plan through the end of Senior Design II {#sprints}
| Sprint | Dates | Focus |
|---|---|---|
| 1 | Sep 29 to Oct 13, 2026 | Setup, corpus survey, interfaces, feasibility study (done) |
| 2 | Oct 13 to Oct 27, 2026 | Data models, loader, frozen corpus, adapters, first operators (done) |
| 3 | Oct 27 to Nov 10, 2026 | DeepEval adapter, async runner, cache, retries, all core statistics, audit command (done) |
| 4 | Nov 10 to Nov 24, 2026 | Numeric operator, operator versions, Ollama, final design document (current) |
| 5 | Nov 24 to Dec 8, 2026 | First pilot audit of DeepEval on corpus records; resume command; rate limiting; validity filter and rejection logging |
| 6 | Jan 12 to Jan 26, 2027 | Database schema, migrations, and repository layer; results API; dashboard scaffold; review protocol and sampling tool; SD2 presentation |
| 7 | Jan 26 to Feb 9, 2027 | Judge profile view; first review round; Ragas adapter or first stretch operator |
| 8 | Feb 9 to Feb 23, 2027 | Comparison view; second review round; agreement statistics |
| 9 | Feb 23 to Mar 9, 2027 | Operator validation scores; fix or drop weak operators; choose the judges to audit |
| 10 | Mar 9 to Mar 30, 2027 | Pilot audits on all judges; start the full audit (includes spring break) |
| 11 | Mar 30 to Apr 13, 2027 | Finish the full audit and analysis; extra dashboard views if time allows |
| 12 | Apr 13 to Apr 24, 2027 | Findings write-up, README and quickstart, final documents and presentation |

![Project timeline through the end of Senior Design II.](diagrams/chart_timeline.png){width=6.5 #timeline}

Table: Major milestones {#milestones}
| Milestone | Target | How it is shown |
|---|---|---|
| Feasibility confirmed | Oct 2026 (met) | Feasibility study write-up |
| Corpus v1 frozen | Oct 23, 2026 (met) | 919 records with a committed checksum |
| First end-to-end CLI audit | Nov 9, 2026 (met) | `mtlj audit run` on the gold set with intervals |
| Final design document | End of SD1 | This document |
| First pilot audit on corpus records | Early Dec 2026 | DeepEval with the MVP operators and intervals |
| Dashboard MVP | Late Feb 2027 | Profile and comparison views on real results |
| Ground truth validated | Mar 9, 2027 | Validation score and agreement for every operator |
| Full audit done | Apr 13, 2027 | At least two published judges audited |
| Final delivery | End of SD2 | Findings write-up, documentation, and final presentation |

{fig:jira_backlog} shows the product backlog in Jira. The backlog holds the 32 tasks still to do, grouped by epic. Most are not yet pointed, since tasks get points when they are pulled into a sprint.

![Jira product backlog.](diagrams/jira_backlog.png){width=5.5 #jira_backlog}

## 21.7 Lessons Learned

Several lessons from the first three sprints changed how the team works:

- **Check the data, not just the schema.** The corpus filter looked right but treated any hallucination field as "true" just because it existed. It was caught only by inspecting records by hand, so hand checks are now part of every data task.
- **Test the risky dependency first.** DeepEval's async mode had been reported to hang on Windows, so the team ran a single call on each member's machine before the feasibility study instead of finding out halfway through it.
- **Small decisions need a home.** Choices such as the verdict-count rule were first made in ticket comments and almost got lost, so decisions now get a dated entry in `docs/metrics.md` and in Confluence.
- **Points in review are not points done.** Sprint 2 committed to more than the team could finish and review. Sprint 3 was planned against completed work, and it finished 19 of its 20 items.
- **Keep Jira honest.** Tickets occasionally moved columns by accident when they were mentioned in another ticket, so status is checked at the weekly meeting instead of trusted blindly.

# 22. Risks and Mitigation

{tab:risks} lists the main risks, their impact, and what the team is doing about each.

Table: Risk register {#risks}
| Risk | Impact | Likelihood | Mitigation | Status |
|---|---|---|---|---|
| Edits that do not really change meaning (equivalent mutants) | High | Medium | Strict operator rules, validity filter, two-person human review | Filter planned for Sprint 5; review in SD2 |
| An entity swap breaks grammar instead of meaning, or swaps a mislabeled entity | High | Medium | Same-type swaps only; filter rejects garbled output; rejection rates logged | Seen in the gold set (Section 8.7); filter planned |
| A "harmless" edit actually hurts quality | Medium | Medium | Filler from the record's own context; report as curves; extra review of preserving edits | Mitigated by design |
| Judge randomness hides real effects | High | High | Repeated sampling, paired comparison, noise-based threshold, intervals | Mitigated; validated on the feasibility data |
| Scores stuck at 1.0 (saturation) | Medium | Medium | Report it honestly; run the `penalize_ambiguous_claims` setting; note it as a limitation | Open; watch in pilot audits |
| Labels give only direction | Medium | Certain | Report detection and sensitivity, not accuracy or calibration | Accepted limitation |
| Long audits crash or take hours | Medium | Medium | Stream results, retries, cache, local models, pilots first; resume planned | Mostly mitigated |
| Framework updates break adapters | Medium | Medium | Pinned versions, adapters, contract tests, replayed fixtures in CI | Mitigated |
| API costs exceed the budget | Medium | Low | Cache, fixtures, local models, spending limits, pilot runs before full runs | Mitigated |
| Team capacity | High | Medium | Smaller MVP, agreed cut order, backup roles, raise problems early | Ahead of plan |
| Dashboard slips | Medium | Medium | Dashboard is off the critical path; the CLI and results file deliver every result | Mitigated by design |

The risks that matter most are the ones that would make a failure the tool's fault instead of the judge's. That is why every degrading operator has strict rules and a filter, and why the human review checks that the expected directions actually hold. The biggest remaining unknown is score saturation. Faithfulness judges often give the same top score to many answers. If clean answers almost always get 1.0, the measured noise could be close to zero, which would make effects look huge while weakening the argument that judges need statistics at all. The team has agreed to report whatever it finds rather than pick a story afterward.

# 23. Project Status and What Is Next

## 23.1 MVP Status

{tab:mvp} compares the MVP scope with what exists on November 22, 2026.

Table: MVP scope and status {#mvp}
| Component | Required | Status |
|---|---|---|
| Corpus | RAGTruth question answering, clean answers only, standard format, frozen and checksummed | Done |
| Adapters | Common interface, DeepEval and plain Python adapters, shared contract tests | Done |
| Operators | Entity substitution, numeric perturbation, reformatting, and verbosity padding at several intensities | Done |
| Validity filter | Filter for degrading mutants with rejection logging | To do (Sprint 5) |
| Mutation engine | Full provenance, seeded generation, operator versions, battery files | Done |
| Execution engine | Async runner, repeated sampling, retries, cache, fixtures | Done |
| Execution engine | Resume, rate limits, local models | In progress (Sprints 4 and 5) |
| Analysis | Detection rate, spurious sensitivity, noise floor, Wilson and bootstrap intervals, versioned results file | Done (plus correction and dose-response curves, originally stretch goals) |
| Interfaces | CLI run and show | Done |
| Interfaces | CLI resume; database; results API; dashboard profile and comparison views | Designed (SD2) |
| Validation | 100 or more edits, two reviewers each, agreement measured, weak operators fixed or dropped | Planned (SD2) |
| Result | At least two published judges audited and written up | Planned (SD2) |

The team is ahead of the plan made in the preliminary design document. Two statistical stretch goals, the multiple-comparison correction and dose-response curve fitting, are already done, and nothing in the MVP is expected to be cut. If the schedule tightens, the team will revisit scope together, but human validation of the ground truth stays in regardless.

## 23.2 Is This the End of the Project?

The Senior Design deliverable ends in April 2027 with the audit results and the tool. The project itself is designed to outlive the course. MTLJ will be released as an open-source package, and the natural next steps are already scoped:

- **More judges and frameworks.** Adapters for Ragas, Promptfoo, and custom judges, and audits of new judge versions as they are released.
- **More operators.** Negation, citation fabrication, sentence reordering, and orthographic noise, plus operators for other quality dimensions such as answer relevance.
- **More domains.** A second corpus from technical, legal, or medical text, frozen as a new corpus version, to test whether findings transfer beyond everyday web questions.
- **CI integration.** A GitHub Action that runs a small audit when a judge's prompt changes and fails the pull request if detection drops or spurious sensitivity rises beyond the interval.
- **Publication.** A short paper or technical report on the audit findings.

The project has no external sponsor, so there is no sponsor to request a follow-on team. The student sponsor intends to keep maintaining the package, and the design, documentation, and handoff guide (Appendix A) are written so that another team could pick it up without contacting the original authors.

# 24. Personal Motivations

This section is written in each member's own voice.

## 24.1 Ethan Goedelman

> NOTE: Ethan writes this section in first person (about half a page to a page): why you proposed the project, what you wanted to learn, and what you took away from the first semester.

## 24.2 Ben Duffaut

> NOTE: Ben writes this section in first person (about half a page to a page): why you joined, what you wanted to learn, and what you took away from the first semester.

## 24.3 Gia Do

> NOTE: Gia writes this section in first person (about half a page to a page): why you joined, what you wanted to learn, and what you took away from the first semester.

## 24.4 Nathaniel Frank

> NOTE: Nathaniel writes this section in first person (about half a page to a page): why you joined, what you wanted to learn, and what you took away from the first semester.

# 25. Conclusion

MTLJ has gone from a proposal to a working tool. The corpus is chosen and frozen. Four operator families make seeded, versioned, fully traceable edits. The DeepEval adapter, the asynchronous runner, retries, the response cache, and recorded fixtures make audits practical and cheap. Every statistic the study needs is defined, ratified, implemented, and tested on real data, and a single command runs an audit end to end and writes a reproducible result. The feasibility study showed that the core measurement works: a widely used faithfulness judge reacts to a deliberate contradiction clearly enough to stand out from its own noise, and ignores formatting and padding at the strengths tested. It also found real blind spots, such as contradictions marked "unclear" and false alarms on answers humans verified as clean.

Senior Design I closes with the validity filter, resume, rate limiting, and the first pilot audit on corpus records. Senior Design II adds the database-backed results service and the dashboard, validates the ground truth with a human review, and runs the audit of published judges that will be the project's main result. The design in this document is complete enough for that work to proceed without open architectural questions, and detailed enough that another team could continue it from this document and the repository alone.

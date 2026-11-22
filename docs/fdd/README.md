# Final Design Document (MTLJ-70, MTLJ-71)

The SD1 Final Design Document is built from the Markdown chapters in `content/` into
`L13_MTLJ_Final_Design_Document.docx`. The requirements specification (MTLJ-71) is Section 6.

## Build

```bash
cd docs/fdd
npm install
node build.js
```

Open the .docx in Word and click **Yes** when it asks to update fields. That fills in the table of
contents with page numbers. In Word for the web or Google Docs, right-click the table of
contents and choose **Update field** (Word) or insert a new one (Docs). Export to PDF for Canvas.

Figures, tables and references are numbered automatically. Write `{fig:id}`, `{tab:id}` or
`{ref:key}` in the text. The build stops if any reference doesn't resolve. `build.js` describes the
Markdown it understands.

## Regenerating figures

| Files | How |
|---|---|
| `diagrams/*.puml` → `diagrams/*.png` | `java -jar plantuml.jar -tpng diagrams/*.puml` (PlantUML 1.2024.7, no Graphviz needed) |
| `diagrams/uc_overall.png` | `python charts/uc_overall.py diagrams/uc_overall.png` |
| `diagrams/chart_*.png` | `python charts/make_charts.py data/ci_test_counts_2026-11-19.json` |
| `diagrams/mock_*.png` | Figma exports (MTLJ-100), copied from the preliminary design document |
| `diagrams/jira_*.png` | **Placeholders.** Replace with Jira screenshots before submitting |

## Before submitting

Every yellow **Team check** note in the document marks something a person has to confirm or
write. Search the .docx for "Team check" to find them all. In short:

- [ ] Each member writes their personal motivations section (Section 24), in first person
- [ ] Each member checks their row of the team roles table (Section 21.1)
- [ ] Replace the three Jira placeholders with screenshots: backlog, epics, Sprint 4 board
- [ ] Confirm the Sprint 2 totals against Jira's sprint report
- [ ] Fill in the first full audit's judges and results (Section 11.4, both tables) from the run directories
- [ ] Check the negation insertion, sentence reordering, validity filter, resume and rate-limit descriptions against the code (Sections 8.7 to 8.9 and 9.5)
- [ ] Replace the test results table with the last CI run on main before submission (Section 18.3)
- [ ] Add the measured local-model speed and memory, and the full audit's run time and cost (Section 16.3)
- [ ] Confirm the meeting, standup, planning and review details (Section 21.2)
- [ ] Confirm the palette hex values against Figma (Section 14.6)
- [ ] Confirm the spaCy accuracy, gpt-4o-mini prices and Ollama sizes (Section 16)
- [ ] Fill in the actual OpenAI spend and who pays (Section 20)
- [ ] Agree on the validation study bars (Section 12.2) and the project license (Section 19.2)
- [ ] Everyone reads the whole document and fixes anything that's wrong (course AI policy)
- [ ] Update the table of contents, then export to PDF

## What changed from the preliminary design document

The grading comments on the PDD are addressed as follows:

| Comment | Fix |
|---|---|
| Executive summary under one page; missing MVP, timeline, team, tech stack | Section 1 is about two pages and covers all four |
| "About a third" vs 8 of 20 (40%) | 40% everywhere |
| Table of contents with no leaders and stale page numbers | A real Word table of contents field, updated when the file is opened |
| "Operators in bold" but nothing bold | Status column in the operator table instead |
| Grammar errors on pp. 8, 14, 15, 20 | Rewritten |
| Figure 13 says 48 backlog items, tables say 47 | Counts now come from one source, the Jira export of November 22: 85 tasks, 49 done, 10 active, 26 to do |
| Entity example looked numeric (5 → 2 minutes) | Real gold-set swap of an organization name |
| Section 9.4 mixed samples per item with number of pairs | Section 10.6 separates them |
| Figures 2 and 4 too small to read | Architecture and ERD redrawn top to bottom at readable size |

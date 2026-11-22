# 14. Interfaces and User Experience

## 14.1 Command-Line Interface

The command-line tool, built with Typer, is the primary interface in both semesters. It is installed as the `mtlj` command. {tab:cli} lists its commands.

Table: CLI commands {#cli}
| Command | Purpose | Status |
|---|---|---|
| `mtlj version` | Print the package version | Done |
| `mtlj audit run` | Generate mutants, score them with a judge, and report how the judge did | Done (MTLJ-58) |
| `mtlj audit show <run>` | Print a finished run's results again from its directory or results.json | Done |
| `mtlj audit resume <run>` | Continue an interrupted run without rescoring completed work | Planned (MTLJ-56) |
| `mtlj db import <run>` | Load a run directory into PostgreSQL for the dashboard | Planned (SD2) |

{tab:cli_opts} lists the options of `mtlj audit run`. Each option can also come from an audit file, and an option given on the command line overrides the file.

Table: Options of mtlj audit run {#cli_opts}
| Option | Default | Meaning |
|---|---|---|
| `--config PATH` | none | A TOML audit file with any of the settings below |
| `--dataset NAME or PATH` | required | `gold` (20 records), `corpus-v1` (919 records), or a RAGTruth .jsonl file |
| `--judge NAME` | `deepeval-faithfulness` | The judge to audit |
| `--model NAME` | judge's default (gpt-4o-mini) | The model the judge uses |
| `--battery SPEC` | `default` | `default`, a comma-separated subset of it, or a battery .toml file |
| `--samples N` | 5 | Scorings per text; at least 2, since noise needs two |
| `--seed N` | 0 | Base seed for generating mutants |
| `--limit N` | all | Use only the first N records |
| `--concurrency N` | 4 | Judge calls in flight at once |
| `--output DIR` | new folder under runs/ | An empty directory for the run |
| `--yes` | off | Skip the confirmation for runs over 1,000 scorings |

The command exits with code 0 on success, 1 if the run stops early or the user declines a large run, and 2 if any setting is invalid. Before scoring it prints the plan, for example "3 originals and 20 mutants, 5 scorings each: 115 scorings with deepeval-faithfulness (gpt-4o-mini)." Afterwards it prints the judge, dataset, status, scoring counts, noise floor, and one line per operator and intensity with the rate, its interval and which kind it is, the hit count, and the mean drop. Each run saves its settings as `config.toml`, so `--config runs/example/config.toml --output runs/again` repeats it exactly.

## 14.2 Configuration Files

An audit file makes a run reviewable and repeatable instead of being rebuilt from shell history. Its keys match the options in {tab:cli_opts}, and relative paths are read from the file's own directory. Every key is checked before anything runs: an unknown key, a value of the wrong type, or a battery that does not load is an error that names the file and the key. A battery file lists operators by registered name, each with its intensities in ascending order, and the loader checks both. `configs/audit.example.toml` and `configs/battery.example.toml` are the study's setup, with every key explained in comments.

## 14.3 Web API

The FastAPI service {ref:fastapi} runs on port 8000 and publishes interactive OpenAPI documentation at `/docs`. In Senior Design I it exposes health and diagnostic routes that the stack-status page uses to confirm the frontend, API, database, and outbound network are all working. {tab:api_now} lists them.

Table: Current API endpoints {#api_now}
| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/health` | Liveness check used by Docker's health check | `{"status": "ok"}` |
| GET | `/testing` | List the diagnostic checks | Array of route name, path, and description |
| GET | `/testing/postgres` | Raw connection to PostgreSQL | Check result with status and detail; 503 on failure |
| GET | `/testing/database` | Round trip through the ORM and the app's own table | Check result; 503 on failure |
| GET | `/testing/external-api` | Outbound HTTPS from the API container | Check result; 503 on failure |

The results API (MTLJ-55) is planned for Sprint 6. Every endpoint is read-only, returns JSON, and maps directly onto the results file and the database tables in Section 13. {tab:api_planned} lists the planned endpoints.

Table: Planned results API endpoints (Senior Design II) {#api_planned}
| Method | Path | Purpose | Main response fields |
|---|---|---|---|
| GET | `/runs` | List audit runs, newest first; filter by judge, model, dataset, or status; paginated | run_id, judge, model, dataset, status, n_records, samples, finished_at |
| GET | `/runs/{run_id}` | One run's full results for the judge profile view | The results.json document |
| GET | `/runs/{run_id}/operators/{name}` | One operator's pooled and per-intensity results and dose-response curve | overall, by_intensity, dose_response |
| GET | `/runs/{run_id}/mutants` | Mutants with their outcome; filter by operator, intensity, and outcome (missed, penalized, rewarded); paginated | mutant_id, record_id, operator, intensity, what changed, original mean, mutant mean, delta, outcome |
| GET | `/runs/{run_id}/mutants/{mutant_id}` | Everything the mutant explorer shows | original and edited answer, context, every score, verdict counts, provenance |
| GET | `/compare?a={run_id}&b={run_id}` | Difference in each rate between two runs, with an interval | per operator and intensity: rate a, rate b, difference, interval |
| GET | `/operators` | The operator registry with metadata | name, family, expected direction, version, parameters |
| GET | `/runs/{run_id}/download` | The run's results.json as a file | application/json attachment |

Errors follow FastAPI's convention: 404 with a detail message for an unknown run or mutant, and 422 for invalid query parameters. Because every route is read-only and the data is public, the API has no authentication. If it is ever exposed with private data, an API key or single sign-on would be added at the hosting layer. Cross-origin requests are allowed only from the configured frontend origin.

## 14.4 Frontend Technology Choices

The dashboard is a Senior Design II deliverable. The Nuxt 4 {ref:nuxt} application with PrimeVue 4 {ref:primevue} is already scaffolded and running in Docker Compose, where its first page is the stack-status check. {tab:fe_choice} records the options considered.

Table: Frontend options considered {#fe_choice}
| Layer | Options considered | Chosen | Reason |
|---|---|---|---|
| Framework | Nuxt (Vue), Next.js (React), Streamlit, Plotly Dash | Nuxt 4 | Server-side rendering or static export with one codebase; the full-stack lead's experience; TypeScript strict mode; Streamlit and Dash are quick but hard to shape into the designed views |
| Component library | PrimeVue, Vuetify, Tailwind with headless components | PrimeVue 4 (Aura theme) | Ready-made data tables with sorting and pagination, which the mutant table needs; documented keyboard and screen-reader support; theming through design tokens |
| Charts | Plotly.js, Apache ECharts, Chart.js | Plotly.js (planned) | Error bars and shaded interval bands built in; hover detail; the same library can render the curves the statistics code produces |
| Language and checks | JavaScript, TypeScript | TypeScript, ESLint, vue-tsc | Type errors caught in CI before review |

## 14.5 Dashboard Mockups

The team made high-fidelity Figma mockups of four views (MTLJ-100) to settle the layout before building anything. The numbers in the mockups are made-up example data, not results.

The **judge profile** ({fig:mock_profile}) covers one run of one judge. It opens with a plain-language summary, then the two headline rates and the noise floor, then a chart of every operator's rate with its interval, split into "should lower the score" and "should leave it alone." Weak spots are flagged, small charts show the two main dose-response curves, and a table at the bottom lists the edits the judge got wrong.

![Figma mockup of the judge profile view (MTLJ-52), with example data.](diagrams/mock_profile.png){width=5.6 #mock_profile}

The **comparison view** ({fig:mock_compare}) puts two runs side by side, such as DeepEval against Ragas, or the same judge on a hosted model and a local one. It shows the difference in each rate with an interval; where the interval includes zero, the judges are not clearly different.

![Figma mockup of the comparison view (MTLJ-51), with example data.](diagrams/mock_compare.png){width=5.6 #mock_compare}

The **dose-response view** ({fig:mock_dose}) plots each operator's rate at each strength, which shows how much of an edit a judge can take before it slips. The **mutant explorer** ({fig:mock_explorer}) shows one edited answer in full, with the change highlighted, the context, every repeated score, the judge's claim-by-claim verdicts, and the provenance. It turns a statistic into something a developer can inspect.

![Figma mockup of the dose-response view (MTLJ-53), with example data.](diagrams/mock_dose.png){width=5.6 #mock_dose}

![Figma mockup of the mutant explorer, with example data.](diagrams/mock_explorer.png){width=5.6 #mock_explorer}

## 14.6 UI Palette

The palette is deliberately quiet. The dashboard is a reading tool, and color is reserved for meaning: green for a judge behaving as it should, crimson for a weak spot, and amber for a caution label. {fig:palette} shows the palette and {tab:palette} gives each color's contrast ratio against the page and card backgrounds, computed with the WCAG 2.2 formula {ref:wcag}.

![Dashboard UI palette with each color's contrast ratio against the page background.](diagrams/chart_palette.png){width=6.2 #palette}

Table: Palette colors and WCAG contrast ratios {#palette}
| Token | Hex | Used for | On white | On page | WCAG AA text (4.5:1) |
|---|---|---|---|---|---|
| Ink | #1F2A44 | Headings, body text, primary button | 14.3:1 | 13.0:1 | Pass |
| Muted text | #5B6270 | Captions, axis labels, secondary text | 6.1:1 | 5.6:1 | Pass |
| Good | #2E6B4F | Detection rates; behaving as expected | 6.3:1 | 5.8:1 | Pass |
| Weak spot | #B3304D | Spurious sensitivity; missed mutants | 6.1:1 | 5.6:1 | Pass |
| Caution | #8A5A00 | "Penalized" label text | 5.9:1 | 5.4:1 | Pass |
| Page | #F4F5F0 | Page background | not text | not text | n/a |
| Surface | #FFFFFF | Cards and tables | not text | not text | n/a |
| Interval band | #F3D6DC | Shaded 95% interval behind a curve | not text | not text | n/a |

> NOTE: The hex values were sampled from the exported mockup images. Confirm them against the color styles in the Figma file and update this table if they differ.

## 14.7 Accessibility

Accessibility was considered from the mockups onward rather than added at the end. The dashboard targets WCAG 2.2 level AA {ref:wcag}:

- **Contrast.** Every text color in the palette passes the 4.5:1 ratio for normal text on both backgrounds ({tab:palette}). Chart marks and interval bands meet the 3:1 ratio for non-text graphics against their background, or are outlined.
- **Color is never the only signal.** Every colored rate also has a text label ("Weak spot," "Penalized," "Missed") and a number. The two families are separated by headings, not only by color, so the views work for readers with color-vision deficiencies and in grayscale print.
- **Charts have text alternatives.** Each chart has a caption describing its message and a data table equivalent, which is also how screen-reader users reach the numbers. Plotly charts receive an accessible name and description.
- **Keyboard and screen readers.** PrimeVue components implement WAI-ARIA patterns for tables, tabs, and menus. The team will verify tab order, visible focus, and skip links on every view. Nuxt's route announcer, already in the application shell, announces page changes to screen readers.
- **Plain language.** Each view opens with a one-sentence summary, such as "This judge catches about three in five wrong facts," before any statistics, and defines each term where it first appears.
- **Testing.** Automated checks with axe-core will run in the end-to-end tests (Section 18), and each view will be checked manually with a screen reader and at 200% zoom before the dashboard milestone.

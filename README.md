# mtlj — Mutation Testing for LLM Judges

LLM judges (DeepEval, Ragas, Promptfoo, ...) score AI answers. We test the
judges: we change ("mutate") answers in ways that *should* or *shouldn't*
change the score, then use statistics to check whether the judge notices.

## Get started (once)

1. Install **[Docker Desktop](https://docs.docker.com/get-started/get-docker/)** and **[Git](https://git-scm.com/downloads)**.
2. Open Docker Desktop and leave it running.
3. In a terminal (on Windows, use a short folder like `C:\code`. Some packages have very long file paths):
   ```bash
   git clone https://github.com/bduffaut/Mutation-Testing.git
   cd Mutation-Testing
   docker compose up
   ```
4. Wait until the messages slow down (the first time takes a few minutes), then open
   **http://localhost:3000**. You should see a table of green checks. 🎉

That's it. You don't need to install Python, Node or a database.

## Every day

```bash
docker compose up
```

Edit code and save. The website and API reload by themselves within a few seconds.
Stop with **Ctrl+C**.

| Address | What it is |
|---|---|
| http://localhost:3000 | The website |
| http://localhost:8000/docs | The API. Click any route, then **Try it out** |

## Run an audit

Add `OPENAI_API_KEY` to `.env` (see [How to develop](docs/DEVELOPER.md)), then:

```bash
uv run mtlj audit run --dataset gold --limit 3 --output runs/example
```

This scores 3 records and their mutants, 5 times each. It's 115 scorings, about 4 minutes and
under 15 cents with `gpt-4o-mini`. It prints (shortened):

```text
Planned: 3 originals and 20 mutants, 5 scorings each: 115 scorings with deepeval-faithfulness (gpt-4o-mini).

Judge: deepeval-faithfulness (gpt-4o-mini, version 4.2.8)
Dataset: gold (3 records)
Status: complete
Samples: 5, alpha: 0.05
Scorings: 115 ok, 0 failed
Noise floor: 0.000
Above chance: corrected across 7 tests (benjamini-hochberg, q = 0.05)

Detection rate (degrading operators)
  entity_swap: 50.0% [9.5%, 90.5%] (Wilson), 1/2 detected, mean drop +0.049, excluded no_change 3
    intensity 0.2: 50.0% [9.5%, 90.5%] (Wilson), 1/2 detected, mean drop +0.049, excluded no_change 1
    ...

Spurious sensitivity (preserving operators, read against 5%)
  ...
  verbosity_padding: 8.3% [1.5%, 35.4%] (Wilson), 1/12 flagged (1 penalized, 0 rewarded), mean drop +0.002
    ...
    dose-response: ...

Saved to runs/example
```

- **Detection rate:** how many mutants that should score lower did, by more than noise.
- **Spurious sensitivity:** how many mutants that shouldn't change the score did. It's read
  against 5%, the rate noise alone gives.
- **Intervals** are 95%. Each line says which kind: the bootstrap from 100 testable mutants up,
  Wilson below that. **Above chance** is after correcting for the number of tests in the run.
- **Dose-response** is the intensity at which the judge starts to react, for operators run at
  more than one intensity.

Three records are only enough to try it: the intervals are wide, and these records never vary,
so the noise floor is 0. Use `--dataset gold` or `--dataset corpus-v1` for real numbers. All of
`corpus-v1` is tens of thousands of scorings, so `mtlj` asks before any run over 1,000. Where
nobody can answer, such as under `nohup` or in CI, add `--yes`: without it the question gets no
answer, and the command prints "Aborted." and exits 1 before any scoring.

`--dataset` also takes a RAGTruth `.jsonl` file. See `uv run mtlj audit run --help` for the rest.
The command exits 1 if the run stops early.

### From a config file

An audit file sets the same options, so a run can be reviewed and repeated instead of rebuilt
from shell history. [`configs/audit.example.toml`](configs/audit.example.toml) is the study's
setup, with each key explained:

```bash
uv run mtlj audit run --config configs/audit.example.toml --limit 3
```

Options given on the command line override the file, like `--limit 3` here, and can also supply
a key the file leaves out. Relative paths in the file are read from the file's directory. Every
setting is checked before anything runs: a mistyped key, a value of the wrong type or a battery
that doesn't load stops the command with exit code 2 and a message naming the file and key, or
the option, it came from.

Each run saves the settings it used as `config.toml` in its directory, including the judge's
model and, in `battery.toml`, the exact operators and intensities it ran. `--config
runs/example/config.toml --output runs/again` reruns them. A dataset
file is referenced by its absolute path rather than copied, so a rerun reads it as it is then;
`originals.jsonl` holds the exact records the first run used. Print a run's results again with
`uv run mtlj audit show runs/example`. [Audit results](docs/results.md) explains the files.

## What's where

```
src/mtlj/              The Python code
├── operators/           Mutations: changes made to answers  ← most work starts here
│   ├── degrading/         ...that should LOWER a judge's score
│   └── preserving/        ...that should NOT change it
├── judges/              Connects to the judges we test (DeepEval, Ragas, ...)
├── stats/               Statistics on the results
├── cli/                 The `mtlj` command
└── api/                 The web API the website calls
tests/                 Tests, laid out the same way as src/mtlj/
frontend/              The website (Nuxt + PrimeVue)
scratch/               Your playground. Never committed
docs/                  How to develop, and the team rules
migrations/            Database changes (generated for you)
docker/                How Docker builds the app (rarely touched)
scripts/check.py       Run before every pull request
compose.yaml           What `docker compose up` starts
pyproject.toml         Python packages and tool settings
```

You can ignore the rest: `uv.lock` and `frontend/package-lock.json` are
generated, `.github/` runs the automatic checks, and the dotfiles configure tools.

## Next

- **[How to develop](docs/DEVELOPER.md)**: running Python on your machine, how-tos, fixing problems
- **[Audit results](docs/results.md)**: what a run leaves behind and the `results.json` format
- **[Rules for contributing](docs/CONTRIBUTING.md)**: how Jira and GitHub work together, Git step by step, pull requests, code style

## Tech stack

Python 3.12 (uv, FastAPI, SQLAlchemy, Alembic, Typer, spaCy, NLTK,
NumPy/SciPy/statsmodels, DeepEval, Ragas) · Postgres 16 · Nuxt 4 + PrimeVue 4 ·
Docker Compose · GitHub Actions

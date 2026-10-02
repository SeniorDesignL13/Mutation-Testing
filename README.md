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
- **[Rules for contributing](docs/CONTRIBUTING.md)**: Git step by step, pull requests, code style

## Tech stack

Python 3.12 (uv, FastAPI, SQLAlchemy, Alembic, Typer, spaCy, NLTK,
NumPy/SciPy/statsmodels, DeepEval, Ragas) · Postgres 16 · Nuxt 4 + PrimeVue 4 ·
Docker Compose · GitHub Actions

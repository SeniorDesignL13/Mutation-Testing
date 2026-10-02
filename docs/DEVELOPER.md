# How to develop

New here? Do the [README](../README.md) setup first.

## What's in the box

```
Browser ──▶ website (fe/, :3000) ──▶ API (src/mtlj/service/, :8000) ──▶ database (Postgres, :5432)
                                          │
                                          ▼
                          the engine: operators / judges / stats
                     (plain Python in src/mtlj/, also used by the CLI and tests)
```

Most of the work is **the engine**: mutation operators, judge adapters and
statistics. It's a plain Python library. You run it from a test, a script, or
the CLI. When something is ready for the website, an API route calls it.

## Two ways to run Python

| | **In Docker** | **On your machine** |
|---|---|---|
| Good for | The website and API | Engine work (operators, judges, stats), faster tests, editor autocomplete |
| Install | Nothing extra | [uv](https://docs.astral.sh/uv/getting-started/installation/), then `uv sync` once |
| Put this before a Python command | `docker compose exec api` | `uv run` |

**Every Python command works both ways. Only the start changes:**

| To... | On your machine | In Docker (with `docker compose up` running) |
|---|---|---|
| Run the tests | `uv run pytest` | `docker compose exec api pytest` |
| Check everything before a PR | `uv run python scripts/check.py` | `docker compose exec api python scripts/check.py` |
| Run a script | `uv run python scratch/try_it.py` | `docker compose exec api python scratch/try_it.py` |
| Use the CLI | `uv run mtlj --help` | `docker compose exec api mtlj --help` |

On your machine, tests that need the database are **skipped** and tell you
so. To include them, start just the database with `docker compose up -d db`.
Stop it later with `docker compose down`.

**VS Code:** open the repo folder and accept the suggested extensions. After `uv sync`,
VS Code uses the `.venv` folder, and Python files are formatted when you save.

## Your routine

1. **Get the latest code and make a branch**
   ```bash
   git switch main
   git pull
   git switch -c feat/my-change
   ```
2. **Start the app** (if you need the website or API): `docker compose up`
3. **Write code and tests.** Save, and it reloads.
4. **Check your work.** This fixes formatting for you and runs the tests:
   ```bash
   uv run python scripts/check.py      # Python
   docker compose exec fe npm run check  # website (only if you changed fe/)
   ```
5. **Commit, push, open a pull request.** See [CONTRIBUTING](CONTRIBUTING.md#git-step-by-step).

## Where code goes

| You're building... | Put it in | Its tests go in |
|---|---|---|
| A mutation operator | `src/mtlj/operators/degrading/` or `preserving/` | `tests/operators/` |
| A judge adapter | `src/mtlj/judges/` | `tests/judges/` |
| Statistics | `src/mtlj/stats/` | `tests/stats/` |
| A CLI command | `src/mtlj/cli/main.py` | `tests/cli/` |
| An API route | `src/mtlj/service/routers/` | `tests/service/` |
| A database table | `src/mtlj/service/models/` | `tests/service/` |
| A web page | `fe/app/pages/` | Try it in the browser |
| A quick experiment | `scratch/` (git ignores it, so it's never committed) | — |

## How to...

### Add a Python package

```bash
uv add --group operators some-package
```

Pick the group the package is for: `operators`, `judges`, `stats`, `service` or `dev`.
Commit `pyproject.toml` and `uv.lock` together. Docker installs it next time
anyone runs `docker compose up`.

### Add a website package

```bash
docker compose exec fe npm install some-package
```

Commit `fe/package.json` and `fe/package-lock.json` together.

### Add a database table

1. Create the model in `src/mtlj/service/models/` (copy `connection_check.py`).
2. Import it in `src/mtlj/service/models/__init__.py`.
3. Create a migration (the script that changes the database) and **read it**:
   ```bash
   docker compose exec api alembic revision --autogenerate -m "add runs table"
   ```
4. Restart the API to apply it: `docker compose restart api`. Teammates get it
   applied automatically.

If you forget step 3, the tests tell you.

### Add an API route

1. Create a router in `src/mtlj/service/routers/` (copy `testing.py`).
2. Register it in `src/mtlj/service/main.py` with `app.include_router(...)`.
3. Try it at http://localhost:8000/docs.

### Add a web page

Create `fe/app/pages/my-page.vue`. It appears at http://localhost:3000/my-page.
Put API calls in `fe/app/composables/` (copy `useConnectionChecks.ts`).
Components come from [PrimeVue 4](https://v4.primevue.org/) and need no imports.

### Use an LLM or spaCy

- **API keys** (e.g. `OPENAI_API_KEY`): `cp .env.example .env`, then fill them in.
  Never commit `.env`. Git already ignores it.
- **Local LLMs:** `docker compose --profile models up`, then download one with
  `docker compose exec ollama ollama pull llama3.2`. Python code in Docker
  reaches it at `http://ollama:11434`. Code on your machine uses `http://localhost:11434`.
- **spaCy models** are listed in `pyproject.toml` under `[tool.uv.sources]`
  (see `en-core-web-sm`). Add new ones there. Don't use `spacy download`.

## When something goes wrong

| Problem | Fix |
|---|---|
| `failed to connect to the docker API` or `Cannot connect to the Docker daemon` | Docker Desktop isn't running. Open it and wait for it to start |
| `port is already allocated` | `cp .env.example .env` and change `API_PORT`, `FE_PORT` or `POSTGRES_PORT` |
| A row on http://localhost:3000 is red | Read its **Detail** column |
| CI says "uv.lock doesn't match" | Run `uv lock`, then commit `uv.lock` |
| CI says files "aren't formatted" | Run `uv run python scripts/check.py`, then commit |
| The database is in a weird state | `docker compose down -v` (**deletes** its data), then `docker compose up` |
| Anything else | `docker compose down -v`, `docker compose build --no-cache`, `docker compose up` |

See what a container is doing: `docker compose logs -f api` (or `fe`, `db`).

## Good to know

- **Dependabot** opens PRs that update packages. If CI is green, merge them.
- **We stay on PrimeVue 4** because version 5 and later need a paid license.
- **Python, Node and Postgres versions** are set in the Dockerfiles, CI and
  `.python-version`. Upgrade them in one PR that changes every place.

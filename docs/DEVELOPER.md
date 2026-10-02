# How to develop

New here? Do the [README](../README.md) setup first.

## How it fits together

```
Browser ──▶ frontend/ (:3000) ──▶ src/mtlj/api/ (:8000) ──▶ database (Postgres, :5432)
                                        │
                                        ▼
                    the engine: src/mtlj/operators, judges, stats
                    (plain Python, also used by the CLI and tests)
```

Most of the work is **the engine**: mutation operators, judge adapters and
statistics. It's a plain Python library. You run it from a test, a script in
`scratch/`, or the CLI. When something is ready for the website, an API route calls it.

## Two ways to run Python

| | **On your machine** | **In Docker** |
|---|---|---|
| Good for | Engine work, fast tests, editor autocomplete | The website and API |
| Install | [uv](https://docs.astral.sh/uv/getting-started/installation/), then run `uv sync` once | Nothing extra |
| Put this before a Python command | `uv run` | `docker compose exec api` |

**Every Python command works both ways. Only the start changes:**

| To... | On your machine | In Docker (while `docker compose up` runs) |
|---|---|---|
| Run the tests | `uv run pytest` | `docker compose exec api pytest` |
| Check everything before a PR | `uv run python scripts/check.py` | `docker compose exec api python scripts/check.py` |
| Run a script | `uv run python scratch/try_it.py` | `docker compose exec api python scratch/try_it.py` |
| Use the CLI | `uv run mtlj --help` | `docker compose exec api mtlj --help` |

On your machine, tests that need the database are **skipped**, and pytest
says why. To include them, start just the database with `docker compose up -d db`.
Stop it later with `docker compose down`.

**VS Code:** open the repo folder and accept the suggested extensions. After
`uv sync`, VS Code finds the `.venv` folder by itself, and Python files are
formatted when you save.

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
   uv run python scripts/check.py              # Python
   docker compose exec frontend npm run check  # website (only if you changed frontend/)
   ```
5. **Commit, push, open a pull request.** See [CONTRIBUTING](CONTRIBUTING.md#git-step-by-step).

## Where code goes

| You're building... | Put it in | Its tests go in |
|---|---|---|
| A mutation operator | `src/mtlj/operators/degrading/` or `preserving/` | `tests/operators/` |
| A judge adapter | `src/mtlj/judges/` | `tests/judges/` |
| Statistics | `src/mtlj/stats/` | `tests/stats/` |
| A CLI command | `src/mtlj/cli/main.py` | `tests/cli/` |
| An API route | `src/mtlj/api/routers/` | `tests/api/` |
| A database table | `src/mtlj/api/models/` | `tests/api/` |
| A web page | `frontend/app/pages/` | Try it in the browser |
| A quick experiment | `scratch/` (never committed) | — |

Make a folder under `tests/` the first time you need it. Tests don't need `__init__.py` files.

### Example: an operator and its test

```python
# src/mtlj/operators/degrading/negation.py
def negate(text: str) -> str:
    """Flip the first "is" to "is not", which should make a correct answer wrong."""
    return text.replace(" is ", " is not ", 1)
```

```python
# tests/operators/degrading/test_negation.py
from mtlj.operators.degrading.negation import negate


def test_negate_flips_the_first_is() -> None:
    assert negate("Paris is in France.") == "Paris is not in France."
```

Run it with `uv run pytest tests/operators`.

## How to...

### Add a Python package

```bash
uv add some-package          # used by the code
uv add --dev some-package    # only a tool for developing (like pytest)
```

Commit `pyproject.toml` and `uv.lock` together. Docker installs it the next
time anyone runs `docker compose up`.

### Add a website package

```bash
docker compose exec frontend npm install some-package
```

Commit `frontend/package.json` and `frontend/package-lock.json` together.

### Add a database table

1. Create the model in `src/mtlj/api/models/` (copy `connection_check.py`).
2. Import it in `src/mtlj/api/models/__init__.py`.
3. Generate a migration (the file that changes the database), then **read it**:
   ```bash
   docker compose exec api alembic revision --autogenerate -m "add runs table"
   ```
   It appears in `migrations/versions/`.
4. Restart the API to apply it: `docker compose restart api`. Teammates' databases
   update the next time they start the app.

If you forget step 3, the tests tell you.

### Add an API route

1. Create a router in `src/mtlj/api/routers/` (copy `testing.py`).
2. Register it in `src/mtlj/api/main.py` with `app.include_router(...)`.
3. Try it at http://localhost:8000/docs.

### Add a web page

Create `frontend/app/pages/my-page.vue`. It appears at http://localhost:3000/my-page.
Put API calls in `frontend/app/composables/` (copy `useConnectionChecks.ts`).
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
| Windows: `ModuleNotFoundError` for a package that is installed | The repo folder's path is too long for Windows. Move it somewhere short like `C:\code\Mutation-Testing`, delete `.venv`, run `uv sync` |
| `port is already allocated` | `cp .env.example .env` and change `API_PORT`, `FRONTEND_PORT` or `POSTGRES_PORT` |
| A row on http://localhost:3000 is red | Read its **Detail** column |
| CI says "uv.lock doesn't match" | Run `uv lock`, then commit `uv.lock` |
| CI says files "aren't formatted" | Run `uv run python scripts/check.py`, then commit |
| The database is in a weird state | `docker compose down -v` (**deletes** its data), then `docker compose up` |
| Anything else | `docker compose down -v`, `docker compose build --no-cache`, `docker compose up` |

See what a container is doing: `docker compose logs -f api` (or `frontend`, `db`).

## Good to know

- **Dependabot** opens PRs that update packages. If CI is green, merge them.
- **We stay on PrimeVue 4** because version 5 and later need a paid license.
- **Upgrading Python, Node or Postgres** means changing every place the version is set,
  in one PR:
  - Python: `.python-version`, `docker/Dockerfile.api`
  - Node: `docker/Dockerfile.frontend`, `.github/workflows/ci.yml`, `frontend/package.json`
  - Postgres: `compose.yaml` (CI uses the same file)

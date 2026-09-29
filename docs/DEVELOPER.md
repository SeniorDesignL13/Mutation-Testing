# Developer guide

How to run the project, how the pieces fit together, and the step-by-step
process for adding Python code, ML/NLP models, database tables, API routes, and
UI. Conventions (naming, style, git) are in [CONTRIBUTING.md](CONTRIBUTING.md).

- [1. Running the stack](#1-running-the-stack)
- [2. How the project fits together](#2-how-the-project-fits-together)
- [3. Working on the Python engine](#3-working-on-the-python-engine)
- [4. Adding ML / NLP models](#4-adding-ml--nlp-models)
- [5. Adding database tables](#5-adding-database-tables)
- [6. Exposing engine code through the API](#6-exposing-engine-code-through-the-api)
- [7. Showing it in the frontend](#7-showing-it-in-the-frontend)
- [8. Managing dependencies](#8-managing-dependencies)
- [9. Configuration](#9-configuration)
- [10. Optional setups](#10-optional-setups)
- [11. Troubleshooting](#11-troubleshooting)

---

## 1. Running the stack

Install [Docker Desktop](https://docs.docker.com/get-started/get-docker/), then from the repo root:

```bash
docker compose up --build
```

| Service | URL | What it runs |
|---|---|---|
| `fe` | http://localhost:3000 | Nuxt dev server. The home page is a stack status table. |
| `api` | http://localhost:8000 | FastAPI via uvicorn. Interactive API docs at http://localhost:8000/docs |
| `db` | `localhost:5432` | Postgres 16 (user / password / db: `mtlj`) |

Open http://localhost:3000. Every row in the table should be green. If one
isn't, its *Detail* column says why.

Stop with `Ctrl+C` or `docker compose down`. Add `-v` to also wipe the database.

### What happens on startup

1. `db` starts and waits until Postgres accepts connections.
2. `api` runs `alembic upgrade head` (applies any new migrations), then starts
   uvicorn with auto-reload watching `src/`.
3. `fe` reinstalls npm packages only if `package-lock.json` changed, then
   starts `nuxt dev`.

Your checkout is mounted into the containers, so **saving a file is enough**:
the API restarts in about a second, and the browser hot-updates for frontend edits.

### When do I need `--build`?

| You changed... | Do this |
|---|---|
| Python / Vue source | Nothing, it reloads |
| `pyproject.toml` / `uv.lock` | `docker compose up --build` |
| `fe/package.json` / `package-lock.json` | `docker compose up --build` |
| A Dockerfile or `compose.yaml` | `docker compose up --build` |
| A migration (someone else's, after `git pull`) | Restart `api` (`docker compose restart api`) or run `alembic upgrade head` |

`docker compose up --build` is always safe and fast when nothing changed.

### Everyday commands

Run from the repo root while the stack is up. `exec` runs a command inside
the already-running container, with exactly the same Python, packages, and
database as the app.

```bash
docker compose exec api pytest                  # tests
docker compose exec api ruff check .            # lint
docker compose exec api ruff format .           # format
docker compose exec api mtlj --help             # CLI
docker compose exec api python                  # Python REPL with everything installed
docker compose exec api bash                    # shell in the backend container
docker compose exec db psql -U mtlj             # SQL shell
docker compose logs -f api                      # follow logs
```

---

## 2. How the project fits together

```
                ┌────────────────────────── api container ───────────────────────────┐
 Browser        │                                                                     │
 localhost:3000 │   service/  (FastAPI)          cli/  (Typer)         tests/          │
  ┌────────┐    │   HTTP in, JSON out            terminal in/out       pytest          │
  │ fe/    │───────▶ routers → schemas              │                    │             │
  │ Nuxt + │    │        │                          │                    │             │
  │PrimeVue│    │        ▼                          ▼                    ▼             │
  └────────┘    │   ┌──────────── the engine (plain Python library) ────────────┐    │
                │   │  operators/        judges/            stats/               │    │
                │   │  mutate text       call LLM judges    analyze results      │    │
                │   └────────────────────────────────────────────────────────────┘    │
                │        │ service/models + db.py                                     │
                └────────┼────────────────────────────────────────────────────────────┘
                         ▼
                   db container (Postgres)
```

The key idea: **the engine (`operators/`, `judges/`, `stats/`) is a plain
Python library.** It doesn't run on its own and knows nothing about HTTP,
the database, or the frontend. It's a set of functions and classes that
other code calls.

There are three ways to call it, and all three run in the same container
with the same dependencies:

| Entry point | Used for | How |
|---|---|---|
| **Tests** | Proving the code works | `docker compose exec api pytest` |
| **CLI** (`mtlj`) | Running things by hand while building: experiments, batch runs | `docker compose exec api mtlj <command>` |
| **API** (`service/`) | The web app, once a feature is ready for the UI | Browser → FastAPI route → engine |

So while you're building an operator or a judge adapter, you work in the
engine plus tests, and try it out through the CLI. Nothing needs to "run in the
background." When the feature is ready for the app, you add a thin API route
that calls **the exact same function**. Because the CLI, tests, and API all
share one container image built from one lockfile, "works on the CLI" means
"works in the app."

The web server that's running (`uvicorn`) only serves HTTP requests. It does not
run your engine code until a route calls it.

---

## 3. Working on the Python engine

### The loop

1. Write the code in the right package, e.g. `src/mtlj/operators/degrading/entity_swap.py`.
2. Write tests next to it in the mirrored path, e.g. `tests/operators/degrading/test_entity_swap.py`.
3. Run the tests: `docker compose exec api pytest tests/operators -x`.
4. Try it by hand via a CLI command (below) or the REPL:
   `docker compose exec api python`, then `>>> from mtlj.operators.degrading.entity_swap import ...`.
5. `ruff check . && ruff format .`, commit, PR.

### Worked example: turning a prototype into an operator

A prototype script like `entity_swap_test.py` at the repo root does its work
at import time (loads spaCy, runs an example, prints). To make it part of the
engine:

**1. Move the logic into a module, with no side effects at import time.**

```python
# src/mtlj/operators/degrading/entity_swap.py
"""Degrading operator: swap a place name for a different one."""

from functools import lru_cache

import spacy
from spacy.language import Language

PLACE_POOL = ("Madrid", "Berlin", "Rome", "Vienna", "Warsaw")


@lru_cache
def get_nlp() -> Language:
    """Load the spaCy pipeline once, on first use (not at import)."""
    return spacy.load("en_core_web_sm")


def entity_swap(text: str, swap_word: str = "Madrid") -> str:
    """Replace the first geopolitical entity (GPE) in ``text`` with ``swap_word``."""
    for ent in get_nlp()(text).ents:
        if ent.label_ == "GPE":
            return text.replace(ent.text, swap_word, 1)
    return text
```

**2. Test it.**

```python
# tests/operators/degrading/test_entity_swap.py
from mtlj.operators.degrading.entity_swap import entity_swap


def test_entity_swap_replaces_first_place() -> None:
    assert entity_swap("The treaty was signed in Lisbon.") == "The treaty was signed in Madrid."


def test_entity_swap_is_noop_without_places() -> None:
    assert entity_swap("Nothing to see here.") == "Nothing to see here."
```

**3. Add a CLI command so you (and teammates) can run it by hand.**

```python
# src/mtlj/cli/main.py
@app.command()
def swap(text: str, word: str = "Madrid") -> None:
    """Run the entity swap operator on TEXT."""
    from mtlj.operators.degrading.entity_swap import entity_swap

    typer.echo(entity_swap(text, word))
```

```bash
docker compose exec api mtlj swap "The treaty was signed in Lisbon in 2007."
```

That's the whole engine loop. The API route in [section 6](#6-exposing-engine-code-through-the-api)
will import the same `entity_swap` function.

### Rules that keep engine code app-ready

- **No work at import time.** Load models lazily (`@lru_cache` getter) so
  importing a module is instant and tests stay fast.
- **Engine code never imports from `service/` or `cli/`.** Dependencies point
  inward, so the engine can be called from anywhere.
- **Inputs and outputs are typed.** Use Pydantic models for anything
  non-trivial. Those same models can become API response schemas later.
- **Randomness takes a seed** so runs are reproducible.
- **Personal experiments** go in `scratch/` (git-ignored), not the repo root.
  Run them with `docker compose exec api python scratch/my_experiment.py` so
  they use the real environment.

---

## 4. Adding ML / NLP models

"Model" means two different things in this repo. This section is about
**ML/NLP models** (spaCy pipelines, LLMs). Database models are
[section 5](#5-adding-database-tables).

### spaCy (and other pip-installable model packages)

spaCy models are Python packages, but they're not on PyPI, so pin the exact
wheel URL in `pyproject.toml`. That captures them in `uv.lock`, and every
environment gets the identical model. `en_core_web_sm` is already set up this way:

```toml
[dependency-groups]
operators = ["en-core-web-sm", ...]

[tool.uv.sources]
en-core-web-sm = { url = "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl" }
```

To add another one (e.g. `en_core_web_md`), add the same two entries with its
URL from the [spacy-models releases](https://github.com/explosion/spacy-models/releases),
matching the installed spaCy minor version, then:

```bash
docker compose exec api uv lock
docker compose up --build
```

**Never** run `python -m spacy download ...`. It installs outside the lockfile,
works on your machine, and breaks for everyone else and in CI.

### NLTK data

NLTK corpora are data files, not packages. Download them at image build time
in `docker/Dockerfile.service` so they're baked in:

```dockerfile
RUN python -m nltk.downloader -d /opt/nltk_data punkt wordnet
ENV NLTK_DATA=/opt/nltk_data
```

### LLMs (the judges under test)

Judges call LLMs over HTTP. Nothing gets installed into the Python environment:

- **Hosted APIs** (OpenAI, Anthropic, etc.): put keys in `.env`, read them via
  `Settings` in `service/config.py`, never hardcode them.
- **Local models**: start the `models` profile
  (`docker compose --profile models up --build`). From inside the `api`
  container, Ollama is at `http://ollama:11434` and vLLM at `http://vllm:8000/v1`.
  Pull an Ollama model with `docker compose exec ollama ollama pull llama3.2`.
  Models persist in a Docker volume.

Unit tests must never call a real LLM. Mock the judge, and mark real-model
tests `@pytest.mark.integration`.

---

## 5. Adding database tables

Tables are defined as SQLAlchemy models in `src/mtlj/service/models/`, and
schema changes are applied with Alembic migrations. `ConnectionCheck`
(`models/connection_check.py`) is a working example.

**1. Define the model.**

```python
# src/mtlj/service/models/mutation_run.py
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from mtlj.service.db import Base


class MutationRun(Base):
    __tablename__ = "mutation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    operator: Mapped[str] = mapped_column(String(100))
    original_text: Mapped[str]
    mutated_text: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**2. Register it** in `src/mtlj/service/models/__init__.py` (Alembic only sees
models imported there):

```python
from mtlj.service.models.mutation_run import MutationRun
```

**3. Generate the migration, then read it.** Autogenerate misses renames and
some constraints.

```bash
docker compose exec api alembic revision --autogenerate -m "add mutation runs table"
# review alembic/versions/<timestamp>-<rev>_add_mutation_runs_table.py
```

**4. Apply it.**

```bash
docker compose exec api alembic upgrade head
```

(Teammates get it automatically the next time their `api` container starts.)

**5. Commit the model and the migration together.**

Useful commands:

```bash
docker compose exec api alembic current         # what revision the DB is at
docker compose exec api alembic history         # all migrations
docker compose exec api alembic downgrade -1    # undo the last one
docker compose down -v && docker compose up     # nuke the DB and rebuild from migrations
```

Never edit a migration that's already on `main`. Add a new one.

---

## 6. Exposing engine code through the API

When an engine feature is ready for the app, add a thin route. `routers/testing.py`
is a working example.

**1. Schemas** (`src/mtlj/service/schemas/mutations.py`):

```python
from pydantic import BaseModel


class EntitySwapRequest(BaseModel):
    text: str
    swap_word: str = "Madrid"


class EntitySwapResponse(BaseModel):
    original: str
    mutated: str
```

**2. Router** (`src/mtlj/service/routers/mutations.py`). It only translates
HTTP to engine calls:

```python
from fastapi import APIRouter

from mtlj.operators.degrading.entity_swap import entity_swap
from mtlj.service.schemas.mutations import EntitySwapRequest, EntitySwapResponse

router = APIRouter(prefix="/mutations", tags=["mutations"])


@router.post("/entity-swap", response_model=EntitySwapResponse)
def run_entity_swap(body: EntitySwapRequest) -> EntitySwapResponse:
    return EntitySwapResponse(original=body.text, mutated=entity_swap(body.text, body.swap_word))
```

Notes:
- CPU-bound engine code (spaCy, stats): use a plain `def` route. FastAPI runs it
  in a thread pool so it doesn't block the server.
- Async I/O (DB, LLM calls): use `async def` and take a session with
  `session: SessionDep` (from `mtlj.service.db`).

**3. Register it** in `src/mtlj/service/main.py`:

```python
from mtlj.service.routers import mutations, testing

app.include_router(mutations.router)
```

**4. Try it** at http://localhost:8000/docs. Every route appears there with a
"Try it out" button. Then add route tests in `tests/service/` (see
`tests/service/test_testing_routes.py`, which uses the `client` fixture from
`tests/conftest.py`).

---

## 7. Showing it in the frontend

The frontend lives in `fe/app/`. `pages/index.vue` +
`composables/useConnectionChecks.ts` is a working example of the pattern:

- **Pages** (`fe/app/pages/*.vue`) map to URLs automatically
  (`pages/mutations.vue` → `/mutations`).
- **Composables** (`fe/app/composables/use*.ts`) own API calls and state.
  Pages and components call composables and never `fetch` directly.
- Build the API URL from `useRuntimeConfig().public.apiBase`.
- **UI components** come from [PrimeVue 4](https://v4.primevue.org/) and are
  auto-imported (`<DataTable>`, `<Button>`, `<Card>`, ...). No import statements needed.

The browser calls the API at `http://localhost:8000`, not the Docker-internal
`http://api:8000`, so fetch data client-side (e.g. in `onMounted`), as the
status page does.

---

## 8. Managing dependencies

Python deps are pinned in `uv.lock`, frontend deps in `fe/package-lock.json`.
The Docker images, CI, and native installs all install **only** from these
lockfiles, and the build fails if a lockfile is out of date. That's what keeps
everyone's environment identical.

Never edit a lockfile by hand, and never `pip install` into a container
(it vanishes on rebuild and nobody else gets it).

### Python

Choose the group: `service` (web backend), `operators`, `judges`, `stats`,
`dev` (tooling), or no group for core deps the CLI needs.

```bash
docker compose exec api uv add --group stats pandas
docker compose exec api uv remove --group stats pandas
docker compose up --build        # bake it into the image
```

(With uv installed locally, `uv add ...` on the host does the same thing.)

Commit `pyproject.toml` + `uv.lock` together. Upgrades:
`uv lock --upgrade-package <name>`, or `uv lock --upgrade` for everything (in its own PR).

### Frontend

```bash
docker compose exec fe npm install <package>
docker compose exec fe npm install -D <package>
```

Commit `fe/package.json` + `fe/package-lock.json` together.

### Automated updates

Dependabot opens weekly grouped update PRs (Python, npm, Docker images,
GitHub Actions). CI must pass before they merge.

---

## 9. Configuration

Everything has a working default in `compose.yaml`. To override, create a
git-ignored `.env`:

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
|---|---|---|
| `API_PORT` / `FE_PORT` / `POSTGRES_PORT` | 8000 / 3000 / 5432 | Host ports, change if one is taken |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `mtlj` | Local database credentials |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Origins allowed to call the API |
| `ENABLE_TESTING_ROUTES` | `true` | Mounts the `/testing/*` diagnostic routes |
| `EXTERNAL_CHECK_URL` | `https://api.github.com` | Target for the outbound-HTTP check |
| `NUXT_PUBLIC_API_BASE` | `http://localhost:8000` | API URL the browser calls |

Backend settings live in `mtlj.service.config.Settings`. Add new ones there
with a safe default, and document them in `.env.example` and this table.
**Never commit secrets.**

**PrimeVue version.** We stay on PrimeVue 4 (`primevue` 4.x, `@primeuix/themes` 2.x,
`primeicons` 7.x) because those are MIT-licensed. From PrimeVue 5 / themes 3 / primeicons 8
onward they need a commercial license key. Dependabot is configured to skip those
major upgrades. Don't bump them by hand.

---

## 10. Optional setups

### VS Code Dev Container

With the *Dev Containers* extension: **Dev Containers: Reopen in Container**.
VS Code attaches to the `api` container with the interpreter, ruff, and
format-on-save configured. The rest of the stack starts alongside it.

### Native (non-Docker) Python

Useful for debugger breakpoints. Requires [uv](https://docs.astral.sh/uv/getting-started/installation/)
and Node 22+.

```bash
docker compose up -d db                          # just Postgres
uv sync                                          # .venv from uv.lock
uv run alembic upgrade head
uv run pytest
uv run uvicorn mtlj.service.main:app --reload    # stop the api container first (port 8000)

cd fe && npm ci && npm run dev                   # stop the fe container first (port 3000)
```

### Pre-commit hooks

Runs CI's lint/format checks before each commit: `uvx pre-commit install`.

---

## 11. Troubleshooting

**A row in the status table is red.** Read its *Detail* column.
- *Postgres* red: `docker compose ps db`, `docker compose logs db`.
- *App database* red but Postgres green: migrations aren't applied. Run
  `docker compose exec api alembic upgrade head`.
- *External API* red: no internet from Docker, or a proxy/firewall is blocking it.
- Every row red / "Could not reach the API": `api` isn't running. Check
  `docker compose logs api`.

**`port is already allocated`.** Set `API_PORT` / `FE_PORT` / `POSTGRES_PORT` in `.env`.

**Code changes aren't picked up.** Edit inside `src/` or `fe/`. File watching
polls (needed on Windows/macOS), so allow a second or two.

**"lockfile needs to be updated" build error.** `pyproject.toml` changed
without relocking. Run `docker compose run --rm api uv lock` and commit `uv.lock`.

**Frontend deps look stale.** `docker compose down && docker volume rm mtlj_fe_node_modules && docker compose up --build`.

**Database in a weird state.** `docker compose down -v` wipes it. Migrations rebuild it on the next `up`.

**Nuclear option.** `docker compose down -v --rmi local && docker compose build --no-cache && docker compose up`.

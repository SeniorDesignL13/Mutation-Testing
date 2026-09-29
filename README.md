# mtlj — Mutation Testing for LLM Judges

Mutation testing for LLM-as-judge evaluators (DeepEval, Ragas, Promptfoo, ...):
mutate prompts/responses in ways that should or shouldn't change a judge's
verdict, then use statistics to check whether the judge actually notices.

**Status:** early. The full stack (Postgres, FastAPI, Nuxt + PrimeVue) is wired
up end to end with diagnostic routes. Mutation operators, judge integrations,
and the real UI are next.

## Tech stack

| Area | Tools |
|---|---|
| Core engine | Python 3.12, Pydantic, Typer, asyncio, httpx, uv |
| Backend service | FastAPI, PostgreSQL, SQLAlchemy (async), Alembic |
| Frontend | Vue / Nuxt, PrimeVue, npm |
| Operators | spaCy, NLTK |
| Statistics | NumPy, SciPy, statsmodels |
| Judges under test | DeepEval, Ragas, Promptfoo, Ollama, vLLM |
| Infrastructure | Docker Compose, GitHub Actions |
| Dev tooling | pytest, ruff |

## Repo layout

```
src/mtlj/
  cli/            Typer CLI (console script: `mtlj`)
  service/        FastAPI app (`uvicorn mtlj.service.main:app`)
    models/       SQLAlchemy ORM models
    routers/      FastAPI routers
    schemas/      Pydantic request/response schemas
  operators/      Mutation operators
    degrading/    Expected to degrade judge scores
    preserving/   Expected to preserve judge scores
  judges/         Adapters for the judges under test
  stats/          Statistical analysis of mutation results
fe/               Nuxt frontend
alembic/          Database migrations
docker/           Dockerfiles
docs/             Project documentation
compose.yaml      Local dev stack
tests/            pytest suite
```

## Setup

### Prerequisites

- [Docker Desktop](https://docs.docker.com/get-started/get-docker/) (Windows: use the WSL2 backend, the default).
  That's the only thing to install. Python, Node, and Postgres all run in containers.
- Git

Check that Docker is running:

```bash
docker compose version
```

### Run it

```bash
git clone https://github.com/bduffaut/Mutation-Testing.git
cd Mutation-Testing
docker compose up --build
```

The first build takes a few minutes. Later runs are fast. When the logs settle:

| Open | You should see |
|---|---|
| http://localhost:3000 | A **stack status** table: API health, Postgres, app database, and external API checks, all green |
| http://localhost:8000/docs | Interactive API docs (try any route from the browser) |

No `.env` file is needed. To change ports or credentials, `cp .env.example .env`
and edit it.

### Everyday use

```bash
docker compose up                        # start (add --build after dependency changes)
docker compose down                      # stop (add -v to also wipe the database)
docker compose exec api pytest           # run tests
docker compose exec api ruff check .     # lint
docker compose exec api mtlj --help      # the CLI
```

Code edits in `src/` and `fe/` hot-reload. No restart needed.

## Documentation

- [Developer guide](docs/DEVELOPER.md): how the pieces fit together; adding
  Python code, ML/NLP models, database tables, API routes, and UI; dependencies; troubleshooting
- [Contributing](docs/CONTRIBUTING.md): git workflow, naming and code conventions

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs lint, format check, tests
(against a real Postgres), the frontend build, and a Docker boot check on
every push/PR to `main`.

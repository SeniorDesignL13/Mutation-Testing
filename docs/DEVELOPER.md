# How to develop

Setup is in the [README](../README.md). This page covers the day-to-day work.

## The workflow

1. **Update and start**
   ```bash
   git switch main && git pull
   docker compose up --build
   ```
2. **Make a branch**: `git switch -c feat/short-description` (types are listed in [CONTRIBUTING](CONTRIBUTING.md#branches))
3. **Write code and save.** The app reloads by itself.
4. **Check your work**
   ```bash
   docker compose exec api sh scripts/check.sh   # Python: fixes style, checks migrations, runs tests
   docker compose exec fe npm run check          # frontend: fixes style, checks types
   ```
5. **Push and open a PR**
   ```bash
   git push -u origin HEAD
   ```
   Then open the PR on GitHub. Title format: `feat(operators): add entity swap`.
6. **CI runs automatically.** Once it's green and a teammate approves, **squash and merge**.

Commands run with `docker compose exec` happen *inside* the container, with
the exact Python, packages, and database the app uses. There is nothing to
install on your machine.

## How the pieces fit

```
Browser ──▶ fe (Nuxt, :3000) ──▶ api (FastAPI, :8000) ──▶ db (Postgres, :5432)
                                     │
                                     ▼
                     the engine: operators / judges / stats
                     (plain Python, also used by the CLI and tests)
```

**The engine doesn't run on its own.** It's a Python library. Anything that
calls it runs it: a test, the CLI, or an API route. While building a feature,
use tests and the CLI. When it's ready for the app, add an API route that calls
the same function.

## Where code goes

| You're building... | Put it in | Test it in |
|---|---|---|
| A mutation operator | `src/mtlj/operators/degrading/` or `preserving/` | `tests/operators/` |
| A judge adapter | `src/mtlj/judges/` | `tests/judges/` |
| Statistics | `src/mtlj/stats/` | `tests/stats/` |
| A CLI command | `src/mtlj/cli/main.py` | `tests/cli/` |
| An API route | `src/mtlj/service/routers/` | `tests/service/` |
| A database table | `src/mtlj/service/models/` | via API route tests |
| A web page | `fe/app/pages/` | by hand, in the browser |
| A throwaway experiment | `scratch/` (ignored by git) | — |

Run an experiment with `docker compose exec api python scratch/my_idea.py`.

## How to...

### Add a Python package

```bash
docker compose exec api uv add --group operators some-package
```

Groups: `operators`, `judges`, `stats`, `service`, `dev`. Commit
`pyproject.toml` and `uv.lock` together. Teammates get the package the next
time they start the stack.

### Add a frontend package

```bash
docker compose exec fe npm install some-package
```

Commit `fe/package.json` and `fe/package-lock.json` together.

### Add a database table

1. Create the model in `src/mtlj/service/models/` (copy `connection_check.py`).
2. Import it in `src/mtlj/service/models/__init__.py`.
3. Generate a migration and **read it**:
   ```bash
   docker compose exec api alembic revision --autogenerate -m "add runs table"
   ```
4. Restart the API to apply it: `docker compose restart api`. It also
   applies automatically for teammates.

### Add an API route

1. Create a router in `src/mtlj/service/routers/` (copy `testing.py`).
2. Register it in `src/mtlj/service/main.py` with `app.include_router(...)`.
3. It appears at http://localhost:8000/docs, where you can try it.

### Add a web page

Create `fe/app/pages/my-page.vue`. It's served at `/my-page`. Put API calls
in a composable in `fe/app/composables/` (copy `useConnectionChecks.ts`). UI
components come from [PrimeVue 4](https://v4.primevue.org/) and need no imports.

### Use a spaCy model or LLM

- **spaCy models** are pinned in `pyproject.toml` under `[tool.uv.sources]`
  (see `en-core-web-sm`). Don't use `spacy download`, because it bypasses
  the lockfile.
- **Local LLMs**: `docker compose --profile models up --build`, then
  `docker compose exec ollama ollama pull llama3.2`. From Python, Ollama is
  at `http://ollama:11434`.
- **API keys** go in `.env` (copy `.env.example`). Never commit them.

## When something breaks

| Problem | Fix |
|---|---|
| A row on http://localhost:3000 is red | Read its *Detail* column |
| `port is already allocated` | `cp .env.example .env` and change `API_PORT` / `FE_PORT` / `POSTGRES_PORT` |
| CI says "uv.lock is out of date" | `docker compose exec api uv lock`, then commit `uv.lock` |
| Database is in a weird state | `docker compose down -v` (wipes it), then `docker compose up --build` |
| Anything else | `docker compose down -v && docker compose build --no-cache && docker compose up` |

Logs: `docker compose logs -f api` (or `fe`, `db`).

## Good to know

- **Why we're on PrimeVue 4:** v5 and later need a paid license. Dependabot
  won't propose the upgrade.
- **VS Code:** "Dev Containers: Reopen in Container" gives you an editor
  running inside the `api` container, with Python and lint set up.
- **Dependency updates:** Dependabot opens weekly PRs. Merge them if CI is green.
  It won't propose Python, Node, or Postgres version jumps, because those are
  upgraded on purpose, in one PR that changes every place the version is set.

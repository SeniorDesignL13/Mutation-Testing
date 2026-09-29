# Contributing

Conventions for working in this repo. They exist so the codebase reads like
one person wrote it. If something here is wrong or missing, fix it in a PR.

Setup instructions are in [DEVELOPER.md](DEVELOPER.md).

---

## Git workflow

### Branches

`main` is always green and deployable. Never commit to it directly; all work
goes through a pull request.

Branch names: `<type>/<short-kebab-description>`, optionally with an issue number.

```
feat/entity-swap-operator
fix/health-check-timeout
docs/contributing-guide
chore/bump-fastapi
refactor/12-judge-adapter-interface
```

Types match the commit types below.

### Commits

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <imperative summary, lowercase, no period>

<optional body: what and why, wrapped at ~72 chars>
```

| Type | Use for |
|---|---|
| `feat` | New user-facing behavior |
| `fix` | Bug fixes |
| `docs` | Documentation only |
| `test` | Adding or fixing tests only |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf` | Performance improvement |
| `build` | Dependencies, Docker, packaging |
| `ci` | GitHub Actions |
| `chore` | Anything else that doesn't touch `src/` behavior |

Scopes are the top-level package area: `operators`, `judges`, `stats`,
`service`, `cli`, `fe`, `db`.

```
feat(operators): add entity swap degrading operator
fix(service): return 404 instead of 500 for unknown run id
build: add pandas to stats group
```

Keep commits focused. A PR can have several commits, but each should make
sense on its own.

### Pull requests

1. Branch from an up-to-date `main`.
2. Keep PRs small, ideally under ~400 lines of non-generated diff. Split big
   features into stacked PRs.
3. Fill in the PR template (what, why, how to test).
4. CI must pass: lint, format, tests, frontend build, Docker boot.
5. At least **one approving review** before merging.
6. **Squash and merge.** The PR title becomes the commit on `main`, so it must
   follow the commit format above.
7. Delete the branch after merging.

Reviewers: review within one working day. Distinguish blocking comments from
suggestions (prefix non-blocking ones with `nit:`).

---

## Project layout

Put code where it belongs. If you're not sure, ask before creating a new top-level module.

```
src/mtlj/
  cli/                  Typer commands. Thin: parse args, call into core modules.
  service/              FastAPI app
    routers/            One module per resource (runs.py, judges.py, ...)
    schemas/            Pydantic request/response models
    models/             SQLAlchemy ORM models
    config.py           Settings (env vars)
    db.py               Engine, session, declarative Base
  operators/
    degrading/          Mutations expected to LOWER a judge's score
    preserving/         Mutations expected to NOT change a judge's score
  judges/               Adapters wrapping DeepEval, Ragas, Promptfoo, ...
  stats/                Statistical analysis of mutation results
tests/                  Mirrors src/mtlj/ (tests/operators/test_entity_swap.py, ...)
alembic/versions/       Generated migrations
fe/                     Nuxt frontend
docs/                   Project documentation
```

Rules:

- **No scripts at the repo root.** Prototypes go in the module they'll live in
  (e.g. `src/mtlj/operators/degrading/entity_swap.py`) or in a `scratch/`
  directory you don't commit.
- **Dependencies flow inward.** `cli` and `service` may import from
  `operators`, `judges`, and `stats`. Those core packages must **never** import
  from `cli` or `service`. That keeps the engine usable as a library.
- `routers` handle HTTP only. Business logic belongs in core modules or a
  service layer, not in route functions.

---

## Naming conventions

### Python

| Thing | Convention | Example |
|---|---|---|
| Modules / packages | `snake_case`, short, singular where natural | `entity_swap.py`, `judges/` |
| Classes | `PascalCase` | `EntitySwapOperator`, `JudgeResult` |
| Functions / methods / variables | `snake_case` | `apply_mutation()`, `swap_word` |
| Constants | `UPPER_SNAKE_CASE`, module level | `PLACE_POOL`, `DEFAULT_TIMEOUT_S` |
| Private helpers | leading underscore | `_pick_replacement()` |
| Type variables | `PascalCase`, short | `T`, `OperatorT` |
| Booleans | read as a question | `is_degrading`, `has_entities`, `should_retry` |
| Units | put the unit in the name | `timeout_s`, `latency_ms`, `max_tokens` |

Domain-specific:

- Mutation operators are **classes** named `<What>Operator` in a module named
  after the mutation: `operators/degrading/entity_swap.py` → `EntitySwapOperator`.
- Judge adapters: `<Framework>Judge` in `judges/<framework>.py`:
  `judges/deepeval.py` → `DeepEvalJudge`.
- Pydantic API schemas: `<Resource><Purpose>`: `RunCreate`, `RunRead`, `RunUpdate`.
- ORM models: singular `PascalCase` class, plural `snake_case` table:
  `class MutationRun` → `__tablename__ = "mutation_runs"`.

### Database

- Tables: plural `snake_case` (`mutation_runs`, `judge_scores`).
- Columns: `snake_case`. Foreign keys: `<singular_table>_id` (`mutation_run_id`).
- Timestamps: `created_at`, `updated_at`, always timezone-aware (`TIMESTAMPTZ`).
- Booleans: `is_` / `has_` prefix.
- Migration messages: short, imperative, lowercase (`add mutation runs table`).

### HTTP API

- Paths: plural, lowercase, kebab-case nouns: `/mutation-runs`, `/mutation-runs/{run_id}`.
- No verbs in paths; the HTTP method is the verb.
- JSON fields: `snake_case` (matches Python, no aliasing needed).
- Version the API under `/api/v1` once there's a real client depending on it.

### Frontend (Vue / Nuxt / TypeScript)

| Thing | Convention | Example |
|---|---|---|
| Components | `PascalCase.vue`, multi-word | `RunSummaryCard.vue` |
| Pages / routes | `kebab-case.vue` (Nuxt file routing) | `pages/mutation-runs/[id].vue` |
| Composables | `useCamelCase.ts` | `useMutationRuns.ts` |
| Variables / functions | `camelCase` | `fetchRuns()` |
| Types / interfaces | `PascalCase`, no `I` prefix | `MutationRun` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_PAGE_SIZE` |

### Files and misc

- Environment variables: `UPPER_SNAKE_CASE`, prefixed by area where
  ambiguous (`POSTGRES_*`, `NUXT_PUBLIC_*`).
- Docs: `kebab-case.md`, except conventional names (`README.md`, `CONTRIBUTING.md`).
- Test files: `test_<module>.py`, test functions `test_<behavior>`:
  `test_entity_swap_replaces_first_gpe`.

---

## Code conventions

### Python

**Formatting and linting are enforced, not debated.** `ruff format` and
`ruff check` run in CI with the config in `pyproject.toml` (line length 100).
Configure your editor to format on save; the Dev Container does this for you.

**Type hints everywhere.** Every function signature (arguments and return type)
in `src/` is annotated. Use modern syntax: `list[str]`, `dict[str, int]`,
`X | None` (not `List`, `Optional`).

**Docstrings** on every public module, class, and function. Say *why* and
*what*, not a line-by-line *how*. Google style for args when they're not obvious:

```python
def apply(self, text: str, *, seed: int | None = None) -> Mutation:
    """Swap the first geopolitical entity in ``text`` for a different one.

    Args:
        text: Input passage to mutate.
        seed: Makes replacement choice deterministic for reproducible runs.

    Returns:
        The mutation, or a no-op mutation if ``text`` contains no entity.
    """
```

**Comments** explain intent or non-obvious decisions. Don't narrate the code.

**Data shapes are Pydantic models**, not bare `dict`s, anywhere data crosses a
boundary (API, DB, between packages, results written to disk).

**No side effects at import time.** Don't load models, open connections, or
print at module level. Load expensive resources lazily (e.g. a cached
`get_nlp()` function) so importing a module is cheap and testable.

**Determinism.** Anything random (operator choices, sampling) takes a `seed` or
a `random.Random` instance. Mutation results must be reproducible.

**Async.** The service and judge calls are async. Don't call blocking I/O
(`requests`, `time.sleep`, sync DB drivers) inside `async def`. Use `httpx.AsyncClient`,
`asyncio.sleep`, and the async SQLAlchemy session. CPU-heavy work (spaCy, stats)
is fine to call synchronously from core modules.

**Errors.** Raise specific exceptions (define them in the relevant package's
`errors.py` when needed). Never use bare `except:`. Don't swallow exceptions
silently: handle, re-raise, or log.

**Logging, not print.** Use `logger = logging.getLogger(__name__)` in library
code. `print` / `typer.echo` belong only in the CLI.

**Configuration** comes from `mtlj.service.config.Settings` (env vars), never
hardcoded URLs, keys, or credentials.

### Frontend

- `<script setup lang="ts">` single-file components, Composition API only.
- TypeScript everywhere. Avoid `any`.
- API calls go through composables (`useMutationRuns()`), not directly in components.
- Read the API base URL from `useRuntimeConfig().public.apiBase`.

### Database changes

- Every schema change is an Alembic migration. Never modify the DB by hand.
- Generate with `alembic revision --autogenerate -m "..."`, then **read the
  generated file**. Autogenerate misses things (renames, some constraints).
- Migrations must have a working `downgrade()`.
- Never edit a migration that has been merged to `main`; add a new one.

### Dependencies

- Add with `uv add --group <group>` or `npm install`. Never hand-edit lockfiles.
- Commit the manifest and lockfile together.
- Justify new dependencies in the PR description. Prefer the standard library
  and what we already have.
- See [DEVELOPER.md](DEVELOPER.md#8-managing-dependencies).

---

## Testing

- Framework: `pytest` (async tests work without decorators, `asyncio_mode = "auto"`).
- `tests/` mirrors `src/mtlj/`: code in `src/mtlj/operators/degrading/entity_swap.py`
  is tested in `tests/operators/degrading/test_entity_swap.py`.
- Every bug fix comes with a test that fails without the fix.
- Every new operator has tests for: a normal mutation, a no-op input (nothing
  to mutate), and determinism with a fixed seed.
- Unit tests must not call real LLMs or external APIs. Mock the judge or HTTP
  layer. Tests that need a real model or network get `@pytest.mark.integration`
  and are opt-in.
- Keep tests fast. The whole unit suite should run in seconds.

```bash
docker compose exec api pytest                     # everything
docker compose exec api pytest tests/operators -k swap   # a subset
```

---

## Before you open a PR

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `pytest` passes
- [ ] New code has tests and type hints
- [ ] Manifest + lockfile committed together if dependencies changed
- [ ] Migration included if models changed
- [ ] Docs updated if setup, behavior, or conventions changed

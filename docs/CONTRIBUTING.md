# Rules

Short on purpose. If a rule is wrong or missing, change it in a PR.

## Branches

Never commit to `main`. Everything goes through a pull request (PR).

Name branches `type/short-description`:

| Type | For |
|---|---|
| `feat` | New features |
| `fix` | Bug fixes |
| `docs` | Documentation |
| `test` | Tests only |
| `refactor` | Restructuring without changing behavior |
| `perf` | Speed-ups |
| `build` | Dependencies, Docker |
| `ci` | GitHub Actions |
| `chore` | Anything else |

Example: `feat/entity-swap-operator`, `fix/health-timeout`.

## Pull requests

- **Title** = `type(scope): summary`, lowercase, e.g. `feat(operators): add entity swap`.
  A bot checks this. Scope is optional: `operators`, `judges`, `stats`, `service`, `cli`, `fe`, `db`.
- **Keep PRs small**, ideally under ~400 changed lines. Big features → several PRs.
- **To merge**, you need green CI and **1 approval** from someone else. Then click
  **Squash and merge**. The branch deletes itself.
- **Reviewing:** review within a day. Prefix optional suggestions with `nit:`.
- The repo owner can merge without a review. Everyone else can't.

## Naming

| Thing | Style | Example |
|---|---|---|
| Python files, functions, variables | `snake_case` | `entity_swap.py`, `apply_mutation()` |
| Python classes | `PascalCase` | `EntitySwapOperator` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRIES` |
| Operators / judges | `<Name>Operator` / `<Framework>Judge` | `EntitySwapOperator`, `RagasJudge` |
| API schemas | `<Thing><Action>` | `RunCreate`, `RunRead` |
| Database tables | plural `snake_case` | `mutation_runs` |
| Database model classes | singular `PascalCase` | `MutationRun` |
| API paths | plural, kebab-case | `/mutation-runs/{run_id}` |
| Vue components | `PascalCase.vue`, 2+ words | `RunSummaryCard.vue` |
| Vue pages | `kebab-case.vue` | `pages/mutation-runs.vue` |
| Composables | `useThing.ts` | `useMutationRuns.ts` |
| Tests | `test_<what_it_does>` | `test_swap_replaces_first_place` |

Put units in names: `timeout_s`, `latency_ms`.

## Code

The linters (`ruff`, `eslint`) handle formatting. The check commands fix it for you.
Beyond that:

1. **Type hints on every Python function.** Use `list[str]` and `X | None`, not `List` and `Optional`.
2. **A docstring on every public function and class.** Explain what it does and why, not how.
3. **Nothing runs at import time.** Load models lazily, e.g. with an `@lru_cache` getter function.
4. **Engine code** (`operators/`, `judges/`, `stats/`) **never imports** from `service/` or `cli/`.
5. **API routes stay thin.** They call engine functions; the logic lives in the engine.
6. **Randomness takes a `seed`** so results can be reproduced.
7. **No secrets in code.** Settings come from `.env` through `src/mtlj/service/config.py`.
8. **Use `logging`, not `print`**, except in CLI output.
9. **Database changes are migrations.** Don't edit a migration after it's merged; add a new one.

## Tests

- Test files mirror the source: `src/mtlj/stats/power.py` → `tests/stats/test_power.py`.
- New code comes with tests. Bug fixes come with a test that would have caught the bug.
- Tests never call real LLMs. Mock them, or mark real-LLM tests `@pytest.mark.integration`.

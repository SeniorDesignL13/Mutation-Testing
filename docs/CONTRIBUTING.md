# Rules for contributing

Short on purpose. If a rule is wrong or missing, change it in a PR.

## The basics

- **Never commit straight to `main`.** Every change goes through a pull request (PR).
- **A PR needs** green CI ✅ and **1 approval** from a teammate. Then click **Squash and merge**.
- **Keep PRs small**, ideally under ~400 changed lines. Split big features into several PRs.
- **Review teammates' PRs within a day.** Start optional suggestions with `nit:`.

## Jira and GitHub

Every change starts as a Jira task. Jira and GitHub are connected, so the task
moves across the board by itself as you work:

| You do this | Jira does this |
|---|---|
| Move the task from **To Do** to **In Progress** | Creates a branch for it on GitHub |
| Open a PR from that branch | Moves the task to **Under Review** |
| Merge the PR | Moves the task to **Done** |

So don't drag tasks to Under Review or Done yourself, and don't make branches
by hand. The branch name links everything back to the task, so don't rename it.
You'll find it on the Jira task, under **Development**.

## Git step by step

```bash
# 1. In Jira, move your task to In Progress. That creates your branch.

# 2. Get the latest code and switch to your branch
git switch main
git pull
git switch <branch-name-from-jira>

# 3. ...write code, then check it (see DEVELOPER.md)...

# 4. Save your work in a commit (do this as often as you like)
git add .
git commit -m "add entity swap operator"

# 5. Upload it
git push -u origin HEAD
```

Then open GitHub. It shows a **Compare & pull request** button. Click it, fill
in the title and description, and create the PR. The Jira task moves to
Under Review.

To change a PR, commit and `git push` again. The PR updates by itself.

Once it's approved and green, click **Squash and merge**. The Jira task moves to Done.

## Code style

Formatting is automatic. `scripts/check.py` and `npm run check` fix it for you.
Beyond that:

1. **Type hints on every Python function.** Write `list[str]` and `X | None`, not `List` / `Optional`.
2. **A docstring on every public function and class** saying what it does and why.
3. **Nothing heavy at import time.** Load models (like spaCy) inside a function, cached with `@lru_cache`.
4. **Engine code** (`operators/`, `judges/`, `stats/`) **never imports** from `api/` or `cli/`.
5. **API routes stay thin.** They call engine functions, and the logic lives in the engine.
6. **Randomness takes a `seed`**, so results can be reproduced.
7. **No secrets in code.** Keys go in `.env`.
8. **Use `logging`, not `print`**, except for CLI output.
9. **Database changes are migrations.** Never edit a merged migration. Add a new one.

## Naming

| Thing | Style | Example |
|---|---|---|
| Python files, functions, variables | `snake_case` | `entity_swap.py`, `apply_mutation()` |
| Python classes | `PascalCase` | `EntitySwapOperator` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRIES` |
| Operators / judges | `<Name>Operator` / `<Framework>Judge` | `EntitySwapOperator`, `RagasJudge` |
| Database tables | plural `snake_case` | `mutation_runs` |
| API paths | plural, kebab-case | `/mutation-runs/{run_id}` |
| Vue components | `PascalCase.vue`, 2+ words | `RunSummaryCard.vue` |
| Vue pages | `kebab-case.vue` | `pages/mutation-runs.vue` |
| Tests | `test_<what_it_checks>` | `test_swap_replaces_first_place` |

Put units in names: `timeout_s`, `latency_ms`.

## Tests

- Tests mirror the code: `src/mtlj/stats/power.py` → `tests/stats/test_power.py`.
- New code comes with tests. A bug fix comes with a test that would have caught the bug.
- Tests never call real LLMs. Fake the response, or mark the test
  `@pytest.mark.integration` (those only run with `pytest -m integration`).

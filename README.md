# mtlj — Mutation Testing for LLM Judges

Mutation testing for LLM-as-judge evaluators (DeepEval, Ragas, Promptfoo, ...):
mutate prompts/responses in ways that should or shouldn't change a judge's
verdict, then use statistics to check whether the judge actually notices.

## Setup

You only need **[Docker Desktop](https://docs.docker.com/get-started/get-docker/)** and **Git**.
Python, Node, and Postgres all run inside Docker.

```bash
git clone https://github.com/bduffaut/Mutation-Testing.git
cd Mutation-Testing
docker compose up --build
```

The first run takes a few minutes. Then open:

- **http://localhost:3000**: the app. You should see a table of green checks.
- **http://localhost:8000/docs**: the API, where you can try every endpoint.

Stop with `Ctrl+C`.

## Daily commands

| To... | Run |
|---|---|
| Start everything | `docker compose up --build` |
| Stop everything | `docker compose down` |
| Check Python before pushing (same as CI) | `docker compose exec api sh scripts/check.sh` |
| Check frontend before pushing (same as CI) | `docker compose exec fe npm run check` |
| Run Python tests only | `docker compose exec api pytest` |
| Use the CLI | `docker compose exec api mtlj --help` |

Saving a file reloads the app automatically. No restart needed.

## Docs

- **[How to develop](docs/DEVELOPER.md)**: the workflow, where code goes, how-tos
- **[Rules](docs/CONTRIBUTING.md)**: branches, PRs, reviews, naming and code style

## Tech stack

Python 3.12 (uv, FastAPI, SQLAlchemy, Alembic, Typer, Pydantic, spaCy, NLTK,
NumPy/SciPy/statsmodels, DeepEval, Ragas) · Postgres 16 · Nuxt 4 + PrimeVue 4 ·
Docker Compose · GitHub Actions

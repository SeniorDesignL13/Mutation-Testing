# mtlj — Mutation Testing for LLM Judges

LLM judges (DeepEval, Ragas, Promptfoo, ...) score AI answers. We test the
judges: we change ("mutate") answers in ways that *should* or *shouldn't*
change the score, then use statistics to check whether the judge notices.

## Get started (once)

1. Install **[Docker Desktop](https://docs.docker.com/get-started/get-docker/)** and **[Git](https://git-scm.com/downloads)**.
2. Open Docker Desktop and leave it running.
3. In a terminal:
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

## Next

- **[How to develop](docs/DEVELOPER.md)**: running Python on your machine, where code goes, how-tos, fixing problems
- **[Rules for contributing](docs/CONTRIBUTING.md)**: Git step by step, pull requests, code style

## Tech stack

Python 3.12 (uv, FastAPI, SQLAlchemy, Alembic, Typer, spaCy, NLTK,
NumPy/SciPy/statsmodels, DeepEval, Ragas) · Postgres 16 · Nuxt 4 + PrimeVue 4 ·
Docker Compose · GitHub Actions

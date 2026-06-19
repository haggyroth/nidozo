# Contributing

Thanks for your interest in contributing to Nidozo.

## Getting started

1. Fork the repo and clone your fork.
2. Follow the setup steps in the README to get the Showdown server running and the Python environment installed.
3. Create a branch: `git checkout -b <type>/<short-description>` (e.g. `feat/llm-player`, `fix/reconnect-logic`).

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/):
```
<type>(<scope>): <short description>
```
Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `style`. Keep the subject under 72 characters and use the imperative mood.

## Pull requests

- Open a PR against `main` for every change, even small ones.
- Describe **what**, **why**, and **how you tested it**.
- Wait for CI to pass before merging.
- Merge with a **merge commit** (`merge: <branch> (#PR)`), then delete the branch.

## Code style

- Python 3.12+, formatted and linted with `ruff` (run `uv run ruff format . && uv run ruff check src/` before committing).
- Types: all source modules are fully typed; CI runs `mypy --strict`. Run `uv run mypy --strict src/` before pushing.
- No secrets, `.env` files, or credentials committed.
- The battle engine layer (`src/nidozo/battle/`) must remain correct with respect to Showdown mechanics — bugs there corrupt every downstream result.

## Running the checks locally

```bash
uv run ruff check src/          # lint
uv run mypy --strict src/       # type check
uv run pytest                          # 1021 unit tests, no Showdown required
uv run pytest -m integration          # 1 integration test, requires Showdown on localhost:8000
cd frontend && npm run lint      # frontend ESLint
cd frontend && npm run build     # frontend build
```

CI runs six gates on every PR: ruff, mypy, pytest, the frontend lint+build, and a
Docker smoke test that brings the full stack up with `docker compose` and polls
`/healthz` to confirm it actually runs.

## Hidden information

If you touch prompt construction or battle-state serialization, be extremely careful about hidden information: a model must never see the opponent's unrevealed moves, items, or stats. Treat any leak as a correctness bug.

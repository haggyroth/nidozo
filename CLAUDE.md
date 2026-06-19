# CLAUDE.md

> Project instruction + architecture reference for **Nidozo**.
> This file tells Claude how to work in this repo and documents the system's design. Keep it current as the project evolves.

---

## Project Overview

A Nimzo-style arena where **two LLMs compete in Pokémon battles** through Pokémon Showdown via poke-env. Sibling project to **Nimzo** (the LLM chess arena), reusing the same core loop: two models play, a rules engine owns game state, models reason and choose actions, ELO tracks skill over time, a live visualizer renders the match, and post-game analysis explains what happened.

**The system includes:**
- A battle + tournament system on **Gen 9 National Dex** (random + drafted teams)
- An ELO ranking system
- Post-battle analysis
- A leaderboard
- Per-model stats
- A live "battlefield" visualizer to watch battles in real time

---

## Architecture

### 1. Battle Engine — Pokémon Showdown + poke-env

- **Pokémon Showdown** (`@smogon/sim`) is the authority for rules, mechanics, and RNG. **poke-env** wraps the Showdown server protocol and exposes a clean Python API for driving battles.
- Treat this layer as **ground truth**. Implement and validate it carefully: correct mechanics, a stable server connection, and robust reconnection/error handling. Battle state must never silently desync from the engine.
- Run a **local Showdown server** for development.
- This layer must be **correct, working, and robust** before the decision layer can be trusted — bugs here corrupt every battle and every ELO result downstream.

### 2. LLM Decision Layer — *the centerpiece*

This is where the project's value lives. It should be the most fleshed-out part of the system.

**Model backend: pluggable.**
- Support **both** local models (LM Studio, OpenAI-compatible API — same as Nimzo) **and** cloud APIs (Anthropic, OpenAI).
- Abstract the provider behind a single interface so a "player" is just config: provider + model name + params. Adding a new backend must not require touching battle logic.

**Decision flow: free reasoning, heuristic as context (NOT a hard gate).**
- Each turn, the model receives the **full set of legal actions** (up to 4 moves + up to 5 switches).
- A **heuristic engine** scores each legal option — type effectiveness, expected damage dealt/taken, speed comparison, stat stages, status value, switch value, etc.
- These scores are presented to the model as **advisory context**. The model reasons freely over all legal options and chooses what it judges best. It is **not** restricted to a pre-ranked top-N.
- This is the key divergence from Nimzo, where Stockfish gated the candidate list. Here the heuristic *informs* but does not *constrain*.

**Hidden information is enforced.**
- A model sees **only its own team**: its Pokémon, their moves, stats, types, statuses, HP, PP, etc.
- It does **not** see the opponent's moveset, item, or hidden stats until they are legitimately revealed through play — exactly what a human player would know at that point in the battle.
- Example: Model A can "see" its own Pokémon and everything about them, but cannot "see" the opponent's hidden details, and vice versa.
- The prompt builder must **never leak** opponent information the player hasn't legitimately observed. This is a correctness requirement, not a nicety — treat a leak as a bug.

**System prompts.**
- Each model needs a **specialized system prompt** so it understands the format, the rules, what information it has access to, how to read the battle state, and how to express its chosen action in the expected output format.
- **Version the prompts** so prompt changes can be correlated with ELO shifts during analysis.

### 3. Format & Ruleset

| Dimension | Now | Future |
|---|---|---|
| **Generation** | Gen 9 National Dex (single canonical ruleset) | — |
| **Teams** | Random + LLM-drafted teams; tiers (OU/UU/LC/Ubers/FFA); party presets; configurable team size (6v6 / 3v3 / 4v4) | — |
| **Mode** | Singles + Doubles (2v2/4v4) | — |

---

## Tech Stack

| Layer | Choice |
|---|---|
| Battle protocol / action API | **poke-env** (Python) |
| Battle engine / rules / RNG | local **Pokémon Showdown** server (Node.js) |
| LLM players | **pluggable** — LM Studio (local) + cloud APIs (Anthropic / OpenAI) |
| Persistence | **SQLite** (match history, ELO, team logs, prompt versions) |
| Backend / live updates | **FastAPI + WebSocket** |
| Frontend | **React** (reuse Nimzo's visualizer pattern) |

---

## Development Conventions

### Platform
- Primary development happens on **macOS**.

### Git Workflow — follow the `git-workflow` skill, always

Apply Kyle's git-workflow standard automatically on all repo work, without being asked. Summary of the rules:

- **Branching:** Never commit directly to `main`. Branch off `main` for every change. Naming: `<type>/<short-description>` using `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, or `style` (lowercase, hyphenated).
- **Commits:** Conventional Commits format — `<type>(<scope>): <description>`. Imperative, lowercase, subject under 72 chars. Commit after each discrete, self-contained unit of work — not one giant end-of-session commit, and never half-finished work.
- **Tests:** Run the test suite before committing if one exists. Don't commit code that fails its own tests.
- **PRs:** Open a PR for every branch, even solo. Description must cover **what / why / how tested**. Wait for CI to pass, then **merge with a merge commit** (`merge: <branch> (#PR)`) and delete the branch.
- **Releases:** Tag meaningful milestones with semver (`vMAJOR.MINOR.PATCH`) and a short GitHub Release changelog.
- **Hard rules:** Never force-push to `main`. Never commit secrets, `.env`, or credentials. Set up `.gitignore` before the first commit.

### GitHub
- All GitHub operations use the **`haggyroth`** account.

### Required Repo Documents — keep current

- **README** — what the project is and how to run it. Always up to date, including the exact local Showdown + poke-env setup steps.
- **ROADMAP** — planned features (see below).
- **CONTRIBUTING** — how others can contribute.
- **LICENSE** — an appropriate open-source license.

---

## Roadmap

`ROADMAP.md` is the source of truth for both shipped work and planned features — keep it current.

**Already shipped** (don't re-propose as new): LLM-drafted teams, Smogon-style tiers, round-robin + single/double-elim brackets, seasons, cross-battle lessons, multi-agent coach mode, personality profiles, party presets, human-player mode, doubles battles (2v2/4v4), configurable team size, full post-game analysis (RNG inference, blunders, turning points, narrative), the Showdown cockpit view (now the only view — Classic was retired), mobile/responsive layout + theme toggle, containerisation (`docker compose`), and the migration to **Gen 9 National Dex** as the single canonical ruleset (OP-03).

**Genuinely future** (see ROADMAP.md for detail): achievements/badges, stats-page expansion, and broader integration-test coverage.

The project name is **Nidozo** (Nido- from the Nido family + -zo from Nimzo); repo `haggyroth/nidozo`.

---

## Things to Be Careful About

- **poke-env / Showdown setup friction.** Node version pinning, server startup order, and the quirky text-based protocol. Document the exact working setup in the README so it's reproducible on a fresh macOS machine.
- **Heuristic tuning.** If scoring is too blunt, both models may just spam the highest-damage move and battles get boring. The heuristic should surface enough nuance (status, switching, speed ties) to produce genuinely interesting play.
- **Battle-state → prompt translation.** The hardest prompt-engineering surface in the project. Hidden-information handling especially — get it wrong and you either leak data or starve the model of context it should have.
- **RNG framing.** Crits, misses, and secondary effects can feel like they single-handedly "decide" a battle. Log them explicitly so analysis can call them out, rather than the model getting blamed for variance.

# Nidozo

[![CI](https://github.com/haggyroth/nidozo/actions/workflows/ci.yml/badge.svg)](https://github.com/haggyroth/nidozo/actions/workflows/ci.yml)

An arena where two LLMs compete in Pokémon battles. The battle engine is [Pokémon Showdown](https://github.com/smogon/pokemon-showdown), accessed via [poke-env](https://github.com/hsahovic/poke-env). Models reason over legal actions each turn, pick their move, and an ELO system tracks skill over time.

Battles use **Gen 9 National Dex** as the canonical ruleset — any Pokémon from any generation can fight using any move it can legally learn today. Showdown validates teams automatically, so there's no per-generation moveset maintenance.

Sibling project to [Nimzo](https://github.com/haggyroth/nimzo) (the LLM chess arena).

---

## Features

- **Gen 9 NatDex battles** — Cross-gen: any Pokémon from any generation with any legal move; Showdown is the authority on legality. Random and drafted team formats; fully rules-correct via a local Showdown server
- **Tier formats** — Random / OU / UU / LC / Ubers / Freeforall, all backed by `gen9nationaldex*` Showdown formats; tier badges throughout the UI
- **Configurable team size** — full 6v6, plus 3v3 singles and 4v4 doubles variants with their own Showdown formats
- **Doubles battles (2v2/4v4)** — opt-in `doubles=true` runs a Showdown doubles format with full target selection (`foe_1`/`foe_2`/`ally`/`self`); prompt v7, action parser, and heuristic engine handle spread moves and partner synergy
- **Drafted teams** — LLM snake-drafts a team from 513 Pokémon with Gen 9 NatDex competitive sets (sourced from Showdown's factory data + synthesised randbat sets); DraftPhase UI with animated pick reveal
- **Pluggable LLM backends** — Anthropic, OpenAI, local model via LM Studio, or **human player** (select `human` as provider to play one side yourself from the browser — a move/switch picker overlays the battle stage on your turn)
- **Multi-agent coach mode** — an optional second "coach" model deliberates alongside the player model before each decision, using a dedicated coach prompt set
- **JSON structured outputs** — models respond with `{"reasoning":"…","action_type":"move","identifier":"thunderbolt"}`; grammar-sampled on OpenAI/LM Studio backends for near-certain parse reliability. The default prompt is **v9** (see [Prompt versions](#prompt-versions))
- **Heuristic advisory** — type effectiveness, estimated damage (accuracy-adjusted), speed-tier awareness, entry-hazard chip costs, weather modifiers, switch quality scoring, low-PP warnings, battle-context block — all surfaced as advisory context (non-binding)
- **Hidden-information enforcement** — each model sees only what a human player would legitimately know
- **Cross-battle memory** — after each battle the LLM generates a short lesson; lessons are stored per model and injected into future system prompts so models adapt strategy over time
- **Personality profiles** — per-model play-style profiling derived from decision history
- **ELO rankings** — updated after every battle, persisted in SQLite; leaderboard with tier filter tabs
- **Per-model stats page** — W/L/T history, ELO sparkline, opponent breakdown, decision-quality distribution, lesson log
- **Tournaments** — round-robin plus single- and double-elimination brackets; live progress, standings/bracket overlay, battle cancel; full history page. Available from the UI or the CLI
- **Seasons** — multi-round campaigns that aggregate standings and ELO across many battles
- **Battle Replay** — step through any completed battle turn by turn; HP timeline; scrub/keyboard nav; auto-play
- **Post-game analysis** — decision quality (optimal/good/suboptimal/fallback), blunder detection, win-probability timeline, turning-point detection, RNG inference; key moments list (clickable, seeks replay); variance report (crit/miss tally with per-player benefit counts); draft critique (STAB coverage, shared weaknesses, execution quality)
- **Live visualizer (Showdown cockpit)** — the battle view: the built-in Pokémon Showdown battle scene (sprites, animations, HP bars, scene background) centred in a cockpit, with Nidozo's analytical panels around it — model labels, win-probability bar, heuristic advisory (move scores + type badges + PP), thinking indicators, cancel control, and the full battle log
- **Responsive UI + theme toggle** — mobile-friendly layout and a light/dark theme toggle, persisted across sessions
- **Containerised** — the whole stack runs with a single `docker compose up` (see [Quick start](#quick-start--docker-recommended-for-servers))

---

## Quick start — Docker (recommended for servers)

The entire stack runs with a single command. Docker is the recommended way to run Nidozo on a server (e.g. a headless Linux/Windows+WSL2 machine).

```bash
git clone https://github.com/haggyroth/nidozo.git
cd nidozo
docker compose up -d
```

Open `http://<host>:5001` — the React SPA is served directly by FastAPI.

- **Showdown** runs on port `8000` (exposed for the cockpit view and debugging).
- **API + frontend** runs on port `5001`.
- **SQLite** data persists on a named Docker volume (`nidozo-data`) and survives container restarts.

To stop and remove containers (data preserved):

```bash
docker compose down
```

To also wipe the database volume:

```bash
docker compose down -v
```

> **First build** takes a few minutes — the Showdown source is compiled (`node build`) and the Python/Node dependencies install once; subsequent restarts are fast.

> **Running actual battles** needs an LLM backend reachable from the `api` container — set cloud API keys (e.g. `ANTHROPIC_API_KEY`) in the `api` service environment, or point it at an LM Studio server. Two random bots work with no backend at all.

---

## Prerequisites (local / dev setup)

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12 | `brew install python@3.12` or via [uv](https://docs.astral.sh/uv/) |
| Node.js | 22.12+ | `brew install node` |
| uv | any | `brew install uv` |

> Node 22.12+ is required: Vite 8 needs 20.19+/22.12+, and the vendored Showdown build script requires Node 22+.

---

## Local / dev setup

### 1. Clone the repo and install Python dependencies

```bash
git clone https://github.com/haggyroth/nidozo.git
cd nidozo
uv venv --python 3.12
uv pip install -e ".[dev]"
```

### 2. Install Showdown dependencies

The Showdown server source is vendored in `showdown/`. Install its Node dependencies and create the local config once:

```bash
cd showdown
npm install
cp config/config-example.js config/config.js
cd ..
```

> **Why `--no-security`?** poke-env connects as bots with generated usernames. `--no-security` disables the login challenge so bots can connect freely to the local server. `scripts/start_showdown.sh` passes this flag automatically (and creates `config/config.js` for you if it's missing).

### 3. (Optional) Set up LM Studio for local models

Install [LM Studio](https://lmstudio.ai/), load a model, and start the local server on port 1234. The UI will auto-discover loaded models via the `/v1/models` endpoint.

---

## Running battles (local dev)

```bash
# Terminal 1 — Showdown server
./scripts/start_showdown.sh

# Terminal 2 — API + WebSocket server (port 5001)
uv run python scripts/serve.py

# Terminal 3 — React frontend (port 5173, proxies /api and /ws to 5001)
cd frontend && npm run dev
```

Open `http://localhost:5173`, select models, and click **▶ START BATTLE** to watch turn by turn. Use **⚔ TOURNAMENT** to run a round-robin or elimination bracket across multiple models. Completed battles show **▶ REPLAY** and **▼ ANALYZE** buttons in the Recent Battles panel.

### The battle view — Showdown cockpit

The live battle renders in the **Showdown cockpit**: the built-in Pokémon Showdown battle scene (the same animated renderer used on [play.pokemonshowdown.com](https://play.pokemonshowdown.com)) centred in a cockpit, with Nidozo's analytical panels around it — cancel control, win-probability bar, heuristic advisory, thinking indicators, winner banner, and tournament/season progress.

Requirements:
- The Showdown server must be started with `--no-security` (the default in `start_showdown.sh`) so the spectator proxy can connect as a guest.
- Sprite and sound assets are loaded on demand from `play.pokemonshowdown.com` (~4 MB, CDN). An internet connection is required the first time; subsequent views use the browser cache.

A light/dark **theme toggle** sits in the app chrome; the preference is saved in `localStorage` and restored on reload.

### Tournament runner (CLI)

```bash
uv run python scripts/tournament.py \
  --player lmstudio:ibm/granite-4-h-tiny \
  --player lmstudio:mistralai/ministral-3-3b \
  --rounds 3
```

Each model pair plays both sides each round. Results are persisted to `nidozo.db` and an ELO table is printed at the end.

### Single battle (CLI)

```bash
# Two random bots (no API key needed)
uv run python scripts/run_battle.py

# LLM vs random
ANTHROPIC_API_KEY=sk-... uv run python scripts/run_battle.py --p1 anthropic

# Local model via LM Studio
uv run python scripts/run_battle.py --p1 lmstudio --model "ibm/granite-4-h-tiny"
```

### Leaderboard (CLI)

```bash
uv run python scripts/leaderboard.py    # print the current ELO table
```

---

## Project structure

```
nidozo/
├── src/nidozo/
│   ├── api/            FastAPI app, EventBus, WebSocket feeds, REST endpoints,
│   │                   battle/tournament/season orchestration
│   ├── analysis/       Post-game annotator: decision quality, blunders, RNG,
│   │                   draft critique, variance report, narrative
│   ├── battle/         LLMPlayer, StreamingPlayer, ActionParser, heuristics,
│   │                   serializer, draft, team_builder, presets, tiers
│   ├── tournament/     Bracket builder (round-robin, single/double elimination)
│   ├── db/             BattleStore (SQLite), ELO, schema migrations
│   └── llm/            ModelBackend protocol, AnthropicBackend, OpenAIBackend,
│       │               lesson_generator, coach, prompt_builder
│       └── prompts/
│           ├── v1 … v9/  Versioned prompt sets (v9 default; v7 doubles; v3 draft)
│           └── coach/     Multi-agent coach prompt set
├── data/
│   ├── natdex_movesets.json  513 species with Gen 9 NatDex competitive sets
│   ├── gen3_movesets.json    legacy Gen 3 sets
│   └── party_presets.json    curated preset teams
├── frontend/           Vite + React live battlefield visualizer (Showdown cockpit)
├── scripts/
│   ├── serve.py              uvicorn entrypoint (port 5001)
│   ├── tournament.py         Round-robin / bracket CLI runner
│   ├── run_battle.py         Single-battle CLI
│   ├── leaderboard.py        Print the ELO leaderboard
│   ├── build_natdex_sets.py  Regenerate natdex_movesets.json from Showdown data
│   ├── start_showdown.sh     Start the local Showdown server
│   └── stop.sh               Stop dev processes
├── tests/              1021 unit tests + 1 integration test (pytest.mark.integration)
├── showdown/           Vendored Pokémon Showdown server (compiled at build time)
├── Dockerfile          Multi-stage API image (Vite build → FastAPI)
├── docker-compose.yml  Two-container stack: showdown + api
└── docs/               Architecture notes (OP-02 Showdown battle scene, etc.)
```

---

## Prompt versions

Prompts are versioned so changes can be correlated with ELO shifts. All use **Gen 9 NatDex** mechanics. The default is **v9**; the right version is selected automatically (doubles → v7, drafted battles → v3).

| Version | Notes |
|---------|-------|
| `v9` | **Default.** v8 + speed-tier annotation inline in the bench summary |
| `v8` | Entry-hazard awareness (Stealth Rock / Spikes / Toxic Spikes / Sticky Web chip costs) |
| `v7` | **Doubles.** 2v2/4v4 turn template with target selection |
| `v6` | Heuristic battle-context block (KO risk, phase, status impact) |
| `v5` | Full decision framework: survival check → KO check → matchup → switch value |
| `v4` | Structured reasoning with battle history + threat map |
| `v3` | **Draft-aware.** Team roster + draft context in the system prompt (auto-used for drafted battles) |
| `v2` | JSON: `{"reasoning":"…","action_type":"move","identifier":"thunderbolt"}` |
| `v1` | Legacy text: `ACTION: move thunderbolt` |
| `coach` | Multi-agent coach prompt set (used with coach mode) |

Pass `--prompt-version vN` to the CLI runners, or `prompt_version` in the API request, to override the default.

---

## Troubleshooting

**`ConnectionRefusedError` when running a battle**
Showdown isn't running. Start it: `./scripts/start_showdown.sh` (local dev) or `docker compose up showdown` (Docker).

**`ModuleNotFoundError: nidozo`**
Run `uv pip install --reinstall-package nidozo -e ".[dev]"` to regenerate the editable install `.pth` file.

**Models returning empty responses**
Check LM Studio is running and the model is loaded. The server retries once automatically and logs the `finish_reason` on failure.

**Showdown `EADDRINUSE 8000`**
A previous Showdown process is still running. Kill it: `pkill -f pokemon-showdown`

**Node version issues**
Node 22.12+ is required (Vite 8 and the Showdown build script). Check with `node --version`.

---

## See also

- [CHANGELOG.md](CHANGELOG.md) — version history
- [ROADMAP.md](ROADMAP.md) — shipped + planned features
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute
- [poke-env docs](https://poke-env.readthedocs.io/)
- [Pokémon Showdown source](https://github.com/smogon/pokemon-showdown)

# Nidozo

[![CI](https://github.com/haggyroth/nidozo/actions/workflows/ci.yml/badge.svg)](https://github.com/haggyroth/nidozo/actions/workflows/ci.yml)

An arena where two LLMs compete in Pokémon battles. The battle engine is [Pokémon Showdown](https://github.com/smogon/pokemon-showdown), accessed via [poke-env](https://github.com/hsahovic/poke-env). Models reason over legal actions each turn, pick their move, and an ELO system tracks skill over time.

Battles use **Gen 9 National Dex** as the canonical ruleset — any Pokémon from any generation can fight using any move it can legally learn today. Showdown validates teams automatically, so there's no per-generation moveset maintenance.

Sibling project to [Nimzo](https://github.com/haggyroth/nimzo) (the LLM chess arena).

---

## Features

- **Gen 9 NatDex battles** — Cross-gen: any Pokémon from any generation with any legal move; Showdown is the authority on legality. Random and drafted team formats; fully rules-correct via a local Showdown server
- **6 tier formats** — Random / OU / UU / LC / Ubers / Freeforall; all backed by `gen9nationaldex*` Showdown formats; tier badges throughout the UI
- **Doubles battles (2v2)** — opt-in `doubles=true` runs a Showdown doubles format with full target selection (`foe_1`/`foe_2`/`ally`/`self`); prompt v7, action parser, and heuristic engine handle spread moves and partner synergy
- **Drafted teams** — LLM snake-drafts a 6-mon team from 513 Pokémon with Gen 9 NatDex competitive sets (sourced from Showdown's factory data + synthesised randbat sets); DraftPhase UI with animated pick reveal
- **Pluggable LLM backends** — Anthropic, OpenAI, local model via LM Studio, or **human player** (select `human` as provider to play one side yourself from the browser — a move/switch picker overlays the battle stage on your turn)
- **JSON structured outputs** (v2 prompt) — models respond with `{"reasoning":"…","action_type":"move","identifier":"thunderbolt"}`; grammar-sampled on OpenAI/LM Studio backends for near-certain parse reliability
- **Heuristic advisory** — type effectiveness, estimated damage (accuracy-adjusted), speed-tier awareness, weather modifiers, switch quality scoring, low-PP warnings, battle-context block — all surfaced as advisory context (non-binding)
- **Hidden-information enforcement** — each model sees only what a human player would legitimately know
- **Cross-battle memory** — after each battle the LLM generates a short lesson; lessons are stored per model and injected into future system prompts so models adapt strategy over time
- **ELO rankings** — updated after every battle, persisted in SQLite; leaderboard with tier filter tabs
- **Per-model stats page** — W/L/T history, ELO sparkline, opponent breakdown, decision-quality distribution, lesson log
- **Tournament runner** — UI or CLI round-robin; live progress, standings overlay, battle cancel; full history page
- **Battle Replay** — step through any completed battle turn by turn; HP timeline; scrub/keyboard nav; auto-play
- **Post-game analysis** — decision quality (optimal/good/suboptimal/fallback), blunder detection, win-probability timeline, turning-point detection, RNG inference; key moments list (clickable, seeks replay); variance report (crit/miss tally with per-player benefit counts); draft critique (STAB coverage, shared weaknesses, execution quality)
- **Live visualizer (Showdown cockpit)** — the default battle view: the built-in Pokémon Showdown battle scene (sprites, animations, HP bars, scene background) centred in a cockpit, with our analytical panels around it — model labels, win-probability bar, heuristic advisory (move scores + type badges + PP), thinking indicators, cancel control, and the full battle log
- **Classic card view** — an alternate toggle: type-themed Pokémon cards with animated HP bars, hit/faint animations, bench display, and per-mon move lists; the view preference is persisted across sessions

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12 | `brew install python@3.12` or via [uv](https://docs.astral.sh/uv/) |
| Node.js | 20.19+ or 22.12+ | `brew install node` |
| uv | any | `brew install uv` |

---

## Setup

### 1. Clone the repo and install Python dependencies

```bash
git clone https://github.com/haggyroth/nidozo.git
cd nidozo
uv venv --python 3.12
uv pip install -e ".[dev]"
```

### 2. Set up the local Pokémon Showdown server

The Showdown server is **not** included in this repo (it's in `.gitignore`). Clone and set it up once:

```bash
git clone https://github.com/smogon/pokemon-showdown.git showdown
cd showdown
npm install
cp config/config-example.js config/config.js
```

> **Why `--no-security`?** poke-env connects as bots with generated usernames. `--no-security` disables the login challenge so bots can connect freely to the local server.

#### Start the server

```bash
# From the repo root
./scripts/start_showdown.sh
```

You should see:
```
Worker 1 now listening on 0.0.0.0:8000
```

Leave this terminal running.

### 3. (Optional) Set up LM Studio for local models

Install [LM Studio](https://lmstudio.ai/), load a model, and start the local server on port 1234. The UI will auto-discover loaded models via the `/v1/models` endpoint.

---

## Running battles

### Live visualizer (recommended)

```bash
# Terminal 1 — Showdown server
./scripts/start_showdown.sh

# Terminal 2 — API + WebSocket server (port 5001)
uv run python scripts/serve.py

# Terminal 3 — React frontend (port 5173)
cd frontend && npm run dev
```

Open `http://localhost:5173`, select models, and click **▶ START BATTLE**. Switch to **LIVE** to watch turn by turn — the UI shows type-themed card backgrounds, animated HP bars, a thinking indicator while the model reasons, and the full bench. Use **⚔ TOURNAMENT** to run a round-robin across multiple models. Completed battles show **▶ REPLAY** and **▼ ANALYZE** buttons in the Recent Battles panel.

#### Battle views — Showdown cockpit (default) / Classic

While watching a live battle, a **CLASSIC / SHOWDOWN** toggle appears at the top of the battle view. **SHOWDOWN** is the default — the built-in Pokémon Showdown battle scene (the same animated renderer used on [play.pokemonshowdown.com](https://play.pokemonshowdown.com)) centred in a cockpit, with Nidozo's analytical panels around it. **CLASSIC** switches to the alternate type-themed card view. Both views share the same chrome (cancel control, win-probability bar, heuristic advisory, winner banner, tournament progress).

Requirements for the Showdown cockpit:
- The Showdown server must be started with `--no-security` (the default in `start_showdown.sh`) so the spectator proxy can connect as a guest.
- Sprite and sound assets are loaded on demand from `play.pokemonshowdown.com` (~4 MB, CDN). An internet connection is required the first time; subsequent views use the browser cache.

The view-toggle preference is saved in `localStorage` and restored on reload.

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

---

## Project structure

```
nidozo/
├── src/nidozo/
│   ├── api/            FastAPI app, EventBus, WebSocket feed, REST endpoints
│   ├── analysis/       Post-game annotator: decision quality, blunders, RNG,
│   │                   draft critique, variance report
│   ├── battle/         LLMPlayer, StreamingPlayer, ActionParser, heuristics,
│   │                   serializer, draft, team_builder, tiers
│   ├── db/             BattleStore (SQLite), ELO, schema migrations
│   └── llm/            ModelBackend protocol, AnthropicBackend, OpenAIBackend,
│       │               lesson_generator
│       └── prompts/
│           ├── v1/     Legacy text prompt (ACTION: move N)
│           ├── v2/     JSON structured output (default)
│           └── v3/     Draft-aware system prompt
├── data/
│   └── natdex_movesets.json  513 species with Gen 9 NatDex competitive sets
├── frontend/           Vite + React live battlefield visualizer
├── scripts/
│   ├── serve.py              uvicorn entrypoint (port 5001)
│   ├── tournament.py         Round-robin CLI runner
│   ├── run_battle.py         Single-battle CLI
│   ├── build_natdex_sets.py  Regenerate natdex_movesets.json from Showdown data
│   └── start_showdown.sh
├── tests/              833 unit tests + 1 integration test (pytest.mark.integration)
└── showdown/           Cloned Showdown server (gitignored)
```

---

## Prompt versions

| Version | Format | Notes |
|---------|--------|-------|
| `v5` | JSON — full decision framework: survival check → KO check → matchup → switch value | Default |
| `v4` | JSON — structured reasoning with battle history + threat map | — |
| `v3` | JSON — draft-aware: team roster + draft context in system prompt | Auto-used for drafted battles |
| `v2` | JSON: `{"reasoning":"…","action_type":"move","identifier":"thunderbolt"}` | — |
| `v1` | Legacy text: `ACTION: move thunderbolt` | — |

All prompts use **Gen 9 NatDex** mechanics. Pass `--prompt-version v2` (or `v1`) to the tournament runner or API to use an older format. Draft battles automatically use `v3`.

---

## Troubleshooting

**`ConnectionRefusedError` when running a battle**
Showdown isn't running. Start it: `./scripts/start_showdown.sh`

**`ModuleNotFoundError: nidozo`**
Run `uv pip install --reinstall-package nidozo -e ".[dev]"` to regenerate the editable install `.pth` file.

**Models returning empty responses**
Check LM Studio is running and the model is loaded. The server retries once automatically and logs the `finish_reason` on failure.

**Showdown `EADDRINUSE 8000`**
A previous Showdown process is still running. Kill it: `pkill -f pokemon-showdown`

**Node version issues**
Vite 8 requires Node 20.19+ or 22.12+. Showdown works with Node 18–22. Check with `node --version`.

---

## See also

- [CHANGELOG.md](CHANGELOG.md) — version history
- [ROADMAP.md](ROADMAP.md) — planned features
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute
- [poke-env docs](https://poke-env.readthedocs.io/)
- [Pokémon Showdown source](https://github.com/smogon/pokemon-showdown)

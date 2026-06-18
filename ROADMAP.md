# Roadmap

## Completed

### v0.1 — Foundation
- Repo scaffold, Python project, venv, pyproject.toml
- Local Pokémon Showdown server wired up with poke-env
- Two RandomBots complete a Gen 3 random singles battle end to end

### v0.2 — LLM Decision Layer
- Pluggable model backend: Anthropic + OpenAI cloud APIs
- Local model support via LM Studio (OpenAI-compatible API)
- Battle state serializer with hidden-information enforcement
- System prompt v1: battle state format, legal actions, `ACTION: move N` output schema
- Versioned prompts for correlating changes with ELO shifts
- `LLMPlayer` full loop: state → prompt → backend → action parser → `BattleOrder`

### v0.3 — Heuristic Engine
- Type effectiveness, estimated damage %, stat stages, priority, status annotation
- Switch matchup scoring
- Heuristic scores surfaced to LLM as advisory context (not a hard filter)

### v0.4 — ELO & Persistence
- SQLite schema: battles, turns, elo_ratings, elo_history, models, prompt versions
- ELO calculation (K=32) updated after each battle
- Per-model stats, turn logging, leaderboard CLI

### v0.5 — API & Visualizer
- FastAPI backend with WebSocket live-battle feed (`/ws/battles`)
- REST endpoints: leaderboard, battles, turns, start-battle
- React + Vite frontend: retro CRT dark-theme battlefield visualizer
- Live Pokémon cards with animated HP bars, type badges, status, stat boosts
- Battle log, heuristic advisory drawer, winner banner

### v0.6 — Post-game Analysis
- Per-turn decision quality annotation: optimal / good / suboptimal / fallback
- Analysis compares chosen action against heuristic ranking
- `/api/battles/{id}/analysis` endpoint
- Analysis panel in the UI with quality bars and turn-by-turn breakdown

### v0.7 — Tournament Runner & Real Battles
- Round-robin tournament CLI (`scripts/tournament.py`)
- Per-player model fields in API and UI
- First live LLM battles: Ministral-3-3b vs Granite-4-h-tiny (12-0 result)
- Parser hardened for name-based actions and markdown-wrapped output
- Leaderboard SQL bug fixed (duplicate rows from UNION ALL)

### v0.8 — JSON Outputs, Engine Reliability, UI Polish
- **v2 prompt**: JSON structured output (`{"reasoning","action_type","identifier"}`)
- LM Studio `response_format=json_schema` for grammar-sampled valid JSON
- `reasoning_content` fallback for Qwen 3 thinking models
- Fuzzy species name matching (`difflib`, cutoff=0.82) for switch typos
- Retry on empty LLM response; logs `finish_reason` for diagnosis
- Thinking events: amber pulsing indicator while model reasons
- Gen 3 sprites via Showdown CDN with pixelated rendering
- Bench row: reserve Pokémon with mini-sprites + HP bars
- Model selector queries LM Studio's `/v1/models` for live chips
- WebSocket keepalive pings (25s) — eliminates reconnection churn
- CI pipeline: ruff lint + pytest + frontend build in parallel
- 127 tests including full API endpoint coverage
- Leaderboard grouped by model (aggregates across prompt versions; `v1`/`v2` pill tags)
- First meaningful ELO results: gemma-4-e2b 7-3 vs ministral-3-3b

### v0.9 — Live Pipeline, Replay, Visual Polish & Richer Analysis
- **Live pipeline**: all battles (UI or CLI) routed through the shared EventBus; tournament progress visible in real time
- **Tournament UI**: configure N players and rounds in the browser; cancel individual battles mid-run; live progress bar + final standings overlay
- **Battle Replay**: step through any completed battle turn by turn; HP timeline SVG; scrub slider; keyboard nav (← → Space Esc); auto-play
- **Type-themed card backgrounds**: 18-type colour map; single-type corner wash; dual-type diagonal gradient split
- **Battle animations**: hit flash, sprite shake, heal pulse, faint fade — driven by HP delta tracking with `useRef`
- **Win probability timeline**: team HP ratio per turn; sparkline in analysis drawer and replay HP chart
- **Turning-point detection**: turn with the largest single-turn win-prob swing, highlighted in both replay and analysis
- **Blunder flagging**: suboptimal moves where score gap ≥ 40% of best option flagged with `⚠`; blunders panel in analysis
- **RNG inference**: possible crit / possible miss inferred from actual vs expected HP drop; badges in replay and analysis log
- 154 tests; parser fix for `"switch 1"` identifier form

### v0.10 — Hardening, Quality & Technical Debt
- **Wave R1 — Critical correctness fixes**: test suite stabilised; port 5001 enforced consistently across serve.py, vite config, and README
- **Wave R2 — Frontend lint + CI gate**: all 16 ESLint v10 / react-hooks v7 errors resolved; `npm run lint` added to CI; pytest coverage gate at 65%; `uv.lock` committed for reproducible builds
- **Wave R3 — Robustness**: `failed` battle status wired end-to-end; Pydantic `Field(ge/le)` bounds on all API inputs (422 on bad requests); 6 DB indexes for hot read paths; `finish_battle` + ELO update made fully atomic; CORS restricted to known origins; EventBus queues bounded (256) with drop-oldest overflow; events list capped at 500 in frontend
- **Wave R4 — Test coverage**: 35 new tests across `LLMPlayer`, `StreamingLLMPlayer`, `StreamingRandomBot`, `AnthropicBackend`, `OpenAIBackend`, `BattleStore`, and schema migrations; overall coverage 85%; schema migration bug found and fixed (`migrate()` would crash on v1 databases due to index creation before column existed)
- **Technical debt cleared**: heuristic bogus tokens removed; `AnthropicBackend` multi-block crash fixed; opponent `ability` hidden-info guard; inline SQL moved to `BattleStore`; `serve.py --reload` fixed
- 203 tests

### v0.11 — Memory, Intelligence, Drafted Teams & Deep Analysis
- **Cross-battle lessons**: after each battle the LLM generates a short lesson (what worked, what to avoid); stored per model in SQLite; injected into future system prompts so models evolve strategy across battles
- **Per-model stats page**: full W/L/T history, ELO sparkline, opponent breakdown, decision-quality distribution, lesson log — all in-browser
- **Richer post-game analysis**: per-turn key moments list (blunders, RNG events, turning point); enhanced lesson generation grounded in specific blunders; `AnalysisSummary` panel in Battle Replay with clickable moments that seek to the turn
- **Tournament mode**: configure N models and rounds in the browser; round-robin; live progress + standings overlay; cancel individual battles mid-run; full tournament history page
- **Drafted teams + Smogon meta tiers**: LLM snake-drafts a 6-mon team from a curated pool; 8 tier formats (Random / OU / UU / NU / LC / Ubers / Freeforall); DraftPhase UI with animated pick reveal; `teams` table in DB; drafted team rosters shown on post-battle result card
- **Tier context in UI**: tier badges throughout battlefield, leaderboard, tournament scoreboard; tier filter tabs on leaderboard
- **Heuristic overhaul**: speed-tier awareness (Gen 3 paralysis ×0.25), weather damage modifier, accuracy-adjusted damage estimates, low-PP warnings, battle context block (matchup quality, remaining counts, status impact), switch quality scoring with matchup labels
- **Draft critique**: team composition analysis — STAB offensive type spread, shared defensive weaknesses (Gen 3 type chart), coverage gaps, execution quality (blunders + decision_quality_pct)
- **Variance report**: structured tally of all inferred RNG events (crits + misses) with per-player benefit counts and plain-English verdict; new `VarianceReport` and `DraftCritiqueSection` panels in Battle Replay
- **Gen 3 pool expansion**: 93 → 153 species with Smogon ADV competitive sets covering all missing starters (Blaziken, Charizard, Venusaur, Blastoise), legends (Raikou, Entei, Regirock, Registeel), and popular UU/NU picks; all Gen 3 legal (no Gen 4+ moves)
- 358 tests; mypy strict enforced across all source files

### v0.12 — Coach Mode, Brackets & Richer Lessons
- **Multi-agent coach mode**: optional pre-turn coach model queries the same battle state with no output constraints; coach advice appended to player prompt; `agent: "coach"|"player"` thinking events in the UI; `coach_advice` column in turns table (schema v8)
- **Tournament brackets**: single-elimination and double-elimination bracket modes with seeded byes for non-power-of-2 fields; lazy battle creation; `bracket_update` WebSocket event; `BracketView` UI with bracket progression visualizer; `tournament_format` and `bracket_state` columns (schema v7)
- **Richer lesson prompting**: draft critique, variance report, and win-probability data now fully surfaced in the lesson prompt; lesson grounded in specific blunders and turning-point turns rather than generic reflection
- **Tier 1 test coverage**: 564 tests at 88% coverage; targeted unit tests for analyzer, heuristics, bracket, store, schema, serializer, action parser, and API routes

### v0.13 — Prompt v4, Head-to-Head Matrix & Stability
- **Prompt v4**: battle event history (last 3 turns of HP deltas), opponent moveset revelation count, pre-computed threat map per threatened Pokémon
- **Head-to-head matchup matrix**: win/loss/tie counts for every model pair; tier-filterable
- **Double-elimination bye fix**: fixed-point resolver handles chained bye slots
- **Tournament failure handling**: unhandled exceptions abort bracket, mark failed, emit `tournament_failed` event
- **ELO idempotency**: `finish_battle` fully idempotent with `AND finished_at IS NULL` guard; `UNIQUE(battle_id, model_id)` index enforced at DB level (schema v9)
- **`app.py` split**: refactored into `lifespan.py` and `middleware.py`; tier-2 async test coverage (53 tests) for the API layer

### v0.14 — Seasons
- **Named competition seasons**: fixed participant list, round-robin scheduling across N rounds, per-season isolated ELO ratings
- **Season UI**: live standings page with progress bar, per-season battle history, start/cancel from the browser
- **Schema v10**: `seasons` and `season_battles` tables

### v0.15 — Prompt v5
- **Decision framework & KO-risk signal**: actual computed stats (Spe/Atk/SpA/Def/SpD) for own active Pokémon; last move used for both sides; KO-risk note injected when either side can OHKO; explicit decision-ordering framework in system prompt
- Default prompt version bumped to `v5`

### v0.16 — Richer Post-Game Analysis
- **LLM battle narrative**: `narrator.py` generates a 4–6 sentence battle story; stored in `battles.narrative` (schema v11); "Battle Story" section in Battle Replay
- **Switch quality classification**: `good_switch` / `bad_switch` / `neutral_switch` / `forced_switch` labels using heuristic switch scores; per-player switch breakdown in analysis summary
- **Enriched turning-point description**: includes move names and win-probability swing

### v0.17 — UI Intelligence & Parser Hardening
- **Model name labels**: provider + model name above each Pokémon card (P1 cyan, P2 amber)
- **Own-mon move display**: active card shows all 4 moves with type-color dot, BP, and PP (red when low)
- **Model dropdowns**: `<select>` with LM Studio live models + static Anthropic/OpenAI presets; Claude Sonnet 4, Haiku 3.5, Opus 4, o4-mini added
- **`<think>` block stripping**: parser strips `<think>...</think>` from reasoning-model responses before JSON parse

### v0.18 — Battle Scene Responsiveness
- **Turn-start state_update**: emitted at the top of `choose_move` (request parsed, stats fresh) so the battlefield refreshes immediately before LLM think-time; eliminates stale-HP window between turns

### v0.19 — Pokémon Mouseover Tooltips
- **Tooltip panel**: hovering any active or bench Pokémon shows base stat bars (color-coded), Gen 3 type matchup table grouped by multiplier, and revealed ability/item
- Base stats added to opponent serializer (Pokédex-public knowledge, no hidden-info violation)

### v0.20 — Rich Stats Dashboard
- **Global stats page**: summary KPIs, battles by tier, top Pokémon, top moves, recent battles feed
- **Per-model stats expanded**: Pokémon/move usage lists, action distribution stacked bar, win-rate-by-tier panel
- Backend uses `json_extract()` to mine `turns.state_json` at the SQL layer

### v0.21 — UI Polish
- Type badges + PP in heuristic advisory drawer
- Client-side leaderboard search/filter; copy model ID button
- Win-streak column (🔥N, pulses orange at ≥3)
- Battle log keyword filter; press R / REPLAY button from winner banner
- Pokéball favicon

### v0.22 — Zero-Lag State Updates (OP-01)
- **`_StreamingMixin._handle_battle_message` hook**: emits a render-only `state_update` the instant Showdown resolves a turn frame, before the next `|request|` arrives; battlefield HP bars update immediately on turn resolution rather than waiting for the next decision
- `serialize_battle(light=True)` for cheap render-only snapshots (omits heuristics/threat-map/legal-actions); frontend merges `state_update` to preserve last advisory

### v0.23 — Bug Fix Batch
- **SQLite threading**: per-thread connections via `threading.local()` fix concurrent `InterfaceError` / `IndexError` on page load
- **Non-draft non-random team rejection**: auto-generate random preset teams when `draft=false` in a non-random tier instead of sending `|/utm null`
- **P1 draft screen missing**: EventBus replay buffer ensures late-joining WebSocket subscribers receive all structural events since `battle_start`
- **Baton Pass ban**: removed from 6 gen3ubers-incompatible movesets
- **Challenge hang**: 60 s timeout on `_battle_semaphore.acquire()` converts infinite hangs to clean `failed` status

### v0.24 — Showdown Built-in Battle Scene (OP-02)
- **Spectator-proxy WebSocket** (`/ws/showdown/{room}`): guest login + `/join` + verbatim frame relay to the browser; room ids validated against a strict `battle-*` pattern; login frames suppressed; injectable upstream for unit testing
- **`showdown_room` EventBus event**: emitted on the first battle frame so the frontend learns the Showdown room id; included in the replay buffer for late-joining subscribers
- **PS bundle loader** (`useShowdownBundle`): fetches 14 CDN scripts from `play.pokemonshowdown.com` in strict dependency order; singleton load promise; `window.Config` stub injected first
- **`ShowdownBattleScene`**: opens the spectator-proxy socket after the PS `Battle` instance is ready; Showdown server replay eliminates any line-buffer requirement
- **Classic / Showdown toggle**: tab bar in the live battle view; defaults to Classic; `localStorage`-persisted preference; graceful fallback to Classic when no room is available yet
- **Integration test gate**: `pytest.mark.integration` marker; `test_proxy_relays_init_battle_and_turn_frames` creates a real Gen 3 battle and asserts the proxy relays `|init|battle` + `|turn|`; excluded from the default test run via `addopts`
- Small fixes: LM Studio stats `json_extract` guard (#95); model labels not cleared before next `battle_start` (#96)

### v0.25 — E2E Testing & Moveset Correctness
- **Playwright smoke suite**: full pipeline test — start battle → watch 5 turns via WebSocket → cancel → replay; headless Chromium; cancel-based design avoids stall-game timeouts; `npm run test:e2e`
- **React StrictMode purity fix**: `delete buf[n]` inside `setEvents` updater crashed on StrictMode's double-invocation; moved eviction to the ref before the pure updater
- **Vite proxy noise**: `ECONNRESET` + `EPIPE` added to the silent-error set so the dev terminal stays clean when the browser disconnects mid-stream
- **Illegal Gen3 moves removed**: Signal Beam (espeon → Baton Pass) and Iron Head (mawile → Rock Slide) are Gen 4+ and were rejected by Showdown's legality checker
- **Hidden Power IV spreads**: all 22 HP users now carry explicit `ivs` in `gen3_movesets.json`; without them Showdown defaulted to all-31 (= HP Dark); all 7 types now emit at max power (70 BP); `build_pokemon_block` extended with `IVs:` line support; 5 new tests verify type/power correctness

### v0.26 — Showdown Cockpit (rehab + made primary, #148)
- **Token foundation** (#149): spacing + type scales in `:root`; reusable `<EmptyState>` on leaderboard / recent battles / global stats
- **Rehab baseline** (#150): PS `battle.css` loaded via `<link>` in `useShowdownBundle` (root cause of "no HP bars / janky scene" — it was never loaded); fixed 640×400 centred stage; cockpit shell (header strip + stage + log)
- **Data parity** (#151, #153): model labels per side, win-probability bar, heuristic advisory (move scores + type badges + PP), thinking badge — all extracted into shared `battleShared.jsx` so Classic and the cockpit can't drift
- **Log contrast fix** (#153): override PS's light-on-light `h2.battle-history` turn headers so they're legible on the dark cockpit
- **Lifecycle chrome shared** (`battleChrome.jsx`): cancel control, winner banner, tournament progress bar, tournament-end overlay, and tier/draft badges lifted out of `BattleField` to wrap *both* stages — App.jsx owns the overlays; each stage renders the inline controls
- **Showdown is now the default view**; Classic remains available behind the toggle as a zero-cost fallback (`nidozo-battle-view` localStorage default flipped to `showdown`)

### v0.27 — Reliability, Validation & Cockpit Polish
- **Cancel actually stops the sim** (#159): cancelling a battle now tears down the poke-env players (closes their websockets) so the Showdown simulation halts instead of playing on after the DB row is marked cancelled; teardown wraps every runner's `battle_against` in a `finally`
- **Per-turn LLM timeout** (#164): `choose_move` bounds each `backend.complete()` with `asyncio.wait_for` (default 90s, `NIDOZO_TURN_TIMEOUT`) so a hung backend can't stall a battle
- **Distinct fallback reasons** (#164): `fallback_reason` now distinguishes `backend_timeout` / `backend_error` / `empty_response` / `parse_failure` instead of conflating them
- **Request-enum validation** (#165): `provider` / `coach_provider` / `prompt_version` / `tier` / `tournament_format` are Pydantic `Literal`s — invalid input is rejected at the boundary (422) instead of silently degrading; redundant manual route checks and dead `EventBus.publish_sync` removed
- **Cockpit polish**: heuristic drawer stays mounted on forced-switch turns instead of vanishing (#166); per-player heuristic drawers (#156); win-probability sparkline over time in the shared `WinProbBar`, shown in both views (#167)

### OP-03 — Gen 9 National Dex (single canonical ruleset, #85)
- Adopted **Gen 9 National Dex** as the one canonical ruleset, retiring hand-maintained per-generation legality (the root cause of the Signal Beam / Iron Head / Hidden Power IV failures)
- Formats: `gen9randombattle` (random), `gen9nationaldexag` (AG / free-for-all), `gen9nationaldex` (OU rules), `gen9nationaldexlc` (Little Cup) — Showdown validates teams; no ad-hoc legality checking
- Full-national-dex competitive sets in `data/natdex_movesets.json` (built by `scripts/build_natdex_sets.py`); heuristics/analyzer use the authoritative Gen 9 type chart via `GenData.from_gen(9)` (Fairy included); serializer/prompts are generation-agnostic

### v0.28 — Player Experience & Tera Awareness (#172–#175)
- **Personality profiles** (#172): named play-style personas (Aggressive, Defensive, Balanced, Trickster, Momentum) selectable per player; persona injected into system prompt to shape reasoning style; stored per model
- **Stats page expansion** (#172): global KPIs, battles-by-tier chart, top Pokémon and move usage, recent battles feed; per-model expanded with Pokémon/move usage lists, action distribution bar, win-rate-by-tier panel
- **Achievements & badges** (#173): per-model milestone badges (first win, win streak ≥5, perfect game, upset win vs higher ELO); badge gallery on stats page; live toast notifications via WebSocket `badge_earned` event; schema v14
- **Party presets — trainer themes** (#174): 10 curated 6-mon teams (Ash's Kanto, Dragon's Lair, Ghost Gang, Inferno Squad, etc.); `GET /api/presets`; `PresetSelector` in battle form with flavour text + team preview; `PresetBadge` chip in both battle views; always forces `gen9nationaldexag`; draft auto-bypassed
- **Terastallization awareness** (#175): `can_tera` at state level; `is_terastallized` + `tera_type` on own and opponent mons (opponent only revealed post-Tera); `tera_note` in heuristic battle context; `action_type: "tera_move"` in parser; prompt v6 with full Tera rules + `tera_move` action + example; `◈ TERA TYPE` badge on Pokémon card

### v0.29 — Competitive Annotations & Logging (#176–#177)
- **Competitive event annotations** (#176): per-turn history now surfaces status applied/cured (burn, paralysis, sleep, poison, toxic, freeze — own and opponent), item consumed (own, e.g. Focus Sash activated), opponent item/ability revealed on first observation, and priority bracket note on any opponent move with non-zero priority; `_update_hp_snapshot` extended to snapshot status/item/ability alongside HP; `_build_recent_events` diffs snapshots to generate human-readable annotation lines
- **Structured JSON logging** (#177): `configure_logging()` now called automatically in `create_app()` so logs are active on every startup; `LOG_LEVEL` env var controls verbosity (default `INFO`, set `DEBUG` for full traces); `LOG_FILE` env var mirrors all records to disk; `_JsonFormatter` emits `extra={}` fields as top-level JSON keys (structured `battle_id`, `player`, `turn`, `action`, `elapsed_s`, `prompt_tokens`, `completion_tokens`); per-turn INFO trace in `LLMPlayer` (`[p1] deciding turn 4` → `[p1] turn 4 → thunderbolt (2.3s)`); LM Studio / OpenAI backend logs token usage + latency at INFO and full prompt + response at DEBUG; `httpx`/`httpcore` loggers enabled at DEBUG to expose raw HTTP traffic to LM Studio

### v0.30 — Doubles Battles (2v2, decision layer)
- **2v2 with target selection**: `StartBattleRequest.doubles=true` runs a Showdown doubles format — random → `gen9randomdoublesbattle` (auto-generated teams), non-random tiers → NatDex Doubles via new `resolve_format()` / `TIER_TO_DOUBLES_FORMAT` in `tiers.py`. Doubles forces prompt **v7** and disables draft/preset (singles-only team builders)
- **Serializer**: `serialize_battle` routes `DoubleBattle` to a doubles state shape — `my_active`/`opponent_active` are per-slot lists (with a `slot` index), `force_switch`/`can_tera` are lists, `available_moves`/`available_switches` are per-slot lists-of-lists; every state carries `is_doubles`
- **Action parser extended for `target`**: doubles JSON is an `actions` array (one entry per active slot), each with an optional `target` (`foe_1`/`foe_2`/`ally`/`self`); resolved per slot into `SingleBattleOrder`s (validated against `get_possible_showdown_targets`) and combined into a `DoubleBattleOrder`. Handles spread/self moves (no target), `pass` for empty slots, and invalid-target fallback
- **Heuristics for spread moves & partner synergy**: `score_doubles_actions` scores both slots and tags each move with targeting metadata (spread-foes / hits-ally-too / choose-foe / self-or-ally), flags spread moves that also hit your own ally, and notes coverage vs the second foe
- **Prompt v7**: doubles-aware system prompt (spread moves, focus fire, partner synergy, target field) + dedicated `turn_doubles.txt.jinja`; `PromptBuilder` auto-selects it when `is_doubles`
- 13 new doubles tests (parser/heuristics/serializer); singles unaffected (858 tests green)

### v0.32 — Configurable Team Size + Doubles Frontend (#193, #194)
- **Configurable team size**: `team_size: Literal[3, 4, 6]` on all request types; 3v3 singles and 4v4 doubles dispatch to dedicated Showdown format strings (`TIER_TO_3V3_FORMAT`, `TIER_TO_DOUBLES_4V4_FORMAT`); draft sampling respects size; heuristics midgame threshold scales with `len(team)`; DB schema v15 adds `battles.team_size INTEGER NOT NULL DEFAULT 6`; Leaderboard form exposes doubles toggle + team-size selector
- **Doubles-aware Classic view**: `BattleField` renders a `doubles-pair` grid (two compact `PokemonCard`s per side) when `is_doubles`, bench row below; "2v2" amber badge in header; `BenchSlot` exported for external use; `compact` prop on `PokemonCard` (64px sprite, ellipsis name, tighter padding)
- **Doubles-aware Showdown cockpit**: heuristic panels use `PlayerHeuristicPanels` — two per-slot drawers for doubles, one drawer for singles; win-probability bar now counts active slots + bench HP in doubles
- **`PlayerHeuristicPanels`** shared helper in `battleShared.jsx` keeps both views in sync; `.doubles-heuristic-stack` CSS container keeps per-slot drawers in a single grid cell
- 939 tests green

### v0.35 — Human Player Mode (#197)
- **`StreamingHumanPlayer`** — poke-env Player whose moves arrive from the browser; registers an `asyncio.Future` each turn and awaits resolution via the API; falls back to random on timeout (configurable via `NIDOZO_HUMAN_TIMEOUT`, default 300 s)
- **`POST /api/battles/{id}/human-action`** — endpoint that resolves the pending Future with the human's chosen move or switch
- **`HumanActionPicker`** — overlay panel that appears over the Showdown stage when it's the human's turn; shows all legal moves (type badge, BP, PP, heuristic score) and switches (sprite, types, HP bar); one click submits the action
- **Human provider** added to the Leaderboard battle form ("🎮 You play!" label, no model dropdown); human skips draft, lessons, and narrative generation
- **17 new tests** in `test_human_player.py`: registry helpers + API endpoint validation
- 984 tests green

### v0.34 — Personality Profiles: Gap Fill (#196)
- **`GET /api/personalities`** — backend is now the single source of truth for persona metadata; `emoji` field added to `Personality` dataclass
- **`GET /api/stats/personalities`** — per-slug win/loss/tie counts and win-rate % across all completed battles
- **GlobalStats: Play Style Win Rates panel** — W/L/T segment bar + win-rate % per persona
- **`BattleForm`** fetches `/api/personalities` on mount; hardcoded list removed; selector shows persona description on selection
- **25 new tests**: `test_personalities.py` (registry consistency, lookup edge cases) + 9 additions to `test_prompt_builder.py` (injection, ordering, all 5 personas parametrised)
- 967 tests green

### v0.33 — Classic View Retirement
- **Showdown cockpit is now the sole live battle renderer** — `BattleField.jsx` deleted; the Classic card-based arena is fully retired
- **Cockpit parity features added before retirement**: turn counter (`TURN N` / `READY`), weather badge, 2v2 doubles badge, and Nidozo structured `BattleLog` (filterable action log) now all appear in the Showdown cockpit
- **Nidozo BattleLog + PS protocol log** rendered side-by-side in a new `.sbs-bottom` two-column grid at the foot of the cockpit
- **App simplification**: battle-view toggle state, `localStorage` read/write, and `CLASSIC` / `SHOWDOWN` toggle buttons removed from `App.jsx`
- **PokemonCard cleanup**: `compact` prop and `BenchSlot` export removed (Classic-only)
- **CSS**: ~120 lines of Classic-only rules removed (`.battlefield-wrapper`, `.battle-header`, `.turn-counter`, `.arena-player-col`, `.doubles-pair`, `.pokemon-card.compact`, `.battle-view-toggle`, `.bvt-btn*`)

---

## Upcoming

### Player Experience

---

### UI & Visualisation

**UI/UX Overhaul** ✅ shipped (phased — v0.31)
- ✅ Dark/light theme toggle persisted to `localStorage` (Phase 1)
- ✅ Mobile-responsive layout (Phase 2): stacking header/nav, wrapping tabs,
  horizontal-scroll data tables, scroll-wrapped Showdown stage, reduced
  shell/panel padding at tablet (≤768px) and phone (≤480px) breakpoints
- ✅ Typography & motion pass (Phase 3): ~329 hardcoded font sizes (26 muddy
  near-duplicate values) snapped onto a 13-step `--fs-*` token scale for a
  clean, centrally tunable hierarchy; `prefers-reduced-motion` support added
  across the 28 animations

---

### Platform Expansion

> Gen 9 National Dex is now the canonical ruleset — see **OP-03** under Completed.
> Configurable team size (3v3 / 4v4 / 6v6) shipped in v0.32.

**Doubles Battles — extensions** (decision layer + frontend shipped — see v0.30/v0.32)
- Doubles for drafted/preset teams (current cut is random + auto-generated NatDex Doubles teams)
- Doubles tournaments and seasons (currently single battles only)

**Deeper Competitive Features**
- Speed tier display: annotate whether each switch candidate moves before/after the opponent (extend existing speed note from heuristic advisory into the bench summary)

---

### Technical Debt & Housekeeping

**Containerisation**
- `docker-compose.yml` for the full dev stack: Showdown server, FastAPI backend, Vite dev server
- Production Dockerfile: multi-stage build, static frontend served from FastAPI
- README updated with Docker-first quickstart

**Test Coverage**
- **Tier 3 (partial)** — `pytest.mark.integration` infrastructure is in place; `test_ws_showdown_integration.py` covers the spectator proxy. Still needed: integration tests for `battle/orchestration.py` and `llm/draft.py`; dedicated CI job that starts the Showdown server

**Infrastructure**
- ~~Dependabot for Python and npm dependency updates~~ — shipped (weekly `uv` / `npm` / `github-actions` updates via `.github/dependabot.yml`)

# Changelog

All notable changes to Nidozo are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [0.32.0] — 2026-06-17

### Added
- **Doubles-aware Classic and Showdown battle views** — both the Classic
  card arena and the Showdown cockpit now correctly render 2v2 doubles
  battles. Classic: a `doubles-pair` grid shows two compact `PokemonCard`s
  per side (64px sprites, ellipsis name truncation, moves list hidden),
  with a bench row below. A "2v2" badge appears in the battle header.
  Showdown: the PS renderer handles the scene natively; the cockpit's
  heuristic panels now dispatch to two per-slot drawers (slot 0 / slot 1)
  for doubles, unchanged for singles. Win-probability bar now counts active
  slots + bench HP in doubles, not bench-only. `PlayerHeuristicPanels`
  helper shared by both views so they can't drift apart. (#194)
- **Configurable team size** — `team_size: Literal[3, 4, 6]` added to all
  three request types (`StartBattleRequest`, `StartTournamentRequest`,
  `StartSeasonRequest`). 3v3 singles and 4v4 doubles dispatch to dedicated
  Showdown format strings via new `TIER_TO_3V3_FORMAT` and
  `TIER_TO_DOUBLES_4V4_FORMAT` maps in `tiers.py`. Draft sampling respects
  the configured size. Heuristics midgame threshold now scales with team
  size (`≤ len(team)` remaining = the halfway point). DB schema v15 adds
  `battles.team_size INTEGER NOT NULL DEFAULT 6`. Frontend Leaderboard
  exposes a doubles checkbox and a team-size selector (3 / 4 / 6). (#193)

### Changed
- `BenchSlot` exported from `PokemonCard.jsx` for use in `BattleField`.
- `PokemonCard` gains a `compact` prop (smaller sprite, reduced padding,
  name truncation) used by the doubles pair layout.

---

## [0.31.0] — 2026-06-17

### Added
- **Typography & motion pass** (UI/UX overhaul, Phase 3) — the stylesheet had
  ~329 hardcoded font sizes spread across 26 near-duplicate values
  (0.58/0.6/0.62/0.63rem…) that muddied the hierarchy. They're snapped onto a
  documented 13-step `--fs-*` token scale (small intentional ≤~10% shifts on the
  worst offenders), so type sizing is now consistent and tunable from one place.
  Added `prefers-reduced-motion` support: the 28 animations (hit flash, sprite
  shake, heal pulse, faint fade, thinking pulse, scanlines) and all transitions
  collapse to near-instant for users who request reduced motion — functional
  state still applies, only the motion is removed.
- **Mobile-responsive layout** (UI/UX overhaul, Phase 2) — the app shipped
  desktop-only; it now adapts down to phone widths. A consolidated responsive
  layer adds tablet (≤768px) and phone (≤480px) breakpoints: the header/nav
  stacks (logo above a wrapping nav, theme toggle pushed right), shell/panel
  padding shrinks, the battle/tier/tournament tab rows wrap, wide data tables
  (leaderboard, head-to-head matrix, tournament/season/stats tables) gain
  horizontal scroll, and the fixed 640×360 Showdown stage is wrapped in a
  horizontal-scroll container so it no longer forces the page wider. Desktop
  layout is unchanged.
- **Dark/light theme toggle** (UI/UX overhaul, Phase 1) — a persisted theme
  switch in the nav (`☀`/`☾`). `useTheme` hook + an inline no-flash script in
  `index.html` set `<html data-theme>` before paint and remember the choice in
  `localStorage` (falling back to the OS `prefers-color-scheme`, defaulting to
  dark). `main.css` gains a `:root[data-theme="light"]` layer overriding the
  structural tokens (surfaces, borders, text, and neon accents tuned for
  contrast on white); type colours, HP fills, and the Showdown game stage stay
  constant. Dark remains the default and is unchanged.

### Changed
- **Dependabot** — added `.github/dependabot.yml` for weekly automated dependency
  updates across three ecosystems: `uv` (Python, root `pyproject.toml`/`uv.lock`),
  `npm` (`frontend/`), and `github-actions` (pinned workflow actions). Minor/patch
  bumps are grouped per ecosystem to reduce PR noise; majors open individual PRs.

---

## [0.30.0] — 2026-06-16

### Added
- **Doubles battles (2v2)** — full decision-layer support for 2v2 with target
  selection. `StartBattleRequest.doubles=true` runs a Showdown doubles format
  (random → `gen9randomdoublesbattle`; non-random → NatDex Doubles) and forces
  the new **prompt v7**.
  - **Serializer**: `serialize_battle` routes `DoubleBattle` to a doubles state
    shape — `my_active`/`opponent_active` are per-slot lists, `force_switch` and
    `can_tera` are lists, `available_moves`/`available_switches` are per-slot
    lists-of-lists, and `is_doubles` is on every state.
  - **Action parser**: parses a doubles JSON `actions` array (one entry per
    active slot) with an optional `target` field (`foe_1`/`foe_2`/`ally`/`self`),
    resolving each into a `SingleBattleOrder` (validated against
    `get_possible_showdown_targets`) combined into a `DoubleBattleOrder`.
    Handles spread/self moves (no target), `pass` for empty slots, and invalid
    targets (falls back to a legal one).
  - **Heuristics**: `score_doubles_actions` scores both slots, adds per-move
    targeting metadata (spread-foes / hits-ally-too / choose-foe / self-or-ally),
    flags spread moves that damage your own ally, and notes partner-relevant
    coverage vs the second foe.
  - **Prompt v7**: doubles-aware system prompt (spread moves, focus fire,
    partner synergy, target field) plus a dedicated `turn_doubles.txt.jinja`
    template; `PromptBuilder` selects it when the state is doubles.
  - 13 new doubles tests covering parser, heuristics, and serializer.

---

## [0.25.0] — 2026-06-10

### Fixed
- **Cancelled battles corrupted W/L/T stats** — `cancel_battle` sets
  `finished_at`, but leaderboard, matchup matrix, model stats, season standings,
  and global stats filtered only on `finished_at IS NOT NULL`, counting every
  cancelled battle as a tie (and applying a phantom ELO draw in season replay).
  All ten aggregation sites now also require `status='completed'`. (#138, closes #129)
- **Bracket ties credited to p2** — `run_bracket_tournament` computed
  `winner = 1 if p1 won else 2`, silently recording a tied battle (Explosion,
  Destiny Bond, Struggle, …) as a p2 win with a real ELO gain. Ties are now
  recorded honestly (ELO draw); bracket advancement uses a deterministic
  better-seed tiebreak for routing only. (#139, closes #131)
- **Tournament/season cancel was a no-op** — the cancel endpoints only flipped
  the DB status; the runners kept playing and then overwrote `cancelled` with
  `completed` at the end of the loop. Runners now re-check status between
  battles and stop, and finalization only writes `completed` when still
  running. `cancel_season` also publishes `season_cancelled` immediately. (#140, closes #130)
- **Model ELO chart froze after 30 battles** — `get_model_stats` ordered the
  ELO history `ASC LIMIT 30`, pinning the chart to a model's *earliest* 30
  battles. Now takes the most recent 30, re-sorted chronologically. (#141, closes #132)
- **Cancelling a multi-battle run stranded the rest** — the `CancelledError`
  handler only marked the current battle cancelled; later battles stayed
  `pending` until the next restart. All still-queued battles are now cancelled
  with events. (#142, closes #134)
- **Post-battle tasks could be garbage-collected** — lesson/narrative
  generators were fire-and-forget with no strong reference, so the event loop's
  weak reference let the GC drop them mid-flight. A module-level set now holds
  them until completion. (#143, closes #135)
- **Season battles never got narratives** — every other runner scheduled a
  post-battle narrative; `run_season` only scheduled lessons. Season battles now
  get narratives too, for consistent BattleAnalysis output. (#144, closes #137)
- **Analyzer used Gen 3 data against Gen 9 battles** — the draft critique used a
  hardcoded Gen 3 type chart (no Fairy) and loaded `gen3_movesets.json`, so
  modern species silently went missing and Fairy interactions were ignored. Now
  derives the chart from poke-env's `GenData.from_gen(9)` and loads
  `natdex_movesets.json`. (#145, closes #133)
- **Qwen / LM Studio 0% JSON parse rate** — `v5` was missing from
  `_JSON_OUTPUT_PROMPT_VERSIONS`, so json_mode was never activated for the
  current default prompt. LM Studio now uses the simpler `json_object` grammar
  (`{"type":"json_object"}`) instead of the full `json_schema` that many local
  models reject. (#117)
- **Schema v12** — stale `gen3randombattle` and `v4` defaults replaced with
  `gen9randombattle` / `v5` in the seasons DDL and v10 migration; new
  `fallback_reason TEXT` column on `turns` records whether a move was chosen
  due to a parse failure or a backend error. (#118)
- **Action parser fuzzy move matching** — `_resolve_move` now applies the same
  `difflib` fuzzy matching that `_resolve_switch` already had, so one-character
  typos in a model's output no longer fall back to a random move. (#119)
- **`fallback_reason` end-to-end** — wired through store queries, replay API
  endpoint, analyzer, and BattleReplay UI so the distinction between a "parse
  failure" and a "random fallback" is surfaced everywhere. (#119, #121)
- **Orchestration format defaults** — `run_battles`, `run_tournament`,
  `run_bracket_tournament`, and `run_season` were using `gen3randombattle` /
  `gen3ou` while the API recorded battles as gen9, silently mismatching
  ruleset. (#122)
- **`run_battle.py` model version tracking** — `get_or_create_model` was
  called without `prompt_version`, defaulting to `"v1"` regardless of
  `--prompt-version`, splitting ELO tracking into separate model rows. (#123)
- **`get_model_stats` SQL aggregation** — replaced a Python-side row fetch +
  count loop with a single `SELECT COUNT(*), SUM(parse_success)` query. (#123)
- **Form error display** — BattleForm, TournamentForm, and SeasonForm silently
  swallowed API errors. Non-2xx responses now surface `data.detail` in a red
  banner above the submit button; network failures are shown too. (#124)
- **`get_tournament` import / except clause** — redundant `import json as _json`
  removed (module-level `import json` was already present); bare
  `except Exception` tightened to `except json.JSONDecodeError`. (#125)
- **`draft.py` format fallback** — unreachable `"gen3ou"` fallback in
  `TIER_TO_FORMAT.get` corrected to `"gen9nationaldexag"`. (#125)
- **`build_natdex_sets.py` lint** — removed unused `import sys`, dead `name_re`
  regex, and a spurious f-string prefix flagged by ruff. (#127)

### Performance
- **Each streaming turn serialized the battle twice** — `StreamingLLMPlayer`
  ran the full heuristic engine once for its WS events and again in the base
  player for the prompt/DB state. The base now reuses the snapshot the streaming
  subclass already computed, halving per-turn serialization cost. (#146, closes #136)

### Changed
- Leaderboard script now reads `NIDOZO_DB` env var (was `NIMZO_DB`; old name
  still accepted as a backward-compat alias). (#121)
- CLI scripts (`run_battle.py`, `tournament.py`) updated to default
  `prompt_version="v5"` and `fmt="gen9randombattle"`, matching the API. (#120)

---

## [0.24.0] — 2026-06-09

### Added
- **Showdown spectator renderer (OP-02)** — the PS battle scene is now
  available as a first-class view alongside the existing Classic battlefield.
  Five-stage implementation:
  - **Stage 0 (proxy)** — `/ws/showdown/{room}` WebSocket endpoint performs
    guest login + `/join` against the local Showdown server and relays the raw
    protocol stream verbatim to the browser. Room ids are validated against a
    strict `battle-*` pattern; login frames are suppressed. Fully unit-tested
    via an injectable `connect_upstream` fake.
  - **Stage 1 (bus event)** — `_StreamingMixin._handle_battle_message` emits a
    `showdown_room` event on the JSON EventBus (first frame per battle only) so
    the frontend learns the Showdown room id. `showdown_room` added to the
    replay-buffer set so late-joining WebSocket subscribers receive it.
  - **Stage 2 (CDN bundle)** — `useShowdownBundle` hook loads the PS battle
    renderer from `play.pokemonshowdown.com` in strict dependency order (14
    scripts, singleton load promise, `window.Config` stub injected first).
    `ShowdownRenderSpike` static-replay proof-of-concept verified in browser.
  - **Stage 3 (live wiring)** — `ShowdownBattleScene` component opens the
    spectator-proxy socket after the PS `Battle` instance is ready; Showdown
    server replay eliminates any need for a line buffer.
  - **Stage 4 (view toggle)** — Classic / Showdown toggle bar in the live
    battle view; defaults to Classic; preference persisted in `localStorage`.
    Falls back to Classic when the Showdown room is not yet available.
- **Integration test gate** — `tests/test_ws_showdown_integration.py` (new
  `pytest.mark.integration` marker) creates a real Gen 3 battle via raw
  WebSocket bots, connects the in-process proxy, and asserts `|init|battle`
  + `|turn|` are relayed. Auto-skips if Showdown is not on `localhost:8000`.
  `addopts = "-m 'not integration'"` excludes it from the default test run.

### Fixed
- **LM Studio stats crash** — `json_extract` on `turns.llm_response` now
  guards against non-JSON values (raw text fallbacks, error strings) that
  caused `sqlite3.OperationalError` on the global stats page (#95).
- **Model labels cleared too early** — `reset()` no longer clears `p1Label`
  / `p2Label` before the next `battle_start` event arrives, preventing a
  blank-label flash on back-to-back battles (#96).

---

## [0.23.0] — 2026-06-08

### Fixed
- **SQLite threading** — `BattleStore` now uses per-thread connections via
  `threading.local()` instead of a single shared `sqlite3.Connection`.
  Concurrent FastAPI route handlers were corrupting cursor state, causing
  `InterfaceError: bad parameter or other API misuse` and `IndexError:
  tuple index out of range` on `/api/battles` and `/api/leaderboard` page
  loads. SQLite WAL mode (already set by the migration) handles concurrent
  readers at the file level. A `_closed` flag preserves the invariant that
  a closed store raises `ProgrammingError` from any thread.
- **Non-draft non-random team rejection** — Starting a freeforall (or any
  non-random tier) battle with `draft=false` sent `|/utm null` to Showdown,
  which rejected it with "This format requires you to use your own team."
  All three battle runners (single, tournament, season) now auto-generate
  random preset teams from the tier pool via `_random_preset_team()` when
  skipping the draft in a non-random format.
- **P1 draft screen not appearing** — `EventBus` now maintains a bounded
  replay buffer (deque, max 100) of structural events since the most recent
  `battle_start`. Subscribers that connect after the battle started receive
  an immediate replay of draft events — fixing the race where P1's
  `draft_start` / `draft_pick` events were published before the WebSocket
  was established. Per-turn events (`turn`, `state_update`, `thinking`) are
  excluded from the buffer to prevent log duplicates on reconnect.
- **Baton Pass banned in gen3ubers** — Six movesets (`jolteon`, `umbreon`,
  `espeon`, `ninjask`, `mawile`, `smeargle`) had Baton Pass, which is
  illegal in the gen3ubers format used for freeforall battles. Replaced with
  legal Gen 3 alternatives.
- **Battle hang on team rejection** — `_send_challenges` now times out after
  60 s if Showdown rejects the team (previously blocked forever on
  `_battle_semaphore.acquire()`). A `TimeoutError` publishes an error event
  to the bus and raises `RuntimeError` so the battle is marked `failed`
  instead of hanging indefinitely.

---

## [0.22.0] — 2026-06-08

### Added
- **Zero-lag state updates (OP-01)** — Hooked `_handle_battle_message` in
  `_StreamingMixin` to emit a render-only `state_update` the instant
  Showdown resolves a turn frame, before the next `|request|` arrives.
  Battlefield HP bars and active Pokémon now update the moment a turn
  resolves rather than waiting for the next decision prompt.
  `serialize_battle(light=True)` added for the cheap render-only snapshot
  (omits heuristics / threat map / legal actions). Frontend merges
  `state_update` into existing state to preserve the last advisory.

---

## [0.21.0] — 2026-06-08

### Added
- **UI polish (8 quick wins)** — Type badges and PP display in the
  heuristic advisory drawer; client-side leaderboard search/filter; copy
  model ID button per leaderboard row; win-streak column (🔥N, pulses
  orange at 3+); battle log keyword filter with match count; press R to
  watch replay from winner banner; REPLAY button on winner banner;
  Pokéball favicon.

---

## [0.20.0] — 2026-06-08

### Added
- **Rich stats dashboard** — New STATS nav page with global KPIs, battles
  by tier, top Pokémon, top moves, and recent battles feed. Per-model stats
  expanded with Pokémon/move usage lists, action distribution stacked bar,
  and win-rate-by-tier panel. Backend uses `json_extract()` to mine
  `turns.state_json` and `turns.llm_response` at the SQL layer.

---

## [0.19.0] — 2026-06-08

### Added
- **Pokémon mouseover tooltip** — Hovering any active or bench Pokémon
  shows a tooltip with base stats (color-coded bars), Gen 3 type matchup
  table grouped by multiplier (4× / 2× / ½ / ¼ / 0×), and revealed
  ability/item. Base stats added to opponent serialization (Pokédex-public
  knowledge, not a hidden-info violation).

---

## [0.18.0] — 2026-06-08

### Fixed
- **Battle scene lag** — `state_update` now emitted at the start of
  `choose_move` (request parsed, stats fresh) so the battlefield refreshes
  before the LLM think-time rather than after. Eliminates the stale-HP
  window between turns.

---

## [0.17.0] — 2026-06-08

### Added
- **Model name labels on battle scene** — Provider + model name displayed
  above each Pokémon card (P1 cyan, P2 amber).
- **Own-mon move display** — Active Pokémon card shows all 4 moves with
  type-color dot, BP, and PP (red when low).
- **Model dropdowns** — Provider selector replaced with `<select>` dropdowns;
  LM Studio live models and static presets for Anthropic/OpenAI populate the
  list; "custom…" option falls back to text input. Claude Sonnet 4, Haiku
  3.5, Opus 4, and o4-mini added as presets.

### Fixed
- **`<think>` block stripping** — Action parser now strips `<think>...</think>`
  blocks from reasoning-model responses (e.g. Qwen 3) before parsing the
  JSON action.

---

## [0.16.0] — 2026-06-08

### Added
- **LLM battle narrative** — `narrator.py` generates a 4–6 sentence
  plain-text battle story after each completed battle; stored in
  `battles.narrative` (schema v11); exposed via `/api/battles/{id}/analysis`;
  shown as "Battle Story" at the top of the Battle Replay analysis panel.
- **Switch quality labels** — `annotate_turn` now classifies each switch as
  `good_switch` / `bad_switch` / `neutral_switch` / `forced_switch` using
  heuristic switch scores; switch breakdown (counts per type) surfaced in
  per-player analysis summary and quality bars.
- **Richer turning-point description** — Turning-point text now includes the
  move names and win-probability swing rather than just the turn number.

---

## [0.15.0] — 2026-06-08

### Added
- **Prompt v5** — Decision framework and KO-risk signal. New additions over
  v4: actual computed stats (Spe / Atk / SpA / Def / SpD) for own active
  Pokémon; last move used surfaced for both own and opponent active; KO-risk
  note injected when the opponent can OHKO or the player can OHKO the
  opponent this turn; explicit decision-framework section in the system
  prompt guiding reasoning order (KO opportunity → survival → type
  advantage → speed). Default prompt version bumped to `v5`.

---

## [0.14.0] — 2026-06-08

### Added
- **Seasons** — Named competition seasons with a fixed participant list,
  round-robin scheduling across all rounds, and per-season isolated ELO
  ratings. Live standings page with progress bar and per-season battle
  history. Start/cancel from the UI. `seasons` and `season_battles` tables
  (schema v10).
- **Head-to-head matchup matrix** — New tab on the leaderboard showing
  win/loss/tie counts for every model pair; tier-filterable.
- **`app.py` split** — FastAPI application factory refactored into separate
  `lifespan.py` and `middleware.py` modules; `app.py` reduced to wiring.
- **Tier-2 test coverage** — 53 async unit tests for the API layer
  (`api/events.py`, `api/ws.py`, `api/helpers.py`, `api/app.py`).

---

## [0.13.0] — 2026-06-08

### Added
- **Prompt v4** — battle event history (last 3 turns of HP deltas), explicit
  moveset revelation count per opponent mon, opponent threat map pre-computed
  per threatened mon, cleaner section layout separating confirmed facts from
  partial observations

### Fixed
- **Double-elimination bye stall** — LB bye slots no longer stall when two WB
  byes feed the same losers-bracket column; fixed-point resolver handles chains
- **Tournament failure handling** — unhandled match exceptions now cleanly
  abort the bracket loop, mark the tournament `failed`, and emit a
  `tournament_failed` WebSocket event; seed-resolution failures also abort
  rather than silently continuing
- **ELO idempotency** — `finish_battle` is now fully idempotent: `AND
  finished_at IS NULL` guard prevents double-apply; `INSERT OR IGNORE` on
  `elo_history`; `UNIQUE(battle_id, model_id)` index enforced at DB level
  (schema v9)
- **Transaction atomicity** — `status='completed'` folded into the same UPDATE
  as `finish_battle`; eliminated redundant `set_battle_status` calls after
  `finish_battle` in orchestration
- **Analysis correctness** — RNG inference now uses defender's own `my_active`
  key (not opponent's) so HP-delta comparisons are from the correct perspective;
  `_team_hp_score` includes the active Pokémon in win-probability calculation;
  status moves get an early-return (no blunder flag) in `annotate_turn`
- **Serializer deduplication** — opponent threat map no longer double-counts
  the active Pokémon (it is already in `opponent_team`)
- **Dead code removed** — `CoachAgent.max_tokens` parameter eliminated;
  `__version__` now sourced from package metadata via `importlib.metadata`
- **Leaderboard games count** — `games` now computed as `wins + losses + ties`
  from a filtered per-tournament subquery instead of the raw global sum

---

## [0.12.0] — 2026-06-08

### Added
- **Coach mode** — optional pre-turn advisor: any model can query a separate
  "coach" model before acting; coach advice appended to the player's turn
  prompt; `agent: "coach"|"player"` field in WebSocket thinking events;
  `coach_advice TEXT` column added to turns table (schema v8)
- **Tournament brackets** — single-elimination and double-elimination formats
  with seeded byes for non-power-of-2 fields; lazy battle creation;
  `bracket_update` WebSocket event; `BracketView` React component;
  `tournament_format` and `bracket_state` columns added to tournaments table
  (schema v7)
- **Richer lesson prompting** — draft critique, variance report, and
  win-probability timeline now fully surfaced in the lesson generation prompt;
  lessons grounded in specific blunders and turning-point turns rather than
  generic reflection; new helper functions in `lesson_generator.py`
- **Tier 1 test coverage** — 565 tests at 88% overall coverage; targeted unit
  tests for all pure-Python modules: analyzer RNG inference paths, heuristic
  edge cases, bracket routing, schema migration idempotency, API validation

---

## [0.11.0] — 2026-05

### Added
- **Cross-battle lessons** — LLM generates a 2–3 sentence lesson after each
  battle; stored in SQLite `lessons` table; injected into future system prompts
  so models adapt strategy over time
- **Per-model stats page** — W/L/T history, ELO sparkline, opponent breakdown,
  decision-quality distribution, lesson log
- **Richer post-game analysis** — per-turn key moments (blunders, RNG events,
  turning point); `AnalysisSummary` panel in Battle Replay with clickable
  moments; blunder flagging (≥40% score gap); probable crit/miss inference from
  HP delta; win-probability timeline from team HP ratio
- **Tournament mode** — round-robin with live progress, standings overlay, and
  mid-run cancel support; full tournament history page
- **Drafted teams + Smogon meta tiers** — LLM snake-drafts a 6-mon team from a
  curated pool; 8 tier formats (Random / OU / UU / NU / LC / Ubers /
  Freeforall); DraftPhase UI; `teams` table in DB; rosters on result card
- **Heuristic overhaul** — speed-tier awareness (Gen 3 paralysis ×0.25),
  weather damage modifier, accuracy-adjusted damage estimates, low-PP warnings,
  battle context block, switch quality scoring with matchup labels
- **Draft critique** — team composition analysis: STAB coverage, shared
  weaknesses, coverage gaps, execution quality
- **Variance report** — structured RNG tally with per-player benefit counts and
  plain-English verdict
- **Gen 3 pool expansion** — 93 → 153 species with Smogon ADV sets
- mypy strict mode enforced across all source files; 358 tests

---

## [0.10.0] — 2026-05

### Added
- Frontend ESLint v10 CI gate; pytest coverage gate at 65%
- Pydantic `Field(ge/le)` bounds on all API inputs (422 on bad requests)
- 6 DB indexes for hot read paths
- Atomic `finish_battle` + ELO update; EventBus queues bounded at 256

### Fixed
- `failed` battle status wired end-to-end
- `migrate()` crash on v1 databases (index before column existed)
- `AnthropicBackend` multi-block response crash
- Opponent `ability` hidden-information guard; `serve.py --reload`

### Changed
- Inline SQL consolidated into `BattleStore`; heuristic bogus tokens removed
- 203 tests

---

## [0.9.0] — 2026-04

### Added
- Live pipeline — all battles routed through shared EventBus
- Battle Replay — scrub slider, keyboard nav, auto-play, HP timeline SVG
- Type-themed card backgrounds (18-type colour map, diagonal dual-type gradient)
- Battle animations — hit flash, sprite shake, heal pulse, faint fade
- Win probability timeline, turning-point detection, blunder flagging, RNG
  inference; tournament UI with live progress and cancel

### Fixed
- Parser fix for `"switch 1"` identifier form

### Changed
- 154 tests

---

## [0.8.0] — 2026-04

### Added
- Prompt v2 — JSON structured output; LM Studio grammar sampling
- Fuzzy species name matching (difflib, cutoff 0.82)
- Thinking events (amber pulse), Gen 3 sprites (Showdown CDN), bench row
- Model selector (live LM Studio `/v1/models`), WebSocket keepalive (25 s)
- CI pipeline: ruff + pytest + frontend build in parallel

### Fixed
- `reasoning_content` fallback for Qwen 3 thinking models
- Leaderboard duplicate rows (UNION ALL bug)

### Changed
- 127 tests; first ELO results: gemma-4-e2b 7-3 vs ministral-3-3b

---

## [0.7.0] — 2026-03

### Added
- Round-robin tournament CLI (`scripts/tournament.py`)
- Per-player model fields (separate p1/p2 provider + model in API and UI)
- Parser hardening for name-based actions and markdown-wrapped output

### Changed
- First live LLM battles: Ministral-3-3b vs Granite-4-h-tiny (12-0)

---

## [0.6.0] — 2026-03

### Added
- Post-game analysis: per-turn decision quality annotation (optimal / good /
  suboptimal / fallback); `/api/battles/{id}/analysis`; analysis panel in UI

---

## [0.5.0] — 2026-02

### Added
- FastAPI backend + WebSocket live-battle feed (`/ws/battles`)
- React + Vite frontend: retro CRT dark-theme battlefield visualizer
- Live Pokémon cards (animated HP bars, type badges, status, stat boosts),
  battle log, heuristic advisory drawer, winner banner

---

## [0.4.0] — 2026-02

### Added
- SQLite persistence: battles, turns, elo_ratings, elo_history, models
- ELO calculation (K=32) updated after each battle; leaderboard CLI

---

## [0.3.0] — 2026-01

### Added
- Heuristic engine: type effectiveness, estimated damage %, stat stages,
  priority, status annotation, switch matchup scoring; advisory not prescriptive

---

## [0.2.0] — 2026-01

### Added
- Pluggable model backend: Anthropic + OpenAI cloud; LM Studio local
- Battle state serializer with hidden-information enforcement
- Prompt v1: battle state, legal actions, `ACTION: move N` output format
- Versioned prompts; `LLMPlayer` full loop

---

## [0.1.0] — 2026-01

### Added
- Repo scaffold, Python project (`uv`, `pyproject.toml`)
- Local Pokémon Showdown server wired with poke-env
- Two RandomBots complete a Gen 3 random singles battle end to end

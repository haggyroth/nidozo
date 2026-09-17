# Changelog

All notable changes to Nidozo are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

- fix(api): **the log redactor now covers HTTP credentials, not just `?token=`** (#283). `configure_logging()` turns the httpx / httpcore loggers up to DEBUG so a failing backend call can be diagnosed, and the docstring promised that "exposes every HTTP request and response — headers, status codes, and timing included". It does not: those libraries log method, URL, status and timing today, so no `Authorization` header is written *right now* — but nothing in our code made that true, it was their repr that did. A version bump rendering headers would have begun writing `Authorization: Bearer sk-…` and `X-Api-Key` into stdout and `LOG_FILE`, where a credential is readable by anyone with log access and survives the incident. The filter that already strips `?token=` (#276) now strips credentials too, at two levels: by **value** — this process's own `NIDOZO_API_TOKEN` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `LM_STUDIO_API_KEY`, masked wherever they appear, in any rendering — and by **shape** (`Bearer …`, `x-api-key` / `api_key` values, self-identifying `sk-…` keys) as a backstop for credentials we do not hold. The by-value pass is the guarantee: no pattern can anticipate every header name a future library decides to print, and a custom one defeats all of them. `_JsonFormatter` scrubs the rendered `message`, `exc`, `stack`, and string `extra=` fields as well, since a secret can arrive inside a non-string argument — a headers dict, a request object — that the record-level filter cannot see into; a traceback carrying a keyed URL is that same case. httpx stays at DEBUG: muzzling the loggers would also satisfy "no headers in the logs" while deleting the traffic detail the level exists for. A value shorter than 8 characters is left alone, so a placeholder like `LM_STUDIO_API_KEY=local` doesn't shred unrelated log text

- fix(api): **blocking SQLite writes ran on the event loop** (#282). `BattleStore` is synchronous sqlite3 by design — every call blocks until the query *and* its `commit()` return — but the cancel handlers called it straight from `async def`, and both players wrote their turn-log row the same way from inside `choose_move`. A turn row carries the whole serialized battle state plus the raw LLM response, so the longest write is also the one that happens twice per turn; while it ran, nothing else did: not the WebSocket stream, not another request, not the other battle in flight. The four cancel handlers now hand their store calls to a worker thread (`asyncio.to_thread` — safe because the store already keeps one connection per thread), and the four start handlers became plain `def`, which FastAPI runs in the threadpool: none of them awaited anything, so `async def` bought nothing and cost the loop every INSERT that seeds the battle rows. `_log_turn` is now awaited in both `LLMPlayer` and `HumanPlayer`. One async handler was left async on purpose — `/api/battles/{id}/human-action` resolves an `asyncio.Future` the human player is awaiting, and resolving a Future from another thread is not safe. Tested two ways: a spy on the store's connection accessor asserts no query reaches it on the loop thread (all eight endpoints, both players), and a deliberately slow 300ms write must leave the loop free to schedule — it managed **0 heartbeats** with the calls put back on the loop

- fix(api): **raw exception text was broadcast to every connected browser** (#281). All five battle-runner failure paths published `str(exc)` on the shared event bus, so an `error` event could carry a backend's `HTTPConnectionPool(host='…'): Max retries exceeded with url: /v1/chat/completions [key=sk-live-…]`, a moveset file path, or an internal tier list — straight into a browser tab, in a channel that fans out to *every* viewer rather than the one who started the battle. The published message is now decided by a new `nidozo/errors.py`: only a `UserFacingError` — raised where the wording is part of the product, e.g. the challenge-timeout message that tells the user Showdown rejected their team — is shown verbatim, and everything else becomes a generic line pointing at the logs. The `battle_id` still rides along, so a reported failure can be matched to its trace. Since the detail now exists *only* server-side, these five handlers also log `exc_info=True`; previously they logged the message alone, so the fix would otherwise have destroyed the traceback rather than relocating it

- fix(api): **the WebSocket endpoints accepted connections from any origin** (#280). WebSockets are exempt from the same-origin policy and CORS middleware never sees the handshake, so a page on any site could open a socket to `/ws/battles` or `/ws/showdown/{room}` and read the live battle stream — a cross-site WebSocket hijack, and the whole of the data flow on an instance running with `NIDOZO_ALLOW_INSECURE=1` (or, with a token set, a way to ride a leaked one). Both endpoints now check `Origin` before accepting: a handshake with no `Origin` at all is allowed (every browser sends one, so that is a script or `curl`, which a hostile page cannot make connect), same-origin is allowed with no configuration, `Origin: null` — a sandboxed iframe or a `file://` page — is rejected, and anything else must be listed in the new `NIDOZO_ALLOWED_ORIGINS`. The same-origin test compares host:port rather than scheme, so a deployment behind TLS termination still works. The allowlist is now shared with the CORS middleware, which had its own copy

---

## [0.46.2] — 2026-09-16

Four medium-severity audit findings, all of them cases where the code did the right thing on the happy path and something quietly wrong off it: a bracket that could never finish, four LLM calls with no deadline, a rate limiter that limited the wrong thing, and a credential that ended up in the logs.

- fix(api): the **API token was leaking into the logs** (#276). Browsers authenticated WebSockets with `?token=<secret>`, and uvicorn's access log records the full request line — so every live-stream connection wrote the shared secret to stdout and to `LOG_FILE` in plaintext. Browsers now send it in the `Sec-WebSocket-Protocol` handshake header as `nidozo-auth.<base64url(token)>` (a header a browser *can* set on a WS handshake, and one that never reaches an access log), the server echoes that subprotocol back — a browser aborts the handshake if the server accepts without selecting one of the offered protocols — and the endpoint reads the token from there. base64url is load-bearing: an RFC 6455 subprotocol value has to be a token, so a raw base64 secret (`+`, `/`, `=`) would make the browser throw before the socket opened. The `?token=` query parameter still works for `curl`/scripts, and a new log filter rewrites any `token=…` on its way to a sink (handler-level, so it covers child loggers like `nidozo.api.ws` and third-party ones, not just the loggers we configure). The browser also stops persisting the token in `localStorage` — it moves to `sessionStorage` (migrating any existing copy over once), so the credential is gone when the tab closes instead of sitting on disk for the life of the browser profile

- fix(api): the **rate limiter keyed every client on the reverse proxy's address** (#277). Behind Docker or any proxy, `request.client.host` is the gateway, so all clients collapsed into one bucket — the per-IP limit became a single global quota that one client could exhaust for everybody. The client key is now the rightmost `X-Forwarded-For` hop that isn't a trusted proxy, with the header read **only** when the direct peer is listed in `NIDOZO_TRUSTED_PROXIES` (empty by default, so a directly-reachable client still can't choose its own bucket). The bucket dict is also swept once per window instead of accumulating one entry per source IP forever, and an instance with `NIDOZO_API_TOKEN` set now defaults to **60 req/min** rather than to no limit — an exposed instance gets a bound without the operator configuring one, while local dev stays unlimited
- fix(llm): **coach, draft, lesson, and narrative LLM calls were unbounded** — only the player's own decision was wrapped in a deadline, so a stalled backend (an unloaded LM Studio model, a network hang) blocked the turn, the draft, or post-battle analysis *indefinitely*, defeating the timeout protection the player implements (#278). All four now share `NIDOZO_LLM_TIMEOUT` (default 120s; 0 disables), resolved through a new `llm/timeouts.py`, and `LLMPlayer` keeps a `wait_for` backstop on the coach so one stuck advisor can never hold the turn open. Each call still degrades exactly as it did — no advice, a fallback draft pick, an empty lesson, an empty narrative — so a timeout costs context rather than the battle. Draft retries also back off (0.5s × attempt) instead of hammering a backend that just failed
- fix(tournament): a **2-player double-elimination bracket could never produce a champion** (#279). With two players there are no losers-bracket rounds at all (`lb_rounds == 2 * (wb_rounds - 1) == 0`), but the WB round-1 loser was still routed to `LR1-1` — a match that is never built. `record_result_double` guards the drop with `if lb_m:`, so it silently no-opped: the grand final's slot 2 stayed empty forever, `get_pending_matches` never returned `GF`, and the tournament drained with `champion_seed` still `None`. The loser of the only WB match *is* the losers-bracket champion, so they now go straight to `GF` slot 2 and the existing bracket-reset logic gives them their second life correctly. The frontend also stops rendering an empty "LOSERS BRACKET" heading when there is no losers bracket to show

---

## [0.46.1] — 2026-09-16

- fix(battle): the **battle-history section** of the turn prompt (HP deltas, status applied/cured, items consumed, abilities revealed) was **dead code** — every lookup missed (#275). `_update_hp_snapshot` keyed each Pokémon by the raw poke-env species id (`"rotomwash"`), while `_build_recent_events` looked up the serialized `"species"` field, which is the Pokédex display name (`"Rotom-Wash"`). `prev_hp.get(key)` therefore returned `None` for every mon, and only the unconditional "Your action" / "Opponent used" lines ever reached the model — a silent loss of decision context in *every* battle, in both the singles and doubles builders. The snapshot now keys by the same name the serializer emits. `_species_name` is promoted to the public `species_name()` to mark it as the single source of truth for how a Pokémon is named anywhere a name is used as a lookup key. New tests drive the **real round trip with real `Pokemon` objects**, which is the only way to catch this: `species_name()` falls back to the raw id when the Pokédex lookup fails, and a `MagicMock` mon has no usable `gen`, so the existing mock-based tests saw both sides agree no matter what — the same faked-interface blind spot that hid the poke-env 0.16 breaks

---

## [0.46.0] — 2026-09-16

- feat(ratings): replace plain Elo with **Glicko-2** (#231) — every model now carries a rating deviation (RD) and volatility alongside its rating, so the leaderboard can distinguish a settled contender from a 3-0 newcomer. Ratings show their 95% confidence band and are tagged *provisional* until enough games narrow them; season standings replay with Glicko-2 too. Schema v20 adds `elo_ratings.rd`/`.volatility` and `elo_history.rd_before`/`.rd_after`; existing ratings carry over unchanged with RD backfilled to the unplayed-model prior. Validated against the worked example in Glickman's Glicko-2 paper
- fix(scripts): `scripts/leaderboard.py` crashed with `KeyError: 'prompt_version'` — it read a grouped leaderboard (which exposes `versions`) while printing a per-version column. Now requests the per-version rows it actually prints, and shows the confidence band
- chore(ci): the lint job now reads the ruff version from the `pyproject.toml` pin instead of hardcoding its own (#263) — CI had been linting with `ruff 0.11.13` while pyproject pinned `0.16.3` under a comment claiming they matched, so a green lint check was testing a linter nobody develops against. Guarded so an unreadable pin fails the job rather than silently linting with nothing
- chore(deps): reconcile the two dev dependency lists (#264) — `[dependency-groups] dev` now aliases the `[project.optional-dependencies] dev` extra instead of duplicating it. The lists had drifted (pytest/ruff in one, httpx2 in the other), and since `uv run` activates the group by default, tools missing from it were uninstalled out from under `uv run` — `uv run mypy` worked while `uv run ruff`/`uv run pytest` failed
- fix(deps): declare `httpx` as a runtime dependency (#264) — `api/routes.py` imports it at module scope but it was listed dev-only, resolving in production only because `anthropic`/`openai` pull it in transitively
- refactor(heuristics): split the 1205-line `battle/heuristics.py` into a `battle/heuristics/` package (#235) — one module per concern (`damage`, `type_chart`, `status`, `hazards`, `context`, `moves`, `switching`, `singles`, `doubles`) with downward-only dependencies; largest module is now 233 lines. Pure refactor: every block extracted verbatim, `__init__.py` re-exports the full former surface, so no caller or test changed and all 1124 tests pass unmodified
- fix(battle): `_StreamingMixin._send_challenges` called `Player.get_next_team()`, which **poke-env 0.16 removed** in favour of a `next_team` property — so every battle driven through the streaming path (`StreamingLLMPlayer`, the production one, plus `StreamingHumanPlayer` / `StreamingRandomBot`) raised `AttributeError` at challenge time. The unit tests missed it because `tests/test_streaming_player.py` declared its own `get_next_team` on the fake player, and the mixin's bare `get_next_team: Callable[...]` annotation created no attribute for mypy to complain about. Now reads `self.next_team`, the fake mirrors the real property, and a new integration test drives a `StreamingRandomBot` through a full battle against live Showdown — the coverage gap that let a removed poke-env method go unnoticed
- chore(deps): upgrade **poke-env 0.15.0 → 0.16.1** — 0.16 folded `Move.deduced_target` into `Move.target` and dropped that property's `SPECIAL_MOVES` short-circuit, so the `>=0.8.0,<0.16` guard pin is lifted deliberately rather than by a dependabot bump. **The upgrade was invisible to the test suite.** `heuristics/doubles.py` read `deduced_target` inside a bare `except Exception`, which swallowed the resulting `AttributeError` and degraded *every* doubles move to `"targeting": "auto"`: the model was no longer told `Choose target: "foe_1" or "foe_2"`, and `_score_move_doubles` stopped appending the `⚠ ALLY HIT` warning that the v7 doubles template renders, so a model could fire Earthquake into its own partner unwarned. All 1154 tests stayed green on the broken build, because `tests/test_doubles.py` set the attribute on an unspec'd `MagicMock`, which accepts any name (and `spec=Move` does not fix that — verified). The scorer now reads `move.target` directly with no exception swallow, and the doubles tests build **real `Move` objects** via `Move(id, gen=9)` so a future poke-env rename fails the suite loudly instead of degrading silently. Confirmed on live doubles battles against Showdown: the old code classified 291/291 observed moves as `auto`, the fix restores `choose_foe` / `spread_foes` / `choose_foe_or_ally` / `auto`
- fix(battle): `action_parser.py` no longer swallows target-resolution failures silently — it logs a warning before falling back to target 0. poke-env 0.16 added an `assert move.target is not None` inside `DoubleBattle.get_possible_showdown_targets`, which the bare `except Exception` would have converted into a foe-targeting move ordered with no target
- docs(deps): the dropped `SPECIAL_MOVES` branch is not a behaviour change for Nidozo. 0.16 lets `request_target` win where 0.15 short-circuited these moves to the entry target — but nothing in poke-env assigns `Move.request_target` (checked 0.15.0 and 0.16.1), so `Move.target` always resolves from static `gen9moves.json` data and the branch is unreachable in practice. Every value that can actually arrive lands on the same `"auto"` fallthrough in `_move_target_hint`, so the new and old paths agree
- fix(llm): a doubles battle state handed to a prompt version with **no doubles template** now fails loudly instead of rendering the singles template against it (#302). `serializer.py` emits a different shape for doubles (`my_active` is a *list* of per-slot dicts, `available_moves` a list of lists), so `PromptBuilder.build_turn` fell through to `turn.txt.jinja` and raised `UndefinedError: 'list object' has no attribute 'species'` from inside `choose_move` — a template variable named instead of the real problem, and `llm_player.py` calls `build_messages` outside the `try` that guards the backend call, so it propagated. It stayed invisible because `api/orchestration.py` silently rewrote the version to `v7` (the only version shipping `turn_doubles.txt.jinja`) at four separate call sites, while `StartBattleRequest` still accepted `doubles=true` with `prompt_version="v9"`. `build_turn` now raises a `ValueError` naming the version, the missing template and the version that works; `PromptBuilder.supports_doubles` exposes the capability; and the mapping lives in one place, `resolve_prompt_version()`, which orchestration now calls instead of repeating the override
- test(llm): doubles prompt coverage renders a **real serialized doubles state** against every prompt version (#302) — `v7` must render it (`SLOT 1`/`SLOT 2`, both sides' actives), every other version must refuse with the clear error rather than an `UndefinedError`, and a `prompts/v<N>/` directory added without being listed in the test's version list fails the suite

---

## [0.45.2] — 2026-06-30

- fix(deploy): remap Showdown's published host port from `8000` to `8001` in `docker-compose.yml` — `8000` is a common default for other self-hosted services and collided with an existing container on a shared host. The internal container port (and `NIDOZO_SHOWDOWN_PORT=8000` used between the `api` and `showdown` containers) is unchanged; only the host-published port moved
- docs: deploy guide now tells you to check for port collisions on shared hosts before bringing the stack up, and documents the leftover-`Created`-container case after a failed `docker compose up`

---

## [0.45.1] — 2026-06-21

- fix(scripts): make the `run_battle.py` and `tournament.py` CLI runners env-aware — they now honor `NIDOZO_SHOWDOWN_HOST`/`NIDOZO_SHOWDOWN_PORT` (mirroring the API) instead of hardcoding `LocalhostServerConfiguration`, so they can drive battles against a containerised or remote Showdown. Defaults to localhost:8000
- docs: add `docs/DEPLOYMENT.md` — a multi-machine deploy checklist + smoke test (server / LM-Studio-host / client), covering the LM Studio `0.0.0.0` bind, `.env` (token + `LM_STUDIO_BASE_URL`), ports/firewall, healthz, token entry, and troubleshooting

---

## [0.45.0] — 2026-06-21

- feat(battle): import a pasted Showdown team (#228) — new `p1_team`/`p2_team` on the battle request let a player battle with an exact pasted team (overrides draft, runs in Anything Goes so any well-formed team is accepted; mutually exclusive with a preset). The battle form gains a collapsible "📋 Paste team" textarea per player. Imported teams suit LLM/human players ("can a model pilot *my* team?")

---

## [0.44.1] — 2026-06-21

- fix(frontend): vendor the Pokémon Showdown client bundle (#232) — the live battle view's 14 renderer scripts + `battle.css` are now served from `frontend/public/showdown/` instead of fetched at runtime from `play.pokemonshowdown.com`, removing a third-party single-point-of-failure for the only battle view. The scene now renders fully offline (HP bars, layout, log); only Pokémon sprites/backgrounds still load from the CDN (cosmetic, graceful when unavailable). Licensing recorded in `public/showdown/NOTICE.md`

---

## [0.44.0] — 2026-06-21

- feat(api): optional rate limiting on the battle/tournament/season/experiment **start** endpoints (#233) — set `NIDOZO_RATE_LIMIT_PER_MIN` to cap requests per minute per client IP (fixed-window, in-process; returns `429` + `Retry-After`). `0`/unset disables it, so local dev is unchanged. Complements the shared-secret auth (#212) for exposed instances; `docker-compose` passes the var through
- test(orchestration): cover the draft, cancellation, and failure branches of the battle runners (#236) — `run_battles` draft/cancel/fail, tournament & season cancel-checks, bracket match-failure, and experiment fail/cancel paths, all with fake players (no Showdown). orchestration.py coverage 78% → 84%

---

## [0.43.0] — 2026-06-20

- feat(lessons): cross-battle lesson efficacy measurement (#227) — a `lessons_enabled` toggle on battle/tournament/season requests (and the battle form) suppresses lesson injection for a control cohort; persisted per battle (`battles.lessons_enabled`, schema v19). A new per-model **Lesson Efficacy** panel compares win-rate with lessons on vs off (`store.get_lesson_efficacy`), so the "models that learn" claim is finally measurable. Experiments are recorded as lessons-off

---

## [0.42.0] — 2026-06-20

- feat(experiments): bake-off harness (#226) — run a fixed N-battle head-to-head between two variants of the same base model (differing provider, model, or prompt version), with sides alternated to cancel first-move advantage. Results report win-rate **and** an exact two-sided binomial significance test so "11–9" reads as *not* significant. New `experiments` table + `battles.experiment_id` (schema v18); `run_experiment` runner reuses `_play_battle`; `POST /api/experiments/start` + list/detail/battles/cancel endpoints; a 🧪 BAKE-OFF tab and an Experiments view (win-rate bar, significance verdict, battle list). Lessons + post-battle generation are skipped during experiments so neither variant evolves mid-run

---

## [0.41.0] — 2026-06-20

- feat(cost): persist per-turn token usage + cost analytics (#225) — the OpenAI/Anthropic backends now expose `last_usage`; `LLMPlayer` records the per-turn total (player + coach) into new `turns.prompt_tokens`/`completion_tokens` columns (schema v17); `data/model_prices.json` (editable) drives USD estimates with local/random models counted free; `GET /api/stats/cost` returns per-model + total tokens and estimated cost; a "Token Usage & Cost" panel on the Global Stats page

---

## [0.40.0] — 2026-06-19

- feat(api): optional shared-secret authentication (#212) — when `NIDOZO_API_TOKEN` is set, every `/api/*` route and both WebSockets require the token (`Authorization: Bearer` / `X-API-Token` header, or `?token=` for WS); `/healthz` and the static SPA stay open. Auth is off by default with a startup warning. The web UI gains a 🔑 token field (stored in `localStorage`, auto-prompts on 401). `docker-compose` now passes `NIDOZO_API_TOKEN`, `LM_STUDIO_BASE_URL`/`MODEL`, and cloud keys through from the host env; README documents a multi-machine deployment
- refactor(orchestration): extract shared `_play_battle` / `_spawn_post_battle` helpers from the four battle runners — the winner/tag/turns idiom, finish→event→badge sequence, and lesson/narrative spawn now live in one place instead of four (#208). `battle_end` events now always carry `battle_tag` (previously only single battles did)
- test: backfill coverage for the six audit-flagged modules (#213–#218) — presets, achievements, human_player, streaming_player, ws_showdown, and orchestration; total project coverage 87% → 93%

---

## [0.39.6] — 2026-06-19

First wave of fixes from the v0.39 code-review audit:

- perf(orchestration): run `analyze_battle` off the event loop via `asyncio.to_thread` in the post-battle lesson/narrative tasks — it's CPU-bound over every turn and was stalling the loop that drives the live WS stream and the next battle in a tournament/season (#207)
- fix(db): add a `UNIQUE` index on `models(provider, model_name, prompt_version)` and make `get_or_create_model` catch `IntegrityError` + re-select — prevents duplicate model rows that would split a model's ELO/stats (#210)
- fix(db): add `idx_teams_model` to fresh-install DDL — it was previously created only by the v6 migration, so fresh databases never had it (#209)
- fix(draft): clamp draft pick count to `min(team_size, pool size)` to prevent an `IndexError` when `team_size` exceeds the tier pool (#211)
- chore(db): schema version 15 → 16

---

## [0.39.3] — 2026-06-19

- fix(tiers): remove 10 ND Uber/AG species from OU pool (`dragapult`, `landorus`, `urshifu`, `naganadel`, `magearna`, `alakazammega`, `gengarmega`, `salamencemega`, `metagrossmega`, `lucariomega`) — they are banned from `gen9nationaldex` and caused immediate team rejection at battle start
- fix(tiers): correct Showdown species IDs in OU pool (`tapu-koko`→`tapukoko`, `tapu-fini`→`tapufini`, `tapu-lele`→`tapulele`, `tapu-bulu`→`tapubulu`, `slowbrotrop`→`slowbrogalar`) and UU pool (`rotomcut`→`rotommow`)
- fix(tiers): add removed ND Uber species (`dragapult`, `landorus`, `urshifu`, `naganadel`, `magearna`) to UBERS pool so they remain available in NatDex AG battles
- test: regression guards against ND Uber species re-entering OU, wrong Showdown IDs in pools

---

## [0.39.2] — 2026-06-18

- fix(prompts): CLI runners (`run_battle.py`, `tournament.py`) now default to `v9` and accept `v1`–`v9`; previously capped at `v5`, so they couldn't run the current default prompt
- fix(prompts): add `v8`/`v9` to `_JSON_OUTPUT_PROMPT_VERSIONS` — grammar-sampled JSON mode now activates for the default prompt on the lmstudio/openai backends
- refactor: CLI scripts import the canonical JSON-version set instead of redefining a stale copy; test asserts the full set to prevent future drift

---

## [0.39.1] — 2026-06-18

- docs: full README revamp — refreshed Features (added seasons, brackets, human player, coach mode, personality profiles, configurable team size, theme toggle, containerisation; removed the retired Classic view), corrected the prompt-versions table (v1–v9 + coach, v9 default), updated project structure (vendored `showdown/`, `tournament/`, data files), and fixed test counts
- docs: CONTRIBUTING — corrected test count (1021), documented all six CI gates incl. the Docker smoke test, switched merge guidance to merge commits
- docs: CLAUDE.md — marked party presets / configurable team size / doubles / personality profiles / human mode / theme toggle / containerisation as shipped; aligned merge-strategy guidance with the git-workflow skill

---

## [0.39.0] — 2026-06-18

- feat(docker): `docker compose up` brings up the full stack — `showdown` (Node, vendored Pokémon Showdown v0.11.10) + `api` (multi-stage Vite SPA build served by FastAPI)
- feat(docker): env-driven Showdown addressing (`NIDOZO_SHOWDOWN_HOST`/`NIDOZO_SHOWDOWN_PORT`) replaces the hardcoded `LocalhostServerConfiguration`
- feat(docker): SQLite persists on the `nidozo-data` named volume across container restarts
- ci(docker): `docker-smoke` job builds both images, runs the stack, and polls `/healthz` to validate it end-to-end
- docs: Docker-first quickstart in the README; vendored `config-example.js` for fresh-clone/image defaults

---

## [0.34.0] — 2026-06-17

### Added
- **`GET /api/personalities`** — live persona list from backend (`slug`, `display_name`, `description`, `emoji`). `emoji` field added to `Personality` dataclass; backend is now the single source of truth.
- **`GET /api/stats/personalities`** — per-slug win/loss/tie counts and win-rate % across all completed battles; `store.get_personality_stats()` aggregates across both player slots via UNION ALL.
- **GlobalStats: Play Style Win Rates panel** — W/L/T segment bar + win-rate % per persona, fetched in parallel with global stats.
- **25 new tests**: `tests/test_personalities.py` (registry consistency, lookup edge cases) and 9 additions to `test_prompt_builder.py` (injection, ordering, all 5 personas parametrised).
- **`BattleForm`**: fetches `/api/personalities` on mount — hardcoded list removed. Selector shows selected persona description below the dropdown.

---

## [0.33.0] — 2026-06-17

### Changed
- **Classic view retired** — `BattleField.jsx` deleted; the Showdown cockpit is
  now the only live battle renderer. The battle-view toggle, its `localStorage`
  state, and `CLASSIC` / `SHOWDOWN` buttons are removed from `App.jsx`.
- **Showdown cockpit parity additions before retirement**: turn counter
  (`TURN N` / `READY`), weather badge, doubles `2v2` badge, and the Nidozo
  `BattleLog` (filterable structured action log) are now rendered inside the
  cockpit. BattleLog + PS protocol log sit in a new `.sbs-bottom` two-column
  grid at the foot of the scene.
- **`PokemonCard`**: `compact` prop removed (was doubles-only in Classic);
  `BenchSlot` unexported (internal use only).
- **CSS**: ~120 lines of Classic-only rules removed.

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

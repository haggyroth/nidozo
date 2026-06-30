# Deploying Nidozo (multi-machine)

A pre-flight checklist + smoke test for running Nidozo across three machines:

| Machine | Role | Runs |
|---|---|---|
| **utopiaplanitia** | server | the `docker compose` stack (`api` + `showdown`) |
| **enterprise-e** | LLM host | LM Studio, serving models over the network |
| **voyager** | client | a browser pointed at the server; local dev |

The stack is two containers (`showdown` on host port `:8001` → container `:8000`, `api`+SPA on `:5001`). The
`api` container reaches LM Studio on enterprise-e for moves and is viewed from
voyager's browser, gated by a shared-secret token.

> Replace the hostnames below with your real ones (or LAN IPs). `<server>` =
> utopiaplanitia, `<workstation>` = enterprise-e.

---

## 1. enterprise-e — LM Studio on the network

1. Load the model(s) you want to battle with.
2. Start LM Studio's **local server**, and set it to listen on **all interfaces
   (`0.0.0.0`)**, not just `127.0.0.1`. This is the #1 gotcha — by default the
   container on utopiaplanitia can't reach a localhost-only LM Studio.
3. Confirm the OpenAI-compatible endpoint is reachable **from the server**:
   ```bash
   # run this on utopiaplanitia
   curl -fsS http://<workstation>:1234/v1/models
   ```
   You should get a JSON list of loaded models. If it times out, it's a bind
   address or firewall issue (allow inbound TCP 1234 on enterprise-e).

---

## 2. utopiaplanitia — configure and bring up the stack

0. **On a shared/prod host, check for port collisions first:**
   ```bash
   docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E ':5001|:8001'
   ```
   If something else already publishes `5001` or `8001`, change the host-side
   port in `docker-compose.yml` (`"<new-port>:5001"` or `"<new-port>:8000"`)
   before starting — don't stop an unrelated container to free the port.
1. Clone (or pull) the repo and create a `.env` **next to `docker-compose.yml`**
   (Compose loads it automatically):
   ```bash
   NIDOZO_API_TOKEN=<a-long-random-secret>
   LM_STUDIO_BASE_URL=http://<workstation>:1234/v1
   # Optional:
   # NIDOZO_RATE_LIMIT_PER_MIN=30
   # ANTHROPIC_API_KEY=...
   # OPENAI_API_KEY=...
   ```
   Generate a token with e.g. `openssl rand -hex 24`.
2. Build + start:
   ```bash
   docker compose up -d --build      # first build takes a few minutes (Showdown compiles)
   ```
3. Watch it become healthy:
   ```bash
   docker compose ps                 # both services 'running'; api 'healthy'
   curl -fsS http://localhost:5001/healthz
   ```
   `/healthz` returns `{"status":"ok", ...}` only when the API can reach **both**
   its DB and the Showdown server. `degraded` (503) means Showdown isn't up yet —
   give it a few seconds, or check `docker compose logs showdown`.
4. Confirm auth is on (not the "DISABLED" warning):
   ```bash
   docker compose logs api | grep -i "authentication"
   # → "API authentication ENABLED — token required on /api/* and WebSockets."
   ```

### Ports & firewall
- `5001` (api+UI) must be reachable **from voyager** → allow inbound TCP 5001 on utopiaplanitia.
- `8001` (Showdown, host-side) only needs to be reachable from voyager if you want
  the raw cockpit spectator stream; the SPA proxies what it needs through `:5001`.
- `1234` on enterprise-e must be reachable **from utopiaplanitia**.

---

## 3. voyager — open the UI and run a smoke battle

1. Browse to `http://<server>:5001`. The page loads (static SPA is unauthenticated).
2. Click the **🔑** button in the header and paste the `NIDOZO_API_TOKEN`. (Data
   panels will 401 until you do — that auto-opens the token prompt.)
3. Start one battle to validate the whole path end-to-end:
   - **Sanity (no LLM):** P1 `random` vs P2 `random`, tier `random`, 1 battle →
     should complete in the cockpit. Proves Showdown + the live stream + auth.
   - **Real LLM:** P1 `lmstudio` (model id from enterprise-e) vs P2 `random` →
     proves the cross-machine LLM path. Watch the cockpit render moves.
4. Confirm it persisted: the leaderboard/recent-battles update, and **Global
   Stats → Token Usage & Cost** shows tokens for the LM Studio battle (0 cost,
   since local models are unpriced).

If the LLM battle hangs at "thinking", it's almost always `LM_STUDIO_BASE_URL`
not being reachable from the container — re-check step 1's `curl`.

---

## 4. CLI against the deployed stack (optional)

The CLI runners are env-aware (they honor `NIDOZO_SHOWDOWN_HOST`/`PORT`), so you
can drive battles/tournaments against the containerised Showdown — e.g. from
utopiaplanitia or any host that can reach it:

```bash
NIDOZO_SHOWDOWN_HOST=<server> NIDOZO_SHOWDOWN_PORT=8001 \
  uv run python scripts/run_battle.py --p1 random --p2 random --battles 1
```

(The CLI talks to Showdown directly and writes to its own DB; it does not go
through the authenticated API.)

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | Another container/process on the host already owns that port (`docker ps` to find it). Remap the host side in `docker-compose.yml` — don't stop someone else's service. Don't need to touch internal `NIDOZO_SHOWDOWN_PORT=8000`, only the published host port |
| `docker compose up` fails after a previous failed attempt | Leftover containers stuck in `Created` state from the failed run — `docker compose down` (or `docker rm <name>`) before retrying |
| `/healthz` is `degraded` | Showdown container not up yet, or crashed — `docker compose logs showdown` |
| Browser data panels all 401 | Token not entered (click 🔑) or wrong token |
| LLM battle stuck "thinking" | `LM_STUDIO_BASE_URL` unreachable from the container (bind/firewall) |
| "Your team was rejected" popup | A drafted/imported team violates the format's ban list (try tier `freeforall`/AG) |
| Sprites missing but HP bars fine | `play.pokemonshowdown.com` unreachable — cosmetic only, renderer is vendored |
| `429` on starting battles | `NIDOZO_RATE_LIMIT_PER_MIN` hit — wait, or raise/unset it |
| Startup log says auth "DISABLED" | `NIDOZO_API_TOKEN` not picked up — check the `.env` location/name |

To reset everything (wipes the SQLite volume): `docker compose down -v`.

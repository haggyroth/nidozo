/**
 * useShowdownBundle — loads the Pokémon Showdown battle renderer on first use
 * and reports readiness.
 *
 * OP-02 (#84) — Stage 2; vendored in #232.
 *
 * Vendored, not CDN
 * -----------------
 * The renderer's JS + CSS are vendored into `frontend/public/showdown/` (mirrors
 * the CDN's `js/`, `data/`, `style/` layout) and served from our own origin.
 * Previously these 14 scripts + stylesheet were fetched at runtime from
 * play.pokemonshowdown.com, making that third party a single point of failure for
 * the *only* battle view — if the CDN was down or changed a path, the live scene
 * broke. Vendoring removes that dependency so the scene renders fully offline.
 * See `frontend/public/showdown/NOTICE.md` for the source commit + licensing.
 *
 * Sprites / FX still load from the CDN (cosmetic): `Config.routes.client` points
 * there for Pokémon sprites and icons, and the vendored `battle.css` keeps its
 * background-image `url()`s absolute to the CDN. If the CDN is unavailable the
 * scene still renders (HP bars, layout, log) — only sprites/backgrounds degrade.
 *
 * Load order
 * ----------
 * These files define browser globals and depend on each other in strict order.
 * They cannot be bundled through Vite (global-scope assumptions break inside a
 * module context), so they are injected as plain <script> tags sequentially.
 */

import { useEffect, useState, useRef } from 'react'

// Local origin path where the vendored bundle is served (Vite copies
// `frontend/public/*` to the SPA root, and the Docker image ships it too).
const LOCAL = '/showdown'
// CDN host kept only for cosmetic sprite/icon assets (see Config stub below).
const CDN_HOST = 'play.pokemonshowdown.com'

// Showdown's own battle stylesheet (vendored). The renderer builds DOM
// (`.statbar`, `.hpbar`, sprite/scene divs) positioned and styled entirely by
// this file — without it HP bars vanish and the scene collapses into document
// flow. It is scoped to PS-specific class names, so it can't conflict with the
// app's own styles. `@import`s the vendored battle-log.css; its background
// `url()`s were rewritten to absolute CDN URLs (cosmetic, graceful if offline).
const STYLES = [
  `${LOCAL}/style/battle.css`,
]

// Showdown's battledata.js sets Dex.resourcePrefix = '//' + Config.routes.client,
// so routes.client must be the bare host (no protocol prefix). We keep it
// pointing at the CDN so Pokémon sprites/icons load from there (vendoring the
// full sprite set would add tens of MB). The vendored JS/CSS make the scene
// itself CDN-independent; only these cosmetic assets still need the network.
const CONFIG_STUB = `
window.Config = window.Config || {};
window.Config.routes = window.Config.routes || {};
window.Config.routes.client = '${CDN_HOST}/';
window.Config.routes.client2 = '${CDN_HOST}/';
window.Config.routes.dex = 'www.smogon.com/dex/';
`

// Vendored script paths in strict dependency order.
const SCRIPTS = [
  `${LOCAL}/js/lib/ps-polyfill.js`,
  `${LOCAL}/js/lib/jquery-1.11.0.min.js`,
  `${LOCAL}/js/lib/html-sanitizer-minified.js`,
  `${LOCAL}/js/battle-sound.js`,
  `${LOCAL}/js/battledata.js`,
  `${LOCAL}/data/pokedex-mini.js`,
  `${LOCAL}/data/pokedex-mini-bw.js`,
  `${LOCAL}/data/graphics.js`,
  // Full dex data (lazy — Dex falls back gracefully without them, but moves /
  // abilities / items won't have display names in the battle log).
  `${LOCAL}/data/pokedex.js`,
  `${LOCAL}/data/moves.js`,
  `${LOCAL}/data/abilities.js`,
  `${LOCAL}/data/items.js`,
  // Tooltips before Battle class (Battle references BattleTooltips).
  `${LOCAL}/js/battle-tooltips.js`,
  // Battle class must be last — depends on all of the above.
  `${LOCAL}/js/battle.js`,
]

/** Inject a single <script src> and resolve when loaded, skip if already present. */
function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) {
      resolve()
      return
    }
    const el = document.createElement('script')
    el.src = src
    el.onload = resolve
    el.onerror = () => reject(new Error(`Failed to load ${src}`))
    document.head.appendChild(el)
  })
}

/** Inject a <link rel="stylesheet">, idempotent by href; resolves on load. */
function loadStyle(href) {
  return new Promise((resolve) => {
    if (document.querySelector(`link[href="${href}"]`)) {
      resolve()
      return
    }
    const el = document.createElement('link')
    el.rel = 'stylesheet'
    el.href = href
    // Don't block bundle readiness on a stylesheet — resolve on load OR error
    // so a CDN hiccup degrades gracefully (unstyled scene) rather than hanging.
    el.onload = resolve
    el.onerror = resolve
    document.head.appendChild(el)
  })
}

/** Inject a <script> tag containing inline JS, idempotent by id. */
function inlineScript(id, code) {
  if (document.getElementById(id)) return
  const el = document.createElement('script')
  el.id = id
  el.textContent = code
  document.head.appendChild(el)
}

let _loadPromise = null   // singleton — only one load sequence at a time

function loadBundle() {
  if (_loadPromise) return _loadPromise
  _loadPromise = (async () => {
    // Stylesheet has no ordering dependency on the scripts — kick it off in
    // parallel so the scene is styled the moment the renderer paints.
    const stylesReady = Promise.all(STYLES.map(loadStyle))
    // Config stub must precede battledata.js.
    inlineScript('ps-config-stub', CONFIG_STUB)
    for (const src of SCRIPTS) {
      await loadScript(src)
    }
    await stylesReady
    if (typeof window.Battle !== 'function') {
      throw new Error('window.Battle not defined after bundle load — vendored bundle may be incomplete')
    }
  })()
  return _loadPromise
}

/**
 * Returns `{ ready, error }`.
 *  ready — true once window.Battle is available
 *  error — Error instance if the bundle failed to load, otherwise null
 *
 * The bundle is loaded at most once per page; subsequent calls reuse the
 * cached result immediately.
 */
export function useShowdownBundle() {
  const [ready, setReady] = useState(() => typeof window.Battle === 'function')
  const [error, setError] = useState(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    // If already loaded (e.g. bundle was injected by a prior mount), the
    // useState initializer already set ready=true — no synchronous setState needed.
    if (typeof window.Battle === 'function') return
    loadBundle()
      .then(() => { if (mounted.current) setReady(true) })
      .catch(err => { if (mounted.current) setError(err) })
    return () => { mounted.current = false }
  }, [])

  return { ready, error }
}

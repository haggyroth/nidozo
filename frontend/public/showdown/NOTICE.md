# Vendored Pokémon Showdown client bundle

These files are the compiled battle-renderer assets from **Pokémon Showdown**
([smogon/pokemon-showdown-client](https://github.com/smogon/pokemon-showdown-client)),
vendored here so Nidozo's live battle view does not depend on
`play.pokemonshowdown.com` at runtime (#232).

- **Source:** `https://play.pokemonshowdown.com/` (`js/`, `data/`, `style/`)
- **Fetched:** 2026-06-21
- **License:** MIT (Pokémon Showdown client). See
  <https://github.com/smogon/pokemon-showdown-client/blob/master/LICENSE>.

## Layout (mirrors the CDN)

```
js/lib/ps-polyfill.js, jquery-1.11.0.min.js, html-sanitizer-minified.js
js/battle-sound.js, battledata.js, battle-tooltips.js, battle.js
data/pokedex-mini.js, pokedex-mini-bw.js, graphics.js,
     pokedex.js, moves.js, abilities.js, items.js
style/battle.css, battle-log.css
```

## Local modifications

- `style/battle.css`: the cosmetic `url(../fx/…)` background references were
  rewritten to absolute `https://play.pokemonshowdown.com/fx/…` URLs. The core
  renderer (HP bars, layout, log) is fully local; only Pokémon **sprites/icons**
  (via `Config.routes.client`) and these CSS **backgrounds** still load from the
  CDN, and the scene degrades gracefully without them.

## Updating

Re-download the same paths from the CDN into this directory, then re-apply the
`../fx/` → absolute-CDN rewrite in `style/battle.css`. Keep the load order in
`frontend/src/hooks/useShowdownBundle.js` in sync if the file set changes.

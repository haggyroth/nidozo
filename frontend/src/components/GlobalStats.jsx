import { useEffect, useReducer, useState } from 'react'
import EmptyState from './EmptyState'

// ---------------------------------------------------------------------------
// Sprite helper (same CDN as PokemonCard)
// ---------------------------------------------------------------------------

function spriteUrl(species) {
  if (!species) return null
  const id = species.toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
  return `https://play.pokemonshowdown.com/sprites/home/${id}.png`
}

// ---------------------------------------------------------------------------
// Tier label map
// ---------------------------------------------------------------------------

const TIER_LABELS = {
  random: 'RANDOM', ou: 'OU', ubers: 'UBERS',
  uu: 'UU', nu: 'NU', lc: 'LC', freeforall: 'FFA',
}

const TIER_COLORS = {
  random: '#4d9de0', ou: '#f7c948', ubers: '#e53935',
  uu: '#ab47bc', nu: '#4caf50', lc: '#80deea', freeforall: '#ff9800',
}

// ---------------------------------------------------------------------------
// Summary KPIs
// ---------------------------------------------------------------------------

function SummaryKpis({ summary }) {
  const { total_battles, avg_turns, decided_battles, total_models } = summary
  const decisive_pct = total_battles > 0
    ? Math.round((decided_battles / total_battles) * 100)
    : 0

  return (
    <div className="gs-kpi-row">
      <div className="gs-kpi">
        <div className="gs-kpi-value">{total_battles}</div>
        <div className="gs-kpi-label">BATTLES PLAYED</div>
      </div>
      <div className="gs-kpi">
        <div className="gs-kpi-value">{total_models}</div>
        <div className="gs-kpi-label">MODELS REGISTERED</div>
      </div>
      <div className="gs-kpi">
        <div className="gs-kpi-value">{avg_turns ?? '—'}</div>
        <div className="gs-kpi-label">AVG TURNS / BATTLE</div>
      </div>
      <div className="gs-kpi">
        <div className="gs-kpi-value">{decisive_pct}%</div>
        <div className="gs-kpi-label">DECISIVE RESULTS</div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Battles by tier — horizontal bar chart + avg turns column
// ---------------------------------------------------------------------------

function TierBreakdown({ battles_by_tier }) {
  if (!battles_by_tier?.length) return <EmptyState compact icon="📊" title="No battles yet" hint="Run battles to see tier breakdown." />
  const max = Math.max(...battles_by_tier.map(r => r.cnt))
  return (
    <div className="gs-tier-chart">
      {battles_by_tier.map(r => {
        const pct = Math.round((r.cnt / max) * 100)
        const color = TIER_COLORS[r.tier] ?? '#666'
        return (
          <div key={r.tier} className="gs-tier-row">
            <span className="gs-tier-label" style={{ color }}>{TIER_LABELS[r.tier] ?? r.tier.toUpperCase()}</span>
            <div className="gs-tier-track">
              <div className="gs-tier-bar" style={{ width: `${pct}%`, background: color }} />
            </div>
            <span className="gs-tier-count">{r.cnt}</span>
            <span className="gs-tier-avg" title="avg turns">{r.avg_turns != null ? `${r.avg_turns}t` : '—'}</span>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Type usage heatmap — 18 coloured cells, intensity by count
// ---------------------------------------------------------------------------

const TYPE_COLORS = {
  normal: '#A8A878', fire: '#F08030', water: '#6890F0', electric: '#F8D030',
  grass: '#78C850', ice: '#98D8D8', fighting: '#C03028', poison: '#A040A0',
  ground: '#E0C068', flying: '#A890F0', psychic: '#F85888', bug: '#A8B820',
  rock: '#B8A038', ghost: '#705898', dragon: '#7038F8', dark: '#705848',
  steel: '#B8B8D0', fairy: '#EE99AC',
}

const ALL_TYPES = Object.keys(TYPE_COLORS)

function TypeHeatmap({ type_usage }) {
  if (!type_usage?.length) return (
    <EmptyState compact icon="🎨" title="No type data yet" hint="Type usage appears once battles are played." />
  )
  const byType = Object.fromEntries(type_usage.map(r => [r.type, r.cnt]))
  const max = Math.max(...type_usage.map(r => r.cnt), 1)
  return (
    <div className="gs-type-grid">
      {ALL_TYPES.map(t => {
        const cnt = byType[t] ?? 0
        const alpha = cnt > 0 ? 0.2 + (cnt / max) * 0.75 : 0.08
        const color = TYPE_COLORS[t]
        return (
          <div
            key={t}
            className="gs-type-cell"
            style={{ background: `${color}${cnt > 0 ? '' : '22'}`, opacity: cnt > 0 ? 1 : 0.4 }}
            title={`${t}: ${cnt} turns`}
          >
            <span className="gs-type-name">{t.toUpperCase()}</span>
            <span className="gs-type-cnt">{cnt > 0 ? cnt : '—'}</span>
            {cnt > 0 && (
              <div
                className="gs-type-intensity"
                style={{ height: `${Math.round(alpha * 100)}%`, background: color }}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Top Pokémon grid — sprite + usage bar
// ---------------------------------------------------------------------------

function TopPokemon({ top_pokemon }) {
  if (!top_pokemon?.length) return <EmptyState compact icon="◓" title="No turn data yet" hint="Top Pokémon appear once battles are played." />
  const max = top_pokemon[0]?.cnt ?? 1
  return (
    <div className="gs-pokemon-grid">
      {top_pokemon.map((r, i) => {
        const url = spriteUrl(r.species)
        const pct = Math.round((r.cnt / max) * 100)
        return (
          <div key={r.species} className="gs-pokemon-card">
            <span className="gs-pokemon-rank">#{i + 1}</span>
            {url && (
              <img
                src={url}
                alt={r.species}
                className="gs-pokemon-sprite"
                onError={e => { e.currentTarget.style.display = 'none' }}
                style={{ imageRendering: 'pixelated' }}
              />
            )}
            <div className="gs-pokemon-name">{r.species}</div>
            <div className="gs-pokemon-track">
              <div className="gs-pokemon-bar" style={{ width: `${pct}%` }} />
            </div>
            <div className="gs-pokemon-cnt">{r.cnt} turns</div>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Top moves — ranked list
// ---------------------------------------------------------------------------

function TopMoves({ top_moves }) {
  if (!top_moves?.length) return <EmptyState compact icon="⚔" title="No move data yet" hint="Most-used moves appear once battles are played." />
  const max = top_moves[0]?.cnt ?? 1
  return (
    <div className="gs-move-list">
      {top_moves.map((r, i) => {
        const pct = Math.round((r.cnt / max) * 100)
        return (
          <div key={r.move ?? i} className="gs-move-row">
            <span className="gs-move-rank">#{i + 1}</span>
            <span className="gs-move-name">{(r.move ?? 'unknown').replace(/_/g, ' ')}</span>
            <div className="gs-move-track">
              <div className="gs-move-bar" style={{ width: `${pct}%` }} />
            </div>
            <span className="gs-move-cnt">{r.cnt}</span>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Personality win-rate breakdown
// ---------------------------------------------------------------------------

const PERSONALITY_META = {
  aggressive: { emoji: '⚔️', label: 'All-out Attacker' },
  defensive:  { emoji: '🛡️', label: 'Bulwark' },
  balanced:   { emoji: '⚖️', label: 'Adaptive' },
  trickster:  { emoji: '🎭', label: 'Mindgame Specialist' },
  momentum:   { emoji: '💨', label: 'Tempo Player' },
}

function PersonalityStats({ personality_stats }) {
  if (!personality_stats?.length) return (
    <EmptyState compact icon="🎭" title="No personality data yet" hint="Run battles with a play style set to see win-rate breakdowns." />
  )
  return (
    <div className="gs-personality-list">
      {personality_stats.map(r => {
        const meta = PERSONALITY_META[r.personality] ?? { emoji: '❓', label: r.personality }
        const winPct  = r.total > 0 ? Math.round((r.wins  / r.total) * 100) : 0
        const lossPct = r.total > 0 ? Math.round((r.losses / r.total) * 100) : 0
        const tiePct  = r.total > 0 ? Math.round((r.ties  / r.total) * 100) : 0
        return (
          <div key={r.personality} className="gs-personality-row">
            <span className="gs-personality-emoji">{meta.emoji}</span>
            <div className="gs-personality-info">
              <span className="gs-personality-name">{meta.label}</span>
              <div className="gs-personality-bar-wrap">
                <div className="gs-personality-segment gs-pseg-win"  style={{ width: `${winPct}%`  }} title={`${r.wins} wins`} />
                <div className="gs-personality-segment gs-pseg-loss" style={{ width: `${lossPct}%` }} title={`${r.losses} losses`} />
                <div className="gs-personality-segment gs-pseg-tie"  style={{ width: `${tiePct}%`  }} title={`${r.ties} ties`} />
              </div>
              <span className="gs-personality-rate">
                {r.win_rate != null ? `${r.win_rate}% WR` : '—'}
              </span>
            </div>
            <span className="gs-personality-total">{r.total} battles</span>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Recent battles feed
// ---------------------------------------------------------------------------

function RecentBattles({ recent_battles, onReplaySelected }) {
  if (!recent_battles?.length) return <EmptyState compact icon="🕒" title="No battles yet" hint="Recent battles will appear here." />
  return (
    <div className="gs-recent-list">
      {recent_battles.map(b => {
        const result = b.winner === 1 ? 'p1' : b.winner === 2 ? 'p2' : 'tie'
        const tierColor = TIER_COLORS[b.tier] ?? '#666'
        const tierLabel = TIER_LABELS[b.tier] ?? b.tier?.toUpperCase()
        return (
          <div key={b.id} className="gs-recent-row">
            <span className="gs-recent-tier" style={{ color: tierColor }}>{tierLabel}</span>
            <div className="gs-recent-matchup">
              <span className={result === 'p1' ? 'gs-winner' : ''}>{b.p1?.split('/').pop()}</span>
              <span className="gs-vs">vs</span>
              <span className={result === 'p2' ? 'gs-winner' : ''}>{b.p2?.split('/').pop()}</span>
            </div>
            <span className="gs-recent-turns">{b.total_turns ?? '?'}t</span>
            <span className="gs-recent-date">
              {b.finished_at ? new Date(b.finished_at).toLocaleDateString() : '—'}
            </span>
            {onReplaySelected && (
              <button
                className="btn-replay btn-replay-sm"
                onClick={() => onReplaySelected(b.id)}
                title="Watch replay"
              >▶</button>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Fetch reducer
// ---------------------------------------------------------------------------

function reducer(state, action) {
  switch (action.type) {
    case 'start':   return { loading: true,  error: null,         data: null }
    case 'success': return { loading: false, error: null,         data: action.data }
    case 'error':   return { loading: false, error: action.error, data: null }
    default:        return state
  }
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function GlobalStats({ onClose, onReplaySelected }) {
  const [{ loading, error, data }, dispatch] = useReducer(
    reducer,
    { loading: true, error: null, data: null },
  )
  const [personalityStats, setPersonalityStats] = useState(null)

  useEffect(() => {
    let cancelled = false
    dispatch({ type: 'start' })
    Promise.all([
      fetch('/api/stats/global').then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() }),
      fetch('/api/stats/personalities').then(r => r.ok ? r.json() : []).catch(() => []),
    ])
      .then(([global, personalities]) => {
        if (!cancelled) {
          dispatch({ type: 'success', data: global })
          setPersonalityStats(personalities)
        }
      })
      .catch(e => { if (!cancelled) dispatch({ type: 'error', error: e.message }) })
    return () => { cancelled = true }
  }, [])

  if (loading) return (
    <div className="stats-page">
      <div className="stats-loading">Loading global stats…</div>
    </div>
  )
  if (error || !data) return (
    <div className="stats-page">
      <button className="stats-back" onClick={onClose}>← BACK</button>
      <div className="stats-error">Failed to load stats: {error}</div>
    </div>
  )

  const { summary, battles_by_tier, type_usage, top_pokemon, top_moves, recent_battles } = data

  return (
    <div className="stats-page">
      <div className="stats-header">
        <button className="stats-back" onClick={onClose}>← BACK</button>
        <div className="stats-identity">
          <div className="stats-model-name">GLOBAL STATS</div>
          <div className="stats-provider-row">
            <span className="provider-tag">all models · all battles</span>
          </div>
        </div>
      </div>

      {/* Summary KPIs */}
      <div className="panel stats-panel gs-summary-panel">
        <SummaryKpis summary={summary} />
      </div>

      {/* Two-column: Tier breakdown + Recent battles */}
      <div className="stats-grid">
        <div className="panel stats-panel">
          <div className="panel-title">BATTLES BY TIER</div>
          <TierBreakdown battles_by_tier={battles_by_tier} />
        </div>
        <div className="panel stats-panel">
          <div className="panel-title">RECENT BATTLES</div>
          <RecentBattles recent_battles={recent_battles} onReplaySelected={onReplaySelected} />
        </div>
      </div>

      {/* Type heatmap */}
      <div className="panel stats-panel">
        <div className="panel-title">
          TYPE USAGE
          <span className="panel-subtitle">how often each type appears as the active Pokémon's type</span>
        </div>
        <TypeHeatmap type_usage={type_usage} />
      </div>

      {/* Top Pokémon */}
      <div className="panel stats-panel">
        <div className="panel-title">
          TOP POKÉMON
          <span className="panel-subtitle">by active turns across all battles</span>
        </div>
        <TopPokemon top_pokemon={top_pokemon} />
      </div>

      {/* Top Moves */}
      <div className="panel stats-panel">
        <div className="panel-title">
          TOP MOVES
          <span className="panel-subtitle">most chosen across all models</span>
        </div>
        <TopMoves top_moves={top_moves} />
      </div>

      {/* Personality win-rate breakdown */}
      <div className="panel stats-panel">
        <div className="panel-title">
          PLAY STYLE WIN RATES
          <span className="panel-subtitle">battles where that personality was active (as either player)</span>
        </div>
        <PersonalityStats personality_stats={personalityStats} />
      </div>
    </div>
  )
}

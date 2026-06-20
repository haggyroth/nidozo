import { useState, useReducer, useEffect, useCallback } from 'react'
import EmptyState from './EmptyState'

// ---------------------------------------------------------------------------
// ELO Sparkline — pure SVG, no external deps
// ---------------------------------------------------------------------------

function EloSparkline({ history }) {
  if (!history || history.length < 2) {
    return (
      <div className="sparkline-empty">Not enough data for ELO chart</div>
    )
  }

  const W = 560
  const H = 90
  const PAD = { top: 12, right: 16, bottom: 20, left: 48 }
  const innerW = W - PAD.left - PAD.right
  const innerH = H - PAD.top - PAD.bottom

  const ratings = history.map(h => h.rating_after)
  const minR = Math.min(...ratings)
  const maxR = Math.max(...ratings)
  const range = maxR - minR || 1

  const xOf = i => PAD.left + (i / (history.length - 1)) * innerW
  const yOf = r => PAD.top + innerH - ((r - minR) / range) * innerH

  const pts = history.map((h, i) => `${xOf(i)},${yOf(h.rating_after)}`).join(' ')
  const areaPts = [
    `${PAD.left},${PAD.top + innerH}`,
    ...history.map((h, i) => `${xOf(i)},${yOf(h.rating_after)}`),
    `${xOf(history.length - 1)},${PAD.top + innerH}`,
  ].join(' ')

  // Y-axis labels
  const yTicks = [minR, (minR + maxR) / 2, maxR]

  const lastRating = ratings[ratings.length - 1]
  const firstRating = ratings[0]
  const delta = lastRating - firstRating
  const deltaSign = delta >= 0 ? '+' : ''
  const deltaClass = delta >= 0 ? 'elo-delta-up' : 'elo-delta-down'

  return (
    <div className="sparkline-wrap">
      <div className="sparkline-header">
        <span className="sparkline-label">ELO PROGRESSION</span>
        <span className={`sparkline-delta ${deltaClass}`}>
          {deltaSign}{delta.toFixed(1)} over {history.length} battles
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="sparkline-svg"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent-cyan)" stopOpacity="0.25" />
            <stop offset="100%" stopColor="var(--accent-cyan)" stopOpacity="0.01" />
          </linearGradient>
        </defs>

        {/* Grid lines */}
        {yTicks.map((r, i) => (
          <g key={i}>
            <line
              x1={PAD.left} y1={yOf(r)}
              x2={PAD.left + innerW} y2={yOf(r)}
              stroke="var(--border)" strokeWidth="1" strokeDasharray="3,4"
            />
            <text
              x={PAD.left - 6} y={yOf(r) + 4}
              textAnchor="end"
              fontSize="9"
              fill="var(--text-muted)"
              fontFamily="var(--font-mono)"
            >
              {Math.round(r)}
            </text>
          </g>
        ))}

        {/* Area fill */}
        <polygon points={areaPts} fill="url(#sparkGrad)" />

        {/* Line */}
        <polyline
          points={pts}
          fill="none"
          stroke="var(--accent-cyan)"
          strokeWidth="1.8"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Data points */}
        {history.map((h, i) => (
          <circle
            key={i}
            cx={xOf(i)} cy={yOf(h.rating_after)}
            r="3"
            fill={h.delta >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}
            stroke="var(--bg-panel)"
            strokeWidth="1.5"
          />
        ))}

        {/* Start / end labels */}
        <text
          x={xOf(0)} y={yOf(firstRating) - 7}
          textAnchor="middle" fontSize="9"
          fill="var(--text-dim)" fontFamily="var(--font-mono)"
        >
          {firstRating.toFixed(0)}
        </text>
        <text
          x={xOf(history.length - 1)} y={yOf(lastRating) - 7}
          textAnchor="middle" fontSize="9"
          fill="var(--accent-cyan)" fontFamily="var(--font-mono)"
          fontWeight="600"
        >
          {lastRating.toFixed(1)}
        </text>
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Decision quality bar
// ---------------------------------------------------------------------------

function QualityBar({ turnStats }) {
  if (!turnStats || turnStats.total_turns === 0) {
    return <div className="quality-bar-empty">No turn data recorded</div>
  }

  const { total_turns, parse_ok, parse_fail, parse_success_rate } = turnStats

  return (
    <div className="quality-section">
      <div className="quality-row">
        <span className="quality-label">Total turns logged</span>
        <span className="quality-value">{total_turns}</span>
      </div>
      <div className="quality-row">
        <span className="quality-label">Parse success rate</span>
        <span className="quality-value" style={{
          color: parse_success_rate >= 90 ? 'var(--accent-green)'
               : parse_success_rate >= 70 ? 'var(--accent-amber)'
               : 'var(--accent-red)'
        }}>
          {parse_success_rate != null ? `${parse_success_rate}%` : '—'}
        </span>
      </div>
      <div className="quality-row">
        <span className="quality-label">Valid actions / parse failures</span>
        <span className="quality-value">
          <span className="w">{parse_ok}</span>
          {' / '}
          <span className="l">{parse_fail}</span>
        </span>
      </div>

      {/* Stacked bar */}
      <div className="parse-bar-wrap">
        <div
          className="parse-bar-ok"
          style={{ width: `${parse_success_rate ?? 0}%` }}
          title={`${parse_ok} valid`}
        />
        <div
          className="parse-bar-fail"
          style={{ width: `${parse_fail / total_turns * 100}%` }}
          title={`${parse_fail} fallback`}
        />
      </div>
      <div className="parse-bar-legend">
        <span className="parse-legend-ok">■ valid action</span>
        <span className="parse-legend-fail">■ fallback (random)</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Battle history table
// ---------------------------------------------------------------------------

function BattleHistoryTable({ battles, onReplaySelected }) {
  if (!battles || battles.length === 0) {
    return <div className="empty-state">No completed battles yet</div>
  }

  return (
    <table className="stats-battle-table">
      <thead>
        <tr>
          <th>RESULT</th>
          <th>OPPONENT</th>
          <th>TURNS</th>
          <th>DATE</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {battles.map(b => (
          <tr key={b.id} className={`battle-row-${b.result}`}>
            <td>
              <span className={`result-badge result-${b.result}`}>
                {b.result.toUpperCase()}
              </span>
            </td>
            <td className="opponent-cell">{b.opponent}</td>
            <td className="turns-cell">{b.total_turns ?? '?'}</td>
            <td className="date-cell">
              {b.finished_at
                ? new Date(b.finished_at).toLocaleDateString()
                : '—'}
            </td>
            <td>
              <button
                className="btn-replay btn-replay-sm"
                onClick={() => onReplaySelected?.(b.id)}
                title="Watch replay"
              >
                ▶
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Lessons log
// ---------------------------------------------------------------------------

function LessonsLog({ lessons }) {
  const [expanded, setExpanded] = useState(null)

  if (!lessons || lessons.length === 0) {
    return (
      <div className="lessons-empty">
        No lessons generated yet — lessons appear after battles with LLM players.
      </div>
    )
  }

  return (
    <ol className="lessons-list">
      {lessons.map((lesson, i) => (
        <li
          key={lesson.id}
          className={`lesson-item ${expanded === i ? 'expanded' : ''}`}
          onClick={() => setExpanded(expanded === i ? null : i)}
        >
          <div className="lesson-header">
            <span className="lesson-num">#{lessons.length - i}</span>
            <span className="lesson-date">
              {lesson.created_at
                ? new Date(lesson.created_at).toLocaleDateString()
                : ''}
            </span>
            <span className="lesson-expand">{expanded === i ? '▲' : '▼'}</span>
          </div>
          {expanded === i && (
            <div className="lesson-body">{lesson.content}</div>
          )}
          {expanded !== i && (
            <div className="lesson-preview">
              {lesson.content.slice(0, 100)}{lesson.content.length > 100 ? '…' : ''}
            </div>
          )}
        </li>
      ))}
    </ol>
  )
}

// ---------------------------------------------------------------------------
// Usage stats sub-components
// ---------------------------------------------------------------------------

function spriteUrl(species) {
  if (!species) return null
  const id = species.toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
  return `https://play.pokemonshowdown.com/sprites/home/${id}.png`
}

function UsagePokemon({ rows }) {
  if (!rows.length) return <div className="gs-empty">No turn data yet.</div>
  const max = rows[0]?.cnt ?? 1
  return (
    <div className="usage-pokemon-list">
      {rows.map((r, i) => {
        const pct = Math.round((r.cnt / max) * 100)
        const url = spriteUrl(r.species)
        return (
          <div key={r.species} className="usage-pokemon-row">
            <span className="usage-rank">#{i + 1}</span>
            {url && (
              <img src={url} alt={r.species} className="usage-sprite"
                onError={e => { e.currentTarget.style.display = 'none' }}
                style={{ imageRendering: 'pixelated' }} />
            )}
            <span className="usage-species">{r.species}</span>
            <div className="usage-track">
              <div className="usage-bar usage-bar--pokemon" style={{ width: `${pct}%` }} />
            </div>
            <span className="usage-cnt">{r.cnt}</span>
          </div>
        )
      })}
    </div>
  )
}

function UsageMoves({ rows }) {
  if (!rows.length) return <div className="gs-empty">No move data yet.</div>
  const max = rows[0]?.cnt ?? 1
  return (
    <div className="usage-move-list">
      {rows.map((r, i) => {
        const pct = Math.round((r.cnt / max) * 100)
        return (
          <div key={r.move ?? i} className="usage-move-row">
            <span className="usage-rank">#{i + 1}</span>
            <span className="usage-move-name">{(r.move ?? 'unknown').replace(/_/g, ' ')}</span>
            <div className="usage-track">
              <div className="usage-bar usage-bar--move" style={{ width: `${pct}%` }} />
            </div>
            <span className="usage-cnt">{r.cnt}</span>
          </div>
        )
      })}
    </div>
  )
}

const ACTION_COLORS = { move: '#42a5f5', switch: '#4caf50', fallback: '#e53935' }
const ACTION_LABELS = { move: 'MOVE', switch: 'SWITCH', fallback: 'FALLBACK' }

function ActionDistribution({ rows }) {
  if (!rows.length) return <div className="gs-empty">No turn data yet.</div>
  const total = rows.reduce((s, r) => s + r.cnt, 0)
  return (
    <div className="action-dist">
      <div className="action-dist-bar">
        {rows.map(r => {
          const pct = total > 0 ? (r.cnt / total) * 100 : 0
          const color = ACTION_COLORS[r.action_type] ?? '#888'
          return (
            <div
              key={r.action_type}
              className="action-dist-segment"
              style={{ width: `${pct}%`, background: color }}
              title={`${ACTION_LABELS[r.action_type] ?? r.action_type}: ${r.cnt} (${pct.toFixed(1)}%)`}
            />
          )
        })}
      </div>
      <div className="action-dist-legend">
        {rows.map(r => {
          const pct = total > 0 ? ((r.cnt / total) * 100).toFixed(1) : '0.0'
          const color = ACTION_COLORS[r.action_type] ?? '#888'
          return (
            <div key={r.action_type} className="action-legend-item">
              <span className="action-legend-dot" style={{ background: color }} />
              <span className="action-legend-label">{ACTION_LABELS[r.action_type] ?? r.action_type}</span>
              <span className="action-legend-pct">{pct}%</span>
              <span className="action-legend-cnt">({r.cnt})</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const TIER_COLORS = {
  random: '#4d9de0', ou: '#f7c948', ubers: '#e53935',
  uu: '#ab47bc', nu: '#4caf50', lc: '#80deea', freeforall: '#ff9800',
}
const TIER_LABELS = {
  random: 'RANDOM', ou: 'OU', ubers: 'UBERS',
  uu: 'UU', nu: 'NU', lc: 'LC', freeforall: 'FFA',
}

function WinRateByTier({ rows }) {
  if (!rows.length) return <div className="gs-empty">No battles by tier yet.</div>
  return (
    <div className="tier-winrate-list">
      {rows.map(r => {
        const winPct = r.total > 0 ? Math.round((r.wins / r.total) * 100) : 0
        const color = TIER_COLORS[r.tier] ?? '#666'
        const label = TIER_LABELS[r.tier] ?? r.tier?.toUpperCase()
        return (
          <div key={r.tier} className="tier-wr-row">
            <span className="tier-wr-label" style={{ color }}>{label}</span>
            <div className="tier-wr-track">
              <div className="tier-wr-bar" style={{ width: `${winPct}%`, background: color }} />
            </div>
            <span className="tier-wr-pct" style={{ color }}>{winPct}%</span>
            <span className="tier-wr-record">{r.wins}W / {r.total - r.wins}L</span>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Head-to-head table (inline on per-model page)
// ---------------------------------------------------------------------------

function ModelH2H({ modelId }) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    if (rows !== null) return  // already fetched
    setLoading(true)
    fetch(`/api/models/${modelId}/matchups`)
      .then(r => r.json())
      .then(data => { setRows(data); setLoading(false) })
      .catch(() => { setRows([]); setLoading(false) })
  }, [modelId, rows])

  function toggle() {
    if (!open) load()
    setOpen(o => !o)
  }

  return (
    <div className="ms-h2h">
      <button className="heuristic-toggle" onClick={toggle}>
        <span>⚔ HEAD-TO-HEAD RECORD</span>
        <span className={`drawer-chevron ${open ? 'open' : ''}`}>▼</span>
      </button>
      {open && (
        loading ? (
          <div className="ms-h2h-loading">Loading…</div>
        ) : !rows?.length ? (
          <div className="ms-h2h-empty">No head-to-head data yet — battle against other models to populate this.</div>
        ) : (
          <table className="ms-h2h-table">
            <thead>
              <tr>
                <th>OPPONENT</th>
                <th>W</th><th>L</th><th>T</th><th>GAMES</th><th>WIN%</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const winPct = r.games > 0 ? Math.round((r.wins / r.games) * 100) : 0
                const oppName = `${r.opp_provider}/${r.opp_model}`.split('/').slice(-1)[0]
                return (
                  <tr key={`${r.opp_provider}/${r.opp_model}`}>
                    <td className="ms-h2h-opp" title={`${r.opp_provider}/${r.opp_model}`}>{oppName}</td>
                    <td className="ms-h2h-w">{r.wins}</td>
                    <td className="ms-h2h-l">{r.losses}</td>
                    <td className="ms-h2h-t">{r.ties}</td>
                    <td className="ms-h2h-g">{r.games}</td>
                    <td className="ms-h2h-pct" style={{
                      color: winPct >= 60 ? 'var(--accent-green)'
                           : winPct >= 40 ? 'var(--accent-amber)'
                           : 'var(--accent-red)'
                    }}>{winPct}%</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Badge gallery
// ---------------------------------------------------------------------------

function BadgeGallery({ modelId }) {
  const [badges, setBadges] = useState(null)

  useEffect(() => {
    if (modelId == null) return
    fetch(`/api/models/${modelId}/badges`)
      .then(r => r.json())
      .then(setBadges)
      .catch(() => setBadges([]))
  }, [modelId])

  if (badges === null) return null
  if (!badges.length) return (
    <EmptyState compact icon="🏅" title="No badges yet"
      hint="Earn badges by winning battles, going on streaks, and hitting milestones." />
  )

  return (
    <div className="badge-gallery">
      {badges.map(b => (
        <div key={b.slug} className="badge-card" title={b.description}>
          <div className="badge-emoji">{b.emoji}</div>
          <div className="badge-name">{b.name}</div>
          <div className="badge-desc">{b.description}</div>
          <div className="badge-date">
            {b.earned_at ? new Date(b.earned_at).toLocaleDateString() : ''}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Export helpers
// ---------------------------------------------------------------------------

function exportBattleHistoryCSV(modelName, battles) {
  const header = 'battle_id,result,opponent,turns,role,date'
  const rows = battles.map(b =>
    [b.id, b.result, `"${b.opponent}"`, b.total_turns ?? '', b.my_role, b.finished_at ?? ''].join(',')
  )
  const blob = new Blob([[header, ...rows].join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${modelName.replace(/[^a-z0-9]/gi, '_')}_battles.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function exportStatsJSON(modelName, stats) {
  const blob = new Blob([JSON.stringify(stats, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${modelName.replace(/[^a-z0-9]/gi, '_')}_stats.json`
  a.click()
  URL.revokeObjectURL(url)
}

// ---------------------------------------------------------------------------
// Lesson efficacy (#227)
// ---------------------------------------------------------------------------

function LessonEfficacy({ efficacy }) {
  const cohorts = [
    { key: 'with_lessons', label: 'Lessons ON' },
    { key: 'without_lessons', label: 'Lessons OFF' },
  ]
  const anyGames = cohorts.some(c => (efficacy[c.key]?.games ?? 0) > 0)
  if (!anyGames) {
    return <p className="panel-subtitle">No completed battles yet — run battles with lessons on and off to compare.</p>
  }
  const fmtRate = r => (r == null ? '—' : `${(r * 100).toFixed(0)}%`)
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr style={{ textAlign: 'left', opacity: 0.7 }}>
          <th>Cohort</th>
          <th style={{ textAlign: 'right' }}>W–L–T</th>
          <th style={{ textAlign: 'right' }}>Win rate</th>
        </tr>
      </thead>
      <tbody>
        {cohorts.map(c => {
          const d = efficacy[c.key] ?? {}
          return (
            <tr key={c.key} style={{ borderTop: '1px solid var(--border, #1e3a5f)' }}>
              <td>{c.label}</td>
              <td style={{ textAlign: 'right' }}>{d.wins ?? 0}–{d.losses ?? 0}–{d.ties ?? 0}</td>
              <td style={{ textAlign: 'right', fontWeight: 'bold' }}>{fmtRate(d.win_rate)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Main ModelStats component
// ---------------------------------------------------------------------------

function fetchReducer(state, action) {
  switch (action.type) {
    case 'start':   return { loading: true,  error: null,         stats: null }
    case 'success': return { loading: false, error: null,         stats: action.data }
    case 'error':   return { loading: false, error: action.error, stats: null }
    default:        return state
  }
}

export default function ModelStats({ modelId, onClose, onReplaySelected }) {
  const [{ loading, error, stats }, dispatch] = useReducer(
    fetchReducer,
    { loading: true, error: null, stats: null },
  )

  useEffect(() => {
    if (modelId == null) return
    let cancelled = false
    dispatch({ type: 'start' })
    fetch(`/api/models/${modelId}/stats`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(data => { if (!cancelled) dispatch({ type: 'success', data }) })
      .catch(err  => { if (!cancelled) dispatch({ type: 'error', error: err.message }) })
    return () => { cancelled = true }
  }, [modelId])

  if (loading) {
    return (
      <div className="stats-page">
        <div className="stats-loading">Loading model stats…</div>
      </div>
    )
  }

  if (error || !stats) {
    return (
      <div className="stats-page">
        <button className="stats-back" onClick={onClose}>← BACK</button>
        <div className="stats-error">Failed to load stats: {error}</div>
      </div>
    )
  }

  const { model, elo_history, battle_history, turn_stats, lessons, usage, lesson_efficacy } = stats
  const winRate = model.games > 0
    ? ((model.wins / model.games) * 100).toFixed(1)
    : null

  return (
    <div className="stats-page">
      {/* Header */}
      <div className="stats-header">
        <button className="stats-back" onClick={onClose}>← BACK</button>
        <div className="stats-identity">
          <div className="stats-model-name">{model.model_name}</div>
          <div className="stats-provider-row">
            <span className="provider-tag">{model.provider}</span>
            <span className="version-tag">{model.prompt_version}</span>
          </div>
        </div>
        <div className="ms-export-buttons">
          <button
            className="ms-export-btn"
            title="Download battle history as CSV"
            onClick={() => exportBattleHistoryCSV(model.model_name, battle_history)}
          >↓ CSV</button>
          <button
            className="ms-export-btn"
            title="Download full stats as JSON"
            onClick={() => exportStatsJSON(model.model_name, stats)}
          >↓ JSON</button>
        </div>
        <div className="stats-kpis">
          <div className="kpi-block">
            <div className="kpi-value elo-value">{model.rating.toFixed(1)}</div>
            <div className="kpi-label">ELO</div>
          </div>
          <div className="kpi-block">
            <div className="kpi-value">
              <span className="w">{model.wins}W</span>
              {' / '}
              <span className="l">{model.losses}L</span>
              {' / '}
              {model.ties}T
            </div>
            <div className="kpi-label">RECORD ({model.games} games)</div>
          </div>
          {winRate != null && (
            <div className="kpi-block">
              <div className="kpi-value" style={{
                color: winRate >= 60 ? 'var(--accent-green)'
                     : winRate >= 40 ? 'var(--accent-amber)'
                     : 'var(--accent-red)'
              }}>
                {winRate}%
              </div>
              <div className="kpi-label">WIN RATE</div>
            </div>
          )}
        </div>
      </div>

      {/* ELO chart */}
      <div className="panel stats-panel">
        <EloSparkline history={elo_history} />
      </div>

      {/* Two-column: quality + battles */}
      <div className="stats-grid">
        <div className="panel stats-panel">
          <div className="panel-title">TURN QUALITY</div>
          <QualityBar turnStats={turn_stats} />
        </div>

        <div className="panel stats-panel">
          <div className="panel-title">BATTLE HISTORY</div>
          <BattleHistoryTable
            battles={battle_history}
            onReplaySelected={onReplaySelected}
          />
        </div>
      </div>

      {/* Lesson efficacy — lessons-on vs lessons-off win rate (#227) */}
      {lesson_efficacy && (
        <div className="panel stats-panel">
          <div className="panel-title">
            LESSON EFFICACY
            <span className="panel-subtitle">win rate with cross-battle lessons on vs off</span>
          </div>
          <LessonEfficacy efficacy={lesson_efficacy} />
        </div>
      )}

      {/* Usage stats — only shown when there's turn data */}
      {usage && (usage.top_pokemon?.length > 0 || usage.top_moves?.length > 0) && (
        <div className="stats-grid">
          <div className="panel stats-panel">
            <div className="panel-title">
              POKÉMON USAGE
              <span className="panel-subtitle">most turns as active mon</span>
            </div>
            <UsagePokemon rows={usage.top_pokemon ?? []} />
          </div>

          <div className="panel stats-panel">
            <div className="panel-title">
              MOVE USAGE
              <span className="panel-subtitle">most frequently chosen</span>
            </div>
            <UsageMoves rows={usage.top_moves ?? []} />
          </div>
        </div>
      )}

      {/* Action distribution + win-rate by tier */}
      {usage && (usage.action_distribution?.length > 0 || usage.win_rate_by_tier?.length > 0) && (
        <div className="stats-grid">
          <div className="panel stats-panel">
            <div className="panel-title">ACTION SPLIT</div>
            <ActionDistribution rows={usage.action_distribution ?? []} />
          </div>

          <div className="panel stats-panel">
            <div className="panel-title">WIN RATE BY TIER</div>
            <WinRateByTier rows={usage.win_rate_by_tier ?? []} />
          </div>
        </div>
      )}

      {/* Head-to-head record */}
      <div className="panel stats-panel">
        <ModelH2H modelId={modelId} />
      </div>

      {/* Badges */}
      <div className="panel stats-panel">
        <div className="panel-title">BADGES</div>
        <BadgeGallery modelId={modelId} />
      </div>

      {/* Lessons */}
      <div className="panel stats-panel">
        <div className="panel-title">
          BATTLE MEMORY
          <span className="panel-subtitle">
            {lessons.length} lesson{lessons.length !== 1 ? 's' : ''} accumulated
          </span>
        </div>
        <LessonsLog lessons={lessons} />
      </div>
    </div>
  )
}

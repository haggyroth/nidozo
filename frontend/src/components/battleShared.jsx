/**
 * Shared battle-data presentational components, used by BOTH the Classic
 * BattleField and the Showdown cockpit so the two views stay in parity (same
 * data, same rendering — no drift). All read from the app EventBus state shapes
 * (`p1State`/`p2State` = { turn, state: { my_active, my_team, ... } }).
 */

import { useState } from 'react'
import { TypeBadge } from './PokemonCard'

/** Team-HP score for win-probability: sum of bench hp_fractions, or active HP. */
function teamHpScore(state) {
  if (!state) return null
  const team = state.my_team ?? []
  if (team.length > 0) {
    return team.reduce((acc, m) => acc + Math.max(0, m.hp_fraction ?? 0), 0)
  }
  const active = state.my_active
  return active ? Math.max(0, active.hp_fraction ?? 0.5) : null
}

/** P1 win-probability over time, drawn as an inline SVG sparkline.
 *  y is P1's share (top = P1 ahead, bottom = P2 ahead); a dashed 50% line marks
 *  parity. viewBox is stretched to the element box, so strokes use
 *  non-scaling-stroke (set in CSS) to stay crisp. */
function WinProbSparkline({ history }) {
  const W = 100
  const H = 100
  const n = history.length
  const points = history
    .map((p1, i) => {
      const x = n === 1 ? 0 : (i / (n - 1)) * W
      const y = (1 - p1) * H
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  return (
    <svg
      className="win-prob-spark"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <line className="wps-mid" x1="0" y1={H / 2} x2={W} y2={H / 2} />
      <polyline className="wps-line" points={points} />
    </svg>
  )
}

/** Win-probability bar from the two players' team-HP ratio, with a sparkline of
 *  P1's win-probability across the battle's turns. */
export function WinProbBar({ p1State, p2State, p1Label, p2Label }) {
  const s1 = teamHpScore(p1State?.state)
  const s2 = teamHpScore(p2State?.state)
  const turn = Math.max(p1State?.turn ?? 0, p2State?.turn ?? 0)
  const haveData = s1 !== null && s2 !== null
  const p1Prob = !haveData ? null : (s1 + s2 === 0 ? 0.5 : s1 / (s1 + s2))

  // Accumulate one P1-probability sample per turn for the sparkline. We adjust
  // state during render when the turn advances (React's documented
  // "adjust state on prop change" pattern — converges immediately, no effect).
  // A turn going backwards means a new battle started, so the history resets.
  const [history, setHistory] = useState([])      // [{ turn, p1 }]
  const [sampledTurn, setSampledTurn] = useState(-1)
  if (p1Prob !== null && turn !== sampledTurn) {
    setSampledTurn(turn)
    setHistory(h => (turn < sampledTurn ? [] : h).concat({ turn, p1: p1Prob }))
  }

  if (!haveData) return null

  const p1Pct  = Math.round(p1Prob * 100)
  const p2Pct  = 100 - p1Pct

  const p1Name = p1Label?.split('/').pop() ?? 'P1'
  const p2Name = p2Label?.split('/').pop() ?? 'P2'

  return (
    <div className="win-prob-bar">
      <div className="win-prob-pcts">
        <span className="win-prob-pct win-prob-pct--p1">
          <span className="win-prob-name">{p1Name}</span>
          <span className="win-prob-num">{p1Pct}%</span>
        </span>
        <span className="win-prob-mid-label">WIN PROB</span>
        <span className="win-prob-pct win-prob-pct--p2">
          <span className="win-prob-num">{p2Pct}%</span>
          <span className="win-prob-name">{p2Name}</span>
        </span>
      </div>
      <div className="win-prob-track">
        <div className="win-prob-fill win-prob-fill--p1" style={{ width: `${p1Pct}%` }} />
        <div className="win-prob-fill win-prob-fill--p2" style={{ width: `${p2Pct}%` }} />
        <div className="win-prob-midline" />
      </div>
      {history.length >= 2 && <WinProbSparkline history={history.map(d => d.p1)} />}
    </div>
  )
}

/** Collapsible heuristic advisory drawer — move scores, type badges, PP. */
export function HeuristicDrawer({ heuristics, moves, label = 'HEURISTIC ADVISORY' }) {
  const [open, setOpen] = useState(false)
  // Render nothing only before any advisory exists (e.g. pre-first-turn). Once a
  // player has had move scores, keep the drawer mounted even on turns where it
  // momentarily has none (a forced switch yields an empty move_scores). Vanishing
  // mid-battle made an opened drawer flicker out and the cockpit grid reflow (#157).
  if (!heuristics) return null

  const moveScores = heuristics.move_scores ?? []

  const moveInfo = {}
  ;(moves ?? []).forEach(m => {
    if (m.id) moveInfo[m.id] = { type: m.type, pp: m.pp, max_pp: m.max_pp }
  })

  return (
    <div className="heuristic-drawer">
      <button className="heuristic-toggle" onClick={() => setOpen(o => !o)}>
        <span>⚙ {label}</span>
        <span className={`drawer-chevron ${open ? 'open' : ''}`}>▼</span>
      </button>
      {open && moveScores.length === 0 && (
        <div className="heuristic-content heuristic-content--empty">
          No move advisory this turn (forced switch).
        </div>
      )}
      {open && moveScores.length > 0 && (
        <div className="heuristic-content">
          {moveScores.map((ms, i) => {
            const isSuper  = ms.effectiveness_label?.includes('super')
            const isImmune = ms.effectiveness_label?.includes('immune')
            const info = moveInfo[ms.move_id] ?? {}
            const ppLow   = info.pp != null && info.max_pp > 0 && (info.pp / info.max_pp) <= 0.25
            const ppEmpty = info.pp === 0
            return (
              <div key={i} className="heuristic-move">
                <div className="hmove-header">
                  <span className="hmove-name">
                    {`move ${i + 1}`} — {ms.move_id.replace(/_/g, ' ')}
                  </span>
                  <div className="hmove-badges">
                    {info.type && <TypeBadge type={info.type.toUpperCase()} />}
                    {info.pp != null && (
                      <span className={`hmove-pp${ppLow ? ' hmove-pp--low' : ''}${ppEmpty ? ' hmove-pp--empty' : ''}`}>
                        {info.pp}/{info.max_pp} PP
                      </span>
                    )}
                  </div>
                </div>
                <span className={`hmove-effectiveness ${isSuper ? 'super' : isImmune ? 'immune' : ''}`}>
                  {ms.effectiveness_label}
                  {ms.estimated_damage_pct && ` · ${ms.estimated_damage_pct}`}
                </span>
                <span className="hmove-notes">
                  {(ms.notes || []).join(' · ')}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** Animated "thinking" or "coach analyzing" indicator. */
export function ThinkingBadge({ role, isCoach }) {
  if (!role) return null
  if (isCoach) {
    return (
      <div className="thinking-badge thinking-badge--coach">
        <span className="thinking-badge-icon">🎓</span>
        <span style={{ marginLeft: '0.35rem', fontSize: '0.65rem', opacity: 0.85, letterSpacing: '0.04em' }}>
          {role.toUpperCase()} COACH ANALYZING
        </span>
      </div>
    )
  }
  return (
    <div className="thinking-badge">
      <span className="thinking-badge-dot" />
      <span className="thinking-badge-dot" />
      <span className="thinking-badge-dot" />
      <span style={{ marginLeft: '0.4rem', fontSize: '0.65rem', opacity: 0.7 }}>
        {role.toUpperCase()} THINKING
      </span>
    </div>
  )
}

const PERSONALITY_META = {
  aggressive: { emoji: '⚔️', label: 'All-out Attacker' },
  defensive:  { emoji: '🛡️', label: 'Bulwark' },
  balanced:   { emoji: '⚖️', label: 'Adaptive' },
  trickster:  { emoji: '🎭', label: 'Mindgame Specialist' },
  momentum:   { emoji: '💨', label: 'Tempo Player' },
}

/** Small chip showing the active play-style persona. */
export function PersonalityChip({ personality }) {
  if (!personality) return null
  const meta = PERSONALITY_META[personality]
  if (!meta) return null
  return (
    <div className="personality-chip" title={meta.label}>
      <span className="personality-chip-emoji">{meta.emoji}</span>
      <span className="personality-chip-label">{meta.label}</span>
    </div>
  )
}

/** Live badge notification stack — shown when a badge_earned WS event fires. */
export function BadgeToastStack({ badges, onDismiss }) {
  if (!badges?.length) return null
  return (
    <div className="badge-toast-stack">
      {badges.map((b, i) => (
        <div key={i} className="badge-toast" onClick={() => onDismiss(i)}>
          <span className="badge-toast-emoji">{b.emoji}</span>
          <div className="badge-toast-body">
            <div className="badge-toast-title">Badge unlocked!</div>
            <div className="badge-toast-name">{b.name}</div>
            <div className="badge-toast-desc">{b.description}</div>
          </div>
          <button className="badge-toast-close" onClick={e => { e.stopPropagation(); onDismiss(i) }}>✕</button>
        </div>
      ))}
    </div>
  )
}

/** Provider + model name label for one side. */
export function PlayerLabel({ label, side }) {
  if (!label) return null
  // "anthropic/claude-sonnet-4-6" → provider="anthropic", name="claude-sonnet-4-6"
  const slash = label.indexOf('/')
  const provider = slash >= 0 ? label.slice(0, slash) : ''
  const name     = slash >= 0 ? label.slice(slash + 1) : label
  return (
    <div className={`player-label player-label--${side}`}>
      {provider && <span className="player-label-provider">{provider}</span>}
      <span className="player-label-name">{name}</span>
    </div>
  )
}

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

/** Win-probability bar from the two players' team-HP ratio. */
export function WinProbBar({ p1State, p2State, p1Label, p2Label }) {
  const s1 = teamHpScore(p1State?.state)
  const s2 = teamHpScore(p2State?.state)

  if (s1 === null || s2 === null) return null

  const total = s1 + s2
  const p1Prob = total === 0 ? 0.5 : s1 / total
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

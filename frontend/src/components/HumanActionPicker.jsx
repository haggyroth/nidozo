/**
 * HumanActionPicker — move/switch selector for the human player slot.
 *
 * Rendered when a `human_action_required` event arrives. The human clicks a
 * move or switch button and the choice is POSTed to the battle API, which
 * resolves the asyncio Future blocking poke-env's choose_move on the server.
 *
 * Props:
 *   battleId        — numeric battle id
 *   playerRole      — "p1" | "p2"
 *   turn            — current turn number
 *   state           — full serialized battle state (available_moves, my_team, etc.)
 *   onDismiss       — called after a successful submission so the parent can hide this
 */

import { useState } from 'react'

const TYPE_COLORS = {
  NORMAL:   '#A8A77A', FIRE:    '#EE8130', WATER:   '#6390F0',
  ELECTRIC: '#F7D02C', GRASS:   '#7AC74C', ICE:     '#96D9D6',
  FIGHTING: '#C22E28', POISON:  '#A33EA1', GROUND:  '#E2BF65',
  FLYING:   '#A98FF3', PSYCHIC: '#F95587', BUG:     '#A6B91A',
  ROCK:     '#B6A136', GHOST:   '#735797', DRAGON:  '#6F35FC',
  DARK:     '#705746', STEEL:   '#B7B7CE', FAIRY:   '#D685AD',
}

function TypeBadge({ type }) {
  const color = TYPE_COLORS[type?.toUpperCase()] ?? '#888'
  return (
    <span className="hap-type-badge" style={{ background: color }}>
      {type}
    </span>
  )
}

function HPBar({ fraction }) {
  const pct = Math.max(0, Math.min(100, (fraction ?? 0) * 100))
  const color = pct > 50 ? 'var(--hp-high)' : pct > 20 ? 'var(--hp-mid)' : 'var(--hp-low)'
  return (
    <div className="hap-hp-track">
      <div className="hap-hp-fill" style={{ width: `${pct}%`, background: color }} />
    </div>
  )
}

export default function HumanActionPicker({ battleId, playerRole, turn, state, onDismiss }) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const availableMoves    = state?.available_moves    ?? []
  const availableSwitches = state?.available_switches ?? []   // list of species name strings
  const myTeam            = state?.my_team            ?? []   // full bench data
  const heuristics        = state?.heuristics         ?? []
  const forceSwitch       = state?.force_switch       ?? false

  // Build a lookup from species name → bench data for switch buttons
  const teamBySpecies = Object.fromEntries(
    myTeam.map(p => [p?.species?.toLowerCase(), p])
  )

  // Heuristics keyed by move id for score display
  const heuristicByMove = Object.fromEntries(
    (heuristics ?? []).map(h => [h.id, h])
  )

  async function submit(actionType, identifier) {
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await fetch(`/api/battles/${battleId}/human-action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ player_role: playerRole, action_type: actionType, identifier }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data.detail ?? `Error ${res.status}`)
        setSubmitting(false)
        return
      }
      onDismiss?.()
    } catch {
      setError('Network error — try again')
      setSubmitting(false)
    }
  }

  return (
    <div className="hap-overlay">
      <div className="hap-panel">
        <div className="hap-header">
          <span className="hap-turn-label">TURN {turn}</span>
          <span className="hap-prompt">Your move</span>
        </div>

        {error && <div className="hap-error">{error}</div>}

        {/* Moves — hidden when force-switching */}
        {!forceSwitch && availableMoves.length > 0 && (
          <section className="hap-section">
            <div className="hap-section-label">Moves</div>
            <div className="hap-move-grid">
              {availableMoves.map((move, i) => {
                const h = heuristicByMove[move.id]
                const ppLow = move.pp != null && move.max_pp != null && move.pp <= move.max_pp * 0.25
                return (
                  <button
                    key={move.id}
                    className={`hap-move-btn${ppLow ? ' hap-move-btn--pp-low' : ''}`}
                    onClick={() => submit('move', move.id)}
                    disabled={submitting}
                    title={h ? `Score: ${h.score?.toFixed(1) ?? '?'}` : undefined}
                  >
                    <span className="hap-move-hotkey">{i + 1}</span>
                    <TypeBadge type={move.type} />
                    <span className="hap-move-name">
                      {move.id.replace(/_/g, ' ')}
                    </span>
                    <span className="hap-move-meta">
                      {move.base_power > 0 ? `${move.base_power} BP` : 'Status'}
                    </span>
                    {move.pp != null && (
                      <span className={`hap-move-pp${ppLow ? ' hap-move-pp--low' : ''}`}>
                        {move.pp}/{move.max_pp} PP
                      </span>
                    )}
                    {h?.score != null && (
                      <span className="hap-move-score">
                        {h.score > 0 ? '+' : ''}{h.score.toFixed(0)}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          </section>
        )}

        {/* Switches */}
        {availableSwitches.length > 0 && (
          <section className="hap-section">
            <div className="hap-section-label">
              {forceSwitch ? 'Choose your next Pokémon' : 'Switch to'}
            </div>
            <div className="hap-switch-grid">
              {availableSwitches.map((species) => {
                const bench = teamBySpecies[species.toLowerCase()]
                const hp = bench?.hp_fraction ?? null
                const fainted = bench?.fainted ?? false
                const status = bench?.status ?? null
                const types = bench?.types ?? []
                if (fainted) return null
                return (
                  <button
                    key={species}
                    className="hap-switch-btn"
                    onClick={() => submit('switch', species)}
                    disabled={submitting}
                  >
                    <img
                      className="hap-switch-sprite"
                      src={`https://play.pokemonshowdown.com/sprites/gen5/${species.toLowerCase().replace(/[^a-z0-9-]/g, '')}.png`}
                      alt={species}
                      onError={e => { e.target.style.display = 'none' }}
                    />
                    <div className="hap-switch-info">
                      <span className="hap-switch-name">{species}</span>
                      <div className="hap-switch-types">
                        {types.map(t => <TypeBadge key={t} type={t} />)}
                        {status && <span className="hap-switch-status">{status}</span>}
                      </div>
                      {hp != null && (
                        <>
                          <HPBar fraction={hp} />
                          <span className="hap-switch-hp">{Math.round(hp * 100)}%</span>
                        </>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          </section>
        )}

        {submitting && (
          <div className="hap-submitting">Sending…</div>
        )}
      </div>
    </div>
  )
}

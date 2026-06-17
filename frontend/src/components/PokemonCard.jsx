import { useRef, useEffect, useState } from 'react'
import HPBar from './HPBar'
import PokemonTooltip from './PokemonTooltip'
import { useTooltip } from '../hooks/useTooltip'

// Official Pokémon type colours
const TYPE_COLORS = {
  NORMAL:   '#9a9a7a', FIRE:     '#ff6b35', WATER:    '#4d9de0',
  ELECTRIC: '#f7c948', GRASS:    '#4caf50', ICE:      '#80deea',
  FIGHTING: '#e53935', POISON:   '#ab47bc', GROUND:   '#c6a34a',
  FLYING:   '#9575cd', PSYCHIC:  '#e91e8c', BUG:      '#8bc34a',
  ROCK:     '#a1887f', GHOST:    '#5e35b1', DRAGON:   '#5c6bc0',
  DARK:     '#6d4c41', STEEL:    '#90a4ae', FAIRY:    '#f48fb1',
}

// ---------------------------------------------------------------------------
// Type background + border helpers
// ---------------------------------------------------------------------------

/**
 * Returns a subtle gradient background reflecting the Pokémon's typing.
 * Single-type: corner wash of that type's colour.
 * Dual-type: diagonal split, both corners tinted.
 * Alpha is kept very low (~10–15%) so text/HP bar always read cleanly.
 */
function typeBackground(types) {
  if (!types?.length) return undefined
  const c1 = TYPE_COLORS[types[0]]
  if (!c1) return undefined
  if (types.length === 1) {
    return `linear-gradient(145deg, ${c1}28 0%, var(--bg-card) 55%)`
  }
  const c2 = TYPE_COLORS[types[1]] ?? c1
  return `linear-gradient(145deg, ${c1}28 0%, var(--bg-card) 42%, var(--bg-card) 58%, ${c2}22 100%)`
}

/**
 * Returns the card border + glow style driven by the primary type.
 * P1 colours its left edge; P2 colours its right edge.
 * The thinking state CSS class overrides these with amber via !important.
 */
function typeAccentStyle(types, side) {
  const color = TYPE_COLORS[types?.[0]]
  if (!color) return {}
  return side === 'p1'
    ? { borderLeftColor: color,  boxShadow: `0 0 14px ${color}26` }
    : { borderRightColor: color, boxShadow: `0 0 14px ${color}26` }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function spriteUrl(species) {
  if (!species) return null
  // Build the Showdown sprite ID: lowercase, spaces→hyphens, strip non-alphanumeric
  // except hyphens (which separate form names like "deoxys-speed", "rotom-wash").
  const id = species.toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
  // HOME sprites cover every National-Dex entry through Gen 9.
  return `https://play.pokemonshowdown.com/sprites/home/${id}.png`
}

function PokemonSprite({ species, size = 80, isThinking = false, animClass = '' }) {
  const url = spriteUrl(species)
  if (!url) return null
  return (
    <div
      className={`pokemon-sprite-wrap${isThinking ? ' thinking' : ''} ${animClass}`}
      style={{ width: size, height: size }}
    >
      <img
        className="pokemon-sprite"
        src={url}
        alt={species}
        width={size}
        height={size}
        onError={e => { e.currentTarget.style.display = 'none' }}
        style={{ imageRendering: 'auto' }}
      />
      {isThinking && (
        <div className="thinking-dots">
          <span /><span /><span />
        </div>
      )}
    </div>
  )
}

export function TypeBadge({ type }) {
  const color = TYPE_COLORS[type] || '#666'
  return (
    <span className="type-badge" style={{ background: color }}>
      {type}
    </span>
  )
}

function StatusBadge({ status }) {
  if (!status) return null
  const short = {
    BURNED: 'BRN', PARALYZED: 'PAR', POISONED: 'PSN',
    TOXIC: 'TOX', ASLEEP: 'SLP', FROZEN: 'FRZ', FAINTED: 'FNT',
  }
  const label = short[status] || status.slice(0, 3)
  return <span className={`status-badge status-${label}`}>{label}</span>
}

function BenchSlot({ mon }) {
  const tooltip = useTooltip()
  if (!mon) return <div className="bench-slot empty" />
  const url = spriteUrl(mon.species)
  return (
    <>
      <div
        className={`bench-slot${mon.fainted ? ' fainted' : ''}`}
        onMouseEnter={tooltip.onEnter}
        onMouseLeave={tooltip.onLeave}
      >
        {url ? (
          <img
            src={url}
            alt={mon.species}
            className="bench-sprite"
            onError={e => { e.currentTarget.style.display = 'none' }}
            style={{ imageRendering: 'auto' }}
          />
        ) : (
          <div className="bench-sprite-fallback">{mon.species?.slice(0, 2).toUpperCase()}</div>
        )}
        <div
          className="bench-hp-bar"
          style={{
            '--hp': `${Math.round((mon.hp_fraction ?? 0) * 100)}%`,
            '--hp-color': (mon.hp_fraction ?? 0) > 0.5 ? '#4caf50'
              : (mon.hp_fraction ?? 0) > 0.2 ? '#f7c948' : '#ff4455',
          }}
        />
      </div>
      <PokemonTooltip mon={mon} anchor={tooltip.anchor} />
    </>
  )
}

// ---------------------------------------------------------------------------
// PokemonCard
// ---------------------------------------------------------------------------

const MOVE_TYPE_COLORS = {
  NORMAL: '#9a9a7a', FIRE: '#ff6b35', WATER: '#4d9de0', ELECTRIC: '#f7c948',
  GRASS: '#4caf50', ICE: '#80deea', FIGHTING: '#e53935', POISON: '#ab47bc',
  GROUND: '#c6a34a', FLYING: '#9575cd', PSYCHIC: '#e91e8c', BUG: '#8bc34a',
  ROCK: '#a1887f', GHOST: '#5e35b1', DRAGON: '#5c6bc0', DARK: '#6d4c41',
  STEEL: '#90a4ae', FAIRY: '#f48fb1',
}

function MoveList({ moves }) {
  const entries = Object.values(moves || {})
  if (entries.length === 0) return null
  return (
    <div className="own-moves">
      {entries.map((m, i) => {
        const color = MOVE_TYPE_COLORS[m.type] ?? '#666'
        const ppLow = m.pp != null && m.max_pp != null && m.pp / m.max_pp <= 0.25
        return (
          <div key={m.id ?? i} className={`own-move-row${ppLow ? ' low-pp' : ''}`}>
            <span className="own-move-type-dot" style={{ background: color }} title={m.type} />
            <span className="own-move-name">{(m.id ?? '').replace(/_/g, ' ')}</span>
            <span className="own-move-meta">
              {m.base_power > 0 ? `${m.base_power}` : '—'}
              {m.pp != null && m.max_pp != null && (
                <span className={`own-move-pp${ppLow ? ' low-pp' : ''}`}> {m.pp}/{m.max_pp}</span>
              )}
            </span>
          </div>
        )
      })}
    </div>
  )
}

export default function PokemonCard({ mon, side, isOpponent = false, isThinking = false, bench = [] }) {
  // ---- Tooltip ----
  const tooltip = useTooltip()

  // ---- Animation state ----
  // Track HP changes to trigger hit / heal / faint animations.
  // Reset when the species changes (new Pokémon switched in).
  const prevHpRef      = useRef(null)
  const prevSpeciesRef = useRef(null)
  const [animClass, setAnimClass] = useState('')

  useEffect(() => {
    const currHp      = mon?.hp_fraction ?? null
    const currSpecies = mon?.species     ?? null

    // Species changed — new mon switched in, reset reference HP silently
    if (currSpecies !== prevSpeciesRef.current) {
      prevHpRef.current      = currHp
      prevSpeciesRef.current = currSpecies
      setAnimClass('')
      return
    }

    const prevHp = prevHpRef.current
    if (prevHp !== null && currHp !== null && prevHp !== currHp) {
      const cls = currHp <= 0 ? 'card-faint' : currHp < prevHp ? 'card-hit' : 'card-heal'

      setAnimClass(cls)
      const duration = cls === 'card-faint' ? 800 : 420
      const timer = setTimeout(() => setAnimClass(''), duration)
      prevHpRef.current = currHp
      return () => clearTimeout(timer)
    }

    prevHpRef.current      = currHp
    prevSpeciesRef.current = currSpecies
  }, [mon?.hp_fraction, mon?.species])

  // ---- Empty card ----
  if (!mon) {
    return (
      <div className={`pokemon-card ${side} empty`}>
        <div className="card-empty-label">waiting…</div>
        {isThinking && (
          <div className="thinking-dots standalone">
            <span /><span /><span />
          </div>
        )}
      </div>
    )
  }

  // Pass bench slot mon as tooltip target; for the card itself use mon directly.

  const boostEntries  = Object.entries(mon.boosts || {}).filter(([, v]) => v !== 0)
  const revealedMoves = isOpponent ? Object.values(mon.revealed_moves || {}) : null
  const ownMoves      = !isOpponent ? (mon.moves ?? {}) : null
  const bg            = typeBackground(mon.types)
  const accentStyle   = typeAccentStyle(mon.types, side)

  return (
    <>
    <div
      className={`pokemon-card ${side}${isThinking ? ' is-thinking' : ''} ${animClass}`}
      style={{ background: bg, ...accentStyle }}
      onMouseEnter={tooltip.onEnter}
      onMouseLeave={tooltip.onLeave}
    >
      {/* Sprite — shake animation attaches here */}
      <PokemonSprite
        species={mon.species}
        size={88}
        isThinking={isThinking}
        animClass={animClass === 'card-hit' ? 'sprite-shake' : ''}
      />

      <div className="card-body">
        <div className="card-header">
          <div>
            <div className="pokemon-name">{mon.species}</div>
            <div className="pokemon-level">Lv.{mon.level ?? 50}</div>
            {mon.status && <StatusBadge status={mon.status} />}
            {mon.is_terastallized && (
              <span className="tera-badge" title={`Terastallized: ${mon.tera_type ?? ''} type`}>
                ◈ TERA{mon.tera_type ? ` ${mon.tera_type}` : ''}
              </span>
            )}
          </div>
          <div className="type-badges">
            {(mon.types || []).map(t => <TypeBadge key={t} type={t} />)}
          </div>
        </div>

        <HPBar fraction={mon.hp_fraction ?? 1} />

        {boostEntries.length > 0 && (
          <div className="stat-boosts">
            {boostEntries.map(([stat, val]) => (
              <span key={stat} className={`boost-badge ${val > 0 ? 'boost-pos' : 'boost-neg'}`}>
                {stat} {val > 0 ? `+${val}` : val}
              </span>
            ))}
          </div>
        )}

        {!isOpponent && ownMoves && <MoveList moves={ownMoves} />}

        {isOpponent && revealedMoves && revealedMoves.length > 0 && (
          <div className="revealed-moves">
            <div className="revealed-moves-title">Revealed moves</div>
            {revealedMoves.map(m => (
              <div key={m.id} className="revealed-move-item">
                · {m.id.replace(/_/g, ' ')} ({m.type}, {m.base_power} BP)
              </div>
            ))}
          </div>
        )}

        {mon.item && <div className="mon-item">Item: {mon.item}</div>}
      </div>

      {bench.length > 0 && (
        <div className="bench-row">
          {bench.map((b, i) => <BenchSlot key={i} mon={b} />)}
        </div>
      )}
    </div>
    <PokemonTooltip mon={mon} anchor={tooltip.anchor} />
    </>
  )
}

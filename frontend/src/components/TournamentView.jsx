import { useState, useEffect, useReducer } from 'react'
import BracketView from './BracketView'

// ---------------------------------------------------------------------------
// Shared tier badge (mirrors BattleField palette)
// ---------------------------------------------------------------------------

const TIER_LABELS = {
  random:     'RANDOM',
  ou:         'OU',
  ubers:      'UBERS',
  uu:         'UU',
  nu:         'NU',
  lc:         'LC',
  freeforall: 'FREE-FOR-ALL',
}

function TierBadge({ tier, className = '' }) {
  if (!tier || tier === 'random') return null
  return (
    <span className={`tier-badge tier-badge--${tier} ${className}`}>
      {TIER_LABELS[tier] ?? tier.toUpperCase()}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }) {
  const cls = status === 'completed' ? 'ts-badge-done'
            : status === 'cancelled' ? 'ts-badge-cancelled'
            : 'ts-badge-running'
  const label = status === 'completed' ? 'COMPLETED'
              : status === 'cancelled' ? 'CANCELLED'
              : 'RUNNING'
  return <span className={`ts-badge ${cls}`}>{label}</span>
}

// ---------------------------------------------------------------------------
// Standings table
// ---------------------------------------------------------------------------

function StandingsTable({ standings }) {
  if (!standings || standings.length === 0) {
    return (
      <div className="ts-empty">
        No completed battles yet — standings will appear after the first battle.
      </div>
    )
  }

  const maxPoints = standings[0]?.points ?? 1

  return (
    <table className="ts-standings-table">
      <thead>
        <tr>
          <th>#</th>
          <th>MODEL</th>
          <th>W</th>
          <th>L</th>
          <th>T</th>
          <th>PTS</th>
          <th>PLAYED</th>
          <th>RATING Δ</th>
          <th>FORM</th>
        </tr>
      </thead>
      <tbody>
        {standings.map((row, i) => {
          const eloSign = row.elo_delta >= 0 ? '+' : ''
          const eloClass = row.elo_delta > 0 ? 'ts-elo-up'
                         : row.elo_delta < 0 ? 'ts-elo-down' : ''
          // Mini point bar (proportional to leader)
          const barPct = maxPoints > 0 ? (row.points / maxPoints) * 100 : 0
          return (
            <tr key={row.model_id} className={i === 0 ? 'ts-leader-row' : ''}>
              <td className="ts-rank">
                {i === 0 ? '🏆' : i + 1}
              </td>
              <td className="ts-model-cell">
                <span className="ts-model-name">{row.model_name}</span>
                <span className="ts-provider-tag">{row.provider}</span>
              </td>
              <td className="ts-w">{row.wins}</td>
              <td className="ts-l">{row.losses}</td>
              <td className="ts-t">{row.ties}</td>
              <td className="ts-pts">
                <div className="ts-pts-inner">
                  <div className="ts-pts-bar" style={{ width: `${barPct}%` }} />
                  <span className="ts-pts-val">{row.points}</span>
                </div>
              </td>
              <td className="ts-played">{row.battles_played}</td>
              <td className={`ts-elo-delta ${eloClass}`}>
                {eloSign}{row.elo_delta.toFixed(1)}
              </td>
              <td className="ts-form-cell">
                {/* placeholder for future form sparkline */}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Battle results grid
// ---------------------------------------------------------------------------

function BattleGrid({ battles, onReplaySelected }) {
  if (!battles || battles.length === 0) {
    return <div className="ts-empty">No battles scheduled.</div>
  }

  return (
    <table className="ts-battle-table">
      <thead>
        <tr>
          <th>#</th>
          <th>P1</th>
          <th></th>
          <th>P2</th>
          <th>TURNS</th>
          <th>TIME</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {battles.map((b, i) => {
          const done = b.status === 'completed'
          const isRunning = b.status === 'running'
          const cls = isRunning ? 'ts-br-running'
                    : !done ? 'ts-br-pending'
                    : b.winner === 1 ? 'ts-br-p1win'
                    : b.winner === 2 ? 'ts-br-p2win'
                    : 'ts-br-tie'
          return (
            <tr key={b.id} className={`ts-battle-row ${cls}`}>
              <td className="ts-bn">
                {i + 1}
                {isRunning && <span className="ts-br-live-dot" title="Live" />}
              </td>
              <td className={`ts-bplayer ${b.winner === 1 ? 'ts-winner-player' : ''}`}>
                <span className="ts-bmodel">{b.p1_model}</span>
                <span className="ts-bprov">{b.p1_provider}</span>
              </td>
              <td className="ts-vs-cell">
                {isRunning
                  ? <span className="ts-result-badge ts-r-running">LIVE</span>
                  : done
                    ? <span className={`ts-result-badge ts-r-${b.winner === 1 ? 'p1' : b.winner === 2 ? 'p2' : 'tie'}`}>
                        {b.winner === 1 ? 'P1 WON' : b.winner === 2 ? 'P2 WON' : 'TIE'}
                      </span>
                    : <span className="ts-result-badge ts-r-pending">PENDING</span>
                }
              </td>
              <td className={`ts-bplayer ts-bplayer-right ${b.winner === 2 ? 'ts-winner-player' : ''}`}>
                <span className="ts-bmodel">{b.p2_model}</span>
                <span className="ts-bprov">{b.p2_provider}</span>
              </td>
              <td className="ts-turns-cell">{done ? b.total_turns : '—'}</td>
              <td className="ts-time-cell">
                {b.finished_at
                  ? new Date(b.finished_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                  : '—'
                }
              </td>
              <td className="ts-replay-cell">
                {b.drafted ? <span className="ts-drafted-tag" title="Drafted teams">DRAFT</span> : null}
                {done && (
                  <button
                    className="btn-replay btn-replay-sm"
                    onClick={() => onReplaySelected?.(b.id)}
                    title="Watch replay"
                  >▶</button>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Now-playing banner
// ---------------------------------------------------------------------------

function NowPlaying({ tournament }) {
  if (!tournament || tournament.status !== 'running' || !tournament.p1) return null
  return (
    <div className="ts-now-playing">
      <span className="ts-now-dot" />
      <span className="ts-now-label">NOW PLAYING</span>
      <span className="ts-now-matchup">
        <span className="ts-now-p1">{tournament.p1.split('/').pop()}</span>
        <span className="ts-now-vs">vs</span>
        <span className="ts-now-p2">{tournament.p2.split('/').pop()}</span>
      </span>
      <span className="ts-now-counter">
        Battle {tournament.battleNum ?? '?'} of {tournament.total}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Fetch reducer
// ---------------------------------------------------------------------------

function fetchReducer(state, action) {
  switch (action.type) {
    case 'start':   return { ...state, loading: true,  error: null }
    case 'success': return { loading: false, error: null, ...action.data }
    case 'error':   return { ...state, loading: false, error: action.error }
    default:        return state
  }
}

// ---------------------------------------------------------------------------
// Main TournamentView
// ---------------------------------------------------------------------------

export default function TournamentView({ tournamentId, tournament: liveTournament, onClose, onWatchLive, onReplaySelected }) {
  const [{ loading, error, meta, battles }, dispatch] = useReducer(fetchReducer, {
    loading: true, error: null, meta: null, battles: [],
  })
  const [activeTab, setActiveTab] = useState(null)  // null = auto

  useEffect(() => {
    if (tournamentId == null) return
    let cancelled = false
    dispatch({ type: 'start' })
    Promise.all([
      fetch(`/api/tournaments/${tournamentId}`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
      fetch(`/api/tournaments/${tournamentId}/battles`).then(r => r.ok ? r.json() : []),
    ])
      .then(([meta, battles]) => {
        if (!cancelled) dispatch({ type: 'success', data: { meta, battles } })
      })
      .catch(err => { if (!cancelled) dispatch({ type: 'error', error: String(err) }) })
    return () => { cancelled = true }
  }, [tournamentId])

  // Merge live tournament data (standings from WS) with fetched battles
  const standings = liveTournament?.standings ?? null
  const status    = liveTournament?.status ?? meta?.status ?? 'running'
  const totalBattles = meta?.total_battles ?? 0
  const battlesCompleted = liveTournament?.done ?? battles.filter(b => b.status === 'completed').length
  const tier = liveTournament?.tier ?? meta?.tier ?? 'random'
  const tournamentFormat = liveTournament?.tournament_format ?? meta?.tournament_format ?? 'round_robin'
  const isBracket = tournamentFormat === 'single_elim' || tournamentFormat === 'double_elim'
  // Bracket state: prefer live (most recent) over fetched meta
  const bracketState = liveTournament?.bracket ?? (
    meta?.bracket_state && typeof meta.bracket_state === 'object' ? meta.bracket_state : null
  )

  // Enrich battle list: if liveTournament.currentBattleId matches, mark it as running
  const enrichedBattles = battles.map(b => {
    if (b.status === 'pending' && b.id === liveTournament?.currentBattleId) {
      return { ...b, status: 'running' }
    }
    return b
  })

  if (loading) {
    return (
      <div className="ts-page">
        <div className="ts-loading">Loading tournament…</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="ts-page">
        <button className="ts-back" onClick={onClose}>← BACK</button>
        <div className="ts-error">Failed to load: {error}</div>
      </div>
    )
  }

  // Parse players JSON
  let players
  try { players = JSON.parse(meta?.players ?? '[]') } catch { players = [] }

  const progressPct = totalBattles > 0 ? (battlesCompleted / totalBattles) * 100 : 0

  return (
    <div className="ts-page">
      {/* Header */}
      <div className="ts-header">
        <button className="ts-back" onClick={onClose}>← BACK</button>

        <div className="ts-title-group">
          <div className="ts-title">TOURNAMENT #{tournamentId}</div>
          <StatusBadge status={status} />
          <TierBadge tier={tier} className="ts-tier-badge" />
        </div>

        <div className="ts-header-meta">
          <span className="ts-meta-item">{players.length} players</span>
          <span className="ts-meta-sep">·</span>
          <span className="ts-meta-item">{meta?.rounds ?? 1} round{(meta?.rounds ?? 1) !== 1 ? 's' : ''}</span>
          <span className="ts-meta-sep">·</span>
          <span className="ts-meta-item">{battlesCompleted} / {totalBattles} battles</span>
          {meta?.created_at && (
            <>
              <span className="ts-meta-sep">·</span>
              <span className="ts-meta-item ts-meta-date">
                {new Date(meta.created_at).toLocaleDateString()}
              </span>
            </>
          )}
        </div>

        {/* Progress bar */}
        <div className="ts-progress-track">
          <div className="ts-progress-fill" style={{ width: `${progressPct}%` }} />
        </div>

        {/* Watch live button for running tournaments */}
        {status === 'running' && (
          <button className="ts-watch-live-btn" onClick={onWatchLive}>
            ● WATCH LIVE
          </button>
        )}
      </div>

      {/* Now playing banner */}
      <NowPlaying tournament={liveTournament} />

      {/* Tab switcher */}
      <div className="ts-tabs">
        {isBracket && (
          <button
            className={`ts-tab ${activeTab === 'bracket' ? 'active' : ''}`}
            onClick={() => setActiveTab('bracket')}
          >
            BRACKET
          </button>
        )}
        <button
          className={`ts-tab ${activeTab === 'standings' ? 'active' : ''}`}
          onClick={() => setActiveTab('standings')}
        >
          STANDINGS
        </button>
        <button
          className={`ts-tab ${activeTab === 'battles' ? 'active' : ''}`}
          onClick={() => setActiveTab('battles')}
        >
          BATTLES ({battles.length})
        </button>
      </div>

      {/* Content */}
      <div className="ts-content">
        {(activeTab === 'bracket' || (activeTab === null && isBracket)) && isBracket && (
          <BracketView
            bracket={bracketState}
            onReplaySelected={onReplaySelected}
          />
        )}
        {(activeTab === 'standings' || (activeTab === null && !isBracket)) && (
          <div className="ts-standings-wrap">
            <StandingsTable standings={standings} />
            {!isBracket && (
              <div className="ts-pts-note">
                Points: 3 per win · 1 per tie · 0 per loss
              </div>
            )}
          </div>
        )}
        {activeTab === 'battles' && activeTab !== null && (
          <BattleGrid
            battles={enrichedBattles}
            onReplaySelected={onReplaySelected}
          />
        )}
      </div>
    </div>
  )
}

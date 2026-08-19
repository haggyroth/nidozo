import { useState, useEffect, useRef, useCallback } from 'react'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function statusLabel(status) {
  if (status === 'running')   return { text: '● LIVE',      cls: 'season-status-running' }
  if (status === 'completed') return { text: '✓ COMPLETE',  cls: 'season-status-done' }
  if (status === 'cancelled') return { text: '✕ CANCELLED', cls: 'season-status-cancelled' }
  return { text: status?.toUpperCase() ?? '—', cls: '' }
}

function rankBadge(rank) {
  if (rank === 1) return <span className="rank-badge gold">①</span>
  if (rank === 2) return <span className="rank-badge silver">②</span>
  if (rank === 3) return <span className="rank-badge bronze">③</span>
  return <span className="rank-badge">{rank}</span>
}

// ---------------------------------------------------------------------------
// Standings table
// ---------------------------------------------------------------------------

function StandingsTable({ standings }) {
  if (!standings || standings.length === 0) {
    return <div className="empty-state">No results yet — battles in progress</div>
  }
  return (
    <table className="leaderboard-table season-standings-table">
      <thead>
        <tr>
          <th>#</th>
          <th>MODEL</th>
          <th>SEASON RATING</th>
          <th>GAMES</th>
          <th>W / L / T</th>
        </tr>
      </thead>
      <tbody>
        {standings.map(s => (
          <tr key={`${s.provider}/${s.model_name}`}>
            <td>{rankBadge(s.rank)}</td>
            <td>
              <div className="model-name">{s.model_name}</div>
              <div className="provider-tag">{s.provider}</div>
            </td>
            <td>
              <span className="elo-value">{s.elo.toFixed(1)}</span>
              {s.rd != null && (
                <span className="elo-rd" title={`Rating deviation ${s.rd.toFixed(0)}`}>
                  ±{(2 * s.rd).toFixed(0)}
                </span>
              )}
              {s.provisional && (
                <span className="provisional-tag" title="Too few games this season to be confident in this rating yet.">
                  provisional
                </span>
              )}
            </td>
            <td>{s.games}</td>
            <td className="wlt">
              <span className="w">{s.wins}W</span>
              {' / '}
              <span className="l">{s.losses}L</span>
              {' / '}
              {s.ties}T
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// Battle list
// ---------------------------------------------------------------------------

function BattleList({ battles, onReplaySelected }) {
  if (!battles || battles.length === 0) {
    return <div className="empty-state">No battles yet</div>
  }
  return (
    <div className="season-battle-list">
      {battles.map(b => {
        const winnerLabel = b.winner === 1 ? 'p1 wins' : b.winner === 2 ? 'p2 wins' : 'tie'
        const winnerCls   = b.winner === 1 ? 'winner-p1' : b.winner === 2 ? 'winner-p2' : 'winner-tie'
        const isPending   = !b.finished_at

        return (
          <div key={b.id} className={`season-battle-row ${isPending ? 'season-battle-pending' : ''}`}>
            <div className="battle-matchup">
              <span>{b.p1}</span>
              <span className="battle-vs">vs</span>
              <span>{b.p2}</span>
            </div>
            <div className="battle-meta">
              {isPending ? (
                <span className="season-pending-tag">PENDING</span>
              ) : (
                <>
                  <div className={winnerCls}>{winnerLabel}</div>
                  {b.total_turns != null && <div>{b.total_turns} turns</div>}
                  {b.status === 'completed' && (
                    <button
                      className="btn-replay"
                      onClick={() => onReplaySelected?.(b.id)}
                      title="Watch replay"
                    >
                      ▶ REPLAY
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Progress bar
// ---------------------------------------------------------------------------

function ProgressBar({ done, total }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className="season-progress-wrap">
      <div className="season-progress-bar">
        <div className="season-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="season-progress-label">{done}/{total} battles</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main SeasonView component
// ---------------------------------------------------------------------------

export default function SeasonView({
  seasonId,
  season: liveSeasonState,   // from WS hook — standings updated live
  onClose,
  onWatchLive,
  onReplaySelected,
}) {
  const [season, setSeason]       = useState(null)
  const [standings, setStandings] = useState([])
  const [battles, setBattles]     = useState([])
  const [loading, setLoading]     = useState(true)
  const [cancelling, setCancelling] = useState(false)

  const fetchAll = useCallback(async () => {
    try {
      const [s, st, bt] = await Promise.all([
        fetch(`/api/seasons/${seasonId}`).then(r => r.ok ? r.json() : null),
        fetch(`/api/seasons/${seasonId}/standings`).then(r => r.ok ? r.json() : []),
        fetch(`/api/seasons/${seasonId}/battles`).then(r => r.ok ? r.json() : []),
      ])
      setSeason(s)
      setStandings(st)
      setBattles(bt)
    } catch {
      // silently retain stale data
    } finally {
      setLoading(false)
    }
  }, [seasonId])

  useEffect(() => { void Promise.resolve().then(fetchAll) }, [fetchAll])

  // Re-fetch battle list whenever a battle in this season completes (via WS done counter).
  // Standings are derived from liveSeasonState directly in the render — no effect needed.
  const prevDoneRef = useRef(null)
  useEffect(() => {
    if (liveSeasonState?.id !== seasonId) return
    const done = liveSeasonState?.done ?? 0
    if (prevDoneRef.current !== null && done !== prevDoneRef.current) {
      const id = setTimeout(() => void fetchAll(), 800)
      prevDoneRef.current = done
      return () => clearTimeout(id)
    }
    prevDoneRef.current = done
  }, [liveSeasonState?.id, liveSeasonState?.done, fetchAll, seasonId])

  async function handleCancel() {
    if (!confirm('Cancel this season? In-progress battles will be marked failed.')) return
    setCancelling(true)
    try {
      await fetch(`/api/seasons/${seasonId}/cancel`, { method: 'POST' })
      await fetchAll()
    } finally {
      setCancelling(false)
    }
  }

  if (loading) {
    return (
      <div className="tournament-view">
        <div className="panel" style={{ padding: '2rem', textAlign: 'center' }}>
          Loading season…
        </div>
      </div>
    )
  }

  if (!season) {
    return (
      <div className="tournament-view">
        <div className="panel" style={{ padding: '2rem' }}>
          <div>Season #{seasonId} not found.</div>
          <button className="btn-start" style={{ marginTop: '1rem' }} onClick={onClose}>← BACK</button>
        </div>
      </div>
    )
  }

  const { text: stText, cls: stCls } = statusLabel(season.status)
  const liveStandings = (liveSeasonState?.id === seasonId && liveSeasonState.standings)
    ? liveSeasonState.standings
    : standings
  const done  = liveSeasonState?.id === seasonId ? (liveSeasonState.done ?? 0) : 0
  const total = season.total_battles ?? 0

  return (
    <div className="tournament-view">
      {/* Header */}
      <div className="panel season-header-panel">
        <div className="season-header-top">
          <div>
            <div className="season-title">{season.name}</div>
            <div className="season-meta">
              <span className={`season-status ${stCls}`}>{stText}</span>
              {season.tier && season.tier !== 'random' && (
                <span className="tier-badge">{season.tier.toUpperCase()}</span>
              )}
              <span className="th-players">
                {Array.isArray(season.participants) ? season.participants.length : '?'} players
                · {season.rounds} round{season.rounds !== 1 ? 's' : ''}
              </span>
            </div>
          </div>
          <div className="season-header-actions">
            {season.status === 'running' && (
              <>
                <button className="btn-watch-live" onClick={onWatchLive}>▶ WATCH LIVE</button>
                <button
                  className="btn-cancel-tournament"
                  onClick={handleCancel}
                  disabled={cancelling}
                >
                  {cancelling ? 'CANCELLING…' : '■ CANCEL'}
                </button>
              </>
            )}
            <button className="btn-close-tournament" onClick={onClose}>✕ CLOSE</button>
          </div>
        </div>

        {season.status === 'running' && (
          <ProgressBar done={done} total={total} />
        )}
      </div>

      {/* Standings */}
      <div className="panel">
        <div className="panel-title">SEASON STANDINGS</div>
        <div className="season-elo-note">
          Ratings start at 1000 for all participants and are computed with Glicko-2 from
          battles within this season only. ± shows the 95% confidence interval; a rating
          stays <em>provisional</em> until enough games narrow it.
        </div>
        <StandingsTable standings={liveStandings} />
      </div>

      {/* Battles */}
      <div className="panel">
        <div className="panel-title">BATTLES</div>
        <BattleList battles={battles} onReplaySelected={onReplaySelected} />
      </div>
    </div>
  )
}

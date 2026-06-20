// Bake-off experiment detail view (#226). Shows the two variants, the running
// win-rate + statistical-significance verdict, and the battle list. Polls while
// the experiment is still running.

import { useCallback, useEffect, useState } from 'react'

function variantLabel(v) {
  if (!v) return '—'
  return `${v.provider}/${v.model_name} · ${v.prompt_version}`
}

function pct(n) {
  return `${Math.round((n ?? 0) * 100)}%`
}

export default function ExperimentView({ experimentId, onClose, onWatchLive, onReplaySelected }) {
  const [exp, setExp] = useState(null)
  const [battles, setBattles] = useState([])
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      const [e, b] = await Promise.all([
        fetch(`/api/experiments/${experimentId}`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
        fetch(`/api/experiments/${experimentId}/battles`).then(r => r.ok ? r.json() : []),
      ])
      setExp(e)
      setBattles(b)
    } catch (err) {
      setError(String(err))
    }
  }, [experimentId])

  useEffect(() => { void Promise.resolve().then(load) }, [load])

  // Poll while the experiment is still running.
  useEffect(() => {
    if (!exp || exp.status !== 'running') return
    const t = setInterval(() => { void load() }, 3000)
    return () => clearInterval(t)
  }, [exp, load])

  if (error) return (
    <div className="stats-page">
      <button className="stats-back" onClick={onClose}>← BACK</button>
      <div className="stats-error">Failed to load experiment: {error}</div>
    </div>
  )
  if (!exp) return <div className="stats-page"><div className="stats-loading">Loading experiment…</div></div>

  const r = exp.result ?? {}
  const aLabel = variantLabel(exp.variant_a)
  const bLabel = variantLabel(exp.variant_b)
  const done = battles.filter(b => b.status === 'completed').length

  return (
    <div className="stats-page">
      <div className="stats-header">
        <button className="stats-back" onClick={onClose}>← BACK</button>
        <div className="stats-identity">
          <div className="stats-model-name">🧪 {exp.name}</div>
          <div className="stats-provider-row">
            <span className="provider-tag">bake-off · {exp.tier} · {exp.status}</span>
            {exp.status === 'running' && (
              <button className="nav-btn" style={{ marginLeft: '0.5rem' }} onClick={onWatchLive}>WATCH LIVE</button>
            )}
          </div>
        </div>
      </div>

      {/* Result summary */}
      <div className="panel stats-panel">
        <div className="panel-title">RESULT <span className="panel-subtitle">{done}/{exp.n_battles} battles</span></div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 'bold', marginBottom: '0.4rem' }}>
          <span style={{ color: 'var(--p1, #00d4ff)' }}>A · {aLabel}</span>
          <span style={{ color: 'var(--p2, #ffaa00)' }}>B · {bLabel}</span>
        </div>
        {/* Win-rate bar (A share of decided battles) */}
        <div style={{ display: 'flex', height: 28, borderRadius: 4, overflow: 'hidden', border: '1px solid var(--border, #1e3a5f)' }}>
          <div style={{ width: pct(r.win_rate_a ?? 0.5), background: 'var(--p1, #00d4ff)', color: '#001018', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold' }}>
            {r.a_wins ?? 0}
          </div>
          <div style={{ flex: 1, background: 'var(--p2, #ffaa00)', color: '#1a1200', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold' }}>
            {r.b_wins ?? 0}
          </div>
        </div>
        <div style={{ marginTop: '0.6rem', fontSize: '0.9rem' }}>
          {r.n_decided > 0 ? (
            <>
              <div>A win rate: <strong>{pct(r.win_rate_a)}</strong> ({r.a_wins}–{r.b_wins}{r.ties ? `, ${r.ties} tie${r.ties !== 1 ? 's' : ''}` : ''})</div>
              <div>p-value: <strong>{r.p_value}</strong> →{' '}
                <span style={{ color: r.significant ? 'var(--good, #4ade80)' : 'var(--text, #9ca3af)', fontWeight: 'bold' }}>
                  {r.significant ? 'statistically significant (p < 0.05)' : 'not significant — could be chance'}
                </span>
              </div>
            </>
          ) : (
            <div className="panel-subtitle">No decided battles yet.</div>
          )}
        </div>
      </div>

      {/* Battle list */}
      <div className="panel stats-panel">
        <div className="panel-title">BATTLES</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {battles.map((b, i) => (
              <tr key={b.id} style={{ borderTop: '1px solid var(--border, #1e3a5f)', cursor: b.status === 'completed' ? 'pointer' : 'default' }}
                  onClick={() => b.status === 'completed' && onReplaySelected?.(b.id)}>
                <td style={{ opacity: 0.6 }}>#{i + 1}</td>
                <td>{b.p1} <span style={{ opacity: 0.5 }}>vs</span> {b.p2}</td>
                <td style={{ textAlign: 'right' }}>
                  {b.status === 'completed'
                    ? (b.winner === 1 ? 'P1' : b.winner === 2 ? 'P2' : 'tie')
                    : b.status}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

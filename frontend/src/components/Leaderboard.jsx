import { useState, useEffect, useCallback } from 'react'
import BattleAnalysis from './BattleAnalysis'
import EmptyState from './EmptyState'

// ---------------------------------------------------------------------------
// Head-to-head matchup matrix
// ---------------------------------------------------------------------------

function MatchupMatrix({ lbTier }) {
  const [data, setData]         = useState([])
  const [loading, setLoading]   = useState(true)
  const [open, setOpen]         = useState(false)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    async function load() {
      setLoading(true)
      try {
        const url = lbTier && lbTier !== 'all'
          ? `/api/leaderboard/matchups?tier=${lbTier}`
          : '/api/leaderboard/matchups'
        const res = await fetch(url)
        if (!res.ok || cancelled) return
        setData(await res.json())
      } catch {
        // silent — stale data retained
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [open, lbTier])

  // Build sorted list of unique models (row = col order)
  const models = (() => {
    const seen = new Set()
    const list = []
    for (const r of data) {
      const key = `${r.row_provider}::${r.row_model}`
      if (!seen.has(key)) { seen.add(key); list.push({ provider: r.row_provider, model: r.row_model }) }
    }
    return list
  })()

  // Lookup map: "row_model::col_model" → { wins, losses, ties, games }
  const lookup = {}
  for (const r of data) {
    lookup[`${r.row_model}::${r.col_model}`] = r
  }

  function cellContent(rowModel, colModel) {
    if (rowModel === colModel) return null  // diagonal
    const cell = lookup[`${rowModel}::${colModel}`]
    if (!cell || cell.games === 0) return <span className="mm-cell-empty">—</span>
    const wr = cell.wins / cell.games
    const cls = wr > 0.6 ? 'mm-cell-win'
              : wr < 0.4 ? 'mm-cell-loss'
              : 'mm-cell-even'
    return (
      <span className={`mm-cell-record ${cls}`} title={`${cell.wins}W / ${cell.losses}L / ${cell.ties}T`}>
        {cell.wins}–{cell.losses}{cell.ties > 0 ? `–${cell.ties}` : ''}
      </span>
    )
  }

  function shortName(model) {
    // Take last segment after '/' for long lmstudio paths
    return model.split('/').pop()
  }

  return (
    <div className="mm-container">
      <button className="mm-toggle" onClick={() => setOpen(o => !o)}>
        {open ? '▲' : '▼'} HEAD-TO-HEAD MATRIX
      </button>
      {open && (
        <div className="mm-body">
          {loading ? (
            <div className="mm-loading">Loading…</div>
          ) : models.length < 2 ? (
            <div className="mm-empty">Not enough data yet — run some battles first.</div>
          ) : (
            <div className="mm-scroll">
              <table className="mm-table">
                <thead>
                  <tr>
                    <th className="mm-th-corner"></th>
                    {models.map(c => (
                      <th key={c.model} className="mm-th-col" title={c.model}>
                        <span className="mm-col-label">{shortName(c.model)}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {models.map(row => (
                    <tr key={row.model}>
                      <td className="mm-td-row" title={row.model}>{shortName(row.model)}</td>
                      {models.map(col => (
                        <td
                          key={col.model}
                          className={`mm-cell ${row.model === col.model ? 'mm-cell-diag' : ''}`}
                        >
                          {cellContent(row.model, col.model)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="mm-legend">
            <span className="mm-legend-item mm-cell-win">win-rate &gt;60%</span>
            <span className="mm-legend-item mm-cell-even">even (40–60%)</span>
            <span className="mm-legend-item mm-cell-loss">win-rate &lt;40%</span>
            <span className="mm-legend-note">W–L (–T if any ties) from row model's perspective</span>
          </div>
        </div>
      )}
    </div>
  )
}

const PROVIDERS = ['random', 'anthropic', 'openai', 'lmstudio']
const COACH_PROVIDERS = ['none', 'anthropic', 'openai', 'lmstudio']
const PERSONALITIES = [
  { id: 'none',        label: '— none —',              emoji: '' },
  { id: 'aggressive',  label: 'All-out Attacker',       emoji: '⚔️' },
  { id: 'defensive',   label: 'Bulwark',                emoji: '🛡️' },
  { id: 'balanced',    label: 'Adaptive',               emoji: '⚖️' },
  { id: 'trickster',   label: 'Mindgame Specialist',    emoji: '🎭' },
  { id: 'momentum',    label: 'Tempo Player',           emoji: '💨' },
]

const TIERS = [
  { id: 'random',     label: 'Random Battle' },
  { id: 'ou',         label: 'OverUsed (OU)' },
  { id: 'ubers',      label: 'Ubers' },
  { id: 'uu',         label: 'UnderUsed (UU)' },
  { id: 'lc',         label: 'Little Cup (LC)' },
  { id: 'freeforall', label: 'Free-for-All' },
]

const TIER_SHORT = {
  random:     'RND',
  ou:         'OU',
  ubers:      'UBERS',
  uu:         'UU',
  nu:         'NU',
  lc:         'LC',
  freeforall: 'FFA',
}

// Inline tier badge — same palette as BattleField/TournamentView
function TierBadge({ tier }) {
  if (!tier || tier === 'random') return null
  return (
    <span className={`tier-badge tier-badge--${tier}`}>
      {TIER_SHORT[tier] ?? tier.toUpperCase()}
    </span>
  )
}

const STATIC_PRESETS = {
  anthropic: [
    { label: 'Claude Sonnet 4.5', value: 'claude-sonnet-4-5' },
    { label: 'Claude Sonnet 4',   value: 'claude-sonnet-4'   },
    { label: 'Claude Haiku 3.5',  value: 'claude-haiku-3-5'  },
    { label: 'Claude Opus 4',     value: 'claude-opus-4'     },
  ],
  openai: [
    { label: 'GPT-4o mini', value: 'gpt-4o-mini' },
    { label: 'GPT-4o',      value: 'gpt-4o'      },
    { label: 'o4-mini',     value: 'o4-mini'      },
  ],
}

async function fetchLMStudioModels() {
  try {
    const res = await fetch('/api/lmstudio/models')
    if (!res.ok) return []
    return await res.json()
  } catch {
    return []
  }
}

// ---------------------------------------------------------------------------
// Shared model selector component
// ---------------------------------------------------------------------------

function ModelSelector({ label, provider, model, onProviderChange, onModelChange, lmModels, lmLoading }) {
  // Build the model options available for the current provider
  const options = provider === 'lmstudio'
    ? lmModels.map(id => ({ label: id.split('/').pop(), value: id }))
    : (STATIC_PRESETS[provider] || [])

  // Use a dropdown when we have known options; text input as fallback
  const useDropdown = provider !== 'random' && options.length > 0

  return (
    <div className="model-selector">
      <label className="form-label">{label}</label>
      <div className="model-selector-row">
        <select
          className="form-select provider-select"
          value={provider}
          onChange={e => { onProviderChange(e.target.value); onModelChange('') }}
        >
          {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
        </select>

        {provider === 'random' ? (
          <span className="model-random-label">—</span>
        ) : useDropdown ? (
          <select
            className="form-select model-select"
            value={model}
            onChange={e => onModelChange(e.target.value)}
          >
            <option value="">select model…</option>
            {options.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
            <option value="__custom__">custom…</option>
          </select>
        ) : (
          <input
            className="form-input model-input"
            placeholder="model id"
            value={model}
            onChange={e => onModelChange(e.target.value)}
          />
        )}
      </div>

      {/* Custom model text input — shown when "custom…" is selected from dropdown */}
      {useDropdown && model === '__custom__' && (
        <input
          className="form-input model-input"
          placeholder="enter model id…"
          autoFocus
          onChange={e => onModelChange(e.target.value)}
        />
      )}

      {provider === 'lmstudio' && lmLoading && (
        <div className="model-presets-status">querying LM Studio…</div>
      )}
      {provider === 'lmstudio' && !lmLoading && lmModels.length === 0 && (
        <div className="model-presets-status offline">LM Studio offline — enter model id manually</div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Coach selector (compact — shown below each player)
// ---------------------------------------------------------------------------

function CoachSelector({ label, provider, model, onProviderChange, onModelChange, lmModels }) {
  const options = provider === 'lmstudio'
    ? lmModels.map(id => ({ label: id.split('/').pop(), value: id }))
    : (STATIC_PRESETS[provider] || [])
  const useDropdown = provider !== 'none' && options.length > 0

  return (
    <div className="coach-selector">
      <label className="coach-selector-label">🎓 {label} COACH</label>
      <div className="model-selector-row">
        <select
          className="form-select provider-select"
          value={provider}
          onChange={e => { onProviderChange(e.target.value); onModelChange('') }}
        >
          {COACH_PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
        </select>

        {provider === 'none' ? (
          <span className="model-random-label">— no coach —</span>
        ) : useDropdown ? (
          <select
            className="form-select model-select"
            value={model}
            onChange={e => onModelChange(e.target.value)}
          >
            <option value="">select model…</option>
            {options.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
            <option value="__custom__">custom…</option>
          </select>
        ) : (
          <input
            className="form-input model-input"
            placeholder="model id (optional)"
            value={model}
            onChange={e => onModelChange(e.target.value)}
          />
        )}
      </div>
      {useDropdown && model === '__custom__' && (
        <input
          className="form-input model-input"
          placeholder="enter model id…"
          autoFocus
          onChange={e => onModelChange(e.target.value)}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Personality selector (compact — shown below each player)
// ---------------------------------------------------------------------------

function PersonalitySelector({ value, onChange }) {
  return (
    <div className="personality-selector">
      <label className="personality-selector-label">🎭 PLAY STYLE</label>
      <select
        className="form-select personality-select"
        value={value}
        onChange={e => onChange(e.target.value === 'none' ? null : e.target.value)}
      >
        {PERSONALITIES.map(p => (
          <option key={p.id} value={p.id}>
            {p.emoji ? `${p.emoji} ${p.label}` : p.label}
          </option>
        ))}
      </select>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single-battle form
// ---------------------------------------------------------------------------

function BattleForm({ onBattleStarted, lmModels, lmLoading }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [form, setForm] = useState({
    p1_provider: 'lmstudio', p2_provider: 'lmstudio',
    p1_model: '', p2_model: '',
    p1_coach_provider: 'none', p1_coach_model: '',
    p2_coach_provider: 'none', p2_coach_model: '',
    p1_personality: null, p2_personality: null,
    n_battles: 1,
    tier: 'random',
    draft: false,
  })

  // Auto-fill with first two LM Studio models once they load.
  // Deferred to a microtask so setState is not called synchronously inside
  // the effect body (react-hooks/set-state-in-effect).
  useEffect(() => {
    if (lmModels.length === 0) return
    void Promise.resolve().then(() =>
      setForm(f => ({
        ...f,
        p1_model: f.p1_model || lmModels[0] || '',
        p2_model: f.p2_model || lmModels[1] || lmModels[0] || '',
      }))
    )
  }, [lmModels])

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const body = { ...form, n_battles: Number(form.n_battles) }
      if (!body.p1_model || body.p1_provider === 'random') delete body.p1_model
      if (!body.p2_model || body.p2_provider === 'random') delete body.p2_model
      // Coach — omit fields if no coach selected
      if (body.p1_coach_provider === 'none') { delete body.p1_coach_provider; delete body.p1_coach_model }
      if (body.p2_coach_provider === 'none') { delete body.p2_coach_provider; delete body.p2_coach_model }
      if (!body.p1_coach_model) delete body.p1_coach_model
      if (!body.p2_coach_model) delete body.p2_coach_model
      // Personality — omit if none selected
      if (!body.p1_personality) delete body.p1_personality
      if (!body.p2_personality) delete body.p2_personality
      const res = await fetch('/api/battles/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (res.ok) onBattleStarted?.(data)
      else setError(data?.detail ?? 'Failed to start battle — check server logs')
    } catch (err) {
      setError(err.message ?? 'Network error')
    } finally {
      setLoading(false)
    }
  }

  const isDrafted = form.tier !== 'random'

  return (
    <form className="start-form" onSubmit={handleSubmit}>
      <ModelSelector
        label="PLAYER 1"
        provider={form.p1_provider} model={form.p1_model}
        onProviderChange={v => setForm(f => ({ ...f, p1_provider: v }))}
        onModelChange={v => setForm(f => ({ ...f, p1_model: v }))}
        lmModels={lmModels} lmLoading={lmLoading}
      />
      <CoachSelector
        label="P1"
        provider={form.p1_coach_provider} model={form.p1_coach_model}
        onProviderChange={v => setForm(f => ({ ...f, p1_coach_provider: v }))}
        onModelChange={v => setForm(f => ({ ...f, p1_coach_model: v }))}
        lmModels={lmModels}
      />
      <PersonalitySelector
        value={form.p1_personality ?? 'none'}
        onChange={v => setForm(f => ({ ...f, p1_personality: v }))}
      />
      <ModelSelector
        label="PLAYER 2"
        provider={form.p2_provider} model={form.p2_model}
        onProviderChange={v => setForm(f => ({ ...f, p2_provider: v }))}
        onModelChange={v => setForm(f => ({ ...f, p2_model: v }))}
        lmModels={lmModels} lmLoading={lmLoading}
      />
      <CoachSelector
        label="P2"
        provider={form.p2_coach_provider} model={form.p2_coach_model}
        onProviderChange={v => setForm(f => ({ ...f, p2_coach_provider: v }))}
        onModelChange={v => setForm(f => ({ ...f, p2_coach_model: v }))}
        lmModels={lmModels}
      />
      <PersonalitySelector
        value={form.p2_personality ?? 'none'}
        onChange={v => setForm(f => ({ ...f, p2_personality: v }))}
      />
      <div className="form-group">
        <label className="form-label">Tier</label>
        <select
          className="form-select"
          value={form.tier}
          onChange={e => setForm(f => ({ ...f, tier: e.target.value, draft: e.target.value !== 'random' ? f.draft : false }))}
        >
          {TIERS.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      </div>
      {isDrafted && (
        <div className="form-group form-group--inline">
          <label className="form-label">Draft teams (LLM picks)</label>
          <input
            type="checkbox"
            className="form-checkbox"
            checked={form.draft}
            onChange={e => setForm(f => ({ ...f, draft: e.target.checked }))}
          />
        </div>
      )}
      <div className="form-group">
        <label className="form-label">Number of battles</label>
        <input
          className="form-input" type="number" min="1" max="20"
          value={form.n_battles}
          onChange={e => setForm(f => ({ ...f, n_battles: e.target.value }))}
        />
      </div>
      {error && <p className="form-error">{error}</p>}
      <button className="btn-start" type="submit" disabled={loading}>
        {loading ? '▶ STARTING…' : '▶ START BATTLE'}
      </button>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Tournament form  /  Season form  — shared helpers
// ---------------------------------------------------------------------------

const EMPTY_PLAYER = { provider: 'lmstudio', model: '', coach_provider: 'none', coach_model: '' }

const TOURNAMENT_FORMATS = [
  { id: 'round_robin', label: 'Round Robin' },
  { id: 'single_elim', label: 'Single Elimination' },
  { id: 'double_elim', label: 'Double Elimination' },
]

function TournamentForm({ onTournamentStarted, lmModels }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [rounds, setRounds] = useState(3)
  const [tier, setTier] = useState('random')
  const [draft, setDraft] = useState(false)
  const [tournamentFormat, setTournamentFormat] = useState('round_robin')
  const [players, setPlayers] = useState([
    { ...EMPTY_PLAYER },
    { ...EMPTY_PLAYER },
  ])

  // Auto-fill first two with LM Studio models once they load.
  useEffect(() => {
    if (lmModels.length === 0) return
    void Promise.resolve().then(() =>
      setPlayers(prev => prev.map((p, i) => ({
        ...p,
        model: p.model || lmModels[i] || lmModels[0] || '',
      })))
    )
  }, [lmModels])

  function setPlayer(i, field, value) {
    setPlayers(prev => prev.map((p, idx) => {
      if (idx !== i) return p
      const extra = field === 'provider' ? { model: '' }
        : field === 'coach_provider' ? { coach_model: '' }
        : {}
      return { ...p, [field]: value, ...extra }
    }))
  }

  function addPlayer() {
    if (players.length >= 6) return
    setPlayers(prev => [...prev, { ...EMPTY_PLAYER, model: lmModels[prev.length] || '' }])
  }

  function removePlayer(i) {
    if (players.length <= 2) return
    setPlayers(prev => prev.filter((_, idx) => idx !== i))
  }

  const isElim = tournamentFormat === 'single_elim' || tournamentFormat === 'double_elim'
  const totalBattles = (() => {
    const n = players.length
    if (n < 2) return 0
    if (tournamentFormat === 'single_elim') return n - 1
    if (tournamentFormat === 'double_elim') return 2 * n - 1
    return (n * (n - 1)) * rounds
  })()

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const payload = {
        players: players.map(p => {
          const spec = {
            provider: p.provider,
            model: p.provider === 'random' ? null : (p.model || null),
          }
          if (p.coach_provider && p.coach_provider !== 'none') {
            spec.coach_provider = p.coach_provider
            if (p.coach_model) spec.coach_model = p.coach_model
          }
          return spec
        }),
        rounds: isElim ? 1 : Number(rounds),
        tier,
        draft,
        tournament_format: tournamentFormat,
      }
      const res = await fetch('/api/tournament/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json()
      if (res.ok) onTournamentStarted?.(data)
      else setError(data?.detail ?? 'Failed to start tournament — check server logs')
    } catch (err) {
      setError(err.message ?? 'Network error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form className="start-form" onSubmit={handleSubmit}>
      <div className="tournament-players-header">
        <label className="form-label">PLAYERS</label>
        {players.length < 6 && (
          <button type="button" className="btn-add-player" onClick={addPlayer}>+ ADD</button>
        )}
      </div>

      {players.map((p, i) => (
        <div key={i} className="tournament-player-row">
          <div className="tournament-player-label">
            P{i + 1}
            {players.length > 2 && (
              <button type="button" className="btn-remove-player" onClick={() => removePlayer(i)}>✕</button>
            )}
          </div>
          <div className="tournament-player-inputs">
            <select
              className="form-select provider-select"
              value={p.provider}
              onChange={e => setPlayer(i, 'provider', e.target.value)}
            >
              {PROVIDERS.map(pv => <option key={pv} value={pv}>{pv}</option>)}
            </select>
            <input
              className="form-input model-input"
              placeholder={p.provider === 'random' ? '—' : 'model id'}
              value={p.model}
              disabled={p.provider === 'random'}
              onChange={e => setPlayer(i, 'model', e.target.value)}
            />
          </div>
          {p.provider === 'lmstudio' && lmModels.length > 0 && (
            <div className="model-presets" style={{ paddingLeft: '1.8rem' }}>
              {lmModels.slice(0, 4).map(id => (
                <button
                  key={id} type="button"
                  className={`preset-chip ${p.model === id ? 'active' : ''}`}
                  onClick={() => setPlayer(i, 'model', id)}
                  title={id}
                >
                  {id.split('/').pop()}
                </button>
              ))}
            </div>
          )}
          <CoachSelector
            label={`P${i + 1}`}
            provider={p.coach_provider}
            model={p.coach_model}
            onProviderChange={v => setPlayer(i, 'coach_provider', v)}
            onModelChange={v => setPlayer(i, 'coach_model', v)}
            lmModels={lmModels}
          />
        </div>
      ))}

      <div className="form-group">
        <label className="form-label">Format</label>
        <div className="format-chips">
          {TOURNAMENT_FORMATS.map(f => (
            <button
              key={f.id}
              type="button"
              className={`format-chip ${tournamentFormat === f.id ? 'active' : ''}`}
              onClick={() => setTournamentFormat(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">Tier</label>
        <select
          className="form-select"
          value={tier}
          onChange={e => { setTier(e.target.value); if (e.target.value === 'random') setDraft(false) }}
        >
          {TIERS.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      </div>
      {tier !== 'random' && (
        <div className="form-group form-group--inline">
          <label className="form-label">Draft teams (LLM picks)</label>
          <input
            type="checkbox"
            className="form-checkbox"
            checked={draft}
            onChange={e => setDraft(e.target.checked)}
          />
        </div>
      )}
      {!isElim && (
        <div className="form-group">
          <label className="form-label">Rounds per matchup</label>
          <input
            className="form-input" type="number" min="1" max="10"
            value={rounds}
            onChange={e => setRounds(e.target.value)}
          />
        </div>
      )}
      {totalBattles > 0 && (
        <div className="tournament-battle-count">
          ~{totalBattles} battle{totalBattles !== 1 ? 's' : ''} total
        </div>
      )}

      {error && <p className="form-error">{error}</p>}
      <button className="btn-start" type="submit" disabled={loading || players.length < 2}>
        {loading ? '⚔ STARTING…' : `⚔ START TOURNAMENT`}
      </button>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Season form
// ---------------------------------------------------------------------------

function SeasonForm({ onSeasonStarted, lmModels }) {
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)
  const [name, setName]       = useState('')
  const [rounds, setRounds]   = useState(2)
  const [tier, setTier]       = useState('random')
  const [draft, setDraft]     = useState(false)
  const [players, setPlayers] = useState([
    { ...EMPTY_PLAYER },
    { ...EMPTY_PLAYER },
  ])

  useEffect(() => {
    if (lmModels.length === 0) return
    void Promise.resolve().then(() =>
      setPlayers(prev => prev.map((p, i) => ({
        ...p,
        model: p.model || lmModels[i] || lmModels[0] || '',
      })))
    )
  }, [lmModels])

  function setPlayer(i, field, value) {
    setPlayers(prev => prev.map((p, idx) => {
      if (idx !== i) return p
      const extra = field === 'provider' ? { model: '' }
        : field === 'coach_provider' ? { coach_model: '' }
        : {}
      return { ...p, [field]: value, ...extra }
    }))
  }

  function addPlayer() {
    if (players.length >= 8) return
    setPlayers(prev => [...prev, { ...EMPTY_PLAYER, model: lmModels[prev.length] || '' }])
  }

  function removePlayer(i) {
    if (players.length <= 2) return
    setPlayers(prev => prev.filter((_, idx) => idx !== i))
  }

  const n = players.length
  const totalBattles = n >= 2 ? (n * (n - 1)) * rounds : 0

  async function handleSubmit(e) {
    e.preventDefault()
    if (!name.trim()) return
    setLoading(true)
    setError(null)
    try {
      const payload = {
        name: name.trim(),
        players: players.map(p => ({
          provider: p.provider,
          model: p.provider === 'random' ? null : (p.model || null),
          ...(p.coach_provider && p.coach_provider !== 'none' ? {
            coach_provider: p.coach_provider,
            ...(p.coach_model ? { coach_model: p.coach_model } : {}),
          } : {}),
        })),
        rounds: Number(rounds),
        tier,
        draft,
      }
      const res = await fetch('/api/seasons/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json()
      if (res.ok) onSeasonStarted?.(data)
      else setError(data?.detail ?? 'Failed to start season — check server logs')
    } catch (err) {
      setError(err.message ?? 'Network error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form className="start-form" onSubmit={handleSubmit}>
      <div className="form-group">
        <label className="form-label">Season name</label>
        <input
          className="form-input"
          placeholder="e.g. Season 1 — Gen 3 OU"
          value={name}
          onChange={e => setName(e.target.value)}
          maxLength={100}
          required
        />
      </div>

      <div className="tournament-players-header">
        <label className="form-label">PLAYERS</label>
        {players.length < 8 && (
          <button type="button" className="btn-add-player" onClick={addPlayer}>+ ADD</button>
        )}
      </div>

      {players.map((p, i) => (
        <div key={i} className="tournament-player-row">
          <div className="tournament-player-label">
            P{i + 1}
            {players.length > 2 && (
              <button type="button" className="btn-remove-player" onClick={() => removePlayer(i)}>✕</button>
            )}
          </div>
          <div className="tournament-player-inputs">
            <select
              className="form-select provider-select"
              value={p.provider}
              onChange={e => setPlayer(i, 'provider', e.target.value)}
            >
              {PROVIDERS.map(pv => <option key={pv} value={pv}>{pv}</option>)}
            </select>
            <input
              className="form-input model-input"
              placeholder={p.provider === 'random' ? '—' : 'model id'}
              value={p.model}
              disabled={p.provider === 'random'}
              onChange={e => setPlayer(i, 'model', e.target.value)}
            />
          </div>
          {p.provider === 'lmstudio' && lmModels.length > 0 && (
            <div className="model-presets" style={{ paddingLeft: '1.8rem' }}>
              {lmModels.slice(0, 4).map(id => (
                <button
                  key={id} type="button"
                  className={`preset-chip ${p.model === id ? 'active' : ''}`}
                  onClick={() => setPlayer(i, 'model', id)}
                  title={id}
                >
                  {id.split('/').pop()}
                </button>
              ))}
            </div>
          )}
          <CoachSelector
            label={`P${i + 1}`}
            provider={p.coach_provider}
            model={p.coach_model}
            onProviderChange={v => setPlayer(i, 'coach_provider', v)}
            onModelChange={v => setPlayer(i, 'coach_model', v)}
            lmModels={lmModels}
          />
        </div>
      ))}

      <div className="form-group">
        <label className="form-label">Tier</label>
        <select
          className="form-select"
          value={tier}
          onChange={e => { setTier(e.target.value); if (e.target.value === 'random') setDraft(false) }}
        >
          {TIERS.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      </div>
      {tier !== 'random' && (
        <div className="form-group form-group--inline">
          <label className="form-label">Draft teams (LLM picks)</label>
          <input
            type="checkbox"
            className="form-checkbox"
            checked={draft}
            onChange={e => setDraft(e.target.checked)}
          />
        </div>
      )}
      <div className="form-group">
        <label className="form-label">Rounds per matchup</label>
        <input
          className="form-input" type="number" min="1" max="10"
          value={rounds}
          onChange={e => setRounds(e.target.value)}
        />
      </div>

      {totalBattles > 0 && (
        <div className="tournament-battle-count">
          ~{totalBattles} battle{totalBattles !== 1 ? 's' : ''} total
        </div>
      )}

      {error && <p className="form-error">{error}</p>}
      <button className="btn-start" type="submit" disabled={loading || players.length < 2 || !name.trim()}>
        {loading ? '🏆 STARTING…' : '🏆 START SEASON'}
      </button>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Main Leaderboard component
// ---------------------------------------------------------------------------

const LB_TIERS = [
  { id: 'all',        label: 'All' },
  { id: 'random',     label: 'Random' },
  { id: 'ou',         label: 'OU' },
  { id: 'ubers',      label: 'Ubers' },
  { id: 'uu',         label: 'UU' },
  { id: 'nu',         label: 'NU' },
  { id: 'lc',         label: 'LC' },
  { id: 'freeforall', label: 'FFA' },
]

export default function Leaderboard({ onBattleStarted, onTournamentStarted, onSeasonStarted, onReplaySelected, onModelSelected, onTournamentSelected, onSeasonSelected }) {
  const [rows, setRows]               = useState([])
  const [battles, setBattles]         = useState([])
  const [tournaments, setTournaments] = useState([])
  const [seasons, setSeasons]         = useState([])
  const [analyzing, setAnalyzing]     = useState(null)
  const [lmModels, setLmModels]       = useState([])
  const [lmLoading, setLmLoading]     = useState(true)
  const [formTab, setFormTab]         = useState('battle')   // 'battle' | 'tournament' | 'season'
  const [lbTier, setLbTier]          = useState('all')       // leaderboard tier filter
  const [lbSearch, setLbSearch]      = useState('')          // model name/provider filter
  const [copyFlash, setCopyFlash]    = useState(null)        // model_id that was just copied

  useEffect(() => {
    let cancelled = false
    async function loadModels() {
      setLmLoading(true)
      const models = await fetchLMStudioModels()
      if (cancelled) return
      setLmModels(models)
      setLmLoading(false)
    }
    loadModels()
    return () => { cancelled = true }
  }, [])

  const fetchData = useCallback(async () => {
    try {
      const lbUrl = lbTier === 'all' ? '/api/leaderboard' : `/api/leaderboard?tier=${lbTier}`
      const [lb, bt, ts, ss] = await Promise.all([
        fetch(lbUrl).then(r => r.json()),
        fetch('/api/battles').then(r => r.json()),
        fetch('/api/tournaments?limit=8').then(r => r.ok ? r.json() : []).catch(() => []),
        fetch('/api/seasons?limit=8').then(r => r.ok ? r.json() : []).catch(() => []),
      ])
      setRows(lb)
      setBattles(bt)
      setTournaments(ts)
      setSeasons(ss)
    } catch {
      // network errors are silently swallowed; UI retains stale data
    }
  }, [lbTier])

  useEffect(() => {
    void Promise.resolve().then(fetchData)
    const id = setInterval(fetchData, 30000)
    return () => clearInterval(id)
  }, [fetchData])

  function rankBadge(i) {
    if (i === 0) return <span className="rank-badge gold">①</span>
    if (i === 1) return <span className="rank-badge silver">②</span>
    if (i === 2) return <span className="rank-badge bronze">③</span>
    return <span className="rank-badge">{i + 1}</span>
  }

  function copyModelId(r) {
    const text = `${r.provider}/${r.model_name}`
    navigator.clipboard.writeText(text).then(() => {
      setCopyFlash(r.model_id)
      setTimeout(() => setCopyFlash(null), 1500)
    }).catch(() => {})
  }

  return (
    <div className="home-grid">
      {/* Left: leaderboard + recent battles */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        <div className="panel">
          <div className="panel-title-row">
            <div className="panel-title">ELO LEADERBOARD</div>
            <div className="lb-tier-tabs">
              {LB_TIERS.map(t => (
                <button
                  key={t.id}
                  className={`lb-tier-tab ${lbTier === t.id ? 'active' : ''}`}
                  onClick={() => setLbTier(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          <div className="lb-search-row">
            <input
              className="lb-search-input"
              type="text"
              placeholder="search models…"
              value={lbSearch}
              onChange={e => setLbSearch(e.target.value)}
              spellCheck={false}
            />
            {lbSearch && (
              <button className="lb-search-clear" onClick={() => setLbSearch('')} title="Clear">✕</button>
            )}
          </div>
          {(() => {
            const q = lbSearch.trim().toLowerCase()
            const filtered = q
              ? rows.filter(r =>
                  r.model_name?.toLowerCase().includes(q) ||
                  r.provider?.toLowerCase().includes(q))
              : rows
            if (filtered.length === 0) {
              if (q) return (
                <EmptyState compact icon="🔍" title="No matching models"
                  hint="Try a different name or provider." />
              )
              return (
                <EmptyState icon="🏆"
                  title={lbTier === 'all' ? 'No battles yet' : `No ${lbTier.toUpperCase()} battles yet`}
                  hint="Start a battle below to put models on the leaderboard." />
              )
            }
            return (
              <table className="leaderboard-table">
                <thead>
                  <tr>
                    <th>#</th><th>MODEL</th><th>ELO</th><th>GAMES</th><th>W / L / T</th><th>STREAK</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r, i) => (
                    <tr key={i}>
                      <td>{rankBadge(i)}</td>
                      <td>
                        <div className="model-name-cell">
                          <div className="model-name">{r.model_name}</div>
                          <button
                            className={`btn-copy${copyFlash === r.model_id ? ' copied' : ''}`}
                            onClick={() => copyModelId(r)}
                            title={`Copy ${r.provider}/${r.model_name}`}
                          >
                            {copyFlash === r.model_id ? '✓' : '⎘'}
                          </button>
                        </div>
                        <div className="provider-tag">
                          {r.provider}
                          {r.versions && (
                            <span className="version-tags">
                              {r.versions.split(',').map(v => (
                                <span key={v} className="version-tag">{v}</span>
                              ))}
                            </span>
                          )}
                        </div>
                      </td>
                      <td><span className="elo-value">{r.rating.toFixed(1)}</span></td>
                      <td>{r.games}</td>
                      <td className="wlt">
                        <span className="w">{r.wins}W</span>
                        {' / '}
                        <span className="l">{r.losses}L</span>
                        {' / '}
                        {r.ties}T
                      </td>
                      <td>
                        {r.streak > 0 ? (
                          <span className={`streak-badge${r.streak >= 3 ? ' streak-badge--hot' : ''}`}>
                            🔥{r.streak}
                          </span>
                        ) : (
                          <span className="streak-badge streak-badge--cold">—</span>
                        )}
                      </td>
                      <td>
                        {r.model_id != null && (
                          <button
                            className="btn-stats"
                            onClick={() => onModelSelected?.(r.model_id)}
                            title="View model stats"
                          >
                            STATS →
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          })()}
          <MatchupMatrix lbTier={lbTier} />
        </div>

        <div className="panel">
          <div className="panel-title">RECENT BATTLES</div>
          {battles.length === 0 ? (
            <EmptyState compact icon="⚔" title="No battles yet"
              hint="Completed battles will appear here." />
          ) : (
            battles.map((b, i) => {
              const winnerCls   = b.winner === 1 ? 'winner-p1' : b.winner === 2 ? 'winner-p2' : 'winner-tie'
              const winnerLabel = b.winner === 1 ? 'p1 wins' : b.winner === 2 ? 'p2 wins' : 'tie'
              const isOpen = analyzing === b.id
              return (
                <div key={i} className="battle-entry-wrap">
                  <div className="battle-entry">
                    <div className="battle-matchup">
                      <span>{b.p1}</span>
                      <span className="battle-vs">vs</span>
                      <span>{b.p2}</span>
                    </div>
                    <div className="battle-meta">
                      <div className={winnerCls}>{winnerLabel}</div>
                      <div>{b.total_turns ?? '?'} turns</div>
                      {b.tier && b.tier !== 'random' && <TierBadge tier={b.tier} />}
                      {b.drafted ? <span className="battle-drafted-tag">DRAFT</span> : null}
                      <button
                        className={`btn-analyze ${isOpen ? 'active' : ''}`}
                        onClick={() => setAnalyzing(isOpen ? null : b.id)}
                      >
                        {isOpen ? '▲ HIDE' : '▼ ANALYZE'}
                      </button>
                      {b.status === 'completed' && (
                        <button
                          className="btn-replay"
                          onClick={() => onReplaySelected?.(b.id)}
                          title="Watch replay"
                        >
                          ▶ REPLAY
                        </button>
                      )}
                    </div>
                  </div>
                  {isOpen && (
                    <BattleAnalysis
                      battleId={b.id}
                      p1Label={b.p1?.split('/').pop() ?? 'P1'}
                      p2Label={b.p2?.split('/').pop() ?? 'P2'}
                    />
                  )}
                </div>
              )
            })
          )}
        </div>

        {/* Tournament history */}
        {tournaments.length > 0 && (
          <div className="panel">
            <div className="panel-title">RECENT TOURNAMENTS</div>
            <div className="tournament-history-list">
              {tournaments.map(t => {
                let players
                try { players = JSON.parse(t.players ?? '[]') } catch { players = [] }
                const statusCls = t.status === 'completed' ? 'th-done'
                                : t.status === 'cancelled' ? 'th-cancelled'
                                : 'th-running'
                return (
                  <div key={t.id} className="tournament-history-row">
                    <div className="th-left">
                      <span className={`th-status ${statusCls}`}>
                        {t.status === 'running' ? '●' : t.status === 'completed' ? '✓' : '✕'}
                      </span>
                      <div className="th-info">
                        <span className="th-id">#{t.id}</span>
                        {t.tier && t.tier !== 'random' && <TierBadge tier={t.tier} />}
                        <span className="th-players">{players.length} players · {t.rounds} round{t.rounds !== 1 ? 's' : ''}</span>
                        <span className="th-progress">{t.battles_completed}/{t.total_battles} battles</span>
                      </div>
                    </div>
                    <div className="th-right">
                      <span className="th-date">
                        {t.created_at ? new Date(t.created_at).toLocaleDateString() : ''}
                      </span>
                      <button
                        className="btn-stats"
                        onClick={() => onTournamentSelected?.(t.id)}
                      >
                        SCORES →
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Season history */}
        {seasons.length > 0 && (
          <div className="panel">
            <div className="panel-title">SEASONS</div>
            <div className="tournament-history-list">
              {seasons.map(s => {
                const statusCls = s.status === 'completed' ? 'th-done'
                                : s.status === 'cancelled' ? 'th-cancelled'
                                : 'th-running'
                const pct = s.total_battles > 0
                  ? Math.round((s.battles_done / s.total_battles) * 100)
                  : 0
                return (
                  <div key={s.id} className="tournament-history-row season-history-row">
                    <div className="th-left">
                      <span className={`th-status ${statusCls}`}>
                        {s.status === 'running' ? '●' : s.status === 'completed' ? '✓' : '✕'}
                      </span>
                      <div className="th-info">
                        <span className="season-history-name">{s.name}</span>
                        {s.tier && s.tier !== 'random' && <TierBadge tier={s.tier} />}
                        <span className="th-progress">
                          {s.battles_done}/{s.total_battles} battles
                          {s.status === 'running' && ` (${pct}%)`}
                        </span>
                      </div>
                    </div>
                    <div className="th-right">
                      <span className="th-date">
                        {s.created_at ? new Date(s.created_at).toLocaleDateString() : ''}
                      </span>
                      <button
                        className="btn-stats"
                        onClick={() => onSeasonSelected?.(s.id)}
                      >
                        STANDINGS →
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* Right: tabbed battle / tournament form */}
      <div className="panel">
        <div className="form-tabs">
          <button
            className={`form-tab ${formTab === 'battle' ? 'active' : ''}`}
            onClick={() => setFormTab('battle')}
          >
            ▶ BATTLE
          </button>
          <button
            className={`form-tab ${formTab === 'tournament' ? 'active' : ''}`}
            onClick={() => setFormTab('tournament')}
          >
            ⚔ TOURNAMENT
          </button>
          <button
            className={`form-tab ${formTab === 'season' ? 'active' : ''}`}
            onClick={() => setFormTab('season')}
          >
            🏆 SEASON
          </button>
        </div>

        {formTab === 'battle' && (
          <BattleForm
            onBattleStarted={onBattleStarted}
            lmModels={lmModels}
            lmLoading={lmLoading}
          />
        )}
        {formTab === 'tournament' && (
          <TournamentForm
            onTournamentStarted={onTournamentStarted}
            lmModels={lmModels}
            lmLoading={lmLoading}
          />
        )}
        {formTab === 'season' && (
          <SeasonForm
            onSeasonStarted={onSeasonStarted}
            lmModels={lmModels}
          />
        )}
      </div>
    </div>
  )
}

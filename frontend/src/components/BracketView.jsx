/**
 * BracketView — renders single-elim and double-elim tournament brackets.
 *
 * Data shape (bracket_state from backend):
 *   format: 'single_elim' | 'double_elim'
 *   seeds: { "1": {seed, provider, model_name}, ... }
 *   match_index: { "WR1-1": BracketMatch, ... }
 *   champion_seed: number | null
 *
 *   single_elim: rounds: [{round_num, bracket:"winners", matches:[...]}]
 *   double_elim: wb_rounds_list, lb_rounds_list, gf_list
 */


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function shortName(player) {
  if (!player) return '?'
  return player.model_name?.split('/').pop() ?? player.model_name ?? player.provider ?? '?'
}

function resolvePlayer(seeds, seed) {
  if (seed == null) return null
  return seeds?.[String(seed)] ?? null
}

function matchStatusClass(match) {
  if (!match) return ''
  if (match.status === 'bye') return 'bm-bye'
  if (match.status === 'running') return 'bm-running'
  if (match.status === 'completed') return 'bm-done'
  if (match.status === 'void') return 'bm-void'
  return 'bm-pending'
}

// ---------------------------------------------------------------------------
// Single match card
// ---------------------------------------------------------------------------

function BracketMatch({ match, seeds, onReplaySelected }) {
  if (!match || match.status === 'void') return null

  const p1 = resolvePlayer(seeds, match.p1_seed)
  const p2 = resolvePlayer(seeds, match.p2_seed)
  const isBye = match.status === 'bye'
  const isDone = match.status === 'completed'
  const isLive = match.status === 'running'

  const p1Won = isDone && match.winner_seed === match.p1_seed
  const p2Won = isDone && match.winner_seed === match.p2_seed

  return (
    <div className={`bracket-match ${matchStatusClass(match)}`}>
      <div className={`bm-slot bm-slot-p1 ${p1Won ? 'bm-winner' : (isDone ? 'bm-loser' : '')}`}>
        <span className="bm-seed">{match.p1_seed ?? '?'}</span>
        <span className="bm-name">{p1 ? shortName(p1) : (isBye ? 'BYE' : 'TBD')}</span>
        {p1Won && <span className="bm-crown">✓</span>}
      </div>
      <div className={`bm-slot bm-slot-p2 ${p2Won ? 'bm-winner' : (isDone ? 'bm-loser' : '')}`}>
        <span className="bm-seed">{match.p2_seed ?? '?'}</span>
        <span className="bm-name">{p2 ? shortName(p2) : (isBye ? 'BYE' : 'TBD')}</span>
        {p2Won && <span className="bm-crown">✓</span>}
      </div>
      <div className="bm-footer">
        <span className="bm-id">{match.match_id}</span>
        {isLive && <span className="bm-live-dot" title="Live" />}
        {isDone && match.battle_id && onReplaySelected && (
          <button
            className="bm-replay-btn"
            onClick={() => onReplaySelected(match.battle_id)}
            title="Watch replay"
          >▶</button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// A column of matches for one round
// ---------------------------------------------------------------------------

function BracketRound({ roundLabel, matches, seeds, onReplaySelected }) {
  if (!matches?.length) return null
  const visible = matches.filter(m => m.status !== 'void')
  if (!visible.length) return null
  return (
    <div className="bracket-round">
      <div className="bracket-round-label">{roundLabel}</div>
      <div className="bracket-round-matches">
        {visible.map(m => (
          <BracketMatch
            key={m.match_id}
            match={m}
            seeds={seeds}
            onReplaySelected={onReplaySelected}
          />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Champion banner
// ---------------------------------------------------------------------------

function ChampionBanner({ seeds, championSeed }) {
  if (!championSeed) return null
  const p = resolvePlayer(seeds, championSeed)
  return (
    <div className="bracket-champion">
      <span className="bracket-champion-icon">🏆</span>
      <span className="bracket-champion-name">{p ? shortName(p) : `Seed ${championSeed}`}</span>
      <span className="bracket-champion-label">CHAMPION</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single elimination layout
// ---------------------------------------------------------------------------

function SingleElimBracket({ state, onReplaySelected }) {
  const { seeds, rounds, champion_seed: championSeed } = state
  const numRounds = rounds?.length ?? 0

  return (
    <div className="bracket-container bracket-single">
      <div className="bracket-track">
        {rounds.map((r, i) => {
          const isLast = i === numRounds - 1
          const label = isLast ? 'FINAL'
            : numRounds - i === 2 ? 'SEMI-FINAL'
            : numRounds - i === 3 ? 'QUARTER-FINAL'
            : `ROUND ${r.round_num}`
          return (
            <BracketRound
              key={r.round_num}
              roundLabel={label}
              matches={r.matches}
              seeds={seeds}
              onReplaySelected={onReplaySelected}
            />
          )
        })}
        <ChampionBanner seeds={seeds} championSeed={championSeed} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Double elimination layout
// ---------------------------------------------------------------------------

function DoubleElimBracket({ state, onReplaySelected }) {
  const {
    seeds, champion_seed: championSeed,
    wb_rounds_list: wbRounds,
    lb_rounds_list: lbRounds,
    match_index: matchIndex,
  } = state

  const wbCount = wbRounds?.length ?? 0
  const lbCount = lbRounds?.length ?? 0

  function wbLabel(rnd, idx) {
    const total = wbCount
    if (idx === total - 1) return 'WB FINAL'
    if (total - idx === 2) return 'WB SEMI'
    return `WB ROUND ${rnd.round_num}`
  }

  function lbLabel(rnd) {
    if (rnd.round_num === lbCount) return 'LB FINAL'
    return `LB ROUND ${rnd.round_num}`
  }

  const gfMatch = matchIndex?.['GF']
  const gfrMatch = matchIndex?.['GFR']

  return (
    <div className="bracket-container bracket-double">
      {/* Winners bracket */}
      <div className="bracket-section-label">WINNERS BRACKET</div>
      <div className="bracket-track">
        {wbRounds?.map((r, i) => (
          <BracketRound
            key={`wb-${r.round_num}`}
            roundLabel={wbLabel(r, i)}
            matches={r.matches}
            seeds={seeds}
            onReplaySelected={onReplaySelected}
          />
        ))}
      </div>

      {/* Losers bracket — a 2-player bracket has none (lb_rounds === 0) */}
      {lbCount > 0 && (
        <>
          <div className="bracket-section-label bracket-section-label--losers">LOSERS BRACKET</div>
          <div className="bracket-track bracket-track--losers">
            {lbRounds?.map(r => (
              <BracketRound
                key={`lb-${r.round_num}`}
                roundLabel={lbLabel(r)}
                matches={r.matches}
                seeds={seeds}
                onReplaySelected={onReplaySelected}
              />
            ))}
          </div>
        </>
      )}

      {/* Grand Final */}
      <div className="bracket-section-label bracket-section-label--gf">GRAND FINAL</div>
      <div className="bracket-track bracket-track--gf">
        {gfMatch && (
          <BracketRound
            roundLabel="GRAND FINAL"
            matches={[gfMatch]}
            seeds={seeds}
            onReplaySelected={onReplaySelected}
          />
        )}
        {gfrMatch && gfrMatch.status !== 'void' && gfrMatch.status !== 'pending' && (
          <BracketRound
            roundLabel="BRACKET RESET"
            matches={[gfrMatch]}
            seeds={seeds}
            onReplaySelected={onReplaySelected}
          />
        )}
      </div>

      <ChampionBanner seeds={seeds} championSeed={championSeed} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

export default function BracketView({ bracket, onReplaySelected }) {
  if (!bracket) {
    return (
      <div className="bracket-empty">
        <div className="bracket-empty-msg">Bracket loading…</div>
      </div>
    )
  }

  if (bracket.format === 'single_elim') {
    return <SingleElimBracket state={bracket} onReplaySelected={onReplaySelected} />
  }
  if (bracket.format === 'double_elim') {
    return <DoubleElimBracket state={bracket} onReplaySelected={onReplaySelected} />
  }

  return <div className="bracket-empty"><div className="bracket-empty-msg">Unknown bracket format.</div></div>
}

import PokemonCard, { BenchSlot } from './PokemonCard'
import BattleLog from './BattleLog'
import { WinProbBar, PlayerLabel, PlayerHeuristicPanels, ThinkingBadge, PersonalityChip, PresetBadge } from './battleShared'
import { BattleBadges, CancelBattleButton } from './battleChrome'

/**
 * Classic battle stage — card-based arena. Handles both singles (one active
 * mon per side) and doubles (two active slots per side).
 *
 * Lifecycle chrome (tournament bar, winner banner, tournament-end overlay)
 * is owned by App.jsx and wraps both this and the Showdown cockpit.
 */
export default function BattleField({
  p1State, p2State, battleInfo, battleResult, events, thinking, coachThinking,
}) {
  const isDoubles = p1State?.state?.is_doubles ?? p2State?.state?.is_doubles ?? false

  // --- Singles helpers ---
  const p1Mon   = !isDoubles ? (p1State?.state?.my_active ?? null) : null
  const oppOfP1 = !isDoubles ? (p1State?.state?.opponent_active ?? null) : null
  const p2Mon   = !isDoubles ? (p2State?.state?.my_active ?? null) : null

  // --- Doubles helpers ---
  // my_active is List[mon|null] (slot 0, slot 1); my_team is bench-only
  const p1Slots    = isDoubles ? (p1State?.state?.my_active ?? []) : []
  const p1OppSlots = isDoubles ? (p1State?.state?.opponent_active ?? []) : []
  const p2Slots    = isDoubles ? (p2State?.state?.my_active ?? []) : []

  const weather = p1State?.state?.weather ?? p2State?.state?.weather ?? null
  const turn    = Math.max(p1State?.turn ?? 0, p2State?.turn ?? 0)

  // Bench: in doubles my_team is already bench-only; in singles filter out active
  const p1Bench = isDoubles
    ? (p1State?.state?.my_team ?? [])
    : (p1State?.state?.my_team ?? []).filter(m => m.species !== p1Mon?.species)
  const p2Bench = isDoubles
    ? (p2State?.state?.my_team ?? [])
    : (p2State?.state?.my_team ?? []).filter(m => m.species !== (p2Mon ?? oppOfP1)?.species)

  const lastState = (p1State?.turn ?? 0) >= (p2State?.turn ?? 0)
    ? p1State?.state
    : p2State?.state

  const isLive          = battleInfo && !battleResult
  const currentBattleId = battleInfo?.battle_id
  const battleTier      = battleInfo?.tier
  const battleDrafted   = battleInfo?.drafted

  const p1Thinking = thinking === 'p1' || coachThinking === 'p1'
  const p2Thinking = thinking === 'p2' || coachThinking === 'p2'

  // For doubles P2 display: use P2's own perspective per slot; fall back to
  // what P1 sees as the opponent when P2 state hasn't arrived yet.
  const getP2DisplaySlot = (i) => p2Slots[i] ?? p1OppSlots[i] ?? null
  const p2UsingOpponentView = p2Slots.length === 0

  // Hide heuristic drawer when no heuristics exist (avoid empty grid column)
  const hasHeuristics = !!lastState?.heuristics

  return (
    <div className="battlefield-wrapper">
      {/* Header row */}
      <div className="battle-header">
        <div className="turn-counter">
          {turn > 0 ? `TURN ${turn}` : 'READY'}
        </div>
        <div className="battle-header-center">
          <BattleBadges tier={battleTier} drafted={battleDrafted} />
          {isDoubles && <span className="doubles-badge">2v2</span>}
          {weather && <div className="weather-badge">🌤 {weather}</div>}
          <ThinkingBadge role={coachThinking || thinking} isCoach={!!coachThinking} />
        </div>
        <div className="battle-header-right">
          <div className="battle-status-text">
            {battleInfo
              ? `${battleInfo.p1} vs ${battleInfo.p2}`
              : 'Waiting for battle…'}
          </div>
          {isLive && <CancelBattleButton battleId={currentBattleId} />}
        </div>
      </div>

      {/* Win-probability bar */}
      <WinProbBar
        p1State={p1State}
        p2State={p2State}
        p1Label={battleInfo?.p1}
        p2Label={battleInfo?.p2}
      />

      {/* Arena */}
      <div className="arena">
        {/* P1 column */}
        <div className="arena-player-col">
          <PlayerLabel label={battleInfo?.p1} side="p1" />
          <PersonalityChip personality={battleInfo?.p1_personality} />
          <PresetBadge presetSlug={battleInfo?.p1_preset} />

          {isDoubles ? (
            <>
              <div className="doubles-pair">
                <PokemonCard mon={p1Slots[0] ?? null} side="p1" isThinking={p1Thinking} compact />
                <PokemonCard mon={p1Slots[1] ?? null} side="p1" isThinking={p1Thinking} compact />
              </div>
              {p1Bench.length > 0 && (
                <div className="bench-row">
                  {p1Bench.map((b, i) => <BenchSlot key={i} mon={b} />)}
                </div>
              )}
            </>
          ) : (
            <PokemonCard
              mon={p1Mon}
              side="p1"
              isThinking={p1Thinking}
              bench={p1Bench}
            />
          )}
        </div>

        <div className="vs-divider">VS</div>

        {/* P2 column */}
        <div className="arena-player-col">
          <PlayerLabel label={battleInfo?.p2} side="p2" />
          <PersonalityChip personality={battleInfo?.p2_personality} />
          <PresetBadge presetSlug={battleInfo?.p2_preset} />

          {isDoubles ? (
            <>
              <div className="doubles-pair">
                <PokemonCard mon={getP2DisplaySlot(0)} side="p2" isOpponent={p2UsingOpponentView} isThinking={p2Thinking} compact />
                <PokemonCard mon={getP2DisplaySlot(1)} side="p2" isOpponent={p2UsingOpponentView} isThinking={p2Thinking} compact />
              </div>
              {p2Bench.length > 0 && (
                <div className="bench-row">
                  {p2Bench.map((b, i) => <BenchSlot key={i} mon={b} />)}
                </div>
              )}
            </>
          ) : (
            <PokemonCard
              mon={p2Mon ?? oppOfP1}
              side="p2"
              isOpponent={!p2Mon}
              isThinking={p2Thinking}
              bench={p2Bench}
            />
          )}
        </div>
      </div>

      {/* Bottom panels */}
      <div
        className="bottom-panels"
        style={!hasHeuristics ? { gridTemplateColumns: '1fr' } : {}}
      >
        <BattleLog events={events} />
        <PlayerHeuristicPanels state={lastState} label="ADVISORY" />
      </div>
    </div>
  )
}

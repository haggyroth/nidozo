import PokemonCard from './PokemonCard'
import BattleLog from './BattleLog'
import { WinProbBar, PlayerLabel, HeuristicDrawer, ThinkingBadge, PersonalityChip, PresetBadge } from './battleShared'
import { BattleBadges, CancelBattleButton } from './battleChrome'

/**
 * Classic battle stage — the original card-based arena. Renders only the stage
 * itself (header chrome + arena + log/heuristic panels). Lifecycle chrome
 * (tournament bar, winner banner, tournament-end overlay) is owned by App.jsx
 * and wraps both this and the Showdown cockpit.
 */
export default function BattleField({
  p1State, p2State, battleInfo, battleResult, events, thinking, coachThinking,
}) {
  const p1Mon   = p1State?.state?.my_active ?? null
  const p2Mon   = p2State?.state?.my_active ?? null
  const oppOfP1 = p1State?.state?.opponent_active ?? null
  const weather = p1State?.state?.weather ?? p2State?.state?.weather ?? null
  const turn    = Math.max(p1State?.turn ?? 0, p2State?.turn ?? 0)

  const p1Bench = (p1State?.state?.my_team ?? []).filter(m => m.species !== p1Mon?.species)
  const p2Bench = (p2State?.state?.my_team ?? []).filter(m => m.species !== p2Mon?.species)

  const lastState = (p1State?.turn ?? 0) >= (p2State?.turn ?? 0)
    ? p1State?.state
    : p2State?.state

  const isLive = battleInfo && !battleResult
  const currentBattleId = battleInfo?.battle_id
  const battleTier = battleInfo?.tier
  const battleDrafted = battleInfo?.drafted

  return (
    <div className="battlefield-wrapper">
      {/* Header row */}
      <div className="battle-header">
        <div className="turn-counter">
          {turn > 0 ? `TURN ${turn}` : 'READY'}
        </div>
        <div className="battle-header-center">
          <BattleBadges tier={battleTier} drafted={battleDrafted} />
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

      {/* Win-probability bar — shown once both players have emitted at least one turn */}
      <WinProbBar
        p1State={p1State}
        p2State={p2State}
        p1Label={battleInfo?.p1}
        p2Label={battleInfo?.p2}
      />

      {/* Arena */}
      <div className="arena">
        <div className="arena-player-col">
          <PlayerLabel label={battleInfo?.p1} side="p1" />
          <PersonalityChip personality={battleInfo?.p1_personality} />
          <PresetBadge presetSlug={battleInfo?.p1_preset} />
          <PokemonCard
            mon={p1Mon}
            side="p1"
            isOpponent={false}
            isThinking={thinking === 'p1' || coachThinking === 'p1'}
            bench={p1Bench}
          />
        </div>
        <div className="vs-divider">VS</div>
        <div className="arena-player-col">
          <PlayerLabel label={battleInfo?.p2} side="p2" />
          <PersonalityChip personality={battleInfo?.p2_personality} />
          <PresetBadge presetSlug={battleInfo?.p2_preset} />
          <PokemonCard
            mon={p2Mon ?? oppOfP1}
            side="p2"
            isOpponent={!p2Mon}
            isThinking={thinking === 'p2' || coachThinking === 'p2'}
            bench={p2Bench}
          />
        </div>
      </div>

      {/* Bottom panels */}
      <div
        className="bottom-panels"
        style={!lastState?.heuristics?.move_scores?.length ? { gridTemplateColumns: '1fr' } : {}}
      >
        <BattleLog events={events} />
        <HeuristicDrawer heuristics={lastState?.heuristics} moves={lastState?.available_moves} />
      </div>
    </div>
  )
}

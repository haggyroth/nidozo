import { useState, useEffect } from 'react'
import './styles/main.css'
import Leaderboard from './components/Leaderboard'
import ThemeToggle from './components/ThemeToggle'
import { TournamentBar, WinnerBanner, TournamentEndOverlay } from './components/battleChrome'
import { BadgeToastStack } from './components/battleShared'
import BattleReplay from './components/BattleReplay'
import ModelStats from './components/ModelStats'
import GlobalStats from './components/GlobalStats'
import TournamentView from './components/TournamentView'
import SeasonView from './components/SeasonView'
import DraftPhase from './components/DraftPhase'
import ShowdownBattleScene from './components/ShowdownBattleScene'
import { useBattleStream } from './hooks/useBattleStream'

function App() {
  const [view, setView]                       = useState('home')
  const [dismissed, setDismissed]             = useState(false)
  const [replayBattleId, setReplayBattleId]   = useState(null)
  const [statsModelId, setStatsModelId]       = useState(null)
  const [tournamentId, setTournamentId]       = useState(null)
  const [seasonId, setSeasonId]               = useState(null)
  // Track where replay was launched from so Close returns there
  const [replayOrigin, setReplayOrigin]       = useState('home')
  const {
    events, isConnected, p1State, p2State, battleInfo, battleResult,
    thinking, coachThinking, tournament, season, draft, showdownRoom,
    newBadges, dismissBadge, humanActionRequired, setHumanActionRequired,
    reset, clearTournament, clearSeason,
  } = useBattleStream()

  const result = dismissed ? null : battleResult

  function handleBattleStarted() {
    setDismissed(false)
    setView('battle')
  }

  function handleTournamentStarted(data) {
    reset()
    clearTournament()
    setDismissed(false)
    // Go directly to the tournament scoreboard; it has a "watch live" button
    if (data?.tournament_id) {
      setTournamentId(data.tournament_id)
      setView('tournament')
    } else {
      setView('battle')
    }
  }

  function handleTournamentSelected(id) {
    setTournamentId(id)
    setView('tournament')
  }

  function handleTournamentClose() {
    setTournamentId(null)
    setView('home')
  }

  function handleSeasonStarted(data) {
    reset()
    clearSeason()
    setDismissed(false)
    if (data?.season_id) {
      setSeasonId(data.season_id)
      setView('season')
    } else {
      setView('battle')
    }
  }

  function handleSeasonSelected(id) {
    setSeasonId(id)
    setView('season')
  }

  function handleSeasonClose() {
    setSeasonId(null)
    setView('home')
  }

  function handleWatchLive() {
    setView('battle')
  }

  function handleReplaySelected(battleId) {
    setReplayOrigin(view)   // remember where we came from
    setReplayBattleId(battleId)
    setView('replay')
  }

  function handleReplayClose() {
    setReplayBattleId(null)
    // Return to origin: stats page, tournament view, or home
    if (replayOrigin === 'stats' && statsModelId != null) {
      setView('stats')
    } else if (replayOrigin === 'tournament' && tournamentId != null) {
      setView('tournament')
    } else if (replayOrigin === 'season' && seasonId != null) {
      setView('season')
    } else {
      setView('home')
    }
  }

  function handleModelSelected(modelId) {
    setStatsModelId(modelId)
    setView('stats')
  }

  function handleStatsClose() {
    setStatsModelId(null)
    setView('home')
  }

  function handleGlobalStatsOpen() {
    setView('globalstats')
  }

  function handleGlobalStatsClose() {
    setView('home')
  }

  // Keyboard shortcut: R = watch replay once a battle has a result.
  // Lives here (not in a stage) so it works under either battle view.
  const finishedBattleId = result ? battleInfo?.battle_id : null
  useEffect(() => {
    if (!finishedBattleId) return
    function onKey(e) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
      if (e.key === 'r' || e.key === 'R') handleReplaySelected(finishedBattleId)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // handleReplaySelected only calls stable state setters; re-bind solely when
    // the finished battle changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishedBattleId])

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-logo">
          NIDOZO
          <span>LLM POKÉMON BATTLE ARENA</span>
        </div>
        <nav className="app-nav">
          <button
            className={`nav-btn ${view === 'home' || view === 'stats' || view === 'tournament' || view === 'season' ? 'active' : ''}`}
            onClick={() => { setStatsModelId(null); setTournamentId(null); setSeasonId(null); setView('home') }}
          >HOME</button>
          <button
            className={`nav-btn ${view === 'globalstats' ? 'active' : ''}`}
            onClick={handleGlobalStatsOpen}
          >STATS</button>
          <button
            className={`nav-btn ${view === 'battle' ? 'active' : ''}`}
            onClick={() => setView('battle')}
          >
            <span className={`status-dot ${isConnected ? 'connected' : 'disconnected'}`} />
            LIVE
            {tournament && tournament.status === 'running' && (
              <span className="nav-tournament-badge">
                {tournament.done}/{tournament.total}
              </span>
            )}
          </button>
          {/* Quick-jump to active tournament scoreboard */}
          {tournament && tournamentId && (
            <button
              className={`nav-btn ${view === 'tournament' ? 'active' : ''}`}
              onClick={() => setView('tournament')}
            >
              SCORES
            </button>
          )}
          <ThemeToggle />
        </nav>
      </header>

      <main>
        {view === 'home' && (
          <Leaderboard
            onBattleStarted={handleBattleStarted}
            onTournamentStarted={handleTournamentStarted}
            onSeasonStarted={handleSeasonStarted}
            onReplaySelected={handleReplaySelected}
            onModelSelected={handleModelSelected}
            onTournamentSelected={handleTournamentSelected}
            onSeasonSelected={handleSeasonSelected}
          />
        )}
        {view === 'globalstats' && (
          <GlobalStats
            onClose={handleGlobalStatsClose}
            onReplaySelected={handleReplaySelected}
          />
        )}
        {view === 'stats' && statsModelId != null && (
          <ModelStats
            modelId={statsModelId}
            onClose={handleStatsClose}
            onReplaySelected={handleReplaySelected}
          />
        )}
        {view === 'tournament' && tournamentId != null && (
          <TournamentView
            tournamentId={tournamentId}
            tournament={tournament?.id === tournamentId ? tournament : null}
            onClose={handleTournamentClose}
            onWatchLive={handleWatchLive}
            onReplaySelected={handleReplaySelected}
          />
        )}
        {view === 'battle' && (
          <>
            {draft && (
              <DraftPhase
                draft={draft}
                p1Label={battleInfo?.p1?.split('/').pop() ?? 'Player 1'}
                p2Label={battleInfo?.p2?.split('/').pop() ?? 'Player 2'}
              />
            )}
            {!draft && (
              <>
                {/* Tournament progress bar — wraps both stages */}
                {tournament && tournament.status !== 'completed' && (
                  <TournamentBar
                    tournament={tournament}
                    onScoreboard={() => {
                      if (tournament?.id) {
                        setTournamentId(tournament.id)
                        setView('tournament')
                      }
                    }}
                  />
                )}

                <ShowdownBattleScene
                  room={showdownRoom}
                  p1State={p1State}
                  p2State={p2State}
                  battleInfo={battleInfo}
                  battleResult={battleResult}
                  thinking={thinking}
                  coachThinking={coachThinking}
                  events={events}
                  humanActionRequired={humanActionRequired}
                  onHumanActionDismiss={() => setHumanActionRequired(null)}
                />

                {/* Lifecycle overlays — shared by both stages */}
                <WinnerBanner
                  battleResult={result}
                  battleInfo={battleInfo}
                  battleTier={battleInfo?.tier}
                  battleDrafted={battleInfo?.drafted}
                  onDismiss={() => setDismissed(true)}
                  onReplaySelected={handleReplaySelected}
                  currentBattleId={battleInfo?.battle_id}
                />
                {tournament?.status === 'completed' && (
                  <TournamentEndOverlay tournament={tournament} />
                )}
              </>
            )}
          </>
        )}
        {view === 'season' && seasonId != null && (
          <SeasonView
            seasonId={seasonId}
            season={season?.id === seasonId ? season : null}
            onClose={handleSeasonClose}
            onWatchLive={handleWatchLive}
            onReplaySelected={handleReplaySelected}
          />
        )}
        {view === 'replay' && replayBattleId != null && (
          <BattleReplay
            battleId={replayBattleId}
            onClose={handleReplayClose}
          />
        )}
      </main>

      {/* Global badge toast stack — visible on any view */}
      <BadgeToastStack badges={newBadges} onDismiss={dismissBadge} />
    </div>
  )
}

export default App

import { useState, useEffect, useRef, useCallback } from 'react'

// Keep only the most recent N events to prevent unbounded memory growth
// over long tournaments.
const MAX_EVENTS = 500

export function useBattleStream() {
  const [events, setEvents]             = useState([])
  const [isConnected, setConnected]     = useState(false)
  const [p1State, setP1State]           = useState(null)
  const [p2State, setP2State]           = useState(null)
  const [battleInfo, setBattleInfo]     = useState(null)
  const [battleResult, setBattleResult] = useState(null)
  const [thinking, setThinking]           = useState(null)   // 'p1' | 'p2' | null
  const [coachThinking, setCoachThinking] = useState(null)   // 'p1' | 'p2' | null — coach phase
  const [tournament, setTournament]     = useState(null)   // tournament progress state
  const [season, setSeason]             = useState(null)   // season progress state
  const [draft, setDraft]               = useState(null)   // draft phase state
  const [showdownRoom, setShowdownRoom] = useState(null)   // OP-02: Showdown battle room id for spectating
  const [newBadges, setNewBadges]       = useState([])     // badge_earned events for live toast
  const [humanActionRequired, setHumanActionRequired] = useState(null)  // human_action_required event
  const wsRef         = useRef(null)
  const shouldConnect = useRef(false)
  const retryDelay    = useRef(1000)
  // Buffer for turn log entries: { [turnNum]: { p1: event|null, p2: event|null } }
  // Flushed to the log in P1 → P2 order once both sides arrive for a given turn.
  const turnBufferRef = useRef({})
  // Stable ref so the ws.onclose handler can schedule a reconnect without
  // capturing `connect` in a closure before it's declared (immutability rule).
  const connectRef    = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN ||
        wsRef.current?.readyState === WebSocket.CONNECTING) return
    shouldConnect.current = true

    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${protocol}://${location.host}/ws/battles`)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      retryDelay.current = 1000
    }

    ws.onclose = () => {
      setConnected(false)
      if (shouldConnect.current) {
        const delay = Math.min(retryDelay.current, 30000)
        retryDelay.current = Math.min(retryDelay.current * 2, 30000)
        setTimeout(() => connectRef.current?.(), delay)
      }
    }

    ws.onerror = () => ws.close()

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data)

        if (event.type === 'ping') return

        // Draft phase events
        if (event.type === 'draft_start') {
          setDraft(prev => ({
            ...(prev || { p1: { picks: [], done: false }, p2: { picks: [], done: false } }),
            tier: event.tier,
            battleId: event.battle_id,
            [event.player_role]: { picks: [], done: false },
          }))
          return
        }

        if (event.type === 'draft_pick') {
          setDraft(prev => {
            if (!prev) return prev
            const role = event.player_role
            const existing = prev[role] || { picks: [], done: false }
            return {
              ...prev,
              [role]: {
                ...existing,
                picks: [...existing.picks, { species: event.species, types: event.types }],
              },
            }
          })
          return
        }

        if (event.type === 'draft_complete') {
          setDraft(prev => {
            if (!prev) return prev
            const role = event.player_role
            return {
              ...prev,
              [role]: { picks: event.team, done: true },
            }
          })
          return
        }

        // OP-02: browser learns the Showdown room id so ShowdownBattleScene can
        // open the spectator-proxy socket as soon as the room is available.
        if (event.type === 'showdown_room') {
          setShowdownRoom(event.room)
          return
        }

        if (event.type === 'human_action_required') {
          setHumanActionRequired(event)
          return
        }

        if (event.type === 'thinking') {
          if (event.agent === 'coach') {
            setCoachThinking(event.player_role)
            setThinking(null)
          } else {
            setThinking(event.player_role)
            setCoachThinking(null)
          }
          return
        }

        // state_update: Showdown just resolved a turn — refresh HP bars and active
        // Pokémon immediately, before the LLM has started deliberating.
        // Don't add to the events log and don't clear the thinking indicator.
        //
        // Render-only (zero-lag) updates omit decision context (heuristics,
        // available_moves, …) because those are stale until poke-env parses the
        // next request. Merge so we keep the last full turn's advisory rather
        // than blanking the heuristic drawer / PP display.
        if (event.type === 'state_update') {
          const merge = (prev) => {
            if (!prev?.state) return event
            const incoming = event.state || {}
            const keep = (next, old) =>
              (Array.isArray(next) ? next.length : next != null) ? next : old
            return {
              ...event,
              state: {
                ...incoming,
                heuristics:          keep(incoming.heuristics,          prev.state.heuristics),
                available_moves:     keep(incoming.available_moves,     prev.state.available_moves),
                available_switches:  keep(incoming.available_switches,  prev.state.available_switches),
                opponent_threat_map: keep(incoming.opponent_threat_map, prev.state.opponent_threat_map),
              },
            }
          }
          if (event.player_role === 'p1') setP1State(merge)
          if (event.player_role === 'p2') setP2State(merge)
          return
        }

        // Tournament lifecycle events — update progress, don't pollute battle log
        if (event.type === 'tournament_start') {
          setTournament({
            id: event.tournament_id,
            players: event.players,
            total: event.total_battles,
            rounds: event.rounds,
            tier: event.tier ?? 'random',
            tournament_format: event.tournament_format ?? 'round_robin',
            done: 0,
            status: 'running',
            leaderboard: null,
            bracket: null,
            champion: null,
          })
          return
        }

        if (event.type === 'bracket_update') {
          setTournament(prev => prev ? {
            ...prev,
            bracket: event.bracket,
          } : null)
          return
        }

        if (event.type === 'tournament_progress') {
          setTournament(prev => prev ? {
            ...prev,
            battleNum: event.battle_num,
            currentBattleId: event.battle_id,
            p1: event.p1,
            p2: event.p2,
          } : null)
          return
        }

        if (event.type === 'tournament_standings') {
          setTournament(prev => prev ? {
            ...prev,
            standings: event.standings,
          } : null)
          return
        }

        if (event.type === 'tournament_end') {
          setTournament(prev => prev ? {
            ...prev,
            status: 'completed',
            done: prev.total,
            leaderboard: event.leaderboard,
            bracket: event.bracket ?? prev?.bracket,
            champion: event.champion ?? null,
          } : null)
          return
        }

        if (event.type === 'tournament_cancelled') {
          setTournament(prev => prev ? {
            ...prev,
            status: 'cancelled',
            done: event.battles_completed,
          } : null)
          return
        }

        // Season lifecycle events
        if (event.type === 'season_start') {
          setSeason({
            id: event.season_id,
            name: event.season_name,
            players: event.players,
            total: event.total_battles,
            rounds: event.rounds,
            tier: event.tier ?? 'random',
            done: 0,
            status: 'running',
            standings: null,
          })
          return
        }

        if (event.type === 'season_progress') {
          setSeason(prev => prev ? {
            ...prev,
            battleNum: event.battle_num,
            currentBattleId: event.battle_id,
            p1: event.p1,
            p2: event.p2,
          } : null)
          return
        }

        if (event.type === 'season_standings') {
          setSeason(prev => prev ? {
            ...prev,
            standings: event.standings,
          } : null)
          return
        }

        if (event.type === 'season_end') {
          setSeason(prev => prev ? {
            ...prev,
            status: 'completed',
            done: prev.total,
            standings: event.standings,
          } : null)
          return
        }

        if (event.type === 'season_cancelled') {
          setSeason(prev => prev ? {
            ...prev,
            status: 'cancelled',
            done: event.battles_completed,
          } : null)
          return
        }

        // Turn events are buffered so the log always shows P1 → P2 regardless
        // of which LLM responds first. State updates and thinking clear
        // immediately so HP bars and indicators still animate in real time.
        if (event.type === 'turn') {
          setThinking(null)
          setCoachThinking(null)
          // Clear the picker as soon as the human's turn is confirmed server-side
          setHumanActionRequired(prev =>
            prev?.player_role === event.player_role ? null : prev
          )
          if (event.player_role === 'p1') setP1State(event)
          if (event.player_role === 'p2') setP2State(event)
          if (event.turn === 1) {
            setTournament(prev => prev ? { ...prev, done: Math.max(0, (prev.done || 0)) } : null)
          }

          const buf = turnBufferRef.current
          // Flush stale entries from earlier turns (e.g. one-sided random-bot turns
          // that will never receive a second event).
          const stale = Object.keys(buf).map(Number).filter(n => n < event.turn).sort((a, b) => a - b)
          if (stale.length) {
            // Evict from the ref BEFORE the state updater so the updater stays pure.
            // (React StrictMode calls updaters twice; deleting inside would crash the
            // second invocation with "Cannot read properties of undefined".)
            const evicted = []
            for (const n of stale) {
              if (buf[n].p1) evicted.push(buf[n].p1)
              if (buf[n].p2) evicted.push(buf[n].p2)
              delete buf[n]
            }
            setEvents(prev => {
              const next = [...prev, ...evicted]
              return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next
            })
          }

          if (!buf[event.turn]) buf[event.turn] = { p1: null, p2: null }
          buf[event.turn][event.player_role] = event

          if (buf[event.turn].p1 && buf[event.turn].p2) {
            const p1ev = buf[event.turn].p1
            const p2ev = buf[event.turn].p2
            delete buf[event.turn]
            setEvents(prev => {
              const next = [...prev, p1ev, p2ev]
              return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next
            })
          }
          return
        }

        // Flush any remaining buffered turns before the battle-end entry so the
        // log ordering is complete even if one side never emitted.
        if (event.type === 'battle_end' || event.type === 'battle_cancelled') {
          const buf = turnBufferRef.current
          const pending = Object.keys(buf).map(Number).sort((a, b) => a - b)
          turnBufferRef.current = {}
          setEvents(prev => {
            let next = [...prev]
            for (const n of pending) {
              if (buf[n].p1) next.push(buf[n].p1)
              if (buf[n].p2) next.push(buf[n].p2)
            }
            next.push(event)
            return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next
          })
          if (event.type === 'battle_end') {
            setBattleResult(event)
            setTournament(prev => prev ? { ...prev, done: (prev.done || 0) + 1 } : null)
          } else {
            setBattleResult({ ...event, cancelled: true })
          }
          setThinking(null)
          setCoachThinking(null)
          setHumanActionRequired(null)
          return
        }

        if (event.type === 'badge_earned') {
          setNewBadges(prev => [...prev, event])
          return
        }

        setEvents(prev => {
          const next = [...prev, event]
          return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next
        })

        if (event.type === 'battle_start') {
          // Clear the previous battle's log here rather than in handleBattleStarted,
          // so the model-name labels are always set by this event and never wiped by
          // a reset() that races the WS delivery of battle_start.
          turnBufferRef.current = {}
          setEvents([])
          setBattleInfo(event)
          setBattleResult(null)
          setP1State(null)
          setP2State(null)
          setThinking(null)
          setCoachThinking(null)
          setDraft(null)
          setShowdownRoom(null)
          setHumanActionRequired(null)
        }

      } catch {
        // non-JSON WebSocket frames are silently ignored
      }
    }
  }, [])

  // Keep the ref current so ws.onclose can schedule a reconnect after
  // `connect` is fully initialised (must be in an effect, not render).
  useEffect(() => {
    connectRef.current = connect
  }, [connect])

  const disconnect = useCallback(() => {
    shouldConnect.current = false
    wsRef.current?.close()
    wsRef.current = null
  }, [])

  const reset = useCallback(() => {
    setEvents([])
    setP1State(null)
    setP2State(null)
    setBattleInfo(null)
    setBattleResult(null)
    setThinking(null)
    setCoachThinking(null)
    setDraft(null)
    setShowdownRoom(null)
    setNewBadges([])
    setHumanActionRequired(null)
  }, [])

  const clearTournament = useCallback(() => setTournament(null), [])
  const clearSeason     = useCallback(() => setSeason(null), [])
  const dismissBadge    = useCallback(idx => setNewBadges(prev => prev.filter((_, i) => i !== idx)), [])

  useEffect(() => {
    connect()
    return disconnect
  }, [connect, disconnect])

  return {
    events, isConnected, p1State, p2State, battleInfo, battleResult,
    thinking, coachThinking, tournament, season, draft, showdownRoom,
    newBadges, dismissBadge, humanActionRequired, setHumanActionRequired,
    reset, clearTournament, clearSeason,
  }
}

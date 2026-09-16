"""Bracket generation and state management for elimination tournaments.

Supports single-elimination and double-elimination formats.
All state is serialisable to/from a plain dict (JSON-friendly).

Key types
---------
PlayerEntry  — one participant: {seed, provider, model_name}
BracketMatch — one scheduled contest with routing metadata
BracketState — the entire bracket as a nested dict

Match IDs
---------
  WR1-1  : Winners bracket, round 1, match 1
  LR1-1  : Losers bracket, round 1, match 1
  GF     : Grand final (double elim only)
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_power_of_two(n: int) -> int:
    return 1 if n <= 1 else 2 ** math.ceil(math.log2(n))


def _bracket_seed_order(size: int) -> list[int]:
    """Return seeds in bracket-draw order for a bracket of `size` (power of 2).

    Produces pairings where seed 1 can only meet seed 2 in the final,
    seeds 1/2 meet seeds 3/4 in the semis, etc.

    Example for size=8: [1, 8, 4, 5, 2, 7, 3, 6]
    Pairs: (1,8), (4,5), (2,7), (3,6)
    """
    positions: list[int] = [1, 2]
    current_size = 2
    while current_size < size:
        new_size = current_size * 2
        new_positions: list[int] = []
        for p in positions:
            new_positions.append(p)
            new_positions.append(new_size + 1 - p)
        positions = new_positions
        current_size = new_size
    return positions


def _make_match(
    match_id: str,
    bracket: str,
    round_num: int,
    p1_seed: int | None,
    p2_seed: int | None,
    *,
    p1_is_bye: bool = False,
    p2_is_bye: bool = False,
    winner_to: str | None = None,
    loser_to: str | None = None,
    winner_slot: int | None = None,
    loser_slot: int | None = None,
) -> dict[str, Any]:
    return {
        "match_id":    match_id,
        "bracket":     bracket,    # "winners" | "losers" | "grand_final"
        "round_num":   round_num,
        "p1_seed":     p1_seed,
        "p2_seed":     p2_seed,
        "p1_is_bye":   p1_is_bye,
        "p2_is_bye":   p2_is_bye,
        "battle_id":   None,
        "winner_seed": None,       # filled when match completes
        "loser_seed":  None,
        "status":      "bye" if (p1_is_bye or p2_is_bye) else "pending",
        "winner_to":   winner_to,
        "loser_to":    loser_to,
        "winner_slot": winner_slot,
        "loser_slot":  loser_slot,
    }


# ---------------------------------------------------------------------------
# Single Elimination
# ---------------------------------------------------------------------------

def build_single_elim(players: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a complete single-elimination bracket state.

    Args:
        players: list of {provider, model_name} in seed order (index 0 = seed 1).

    Returns:
        bracket_state dict with keys:
            format, size, seeds, rounds, match_index, champion_seed
    """
    n = len(players)
    size = _next_power_of_two(n)
    num_rounds = int(math.log2(size))

    # Seed map: seed number → player dict (None if bye)
    seeds: dict[int, dict[str, Any] | None] = {}
    for i, p in enumerate(players):
        seeds[i + 1] = {"seed": i + 1, **p}
    for s in range(n + 1, size + 1):
        seeds[s] = None  # bye slot

    # --- Round 1 ---
    order = _bracket_seed_order(size)  # length == size
    pairs = [(order[i], order[i + 1]) for i in range(0, size, 2)]

    matches: list[dict[str, Any]] = []
    match_index: dict[str, dict[str, Any]] = {}

    # Pre-compute match IDs for all rounds so we can wire winner_to
    def match_id(rnd: int, idx: int) -> str:
        return f"WR{rnd}-{idx + 1}"

    # Number of matches per round: size/2, size/4, ...
    round_match_counts = [size // (2 ** r) for r in range(1, num_rounds + 1)]

    # Build all matches (round 1 first, then subsequent rounds)
    for rnd in range(1, num_rounds + 1):
        n_matches = round_match_counts[rnd - 1]
        for idx in range(n_matches):
            mid = match_id(rnd, idx)
            # winner goes to next round
            if rnd < num_rounds:
                next_idx = idx // 2
                wt = match_id(rnd + 1, next_idx)
                ws = 1 if (idx % 2 == 0) else 2
            else:
                wt = None
                ws = None

            if rnd == 1:
                s1, s2 = pairs[idx]
                p1_bye = seeds[s1] is None
                p2_bye = seeds[s2] is None
                m = _make_match(
                    mid, "winners", rnd, s1, s2,
                    p1_is_bye=p1_bye,
                    p2_is_bye=p2_bye,
                    winner_to=wt,
                    winner_slot=ws,
                )
                # Auto-resolve byes
                if p1_bye and not p2_bye:
                    m["winner_seed"] = s2
                    m["loser_seed"] = s1
                    m["status"] = "bye"
                elif p2_bye and not p1_bye:
                    m["winner_seed"] = s1
                    m["loser_seed"] = s2
                    m["status"] = "bye"
            else:
                # TBD seeds — will be filled as prior rounds complete
                m = _make_match(
                    mid, "winners", rnd, None, None,
                    winner_to=wt,
                    winner_slot=ws,
                )

            matches.append(m)
            match_index[mid] = m

    # Pre-propagate byes from round 1 into round 2+
    _propagate_byes_single(match_index, num_rounds)

    rounds_out: list[dict[str, Any]] = []
    for rnd in range(1, num_rounds + 1):
        rounds_out.append({
            "round_num":  rnd,
            "bracket":    "winners",
            "matches":    [m for m in matches if m["round_num"] == rnd],
        })

    return {
        "format":        "single_elim",
        "size":          size,
        "num_rounds":    num_rounds,
        "seeds":         {str(k): v for k, v in seeds.items()},
        "rounds":        rounds_out,
        "match_index":   match_index,
        "champion_seed": None,
    }


def _propagate_byes_single(
    match_index: dict[str, dict[str, Any]],
    num_rounds: int,
) -> None:
    """Cascade bye wins forward so round 2 matches are seeded where possible."""
    for rnd in range(1, num_rounds):
        n_matches = len([m for m in match_index.values() if m["round_num"] == rnd])
        for idx in range(n_matches):
            mid = f"WR{rnd}-{idx + 1}"
            m = match_index.get(mid)
            if m and m["status"] == "bye" and m["winner_seed"] is not None:
                _advance_winner_single(match_index, m)


def _advance_winner_single(
    match_index: dict[str, dict[str, Any]],
    completed: dict[str, Any],
) -> None:
    """Place the winner of `completed` into the next match slot."""
    wt = completed.get("winner_to")
    ws = completed.get("winner_slot")
    winner_seed = completed.get("winner_seed")
    if wt is None or winner_seed is None:
        return
    next_m = match_index.get(wt)
    if next_m is None:
        return
    if ws == 1:
        next_m["p1_seed"] = winner_seed
    else:
        next_m["p2_seed"] = winner_seed


# ---------------------------------------------------------------------------
# Record a result and advance in single elim
# ---------------------------------------------------------------------------

def record_result_single(
    state: dict[str, Any],
    match_id: str,
    winner_slot: int,   # 1 = p1 won, 2 = p2 won
    battle_id: int,
) -> None:
    """Update state after a battle completes in a single-elim bracket."""
    mi = state["match_index"]
    m = mi.get(match_id)
    if m is None:
        return

    winner_seed = m["p1_seed"] if winner_slot == 1 else m["p2_seed"]
    loser_seed  = m["p2_seed"] if winner_slot == 1 else m["p1_seed"]

    m["winner_seed"] = winner_seed
    m["loser_seed"]  = loser_seed
    m["battle_id"]   = battle_id
    m["status"]      = "completed"

    _advance_winner_single(mi, m)

    # Check if this was the final
    if m["winner_to"] is None:
        state["champion_seed"] = winner_seed


# ---------------------------------------------------------------------------
# Double Elimination
# ---------------------------------------------------------------------------

def build_double_elim(players: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a complete double-elimination bracket state.

    WB: standard seeded bracket.
    LB: losers from WB feed in; structure mirrors standard DE.
    GF: WB winner vs LB winner.
    """
    n = len(players)
    size = _next_power_of_two(n)
    wb_rounds = int(math.log2(size))         # rounds in winners bracket
    lb_rounds = 2 * (wb_rounds - 1)          # rounds in losers bracket

    seeds: dict[int, dict[str, Any] | None] = {}
    for i, p in enumerate(players):
        seeds[i + 1] = {"seed": i + 1, **p}
    for s in range(n + 1, size + 1):
        seeds[s] = None

    match_index: dict[str, dict[str, Any]] = {}

    # ---- Build Winners Bracket ----
    # WB has same structure as single-elim but losers drop to LB
    order = _bracket_seed_order(size)
    wb_r1_pairs = [(order[i], order[i + 1]) for i in range(0, size, 2)]

    for rnd in range(1, wb_rounds + 1):
        n_wb = size // (2 ** rnd)
        n_wb_matches = max(1, n_wb)
        for idx in range(n_wb_matches):
            mid = f"WR{rnd}-{idx + 1}"
            wt = f"WR{rnd + 1}-{idx // 2 + 1}" if rnd < wb_rounds else "GF"
            ws = 1 if idx % 2 == 0 else 2
            # Losers routing from WB round 1 → LB round 1
            # WB round r losers → LB round (2r-2)+1 (for r>=2) or LB round 1 (for r=1)
            lt, ls = _wb_loser_destination(rnd, idx, wb_rounds)

            if rnd == 1:
                s1, s2 = wb_r1_pairs[idx]
                p1_bye = seeds[s1] is None
                p2_bye = seeds[s2] is None
                m = _make_match(
                    mid, "winners", rnd, s1, s2,
                    p1_is_bye=p1_bye, p2_is_bye=p2_bye,
                    winner_to=wt, winner_slot=ws,
                    loser_to=lt, loser_slot=ls,
                )
                if p1_bye and not p2_bye:
                    m["winner_seed"] = s2
                    m["loser_seed"]  = s1
                    m["status"]      = "bye"
                elif p2_bye and not p1_bye:
                    m["winner_seed"] = s1
                    m["loser_seed"]  = s2
                    m["status"]      = "bye"
            else:
                m = _make_match(
                    mid, "winners", rnd, None, None,
                    winner_to=wt, winner_slot=ws,
                    loser_to=lt, loser_slot=ls,
                )
            match_index[mid] = m

    # ---- Build Losers Bracket ----
    # LB match counts per round:
    #   n_lb_matches(r) = size // 2^(2 + (r-1)//2)
    #
    # Odd LB rounds: two WB losers fight (LR1) or LB survivor vs WB loser
    #   winner → next round same match index, slot 1 (LB survivor is "incumbent")
    # Even LB rounds: LB survivors play each other
    #   adjacent pairs merge → winner → next round match idx//2, slot 1 or 2
    for lb_rnd in range(1, lb_rounds + 1):
        n_lb = max(1, size // (2 ** (2 + (lb_rnd - 1) // 2)))
        for idx in range(n_lb):
            mid = f"LR{lb_rnd}-{idx + 1}"
            if lb_rnd == lb_rounds:
                wt = "GF"
                ws = 2  # LB champion enters GF as slot 2
            elif lb_rnd % 2 == 1:
                # Odd round: winner keeps same index in next round, slot 1
                wt = f"LR{lb_rnd + 1}-{idx + 1}"
                ws = 1
            else:
                # Even round: adjacent pairs merge
                next_idx = idx // 2
                wt = f"LR{lb_rnd + 1}-{next_idx + 1}"
                ws = 1 if idx % 2 == 0 else 2
            m = _make_match(
                mid, "losers", lb_rnd, None, None,
                winner_to=wt, winner_slot=ws,
            )
            match_index[mid] = m

    # ---- Grand Final ----
    gf = _make_match(
        "GF", "grand_final", 1, None, None,
    )
    # Potential bracket reset — GF winner wins; if LB player wins GF, play GFR
    gf["winner_to"]  = "GFR"
    gf["winner_slot"] = 1
    match_index["GF"] = gf

    gfr = _make_match(
        "GFR", "grand_final", 2, None, None,
    )
    match_index["GFR"] = gfr

    # Pre-propagate byes
    _propagate_byes_double(match_index, seeds)

    # Build round list
    wb_round_list = [
        {
            "round_num": rnd,
            "bracket":   "winners",
            "matches":   [m for m in match_index.values()
                          if m["bracket"] == "winners" and m["round_num"] == rnd],
        }
        for rnd in range(1, wb_rounds + 1)
    ]
    lb_round_list = [
        {
            "round_num": lb_rnd,
            "bracket":   "losers",
            "matches":   [m for m in match_index.values()
                          if m["bracket"] == "losers" and m["round_num"] == lb_rnd],
        }
        for lb_rnd in range(1, lb_rounds + 1)
    ]
    gf_list = [
        {
            "round_num": 1,
            "bracket":   "grand_final",
            "matches":   [match_index["GF"], match_index["GFR"]],
        }
    ]

    return {
        "format":        "double_elim",
        "size":          size,
        "wb_rounds":     wb_rounds,
        "lb_rounds":     lb_rounds,
        "seeds":         {str(k): v for k, v in seeds.items()},
        "wb_rounds_list": wb_round_list,
        "lb_rounds_list": lb_round_list,
        "gf_list":       gf_list,
        "match_index":   match_index,
        "champion_seed": None,
    }


def _wb_loser_destination(
    wb_rnd: int, match_idx: int, total_wb_rounds: int,
) -> tuple[str | None, int | None]:
    """Return (loser_to match_id, slot) for a WB match.

    Two players: there is no losers bracket at all (`lb_rounds == 0`), so the
    single WB loser is the losers-bracket champion by default and goes straight
    to slot 2 of the grand final. Without this the loser was routed to `LR1-1`,
    a match that does not exist, so GF's slot 2 stayed empty, GF was never
    playable and the tournament drained with no champion (#279).
    WB R1: adjacent pairs of losers fight each other in LB R1.
      match 0 loser → LR1-1 slot 1
      match 1 loser → LR1-1 slot 2
      match 2 loser → LR1-2 slot 1
      ...
    WB round r (r>=2): losers drop to LB round 2*(r-1) as slot 2
      (slot 1 = incumbent LB survivor).
    """
    if wb_rnd == 1 and total_wb_rounds == 1:
        return "GF", 2
    if wb_rnd == 1:
        lb_match_num = match_idx // 2 + 1
        lb_slot = (match_idx % 2) + 1
        return f"LR1-{lb_match_num}", lb_slot
    else:
        lb_rnd = 2 * (wb_rnd - 1)
        lb_match_num = match_idx + 1
        return f"LR{lb_rnd}-{lb_match_num}", 2


def _propagate_byes_double(
    match_index: dict[str, dict[str, Any]],
    seeds: dict[int, dict[str, Any] | None],
) -> None:
    """Forward-propagate WB round-1 bye results."""
    for m in list(match_index.values()):
        if m["bracket"] == "winners" and m["round_num"] == 1 and m["status"] == "bye":
            # advance winner into WB
            _advance_winner_single(match_index, m)
            # advance loser into LB (it's a bye player — slot it as "bye" in LB)
            lt = m.get("loser_to")
            ls = m.get("loser_slot")
            loser_seed = m.get("loser_seed")
            if lt and loser_seed is not None:
                lb_m = match_index.get(lt)
                if lb_m:
                    if ls == 1:
                        lb_m["p1_seed"] = loser_seed
                        lb_m["p1_is_bye"] = True
                    else:
                        lb_m["p2_seed"] = loser_seed
                        lb_m["p2_is_bye"] = True
    # Now resolve any LB matches that already have one real seed vs one bye seed.
    _resolve_lb_byes_double(match_index)


def _resolve_lb_byes_double(match_index: dict[str, dict[str, Any]]) -> None:
    """Walkover any LB match where one slot is a bye and the other has a real seed.

    Iterates until stable — one resolved match may seed a downstream LB slot that
    also turns out to be a walkover (e.g. two consecutive WB byes feeding the same
    LB column in a non-power-of-two bracket).
    """
    changed = True
    while changed:
        changed = False
        for m in list(match_index.values()):
            if m["bracket"] != "losers" or m["status"] != "pending":
                continue
            p1_bye = m.get("p1_is_bye", False)
            p2_bye = m.get("p2_is_bye", False)
            if p1_bye and m.get("p2_seed") is not None:
                # Real player in slot 2 — walkover win
                m["winner_seed"] = m["p2_seed"]
                m["loser_seed"] = m["p1_seed"]
                m["status"] = "bye"
                _advance_winner_single(match_index, m)
                changed = True
            elif p2_bye and m.get("p1_seed") is not None:
                # Real player in slot 1 — walkover win
                m["winner_seed"] = m["p1_seed"]
                m["loser_seed"] = m["p2_seed"]
                m["status"] = "bye"
                _advance_winner_single(match_index, m)
                changed = True


def record_result_double(
    state: dict[str, Any],
    match_id: str,
    winner_slot: int,
    battle_id: int,
) -> None:
    """Update double-elim state after a battle completes."""
    mi = state["match_index"]
    m  = mi.get(match_id)
    if m is None:
        return

    winner_seed = m["p1_seed"] if winner_slot == 1 else m["p2_seed"]
    loser_seed  = m["p2_seed"] if winner_slot == 1 else m["p1_seed"]

    m["winner_seed"] = winner_seed
    m["loser_seed"]  = loser_seed
    m["battle_id"]   = battle_id
    m["status"]      = "completed"

    # Advance winner
    wt = m.get("winner_to")
    ws = m.get("winner_slot")
    if wt and winner_seed is not None:
        next_m = mi.get(wt)
        if next_m:
            if ws == 1:
                next_m["p1_seed"] = winner_seed
            else:
                next_m["p2_seed"] = winner_seed

    # Drop loser (WB only — LB losers are eliminated)
    lt = m.get("loser_to")
    ls = m.get("loser_slot")
    if lt and loser_seed is not None and m["bracket"] == "winners":
        lb_m = mi.get(lt)
        if lb_m:
            if ls == 1:
                lb_m["p1_seed"] = loser_seed
            else:
                lb_m["p2_seed"] = loser_seed
        # A freshly-dropped loser may land next to a bye slot — resolve immediately.
        _resolve_lb_byes_double(mi)

    # Handle GF: if LB player (p2) wins GF, trigger bracket reset (GFR)
    if match_id == "GF":
        if winner_slot == 2:
            # LB player won — GFR is live
            gfr = mi.get("GFR")
            if gfr:
                gfr["p1_seed"] = m["p2_seed"]   # LB player (now has won GF)
                gfr["p2_seed"] = m["p1_seed"]   # WB player
                gfr["status"]  = "pending"
        else:
            # WB player won GF directly — tournament over
            state["champion_seed"] = winner_seed
            gfr = mi.get("GFR")
            if gfr:
                gfr["status"] = "void"
    elif match_id == "GFR":
        state["champion_seed"] = winner_seed


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_pending_matches(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return matches that are ready to play (both seeds known, status=pending)."""
    mi = state.get("match_index", {})
    ready = []
    for m in mi.values():
        if m["status"] != "pending":
            continue
        if m.get("p1_is_bye") or m.get("p2_is_bye"):
            continue
        if m.get("p1_seed") is not None and m.get("p2_seed") is not None:
            ready.append(m)
    return ready


def get_all_matches_ordered(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all matches in a sensible display order."""
    mi = state.get("match_index", {})

    def sort_key(m: dict[str, Any]) -> tuple[int, int, int]:
        bkt = {"winners": 0, "losers": 1, "grand_final": 2}.get(m["bracket"], 3)
        return (bkt, m["round_num"], int(m["match_id"].split("-")[-1]) if "-" in m["match_id"] else 0)

    return sorted(mi.values(), key=sort_key)


def resolve_seed(
    state: dict[str, Any],
    seed: int | None,
) -> dict[str, Any] | None:
    """Return the player dict for a given seed, or None."""
    if seed is None:
        return None
    result: dict[str, Any] | None = state["seeds"].get(str(seed))
    return result


def build_bracket(
    players: list[dict[str, Any]],
    fmt: str,
) -> dict[str, Any]:
    """Build a bracket for the given format."""
    if fmt == "single_elim":
        return build_single_elim(players)
    elif fmt == "double_elim":
        return build_double_elim(players)
    raise ValueError(f"Unknown bracket format: {fmt!r}")


def record_result(
    state: dict[str, Any],
    match_id: str,
    winner_slot: int,
    battle_id: int,
) -> None:
    """Dispatch result recording to the right format handler."""
    fmt = state.get("format")
    if fmt == "single_elim":
        record_result_single(state, match_id, winner_slot, battle_id)
    elif fmt == "double_elim":
        record_result_double(state, match_id, winner_slot, battle_id)
    else:
        raise ValueError(f"Unknown bracket format: {fmt!r}")

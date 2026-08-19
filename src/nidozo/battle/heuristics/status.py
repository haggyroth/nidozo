"""Status conditions and status-move annotation.

Holds the two lookup tables — what a status does to the afflicted Pokémon, and
what each status move inflicts — plus the annotator that turns a status move
into advisory notes contextualised by the current stat stages and HP.
"""

from __future__ import annotations

from typing import Any

from poke_env.battle import Pokemon
from poke_env.battle.move import Move

from nidozo.battle.heuristics.damage import _stage_mult

# ---------------------------------------------------------------------------
# Status effect mechanical impact summaries
# ---------------------------------------------------------------------------

_STATUS_IMPACT: dict[str, str] = {
    "BRN": "Burn: physical attack halved; takes 1/16 HP per turn",
    "PAR": "Paralysis: speed reduced to 50%; 25% chance to be fully paralyzed each turn",
    "PSN": "Poison: takes 1/8 HP per turn",
    "TOX": "Badly Poisoned: damage increases each turn (1/16, 2/16, 3/16…)",
    "SLP": "Sleep: cannot act (1–3 turns in Gen 9)",
    "FRZ": "Frozen: cannot act until thawed (rare thaw chance each turn)",
}

# Status move annotations — what each status inflicts and why it matters
_STATUS_MOVE_EFFECTS: dict[str, dict[str, Any]] = {
    # Sleep
    "spore":        {"inflicts": "SLP", "note": "inflicts Sleep (most reliable — 100% accurate)"},
    "sleeppowder":  {"inflicts": "SLP", "note": "inflicts Sleep (75% accurate)"},
    "hypnosis":     {"inflicts": "SLP", "note": "inflicts Sleep (60% accurate)"},
    "sing":         {"inflicts": "SLP", "note": "inflicts Sleep (55% accurate)"},
    "grasswhistle": {"inflicts": "SLP", "note": "inflicts Sleep (55% accurate)"},
    "lovelykiss":   {"inflicts": "SLP", "note": "inflicts Sleep (75% accurate)"},
    "yawn":         {"inflicts": "SLP", "note": "inflicts Sleep next turn (opponent can switch)"},
    # Paralysis
    "thunderwave":  {"inflicts": "PAR", "note": "inflicts Paralysis (100% accurate) — slows opponent to 50% speed, 25% chance to not act"},
    "stunspore":    {"inflicts": "PAR", "note": "inflicts Paralysis (75% accurate)"},
    "glare":        {"inflicts": "PAR", "note": "inflicts Paralysis (75% accurate) — hits Normal types unlike Thunder Wave"},
    "bodyslam":     {"inflicts": "PAR", "note": "30% paralysis chance on hit"},
    "lick":         {"inflicts": "PAR", "note": "30% paralysis chance on hit"},
    # Burn
    "willowisp":    {"inflicts": "BRN", "note": "inflicts Burn (85% accurate) — halves opponent's physical attack"},
    # Poison
    "toxic":        {"inflicts": "TOX", "note": "inflicts Badly Poisoned — damage escalates each turn; high value in longer battles"},
    "poisonpowder": {"inflicts": "PSN", "note": "inflicts Poison (75% accurate)"},
    "poisongas":    {"inflicts": "PSN", "note": "inflicts Poison (55% accurate)"},
    # Stat boosts — own
    "swordsdance":  {"stat_boost": {"atk": +2}, "note": "raises Attack +2 stages"},
    "nastyplot":    {"stat_boost": {"spa": +2}, "note": "raises Sp. Atk +2 stages"},
    "calmmind":     {"stat_boost": {"spa": +1, "spd": +1}, "note": "raises Sp. Atk and Sp. Def +1 stage each"},
    "dragondance":  {"stat_boost": {"atk": +1, "spe": +1}, "note": "raises Attack and Speed +1 stage each"},
    "bulkup":       {"stat_boost": {"atk": +1, "def": +1}, "note": "raises Attack and Defense +1 stage each"},
    "agility":      {"stat_boost": {"spe": +2}, "note": "raises Speed +2 stages — may enable you to outspeed threats"},
    "amnesia":      {"stat_boost": {"spd": +2}, "note": "raises Sp. Def +2 stages"},
    "growth":       {"stat_boost": {"spa": +1}, "note": "raises Sp. Atk +1 stage"},
    "meditate":     {"stat_boost": {"atk": +1}, "note": "raises Attack +1 stage"},
    "sharpen":      {"stat_boost": {"atk": +1}, "note": "raises Attack +1 stage"},
    "workup":       {"stat_boost": {"atk": +1, "spa": +1}, "note": "raises Attack and Sp. Atk +1 stage each"},
    "batonpass":    {"baton_pass": True, "note": "passes stat boosts/drops to the next ally — use while boosted"},
    # Stat drops — opponent
    "screech":      {"stat_drop": {"def": -2}, "note": "lowers opponent Defense -2 stages — amplifies physical moves"},
    "charm":        {"stat_drop": {"atk": -2}, "note": "lowers opponent Attack -2 stages — reduces physical damage taken"},
    "growl":        {"stat_drop": {"atk": -1}, "note": "lowers opponent Attack -1 stage"},
    "leer":         {"stat_drop": {"def": -1}, "note": "lowers opponent Defense -1 stage"},
    "tickle":       {"stat_drop": {"atk": -1, "def": -1}, "note": "lowers opponent Attack and Defense -1 stage each"},
    "stringshot":   {"stat_drop": {"spe": -1}, "note": "lowers opponent Speed -1 stage"},
    "featherdance": {"stat_drop": {"atk": -2}, "note": "lowers opponent Attack -2 stages"},
    "sweetscent":   {"stat_drop": {"eva": -1}, "note": "lowers opponent evasion -1 stage"},
    # Utility
    "recover":   {"heal": 0.5, "note": "restores 50% of max HP"},
    "softboiled":{"heal": 0.5, "note": "restores 50% of max HP"},
    "moonlight": {"heal": 0.5, "note": "restores HP (50% normally, more in Sun, less in Sand/Rain)"},
    "morningsun":{"heal": 0.5, "note": "restores HP (50% normally, more in Sun, less in Sand/Rain)"},
    "synthesis": {"heal": 0.5, "note": "restores HP (50% normally, more in Sun, less in Sand/Rain)"},
    "wish":      {"heal": 0.5, "note": "heals ally next turn — use while healthy so the ally benefits"},
    "lightscreen":{"screen": "spa", "note": "halves special damage for 5 turns for your side"},
    "reflect":   {"screen": "atk", "note": "halves physical damage for 5 turns for your side"},
    "substitute":{"substitute": True, "note": "creates a 25% HP decoy — blocks status and chip damage"},
    "leechseed": {"inflicts": "SEED", "note": "drains 1/8 HP per turn from opponent; wasted on Grass types"},
    "stealthrock": {"hazard": True, "note": "sets Stealth Rock — damages all incoming opponents based on Rock-type effectiveness (12%–50%)"},
    "spikes":      {"hazard": True, "note": "lays Spikes (up to 3 layers) — damages grounded opponents 12/17/25% on switch-in"},
    "toxicspikes": {"hazard": True, "note": "sets Toxic Spikes (1 layer=Poison, 2=Badly Poisoned) — absorbed by Poison-type switch-ins"},
    "stickyweb":   {"hazard": True, "note": "sets Sticky Web — reduces Speed -1 for grounded opponents on switch-in"},
    "rapidspin":   {"hazard": True, "note": "removes entry hazards and Leech Seed from your side"},
    "defog":       {"hazard": True, "note": "clears hazards from both sides and lowers opponent's evasion -1"},
    "mortalspin":  {"hazard": True, "note": "removes entry hazards from your side and poisons the opponent"},
    "tidyup":      {"hazard": True, "note": "removes Spikes/Stealth Rock/Sticky Web and Substitutes; raises your Attack and Speed +1"},
    "perishsong":{"perish": True, "note": "both active Pokémon faint in 3 turns unless switched"},
    "encore":    {"encore": True, "note": "forces opponent to repeat their last move for 3 turns"},
    "taunt":     {"taunt": True, "note": "prevents opponent from using status moves for 3 turns"},
    "protect":   {"protect": True, "note": "blocks all moves this turn — good for scouting or stalling"},
    "detect":    {"protect": True, "note": "blocks all moves this turn — same effect as Protect"},
    "roar":      {"phazing": True, "note": "forces opponent to switch; erases their stat boosts"},
    "whirlwind": {"phazing": True, "note": "forces opponent to switch; erases their stat boosts"},
    "haze":      {"haze": True, "note": "resets all stat stages for both sides to zero"},
    "trick":     {"trick": True, "note": "swaps held items with opponent — devastating if you hold a Choice item"},
    "knockoff":  {"knockoff": True, "note": "removes opponent's held item permanently"},
    "spite":     {"spite": True, "note": "reduces PP of opponent's last used move by 4"},
    "painsplit": {"painsplit": True, "note": "averages HP between both active Pokémon — best when opponent is high HP"},
}


# ---------------------------------------------------------------------------
# Status move annotation (expanded)
# ---------------------------------------------------------------------------

def _annotate_status_move(
    move: Move,
    score: dict[str, Any],
    opp: Pokemon | None,
    own: Pokemon | None,
) -> None:
    mid = move.id
    entry = _STATUS_MOVE_EFFECTS.get(mid)

    if entry is None:
        # Unknown status move — generic fallback
        score["notes"].append("status move")
        return

    score["notes"].append(entry["note"])

    # Status infliction check — wasted if opponent already has a status
    inflicts = entry.get("inflicts")
    if inflicts and inflicts not in ("SEED",) and opp is not None and opp.status is not None:
        score["notes"].append(
            f"⚠ Opponent already has {opp.status.name} — status moves cannot stack; this would be wasted"
        )

    # Boost moves — show current stage and diminishing value
    stat_boost = entry.get("stat_boost")
    if stat_boost and own is not None:
        stage_notes = []
        for stat, delta in stat_boost.items():
            current = own.boosts.get(stat, 0)
            new_stage = min(6, current + delta)
            if current >= 6:
                stage_notes.append(f"{stat} already at +6 (max) — no further effect")
            else:
                mult_now = _stage_mult(current)
                mult_after = _stage_mult(new_stage)
                gain_pct = int((mult_after / mult_now - 1) * 100)
                stage_notes.append(f"{stat} {current:+d} → {new_stage:+d} (+{gain_pct}% effective stat)")
        if stage_notes:
            score["notes"].extend(stage_notes)

    # Stat drop moves — show opponent's current stage
    stat_drop = entry.get("stat_drop")
    if stat_drop and opp is not None:
        drop_notes = []
        for stat, delta in stat_drop.items():
            current = opp.boosts.get(stat, 0)
            new_stage = max(-6, current + delta)
            if current <= -6:
                drop_notes.append(f"Opponent {stat} already at -6 (min) — no further effect")
            else:
                mult_now = _stage_mult(current)
                mult_after = _stage_mult(new_stage)
                reduction_pct = int((1 - mult_after / mult_now) * 100)
                drop_notes.append(f"Opponent {stat} {current:+d} → {new_stage:+d} (-{reduction_pct}% effective stat)")
        if drop_notes:
            score["notes"].extend(drop_notes)

    # Healing moves — context based on own HP
    heal_frac = entry.get("heal")
    if heal_frac is not None and own is not None:
        hp = own.current_hp_fraction
        if hp >= 0.875:
            score["notes"].append(f"HP is high ({int(hp * 100)}%) — limited recovery value right now")
        elif hp <= 0.5:
            score["notes"].append(f"HP is low ({int(hp * 100)}%) — high recovery value")

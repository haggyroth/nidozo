"""Entry hazard chip damage and removal (v8+).

Reads our own side conditions and reports what a switch-in will cost: Stealth
Rock chip scaled by Rock effectiveness, Spikes by layer, Toxic Spikes status,
and the Sticky Web speed drop.
"""

from __future__ import annotations

from poke_env.battle import AbstractBattle, Pokemon

from nidozo.battle.heuristics.type_chart import _is_grounded, _type_effectiveness_vs

# Moves that clear entry hazards from the user's side.
_HAZARD_REMOVAL_MOVES: frozenset[str] = frozenset({
    "rapidspin", "defog", "mortalspin", "tidyup",
})


def _hazard_switch_notes(incoming: Pokemon, battle: AbstractBattle) -> list[str]:
    """Return advisory notes about entry hazard chip damage for this switch-in.

    Reads ``battle.side_conditions`` (our own side) and computes:
    - Stealth Rock chip (based on Rock-type effectiveness vs incoming)
    - Spikes chip (layers × flat %; grounded only)
    - Toxic Spikes status (grounded only; Poison absorbs)
    - Sticky Web Speed drop (grounded only)
    """
    notes: list[str] = []
    try:
        from poke_env.battle import SideCondition  # noqa: PLC0415
        side_conds = battle.side_conditions
        if not side_conds:
            return notes

        grounded = _is_grounded(incoming)

        # Stealth Rock — all Pokémon, damage based on Rock effectiveness
        if SideCondition.STEALTH_ROCK in side_conds:
            rock_mult = _type_effectiveness_vs("ROCK", incoming)
            chip_pct = round(rock_mult / 8.0 * 100)
            if rock_mult >= 4.0:
                notes.append(f"⚠ Stealth Rock: -{chip_pct}% HP on entry (4× weak to Rock!)")
            elif rock_mult >= 2.0:
                notes.append(f"⚠ Stealth Rock: -{chip_pct}% HP on entry (2× weak)")
            elif rock_mult <= 0.25:
                notes.append(f"Stealth Rock: -{chip_pct}% HP on entry (0.25× resist)")
            elif rock_mult <= 0.5:
                notes.append(f"Stealth Rock: -{chip_pct}% HP on entry (resist)")
            else:
                notes.append(f"Stealth Rock: -{chip_pct}% HP on entry")

        if grounded:
            # Spikes — 1/8 / 1/6 / 1/4 HP by layer count
            if SideCondition.SPIKES in side_conds:
                layers = side_conds[SideCondition.SPIKES]
                chip_pct = {1: 12, 2: 17, 3: 25}.get(layers, 25)
                notes.append(f"⚠ Spikes ({layers} layer{'s' if layers > 1 else ''}): ~-{chip_pct}% HP on entry")

            # Toxic Spikes — Poison/Steel types immune; Poison absorbs
            if SideCondition.TOXIC_SPIKES in side_conds:
                is_poison = any(t is not None and t.name == "POISON" for t in incoming.types)
                is_steel = any(t is not None and t.name == "STEEL" for t in incoming.types)
                if is_poison:
                    notes.append("✓ Toxic Spikes: absorbed on entry (Poison-type clears your hazard!)")
                elif not is_steel:
                    layers = side_conds[SideCondition.TOXIC_SPIKES]
                    status = "Poison" if layers == 1 else "Badly Poisoned"
                    notes.append(f"⚠ Toxic Spikes ({layers}L): inflicts {status} on entry")

            # Sticky Web — Speed -1 on entry
            if SideCondition.STICKY_WEB in side_conds:
                notes.append("⚠ Sticky Web: Speed -1 on entry")

    except Exception:  # noqa: BLE001
        pass
    return notes

"""Type effectiveness and grounding.

The Gen 9 18-type chart lookups plus the grounding check that decides whether a
Pokémon is affected by ground-based entry hazards.
"""

from __future__ import annotations

from poke_env.battle import Pokemon


def _is_grounded(mon: Pokemon) -> bool:
    """True if the Pokémon is affected by Spikes / Toxic Spikes / Sticky Web.

    Approximation: Flying-type or Levitate ability are the most common
    immunity sources. Air Balloon and Magnet Rise are not tracked here.
    """
    try:
        if any(t is not None and t.name == "FLYING" for t in mon.types):
            return False
        ability = (mon.ability or "").lower()
        if ability == "levitate":
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _type_effectiveness_vs(attacker_type_name: str, defender: Pokemon) -> float:
    """Compute the combined type-effectiveness multiplier of one attacking type vs defender.

    poke-env's type_chart is keyed as {defending_type: {attacking_type: multiplier}},
    so we look up defender types as the outer key and the attacker as the inner key.
    """
    try:
        from poke_env.data.gen_data import GenData  # noqa: PLC0415
        gen = GenData.from_gen(9)
        type_chart = gen.type_chart
        mult = 1.0
        for def_type in defender.types:
            if def_type is None:
                continue
            row = type_chart.get(def_type.name.upper(), {})
            mult *= float(row.get(attacker_type_name.upper(), 1.0))
        return mult
    except Exception:  # noqa: BLE001
        return 1.0


def _effectiveness_label(mult: float) -> str:
    if mult == 0.0:
        return "immune (0×)"
    if mult >= 4.0:
        return "super effective (4×)"
    if mult >= 2.0:
        return "super effective (2×)"
    if mult == 1.0:
        return "neutral (1×)"
    if mult <= 0.25:
        return "not very effective (0.25×)"
    return "not very effective (0.5×)"

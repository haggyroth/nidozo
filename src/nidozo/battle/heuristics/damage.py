"""Damage math — stat stages, weather modifiers, speed, and damage estimation.

Everything here is arithmetic over battle state: it takes Pokémon and moves and
returns numbers. No advisory strings, no scoring decisions.
"""

from __future__ import annotations

from poke_env.battle import Pokemon
from poke_env.battle.move import Move
from poke_env.battle.move_category import MoveCategory

# ---------------------------------------------------------------------------
# Stat stage multipliers: stage -6..+6
# ---------------------------------------------------------------------------

_STAGE_MULT: dict[int, float] = {
    -6: 2/8, -5: 2/7, -4: 2/6, -3: 2/5, -2: 2/4, -1: 2/3,
     0: 1.0,
     1: 3/2,  2: 4/2,  3: 5/2,  4: 6/2,  5: 7/2,  6: 8/2,
}


def _stage_mult(stage: int) -> float:
    return _STAGE_MULT.get(max(-6, min(6, stage)), 1.0)


# ---------------------------------------------------------------------------
# Weather damage modifiers
# ---------------------------------------------------------------------------

# Maps (weather_name, move_type_name) → multiplier
_WEATHER_MODS: dict[tuple[str, str], float] = {
    ("RAINDANCE", "WATER"):  1.5,
    ("RAINDANCE", "FIRE"):   0.5,
    ("SUNNYDAY",  "FIRE"):   1.5,
    ("SUNNYDAY",  "WATER"):  0.5,
    # Sandstorm boosts Rock-type SpD but doesn't modify move damage
    # Hail/Snow has no move-damage modifier
}


def _weather_damage_mod(weather_name: str | None, move_type_name: str) -> float:
    if not weather_name:
        return 1.0
    return _WEATHER_MODS.get((weather_name.upper(), move_type_name.upper()), 1.0)


# ---------------------------------------------------------------------------
# Speed
# ---------------------------------------------------------------------------

def _comparable_speed(mon: Pokemon) -> float:
    """Speed on a scale both sides of the battle share (#289). Stat stages and
    paralysis applied — but from *base* speed, never ``mon.stats``.

    Both sides of any comparison must come from this function, or the comparison
    means nothing. ``mon.stats`` is populated from the battle request, which
    Showdown sends only to a Pokémon's owner; a Pokémon met in battle reaches us
    through ``|switch|``, which carries species, level and an HP percentage and
    never EVs, nature or IVs. So for our own side ``mon.stats[\"spe\"]`` is the
    real battle stat — a max-invested sweeper's 333 — while the opponent only
    ever has their species base, about 102.

    Comparing those two numbers is not a comparison. It is what this used to do,
    and the result was a near-constant "You move first", because the own side was
    carrying roughly 50-100 points of investment the opponent's number could not
    include. Against any speed-invested opponent the advisory was simply wrong.

    Base speed is the only quantity both sides have. The trade is explicit: this
    answers "who is faster if both are invested the same way", not who moves
    first. Nothing is lost from our own side by it — the model reads its exact
    stats in the serialized state (``actual_stats``) — and speed ties become
    meaningful again: with base stats they happen when the two species are
    genuinely matched, whereas comparing an invested stat against a bare base
    almost never produced one.
    """
    try:
        base_spd = float(mon.base_stats.get("spe", 80))
        stage_raw = mon.boosts.get("spe", 0)
        stage = int(stage_raw) if isinstance(stage_raw, int | float) else 0
        spd = base_spd * _stage_mult(stage)
        # Gen 9: paralysis reduces speed to 50%
        if mon.status and mon.status.name == "PAR":
            spd *= 0.5
        return spd
    except (TypeError, ValueError, AttributeError):
        return 80.0  # safe fallback


# ---------------------------------------------------------------------------
# Damage estimation
# ---------------------------------------------------------------------------

def _estimate_incoming_damage(
    move: Move,
    attacker: Pokemon,
    defender: Pokemon,
    weather: str | None,
) -> float | None:
    """Estimate damage % dealt to *defender* by *attacker* using *move*.

    Mirrors the formula in ``_score_move`` but from the opponent's perspective.
    Returns a float (percentage of defender's HP) or None on any error.
    """
    try:
        is_physical = move.category == MoveCategory.PHYSICAL
        atk_key = "atk" if is_physical else "spa"
        def_key = "def" if is_physical else "spd"

        # Attacker's offensive stat — base stat only (opponent stats are unknown)
        opp_atk_base = float(attacker.base_stats.get(atk_key, 80))
        opp_atk_stage = attacker.boosts.get(atk_key, 0)
        opp_atk = opp_atk_base * _stage_mult(int(opp_atk_stage))

        # Defender's defensive stat — use actual stats when available
        defender_stats = defender.stats or {}
        own_def_base = float(
            defender_stats.get(def_key)
            or defender.base_stats.get(def_key, 80)
        )
        own_def_stage = defender.boosts.get(def_key, 0)
        own_def = own_def_base * _stage_mult(int(own_def_stage))

        type_mult = defender.damage_multiplier(move)
        if type_mult == 0.0:
            return 0.0

        move_type_name = move.type.name if hasattr(move, "type") else ""
        w_mod = _weather_damage_mod(weather, move_type_name)

        # Damage formula (level 100, no crit, no random roll)
        raw = ((42 * move.base_power * opp_atk / own_def) / 50 + 2) * type_mult * w_mod

        # Defender HP pool — use actual HP stat if known, else base-stat approximation
        own_hp = float(
            defender_stats.get("hp")
            or (defender.base_stats.get("hp", 80) * 2 + 110)
        )
        return raw / own_hp * 100
    except Exception:  # noqa: BLE001
        return None

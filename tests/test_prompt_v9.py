"""Tests for prompt v9 — speed tier annotation in bench summary."""

from nidozo.llm.prompt_builder import PromptBuilder

# Minimal battle state with a bench mon and switch_scores that includes speed info
_BENCH_MON = {
    "species": "blastoise",
    "types": ["WATER"],
    "hp_fraction": 0.85,
    "status": None,
    "fainted": False,
    "item": "leftovers",
    "ability": "torrent",
    "tera_type": None,
}

_STATE_WITH_BENCH: dict = {
    "turn": 3,
    "format": "gen9nationaldex",
    "weather": None,
    "fields": [],
    "my_side_conditions": [],
    "opponent_side_conditions": [],
    "my_active": {
        "species": "pikachu",
        "types": ["ELECTRIC"],
        "hp_fraction": 0.6,
        "status": None,
        "boosts": {},
        "item": None,
        "ability": "static",
        "moves": {
            "thunderbolt": {
                "id": "thunderbolt",
                "type": "ELECTRIC",
                "category": "SPECIAL",
                "base_power": 90,
                "pp": 15,
                "max_pp": 15,
                "priority": 0,
            }
        },
        "effects": [],
        "fainted": False,
        "actual_stats": None,
        "last_move": None,
        "is_terastallized": False,
        "tera_type": None,
    },
    "my_team": [_BENCH_MON],
    "opponent_active": {
        "species": "charmander",
        "types": ["FIRE"],
        "hp_fraction": 0.9,
        "status": None,
        "boosts": {},
        "item": None,
        "ability": None,
        "revealed_moves": {},
        "fainted": False,
        "is_terastallized": False,
        "tera_type": None,
        "last_move": None,
        "moves_revealed": 0,
    },
    "opponent_team": [],
    "opponent_team_size_seen": 1,
    "available_moves": [
        {
            "id": "thunderbolt",
            "type": "ELECTRIC",
            "category": "SPECIAL",
            "base_power": 90,
            "accuracy": 1.0,
            "pp": 15,
            "max_pp": 15,
            "priority": 0,
        }
    ],
    "available_switches": ["blastoise"],
    "force_switch": False,
    "can_tera": False,
    "recent_events": [],
    "heuristics": {
        "battle_context": None,
        "move_scores": [
            {
                "move_id": "thunderbolt",
                "type_multiplier": 1.0,
                "effectiveness_label": "neutral (1×)",
                "estimated_damage_pct": "~30%",
                "accuracy_adjusted_pct": "~30%",
                "priority": 0,
                "is_status": False,
                "low_pp": False,
                "notes": [],
            }
        ],
        "switch_scores": [
            {
                "species": "blastoise",
                "hp_fraction": 0.85,
                "switch_quality": 1,
                "quality_label": "good",
                "defensive_vs_opp": "neutral",
                "speed_vs_opp": "faster (110 vs ~65)",
                "notes": ["Healthy HP (85%)"],
            }
        ],
    },
    "opponent_threat_map": [],
    "is_doubles": False,
}


def test_v9_loads() -> None:
    pb = PromptBuilder("v9")
    assert pb.version == "v9"


def test_v9_system_mentions_bench_speed() -> None:
    pb = PromptBuilder("v9")
    content = pb.build_system()["content"]
    assert "bench" in content.lower()
    assert "speed" in content.lower()


def test_v9_bench_shows_speed_note_when_present() -> None:
    pb = PromptBuilder("v9")
    content = pb.build_turn(_STATE_WITH_BENCH)["content"]
    # speed_vs_opp from switch_scores should appear in the bench section
    assert "faster (110 vs ~65)" in content


def test_v9_bench_speed_appears_before_heuristic_section() -> None:
    pb = PromptBuilder("v9")
    content = pb.build_turn(_STATE_WITH_BENCH)["content"]
    bench_idx = content.index("YOUR BENCH")
    heuristic_idx = content.index("HEURISTIC ADVISORY")
    speed_idx = content.index("faster (110 vs ~65)")
    # Speed note should appear in the bench section, not just the heuristic
    assert bench_idx < speed_idx < heuristic_idx


def test_v9_bench_no_speed_when_switch_scores_empty() -> None:
    """When switch_scores is empty (no available switches), bench has no speed note."""
    import copy
    state = copy.deepcopy(_STATE_WITH_BENCH)
    state["heuristics"]["switch_scores"] = []
    pb = PromptBuilder("v9")
    content = pb.build_turn(state)["content"]
    # The bench section still renders but without a speed annotation
    assert "Blastoise" in content
    assert "faster" not in content


def test_v9_bench_no_speed_when_switch_scores_missing_species() -> None:
    """Bench mon with no matching switch score shows no speed annotation."""
    import copy
    state = copy.deepcopy(_STATE_WITH_BENCH)
    # switch_scores has a different species — no match for blastoise
    state["heuristics"]["switch_scores"] = [
        {
            "species": "venusaur",
            "hp_fraction": 1.0,
            "switch_quality": 0,
            "quality_label": None,
            "defensive_vs_opp": "neutral",
            "speed_vs_opp": "slower (80 vs ~100)",
            "notes": [],
        }
    ]
    pb = PromptBuilder("v9")
    content = pb.build_turn(state)["content"]
    assert "Blastoise" in content
    assert "faster" not in content


def test_v9_bench_species_still_shown_with_speed() -> None:
    pb = PromptBuilder("v9")
    content = pb.build_turn(_STATE_WITH_BENCH)["content"]
    assert "Blastoise" in content
    assert "85.0%" in content


def test_v9_inherits_entry_hazard_content_from_v8() -> None:
    """v9 system prompt retains the entry hazard section from v8."""
    pb = PromptBuilder("v9")
    content = pb.build_system()["content"]
    assert "Entry hazards" in content
    assert "Stealth Rock" in content

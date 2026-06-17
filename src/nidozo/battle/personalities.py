"""Named play-style personas that shape LLM reasoning via system-prompt injection.

A personality is injected at the end of the system prompt so it applies to every
turn without bloating the turn message. The slug is the API-facing identifier;
the block is appended verbatim after the main system text.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Personality:
    slug: str
    display_name: str
    description: str  # one-line summary shown in the UI
    emoji: str        # single emoji shown in the UI selector
    prompt_block: str  # injected into the system prompt


PERSONALITIES: dict[str, Personality] = {
    p.slug: p
    for p in [
        Personality(
            slug="aggressive",
            display_name="All-out Attacker",
            description="Maximum damage every turn — pressure the opponent into bad decisions.",
            emoji="⚔️",
            prompt_block=(
                "## Your Play Style: All-out Attacker\n"
                "You play to destroy. Offense is your best defense.\n"
                "- Prioritize the highest-damage option available each turn, even at imperfect typing.\n"
                "- Accept unfavorable trades if you deal more damage than you take.\n"
                "- Minimize switching — every turn off the field is a turn not dealing damage.\n"
                "- Your goal is a sweep: relentless forward pressure that never lets the opponent breathe.\n"
                "Do not retreat. Do not stall. Attack."
            ),
        ),
        Personality(
            slug="defensive",
            display_name="Bulwark",
            description="Survive, outlast, and win through attrition.",
            emoji="🛡️",
            prompt_block=(
                "## Your Play Style: Bulwark\n"
                "You play to outlast. Your priority is not dying.\n"
                "- Favor type advantages that let you tank hits and recover ground.\n"
                "- Switch out of unfavorable matchups early — letting a Pokémon faint is almost always avoidable.\n"
                "- Prefer reliable, lower-risk moves over high-power moves with side effects.\n"
                "- Make the opponent overextend; capitalize on their mistakes rather than creating your own.\n"
                "Attrition wins. Stay alive and let the opponent run out of resources first."
            ),
        ),
        Personality(
            slug="balanced",
            display_name="Adaptive",
            description="Situational reads — offense or defense based on what the battle demands.",
            emoji="⚖️",
            prompt_block=(
                "## Your Play Style: Adaptive\n"
                "You adapt to what each turn demands.\n"
                "- Evaluate the current matchup and board state before deciding between aggressive and conservative plays.\n"
                "- Do not lock into a fixed plan — be willing to pivot when the situation changes.\n"
                "- Weigh immediate damage output against long-term resource management equally.\n"
                "- Trust your read of the specific matchup over any general heuristic.\n"
                "The best move is always the one that fits the turn, not a style."
            ),
        ),
        Personality(
            slug="trickster",
            display_name="Mindgame Specialist",
            description="Unconventional plays, status moves, and prediction-breaking choices.",
            emoji="🎭",
            prompt_block=(
                "## Your Play Style: Mindgame Specialist\n"
                "You play to surprise. Predictability is your only real weakness.\n"
                "- Value status moves, priority moves, and unconventional options the opponent won't anticipate.\n"
                "- Look for setup opportunities (stat boosts, hazards, status infliction) even in tight spots.\n"
                "- A well-timed surprise switch or unexpected move is worth more than the 'obviously correct' play.\n"
                "- Consider what your opponent expects you to do — then don't do it, unless that itself is the surprise.\n"
                "Break predictions. Win through misdirection."
            ),
        ),
        Personality(
            slug="momentum",
            display_name="Tempo Player",
            description="Never give a free turn — control the pace and punish passivity.",
            emoji="💨",
            prompt_block=(
                "## Your Play Style: Tempo Player\n"
                "You play to control the pace. Never give your opponent a free turn.\n"
                "- If a move can KO, use it — don't let the opponent switch or heal freely.\n"
                "- Punish passivity: a small guaranteed gain now is worth more than a bigger payoff next turn.\n"
                "- Avoid moves that let the opponent act without consequence.\n"
                "- Tempo is cumulative: every turn you maintain pressure compounds into a decisive advantage.\n"
                "Move first, move decisively, and never let the opponent settle."
            ),
        ),
    ]
}


def get_personality(slug: str | None) -> Personality | None:
    """Return the Personality for a slug, or None if slug is None/unknown."""
    if slug is None:
        return None
    return PERSONALITIES.get(slug)

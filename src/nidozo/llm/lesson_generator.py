"""Post-battle lesson generator.

After a battle finishes, each LLM player is asked to reflect on what happened
and produce a 2-3 sentence lesson.  The lesson is stored in the DB and injected
into future prompts so the model can adapt its strategy across battles.

The lesson call uses plain-text output (not JSON).  Callers must supply a
backend that is NOT in json_mode so that grammar sampling does not force
JSON-shaped output.
"""

from __future__ import annotations

import logging
from typing import Any

from nidozo.llm.backend import Message, ModelBackend
from nidozo.llm.timeouts import complete_within, resolve_llm_timeout

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a competitive Pokémon trainer reviewing your most recent battle. "
    "Your goal is to extract a concise, actionable lesson that will improve your "
    "future play. Be honest about mistakes."
)

_MAX_TURNS_SHOWN = 20  # cap turn list to keep the prompt short


def _result_label(winner: int | None, player_role: str) -> str:
    if winner is None:
        return "Draw"
    role_num = 1 if player_role == "p1" else 2
    return "Win" if winner == role_num else "Loss"


def _format_turns(turns: list[dict[str, Any]], player_role: str) -> str:
    """Format a player's turns into a compact bullet list."""
    my_turns = [t for t in turns if t.get("player_role") == player_role]
    # Most recent turns are most informative; show up to _MAX_TURNS_SHOWN
    my_turns = my_turns[-_MAX_TURNS_SHOWN:]
    if not my_turns:
        return "  (no turns recorded)"
    lines = []
    for t in my_turns:
        action = t.get("action_chosen") or "(none)"
        ok = t.get("parse_success", 1)
        flag = "" if ok else " ⚠ parse failed → random"
        lines.append(f"  Turn {t['turn_number']}: {action}{flag}")
    return "\n".join(lines)


def _extract_move_name(raw: str | None) -> str:
    """Pull a readable move name out of a raw action string.

    Handles both the poke-env format ("/choose move fireblast") and the
    annotator's best_action format ("move 2 (thunderbolt)").
    """
    if not raw:
        return "?"
    raw = raw.strip()
    # annotator format: "move 2 (thunderbolt)"
    if "(" in raw and raw.endswith(")"):
        inner = raw[raw.index("(") + 1 : -1]
        if inner:
            return inner
    # poke-env /choose format: "/choose move fireblast" or "move fireblast"
    for prefix in ("/choose move ", "move "):
        if raw.lower().startswith(prefix):
            token = raw[len(prefix):].strip()
            # strip trailing slot number if numeric
            if token.isdigit():
                return f"slot {token}"
            return token
    # switch format
    for prefix in ("/choose switch ", "switch "):
        if raw.lower().startswith(prefix):
            return raw[len(prefix):].strip()
    return raw


def _format_draft_critique_block(critique: dict[str, Any]) -> str:
    """Render team composition data into a context block for the lesson prompt.

    Covers: team members, shared type weaknesses, decision quality %, and the
    most impactful deviations from the heuristic top move (with move names).
    """
    lines: list[str] = []

    team = critique.get("team") or []
    if team:
        lines.append(f"Your team this battle: {', '.join(team)}")

    # Shared weaknesses — the most actionable team-composition insight
    shared = critique.get("shared_weaknesses") or []
    if shared:
        types_str = ", ".join(t.title() for t in shared)
        lines.append(
            f"Shared team weaknesses (≥2 members 2× weak): {types_str}"
        )

    # Offensive coverage
    offensive = sorted(critique.get("offensive_types") or [])
    if offensive:
        lines.append(f"Your team's STAB types: {', '.join(t.title() for t in offensive)}")

    # Decision quality %
    exec_data = critique.get("execution") or {}
    quality_pct = exec_data.get("decision_quality_pct")
    blunder_count = exec_data.get("blunders", 0)
    if quality_pct is not None:
        blunder_tag = (
            f" — including {blunder_count} blunder{'s' if blunder_count != 1 else ''}"
            if blunder_count else ""
        )
        lines.append(f"Decision quality this battle: {quality_pct}% optimal/good turns{blunder_tag}")

    # Deviation turns: show blunders first, then other deviations, capped at 3
    dev_turns = exec_data.get("deviation_turns") or []
    blunder_devs = [d for d in dev_turns if d.get("was_blunder")]
    other_devs   = [d for d in dev_turns if not d.get("was_blunder")]
    show_devs = (blunder_devs + other_devs)[:3]

    if show_devs:
        lines.append("Notable move choices vs. heuristic recommendation:")
        for d in show_devs:
            chose = _extract_move_name(d.get("chose"))
            best  = _extract_move_name(d.get("best"))
            gap   = d.get("gap_pct", 0)
            tag   = " [BLUNDER]" if d.get("was_blunder") else ""
            lines.append(
                f"  Turn {d['turn_number']}: chose {chose} — heuristic top was {best} ({gap}% gap){tag}"
            )

    return "\n".join(lines)


def _format_variance_block(variance_report: dict[str, Any], player_role: str) -> str:
    """Render the RNG variance context so the model can correctly attribute wins/losses."""
    lines: list[str] = []

    verdict = variance_report.get("verdict", "")
    if verdict:
        lines.append(f"RNG summary: {verdict}")

    # Crits and misses with attacker info — translate to benefit framing for this player
    crits  = variance_report.get("crits")  or []
    misses = variance_report.get("misses") or []

    # Events that benefited *this* player
    my_role   = player_role
    opp_role  = "p2" if my_role == "p1" else "p1"
    my_benefits  = (
        [f"turn {c['turn_number']} (crit for you)"  for c in crits  if c.get("attacker") == my_role] +
        [f"turn {m['turn_number']} (miss for opponent)" for m in misses if m.get("attacker") == opp_role]
    )
    opp_benefits = (
        [f"turn {c['turn_number']} (crit for opponent)" for c in crits  if c.get("attacker") == opp_role] +
        [f"turn {m['turn_number']} (miss for you)"   for m in misses if m.get("attacker") == my_role]
    )

    if my_benefits:
        lines.append(f"  RNG in your favour: {', '.join(my_benefits[:4])}")
    if opp_benefits:
        lines.append(f"  RNG against you: {', '.join(opp_benefits[:4])}")

    return "\n".join(lines)


def _format_win_prob_context(
    timeline: list[dict[str, Any]],
    turning_point: int | None,
    player_role: str,
) -> str:
    """Describe the win-probability swing at the turning point."""
    if not timeline or turning_point is None:
        return ""

    # Index by turn number
    by_turn: dict[int, float] = {
        row["turn_number"]: row["p1_win_prob"]
        for row in timeline
        if "turn_number" in row and "p1_win_prob" in row
    }
    if not by_turn:
        return ""

    # For p2, flip the probability
    def my_prob(p1_prob: float) -> float:
        return p1_prob if player_role == "p1" else 1.0 - p1_prob

    tp_prob = by_turn.get(turning_point)
    # Try the turn immediately after the turning point for the "after" value
    turns_sorted = sorted(by_turn.keys())
    tp_next: float | None = None
    for t in turns_sorted:
        if t > turning_point:
            tp_next = by_turn[t]
            break

    if tp_prob is None:
        return ""

    my_before = my_prob(tp_prob)
    if tp_next is not None:
        my_after = my_prob(tp_next)
        swing = my_after - my_before
        direction = "dropped" if swing < 0 else "rose"
        lines = [
            f"Win probability at turning point (turn {turning_point}): "
            f"~{round(my_before * 100)}% → {round(my_after * 100)}% "
            f"({direction} {round(abs(swing) * 100)} pp next turn)"
        ]
    else:
        lines = [
            f"Win probability at turning point (turn {turning_point}): ~{round(my_before * 100)}%"
        ]

    return "\n".join(lines)


def _format_analysis_context(analysis: dict[str, Any], player_role: str) -> str:
    """Render all available analysis data for this player into a context block.

    Sections (each omitted when data is absent):
      1. Team composition & weaknesses (draft critique — drafted battles only)
      2. Decision quality summary
      3. Top blunders with move names and gap details
      4. Win-probability turning point with swing magnitude
      5. RNG variance verdict and per-player benefit list
    """
    summary_key   = f"{player_role}_summary"
    critique_key  = f"{player_role}_draft_critique"

    summary: dict[str, Any]       = analysis.get(summary_key) or {}
    key_moments: list[dict[str, Any]] = analysis.get("key_moments") or []
    critique: dict[str, Any] | None   = analysis.get(critique_key)
    variance: dict[str, Any] | None   = analysis.get("variance_report")
    timeline: list[dict[str, Any]]    = analysis.get("win_probability_timeline") or []
    turning_point: int | None         = analysis.get("turning_point")

    sections: list[str] = []

    # ── 1. Draft critique (team + weaknesses + deviation turns) ──────────────
    if critique:
        block = _format_draft_critique_block(critique)
        if block:
            sections.append(block)

    # ── 2. Decision quality summary (when no draft critique, or as supplement) ──
    else:
        # Draft critique already includes decision_quality_pct; only emit a
        # standalone summary when there's no critique to avoid repetition.
        total = summary.get("total_turns", 0)
        if total > 0:
            optimal  = summary.get("optimal", 0)
            good     = summary.get("good", 0)
            subopt   = summary.get("suboptimal", 0)
            fallback = summary.get("fallback", 0)
            blunders = summary.get("blunders", 0)
            avg_rank = summary.get("avg_heuristic_rank")

            quality_lines = [
                "Decision quality this battle:",
                f"  {optimal} optimal, {good} good, {subopt} suboptimal"
                + (f", {fallback} fallback (parse failures)" if fallback else ""),
            ]
            if avg_rank is not None:
                quality_lines.append(f"  Average heuristic rank: {avg_rank:.1f}")
            if blunders:
                quality_lines.append(
                    f"  Blunders (moves ≥40% below best option): {blunders}"
                )
            sections.append("\n".join(quality_lines))

    # ── 3. Top blunders with full move detail ─────────────────────────────────
    # Prefer the structured blunders list (has score_gap for sorting + full notes)
    # over key_moments, which is already sorted by turn.  Show worst-gap first.
    structured_blunders: list[dict[str, Any]] = [
        b for b in (analysis.get("blunders") or [])
        if b.get("player_role") == player_role
    ]
    structured_blunders.sort(key=lambda b: b.get("score_gap") or 0.0, reverse=True)

    if structured_blunders:
        blunder_lines = ["Your worst move decisions (most impactful first):"]
        for b in structured_blunders[:3]:
            notes = b.get("notes") or (
                f"Turn {b['turn_number']}: {b.get('action') or '?'} "
                f"({round((b.get('score_gap') or 0) * 100)}% below best option)"
            )
            blunder_lines.append(f"  Turn {b['turn_number']}: {notes}")
        sections.append("\n".join(blunder_lines))
    else:
        # Fall back to key_moments blunders (random-battle path)
        my_blunders = [
            m for m in key_moments
            if m.get("type") == "blunder" and m.get("player_role") == player_role
        ]
        if my_blunders:
            km_lines = ["Your worst decisions:"]
            for m in my_blunders[:3]:
                km_lines.append(f"  Turn {m['turn_number']}: {m['description']}")
            sections.append("\n".join(km_lines))

    # ── 4. Win-probability turning point with swing magnitude ─────────────────
    wp_block = _format_win_prob_context(timeline, turning_point, player_role)
    if wp_block:
        sections.append(wp_block)
    elif turning_point is not None:
        # Fallback: just the turn number
        sections.append(
            f"Turning point: turn {turning_point} had the largest win-probability swing."
        )

    # ── 5. Variance report ────────────────────────────────────────────────────
    if variance:
        var_block = _format_variance_block(variance, player_role)
        if var_block:
            sections.append(var_block)
    else:
        # Legacy: extract RNG events from key_moments
        my_rng = [
            m for m in key_moments
            if m.get("type") == "rng" and m.get("player_role") == player_role
        ]
        if my_rng:
            rng_lines = ["RNG events affecting you:"]
            for m in my_rng[:4]:
                rng_lines.append(f"  Turn {m['turn_number']}: {m['description']}")
            sections.append("\n".join(rng_lines))

    return "\n\n".join(sections)


async def generate_lesson(
    backend: ModelBackend,
    player_role: str,
    winner: int | None,
    total_turns: int,
    opponent_label: str,
    turns: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> str:
    """Ask the LLM to reflect on the battle and return a 2-3 sentence lesson.

    Args:
        backend:        A ModelBackend instance with json_mode=False.
        player_role:    "p1" or "p2".
        winner:         1, 2, or None (tie).
        total_turns:    Total turns played.
        opponent_label: Human-readable opponent identifier (e.g. "random/random").
        turns:          Full turn log from BattleStore.get_turns_basic().
        analysis:       Optional output from analyze_battle(); enriches the lesson
                        prompt with quality annotations, blunders, draft critique,
                        variance report, and win-probability context.
        timeout:        Deadline in seconds for the lesson call (#278).  ``None``
                        uses ``NIDOZO_LLM_TIMEOUT``; zero or negative disables it.

    Returns:
        A plain-text lesson string, or "" if the backend fails.
    """
    result = _result_label(winner, player_role)
    turn_summary = _format_turns(turns, player_role)

    analysis_block = ""
    has_blunders   = False
    has_critique   = False
    has_variance   = False

    if analysis:
        ctx = _format_analysis_context(analysis, player_role)
        if ctx:
            analysis_block = f"\nPost-battle analysis:\n{ctx}\n"

        # Track what's available to tune the closing instruction
        blunders = [
            b for b in (analysis.get("blunders") or [])
            if b.get("player_role") == player_role
        ]
        has_blunders = bool(blunders) or any(
            m.get("type") == "blunder" and m.get("player_role") == player_role
            for m in (analysis.get("key_moments") or [])
        )
        critique_key = f"{player_role}_draft_critique"
        has_critique = bool(analysis.get(critique_key))
        variance = analysis.get("variance_report") or {}
        has_variance = variance.get("total_events", 0) > 0

    # Build a targeted closing instruction based on what data is present
    specificity_lines = [
        "Write exactly 2-3 sentences.",
        "The first sentence must be specific: name an actual turn number, move name, "
        "or team weakness from the analysis — not generic advice.",
    ]
    if has_blunders:
        specificity_lines.append(
            "A blunder is listed above — it must be the centerpiece of your lesson. "
            "Name the move you chose and what you should have played instead."
        )
    if has_critique:
        specificity_lines.append(
            "If a team weakness was exposed by a blunder, connect them explicitly."
        )
    if has_variance:
        specificity_lines.append(
            "Use the RNG summary to correctly attribute this result to skill vs. luck — "
            "don't blame variance if it was roughly even."
        )
    specificity_lines.append(
        "Write in first person. Plain text only — no JSON, no bullet points, no headers."
    )

    user_content = (
        f"You just played a Gen 3 random battle.\n"
        f"Result: {result} in {total_turns} turns against {opponent_label}.\n"
        f"{analysis_block}\n"
        f"Your decisions this battle:\n{turn_summary}\n\n"
        + "\n".join(specificity_lines)
    )

    messages: list[Message] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        lesson = await complete_within(
            backend, messages, timeout=resolve_llm_timeout(timeout),
            what=f"Lesson generation ({player_role})",
        )
        lesson = lesson.strip()
        if not lesson:
            logger.warning("Lesson generator returned empty string for %s", player_role)
        return lesson
    except Exception as exc:
        logger.error("Lesson generation failed for %s: %s", player_role, exc)
        return ""

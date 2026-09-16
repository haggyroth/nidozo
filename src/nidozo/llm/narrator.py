"""Battle narrator — generates a short plain-English story of a completed battle.

Produces a 4-6 sentence narrative that weaves together:
  - who played better (decision quality)
  - the turning point and what happened there
  - any significant blunders
  - whether variance (crits/misses) influenced the result

The narrative is stored in ``battles.narrative`` and shown at the top of the
analysis panel in the frontend.  It is intentionally concise and opinionated —
meant to be read in 15 seconds, not studied.
"""

from __future__ import annotations

import logging
from typing import Any

from nidozo.llm.backend import Message, ModelBackend
from nidozo.llm.timeouts import complete_within, resolve_llm_timeout

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a concise Pokémon battle commentator. "
    "Write short, engaging summaries of Gen 3 battles. "
    "Be specific: name moves, Pokémon, and turn numbers when relevant. "
    "Plain text only — no bullet points, no headers, no JSON."
)

_MAX_KEY_MOMENTS = 5


def _fmt_summary(summary: dict[str, Any], label: str) -> str:
    total = summary.get("total_turns", 0)
    if total == 0:
        return f"{label}: no turn data"
    optimal  = summary.get("optimal", 0)
    good     = summary.get("good",    0)
    blunders = summary.get("blunders", 0)
    qpct     = summary.get("decision_quality_pct")
    switches = summary.get("switch_turns", 0)

    qstr = f"{qpct}% quality" if qpct is not None else f"{optimal + good}/{total} good turns"
    bstr = f", {blunders} blunder{'s' if blunders != 1 else ''}" if blunders else ""
    sstr = f", {switches} switch{'es' if switches != 1 else ''}" if switches > 0 else ""
    return f"{label}: {qstr}{bstr}{sstr}"


def _build_narrative_context(
    analysis: dict[str, Any],
    p1_label: str,
    p2_label: str,
    winner_label: str,
    total_turns: int,
) -> str:
    """Format analysis data into a compact context block for the LLM."""
    lines: list[str] = []

    lines.append(f"Result: {winner_label} won in {total_turns} turns.")
    lines.append(f"Players: {p1_label} (P1) vs {p2_label} (P2).")

    p1_sum = analysis.get("p1_summary") or {}
    p2_sum = analysis.get("p2_summary") or {}
    if p1_sum.get("total_turns", 0) > 0 or p2_sum.get("total_turns", 0) > 0:
        lines.append(_fmt_summary(p1_sum, p1_label))
        lines.append(_fmt_summary(p2_sum, p2_label))

    # Key moments
    moments: list[dict[str, Any]] = (analysis.get("key_moments") or [])[:_MAX_KEY_MOMENTS]
    if moments:
        lines.append("Key moments:")
        for m in moments:
            role_str = ""
            if m.get("player_role"):
                role_str = f"[{p1_label if m['player_role'] == 'p1' else p2_label}] "
            lines.append(f"  T{m['turn_number']} {role_str}({m['type']}): {m['description']}")

    # Variance
    vr = analysis.get("variance_report") or {}
    verdict = vr.get("verdict", "")
    if verdict and vr.get("total_events", 0) > 0:
        lines.append(f"RNG: {verdict}")
        crits  = vr.get("crits")  or []
        misses = vr.get("misses") or []
        if crits:
            lines.append(f"  Possible crits at turns: {', '.join(str(c['turn_number']) for c in crits[:4])}")
        if misses:
            lines.append(f"  Possible misses at turns: {', '.join(str(m['turn_number']) for m in misses[:4])}")

    return "\n".join(lines)


async def generate_battle_narrative(
    backend: ModelBackend,
    analysis: dict[str, Any],
    p1_label: str,
    p2_label: str,
    winner: int | None,
    total_turns: int,
    timeout: float | None = None,
) -> str:
    """Generate a 4-6 sentence narrative of the completed battle.

    Args:
        backend:      Any ModelBackend with json_mode=False.
        analysis:     Output from analyze_battle().
        p1_label:     Human-readable P1 identifier.
        p2_label:     Human-readable P2 identifier.
        winner:       1, 2, or None (tie).
        total_turns:  Total turns played.
        timeout:      Deadline in seconds for the narrative call (#278).  ``None``
                      uses ``NIDOZO_LLM_TIMEOUT``; zero or negative disables it.

    Returns:
        A plain-text narrative string, or "" on failure.
    """
    if winner == 1:
        winner_label = p1_label
    elif winner == 2:
        winner_label = p2_label
    else:
        winner_label = "neither player (tie)"

    context = _build_narrative_context(analysis, p1_label, p2_label, winner_label, total_turns)

    user_content = (
        f"{context}\n\n"
        "Write a 4-6 sentence battle narrative. "
        "Describe what happened, who had the edge in decision-making, "
        "any turning points or blunders that shaped the result, "
        "and whether RNG played a role. "
        "Be specific — name actual moves and Pokémon if you have them. "
        "End with a one-sentence verdict on why the winner won. "
        "Plain text only."
    )

    messages: list[Message] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    try:
        result = await complete_within(
            backend, messages, timeout=resolve_llm_timeout(timeout),
            what="Battle narrative",
        )
        return (result or "").strip()
    except Exception as exc:
        logger.warning("Battle narrative generation failed: %s", exc)
        return ""

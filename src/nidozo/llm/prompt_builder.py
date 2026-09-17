"""
PromptBuilder — loads versioned prompt templates and renders turn messages.

Templates live at src/nidozo/llm/prompts/<version>/:
  system.txt        — static system prompt (loaded once)
  turn.txt.jinja    — Jinja2 template rendered each turn with the battle state dict
  turn_doubles.txt.jinja
                    — optional 2v2 variant, required for doubles battles
                      (present in DOUBLES_PROMPT_VERSION only)

Changing prompt content = bump the version directory (v1 → v2). The version
string is stored on the builder so it can be persisted with battle records and
correlated with ELO changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from nidozo.llm.backend import Message
from nidozo.llm.versions import (
    ALL_PROMPT_VERSIONS,
    DEFAULT_PROMPT_VERSION,
    DOUBLES_PROMPT_VERSION,
    DRAFT_PROMPT_VERSION,
    PromptVersion,
)

__all__ = [
    "ALL_PROMPT_VERSIONS",
    "DEFAULT_PROMPT_VERSION",
    "DOUBLES_PROMPT_VERSION",
    "DRAFT_PROMPT_VERSION",
    "PromptBuilder",
    "PromptVersion",
    "resolve_prompt_version",
]

_PROMPTS_ROOT = Path(__file__).parent / "prompts"

# Version identifiers live in nidozo.llm.versions (#285) — the DB layer records
# them too, and imports them from there rather than through this module, which
# would drag Jinja2 into schema definition. Re-exported above so existing
# ``from nidozo.llm.prompt_builder import DOUBLES_PROMPT_VERSION`` keeps working.


def resolve_prompt_version(
    requested: str,
    *,
    doubles: bool = False,
    draft: bool = False,
) -> str:
    """Return the prompt version a battle must actually run under.

    Doubles and draft each pin their own template version, so ``requested`` is
    ignored for those. Callers should go through this rather than rewriting the
    version inline — the override is a hard requirement (doubles on a version
    without a doubles template raises), so it belongs in one place.
    """
    if doubles:
        return DOUBLES_PROMPT_VERSION
    if draft:
        return DRAFT_PROMPT_VERSION
    return requested


class PromptBuilder:
    def __init__(self, version: str = DEFAULT_PROMPT_VERSION) -> None:
        self.version = version
        self._version_dir = _PROMPTS_ROOT / version

        if not self._version_dir.is_dir():
            raise ValueError(
                f"Prompt version '{version}' not found at {self._version_dir}"
            )

        self._system_text = (self._version_dir / "system.txt").read_text()

        self._jinja_env = Environment(
            loader=FileSystemLoader(str(self._version_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._turn_template = self._jinja_env.get_template("turn.txt.jinja")
        # Doubles turn template — ships only in versions that support 2v2
        # (currently just DOUBLES_PROMPT_VERSION). Required, not optional: a
        # doubles state has a different shape and build_turn raises without it.
        self._turn_doubles_path = self._version_dir / "turn_doubles.txt.jinja"
        self._turn_doubles_template = (
            self._jinja_env.get_template("turn_doubles.txt.jinja")
            if self._turn_doubles_path.is_file() else None
        )

    @property
    def supports_doubles(self) -> bool:
        """True when this version ships a doubles (2v2) turn template."""
        return self._turn_doubles_template is not None

    def build_system(
        self,
        lessons: list[str] | None = None,
        personality: str | None = None,
    ) -> Message:
        """Return the system message, optionally appending memory and/or a persona."""
        from nidozo.battle.personalities import get_personality

        content = self._system_text
        if personality:
            persona = get_personality(personality)
            if persona:
                content = f"{content}\n\n{persona.prompt_block}"
        if lessons:
            memory_lines = "\n".join(f"{i}. {lesson}" for i, lesson in enumerate(lessons, 1))
            content = (
                f"{content}\n\n"
                f"## Your Battle Memory\n"
                f"Based on your previous battles, you have learned:\n"
                f"{memory_lines}\n\n"
                f"Apply these lessons as you make decisions this battle."
            )
        return Message(role="system", content=content)

    def build_turn(self, battle_state: dict[str, Any]) -> Message:
        # Doubles renders a distinct template (two active slots, per-slot moves
        # and targets). Falling back to the singles template is not a graceful
        # degradation: the shapes differ, so it either emits nonsense or — with
        # StrictUndefined — an UndefinedError naming a template variable, which
        # hides the real problem (the wrong version for this battle).
        if battle_state.get("is_doubles"):
            if self._turn_doubles_template is None:
                raise ValueError(
                    f"Prompt version '{self.version}' has no doubles template, so it "
                    f"cannot render a doubles battle state. Doubles requires "
                    f"'{DOUBLES_PROMPT_VERSION}' — use resolve_prompt_version() to "
                    f"pick the version for a battle. Missing template: "
                    f"{self._turn_doubles_path}"
                )
            rendered = self._turn_doubles_template.render(**battle_state)
        else:
            rendered = self._turn_template.render(**battle_state)
        return Message(role="user", content=rendered)

    def build_messages(
        self,
        battle_state: dict[str, Any],
        lessons: list[str] | None = None,
        coach_advice: str | None = None,
        personality: str | None = None,
    ) -> list[Message]:
        """Return [system, turn] ready to pass to a ModelBackend.

        Args:
            battle_state:  Serialized battle dict from serialize_battle().
            lessons:       Optional prior-battle lesson strings to inject into
                           the system prompt as the model's "memory".
            coach_advice:  Optional free-form text from a CoachAgent.  When
                           provided it is appended to the turn message so the
                           player can weigh it alongside the heuristic scores.
            personality:   Optional play-style persona slug to inject into the
                           system prompt (see nidozo.battle.personalities).
        """
        turn = self.build_turn(battle_state)
        if coach_advice:
            turn = Message(
                role="user",
                content=(
                    turn["content"]
                    + f"\n\n--- COACH ANALYSIS ---\n{coach_advice}\n---"
                    "\n\nWith this analysis in mind, what is your chosen action?"
                ),
            )
        return [self.build_system(lessons=lessons, personality=personality), turn]

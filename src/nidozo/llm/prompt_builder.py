"""
PromptBuilder — loads versioned prompt templates and renders turn messages.

Templates live at src/nidozo/llm/prompts/<version>/:
  system.txt        — static system prompt (loaded once)
  turn.txt.jinja    — Jinja2 template rendered each turn with the battle state dict

Changing prompt content = bump the version directory (v1 → v2). The version
string is stored on the builder so it can be persisted with battle records and
correlated with ELO changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from nidozo.llm.backend import Message

_PROMPTS_ROOT = Path(__file__).parent / "prompts"


class PromptBuilder:
    def __init__(self, version: str = "v1") -> None:
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
        # Optional doubles turn template — present only in versions that support
        # 2v2 (v7+). When a battle state has is_doubles=True and this template
        # exists, build_turn renders it instead of the singles template.
        doubles_path = self._version_dir / "turn_doubles.txt.jinja"
        self._turn_doubles_template = (
            self._jinja_env.get_template("turn_doubles.txt.jinja")
            if doubles_path.is_file() else None
        )

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
        # Doubles battles render a distinct template (two active slots, target
        # field) when the version provides one; otherwise fall back to singles.
        if battle_state.get("is_doubles") and self._turn_doubles_template is not None:
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

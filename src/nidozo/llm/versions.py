"""Prompt-version identifiers (#285).

Three layers need to name a prompt version: the LLM layer ships the templates
under ``llm/prompts/<version>/``, the API layer validates the version a request
asks for, and the DB layer records which version a battle ran under — as Python
defaults *and* as SQL column defaults in ``db/schema.py``.

They used to name them independently, and drifted: `PromptBuilder`,
`LLMPlayer`, and `BattleStore.get_or_create_model` each defaulted to ``"v1"``
while the API defaulted to ``"v9"``, so anything constructed without an explicit
version silently ran the oldest prompt in the repo. The identifiers live here
now, next to each other, so a bump is one edit.

This module is deliberately dependency-free (``typing`` only).
``prompt_builder`` re-exports everything, so
``from nidozo.llm.prompt_builder import DEFAULT_PROMPT_VERSION`` keeps working,
but the DB layer can import from here without pulling Jinja2 into schema
definition just to write a column default.
"""

from __future__ import annotations

from typing import Final, Literal, get_args

# Every prompt version that exists on disk. This is the API's validation
# contract — an unknown version is rejected at the boundary (422) rather than
# failing deep inside a background battle.
PromptVersion = Literal["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"]

# Derived, so the tuple and the Literal cannot disagree.
ALL_PROMPT_VERSIONS: Final[tuple[str, ...]] = get_args(PromptVersion)

# The singles turn template a battle runs under when no version is requested.
# Must be the newest directory under llm/prompts/ that ships turn.txt.jinja — a
# test pins that, so adding v10 without moving this constant fails the suite
# rather than silently running v9.
DEFAULT_PROMPT_VERSION: Final[PromptVersion] = "v9"

# The prompt version that ships the doubles (2v2) turn template. Doubles is not
# a superset of singles — serializer.py emits a different state shape for it
# (my_active is a *list* of slot dicts, available_moves a list of lists), which
# the singles templates cannot render. See resolve_prompt_version().
DOUBLES_PROMPT_VERSION: Final[PromptVersion] = "v7"

# The prompt version used for the team-draft phase.
DRAFT_PROMPT_VERSION: Final[PromptVersion] = "v3"

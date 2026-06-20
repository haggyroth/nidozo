"""Token pricing → estimated USD cost (#225).

Prices are USD per 1,000,000 tokens, keyed by model name, loaded from
``data/model_prices.json`` and cached. Models absent from the table (local
LM Studio models, ``random``) cost nothing. Edit the JSON to update prices —
no code change needed.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PRICES_PATH = Path(__file__).parent.parent.parent.parent / "data" / "model_prices.json"


@lru_cache(maxsize=1)
def _load_prices() -> dict[str, dict[str, float]]:
    try:
        raw: dict[str, Any] = json.loads(_PRICES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    # Drop any documentation key; keep only {model: {input, output}} entries.
    return {
        name: {"input": float(p.get("input", 0.0)), "output": float(p.get("output", 0.0))}
        for name, p in raw.items()
        if isinstance(p, dict)
    }


def model_price(model_name: str) -> dict[str, float] | None:
    """Return ``{'input': $/1M, 'output': $/1M}`` for a model, or None if unpriced."""
    return _load_prices().get(model_name)


def estimate_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD estimate for the given token counts; 0.0 for unpriced (local) models."""
    price = _load_prices().get(model_name)
    if not price:
        return 0.0
    return (
        (prompt_tokens / 1_000_000.0) * price["input"]
        + (completion_tokens / 1_000_000.0) * price["output"]
    )

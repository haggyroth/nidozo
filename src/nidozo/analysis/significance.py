"""Binomial significance test for bake-off experiment results (#226).

A bake-off asks: across N decided battles, did variant A beat variant B more
often than a fair coin would explain? We use an exact two-sided binomial test
against p = 0.5 (ties excluded), so "11-9" is correctly reported as *not*
significant while "18-2" is.
"""

from __future__ import annotations

from math import comb
from typing import Any


def two_sided_binomial_p(wins: int, losses: int) -> float:
    """Exact two-sided binomial p-value vs a fair (p=0.5) coin.

    H0: each decided battle is a 50/50 toss. ``n = wins + losses``. Returns the
    probability, under H0, of a split at least as extreme (either direction) as
    observed. Returns 1.0 when there are no decided battles.
    """
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    # By symmetry the two tails are equal: 2 * P(X <= k) under Binomial(n, 0.5),
    # capped at 1.0 (which is what a perfectly even split returns).
    tail = sum(comb(n, i) for i in range(k + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def bakeoff_result(
    a_wins: int, b_wins: int, ties: int, *, alpha: float = 0.05
) -> dict[str, Any]:
    """Build the result summary for a bake-off from raw counts."""
    n_decided = a_wins + b_wins
    p = two_sided_binomial_p(a_wins, b_wins)
    return {
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
        "n_decided": n_decided,
        "win_rate_a": round(a_wins / n_decided, 4) if n_decided else None,
        "p_value": round(p, 4),
        "significant": n_decided > 0 and p < alpha,
        "alpha": alpha,
    }

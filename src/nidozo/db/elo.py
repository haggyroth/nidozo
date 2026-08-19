"""Rating calculation — pure functions, no I/O.

The live rating system is **Glicko-2**: each model carries a rating, a rating
deviation (RD, the uncertainty around that rating) and a volatility (how
erratic its results have been). A model at 1200 ± 40 has demonstrably earned
its place; a model at 1200 ± 300 has played three games and could be anywhere.
Plain Elo cannot express that difference, which is misleading for a
benchmark-flavoured project.

Two deliberate choices:

- **Centred on DEFAULT_RATING (1000), not Glicko's customary 1500.** The scale
  is arbitrary as long as it is applied consistently, and keeping 1000 means
  existing ratings and the `rating` column carry over unchanged. Pass an
  explicit ``center`` to work on another scale.
- **One battle per rating period.** Glicko-2 is specified over a period holding
  several games; Nidozo rates every battle as it finishes. The consequence is
  slightly faster RD decay than a batched period would give, in exchange for
  ratings that are correct the moment a battle ends.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# --- Glicko-2 -------------------------------------------------------------

DEFAULT_RATING = 1000.0
DEFAULT_RD = 350.0          # an unplayed model could be almost anywhere
DEFAULT_VOLATILITY = 0.06   # Glickman's suggested starting sigma
TAU = 0.5                   # constrains volatility change; 0.3–1.2 is sane
PROVISIONAL_RD = 100.0      # at or above this, a rating is still provisional

_SCALE = 173.7178   # converts between the Glicko-2 internal scale and ours
_EPSILON = 1e-6     # convergence threshold for the volatility solver

# --- Legacy plain Elo -----------------------------------------------------
# Superseded by Glicko-2 for live rating (#231). Retained because every
# elo_history row written before the migration was produced by this function,
# so it remains the only way to reproduce that history.

K_FACTOR = 32


@dataclass(frozen=True)
class Rating:
    """A Glicko-2 rating: the estimate, its uncertainty, and its volatility."""

    rating: float = DEFAULT_RATING
    rd: float = DEFAULT_RD
    volatility: float = DEFAULT_VOLATILITY

    @property
    def provisional(self) -> bool:
        """True while RD is too wide for the rating to be taken at face value."""
        return self.rd >= PROVISIONAL_RD

    @property
    def interval(self) -> tuple[float, float]:
        """The ~95% confidence interval, rating ± 2·RD."""
        return (self.rating - 2.0 * self.rd, self.rating + 2.0 * self.rd)


def expected_score(rating_a: float, rating_b: float) -> float:
    """Probability that player A beats player B, ignoring uncertainty.

    Legacy plain-Elo expectation. ``win_probability`` is the Glicko-2-aware
    version and should be preferred for anything user-facing.
    """
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def updated_ratings(
    rating_a: float,
    rating_b: float,
    winner: int | None,  # 1=A wins, 2=B wins, None=tie
    k: float = K_FACTOR,
) -> tuple[float, float]:
    """Return (new_rating_a, new_rating_b) after one game, under plain Elo.

    Legacy. ``updated_glicko`` is the live path.
    """
    e_a = expected_score(rating_a, rating_b)
    e_b = 1.0 - e_a

    if winner == 1:
        s_a, s_b = 1.0, 0.0
    elif winner == 2:
        s_a, s_b = 0.0, 1.0
    else:
        s_a, s_b = 0.5, 0.5

    new_a = rating_a + k * (s_a - e_a)
    new_b = rating_b + k * (s_b - e_b)
    return new_a, new_b


# ---------------------------------------------------------------------------
# Glicko-2 internals — all on the Glicko-2 scale (mu, phi), not ours
# ---------------------------------------------------------------------------

def _g(phi: float) -> float:
    """Weight an opponent's influence by how well-known their rating is."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _expected(mu: float, mu_j: float, phi_j: float) -> float:
    """Expected score against one opponent, discounted by their uncertainty."""
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _new_volatility(phi: float, v: float, delta: float, sigma: float, tau: float) -> float:
    """Solve for the new volatility (Glickman step 5, Illinois algorithm).

    Finds the root of f(x) on a bracket that is guaranteed to contain it, so
    this converges in a handful of iterations and cannot run away.
    """
    a = math.log(sigma * sigma)
    delta_sq = delta * delta
    phi_sq = phi * phi

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta_sq - phi_sq - v - ex)
        den = 2.0 * (phi_sq + v + ex) ** 2
        return num / den - (x - a) / (tau * tau)

    upper = a
    if delta_sq > phi_sq + v:
        lower = math.log(delta_sq - phi_sq - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        lower = a - k * tau

    f_upper, f_lower = f(upper), f(lower)
    while abs(lower - upper) > _EPSILON:
        c = upper + (upper - lower) * f_upper / (f_lower - f_upper)
        f_c = f(c)
        if f_c * f_lower <= 0:
            upper, f_upper = lower, f_lower
        else:
            f_upper /= 2.0
        lower, f_lower = c, f_c

    return math.exp(upper / 2.0)


def rating_period(
    player: Rating,
    results: Sequence[tuple[Rating, float]],
    tau: float = TAU,
    center: float = DEFAULT_RATING,
) -> Rating:
    """Apply one Glicko-2 rating period.

    ``results`` pairs each opponent (as they stood *before* the period) with the
    score against them: 1.0 win, 0.5 draw, 0.0 loss. An empty period is legal —
    a model that sits idle grows less certain rather than staying put.
    """
    mu = (player.rating - center) / _SCALE
    phi = player.rd / _SCALE

    if not results:
        # Step 6 applied alone: no evidence, so uncertainty widens.
        phi_star = math.sqrt(phi * phi + player.volatility * player.volatility)
        return Rating(player.rating, _SCALE * phi_star, player.volatility)

    v_inv = 0.0
    delta_sum = 0.0
    for opponent, score in results:
        mu_j = (opponent.rating - center) / _SCALE
        phi_j = opponent.rd / _SCALE
        g_j = _g(phi_j)
        e_j = _expected(mu, mu_j, phi_j)
        v_inv += g_j * g_j * e_j * (1.0 - e_j)
        delta_sum += g_j * (score - e_j)

    v = 1.0 / v_inv
    delta = v * delta_sum

    sigma_prime = _new_volatility(phi, v, delta, player.volatility, tau)
    phi_star = math.sqrt(phi * phi + sigma_prime * sigma_prime)
    phi_prime = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + v_inv)
    mu_prime = mu + phi_prime * phi_prime * delta_sum

    return Rating(
        rating=_SCALE * mu_prime + center,
        rd=_SCALE * phi_prime,
        volatility=sigma_prime,
    )


def updated_glicko(
    a: Rating,
    b: Rating,
    winner: int | None,  # 1=A wins, 2=B wins, None=tie
    tau: float = TAU,
    center: float = DEFAULT_RATING,
) -> tuple[Rating, Rating]:
    """Return both players' ratings after one battle.

    Both updates read the *pre-battle* state of the other player, so the result
    does not depend on which player is processed first.
    """
    if winner == 1:
        s_a, s_b = 1.0, 0.0
    elif winner == 2:
        s_a, s_b = 0.0, 1.0
    else:
        s_a, s_b = 0.5, 0.5

    new_a = rating_period(a, [(b, s_a)], tau=tau, center=center)
    new_b = rating_period(b, [(a, s_b)], tau=tau, center=center)
    return new_a, new_b


def win_probability(a: Rating, b: Rating, center: float = DEFAULT_RATING) -> float:
    """Probability that A beats B, widening toward 0.5 as either side is less known."""
    mu_a = (a.rating - center) / _SCALE
    mu_b = (b.rating - center) / _SCALE
    phi_combined = math.sqrt((a.rd / _SCALE) ** 2 + (b.rd / _SCALE) ** 2)
    return _expected(mu_a, mu_b, phi_combined)


def conservative_rating(r: Rating) -> float:
    """Rating discounted by uncertainty — what the model has *demonstrated*."""
    return r.rating - 2.0 * r.rd

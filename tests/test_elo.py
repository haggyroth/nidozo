"""Tests for rating calculation — pure math, no I/O.

Covers both the live Glicko-2 system and the legacy plain-Elo functions
retained for reproducing pre-migration history.
"""

import pytest

from nidozo.db.elo import (
    DEFAULT_RATING,
    DEFAULT_RD,
    DEFAULT_VOLATILITY,
    K_FACTOR,
    PROVISIONAL_RD,
    Rating,
    conservative_rating,
    expected_score,
    rating_period,
    updated_glicko,
    updated_ratings,
    win_probability,
)


def test_equal_ratings_expect_half() -> None:
    assert expected_score(1000, 1000) == pytest.approx(0.5)


def test_higher_rating_expects_more() -> None:
    e = expected_score(1200, 1000)
    assert e > 0.5


def test_lower_rating_expects_less() -> None:
    e = expected_score(1000, 1200)
    assert e < 0.5


def test_expected_scores_sum_to_one() -> None:
    e_a = expected_score(1100, 950)
    e_b = expected_score(950, 1100)
    assert e_a + e_b == pytest.approx(1.0)


def test_winner_gains_rating() -> None:
    r1, r2 = updated_ratings(1000, 1000, winner=1)
    assert r1 > 1000
    assert r2 < 1000


def test_loser_loses_rating() -> None:
    r1, r2 = updated_ratings(1000, 1000, winner=2)
    assert r1 < 1000
    assert r2 > 1000


def test_tie_equal_ratings_unchanged() -> None:
    r1, r2 = updated_ratings(1000, 1000, winner=None)
    assert r1 == pytest.approx(1000.0)
    assert r2 == pytest.approx(1000.0)


def test_zero_sum_ratings() -> None:
    """Total ELO is conserved across a game."""
    for winner in (1, 2, None):
        r1, r2 = updated_ratings(1000, 1200, winner=winner)
        assert r1 + r2 == pytest.approx(2200.0)


def test_upset_win_gains_more() -> None:
    """Lower-rated player winning an upset should gain more ELO than a favourite winning."""
    low_wins_r, _ = updated_ratings(800, 1200, winner=1)
    high_wins_r, _ = updated_ratings(1200, 800, winner=1)
    assert (low_wins_r - 800) > (high_wins_r - 1200)


def test_k_factor_bounds_delta() -> None:
    """Delta is always strictly less than K (never earn or lose a full K in one game)."""
    for winner in (1, 2, None):
        r1, r2 = updated_ratings(1000, 1000, winner=winner)
        assert abs(r1 - 1000) < K_FACTOR
        assert abs(r2 - 1000) < K_FACTOR


# ---------------------------------------------------------------------------
# Glicko-2
# ---------------------------------------------------------------------------


def test_glicko2_matches_glickman_reference_example() -> None:
    """Reproduce the worked example from Glickman's Glicko-2 paper.

    Player 1500/200/0.06 with tau=0.5 plays three opponents in one rating
    period: beats 1400/30, loses to 1550/100, loses to 1700/300. The paper
    gives r'=1464.06, RD'=151.52, sigma'=0.05999. This is the load-bearing
    test for the whole implementation — if it drifts, the maths is wrong.
    """
    player = Rating(1500, 200, 0.06)
    results = [
        (Rating(1400, 30), 1.0),
        (Rating(1550, 100), 0.0),
        (Rating(1700, 300), 0.0),
    ]
    out = rating_period(player, results, tau=0.5, center=1500)

    assert out.rating == pytest.approx(1464.06, abs=0.01)
    assert out.rd == pytest.approx(151.52, abs=0.01)
    assert out.volatility == pytest.approx(0.05999, abs=0.00001)


def test_glicko2_empty_period_widens_rd_only() -> None:
    """A model that sits out gets less certain, not better or worse."""
    before = Rating(1200, 80, 0.06)
    after = rating_period(before, [])

    assert after.rating == pytest.approx(1200.0)
    assert after.rd > before.rd
    assert after.volatility == pytest.approx(before.volatility)


def test_glicko2_winner_gains_loser_loses() -> None:
    a, b = updated_glicko(Rating(), Rating(), winner=1)
    assert a.rating > DEFAULT_RATING
    assert b.rating < DEFAULT_RATING


def test_glicko2_playing_narrows_rd() -> None:
    """Evidence reduces uncertainty — RD shrinks with every game played."""
    for winner in (1, 2, None):
        a, b = updated_glicko(Rating(), Rating(), winner=winner)
        assert a.rd < DEFAULT_RD
        assert b.rd < DEFAULT_RD


def test_glicko2_rd_converges_downward_over_many_games() -> None:
    """RD keeps narrowing across a long series and settles well below the prior."""
    a, b = Rating(), Rating()
    for i in range(40):
        a, b = updated_glicko(a, b, winner=1 if i % 2 == 0 else 2)
    assert a.rd < 100.0
    assert not a.provisional


def test_glicko2_symmetric_regardless_of_argument_order() -> None:
    """Both players update from pre-battle state, so order cannot matter."""
    a1, b1 = updated_glicko(Rating(1200, 90), Rating(1000, 200), winner=1)
    b2, a2 = updated_glicko(Rating(1000, 200), Rating(1200, 90), winner=2)

    assert a1.rating == pytest.approx(a2.rating)
    assert b1.rating == pytest.approx(b2.rating)
    assert a1.rd == pytest.approx(a2.rd)


def test_glicko2_uncertain_player_moves_further() -> None:
    """A provisional model's rating swings more than a settled one's."""
    uncertain, _ = updated_glicko(Rating(1000, 350), Rating(1000, 50), winner=1)
    settled, _ = updated_glicko(Rating(1000, 50), Rating(1000, 350), winner=1)

    assert (uncertain.rating - 1000) > (settled.rating - 1000)


def test_glicko2_upset_moves_more_than_expected_result() -> None:
    upset, _ = updated_glicko(Rating(800, 100), Rating(1400, 100), winner=1)
    expected, _ = updated_glicko(Rating(1400, 100), Rating(800, 100), winner=1)

    assert (upset.rating - 800) > (expected.rating - 1400)


def test_provisional_flag_tracks_rd() -> None:
    assert Rating(1000, PROVISIONAL_RD + 1).provisional is True
    assert Rating(1000, PROVISIONAL_RD - 1).provisional is False
    assert Rating().provisional is True  # an unplayed model is always provisional


def test_interval_is_two_rd_either_side() -> None:
    low, high = Rating(1200, 75).interval
    assert low == pytest.approx(1050.0)
    assert high == pytest.approx(1350.0)


def test_conservative_rating_penalises_uncertainty() -> None:
    """A 3-0 model must not outrank a settled model of equal nominal rating."""
    newcomer = Rating(1200, 300)
    veteran = Rating(1150, 40)
    assert conservative_rating(veteran) > conservative_rating(newcomer)


def test_win_probability_even_for_equal_ratings() -> None:
    assert win_probability(Rating(), Rating()) == pytest.approx(0.5)


def test_win_probability_pulled_toward_even_by_uncertainty() -> None:
    """The same rating gap is less decisive when either side is poorly known."""
    confident = win_probability(Rating(1300, 30), Rating(1000, 30))
    unsure = win_probability(Rating(1300, 350), Rating(1000, 350))

    assert confident > unsure > 0.5


def test_defaults_are_the_documented_priors() -> None:
    r = Rating()
    assert (r.rating, r.rd, r.volatility) == (
        DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOLATILITY,
    )

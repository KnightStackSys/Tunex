import math

from tunex.stats import WDL, elo, elo_confidence_interval, score_to_elo


def test_even_score_is_zero_elo():
    assert abs(score_to_elo(0.5)) < 1e-12


def test_wdl_score_and_elo():
    wdl = WDL(wins=60, draws=20, losses=20)
    assert wdl.games == 100
    assert wdl.score == 0.7
    assert elo(wdl) > 0
    lo, hi = elo_confidence_interval(wdl)
    assert lo < elo(wdl) < hi


def test_add_results():
    wdl = WDL()
    for result in (1.0, 0.5, 0.0):
        wdl.add(result)
    assert (wdl.wins, wdl.draws, wdl.losses) == (1, 1, 1)
    assert math.isclose(wdl.score, 0.5)

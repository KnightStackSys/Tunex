from tunex.sprt import SprtState
from tunex.stats import WDL


def test_sprt_moves_toward_h1_for_wins():
    state = SprtState(elo0=0, elo1=5, alpha=0.05, beta=0.05)
    state.update(WDL(wins=1000, draws=0, losses=0))
    assert state.llr > 0


def test_sprt_moves_toward_h0_for_losses():
    state = SprtState(elo0=0, elo1=5, alpha=0.05, beta=0.05)
    state.update(WDL(wins=0, draws=0, losses=1000))
    assert state.llr < 0

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tunex.cmaes import CmaesRunner, quantize
from tunex.config import CmaesConfig, SpsaParameter


def test_quantization_bounds_and_step():
    p = SpsaParameter("Test", 6, 2, 10, 2)
    assert quantize(-3, p) == 2
    assert quantize(1.5, p) == 10
    assert quantize(0.5, p) == 6
    assert quantize(0.26, p) == 4


def test_cmaes_config_rejects_invalid_inputs():
    param = {"name": "X", "value": 5, "min": 0, "max": 10, "step": 1}
    assert CmaesConfig.from_dict({"parameters": [param]}).generations == 30
    with pytest.raises(ValueError):
        CmaesConfig.from_dict({"parameters": [param], "games_per_trial": 3})
    with pytest.raises(ValueError):
        CmaesConfig.from_dict({"parameters": [param, param]})
    with pytest.raises(ValueError):
        CmaesConfig.from_dict({"parameters": [{**param, "step": 0.5}]})


def test_cmaes_ask_tell_and_checkpoint(tmp_path, monkeypatch):
    from tunex import cmaes
    from tunex.config import (
        Config, EngineConfig, SearchConfig, MatchConfig, VariantConfig, SprtConfig
    )
    cfg = Config(
        engine=EngineConfig(path="engine"),
        search=SearchConfig(),
        match=MatchConfig(games=4, workers=1, paired=True),
        baseline=VariantConfig(name="baseline"),
        candidate=VariantConfig(name="candidate"),
        sprt=SprtConfig(),
        cmaes=CmaesConfig(
            generations=2, games_per_trial=2, population_size=4,
            state_file=str(tmp_path / "state.pkl"),
            parameters=[
                SpsaParameter("X", 5, 0, 10, 1),
                SpsaParameter("Y", 5, 0, 10, 1),
            ],
        ),
    )
    calls = []
    class FakeMatch:
        def __init__(self, config):
            pass
        def run(self, **kwargs):
            calls.append(kwargs)
            options = kwargs["candidate"].options
            score = 1 if options["X"] >= 5 else 0
            return SimpleNamespace(wins=2 * score, draws=0, losses=2 * (1 - score), games=2), []
    monkeypatch.setattr(cmaes, "MatchRunner", FakeMatch)
    runner = CmaesRunner(cfg)
    runner.run(1)
    assert len(calls) == 4
    assert len(runner.trials) == 4
    assert runner.generation == 1
    assert len({c["offset"] for c in calls}) == 1
    resumed = CmaesRunner(cfg)
    assert resumed.generation == 1
    assert resumed.best_options() == runner.best_options()
    resumed.run(2)
    assert len(calls) == 8
    assert resumed.generation == 2
    assert len({c["offset"] for c in calls[4:]}) == 1
    assert calls[4]["offset"] != calls[0]["offset"]

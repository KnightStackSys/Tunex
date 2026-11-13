from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from tunex.bayesian import BayesianRunner, _choose, _grid, _nearest
from tunex.cli import build_parser
from tunex.config import (
    BayesianConfig, Config, EngineConfig, MatchConfig, SearchConfig,
    SprtConfig, SpsaParameter, VariantConfig,
)
from tunex.stats import WDL


def build_config(tmp_path: Path) -> Config:
    param = SpsaParameter("SearchLMRFullMoves", 6, 4, 9, 1)
    other = SpsaParameter("SearchSEEPruneMargin", 90, 60, 120, 10)
    return Config(
        engine=EngineConfig(path="engine"),
        search=SearchConfig("movetime", 250),
        match=MatchConfig(games=40, paired=True, workers=1,
                          results_file=str(tmp_path / "games.jsonl")),
        baseline=VariantConfig("fixed", options={"Hash": 64}),
        candidate=VariantConfig("candidate"),
        sprt=SprtConfig(),
        bayesian=BayesianConfig(
            iterations=7, games_per_trial=4, startup_trials=3,
            candidates=16, seed=1234,
            state_file=str(tmp_path / "bayesian_state.json"),
            parameters=[param, other],
        ),
    )


class FakeRunner:
    def __init__(self, cfg: Config) -> None:
        self.calls = []

    def run(self, *, baseline, candidate, games, offset):
        assert baseline.options == {"Hash": 64}
        assert candidate.options["Hash"] == 64
        assert games == 4
        self.calls.append((dict(candidate.options), offset))
        # deterministic, nonconstant sample objective; no real engine needed.
        wins = 3 if candidate.options["SearchLMRFullMoves"] <= 6 else 1
        return WDL(wins=wins, draws=0, losses=games - wins), []


def test_bayesian_config_validation(tmp_path):
    cfg = build_config(tmp_path)
    assert cfg.bayesian is not None
    assert cfg.bayesian.games_per_trial == 4
    with pytest.raises(ValueError, match="even"):
        BayesianConfig.from_dict({
            "games_per_trial": 3,
            "parameters": [{"name": "SearchLMRFullMoves", "value": 6,
                            "min": 4, "max": 9, "step": 1}],
        })
    with pytest.raises(ValueError, match="unique"):
        BayesianConfig.from_dict({
            "parameters": [{"name": "X", "value": 6, "min": 4, "max": 9},
                           {"name": "X", "value": 3, "min": 1, "max": 6}],
        })


def test_grid_and_distinct_model_candidates(tmp_path):
    cfg = build_config(tmp_path).bayesian
    assert cfg is not None
    assert _grid(cfg.parameters[1]) == [60, 70, 80, 90, 100, 110, 120]
    assert _nearest(94, cfg.parameters[1]) == 90
    rng = random.Random(11)
    history = []
    for k in range(9):
        proposal = _choose(rng, history, cfg)
        assert tuple(proposal.items()) not in {
            tuple(x["options"].items()) for x in history
        }
        history.append({
            "options": proposal,
            "objective": 0.6 if proposal["SearchLMRFullMoves"] < 7 else 0.4,
        })
    assert len({tuple(sorted(h["options"].items())) for h in history}) == 9


def test_bayesian_tunes_and_resumes_deterministically(tmp_path, monkeypatch):
    from tunex import bayesian
    monkeypatch.setattr(bayesian, "MatchRunner", FakeRunner)
    config = build_config(tmp_path)
    first = BayesianRunner(config)
    first.run(iterations=3)
    assert len(first.trials) == 3
    assert first.trials[0]["options"] == {
        "SearchLMRFullMoves": 6, "SearchSEEPruneMargin": 90,
    }
    stored = json.loads(Path(config.bayesian.state_file).read_text())
    assert stored["version"] == 1
    assert len(stored["trials"]) == 3

    resumed = BayesianRunner(config)
    resumed.run(iterations=7)
    assert len(resumed.trials) == 7
    assert len({tuple(sorted(t["options"].items())) for t in resumed.trials}) == 7
    assert resumed.match_runner.calls[0][1] == 12  # correct game offset
    assert set(resumed.best_options()) == {
        "SearchLMRFullMoves", "SearchSEEPruneMargin",
    }

    uninterrupted_config = build_config(tmp_path / "other")
    full = BayesianRunner(uninterrupted_config)
    full.run(iterations=7)
    assert [t["options"] for t in resumed.trials] == [
        t["options"] for t in full.trials
    ]


def test_resume_rejects_mismatched_experiment(tmp_path, monkeypatch):
    from tunex import bayesian
    monkeypatch.setattr(bayesian, "MatchRunner", FakeRunner)
    config = build_config(tmp_path)
    BayesianRunner(config).run(iterations=1)
    config.search.value = 400
    with pytest.raises(ValueError, match="different configuration"):
        BayesianRunner(config)


def test_space_exhaustion(tmp_path):
    cfg = BayesianConfig(
        iterations=10, games_per_trial=4, startup_trials=2,
        parameters=[SpsaParameter("X", 0, 0, 1, 1)],
    )
    previous = [
        {"options": {"X": 0}, "objective": 0.5},
        {"options": {"X": 1}, "objective": 0.5},
    ]
    with pytest.raises(ValueError, match="exhausted"):
        _choose(random.Random(1), previous, cfg)


def test_cli_bayesian_command():
    args = build_parser().parse_args(["-c", "test.json", "bayesian", "--iterations", "25"])
    assert args.command == "bayesian"
    assert args.iterations == 25
    assert build_parser().parse_args(["sprt"]).command == "sprt"
    assert build_parser().parse_args(["spsa"]).command == "spsa"

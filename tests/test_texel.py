from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tunex.config import (
    Config, EngineConfig, SearchConfig, MatchConfig, SprtConfig,
    VariantConfig, TexelConfig, SpsaParameter,
)
from tunex.texel import (
    TexelRunner, read_dataset, split_games, prediction, sign, grid_value,
)
from tunex.cli import build_parser


FEN_R = "R-0,0,0,0-1,1,1,1-1,1,1,1-0,0,0,0-0-{}-x,x,x,yK,1,yR,x,x,x/x,x,x,8,x,x,x"
FEN_B = FEN_R.replace("R-", "B-", 1)


def make_data(path: Path) -> None:
    records = []
    for game in range(10):
        for fen in (FEN_R, FEN_B):
            records.append({"fen": fen, "result": float(game % 2),
                            "game_id": f"game-{game}"})
    path.write_text("\n".join(map(json.dumps, records)) + "\n", encoding="utf-8")


def cfg(tmp_path: Path) -> Config:
    data = tmp_path / "data.jsonl"
    make_data(data)
    return Config(
        engine=EngineConfig("test-engine", common_options={"UseNNUE": False}),
        search=SearchConfig(),
        match=MatchConfig(),
        baseline=VariantConfig("original"),
        candidate=VariantConfig("candidate"),
        sprt=SprtConfig(),
        texel=TexelConfig(
            dataset=str(data), parameters=[
                SpsaParameter("EvalKnightMobility", 9, 0, 20, 1)
            ],
            epochs=2, validation_fraction=0.2,
            state_file=str(tmp_path / "texel.json"),
        ),
    )


class FakeUci:
    def __init__(self, *args, **kwargs):
        self.options = {"EvalKnightMobility"}
        self.current = 9
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def configure(self, options):
        self.current = options.get("EvalKnightMobility", self.current)

    def new_game(self):
        pass

    def static_eval_cp(self, fen):
        # Synthetic black-box HCE, not search scores.
        return (self.current * 20) * (1 if fen.startswith("R-") else -1)


def test_labels_and_perspective(tmp_path):
    file = tmp_path / "data.jsonl"
    make_data(file)
    samples = read_dataset(str(file), "four_player_teams")
    train, valid = split_games(samples, 123, 0.2)
    assert set(s.game for s in train).isdisjoint(s.game for s in valid)
    assert len(train) + len(valid) == 20
    assert sign(FEN_R, "four_player_teams") == 1
    assert sign(FEN_B, "four_player_teams") == -1
    assert sign("4k3/8/8/8/8/8/8/4K3 b - - 0 1", "spell_chess") == -1
    assert prediction(0, 1.0) == 0.5
    assert prediction(10000, 1.0) > 0.9999
    assert grid_value(12.9, SpsaParameter("x", 10, 0, 20, 3)) == 12


def test_invalid_data(tmp_path):
    file = tmp_path / "data.jsonl"
    file.write_text(json.dumps({"fen": FEN_R, "result": 2, "game_id": "x"}) + "\n")
    with pytest.raises(ValueError, match="result must"):
        read_dataset(str(file), "four_player_teams")
    file.write_text(json.dumps({"fen": FEN_R, "result": 1}) + "\n")
    with pytest.raises(ValueError, match="fen/result/game_id"):
        read_dataset(str(file), "four_player_teams")


def test_fitting_and_resume(tmp_path):
    config = cfg(tmp_path)
    with patch("tunex.texel.UciEngine", FakeUci):
        runner = TexelRunner(config)
        runner.run(1)
        assert runner.epochs_done == 1
        assert runner.best_validation < float("inf")
        resumed = TexelRunner(config)
        resumed.run(2)
        assert resumed.epochs_done == 2
        assert Path(config.texel.state_file).exists()

        # Only best validation weights are presented for a held-out test.
        assert "EvalKnightMobility" in resumed.best_weights
        config.texel.k = 2
        with pytest.raises(ValueError, match="does not match"):
            TexelRunner(config)


def test_config_validation_and_cli(tmp_path):
    data = cfg(tmp_path)
    assert data.texel is not None
    assert build_parser().parse_args(["texel", "--epochs", "4"]).epochs == 4
    with pytest.raises(ValueError, match="required"):
        TexelConfig.from_dict({"dataset": ""})
    with pytest.raises(ValueError, match="integer"):
        TexelConfig.from_dict({"dataset": "a", "parameters": [
            {"name": "EvalPawnValue", "value": 86.5, "min": 80, "max": 100}
        ]})
    data.variant = "spell_chess"
    with pytest.raises(ValueError, match="no Eval"):
        TexelRunner(data)

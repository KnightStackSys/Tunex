from __future__ import annotations

import json
import signal

import pytest

from tunex import cli
from tunex.config import (
    Config, EngineConfig, MatchConfig, SearchConfig, SprtConfig, VariantConfig
)
from tunex.stats import WDL


def config(tmp_path):
    return Config(
        engine=EngineConfig(path="engine"),
        search=SearchConfig(),
        match=MatchConfig(games=10, paired=True, workers=1),
        baseline=VariantConfig(name="baseline"),
        candidate=VariantConfig(name="candidate"),
        sprt=SprtConfig(
            max_games=6, batch_games=2,
            state_file=str(tmp_path / "sprt_state.json"),
        ),
    )


def test_checkpoint_roundtrip_and_incompatible_config(tmp_path):
    cfg = config(tmp_path)
    cli._save_sprt_checkpoint(cfg, WDL(wins=1, draws=1), 2)
    checkpoint_text = (tmp_path / "sprt_state.json").read_text(encoding="utf-8")
    assert checkpoint_text.endswith("\n")
    assert json.loads(checkpoint_text)["played"] == 2
    total, played = cli._load_sprt_checkpoint(cfg)
    assert played == 2 and (total.wins, total.draws, total.losses) == (1, 1, 0)
    cfg.search.value = 200
    with pytest.raises(ValueError, match="incompatible"):
        cli._load_sprt_checkpoint(cfg)


def test_sprt_pause_at_batch_boundary_then_resume(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    calls = []
    class FakeMatchRunner:
        def __init__(self, config):
            pass
        def run(self, *, games, offset, on_progress, on_game_complete):
            calls.append(offset)
            if len(calls) == 1:
                signal.raise_signal(signal.SIGINT)
            batch = WDL()
            for i in range(games):
                batch.add(0.5)
            return batch, []
    monkeypatch.setattr(cli, "MatchRunner", FakeMatchRunner)
    assert cli.command_sprt(cfg) == 0
    assert calls == [0]
    assert cli._load_sprt_checkpoint(cfg)[1] == 2
    assert cli.command_sprt(cfg) == 3
    assert calls == [0, 2, 4]
    assert cli._load_sprt_checkpoint(cfg)[1] == 6


def test_sprt_fresh_discards_old_checkpoint(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cli._save_sprt_checkpoint(cfg, WDL(draws=2), 2)
    offsets = []
    class FakeMatchRunner:
        def __init__(self, config):
            pass
        def run(self, *, games, offset, on_progress, on_game_complete):
            offsets.append(offset)
            return WDL(draws=games), []
    monkeypatch.setattr(cli, "MatchRunner", FakeMatchRunner)
    assert cli.command_sprt(cfg, fresh=True) == 3
    assert offsets == [0, 2, 4]

"""Regression tests for binary-vs-binary SPRT and legacy single-engine configs."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tunex.cli import command_probe
from tunex.config import Config, SearchConfig, VariantConfig
from tunex.engine import SearchResult
from tunex.match import MatchRunner


def _config_payload():
    return {
        "engine": {
            "path": "../bin/default.exe",
            "common_options": {"Hash": 64, "UseNNUE": False},
        },
        "baseline": {
            "name": "before",
            "path": "../bin/old.exe",
            "options": {},
        },
        "candidate": {
            "name": "after",
            "path": "../bin/new.exe",
            "options": {},
        },
        "search": {"mode": "movetime", "value": 100},
        "match": {
            "paired": True,
            "workers": 1,
            "games": 2,
            "max_plies": 2,
            "openings_file": None,
        },
        "sprt": {"elo0": 0, "elo1": 5, "batch_games": 2},
    }


def test_variant_paths_resolved_relative_to_config_directory():
    with TemporaryDirectory() as temp:
        base = Path(temp)
        config_file = base / "configs" / "compare.json"
        config_file.parent.mkdir()
        config_file.write_text(json.dumps(_config_payload()), encoding="utf-8")
        cfg = Config.load(config_file)
        assert cfg.engine.path == str((base / "bin" / "default.exe").resolve())
        assert cfg.baseline.path == str((base / "bin" / "old.exe").resolve())
        assert cfg.candidate.path == str((base / "bin" / "new.exe").resolve())
        assert cfg.sprt.batch_games == 2


def test_variant_path_optional_and_validation():
    assert VariantConfig.from_dict({}, "old").path is None
    assert VariantConfig.from_dict({"path": "C:/Engines/old.exe"}, "old").path == "C:/Engines/old.exe"
    for value in ("", "  ", 123):
        with pytest.raises(ValueError, match="path must be a nonempty string"):
            VariantConfig.from_dict({"path": value}, "old")


def test_paired_match_launches_each_executable_by_role_and_resets_each_game():
    class FakeEngine:
        instances = []

        def __init__(self, path, *, name, **kwargs):
            self.path = path
            self.name = name
            self.events = []
            self.options = {}
            FakeEngine.instances.append(self)

        def start(self):
            self.events.append("start")

        def configure(self, options):
            self.events.append("configure")
            self.options = options

        def new_game(self):
            self.events.append("ucinewgame")

        def search(self, position_command, go_command):
            self.events.append("search")
            return SearchResult(bestmove="h2h3", score_cp=0)

        def close(self):
            self.events.append("close")

    for use_override in (True, False):
        for workers in (1, 2):
            FakeEngine.instances.clear()
            config = SimpleNamespace(
                engine=SimpleNamespace(
                    path="shared.exe", common_options={"Hash": 64},
                    startup_timeout=1, move_timeout=1, strict_options=True,
                ),
                search=SearchConfig(mode="depth", value=1),
                match=SimpleNamespace(
                    paired=True, workers=workers, games=2, seed=7,
                    max_plies=2, openings_file=None, results_file="unused.jsonl",
                ),
                baseline=VariantConfig(
                    "old", {"SearchContinuationCorrection": 0},
                    path="old.exe" if use_override else None,
                ),
                candidate=VariantConfig(
                    "new", {"SearchContinuationCorrection": 1},
                    path="new.exe" if use_override else None,
                ),
            )
            with patch("tunex.match.UciEngine", FakeEngine):
                _, records = MatchRunner(config).run(games=2, write_results=False)
            assert len(records) == 2
            assert sorted(r.candidate_team for r in records) == [0, 1]
            assert len(FakeEngine.instances) == 4
            for engine in FakeEngine.instances:
                expected = (
                    ("new.exe" if engine.name == "new" else "old.exe")
                    if use_override else "shared.exe"
                )
                assert engine.path == expected
                assert engine.events[:3] == ["start", "configure", "ucinewgame"]
                assert engine.events.count("ucinewgame") == 1
                assert engine.events.count("search") == 1
                assert engine.events[-1] == "close"
                assert engine.options["Hash"] == 64
                expected_toggle = 1 if engine.name == "new" else 0
                assert engine.options["SearchContinuationCorrection"] == expected_toggle


def test_probe_uses_candidate_executable_override(capsys):
    class ProbeEngine:
        created = []

        def __init__(self, path, **kwargs):
            self.path = path
            self.options = set()
            ProbeEngine.created.append(self)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def configure(self, options):
            self.options = options

        def diagnostic(self):
            return []

    cfg = SimpleNamespace(
        engine=SimpleNamespace(
            path="shared.exe", startup_timeout=1, move_timeout=1,
            strict_options=True, common_options={"Hash": 64},
        ),
        candidate=VariantConfig("new", options={}, path="new.exe"),
    )
    with patch("tunex.cli.UciEngine", ProbeEngine):
        assert command_probe(cfg) == 0
    assert ProbeEngine.created[-1].path == "new.exe"
    assert ProbeEngine.created[-1].options == {"Hash": 64}
    assert "new.exe" in capsys.readouterr().out

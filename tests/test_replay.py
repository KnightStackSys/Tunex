from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tunex.config import Config
from tunex.engine import EngineError, SearchResult
from tunex.replay import load_game, parse_game, replay_game


GAME = """
1. h2-h3 .. b9-c9 .. g13-g12 .. m8-l8
2. Qg1-j4 .. b7-c7 .. Qh14-g13 .. m6-l6
3. k2-k4 .. Na10-c11 .. Ne14-f12 .. Qn7-l9
4. Nj1-i3 .. Qa8-b9 .. Nj14-i12 .. Ql9-g9
5. Bi1-h2 .. Na5-c6 .. Kg14-h14 .. Nn10-m8
6. Qj4-j7 .. Qb9-f5 .. Ni12-k11 .. Kn8-n7
7. d2-d4 .. Qf5xf2 .. Nk11-j9 .. Qg9xg2+
"""


def test_pgn4_rounds_and_comments():
    assert len(parse_game(GAME)) == 28
    assert parse_game('[Event "test"]\n1. e2-e3 .. b7-c7 {note} .. g13-g12 .. m7-l7') == (
        "e2-e3", "b7-c7", "g13-g12", "m7-l7"
    )
    assert parse_game("1. e2-e3 .. b7-c7 .. g13-g12 .. m7-l7 1-0")[-1] == "m7-l7"
    with pytest.raises(ValueError, match="variations"):
        parse_game("1. e2-e3 (e2-e4) .. b7-c7")
    with pytest.raises(ValueError, match="no moves"):
        parse_game("# nothing")


class FakeEngine:
    instances = []

    def __init__(self, *args, **kwargs):
        self.commands = []
        self.moves_seen = []
        self.resets = 0
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def configure(self, options):
        self.options = options

    def new_game(self):
        self.resets += 1

    def opening_position(self, position):
        self.commands.append(position)
        move_count = 0 if " moves " not in position else len(position.split(" moves ", 1)[1].split())
        seat = ("R", "B", "Y", "G")[move_count % 4]
        legal = ("h2h3", "b9c9", "g13g12", "m8l8")
        return seat + "-dummy", (legal[move_count % 4],)

    def search(self, position, go_command):
        self.moves_seen.append((position, go_command))
        # Deliberately disagree with every Red+Yellow move except the first.
        index = len(position.split(" moves ", 1)[1].split()) if " moves " in position else 0
        return SearchResult(bestmove="h2h3" if index == 0 else "0000", score_mate=13)


def test_replay_forces_pgn_and_only_searches_selected_team(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "engine": {"path": "not-real"},
        "search": {"mode": "depth", "value": 22},
    }))
    config = Config.load(config_path)
    game_file = tmp_path / "game.txt"
    # The fake engine exposes legal coordinate moves but not a complete FEN4\n    # board, so use UCI here; PGN4 parsing is covered by the test above.\n    game_file.write_text("1. h2h3 .. b9c9 .. g13g12 .. m8l8")
    output = tmp_path / "report.json"
    FakeEngine.instances.clear()
    with patch("tunex.replay.UciEngine", FakeEngine):
        result = replay_game(config, game_file, "RY", report_file=output)
    engine = FakeEngine.instances[-1]
    assert engine.resets == 1
    assert len(engine.moves_seen) == 2
    assert engine.moves_seen[0] == ("position startpos", "go depth 22")
    assert engine.moves_seen[1][0] == "position startpos moves h2h3 b9c9"
    assert result.matched == 1 and result.mismatched == 1
    assert result.total_plies == 4
    assert json.loads(output.read_text())["comparisons"][1]["recorded_uci"] == "g13g12"

    with patch("tunex.replay.UciEngine", FakeEngine):
        bg = replay_game(config, game_file, "BG")
    assert bg.compared == 2
    assert FakeEngine.instances[-1].moves_seen[0][0] == "position startpos moves h2h3"
    assert FakeEngine.instances[-1].moves_seen[1][0] == (
        "position startpos moves h2h3 b9c9 g13g12"
    )


def test_invalid_team_and_missing_file(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"engine": {"path": "not-real"}}')
    config = Config.load(config_path)
    game = tmp_path / "game.txt"
    game.write_text("1. h2-h3")
    with pytest.raises(ValueError, match="RY or BG"):
        replay_game(config, game, "RB")
    with pytest.raises(FileNotFoundError):
        load_game(tmp_path / "missing.txt")

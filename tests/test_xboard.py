"""Exercise real XBoard subprocess IO and standard-chess match integration."""
from __future__ import annotations

import json
import sys

import chess
import pytest

from tunex.config import Config, EngineConfig, MatchConfig, SearchConfig, SprtConfig, VariantConfig
from tunex.engine import EngineError
from tunex.match import MatchRunner
from tunex.xboard import XBoardEngine


FAKE_XBOARD = r'''
import sys
import chess

log_path = sys.argv[1]
board = chess.Board()
def log(line):
    with open(log_path, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")
for raw in sys.stdin:
    line = raw.strip()
    log(line)
    if line == "protover 2":
        print('feature ping=1 setboard=1 usermove=1 memory=1 option="Skill -spin 5 0 20" done=1', flush=True)
    elif line == "new":
        board.reset()
    elif line.startswith("setboard "):
        board = chess.Board(line[9:])
    elif line.startswith("usermove "):
        board.push_uci(line[9:])
    elif line.startswith("ping "):
        print("pong " + line[5:], flush=True)
    elif line == "go":
        if board.is_game_over():
            print("resign", flush=True)
        else:
            move = next(iter(board.legal_moves))
            print("3 24 10 1000 " + move.uci(), flush=True)
            print("move " + move.uci(), flush=True)
            board.push(move)
    elif line == "quit":
        break
'''


def fake_engine(tmp_path):
    script = tmp_path / "fake_xboard.py"
    script.write_text(FAKE_XBOARD, encoding="utf-8")
    log = tmp_path / "engine-commands.txt"
    return script, log


def test_xboard_handshake_options_move_history_and_ping(tmp_path):
    script, log = fake_engine(tmp_path)
    with XBoardEngine(
        sys.executable, arguments=[str(script), str(log)],
        name="mock", move_timeout=5,
    ) as engine:
        assert "Skill" in engine.options
        engine.configure({"Skill": 7, "Hash": 32})
        engine.new_game()
        result = engine.search("position startpos", "go depth 3")
        assert result.bestmove
        assert result.score_cp == 24
        board = chess.Board()
        board.push_uci(result.bestmove)
        response = next(iter(board.legal_moves))
        board.push(response)
        engine.search("position startpos moves " + result.bestmove + " " + response.uci(),
                      "go movetime 250")
    commands = log.read_text(encoding="utf-8").splitlines()
    assert commands.count("protover 2") == 1
    assert "option Skill=7" in commands
    assert "memory 32" in commands
    assert "sd 3" in commands
    assert "st 0.25" in commands
    assert commands.count("usermove " + result.bestmove) == 0  # Already applied by engine
    assert commands.count("usermove " + response.uci()) == 1
    assert commands[-2:] == ["force", "quit"]


def test_xboard_standard_chess_match_san_and_paired_sides(tmp_path):
    script, log = fake_engine(tmp_path)
    openings = tmp_path / "openings.txt"
    openings.write_text("e4 e5 Nf3\n", encoding="utf-8")
    config = Config(
        engine=EngineConfig(
            path=sys.executable, protocol="xboard",
            arguments=[str(script), str(log)], common_options={"Skill": 3},
            move_timeout=5, startup_timeout=3,
        ),
        search=SearchConfig(mode="depth", value=2),
        match=MatchConfig(
            games=2, workers=1, max_plies=4, paired=True,
            openings_file=str(openings), results_file=str(tmp_path / "games.jsonl"),
        ),
        baseline=VariantConfig("baseline"),
        candidate=VariantConfig("candidate"),
        sprt=SprtConfig(),
        variant="standard_chess",
    )
    wdl, records = MatchRunner(config).run()
    assert wdl.games == 2
    assert [x.candidate_team for x in records] == [0, 1]
    assert all(x.opening_moves == ["e2e4", "e7e5", "g1f3"] for x in records)
    assert all(x.plies == 7 for x in records)
    commands = log.read_text(encoding="utf-8")
    assert "usermove e2e4" in commands
    assert "option Skill=3" in commands


def test_xboard_fen_opening_and_nodes_error(tmp_path):
    script, log = fake_engine(tmp_path)
    with XBoardEngine(sys.executable, arguments=[str(script), str(log)],
                      name="mock", move_timeout=5) as engine:
        engine.new_game()
        with pytest.raises(EngineError, match="fixed node limit"):
            engine.search("position startpos", "go nodes 100")
        engine.search("position fen " + chess.STARTING_FEN, "go movetime 1000")
    assert "setboard " + chess.STARTING_FEN in log.read_text(encoding="utf-8")


def test_xboard_rejects_variant_without_engine_support(tmp_path):
    script, log = fake_engine(tmp_path)
    with XBoardEngine(sys.executable, arguments=[str(script), str(log)],
                      name="mock", variant_name="four_player_teams") as engine:
        with pytest.raises(EngineError, match="does not advertise XBoard variant"):
            engine.new_game()


def test_protocol_config_and_validation(tmp_path):
    obj = {
        "variant": "standard_chess",
        "engine": {
            "path": sys.executable, "protocol": "xboard",
            "arguments": ["engine.py"], "common_options": {},
        },
        "candidate": {"protocol": "uci", "options": {}},
    }
    config_path = tmp_path / "test.json"
    config_path.write_text(json.dumps(obj), encoding="utf-8")
    cfg = Config.load(config_path)
    assert cfg.engine.protocol == "xboard"
    assert cfg.candidate.protocol == "uci"
    assert cfg.engine.arguments == ["engine.py"]
    obj["engine"]["protocol"] = "unknown"
    config_path.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="engine.protocol"):
        Config.load(config_path)


FAKE_UCI = r'''
import sys
import chess

board = chess.Board()
log_path = sys.argv[1]
for raw in sys.stdin:
    line = raw.strip()
    with open(log_path, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")
    if line == "uci":
        print("id name Fake UCI", flush=True)
        print("uciok", flush=True)
    elif line == "isready":
        print("readyok", flush=True)
    elif line == "ucinewgame":
        board.reset()
    elif line.startswith("position startpos"):
        board.reset()
        if " moves " in line:
            for move in line.split(" moves ", 1)[1].split():
                board.push_uci(move)
    elif line.startswith("go "):
        move = next(iter(board.legal_moves))
        print("info depth 1 score cp 18 pv " + move.uci(), flush=True)
        print("bestmove " + move.uci(), flush=True)
    elif line == "quit":
        break
'''


def test_mixed_uci_and_xboard_match(tmp_path):
    xb_script, xb_log = fake_engine(tmp_path)
    uci_script = tmp_path / "fake_uci.py"
    uci_script.write_text(FAKE_UCI, encoding="utf-8")
    uci_log = tmp_path / "uci-commands.txt"
    config = Config(
        engine=EngineConfig(
            path=sys.executable, protocol="xboard",
            arguments=[str(xb_script), str(xb_log)],
            startup_timeout=3, move_timeout=5,
        ),
        search=SearchConfig(mode="depth", value=1),
        match=MatchConfig(
            games=2, workers=1, max_plies=2, paired=True,
            results_file=str(tmp_path / "mixed.jsonl"),
        ),
        baseline=VariantConfig("xb"),
        candidate=VariantConfig("uci", protocol="uci",
                                arguments=[str(uci_script), str(uci_log)]),
        sprt=SprtConfig(),
        variant="standard_chess",
    )
    stats, records = MatchRunner(config).run()
    assert stats.games == 2
    assert len(records) == 2
    assert "protover 2" in xb_log.read_text(encoding="utf-8")
    assert "uci" in uci_log.read_text(encoding="utf-8")
    assert all(record.reason == "max-plies" for record in records)

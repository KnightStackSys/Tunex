"""4-player Teams through CECP, including native variants and mixed UCI/CECP."""
from __future__ import annotations

import sys

from tunex.config import Config, EngineConfig, MatchConfig, SearchConfig, SprtConfig, VariantConfig
from tunex.match import MatchRunner

# The referee owns the 4PC turn order (R, B, Y, G). These test engines
# deliberately have NO "variants" feature: 4PC is their native game.
FOUR_PLAYER_MOVES = ["d2d3", "b8b9", "d13d12", "m7l7", "e2e3"]

CECP_4PC_ENGINE = r'''
import sys
history = []
log_path = sys.argv[1]
resign_at = int(sys.argv[2])
moves = ["d2d3", "b8b9", "d13d12", "m7l7", "e2e3"]
for raw in sys.stdin:
    cmd = raw.strip()
    with open(log_path, "a", encoding="utf-8") as fp:
        fp.write(cmd + "\n")
    if cmd == "protover 2":
        print("feature ping=1 usermove=1 setboard=1 done=1", flush=True)
    elif cmd == "new":
        history = []
    elif cmd.startswith("usermove "):
        history.append(cmd[len("usermove "):])
    elif cmd.startswith("ping "):
        print("pong " + cmd[5:], flush=True)
    elif cmd == "go":
        if len(history) == resign_at:
            print("resign", flush=True)
        elif len(history) >= len(moves):
            print("1/2-1/2 {completed}", flush=True)
        else:
            mv = moves[len(history)]
            print("2 13 1 100 " + mv, flush=True)
            print("move " + mv, flush=True)
            history.append(mv)
    elif cmd == "quit":
        break
'''

UCI_4PC_ENGINE = r'''
import sys
moves = ["d2d3", "b8b9", "d13d12", "m7l7", "e2e3"]
history = []
log_path = sys.argv[1]
for raw in sys.stdin:
    cmd = raw.strip()
    with open(log_path, "a", encoding="utf-8") as fp:
        fp.write(cmd + "\n")
    if cmd == "uci":
        print("id name Mock 4PC UCI", flush=True)
        print("uciok", flush=True)
    elif cmd == "isready":
        print("readyok", flush=True)
    elif cmd == "ucinewgame":
        history = []
    elif cmd.startswith("position startpos"):
        history = cmd.split(" moves ", 1)[1].split() if " moves " in cmd else []
    elif cmd.startswith("go "):
        print("bestmove " + moves[len(history)], flush=True)
    elif cmd == "quit":
        break
'''


def make_config(tmp_path, *, candidate_protocol="xboard", baseline_protocol="xboard",
                resign_at=-1, max_plies=4):
    cecp = tmp_path / "xboard_4pc.py"
    cecp.write_text(CECP_4PC_ENGINE, encoding="utf-8")
    uci = tmp_path / "uci_4pc.py"
    uci.write_text(UCI_4PC_ENGINE, encoding="utf-8")
    variants = []
    for label, protocol in (("base", baseline_protocol), ("test", candidate_protocol)):
        log = tmp_path / f"{label}.log"
        script = cecp if protocol == "xboard" else uci
        arguments = [str(script), str(log)]
        if protocol == "xboard":
            arguments.append(str(resign_at))
        variants.append(VariantConfig(label, path=sys.executable, protocol=protocol,
                                      arguments=arguments))
    return Config(
        engine=EngineConfig(path=sys.executable, protocol="xboard",
                            startup_timeout=4, move_timeout=5, common_options={}),
        search=SearchConfig(mode="depth", value=2),
        match=MatchConfig(
            games=2, paired=True, workers=1, max_plies=max_plies,
            results_file=str(tmp_path / "games.jsonl"),
        ),
        baseline=variants[0], candidate=variants[1],
        sprt=SprtConfig(state_file=str(tmp_path / "sprt.json")),
        variant="four_player_teams",
    )


def test_native_4pc_xboard_pair_without_variant_feature(tmp_path):
    config = make_config(tmp_path)
    total, games = MatchRunner(config).run()
    assert total.games == 2
    assert total.draws == 2
    assert [g.candidate_team for g in games] == [0, 1]
    for game in games:
        assert game.moves == FOUR_PLAYER_MOVES[:4]
        assert game.reason == "max-plies"

    for name in ("base", "test"):
        commands = (tmp_path / f"{name}.log").read_text(encoding="utf-8").splitlines()
        assert "xboard" in commands
        assert "protover 2" in commands
        assert "go" in commands
        assert not any(x.startswith("variant ") for x in commands)
        assert any(x.startswith("usermove ") for x in commands)


def test_4pc_mixed_uci_xboard_engine_match(tmp_path):
    config = make_config(tmp_path, baseline_protocol="uci")
    total, games = MatchRunner(config).run()
    assert total.games == 2
    assert all(g.moves == FOUR_PLAYER_MOVES[:4] for g in games)
    assert "uci" in (tmp_path / "base.log").read_text(encoding="utf-8")
    assert "protover 2" in (tmp_path / "test.log").read_text(encoding="utf-8")


def test_xboard_resignation_is_recorded_as_team_loss(tmp_path):
    config = make_config(tmp_path, resign_at=0)
    total, games = MatchRunner(config).run()
    assert total.games == 2
    assert len(games) == 2
    assert all(g.reason == "resignation" for g in games)
    assert all(g.winner_team == 1 for g in games)
    assert sorted(g.result for g in games) == [0.0, 1.0]
    assert all(g.plies == 0 for g in games)

"""SAN -> UCI opening conversion for the 14x14 four-player Teams board."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

from tunex.config import SearchConfig, VariantConfig
from tunex.engine import EngineError, SearchResult, UciEngine
from tunex.match import MatchRunner, _convert_opening, _load_openings, _start_side
from tunex.notation import coordinate_move, fen4_board, san_to_uci


FILES = "abcdefghijklmn"


def make_fen(turn="R", pieces=None, *, metadata=False):
    """Compact Chess.com-style 14x14 FEN4 test fixture."""
    pieces = pieces or {}
    ranks = []
    for rank in range(14, 0, -1):
        row = []
        for fi, file in enumerate(FILES):
            square = f"{file}{rank}"
            invalid = (fi < 3 or fi >= 11) and (rank <= 3 or rank >= 12)
            row.append("x" if invalid else pieces.get(square, "1"))
        ranks.append(",".join(row))
    prefix = f"{turn}-0,0,0,0-0,0,0,0-0,0,0,0-0,0,0,0-0-"
    if metadata:
        prefix += "{'hint':'a-b'}-"
    return prefix + "/".join(ranks)


def test_fen4_board_supports_all_four_colors_and_metadata():
    fen = make_fen("G", {
        "e1": "rN", "a7": "bK", "h14": "yQ", "n7": "gP"
    }, metadata=True)
    turn, board = fen4_board(fen)
    assert turn == "G"
    assert board == {"e1": "rN", "a7": "bK", "h14": "yQ", "n7": "gP"}


def test_quiet_san_and_annotations():
    fen = make_fen(pieces={"e1": "rN", "g2": "rP"})
    assert san_to_uci("Nf3+?!", fen, ["e1f3", "g2g3"]) == "e1f3"
    assert san_to_uci("g3", fen, ["g2g3", "e1f3"]) == "g2g3"
    assert coordinate_move("E1F3") == "e1f3"
    assert coordinate_move("g10g11q") == "g10g11q"
    assert coordinate_move("Nf3") is None


def test_pawn_capture_and_4pc_orientation():
    red = make_fen(pieces={"h4": "rP", "i5": "bP"})
    assert san_to_uci("hxi5", red, ["h4i5"]) == "h4i5"
    assert san_to_uci("h4xi5", red, ["h4i5"]) == "h4i5"
    blue = make_fen("B", {"b7": "bP", "c8": "rP"})
    assert san_to_uci("7xc8", blue, ["b7c8"]) == "b7c8"
    green = make_fen("G", {"m7": "gP", "l6": "rP"})
    assert san_to_uci("7xl6", green, ["m7l6"]) == "m7l6"


def test_en_passant_uses_engine_legality_and_diagonal_pawn_geometry():
    red = make_fen(pieces={"h4": "rP"})
    assert san_to_uci("hxi5", red, ["h4i5"]) == "h4i5"
    with pytest.raises(ValueError, match="illegal SAN"):
        san_to_uci("hi5", red, ["h4i5"])


def test_promotions_and_long_four_player_algebraic():
    red = make_fen(pieces={"g10": "rP", "e1": "rN"})
    assert san_to_uci("g11=Q", red, ["g10g11q", "g10g11n"]) == "g10g11q"
    assert san_to_uci("g11=N", red, ["g10g11q", "g10g11n"]) == "g10g11n"
    assert san_to_uci("Ne1-f3+", red, ["e1f3"]) == "e1f3"
    assert san_to_uci("e1-f3", red, ["e1f3"]) == "e1f3"
    with pytest.raises(ValueError, match="illegal SAN"):
        san_to_uci("g11", red, ["g10g11q", "g10g11n"])


def test_disambiguation_and_ambiguous_moves_rejected():
    red = make_fen(pieces={"e1": "rN", "g1": "rN"})
    with pytest.raises(ValueError, match="ambiguous SAN"):
        san_to_uci("Nf3", red, ["e1f3", "g1f3"])
    assert san_to_uci("Nef3", red, ["e1f3", "g1f3"]) == "e1f3"
    assert san_to_uci("N1f3", red, ["e1f3"]) == "e1f3"
    assert san_to_uci("Ne1f3", red, ["e1f3", "g1f3"]) == "e1f3"


def test_castling_uses_all_four_directions():
    positions = [
        ("R", "g1", "i1", "e1"),
        ("B", "a7", "a5", "a9"),
        ("Y", "g14", "e14", "i14"),
        ("G", "n7", "n9", "n5"),
    ]
    for color, king, short_to, long_to in positions:
        fen = make_fen(color, {king: color.lower() + "K"})
        legal = [king + short_to, king + long_to]
        assert san_to_uci("O-O", fen, legal) == king + short_to
        assert san_to_uci("0-0-0", fen, legal) == king + long_to


def test_invalid_moves_are_rejected():
    red = make_fen(pieces={"e1": "rN"})
    with pytest.raises(ValueError, match="illegal SAN"):
        san_to_uci("Nf4", red, ["e1f3"])
    with pytest.raises(ValueError, match="illegal SAN"):
        san_to_uci("Nxf3", red, ["e1f3"])
    with pytest.raises(ValueError, match="unsupported SAN"):
        san_to_uci("not_a_move", red, ["e1f3"])


def test_convert_mixed_san_and_uci_keeps_coordinate_inputs_unchanged():
    red = make_fen(pieces={"e1": "rN"})
    blue = make_fen("B", {"b7": "bP"})
    engine = SimpleNamespace(opening_position=lambda command: (
        (red, ["e1f3"]) if command == "position startpos"
        else (blue, ["b7c7"])
    ))
    opening = _convert_opening(engine, None, ("Nf3", "c7", "g13g12"))
    assert opening == ["e1f3", "b7c7", "g13g12"]
    assert _convert_opening(engine, None, ("E1F3", "b7c7")) == ["e1f3", "b7c7"]


def test_opening_file_accepts_san_prefix_mixed_and_custom_fen():
    with TemporaryDirectory() as temp:
        path = Path(temp) / "openings.txt"
        path.write_text(
            "# comment\n"
            "san Nf3 c7 g12 l7\n"
            "moves e2e3 c7\n"
            "Nf3 b7c7\n"
            "fen 4PC | san Nf3 c7\n"
            "fen 4PC | moves e2e3 c7\n",
            encoding="utf-8",
        )
        assert _load_openings(str(path)) == [
            (None, ("Nf3", "c7", "g12", "l7")),
            (None, ("e2e3", "c7")),
            (None, ("Nf3", "b7c7")),
            ("4PC", ("Nf3", "c7")),
            ("4PC", ("e2e3", "c7")),
        ]
    assert _start_side("4PC") == 0
    assert _start_side("B-test") == 1


def test_plain_fen4_lines_can_be_loaded_from_txt_and_paired():
    red = make_fen("R", {"e1": "rN"}, metadata=True)
    blue = make_fen("B", {"a7": "bK"})
    green = make_fen("G", {"m7": "gP"})

    with TemporaryDirectory() as temp:
        path = Path(temp) / "positions.txt"
        path.write_text(
            "\ufeff# One FEN4 per line; leading BOM, comments, and blank lines work\n"
            f"{red}\n"
            "\n"
            f"  {blue}  \n"
            f"fen {green}\n"
            f"{red} | moves e1f3\n"
            f"fen {blue} | san c7\n"
            "4PC\n"
            "moves e2e3 b7c7\n"
            "Ra4xQd4\n",
            encoding="utf-8",
        )
        assert _load_openings(str(path)) == [
            (red, ()),
            (blue, ()),
            (green, ()),
            (red, ("e1f3",)),
            (blue, ("c7",)),
            ("4PC", ()),
            (None, ("e2e3", "b7c7")),
            (None, ("Ra4xQd4",)),
        ]
        config = SimpleNamespace(
            match=SimpleNamespace(openings_file=str(path), paired=True, seed=20261009)
        )
        specs = MatchRunner(config)._specs(20)

    for first, second in zip(specs[::2], specs[1::2]):
        assert first.candidate_team == 0
        assert second.candidate_team == 1
        assert first.start_fen == second.start_fen
        assert first.opening_moves == second.opening_moves
        assert first.game_id + 1 == second.game_id


def test_engine_snapshot_sends_position_then_fen_moves_and_ready_barrier():
    fen = make_fen(pieces={"e1": "rN"})
    engine = UciEngine("unused", name="san-probe")
    with patch.object(engine, "send") as send, patch.object(
        engine, "_read_until", return_value=[
            fen, "legal moves (2): e1f3 e1g2", "readyok"
        ]
    ) as read:
        result = engine.opening_position("position startpos")
    assert result == (fen, ("e1f3", "e1g2"))
    assert send.call_args_list == [
        call("position startpos"), call("fen"), call("moves"), call("isready")
    ]
    read.assert_called_once()


def test_engine_snapshot_rejects_invalid_position_or_bad_move_listing():
    fen = make_fen()
    engine = UciEngine("unused", name="san-probe")
    with patch.object(engine, "send"), patch.object(
        engine, "_read_until",
        return_value=["info string move list error: illegal", fen,
                      "legal moves (0):", "readyok"]
    ):
        with pytest.raises(EngineError, match="invalid opening position"):
            engine.opening_position("position startpos moves BAD")
    with patch.object(engine, "send"), patch.object(
        engine, "_read_until", return_value=[fen, "legal moves (2): e1f3", "readyok"]
    ):
        with pytest.raises(EngineError, match="incomplete legal move"):
            engine.opening_position("position startpos")


def test_paired_match_uses_uci_openings_after_san_conversion():
    red = make_fen(pieces={"e2": "rP"})
    blue = make_fen("B", {"b7": "bP"})

    class FakeEngine:
        instances = []

        def __init__(self, path, *, name, **kwargs):
            self.name = name
            self.calls = []
            FakeEngine.instances.append(self)

        def start(self):
            self.calls.append("start")

        def configure(self, options):
            self.calls.append("configure")

        def new_game(self):
            self.calls.append("ucinewgame")

        def opening_position(self, position_command):
            self.calls.append("opening:" + position_command)
            if position_command == "position startpos":
                return red, ("e2e3",)
            if position_command == "position startpos moves e2e3":
                return blue, ("b7c7",)
            raise AssertionError(f"unexpected position: {position_command}")

        def search(self, position_command, go_command):
            self.calls.append("search:" + position_command)
            return SearchResult(bestmove="g13g12", score_cp=0)

        def close(self):
            self.calls.append("close")

    with TemporaryDirectory() as temp:
        opening_path = Path(temp) / "openings.txt"
        opening_path.write_text("san e3 c7\n", encoding="utf-8")
        config = SimpleNamespace(
            engine=SimpleNamespace(
                path="unused", common_options={}, startup_timeout=1.0,
                move_timeout=1.0, strict_options=True
            ),
            search=SearchConfig(mode="depth", value=1),
            match=SimpleNamespace(
                paired=True, workers=1, games=2, seed=2026,
                max_plies=3, openings_file=str(opening_path),
                results_file=str(Path(temp) / "unused.jsonl"),
            ),
            baseline=VariantConfig("baseline"),
            candidate=VariantConfig("candidate"),
        )
        with patch("tunex.match.UciEngine", FakeEngine):
            _, records = MatchRunner(config).run(games=2, write_results=False)

    assert len(FakeEngine.instances) == 4
    for engine in FakeEngine.instances:
        assert engine.calls.count("ucinewgame") == 1
        assert engine.calls.index("ucinewgame") < next(
            i for i, c in enumerate(engine.calls) if c.startswith("search:")
        )
    for record in records:
        assert record.opening_moves == ["e2e3", "b7c7"]
        assert record.moves[:2] == ["e2e3", "b7c7"]
    for engine in FakeEngine.instances:
        for item in engine.calls:
            if item.startswith("search:"):
                assert " moves e2e3 b7c7" in item
                assert "san" not in item


def test_illegal_san_fails_before_search():
    red = make_fen(pieces={"e1": "rN"})
    engine = SimpleNamespace(opening_position=lambda _: (red, ["e1f3"]))
    with pytest.raises(EngineError, match="opening ply 1.*illegal SAN"):
        _convert_opening(engine, None, ("Nf4",))


def test_user_long_algebraic_four_seat_opening():
    """Examples from an actual 4PC opening file; '+' is not a UCI suffix."""
    positions = [
        ("R", {"g2": "rP"}, "g2-g4", "g2g4"),
        ("B", {"b7": "bP"}, "b7-c7+", "b7c7"),
        ("Y", {"g13": "yP"}, "g13-g12", "g13g12"),
        ("G", {"m8": "gP"}, "m8-l8", "m8l8"),
        ("R", {"e1": "rN"}, "Ne1-f3", "e1f3"),
    ]
    for turn, pieces, token, expected in positions:
        assert san_to_uci(token, make_fen(turn, pieces), [expected]) == expected


def test_user_long_algebraic_piece_captures():
    """Captured-piece prefixes in Chess.com 4PC PGN4 are not destinations."""
    captures = [
        # side, origin piece, destination victim, long algebraic, UCI
        ("R", "g1", "rQ", "d4", "bP", "Qg1xd4", "g1d4"),
        ("B", "a4", "bR", "d4", "rQ", "Ra4xQd4", "a4d4"),
        ("Y", "h12", "yB", "b6", "bP", "Bh12xb6+", "h12b6"),
        ("G", "n7", "gQ", "i2", "rP", "Qn7xi2+", "n7i2"),
        ("R", "h1", "rK", "i2", "gQ", "Kh1xQi2", "h1i2"),
        ("B", "a7", "bK", "b6", "yB", "Ka7xBb6", "a7b6"),
        ("B", "b6", "bP", "c7", "rB", "b6xBc7", "b6c7"),
        ("R", "i1", "rB", "c7", "bP", "Bi1xc7", "i1c7"),
        ("Y", "f14", "yB", "l8", "gP", "Bf14xl8", "f14l8"),
    ]
    for turn, src, attacker, dst, victim, token, expected in captures:
        fen = make_fen(turn, {src: attacker, dst: victim})
        assert san_to_uci(token, fen, [expected]) == expected

    wrong_victim = make_fen("B", {"a4": "bR", "d4": "rN"})
    with pytest.raises(ValueError, match="illegal SAN"):
        san_to_uci("Ra4xQd4", wrong_victim, ["a4d4"])


def test_unprefixed_incremental_long_algebraic_opening_lines():
    """Every line is an independent opening, including prefix expansions."""
    source = (
        "g2-g4\n"
        "g2-g4 b7-c7+\n"
        "g2-g4 b7-c7+ g13-g12\n"
        "g2-g4 b7-c7+ g13-g12 m8-l8\n"
        "g2-g4 b7-c7+ g13-g12 m8-l8 Ne1-f3\n"
        "f2-f3 b9-c9 g13-g12 m8-l8 h2-h3 b4-d4 "
        "Bf14-h12 m6-l6 Qg1xd4 Ra4xQd4 Bh12xb6+ "
        "Qn7xi2+ Kh1xQi2 Ka7xBb6\n"
    )
    with TemporaryDirectory() as temp:
        path = Path(temp) / "long_algebraic.txt"
        path.write_text(source, encoding="utf-8")
        parsed = _load_openings(str(path))
    assert len(parsed) == 6
    assert all(fen is None for fen, _ in parsed)
    assert [len(tokens) for _, tokens in parsed] == [1, 2, 3, 4, 5, 14]
    assert parsed[1][1] == ("g2-g4", "b7-c7+")
    assert parsed[-1][1][-4:] == (
        "Bh12xb6+", "Qn7xi2+", "Kh1xQi2", "Ka7xBb6"
    )


def test_long_algebraic_prefix_converts_to_uci_before_gameplay():
    """Every prefix snapshot comes from native engine legal moves + FEN4."""
    positions = [
        ("R", {"g2": "rP"}, "g2g4"),
        ("B", {"b7": "bP"}, "b7c7"),
        ("Y", {"g13": "yP"}, "g13g12"),
        ("G", {"m8": "gP"}, "m8l8"),
        ("R", {"e1": "rN"}, "e1f3"),
    ]
    calls = []
    class FakeEngine:
        def opening_position(self, command):
            index = len(calls)
            calls.append(command)
            turn, board, move = positions[index]
            return make_fen(turn, board), (move,)

    result = _convert_opening(
        FakeEngine(), None,
        ("g2-g4", "b7-c7+", "g13-g12", "m8-l8", "Ne1-f3"),
    )
    assert result == ["g2g4", "b7c7", "g13g12", "m8l8", "e1f3"]
    assert calls == [
        "position startpos",
        "position startpos moves g2g4",
        "position startpos moves g2g4 b7c7",
        "position startpos moves g2g4 b7c7 g13g12",
        "position startpos moves g2g4 b7c7 g13g12 m8l8",
    ]

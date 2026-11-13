from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunex.config import (
    Config, EngineConfig, MatchConfig, SearchConfig, SprtConfig, VariantConfig,
)
from tunex.engine import EngineError, SearchResult
from tunex.match import (
    GameSpec, _is_bare_fen4, _load_openings, _play_game, _spell_start_side,
    _spell_winner, _spell_opening,
)


START_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
NO_BLACK_KING = "8/8/8/8/8/8/8/4K3 b - - 0 1"


class FakeSpellEngine:
    mode = "capture"
    instances = []

    def __init__(self, path, *, name, **kwargs):
        self.name = name
        self.path = path
        self.commands = []
        self.closed = False
        self.__class__.instances.append(self)

    def start(self):
        pass

    def configure(self, options):
        self.options = options

    def new_game(self):
        self.commands.append("ucinewgame")

    def close(self):
        self.closed = True

    def spell_legal_turns(self, position):
        return {"freeze@d5:e2e4", "e2e4", "jump@d4:d1d8"}

    def spell_status(self, position):
        self.commands.append(position)
        if "freeze@d5:e2e4" in position and self.mode == "capture":
            return NO_BLACK_KING, False
        return START_FEN, self.mode == "checkmate"

    def search(self, position, go):
        self.commands.append(go)
        if self.mode == "capture":
            return SearchResult(bestmove="freeze@d5:e2e4", score_cp=42)
        return SearchResult(bestmove="0000")


def config():
    return Config(
        engine=EngineConfig(path="spellwarden"),
        search=SearchConfig(mode="movetime", value=250),
        match=MatchConfig(games=2, workers=1, paired=True, max_plies=6),
        baseline=VariantConfig(name="baseline", options={}),
        candidate=VariantConfig(name="candidate", options={}),
        sprt=SprtConfig(),
        variant="spell_chess",
    )


class SpellChessTests(unittest.TestCase):
    def setUp(self):
        FakeSpellEngine.instances = []
        FakeSpellEngine.mode = "capture"

    def test_turn_order_and_terminal_king_capture(self):
        cfg = config()
        with patch("tunex.match.UciEngine", FakeSpellEngine):
            white = _play_game(cfg, GameSpec(0, 0), cfg.baseline, cfg.candidate)
            black = _play_game(cfg, GameSpec(1, 1), cfg.baseline, cfg.candidate)
        self.assertEqual(white.result, 1.0)
        self.assertEqual(black.result, 0.0)
        self.assertEqual(white.winner_team, 0)
        self.assertEqual(white.reason, "terminal-king-capture")
        self.assertEqual(white.moves, ["freeze@d5:e2e4"])
        self.assertEqual(white.plies, 1)
        self.assertTrue(all(engine.closed for engine in FakeSpellEngine.instances))
        self.assertTrue(all("ucinewgame" in engine.commands
                            for engine in FakeSpellEngine.instances))

    def test_no_moves_checkmate_and_stalemate(self):
        cfg = config()
        with patch("tunex.match.UciEngine", FakeSpellEngine):
            FakeSpellEngine.mode = "checkmate"
            mate = _play_game(cfg, GameSpec(0, 0), cfg.baseline, cfg.candidate)
            FakeSpellEngine.mode = "stalemate"
            draw = _play_game(cfg, GameSpec(1, 0), cfg.baseline, cfg.candidate)
        self.assertEqual(mate.result, 0.0)
        self.assertEqual(mate.winner_team, 1)
        self.assertEqual(mate.reason, "checkmate/no-legal-move")
        self.assertEqual(draw.result, 0.5)
        self.assertEqual(draw.reason, "stalemate/no-legal-move")

    def test_spell_opening_validation(self):
        engine = FakeSpellEngine("fake", name="spell")
        opening = _spell_opening(engine, None,
                                 ("freeze@d5:e2e4", "jump@d4:d1d8"))
        self.assertEqual(opening, ["freeze@d5:e2e4", "jump@d4:d1d8"])
        with self.assertRaisesRegex(EngineError, "SAN is not supported"):
            _spell_opening(engine, None, ("Nf3",))
        with self.assertRaisesRegex(EngineError, "illegal turn"):
            _spell_opening(engine, None, ("a1a7",))

    def test_spell_fen_and_opening_file(self):
        self.assertEqual(_spell_start_side(START_FEN.replace(" w ", " b ")), 1)
        self.assertEqual(_spell_start_side(None), 0)
        self.assertEqual(_spell_winner(NO_BLACK_KING), 0)
        self.assertIsNone(_spell_winner(START_FEN))
        self.assertTrue(_is_bare_fen4("4PC"))
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "openings.txt"
            file.write_text(
                START_FEN + " | moves freeze@d5:e2e4\n"
                + "moves jump@d4:d1d8\n", encoding="utf-8"
            )
            parsed = _load_openings(str(file), "spell_chess")
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0], (START_FEN, ("freeze@d5:e2e4",)))
        self.assertEqual(parsed[1], (None, ("jump@d4:d1d8",)))

    def test_variant_config_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text('{"variant": "spell_chess", '
                            '"engine": {"path": "spellwarden"}}', encoding="utf-8")
            self.assertEqual(Config.load(path).variant, "spell_chess")
            path.write_text('{"engine": {"path": "ironphoenix"}}',
                            encoding="utf-8")
            self.assertEqual(Config.load(path).variant, "four_player_teams")
            path.write_text('{"variant": "unknown", "engine": {"path": "engine"}}',
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "variant must"):
                Config.load(path)


if __name__ == "__main__":
    unittest.main()

"""Verify Tunex resets IronPhoenix exactly once before EACH game.

Correction histories now persist across go searches, so sending ucinewgame
between moves would silently erase learned pawn and continuation corrections.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from tunex.config import SearchConfig, VariantConfig
from tunex.engine import SearchResult, UciEngine
from tunex.match import MatchRunner


class UciNewGameProtocolTests(unittest.TestCase):
    def test_new_game_sends_ucinewgame_and_waits_for_readyok(self):
        engine = UciEngine("unused", name="candidate")
        with patch.object(engine, "send") as send, patch.object(
            engine, "_read_until", return_value=["readyok"]
        ) as read_until:
            engine.new_game()

        self.assertEqual(send.call_args_list, [
            call("ucinewgame"),
            call("isready"),
        ])
        read_until.assert_called_once()


class MatchNewGameTests(unittest.TestCase):
    def test_each_game_resets_both_engines_once_before_any_search(self):
        class RecordingEngine:
            instances: list["RecordingEngine"] = []

            def __init__(self, path, *, name, **kwargs):
                self.name = name
                self.calls: list[str] = []
                RecordingEngine.instances.append(self)

            def start(self):
                self.calls.append("start")

            def configure(self, options):
                self.calls.append("configure")

            def new_game(self):
                self.calls.append("ucinewgame")

            def search(self, position_command, go_command):
                self.calls.append("search")
                return SearchResult(bestmove="h2h3", score_cp=0)

            def close(self):
                self.calls.append("close")

        for workers in (1, 2):
            with self.subTest(workers=workers):
                RecordingEngine.instances.clear()
                config = SimpleNamespace(
                    engine=SimpleNamespace(
                        path="unused", common_options={},
                        startup_timeout=1.0, move_timeout=1.0,
                        strict_options=True,
                    ),
                    search=SearchConfig(mode="depth", value=1),
                    match=SimpleNamespace(
                        paired=True, workers=workers, games=2, seed=19,
                        max_plies=4, openings_file=None,
                        results_file="unused.jsonl",
                    ),
                    baseline=VariantConfig("baseline"),
                    candidate=VariantConfig("candidate"),
                )

                with patch("tunex.match.UciEngine", RecordingEngine):
                    _, records = MatchRunner(config).run(
                        games=2, write_results=False
                    )

                # Two fresh engines per game, including swapped-team game 2.
                self.assertEqual(len(records), 2)
                self.assertEqual(len(RecordingEngine.instances), 4)
                for engine in RecordingEngine.instances:
                    self.assertEqual(engine.calls.count("ucinewgame"), 1)
                    self.assertEqual(engine.calls.count("search"), 2)
                    self.assertEqual(engine.calls[:3], [
                        "start", "configure", "ucinewgame",
                    ])
                    self.assertEqual(engine.calls[-1], "close")
                    self.assertLess(
                        engine.calls.index("ucinewgame"),
                        engine.calls.index("search"),
                    )


if __name__ == "__main__":
    unittest.main()

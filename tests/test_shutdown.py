"""Regression coverage for clean UCI subprocess shutdown and cancellation."""

from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tunex.cli import main
from tunex.config import VariantConfig
from tunex.engine import EngineError, UciEngine
from tunex.match import MatchRunner


def fake_process(*, running: bool) -> Mock:
    proc = Mock()
    proc.poll.return_value = None if running else 0
    proc.stdin = Mock()
    proc.stdout = Mock()
    proc.stderr = None
    return proc


class UciEngineShutdownTests(unittest.TestCase):
    def test_close_closes_pipes_when_windows_close_raises_invalid_argument(self):
        proc = fake_process(running=False)
        proc.stdout.close.side_effect = OSError(22, "Invalid argument")
        engine = UciEngine("unused", name="test")
        engine.process = proc

        engine.close()
        engine.close()  # safe to call twice

        self.assertIsNone(engine.process)
        proc.stdin.close.assert_called_once()
        proc.stdout.close.assert_called_once()

    def test_hung_engine_is_killed_and_all_pipes_closed(self):
        proc = fake_process(running=True)
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="ironphoenix.exe", timeout=1.0),
            0,
        ]
        engine = UciEngine("unused", name="test")
        engine.process = proc

        engine.close()

        proc.stdin.write.assert_called_once_with("stop\\nquit\\n")
        proc.kill.assert_called_once()
        proc.stdin.close.assert_called_once()
        proc.stdout.close.assert_called_once()

    def test_cancellation_interrupts_blocked_search(self):
        cancel = threading.Event()
        proc = fake_process(running=True)
        engine = UciEngine("unused", name="test", move_timeout=5, cancel_event=cancel)
        engine.process = proc
        timer = threading.Timer(0.05, cancel.set)
        timer.start()
        try:
            with self.assertRaisesRegex(EngineError, "interrupted"):
                engine.search("position startpos", "go infinite")
        finally:
            timer.join(timeout=1)
            engine.close()

        proc.kill.assert_called_once()
        proc.stdin.close.assert_called_once()
        proc.stdout.close.assert_called_once()


class MatchCancellationTests(unittest.TestCase):
    def test_parallel_failure_cancels_waiting_worker_and_pending_games(self):
        cfg = SimpleNamespace(
            match=SimpleNamespace(
                paired=True, workers=2, games=20, seed=10,
                openings_file=None, results_file="unused.jsonl",
            ),
            baseline=VariantConfig("base"),
            candidate=VariantConfig("candidate"),
        )
        runner = MatchRunner(cfg)

        def play(_config, spec, _baseline, _candidate, cancel_event):
            if spec.game_id == 0:
                time.sleep(0.05)
                raise EngineError("simulated engine failure")
            if not cancel_event.wait(2):
                raise AssertionError("running worker was not cancelled")
            raise EngineError("cancelled")

        with patch("tunex.match._play_game", side_effect=play):
            with self.assertRaisesRegex(EngineError, "game 0 failed"):
                runner.run(games=20, write_results=False)

        self.assertTrue(runner.cancel_event.is_set())

    def test_cli_ctrl_c_exits_without_traceback(self):
        err = StringIO()
        with patch("tunex.cli._load", return_value=object()), patch(
            "tunex.cli.command_match", side_effect=KeyboardInterrupt
        ), redirect_stderr(err):
            code = main(["-c", "irrelevant.json", "match"])
        self.assertEqual(code, 130)
        self.assertIn("interrupted", err.getvalue().lower())
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()

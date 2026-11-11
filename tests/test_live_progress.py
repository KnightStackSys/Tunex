"""Live progress covers games that have not finished and SPRT batches."""

import threading
from types import SimpleNamespace
from unittest.mock import patch

from tunex.cli import _game_progress, command_sprt
from tunex.config import SearchConfig, SprtConfig, VariantConfig
from tunex.engine import SearchResult
from tunex.match import GameSpec, MatchRunner


class FakeEngine:
    def __init__(self, path, *, name, **kwargs):
        self.path = path
        self.name = name
        self.searches = 0

    def start(self):
        pass

    def configure(self, options):
        pass

    def new_game(self):
        pass

    def search(self, position_command, go_command):
        self.searches += 1
        return SearchResult(bestmove="h2h3", score_cp=0)

    def close(self):
        pass


def config(workers=2, games=2, plies=33):
    return SimpleNamespace(
        engine=SimpleNamespace(
            path="shared.exe", common_options={},
            startup_timeout=1.0, move_timeout=1.0,
            strict_options=True,
        ),
        baseline=VariantConfig("baseline", path="old.exe"),
        candidate=VariantConfig("candidate", path="new.exe"),
        search=SearchConfig(mode="movetime", value=250),
        match=SimpleNamespace(
            paired=True, workers=workers, games=games, seed=9,
            max_plies=plies, openings_file=None, results_file="unused.jsonl",
        ),
        sprt=SprtConfig(elo0=0, elo1=5, max_games=games, batch_games=2),
    )


def test_progress_starts_before_game_finishes_and_reports_ply_counts():
    for workers in (1, 2):
        events = []
        completed = []
        runner = MatchRunner(config(workers=workers))
        with patch("tunex.match.UciEngine", FakeEngine):
            wdl, records = runner.run(
                games=2, write_results=False,
                on_progress=lambda spec, stage, plies: events.append(
                    (spec.game_id, stage, plies)
                ),
                on_game_complete=lambda record, stats: completed.append(
                    (record.game_id, stats.games)
                ),
            )

        assert len(records) == 2
        assert wdl.games == 2
        assert len(completed) == 2
        assert sorted([count for _, count in completed]) == [1, 2]
        for game_id in (0, 1):
            one_game = [(stage, plies) for number, stage, plies in events
                        if number == game_id]
            assert one_game == [
                ("starting", 0), ("playing", 0),
                ("playing", 16), ("playing", 32),
            ]


def test_legacy_match_without_callback_does_not_change_engine_contract():
    runner = MatchRunner(config(workers=2, plies=2))
    with patch("tunex.match.UciEngine", FakeEngine):
        stats, records = runner.run(games=2, write_results=False)
    assert stats.games == 2
    assert len(records) == 2


def test_sprt_quiet_progress_does_not_change_match_progress(capsys):
    spec = GameSpec(game_id=7, candidate_team=1)
    quiet = _game_progress("SPRT", threading.Lock(), show_playing=False)
    quiet(spec, "starting", 0)
    quiet(spec, "playing", 0)
    quiet(spec, "playing", 16)
    assert capsys.readouterr().out == (
        "[SPRT] Game 8 starting | candidate Blue+Green\\n"
    )

    verbose = _game_progress("Match", threading.Lock())
    verbose(spec, "starting", 0)
    verbose(spec, "playing", 0)
    verbose(spec, "playing", 16)
    assert capsys.readouterr().out == (
        "[Match] Game 8 starting | candidate Blue+Green\\n"
        "[Match] Game 8 playing | ply 0\\n"
        "[Match] Game 8 playing | ply 16\\n"
    )


def test_sprt_prints_start_per_game_and_batch_decision(capsys, tmp_path):
    cfg = config(workers=2, plies=17)
    cfg.match.results_file = str(tmp_path / "games.jsonl")
    with patch("tunex.match.UciEngine", FakeEngine):
        # No decisive score after two draws, so the SPRT hits max_games.
        code = command_sprt(cfg)

    output = capsys.readouterr().out
    assert code == 3
    assert "[SPRT] Starting | baseline=old.exe | candidate=new.exe" in output
    assert "workers=2 | go movetime 250" in output
    assert "Game 1 starting" in output
    assert "Game 2 starting" in output
    assert "[SPRT] Game 1 playing | ply" not in output
    assert "[SPRT] Game 2 playing | ply" not in output
    assert " playing | ply " not in output
    assert "[SPRT] Completed 1/2" in output
    assert "[SPRT] Completed 2/2" in output
    assert "SPRT reached max_games" in output

from __future__ import annotations

import argparse
import json
import sys
import threading

from .bayesian import BayesianRunner
from .config import Config
from .engine import EngineError, UciEngine
from .match import GameRecord, GameSpec, MatchRunner
from .sprt import SprtState
from .spsa import SpsaRunner
from .stats import WDL, format_summary


def _load(path: str) -> Config:
    try:
        return Config.load(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"configuration error: {exc}") from exc


def command_probe(config: Config) -> int:
    options = dict(config.engine.common_options)
    options.update(config.candidate.options)
    with UciEngine(
        config.candidate.path or config.engine.path,
        name=config.candidate.name,
        startup_timeout=config.engine.startup_timeout,
        move_timeout=config.engine.move_timeout,
        strict_options=config.engine.strict_options,
    ) as engine:
        print(f"engine: {engine.path}")
        print(f"advertised options: {len(engine.options)}")
        engine.configure(options)
        print("configured successfully")
        try:
            lines = engine.diagnostic()
        except EngineError:
            lines = []
        for line in lines:
            print(line)
    return 0


def _game_progress(
    label: str, lock: threading.Lock, *, show_playing: bool = True
):
    """Progress callback is called on worker threads; serialize console lines."""
    def report(spec: GameSpec, stage: str, plies: int) -> None:
        if stage == "playing" and not show_playing:
            return
        with lock:
            team = "Red+Yellow" if spec.candidate_team == 0 else "Blue+Green"
            if stage == "starting":
                print(f"[{label}] Game {spec.game_id + 1} starting | candidate {team}",
                      flush=True)
            else:
                print(f"[{label}] Game {spec.game_id + 1} playing | ply {plies}",
                      flush=True)
    return report


def command_match(config: Config, games: int | None) -> int:
    runner = MatchRunner(config)
    target = games if games is not None else config.match.games
    output_lock = threading.Lock()

    def completed(record: GameRecord, wdl: WDL) -> None:
        label = ("WIN" if record.result == 1.0
                 else "LOSS" if record.result == 0.0 else "DRAW")
        with output_lock:
            print(
                f"[Match] Completed {wdl.games}/{target} | "
                f"game {record.game_id + 1}: {label} | "
                f"W/D/L {wdl.wins}/{wdl.draws}/{wdl.losses}",
                flush=True,
            )

    print(
        f"[Match] Starting {target} games with {config.match.workers} workers | "
        f"{config.search.go_command()}", flush=True,
    )
    wdl, records = runner.run(
        games=games, on_progress=_game_progress("Match", output_lock),
        on_game_complete=completed,
    )
    print(format_summary(wdl))
    reasons: dict[str, int] = {}
    for record in records:
        reasons[record.reason] = reasons.get(record.reason, 0) + 1
    print("termination:", ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    return 0


def command_sprt(config: Config) -> int:
    runner = MatchRunner(config)
    state = SprtState(
        elo0=config.sprt.elo0,
        elo1=config.sprt.elo1,
        alpha=config.sprt.alpha,
        beta=config.sprt.beta,
    )
    total = WDL()
    played = 0
    output_lock = threading.Lock()

    print(
        f"[SPRT] Starting | baseline={config.baseline.path or config.engine.path} "
        f"| candidate={config.candidate.path or config.engine.path}",
        flush=True,
    )
    print(
        f"[SPRT] Up to {config.sprt.max_games} games | "
        f"batch={config.sprt.batch_games} | "
        f"workers={config.match.workers} | {config.search.go_command()}",
        flush=True,
    )

    while played < config.sprt.max_games and state.decision == "continue":
        batch = min(config.sprt.batch_games, config.sprt.max_games - played)
        if config.match.paired and batch % 2:
            batch -= 1
        if batch <= 0:
            break

        def completed(record: GameRecord, batch_wdl: WDL) -> None:
            result_label = ("WIN" if record.result == 1.0
                            else "LOSS" if record.result == 0.0 else "DRAW")
            with output_lock:
                print(
                    f"[SPRT] Completed {played + batch_wdl.games}/"
                    f"{config.sprt.max_games} | "
                    f"game {record.game_id + 1}: {result_label} | "
                    f"W/D/L {total.wins + batch_wdl.wins}/"
                    f"{total.draws + batch_wdl.draws}/"
                    f"{total.losses + batch_wdl.losses}",
                    flush=True,
                )

        batch_wdl, _ = runner.run(
            games=batch, offset=played,
            on_progress=_game_progress("SPRT", output_lock, show_playing=False),
            on_game_complete=completed,
        )
        total.extend(batch_wdl)
        played += batch
        state.update(total)
        print(format_summary(total))
        print(state.summary())

    if state.decision == "H1":
        print("SPRT accepted H1: candidate reached the positive Elo hypothesis.")
        return 0
    if state.decision == "H0":
        print("SPRT accepted H0: candidate did not reach the positive Elo hypothesis.")
        return 2
    print("SPRT reached max_games without crossing a boundary.")
    return 3


def command_spsa(config: Config, iterations: int | None) -> int:
    runner = SpsaRunner(config)
    runner.run(iterations)
    print("current tuned options:")
    for name, value in runner.current_options().items():
        print(f"  {name} = {value}")
    return 0


def command_bayesian(config: Config, iterations: int | None) -> int:
    runner = BayesianRunner(config)
    runner.run(iterations)
    print("best observed Bayesian options (validate with SPRT):")
    for name, value in runner.best_options().items():
        print(f"  {name} = {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tunex",
        description="UCI match testing, SPRT, and SPSA tuning for 4PC Teams and Spell Chess.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="configs/ironphoenix.json",
        help="JSON configuration file (default: configs/ironphoenix.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("probe", help="start the engine, apply options, and print tuning state")

    match = sub.add_parser("match", help="run a fixed number of candidate-vs-baseline games")
    match.add_argument("--games", type=int, default=None)

    sub.add_parser("sprt", help="run sequential candidate-vs-baseline testing")

    spsa = sub.add_parser("spsa", help="run SPSA parameter tuning")
    spsa.add_argument("--iterations", type=int, default=None)

    bayesian = sub.add_parser("bayesian", help="run resumable Bayesian TPE tuning")
    bayesian.add_argument("--iterations", type=int, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _load(args.config)

    try:
        if args.command == "probe":
            return command_probe(config)
        if args.command == "match":
            return command_match(config, args.games)
        if args.command == "sprt":
            return command_sprt(config)
        if args.command == "spsa":
            return command_spsa(config, args.iterations)
        if args.command == "bayesian":
            return command_bayesian(config, args.iterations)
    except KeyboardInterrupt:
        print("\nTunex interrupted; active engine processes stopped.", file=sys.stderr)
        return 130
    except (EngineError, OSError, ValueError) as exc:
        print(f"tunex error: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2

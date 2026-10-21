from __future__ import annotations

import argparse
import json
import sys

from .config import Config
from .engine import EngineError, UciEngine
from .match import MatchRunner
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
        config.engine.path,
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


def command_match(config: Config, games: int | None) -> int:
    runner = MatchRunner(config)
    wdl, records = runner.run(games=games)
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

    while played < config.sprt.max_games and state.decision == "continue":
        batch = min(config.sprt.batch_games, config.sprt.max_games - played)
        if config.match.paired and batch % 2:
            batch -= 1
        if batch <= 0:
            break

        batch_wdl, _ = runner.run(games=batch, offset=played)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tunex",
        description="UCI match testing, SPRT, and SPSA tuning for IronPhoenix.",
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
    except KeyboardInterrupt:
        print("\nTunex interrupted; active engine processes stopped.", file=sys.stderr)
        return 130
    except (EngineError, OSError, ValueError) as exc:
        print(f"tunex error: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2

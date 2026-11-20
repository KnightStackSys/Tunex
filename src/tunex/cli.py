from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import signal
import json
import sys
import threading

from .bayesian import BayesianRunner
from .cmaes import CmaesRunner
from .config import Config
from .engine import EngineError, UciEngine
from .xboard import XBoardEngine
from .match import GameRecord, GameSpec, MatchRunner
from .sprt import SprtState
from .spsa import SpsaRunner
from .replay import replay_game
from .texel import TexelRunner
from .stats import WDL, format_summary


def _load(path: str) -> Config:
    try:
        return Config.load(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"configuration error: {exc}") from exc


def command_probe(config: Config) -> int:
    options = dict(config.engine.common_options)
    options.update(config.candidate.options)
    protocol = getattr(config.candidate, 'protocol', None) or getattr(config.engine, 'protocol', 'uci')
    engine_type = UciEngine if protocol == "uci" else XBoardEngine
    arguments = getattr(config.candidate, 'arguments', None)
    if arguments is None:
        arguments = getattr(config.engine, 'arguments', [])
    kwargs = dict(
        name=config.candidate.name,
        startup_timeout=config.engine.startup_timeout,
        move_timeout=config.engine.move_timeout,
        strict_options=config.engine.strict_options,
    )
    if arguments:
        kwargs["arguments"] = arguments
    if protocol == "xboard":
        kwargs["variant_name"] = getattr(config.candidate, 'xboard_variant', None) or getattr(config.engine, 'xboard_variant', None)
    with engine_type(config.candidate.path or config.engine.path, **kwargs) as engine:
        print(f"engine: {engine.path} ({protocol})")
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
    label: str, lock: threading.Lock, *, show_playing: bool = True,
    standard_chess: bool = False,
):
    """Progress callback is called on worker threads; serialize console lines."""
    def report(spec: GameSpec, stage: str, plies: int) -> None:
        if stage == "playing" and not show_playing:
            return
        with lock:
            team = (("White" if spec.candidate_team == 0 else "Black")
                    if standard_chess else ("Red+Yellow" if spec.candidate_team == 0 else "Blue+Green"))
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
        games=games, on_progress=_game_progress("Match", output_lock,
                                   standard_chess=getattr(config, "variant", "four_player_teams") == "standard_chess"),
        on_game_complete=completed,
    )
    print(format_summary(wdl))
    reasons: dict[str, int] = {}
    for record in records:
        reasons[record.reason] = reasons.get(record.reason, 0) + 1
    print("termination:", ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    return 0


def _sprt_signature(config: Config) -> str:
    """Bind checkpoint to engine, variants, opening schedule and SPRT hypotheses."""
    payload = {
        "variant": config.variant,
        "engine": asdict(config.engine),
        "search": asdict(config.search),
        "match": {
            "paired": config.match.paired,
            "seed": config.match.seed,
            "openings_file": config.match.openings_file,
            "max_plies": config.match.max_plies,
        },
        "baseline": asdict(config.baseline),
        "candidate": asdict(config.candidate),
        "sprt": {
            "elo0": config.sprt.elo0,
            "elo1": config.sprt.elo1,
            "alpha": config.sprt.alpha,
            "beta": config.sprt.beta,
            "batch_games": config.sprt.batch_games,
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _load_sprt_checkpoint(config: Config) -> tuple[WDL, int]:
    path = Path(config.sprt.state_file)
    if not path.exists():
        return WDL(), 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or payload.get("signature") != _sprt_signature(config):
        raise ValueError(
            f"SPRT checkpoint {path} is incompatible with this experiment; "
            "use a different sprt.state_file or --fresh to start again"
        )
    wdl_data = payload["wdl"]
    total = WDL(
        wins=int(wdl_data["wins"]),
        draws=int(wdl_data["draws"]),
        losses=int(wdl_data["losses"]),
    )
    played = int(payload["played"])
    if min(total.wins, total.draws, total.losses) < 0 or played != total.games:
        raise ValueError(f"SPRT checkpoint {path} has inconsistent game totals")
    if played < 0 or (config.match.paired and played % 2):
        raise ValueError(f"SPRT checkpoint {path} has invalid game count")
    return total, played


def _save_sprt_checkpoint(config: Config, total: WDL, played: int) -> None:
    path = Path(config.sprt.state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "signature": _sprt_signature(config),
        "played": played,
        "wdl": asdict(total),
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def command_sprt(config: Config, *, fresh: bool = False) -> int:
    if fresh:
        Path(config.sprt.state_file).unlink(missing_ok=True)
    total, played = _load_sprt_checkpoint(config)
    runner = MatchRunner(config)
    state = SprtState(
        elo0=config.sprt.elo0,
        elo1=config.sprt.elo1,
        alpha=config.sprt.alpha,
        beta=config.sprt.beta,
    )
    state.update(total)
    output_lock = threading.Lock()
    stop_after_batch = threading.Event()
    previous_sigint = None
    can_install_signal = threading.current_thread() is threading.main_thread()

    def request_stop(signum: int, frame: object) -> None:
        if stop_after_batch.is_set():
            # A second Ctrl+C is an emergency interruption; the in-progress
            # batch is not checkpointed and will be replayed on resume.
            raise KeyboardInterrupt
        stop_after_batch.set()
        print("\n[SPRT] Stop requested; finishing current batch before saving.",
              flush=True)

    if can_install_signal:
        previous_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, request_stop)

    try:
        print(
            f"[SPRT] {'Resuming' if played else 'Starting'} at {played} games | "
            f"baseline={config.baseline.path or config.engine.path} "
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
            if stop_after_batch.is_set():
                break
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
                on_progress=_game_progress("SPRT", output_lock, show_playing=False,
                                           standard_chess=config.variant == "standard_chess"),
                on_game_complete=completed,
            )
            total.extend(batch_wdl)
            played += batch
            state.update(total)
            _save_sprt_checkpoint(config, total, played)
            print(format_summary(total))
            print(state.summary())

        if stop_after_batch.is_set():
            print(f"[SPRT] Paused after {played} games; "
                  f"checkpoint: {config.sprt.state_file}", flush=True)
            return 0
        if state.decision == "H1":
            print("SPRT accepted H1: candidate reached the positive Elo hypothesis.")
            return 0
        if state.decision == "H0":
            print("SPRT accepted H0: candidate did not reach the positive Elo hypothesis.")
            return 2
        print("SPRT reached max_games without crossing a boundary.")
        return 3
    finally:
        if can_install_signal:
            signal.signal(signal.SIGINT, previous_sigint)



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


def command_cmaes(config: Config, generations: int | None) -> int:
    runner = CmaesRunner(config)
    runner.run(generations)
    print("best observed CMA-ES options (validate with independent SPRT):")
    for name, value in runner.best_options().items():
        print(f"  {name} = {value}")
    return 0


def command_texel(config: Config, epochs: int | None) -> int:
    runner = TexelRunner(config)
    runner.run(epochs)
    print("best validation HCE weights (verify in independent matches):")
    for name, value in runner.best_weights.items():
        print(f"  {name} = {value}")
    return 0


def command_replay(config: Config, file: str, team: str, report: str | None) -> int:
    from pathlib import Path

    def progress(entry):
        status = "MATCH" if entry.matched else "DIFFERENT"
        print(
            f"[Replay] ply {entry.ply:02d} {entry.seat} | "
            f"PGN {entry.recorded} ({entry.recorded_uci}) | "
            f"engine {entry.engine_move} | {status}"
            + (f" | mate {entry.score_mate}" if entry.score_mate is not None
               else f" | cp {entry.score_cp}" if entry.score_cp is not None else ""),
            flush=True,
        )

    report_data = replay_game(config, Path(file), team, report_file=report,
                              on_comparison=progress)
    print(
        f"[Replay] {report_data.team}: matched {report_data.matched}/"
        f"{report_data.compared}, different {report_data.mismatched}; "
        f"forced {report_data.total_plies} recorded plies",
        flush=True,
    )
    if report:
        print(f"[Replay] JSON report: {report}")
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

    replay = sub.add_parser(
        "replay", help="replay a PGN4 TXT game, search RY or BG, force recorded moves"
    )
    replay.add_argument("--file", required=True,
                        help="game TXT/PGN path, relative to the config directory")
    replay.add_argument("--team", required=True, choices=["RY", "BG", "ry", "bg"],
                        help="team controlled by the tested engine")
    replay.add_argument("--report", default=None,
                        help="optional JSON comparison output path")

    sprt = sub.add_parser("sprt", help="run resumable SPRT; Ctrl+C stops after current batch")
    sprt.add_argument("--fresh", action="store_true",
                      help="discard the existing SPRT checkpoint and start over")

    spsa = sub.add_parser("spsa", help="run SPSA parameter tuning")
    spsa.add_argument("--iterations", type=int, default=None)

    bayesian = sub.add_parser("bayesian", help="run resumable Bayesian TPE tuning")
    bayesian.add_argument("--iterations", type=int, default=None)

    cmaes = sub.add_parser("cmaes", help="run resumable full-covariance CMA-ES tuning")
    cmaes.add_argument("--generations", type=int, default=None)

    texel = sub.add_parser("texel", help="fit HCE weights to labeled positions")
    texel.add_argument("--epochs", type=int, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _load(args.config)

    if args.command == "replay":
        from pathlib import Path
        config_dir = Path(args.config).resolve().parent
        for attr in ("file", "report"):
            value = getattr(args, attr, None)
            if value and not Path(value).is_absolute():
                setattr(args, attr, str((config_dir / value).resolve()))

    try:
        if args.command == "probe":
            return command_probe(config)
        if args.command == "match":
            return command_match(config, args.games)
        if args.command == "replay":
            return command_replay(config, args.file, args.team, args.report)
        if args.command == "sprt":
            return command_sprt(config, fresh=args.fresh)
        if args.command == "spsa":
            return command_spsa(config, args.iterations)
        if args.command == "bayesian":
            return command_bayesian(config, args.iterations)
        if args.command == "cmaes":
            return command_cmaes(config, args.generations)
        if args.command == "texel":
            return command_texel(config, args.epochs)
    except KeyboardInterrupt:
        print("\nTunex interrupted; active engine processes stopped.", file=sys.stderr)
        return 130
    except (EngineError, OSError, ValueError) as exc:
        print(f"tunex error: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import random
import threading
from typing import Any

from .config import Config, VariantConfig
from .engine import EngineError, SearchResult, UciEngine
from .stats import WDL


COLORS = ("R", "B", "Y", "G")
COLOR_TO_INDEX = {c: i for i, c in enumerate(COLORS)}


@dataclass(slots=True)
class GameSpec:
    game_id: int
    candidate_team: int
    opening_moves: tuple[str, ...] = ()
    start_fen: str | None = None


@dataclass(slots=True)
class GameRecord:
    game_id: int
    candidate_team: int
    result: float
    winner_team: int | None
    reason: str
    plies: int
    moves: list[str]
    opening_moves: list[str]
    start_fen: str | None
    last_score_cp: int | None = None
    last_score_mate: int | None = None


class ResultWriter:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: GameRecord) -> None:
        line = json.dumps(asdict(record), sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def _load_openings(path: str | None) -> list[tuple[str | None, tuple[str, ...]]]:
    if not path:
        return [(None, ())]
    openings: list[tuple[str | None, tuple[str, ...]]] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("fen "):
            if " | moves " in line:
                fen, move_text = line[4:].split(" | moves ", 1)
                openings.append((fen.strip(), tuple(move_text.split())))
            else:
                openings.append((line[4:].strip(), ()))
        elif line.startswith("moves "):
            openings.append((None, tuple(line[6:].split())))
        else:
            openings.append((None, tuple(line.split())))
    if not openings:
        raise ValueError(f"no usable openings found in {path}")
    return openings


def _position_command(start_fen: str | None, moves: list[str]) -> str:
    if start_fen:
        command = f"position fen {start_fen}"
    else:
        command = "position startpos"
    if moves:
        command += " moves " + " ".join(moves)
    return command


def _start_side(start_fen: str | None) -> int:
    if not start_fen:
        return 0
    first = start_fen.lstrip()[:1].upper()
    if first not in COLOR_TO_INDEX:
        raise ValueError(f"unable to read side-to-move from FEN4: {start_fen!r}")
    return COLOR_TO_INDEX[first]


def _candidate_result(winner_team: int | None, candidate_team: int) -> float:
    if winner_team is None:
        return 0.5
    return 1.0 if winner_team == candidate_team else 0.0


def _merged_options(common: dict[str, Any], variant: VariantConfig) -> dict[str, Any]:
    merged = dict(common)
    merged.update(variant.options)
    return merged


def _play_game(config: Config, spec: GameSpec, baseline: VariantConfig, candidate: VariantConfig) -> GameRecord:
    candidate_options = _merged_options(config.engine.common_options, candidate)
    baseline_options = _merged_options(config.engine.common_options, baseline)

    engines = {
        spec.candidate_team: UciEngine(
            config.engine.path,
            name=candidate.name,
            startup_timeout=config.engine.startup_timeout,
            move_timeout=config.engine.move_timeout,
            strict_options=config.engine.strict_options,
        ),
        1 - spec.candidate_team: UciEngine(
            config.engine.path,
            name=baseline.name,
            startup_timeout=config.engine.startup_timeout,
            move_timeout=config.engine.move_timeout,
            strict_options=config.engine.strict_options,
        ),
    }

    moves = list(spec.opening_moves)
    last_result: SearchResult | None = None

    try:
        for engine in engines.values():
            engine.start()
        engines[spec.candidate_team].configure(candidate_options)
        engines[1 - spec.candidate_team].configure(baseline_options)
        for engine in engines.values():
            engine.new_game()

        initial_side = _start_side(spec.start_fen)
        for _ in range(config.match.max_plies):
            side = (initial_side + len(moves)) % 4
            team = side & 1
            engine = engines[team]
            position = _position_command(spec.start_fen, moves)
            result = engine.search(position, config.search.go_command())
            last_result = result

            if result.bestmove == "0000":
                in_check = engine.side_in_check(position)
                if in_check:
                    winner_team = 1 - team
                    reason = "checkmate/no-legal-move"
                else:
                    winner_team = None
                    reason = "stalemate/no-legal-move"
                return GameRecord(
                    game_id=spec.game_id,
                    candidate_team=spec.candidate_team,
                    result=_candidate_result(winner_team, spec.candidate_team),
                    winner_team=winner_team,
                    reason=reason,
                    plies=len(moves),
                    moves=moves,
                    opening_moves=list(spec.opening_moves),
                    start_fen=spec.start_fen,
                    last_score_cp=result.score_cp,
                    last_score_mate=result.score_mate,
                )

            moves.append(result.bestmove)

            # IronPhoenix encodes an immediate terminal king capture as mate 1
            # at the root (MATE_SCORE - 1). End immediately so the tuner does
            # not attempt to continue a game after the 4PC terminal capture.
            if result.score_mate == 1:
                winner_team = team
                return GameRecord(
                    game_id=spec.game_id,
                    candidate_team=spec.candidate_team,
                    result=_candidate_result(winner_team, spec.candidate_team),
                    winner_team=winner_team,
                    reason="terminal-king-capture",
                    plies=len(moves),
                    moves=moves,
                    opening_moves=list(spec.opening_moves),
                    start_fen=spec.start_fen,
                    last_score_cp=result.score_cp,
                    last_score_mate=result.score_mate,
                )

        return GameRecord(
            game_id=spec.game_id,
            candidate_team=spec.candidate_team,
            result=0.5,
            winner_team=None,
            reason="max-plies",
            plies=len(moves),
            moves=moves,
            opening_moves=list(spec.opening_moves),
            start_fen=spec.start_fen,
            last_score_cp=last_result.score_cp if last_result else None,
            last_score_mate=last_result.score_mate if last_result else None,
        )
    finally:
        for engine in engines.values():
            engine.close()


class MatchRunner:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.openings = _load_openings(config.match.openings_file)

    def _specs(self, games: int, offset: int = 0) -> list[GameSpec]:
        if self.config.match.paired and games % 2:
            raise ValueError("paired matches require an even game count")
        rng = random.Random(self.config.match.seed + offset)
        specs: list[GameSpec] = []

        if self.config.match.paired:
            for pair in range(games // 2):
                opening = self.openings[rng.randrange(len(self.openings))]
                start_fen, opening_moves = opening
                base_id = offset + pair * 2
                specs.append(GameSpec(base_id, 0, opening_moves, start_fen))
                specs.append(GameSpec(base_id + 1, 1, opening_moves, start_fen))
        else:
            for i in range(games):
                opening = self.openings[rng.randrange(len(self.openings))]
                start_fen, opening_moves = opening
                specs.append(GameSpec(offset + i, i & 1, opening_moves, start_fen))
        return specs

    def run(
        self,
        *,
        baseline: VariantConfig | None = None,
        candidate: VariantConfig | None = None,
        games: int | None = None,
        offset: int = 0,
        write_results: bool = True,
    ) -> tuple[WDL, list[GameRecord]]:
        baseline = baseline or self.config.baseline
        candidate = candidate or self.config.candidate
        games = games if games is not None else self.config.match.games
        specs = self._specs(games, offset)
        writer = ResultWriter(self.config.match.results_file) if write_results else None
        wdl = WDL()
        records: list[GameRecord] = []

        def completed(record: GameRecord) -> None:
            records.append(record)
            wdl.add(record.result)
            if writer:
                writer.write(record)

        if self.config.match.workers == 1:
            for spec in specs:
                completed(_play_game(self.config, spec, baseline, candidate))
        else:
            with ThreadPoolExecutor(max_workers=self.config.match.workers) as pool:
                futures = {
                    pool.submit(_play_game, self.config, spec, baseline, candidate): spec
                    for spec in specs
                }
                for future in as_completed(futures):
                    spec = futures[future]
                    try:
                        completed(future.result())
                    except EngineError as exc:
                        raise EngineError(f"game {spec.game_id} failed: {exc}") from exc

        records.sort(key=lambda r: r.game_id)
        return wdl, records

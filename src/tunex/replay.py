"""Replay one 4PC PGN4 game and compare one team's searches to recorded moves.

All four moves are *forced* from the game file, including when the tested
engine disagrees. This preserves the recorded position while allowing the
same engine process to accumulate search history between its turns.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Callable

from .config import Config
from .engine import EngineError, UciEngine
from .match import _position_command, _start_side
from .notation import coordinate_move, san_to_uci


# One four-player round may be written as:
#  1. h2-h3 .. b9-c9 .. g13-g12 .. m8-l8
# or the usual spaced PGN numbering, including 1... for the next seat.
_NUMBER = re.compile(r"^\d+\.(?:\.\.)?$")
_NUMBER_PREFIX = re.compile(r"^\d+\.(?:\.\.)?")
_RESULT = {"1-0", "0-1", "1/2-1/2", "*"}
_TEAM_COLORS = {"RY": {0, 2}, "BG": {1, 3}}
_SEAT_LABELS = ("R", "B", "Y", "G")


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    ply: int
    seat: str
    recorded: str
    recorded_uci: str
    engine_move: str | None
    matched: bool
    score_cp: int | None
    score_mate: int | None


@dataclass(frozen=True, slots=True)
class ReplayReport:
    team: str
    total_plies: int
    compared: int
    matched: int
    mismatched: int
    comparisons: tuple[ReplayComparison, ...]


def parse_game(text: str) -> tuple[str, ...]:
    """Read one game with four moves per round, optional tags and comments.

    A single file is one game, not a line-by-line opening book. Comments in
    braces, semicolon-to-EOL comments and PGN tags are ignored; variations
    are rejected to avoid silently replaying a side line.
    """
    text = re.sub(r"(?m)^\s*#[^\n]*", "", text)
    text = re.sub(r";[^\n]*", "", text)
    text = re.sub(r"\{[^}]*\}", " ", text, flags=re.S)
    text = re.sub(r"(?m)^\s*\[[^\n]*\]\s*$", "", text)
    if "(" in text or ")" in text:
        raise ValueError("PGN variations are not supported; supply the main line only")
    tokens: list[str] = []
    for raw in text.split():
        if raw in {"..", "..."} or _NUMBER.fullmatch(raw):
            continue
        token = _NUMBER_PREFIX.sub("", raw)
        if not token:
            continue
        if token in _RESULT:
            break
        if token.startswith("$") and token[1:].isdigit():
            continue
        tokens.append(token)
    if not tokens:
        raise ValueError("PGN replay file contains no moves")
    return tuple(tokens)


def load_game(path: str | Path) -> tuple[str, ...]:
    return parse_game(Path(path).read_text(encoding="utf-8-sig"))


def replay_game(
    config: Config, game_file: str | Path, team: str,
    *, report_file: str | Path | None = None,
    on_comparison: Callable[[ReplayComparison], None] | None = None,
) -> ReplayReport:
    if config.variant != "four_player_teams":
        raise ValueError("PGN replay is supported for four_player_teams only")
    team = team.upper()
    if team not in _TEAM_COLORS:
        raise ValueError("team must be RY or BG")
    tokens = load_game(game_file)
    options = dict(config.engine.common_options)
    options.update(config.candidate.options)
    comparisons: list[ReplayComparison] = []
    moves: list[str] = []
    with UciEngine(
        config.candidate.path or config.engine.path,
        name=config.candidate.name,
        startup_timeout=config.engine.startup_timeout,
        move_timeout=config.engine.move_timeout,
        strict_options=config.engine.strict_options,
    ) as engine:
        engine.configure(options)
        # Only reset once. Search history and TT survive across engine turns.
        engine.new_game()
        for ply, token in enumerate(tokens, 1):
            position = _position_command(None, moves)
            fen, legal = engine.opening_position(position)
            side = _start_side(fen)
            seat = _SEAT_LABELS[side]
            try:
                recorded = coordinate_move(token)
                if recorded is None:
                    recorded = san_to_uci(token, fen, legal)
                if recorded.lower() not in {move.lower() for move in legal}:
                    raise ValueError(f"recorded move {recorded!r} is not legal")
            except (ValueError, EngineError) as exc:
                raise EngineError(
                    f"PGN ply {ply} ({seat}), {token!r}: {exc}"
                ) from exc

            if side in _TEAM_COLORS[team]:
                search = engine.search(position, config.search.go_command())
                played = search.bestmove.lower()
                match = played == recorded.lower()
                entry = ReplayComparison(
                    ply=ply, seat=seat, recorded=token, recorded_uci=recorded.lower(),
                    engine_move=played, matched=match,
                    score_cp=search.score_cp, score_mate=search.score_mate,
                )
                comparisons.append(entry)
                if on_comparison is not None:
                    on_comparison(entry)
            # Always apply the PGN move, even when the engine disagrees.
            # The next engine search includes the entire forced move history.
            moves.append(recorded.lower())

    matched = sum(entry.matched for entry in comparisons)
    report = ReplayReport(
        team=team, total_plies=len(moves), compared=len(comparisons),
        matched=matched, mismatched=len(comparisons) - matched,
        comparisons=tuple(comparisons),
    )
    if report_file is not None:
        dest = Path(report_file)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    return report

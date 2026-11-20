"""WinBoard / XBoard (CECP v2) engine adapter.

Uses the same search and lifecycle interface as UciEngine. Position synchronization
is incremental so a persistent engine can retain its search history between turns.
"""
from __future__ import annotations

import queue
import re
import time
from typing import Any

from .engine import EngineError, SearchResult, UciEngine


_FEATURE_RE = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')
_OPTION_TYPE_RE = re.compile(
    r"^(.*?) -(?:button|save|reset|check|spin|slider|combo|string|file|path)(?:\s|$)"
)
_THINKING_RE = re.compile(r"^\s*\d+[.+&-]?\s+(-?\d+)\s+\d+\s+\d+(?:\s+(.*))?$")
_MOVE_RE = re.compile(r"^move\s+(\S+)", re.IGNORECASE)


class XBoardEngine(UciEngine):
    """XBoard engine with v2 negotiation, options, and bounded searches."""

    def __init__(self, *args, variant_name: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.variant_name = variant_name
        self.features: dict[str, str] = {}
        self._ping_number = 0
        self._position_base: str | None = None
        self._position_moves: tuple[str, ...] = ()

    def _handshake(self) -> None:
        self.send("xboard")
        self.send("protover 2")
        deadline = time.monotonic() + self.startup_timeout
        received_feature = False

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if received_feature:
                    raise EngineError(f"{self.name}: XBoard feature handshake did not finish")
                break
            try:
                # CECP v1 engines do not emit feature lines. Allow them to
                # work without waiting for the entire startup_timeout.
                line = self._read_line(min(remaining, 1.0 if not received_feature else remaining))
            except EngineError as exc:
                if not received_feature and "timeout waiting for" in str(exc):
                    break
                raise
            if not line.startswith("feature "):
                continue
            received_feature = True
            finished = False
            for match in _FEATURE_RE.finditer(line[len("feature "):]):
                name = match.group(1)
                value = match.group(2) if match.group(2) is not None else match.group(3)
                if name == "option":
                    option = _OPTION_TYPE_RE.match(value)
                    if option:
                        self.options.add(option.group(1))
                else:
                    self.features[name] = value
                self.send(f"accepted {name}")
                if name == "done" and value == "1":
                    finished = True
            if finished:
                return

    def ready(self) -> None:
        if self.features.get("ping") != "1":
            return
        self._ping_number += 1
        expected = f"pong {self._ping_number}"
        self.send(f"ping {self._ping_number}")
        self._read_until(lambda line: line.strip() == expected, self.startup_timeout)

    def set_option(self, name: str, value: Any) -> None:
        if name == "UseNNUE" and value is False and name not in self.options:
            return
        if name == "Hash" and self.features.get("memory") == "1":
            self.send(f"memory {int(value)}")
            return
        if name == "Threads" and self.features.get("smp") == "1":
            self.send(f"cores {int(value)}")
            return
        if name not in self.options:
            if self.strict_options:
                raise EngineError(f"{self.name} does not advertise XBoard option {name!r}")
            return
        if value is None:
            self.send(f"option {name}")
        else:
            if isinstance(value, bool):
                value = int(value)
            self.send(f"option {name}={value}")

    def configure(self, options: dict[str, Any]) -> None:
        for name, value in options.items():
            self.set_option(name, value)
        self.ready()

    def new_game(self) -> None:
        self.send("new")
        if self.variant_name and self.variant_name != "normal":
            advertised = self.features.get("variants", "normal").split(",")
            if self.variant_name not in advertised:
                raise EngineError(
                    f"{self.name} does not advertise XBoard variant {self.variant_name!r}; "
                    "regular XBoard engines cannot play 4-player or Spell Chess"
                )
            self.send(f"variant {self.variant_name}")
        self.send("force")
        self.send("easy")
        self.send("post")
        self._position_base = "startpos"
        self._position_moves = ()
        self.ready()

    @staticmethod
    def _parse_position(position_command: str) -> tuple[str, tuple[str, ...]]:
        if position_command.startswith("position startpos"):
            rest = position_command[len("position startpos"):]
            base = "startpos"
        elif position_command.startswith("position fen "):
            rest = position_command[len("position fen "):]
            fen, delimiter, moves = rest.partition(" moves ")
            if not fen:
                raise EngineError("XBoard position has an empty FEN")
            return f"fen:{fen}", tuple(moves.split()) if delimiter else ()
        else:
            raise EngineError(f"invalid position command for XBoard: {position_command!r}")
        if rest and not rest.startswith(" moves "):
            raise EngineError(f"invalid position moves for XBoard: {position_command!r}")
        return base, tuple(rest[len(" moves "):].split()) if rest else ()

    def _send_move(self, move: str) -> None:
        if self.features.get("usermove") == "1":
            self.send(f"usermove {move}")
        else:
            self.send(move)

    def _set_position(self, position_command: str) -> None:
        base, moves = self._parse_position(position_command)
        # XBoard engines advance their own board on 'move', so include
        # the engine's preceding move in the applied-prefix bookkeeping.
        if base != self._position_base or moves[:len(self._position_moves)] != self._position_moves:
            self.new_game()
            if base.startswith("fen:"):
                if self.features.get("setboard") != "1":
                    raise EngineError(
                        f"{self.name} does not advertise setboard=1; cannot use FEN openings"
                    )
                self.send(f"setboard {base[4:]}")
            self._position_base, self._position_moves = base, ()
        self.send("force")
        for move in moves[len(self._position_moves):]:
            self._send_move(move)
        self._position_base, self._position_moves = base, moves

    def search(self, position_command: str, go_command: str) -> SearchResult:
        self._set_position(position_command)
        command = re.fullmatch(r"go (movetime|depth|nodes) (\d+)", go_command)
        if command is None:
            raise EngineError(f"unsupported Tunex search command: {go_command}")
        mode, value = command.group(1), int(command.group(2))
        if mode == "nodes":
            raise EngineError(
                "XBoard does not standardize a fixed node limit; "
                "use search.mode=movetime or depth"
            )
        if mode == "movetime":
            # The CECP st command is in seconds, not UCI milliseconds.
            self.send(f"st {value / 1000:g}")
        else:
            # sd limits depth; still set a generous per-move time limit.
            self.send(f"st {max(1, int(self.move_timeout))}")
            self.send(f"sd {value}")
        self.send("go")

        deadline = time.monotonic() + self.move_timeout
        score_cp: int | None = None
        score_mate: int | None = None
        pv: tuple[str, ...] = ()
        last_info = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    self.send("?")
                except EngineError:
                    pass
                raise EngineError(f"{self.name} exceeded move timeout")
            line = self._read_line(remaining)
            thinking = _THINKING_RE.match(line)
            if thinking:
                last_info = line
                raw_score = int(thinking.group(1))
                if abs(raw_score) >= 100000:
                    score_mate = (1 if raw_score > 0 else -1) * (abs(raw_score) - 100000)
                    score_cp = None
                else:
                    score_cp = raw_score
                    score_mate = None
                if thinking.group(2):
                    pv = tuple(thinking.group(2).split())
                continue
            move = _MOVE_RE.match(line)
            if move:
                bestmove = move.group(1)
                self._position_moves += (bestmove,)
                self.send("force")
                return SearchResult(bestmove, score_cp, score_mate, pv, last_info)
            if line == "resign":
                self.send("force")
                return SearchResult("resign", score_cp, score_mate, pv, last_info)
            if line.startswith(("1-0", "0-1", "1/2-1/2")):
                self.send("force")
                return SearchResult(line.split()[0], score_cp, score_mate, pv, last_info)
            if line.startswith(("Illegal move:", "Error (")):
                raise EngineError(f"{self.name}: {line}")

    def opening_position(self, position_command: str) -> tuple[str, tuple[str, ...]]:
        raise EngineError("XBoard engines do not expose native IronPhoenix FEN4/legalmoves; "
                          "use coordinate openings or a UCI engine for SAN in 4PC")

    def spell_status(self, position_command: str) -> tuple[str, bool]:
        raise EngineError("XBoard does not expose SpellWarden status; use UCI for Spell Chess")

    def spell_legal_turns(self, position_command: str) -> set[str]:
        raise EngineError("XBoard does not expose SpellWarden legalmoves; use UCI")

    def side_in_check(self, position_command: str) -> bool | None:
        raise EngineError("XBoard has no standard 4-player check-status command")

    def static_eval_cp(self, fen: str) -> int:
        raise EngineError("Texel static-eval tuning requires a UCI engine")

    def diagnostic(self) -> list[str]:
        return []

    def _quit_commands(self) -> str:
        return "force\nquit\n"

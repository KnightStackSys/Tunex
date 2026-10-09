from __future__ import annotations

from dataclasses import dataclass
import queue
import re
import subprocess
import threading
import time
from typing import Any


class EngineError(RuntimeError):
    pass


@dataclass(slots=True)
class SearchResult:
    bestmove: str
    score_cp: int | None = None
    score_mate: int | None = None
    pv: tuple[str, ...] = ()
    info_line: str = ""


_OPTION_RE = re.compile(r"^option name (.+?) type ")
_SCORE_CP_RE = re.compile(r"\bscore cp (-?\d+)")
_SCORE_MATE_RE = re.compile(r"\bscore mate (-?\d+)")
_PV_RE = re.compile(r"\bpv(?:\s+(.+))?$")
_CHECK_RE = re.compile(r"\bcheck:\s*(yes|no)\b", re.IGNORECASE)


class UciEngine:
    def __init__(
        self,
        path: str,
        *,
        name: str,
        startup_timeout: float = 10.0,
        move_timeout: float = 60.0,
        strict_options: bool = True,
    ) -> None:
        self.path = path
        self.name = name
        self.startup_timeout = startup_timeout
        self.move_timeout = move_timeout
        self.strict_options = strict_options
        self.process: subprocess.Popen[str] | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self.options: set[str] = set()

    def __enter__(self) -> "UciEngine":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            return
        try:
            self.process = subprocess.Popen(
                [self.path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise EngineError(f"unable to start {self.name}: {exc}") from exc

        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()
        self.send("uci")
        lines = self._read_until(lambda line: line == "uciok", self.startup_timeout)
        for line in lines:
            match = _OPTION_RE.match(line)
            if match:
                self.options.add(match.group(1))

    def _reader_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for raw in self.process.stdout:
                self._queue.put(raw.rstrip("\r\n"))
        finally:
            self._queue.put(None)

    def _check_running(self) -> None:
        if self.process is None:
            raise EngineError(f"{self.name} has not been started")
        code = self.process.poll()
        if code is not None:
            raise EngineError(f"{self.name} exited unexpectedly with code {code}")

    def send(self, command: str) -> None:
        self._check_running()
        assert self.process is not None and self.process.stdin is not None
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            raise EngineError(f"failed to write to {self.name}: {exc}") from exc

    def _read_line(self, timeout: float) -> str:
        try:
            line = self._queue.get(timeout=max(timeout, 0.001))
        except queue.Empty as exc:
            raise EngineError(f"timeout waiting for {self.name}") from exc
        if line is None:
            self._check_running()
            raise EngineError(f"{self.name} closed its output")
        return line

    def _read_until(self, predicate, timeout: float) -> list[str]:
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EngineError(
                    f"timeout waiting for response from {self.name}; last output: "
                    + (lines[-1] if lines else "<none>")
                )
            line = self._read_line(remaining)
            lines.append(line)
            if predicate(line):
                return lines

    def ready(self) -> None:
        self.send("isready")
        self._read_until(lambda line: line == "readyok", self.startup_timeout)

    def set_option(self, name: str, value: Any) -> None:
        if self.strict_options and self.options and name not in self.options:
            raise EngineError(f"{self.name} does not advertise UCI option {name!r}")
        if value is None:
            self.send(f"setoption name {name}")
        else:
            if isinstance(value, bool):
                value = "true" if value else "false"
            self.send(f"setoption name {name} value {value}")

    def configure(self, options: dict[str, Any]) -> None:
        for name, value in options.items():
            self.set_option(name, value)
        self.ready()

    def new_game(self) -> None:
        self.send("ucinewgame")
        self.ready()

    def search(self, position_command: str, go_command: str) -> SearchResult:
        self.send(position_command)
        self.send(go_command)

        deadline = time.monotonic() + self.move_timeout
        last_info = ""
        score_cp: int | None = None
        score_mate: int | None = None
        pv: tuple[str, ...] = ()

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    self.send("stop")
                except EngineError:
                    pass
                raise EngineError(f"{self.name} exceeded move timeout")

            line = self._read_line(remaining)
            if line.startswith("info "):
                last_info = line
                cp_match = _SCORE_CP_RE.search(line)
                mate_match = _SCORE_MATE_RE.search(line)
                if cp_match:
                    score_cp = int(cp_match.group(1))
                    score_mate = None
                elif mate_match:
                    score_mate = int(mate_match.group(1))
                    score_cp = None
                pv_match = _PV_RE.search(line)
                if pv_match and pv_match.group(1):
                    pv = tuple(pv_match.group(1).split())
            elif line.startswith("bestmove "):
                parts = line.split()
                bestmove = parts[1] if len(parts) >= 2 else "0000"
                return SearchResult(
                    bestmove=bestmove,
                    score_cp=score_cp,
                    score_mate=score_mate,
                    pv=pv,
                    info_line=last_info,
                )

    def side_in_check(self, position_command: str) -> bool | None:
        self.send(position_command)
        self.send("d")
        self.send("isready")
        deadline = time.monotonic() + self.startup_timeout
        check: bool | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EngineError(f"timeout reading board state from {self.name}")
            line = self._read_line(remaining)
            match = _CHECK_RE.search(line)
            if match:
                check = match.group(1).lower() == "yes"
            if line == "readyok":
                return check

    def diagnostic(self) -> list[str]:
        self.send("tuneparams")
        self.send("isready")
        lines = self._read_until(lambda line: line == "readyok", self.startup_timeout)
        return [line for line in lines if line != "readyok"]

    def close(self) -> None:
        proc = self.process
        if proc is None:
            return
        if proc.poll() is None:
            try:
                self.send("quit")
            except EngineError:
                pass
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        self.process = None

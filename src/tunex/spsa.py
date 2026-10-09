from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import random

from .config import Config, SpsaParameter, VariantConfig
from .match import MatchRunner
from .stats import format_summary


def _quantize(value: float, param: SpsaParameter) -> int:
    value = min(max(value, param.minimum), param.maximum)
    steps = round((value - param.minimum) / param.step)
    quantized = param.minimum + steps * param.step
    quantized = min(max(quantized, param.minimum), param.maximum)
    return int(round(quantized))


class SpsaRunner:
    def __init__(self, config: Config) -> None:
        if config.spsa is None:
            raise ValueError("config has no spsa section")
        self.config = config
        self.spsa = config.spsa
        self.match_runner = MatchRunner(config)
        self.rng = random.Random(self.spsa.seed)
        self.theta = {p.name: p.value for p in self.spsa.parameters}
        self.start_iteration = 0
        self._load_state()

    @property
    def state_path(self) -> Path:
        return Path(self.spsa.state_file)

    def _load_state(self) -> None:
        path = self.state_path
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self.start_iteration = int(data.get("iteration", 0))
        stored = data.get("theta", {})
        for param in self.spsa.parameters:
            if param.name in stored:
                self.theta[param.name] = float(stored[param.name])
        rng_state = data.get("rng_state")
        if rng_state:
            def as_tuple(value):
                if isinstance(value, list):
                    return tuple(as_tuple(v) for v in value)
                return value
            self.rng.setstate(as_tuple(rng_state))

    def _save_state(self, iteration: int, history_entry: dict) -> None:
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        history: list[dict] = []
        if path.exists():
            try:
                history = json.loads(path.read_text(encoding="utf-8")).get("history", [])
            except (json.JSONDecodeError, OSError):
                history = []
        history.append(history_entry)
        payload = {
            "iteration": iteration,
            "theta": self.theta,
            "rng_state": self.rng.getstate(),
            "history": history,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def run(self, iterations: int | None = None) -> None:
        target = iterations if iterations is not None else self.spsa.iterations
        params = self.spsa.parameters

        for k in range(self.start_iteration, target):
            ak = self.spsa.a / ((self.spsa.A + k + 1.0) ** self.spsa.alpha)
            ck = self.spsa.c / ((k + 1.0) ** self.spsa.gamma)
            delta = {p.name: self.rng.choice((-1.0, 1.0)) for p in params}

            # Treat baseline.options as fixed tuning context. The perturbed
            # SPSA parameters override only the knobs being optimized.
            plus_options = dict(self.config.baseline.options)
            minus_options = dict(self.config.baseline.options)
            for p in params:
                plus_options[p.name] = _quantize(self.theta[p.name] + ck * delta[p.name], p)
                minus_options[p.name] = _quantize(self.theta[p.name] - ck * delta[p.name], p)

            plus = VariantConfig(name="SPSA+", options=plus_options)
            minus = VariantConfig(name="SPSA-", options=minus_options)

            wdl, _ = self.match_runner.run(
                baseline=minus,
                candidate=plus,
                games=self.spsa.games_per_iteration,
                offset=k * self.spsa.games_per_iteration,
                write_results=True,
            )

            signal = wdl.score - 0.5
            for p in params:
                gradient = signal * delta[p.name] / max(ck, 1e-9)
                self.theta[p.name] = min(
                    max(self.theta[p.name] + ak * gradient, p.minimum),
                    p.maximum,
                )

            printable = {p.name: _quantize(self.theta[p.name], p) for p in params}
            history = {
                "iteration": k + 1,
                "a_k": ak,
                "c_k": ck,
                "wdl": asdict(wdl),
                "plus": plus_options,
                "minus": minus_options,
                "theta": printable,
            }
            self._save_state(k + 1, history)
            print(f"[SPSA {k + 1}/{target}] {format_summary(wdl)}")
            print("  theta:", " ".join(f"{name}={value}" for name, value in printable.items()))

    def current_options(self) -> dict[str, int]:
        return {
            p.name: _quantize(self.theta[p.name], p)
            for p in self.spsa.parameters
        }

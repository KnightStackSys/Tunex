"""Resumable full-covariance CMA-ES tuner using the pycma implementation.

Continuous coordinates are normalized to [0, 1], then quantized to UCI integer
options. Each generation uses the same paired opening schedule for all members,
so candidate fitness is compared against a fixed baseline with lower variance.
The checkpoint is a trusted local pickle: never load checkpoints from strangers.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pickle

import cma

from .config import Config, CmaesConfig, SpsaParameter, VariantConfig
from .match import MatchRunner
from .stats import format_summary


def quantize(value: float, param: SpsaParameter) -> int:
    """Round normalized CMA coordinates onto the allowed integer UCI grid."""
    clamped = max(0.0, min(1.0, float(value)))
    raw = param.minimum + clamped * (param.maximum - param.minimum)
    steps = round((raw - param.minimum) / param.step)
    quantized = param.minimum + steps * param.step
    return int(max(param.minimum, min(param.maximum, quantized)))


def normalized(value: float, param: SpsaParameter) -> float:
    return (value - param.minimum) / (param.maximum - param.minimum)


def signature(config: Config, tune: CmaesConfig) -> str:
    payload = {
        "variant": config.variant,
        "engine": asdict(config.engine),
        "search": asdict(config.search),
        "match": {
            "max_plies": config.match.max_plies,
            "paired": config.match.paired,
            "seed": config.match.seed,
            "openings_file": config.match.openings_file,
        },
        "baseline": asdict(config.baseline),
        "games_per_trial": tune.games_per_trial,
        "sigma": tune.sigma,
        "population_size": tune.population_size,
        "seed": tune.seed,
        "parameters": [asdict(p) for p in tune.parameters],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class CmaesRunner:
    def __init__(self, config: Config) -> None:
        if config.cmaes is None:
            raise ValueError("config has no cmaes section")
        if not config.match.paired:
            raise ValueError("CMA-ES tuning requires paired matches")
        self.config = config
        self.tune = config.cmaes
        self.match_runner = MatchRunner(config)
        self.generation = 0
        self.trials: list[dict] = []
        self.best: dict[str, int] | None = None
        self.best_score = float("-inf")
        self.strategy = cma.CMAEvolutionStrategy(
            [normalized(p.value, p) for p in self.tune.parameters],
            self.tune.sigma,
            {
                "bounds": [0, 1],
                "seed": self.tune.seed,
                "verbose": -9,
                **({"popsize": self.tune.population_size}
                   if self.tune.population_size is not None else {}),
            },
        )
        self._load_state()

    @property
    def state_path(self) -> Path:
        return Path(self.tune.state_file)

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        # Pickle is executable data. Only load checkpoints created locally by Tunex.
        with self.state_path.open("rb") as handle:
            state = pickle.load(handle)  # noqa: S301
        if state.get("version") != 1 or state.get("signature") != signature(self.config, self.tune):
            raise ValueError("CMA-ES checkpoint belongs to another configuration; use a new state_file")
        self.strategy = state["strategy"]
        self.generation = state["generation"]
        self.trials = state["trials"]
        self.best = state["best"]
        self.best_score = state["best_score"]

    def _save_state(self) -> None:
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "signature": signature(self.config, self.tune),
            "strategy": self.strategy,
            "generation": self.generation,
            "trials": self.trials,
            "best": self.best,
            "best_score": self.best_score,
        }
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, path)

    def best_options(self) -> dict[str, int]:
        return dict(self.best) if self.best is not None else {
            p.name: quantize(normalized(p.value, p), p)
            for p in self.tune.parameters
        }

    def run(self, generations: int | None = None) -> None:
        target = self.tune.generations if generations is None else generations
        if target < 1:
            raise ValueError("CMA-ES generations must be positive")
        for generation in range(self.generation, target):
            points = self.strategy.ask()
            objectives: list[float] = []
            for index, point in enumerate(points):
                options = {
                    p.name: quantize(float(x), p)
                    for p, x in zip(self.tune.parameters, point)
                }
                candidate_options = dict(self.config.baseline.options)
                candidate_options.update(options)
                candidate = VariantConfig(
                    name="CMA-ES candidate",
                    options=candidate_options,
                    path=self.config.baseline.path,
                )
                # Same openings for every member of this generation; distinct
                # generations use different opening schedules.
                offset = generation * self.tune.games_per_trial
                wdl, _ = self.match_runner.run(
                    baseline=self.config.baseline,
                    candidate=candidate,
                    games=self.tune.games_per_trial,
                    offset=offset,
                )
                score = (wdl.wins + 0.5 * wdl.draws) / wdl.games
                objectives.append(-score)  # pycma minimizes.
                self.trials.append({
                    "generation": generation + 1,
                    "member": index + 1,
                    "options": options,
                    "score": score,
                    "wdl": asdict(wdl),
                })
                if score > self.best_score:
                    self.best_score = score
                    self.best = dict(options)
                print(
                    f"[CMA-ES {generation + 1}/{target} "
                    f"member {index + 1}/{len(points)}] {format_summary(wdl)}",
                    flush=True,
                )
            # Tell with original continuous points, not quantized values:
            # covariance adaptation needs the actual sampled vectors.
            self.strategy.tell(points, objectives)
            self.generation = generation + 1
            self._save_state()
            print(f"[CMA-ES] generation {self.generation} complete | "
                  f"best score={self.best_score:.4f} | {self.best_options()}", flush=True)

"""Dependency-free Bayesian optimization using a discrete TPE surrogate.

A Tree-structured Parzen Estimator (TPE) fits independent smoothed
one-dimensional densities to good and bad observed configurations, then
samples the good distribution and ranks proposals by log(l(x) / g(x)).
This is a sequential, model-guided optimizer, not Gaussian-process BO.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import random
from typing import Any

from .config import BayesianConfig, Config, SpsaParameter, VariantConfig
from .match import MatchRunner
from .stats import format_summary


def _grid(param: SpsaParameter) -> list[int]:
    low, high, step = int(param.minimum), int(param.maximum), int(param.step)
    return list(range(low, high + 1, step))


def _nearest(value: float, param: SpsaParameter) -> int:
    values = _grid(param)
    return min(values, key=lambda v: (abs(value - v), v))


def _signature(config: Config, tune: BayesianConfig) -> str:
    # Reject incompatible resume attempts rather than silently mixing matches
    # from different engines, time controls, baselines, or parameter spaces.
    payload: dict[str, Any] = {
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
        "startup_trials": tune.startup_trials,
        "candidates": tune.candidates,
        "gamma": tune.gamma,
        "prior_weight": tune.prior_weight,
        "parameters": [asdict(p) for p in tune.parameters],
        "seed": tune.seed,
    }
    return json.dumps(payload, sort_keys=True)


def _density(value: int, observations: list[int], param: SpsaParameter) -> float:
    """Smoothed mixture of Gaussian kernels and a uniform prior on grid."""
    grid = _grid(param)
    spread = max(float(param.step), (param.maximum - param.minimum) * 0.12)
    if not observations:
        return 1.0 / len(grid)
    # Truncate/normalize on the finite grid to avoid boundary bias.
    probabilities = []
    for observed in observations:
        weights = [math.exp(-0.5 * ((g - observed) / spread) ** 2) for g in grid]
        total = sum(weights)
        probabilities.append(weights[grid.index(value)] / total)
    return 0.1 / len(grid) + 0.9 * sum(probabilities) / len(probabilities)


def _draw_good(
    rng: random.Random, observations: list[int], param: SpsaParameter
) -> int:
    grid = _grid(param)
    if not observations:
        return rng.choice(grid)
    observed = rng.choice(observations)
    spread = max(float(param.step), (param.maximum - param.minimum) * 0.12)
    if rng.random() < 0.10:
        return rng.choice(grid)
    return _nearest(rng.gauss(observed, spread), param)


def _choose(
    rng: random.Random,
    history: list[dict[str, Any]],
    config: BayesianConfig,
) -> dict[str, int]:
    params = config.parameters
    previous = {
        tuple(int(trial["options"][p.name]) for p in params)
        for trial in history
    }
    capacity = math.prod(len(_grid(p)) for p in params)
    if len(previous) >= capacity:
        raise ValueError("Bayesian parameter search space is exhausted")

    def new(options: dict[str, int]) -> bool:
        return tuple(options[p.name] for p in params) not in previous

    if not history:
        first = {p.name: _nearest(p.value, p) for p in params}
        if new(first):
            return first

    if len(history) < config.startup_trials:
        for _ in range(1000):
            choice = {p.name: rng.choice(_grid(p)) for p in params}
            if new(choice):
                return choice
    else:
        ranked = sorted(history, key=lambda t: t["objective"], reverse=True)
        elite = max(1, min(len(ranked) - 1, math.ceil(config.gamma * len(ranked))))
        good, bad = ranked[:elite], ranked[elite:]
        best: dict[str, int] | None = None
        best_score = -math.inf
        for _ in range(max(config.candidates * 4, 128)):
            proposal = {
                p.name: _draw_good(
                    rng, [int(t["options"][p.name]) for t in good], p
                )
                for p in params
            }
            if not new(proposal):
                continue
            ratio = 0.0
            for p in params:
                x = proposal[p.name]
                ratio += math.log(_density(
                    x, [int(t["options"][p.name]) for t in good], p
                ))
                ratio -= math.log(_density(
                    x, [int(t["options"][p.name]) for t in bad], p
                ))
            if ratio > best_score:
                best, best_score = proposal, ratio
        if best is not None:
            return best

    # Guaranteed finite-grid fallback; also handles duplicate proposals.
    for _ in range(1000):
        candidate = {p.name: rng.choice(_grid(p)) for p in params}
        if new(candidate):
            return candidate
    # Rare near-exhaustion case: enumerate only until an unused combination.
    import itertools
    for values in itertools.product(*(_grid(p) for p in params)):
        if values not in previous:
            return dict(zip((p.name for p in params), values))
    raise ValueError("Bayesian parameter search space is exhausted")


class BayesianRunner:
    def __init__(self, config: Config) -> None:
        if config.bayesian is None:
            raise ValueError("config has no bayesian section")
        self.config = config
        self.tune = config.bayesian
        if not config.match.paired:
            raise ValueError("Bayesian tuning requires paired matches")
        self.match_runner = MatchRunner(config)
        self.rng = random.Random(self.tune.seed)
        self.trials: list[dict[str, Any]] = []
        self._load_state()

    @property
    def state_path(self) -> Path:
        return Path(self.tune.state_file)

    def _load_state(self) -> None:
        path = self.state_path
        if not path.exists():
            return
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("signature") != _signature(self.config, self.tune):
            raise ValueError(
                f"Bayesian state {path} belongs to different configuration; "
                "use a new state_file for this experiment"
            )
        self.trials = state["trials"]
        self.rng.setstate(_tuple(state["rng_state"]))

    def _save_state(self) -> None:
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "signature": _signature(self.config, self.tune),
            "rng_state": self.rng.getstate(),
            "trials": self.trials,
            "best_options": self.best_options(),
        }
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def best_options(self) -> dict[str, int]:
        if not self.trials:
            return {p.name: _nearest(p.value, p) for p in self.tune.parameters}
        winner = max(self.trials, key=lambda t: t["objective"])
        return dict(winner["options"])

    def run(self, iterations: int | None = None) -> None:
        target = self.tune.iterations if iterations is None else iterations
        if target <= 0:
            raise ValueError("Bayesian iterations must be positive")
        for k in range(len(self.trials), target):
            options = _choose(self.rng, self.trials, self.tune)
            candidate_options = dict(self.config.baseline.options)
            candidate_options.update(options)
            # Match against a *fixed* baseline. The current best isn't
            # a moving target; this preserves comparability of observations.
            candidate = VariantConfig(
                name="Bayesian candidate", options=candidate_options,
                path=self.config.baseline.path,
            )
            wdl, _ = self.match_runner.run(
                baseline=self.config.baseline,
                candidate=candidate,
                games=self.tune.games_per_trial,
                offset=k * self.tune.games_per_trial,
            )
            # Shrink noisy small-sample match scores towards 50% to avoid
            # over-trusting a lucky unbeaten trial. Kept for each observation.
            score = (
                wdl.wins + 0.5 * wdl.draws + 0.5 * self.tune.prior_weight
            ) / (wdl.games + self.tune.prior_weight)
            trial = {
                "iteration": k + 1,
                "options": options,
                "objective": score,
                "wdl": asdict(wdl),
            }
            self.trials.append(trial)
            self._save_state()
            print(f"[Bayesian {k + 1}/{target}] {format_summary(wdl)}")
            print("  evaluated:", options, "| best:", self.best_options())


def _tuple(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tuple(v) for v in value)
    return value

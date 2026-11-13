"""Texel tuning of HCE parameters from outcome-labeled positions.

Uses an engine's true static evaluation rather than search scores. The
objective is mean squared error of sigmoid(K * cp / 400) against 0/0.5/1
outcomes from a fixed team/colour perspective. Integer coordinate descent
is a dependency-free baseline for expensive black-box eval callbacks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import random

from .config import Config, SpsaParameter
from .engine import UciEngine


@dataclass(frozen=True)
class Sample:
    fen: str
    result: float
    game: str


def read_dataset(path: str, variant: str) -> list[Sample]:
    samples: list[Sample] = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
            fen = entry["fen"]
            result = entry["result"]
            game = entry["game_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"dataset line {number}: expected fen/result/game_id JSON") from exc
        if not isinstance(fen, str) or not fen.strip() or not isinstance(game, (str, int)):
            raise ValueError(f"dataset line {number}: invalid fen or game_id")
        if type(result) not in (float, int) or float(result) not in (0, 0.5, 1):
            raise ValueError(f"dataset line {number}: result must be 0, 0.5 or 1")
        parts = fen.split()
        if variant == "four_player_teams":
            if fen[0].upper() not in "RBYG" or fen[1:2] != "-":
                raise ValueError(f"dataset line {number}: expected native FEN4")
        elif len(parts) < 6 or parts[1] not in ("w", "b"):
            raise ValueError(f"dataset line {number}: expected standard 8x8 FEN")
        samples.append(Sample(fen, float(result), str(game)))
    if len(samples) < 2 or len({p.game for p in samples}) < 2:
        raise ValueError("Texel tuning needs positions from at least two distinct games")
    return samples


def split_games(samples: list[Sample], seed: int, fraction: float) -> tuple[list[Sample], list[Sample]]:
    groups = sorted({p.game for p in samples})
    random.Random(seed).shuffle(groups)
    holdout = set(groups[:max(1, min(len(groups) - 1, round(len(groups) * fraction)))])
    return ([p for p in samples if p.game not in holdout],
            [p for p in samples if p.game in holdout])


def prediction(cp: int, k: float) -> float:
    # Avoid overflow in logistic tails.
    z = max(-60., min(60., k * cp / 400.0 * math.log(10)))
    return 1.0 / (1.0 + math.exp(-z))


def sign(fen: str, variant: str) -> int:
    # Static eval from both engines is side-to-move relative.
    # Labels are always from R+Y (4PC) or White (SpellWarden).
    if variant == "four_player_teams":
        return 1 if fen[0].upper() in ("R", "Y") else -1
    return 1 if fen.split()[1] == "w" else -1


def grid_value(value: float, p: SpsaParameter) -> int:
    low, high, step = int(p.minimum), int(p.maximum), int(p.step)
    n = max(0, min((high - low) // step, round((value - low) / step)))
    return low + n * step


def signature(config: Config, digest: str) -> str:
    tune = config.texel
    assert tune is not None
    return json.dumps({
        "engine": asdict(config.engine), "baseline": asdict(config.baseline),
        "variant": config.variant, "texel": asdict(tune), "dataset_sha256": digest,
    }, sort_keys=True)


class TexelRunner:
    def __init__(self, config: Config) -> None:
        if config.texel is None:
            raise ValueError("config has no texel section")
        self.config = config
        self.tune = config.texel
        if config.variant == "spell_chess":
            # SpellWarden's evaluation weights are presently hard-coded and
            # not UCI-tunable. Refuse to pretend these are trainable.
            raise ValueError("SpellWarden has no Eval* UCI HCE options yet")
        data = Path(self.tune.dataset).read_bytes()
        self.digest = hashlib.sha256(data).hexdigest()
        self.training, self.validation = split_games(
            read_dataset(self.tune.dataset, config.variant),
            self.tune.seed, self.tune.validation_fraction,
        )
        self.weights = {p.name: grid_value(p.value, p) for p in self.tune.parameters}
        self.best_weights = dict(self.weights)
        self.best_validation = math.inf
        self.epochs_done = 0
        self._load()

    @property
    def state_path(self) -> Path:
        return Path(self.tune.state_file)

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        doc = json.loads(self.state_path.read_text(encoding="utf-8"))
        if doc.get("signature") != signature(self.config, self.digest):
            raise ValueError("Texel resume state does not match config/dataset")
        self.weights = doc["weights"]
        self.best_weights = doc["best_weights"]
        self.best_validation = float(doc["best_validation"])
        self.epochs_done = int(doc["epochs_done"])

    def _save(self) -> None:
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "signature": signature(self.config, self.digest),
            "weights": self.weights, "best_weights": self.best_weights,
            "best_validation": self.best_validation, "epochs_done": self.epochs_done,
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def run(self, epochs: int | None = None) -> None:
        target = self.tune.epochs if epochs is None else epochs
        if target < 1:
            raise ValueError("Texel epochs must be positive")
        path = self.config.baseline.path or self.config.engine.path
        with UciEngine(
            path, name="Texel HCE", startup_timeout=self.config.engine.startup_timeout,
            move_timeout=self.config.engine.move_timeout,
            strict_options=True,
        ) as engine:
            fixed = dict(self.config.engine.common_options)
            fixed.update(self.config.baseline.options)
            # Never fit an NNUE prediction as though it were HCE.
            fixed["UseNNUE"] = False
            engine.configure(fixed)
            for p in self.tune.parameters:
                if p.name not in engine.options or not p.name.startswith("Eval"):
                    raise ValueError(f"unsupported HCE UCI parameter {p.name!r}")
            engine.new_game()

            def mse(weights: dict[str, int], samples: list[Sample]) -> float:
                engine.configure(weights)
                errors = []
                for sample in samples:
                    cp = engine.static_eval_cp(sample.fen) * sign(
                        sample.fen, self.config.variant
                    )
                    score = prediction(cp, self.tune.k)
                    errors.append((score - sample.result) ** 2)
                return sum(errors) / len(errors)

            for epoch in range(self.epochs_done, target):
                # Coordinate descent with shrinking step radius, bounded to
                # each parameter's grid. Consider both directions; never
                # accept a worsening training objective.
                base_loss = mse(self.weights, self.training)
                for p in self.tune.parameters:
                    current = self.weights[p.name]
                    stride = max(1, round((target - epoch) / max(1, target) * 4))
                    neighbors = {
                        grid_value(current + delta * stride * p.step, p)
                        for delta in (-1, 1)
                    }
                    for value in sorted(neighbors):
                        if value == current:
                            continue
                        candidate = dict(self.weights)
                        candidate[p.name] = value
                        loss = mse(candidate, self.training)
                        if loss < base_loss - 1e-12:
                            self.weights = candidate
                            current = value
                            base_loss = loss
                validation = mse(self.weights, self.validation)
                if validation < self.best_validation:
                    self.best_validation = validation
                    self.best_weights = dict(self.weights)
                self.epochs_done = epoch + 1
                self._save()
                print(
                    f"[Texel {self.epochs_done}/{target}] train MSE {base_loss:.6f} "
                    f"validation MSE {validation:.6f} best {self.best_validation:.6f}",
                    flush=True,
                )
        print("Best validation weights:", self.best_weights)

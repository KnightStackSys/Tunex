from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SearchConfig:
    mode: str = "movetime"
    value: int = 100

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SearchConfig":
        data = data or {}
        mode = str(data.get("mode", "movetime")).lower()
        if mode not in {"movetime", "nodes", "depth"}:
            raise ValueError("search.mode must be movetime, nodes, or depth")
        value = int(data.get("value", 100))
        if value <= 0:
            raise ValueError("search.value must be > 0")
        return cls(mode=mode, value=value)

    def go_command(self) -> str:
        return f"go {self.mode} {self.value}"


@dataclass(slots=True)
class EngineConfig:
    path: str
    common_options: dict[str, Any] = field(default_factory=dict)
    startup_timeout: float = 10.0
    move_timeout: float = 60.0
    strict_options: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineConfig":
        if "path" not in data:
            raise ValueError("engine.path is required")
        return cls(
            path=str(data["path"]),
            common_options=dict(data.get("common_options", {})),
            startup_timeout=float(data.get("startup_timeout", 10.0)),
            move_timeout=float(data.get("move_timeout", 60.0)),
            strict_options=bool(data.get("strict_options", True)),
        )


@dataclass(slots=True)
class VariantConfig:
    name: str
    options: dict[str, Any] = field(default_factory=dict)
    # Optional executable override. Default remains engine.path for existing
    # SPRT, fixed-match and SPSA configurations.
    path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_name: str) -> "VariantConfig":
        path = data.get("path")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            raise ValueError(f"{default_name}.path must be a nonempty string")
        return cls(
            name=str(data.get("name", default_name)),
            options=dict(data.get("options", {})),
            path=path,
        )


@dataclass(slots=True)
class MatchConfig:
    games: int = 100
    workers: int = 1
    max_plies: int = 320
    paired: bool = True
    seed: int = 20261009
    openings_file: str | None = None
    results_file: str = "results/games.jsonl"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MatchConfig":
        data = data or {}
        cfg = cls(
            games=int(data.get("games", 100)),
            workers=int(data.get("workers", 1)),
            max_plies=int(data.get("max_plies", 320)),
            paired=bool(data.get("paired", True)),
            seed=int(data.get("seed", 20261009)),
            openings_file=data.get("openings_file"),
            results_file=str(data.get("results_file", "results/games.jsonl")),
        )
        if cfg.games <= 0:
            raise ValueError("match.games must be > 0")
        if cfg.workers <= 0:
            raise ValueError("match.workers must be > 0")
        if cfg.max_plies <= 0:
            raise ValueError("match.max_plies must be > 0")
        if cfg.paired and cfg.games % 2:
            raise ValueError("match.games must be even when match.paired is true")
        return cfg


@dataclass(slots=True)
class SprtConfig:
    elo0: float = 0.0
    elo1: float = 5.0
    alpha: float = 0.05
    beta: float = 0.05
    max_games: int = 10000
    batch_games: int = 20

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SprtConfig":
        data = data or {}
        cfg = cls(
            elo0=float(data.get("elo0", 0.0)),
            elo1=float(data.get("elo1", 5.0)),
            alpha=float(data.get("alpha", 0.05)),
            beta=float(data.get("beta", 0.05)),
            max_games=int(data.get("max_games", 10000)),
            batch_games=int(data.get("batch_games", 20)),
        )
        if not 0.0 < cfg.alpha < 1.0 or not 0.0 < cfg.beta < 1.0:
            raise ValueError("SPRT alpha/beta must be in (0, 1)")
        if cfg.max_games <= 0 or cfg.batch_games <= 0:
            raise ValueError("SPRT max_games and batch_games must be > 0")
        if cfg.batch_games % 2:
            raise ValueError("sprt.batch_games should be even for paired 4PC testing")
        if cfg.elo1 <= cfg.elo0:
            raise ValueError("sprt.elo1 must be greater than sprt.elo0")
        return cfg


@dataclass(slots=True)
class SpsaParameter:
    name: str
    value: float
    minimum: float
    maximum: float
    step: float = 1.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpsaParameter":
        p = cls(
            name=str(data["name"]),
            value=float(data["value"]),
            minimum=float(data["min"]),
            maximum=float(data["max"]),
            step=float(data.get("step", 1.0)),
        )
        if p.minimum > p.maximum:
            raise ValueError(f"{p.name}: min must be <= max")
        if p.step <= 0:
            raise ValueError(f"{p.name}: step must be > 0")
        if not p.minimum <= p.value <= p.maximum:
            raise ValueError(f"{p.name}: initial value outside bounds")
        return p


@dataclass(slots=True)
class SpsaConfig:
    iterations: int = 100
    games_per_iteration: int = 20
    a: float = 2.0
    c: float = 2.0
    alpha: float = 0.602
    gamma: float = 0.101
    A: float = 10.0
    seed: int = 20261009
    state_file: str = "results/spsa_state.json"
    parameters: list[SpsaParameter] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SpsaConfig":
        data = data or {}
        params = [SpsaParameter.from_dict(p) for p in data.get("parameters", [])]
        cfg = cls(
            iterations=int(data.get("iterations", 100)),
            games_per_iteration=int(data.get("games_per_iteration", 20)),
            a=float(data.get("a", 2.0)),
            c=float(data.get("c", 2.0)),
            alpha=float(data.get("alpha", 0.602)),
            gamma=float(data.get("gamma", 0.101)),
            A=float(data.get("A", 10.0)),
            seed=int(data.get("seed", 20261009)),
            state_file=str(data.get("state_file", "results/spsa_state.json")),
            parameters=params,
        )
        if cfg.iterations <= 0 or cfg.games_per_iteration <= 0:
            raise ValueError("SPSA iterations/games_per_iteration must be > 0")
        if cfg.games_per_iteration % 2:
            raise ValueError("spsa.games_per_iteration should be even for paired testing")
        if cfg.a <= 0 or cfg.c <= 0:
            raise ValueError("SPSA a and c must be > 0")
        if not cfg.parameters:
            raise ValueError("spsa.parameters must contain at least one parameter")
        return cfg


@dataclass(slots=True)
class BayesianConfig:
    """Sequential TPE-style Bayesian optimization of integer UCI options."""

    iterations: int = 100
    games_per_trial: int = 40
    startup_trials: int = 12
    candidates: int = 64
    gamma: float = 0.25
    prior_weight: float = 4.0
    seed: int = 20261113
    state_file: str = "results/bayesian_state.json"
    parameters: list[SpsaParameter] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BayesianConfig":
        cfg = cls(
            iterations=int(data.get("iterations", 100)),
            games_per_trial=int(data.get("games_per_trial", 40)),
            startup_trials=int(data.get("startup_trials", 12)),
            candidates=int(data.get("candidates", 64)),
            gamma=float(data.get("gamma", 0.25)),
            prior_weight=float(data.get("prior_weight", 4.0)),
            seed=int(data.get("seed", 20261113)),
            state_file=str(data.get("state_file", "results/bayesian_state.json")),
            parameters=[SpsaParameter.from_dict(p) for p in data.get("parameters", [])],
        )
        if cfg.iterations <= 0 or cfg.games_per_trial <= 0:
            raise ValueError("bayesian iterations and games_per_trial must be positive")
        if cfg.games_per_trial % 2:
            raise ValueError("bayesian games_per_trial must be even for paired testing")
        if cfg.startup_trials < 2 or cfg.candidates <= 0:
            raise ValueError("bayesian startup_trials must be >= 2 and candidates > 0")
        if not 0 < cfg.gamma <= 0.5:
            raise ValueError("bayesian gamma must be in (0, 0.5]")
        if cfg.prior_weight < 0:
            raise ValueError("bayesian prior_weight must be >= 0")
        if not cfg.state_file.strip():
            raise ValueError("bayesian state_file cannot be empty")
        if not cfg.parameters:
            raise ValueError("bayesian parameters must not be empty")
        if len({p.name for p in cfg.parameters}) != len(cfg.parameters):
            raise ValueError("bayesian parameter names must be unique")
        for p in cfg.parameters:
            if p.minimum == p.maximum:
                raise ValueError(f"{p.name}: Bayesian bounds must allow variation")
            if any(abs(x - round(x)) > 1e-9 for x in
                   (p.minimum, p.maximum, p.value, p.step)):
                raise ValueError(f"{p.name}: Bayesian parameters must be integer-valued")
            if p.step < 1:
                raise ValueError(f"{p.name}: Bayesian step must be >= 1")
        return cfg


@dataclass(slots=True)
class TexelConfig:
    """Labeled-position Texel fitting via bounded integer coordinate descent."""

    dataset: str
    parameters: list[SpsaParameter]
    epochs: int = 5
    k: float = 1.0
    validation_fraction: float = 0.2
    seed: int = 20261113
    state_file: str = "results/texel_state.json"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TexelConfig":
        params = [SpsaParameter.from_dict(p) for p in data.get("parameters", [])]
        cfg = cls(
            dataset=str(data.get("dataset", "")),
            parameters=params,
            epochs=int(data.get("epochs", 5)),
            k=float(data.get("k", 1.0)),
            validation_fraction=float(data.get("validation_fraction", 0.2)),
            seed=int(data.get("seed", 20261113)),
            state_file=str(data.get("state_file", "results/texel_state.json")),
        )
        if not cfg.dataset.strip() or not params:
            raise ValueError("texel dataset and parameters are required")
        if cfg.epochs < 1 or not 0 < cfg.k <= 10:
            raise ValueError("texel epochs must be positive and k in (0, 10]")
        if not 0 < cfg.validation_fraction < 0.5:
            raise ValueError("texel validation_fraction must be in (0, 0.5)")
        if len({p.name for p in params}) != len(params):
            raise ValueError("texel parameter names must be unique")
        for p in params:
            if any(abs(v - round(v)) > 1e-9 for v in
                   (p.minimum, p.maximum, p.value, p.step)):
                raise ValueError(f"{p.name}: texel requires integer-valued parameters")
            if p.minimum == p.maximum or p.step < 1:
                raise ValueError(f"{p.name}: texel bounds must vary with step >= 1")
        return cfg


@dataclass(slots=True)
class Config:
    engine: EngineConfig
    search: SearchConfig
    match: MatchConfig
    baseline: VariantConfig
    candidate: VariantConfig
    sprt: SprtConfig
    spsa: SpsaConfig | None = None
    bayesian: BayesianConfig | None = None
    texel: TexelConfig | None = None
    variant: str = "four_player_teams"

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        config_path = Path(path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        variant = str(data.get("variant", "four_player_teams")).lower()
        if variant not in {"four_player_teams", "spell_chess"}:
            raise ValueError("variant must be four_player_teams or spell_chess")
        if "texel" in data and data["texel"].get("dataset"):
            dataset = Path(data["texel"]["dataset"])
            if not dataset.is_absolute():
                data["texel"]["dataset"] = str((config_path.parent / dataset).resolve())
        engine = EngineConfig.from_dict(data.get("engine", {}))
        match = MatchConfig.from_dict(data.get("match"))
        openings = match.openings_file
        if openings and not Path(openings).is_absolute():
            match.openings_file = str((config_path.parent / openings).resolve())
        if not Path(engine.path).is_absolute():
            engine.path = str((config_path.parent / engine.path).resolve())
        baseline = VariantConfig.from_dict(data.get("baseline", {}), "baseline")
        candidate = VariantConfig.from_dict(data.get("candidate", {}), "candidate")
        for engine_variant in (baseline, candidate):
            if engine_variant.path is not None and not Path(engine_variant.path).is_absolute():
                engine_variant.path = str((config_path.parent / engine_variant.path).resolve())
        return cls(
            engine=engine,
            search=SearchConfig.from_dict(data.get("search")),
            match=match,
            baseline=baseline,
            candidate=candidate,
            sprt=SprtConfig.from_dict(data.get("sprt")),
            spsa=SpsaConfig.from_dict(data["spsa"]) if "spsa" in data else None,
            bayesian=BayesianConfig.from_dict(data["bayesian"])
            if "bayesian" in data else None,
            texel=TexelConfig.from_dict(data["texel"]) if "texel" in data else None,
            variant=variant,
        )

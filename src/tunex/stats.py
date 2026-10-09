from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(slots=True)
class WDL:
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def points(self) -> float:
        return self.wins + 0.5 * self.draws

    @property
    def score(self) -> float:
        return self.points / self.games if self.games else 0.5

    def add(self, result: float) -> None:
        if result == 1.0:
            self.wins += 1
        elif result == 0.5:
            self.draws += 1
        elif result == 0.0:
            self.losses += 1
        else:
            raise ValueError("result must be 1.0, 0.5, or 0.0")

    def extend(self, other: "WDL") -> None:
        self.wins += other.wins
        self.draws += other.draws
        self.losses += other.losses


def score_to_elo(score: float) -> float:
    if score <= 0.0:
        return float("-inf")
    if score >= 1.0:
        return float("inf")
    return 400.0 * math.log10(score / (1.0 - score))


def elo(wdl: WDL) -> float:
    return score_to_elo(wdl.score)


def score_standard_error(wdl: WDL) -> float:
    n = wdl.games
    if n < 2:
        return float("inf")
    mean = wdl.score
    sum_sq = wdl.wins * (1.0 - mean) ** 2
    sum_sq += wdl.draws * (0.5 - mean) ** 2
    sum_sq += wdl.losses * (0.0 - mean) ** 2
    sample_var = sum_sq / (n - 1)
    return math.sqrt(sample_var / n)


def elo_confidence_interval(wdl: WDL, z: float = 1.959963984540054) -> tuple[float, float]:
    if wdl.games < 2:
        return (float("-inf"), float("inf"))
    se = score_standard_error(wdl)
    lo_score = max(1e-9, wdl.score - z * se)
    hi_score = min(1.0 - 1e-9, wdl.score + z * se)
    return score_to_elo(lo_score), score_to_elo(hi_score)


def format_summary(wdl: WDL) -> str:
    estimate = elo(wdl)
    lo, hi = elo_confidence_interval(wdl)
    return (
        f"{wdl.games} games  W/D/L {wdl.wins}/{wdl.draws}/{wdl.losses}  "
        f"score {wdl.score * 100:.2f}%  Elo {estimate:+.2f}  "
        f"95% CI [{lo:+.2f}, {hi:+.2f}]"
    )

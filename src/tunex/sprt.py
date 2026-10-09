from __future__ import annotations

from dataclasses import dataclass
import math

from .stats import WDL


def elo_to_score(elo: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-elo / 400.0))


@dataclass(slots=True)
class SprtState:
    elo0: float
    elo1: float
    alpha: float
    beta: float
    llr: float = 0.0

    @property
    def lower_bound(self) -> float:
        return math.log(self.beta / (1.0 - self.alpha))

    @property
    def upper_bound(self) -> float:
        return math.log((1.0 - self.beta) / self.alpha)

    @property
    def decision(self) -> str:
        if self.llr >= self.upper_bound:
            return "H1"
        if self.llr <= self.lower_bound:
            return "H0"
        return "continue"

    def update(self, wdl: WDL) -> None:
        p0 = min(max(elo_to_score(self.elo0), 1e-12), 1.0 - 1e-12)
        p1 = min(max(elo_to_score(self.elo1), 1e-12), 1.0 - 1e-12)

        # Logistic-score SPRT. Draws are represented as half a win and half a
        # loss. This is intentionally simple and stable for engine-development
        # testing; it is not a pentanomial paired-game model.
        self.llr = (
            wdl.wins * math.log(p1 / p0)
            + wdl.losses * math.log((1.0 - p1) / (1.0 - p0))
            + 0.5
            * wdl.draws
            * (
                math.log(p1 / p0)
                + math.log((1.0 - p1) / (1.0 - p0))
            )
        )

    def summary(self) -> str:
        return (
            f"LLR {self.llr:+.4f}  bounds "
            f"[{self.lower_bound:+.4f}, {self.upper_bound:+.4f}]  "
            f"decision {self.decision}"
        )

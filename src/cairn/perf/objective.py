"""What `cairn tune` chooses by when it prices a function at several sizes: one objective over their times.

A search is given sizes (`--at n=1e6 --at n=1e8`, or a shapes file that also weighs each) and prices every candidate
at each of them. The objective folds those times into the one number that ranks the candidates:

- `geomean`, the default: the geometric mean, weighted when weights are given, the way GPU MODE's leaderboards score
  a kernel over their list of shapes. A candidate twice as fast at one size and twice as slow at another ties.
- `mean`: the weighted arithmetic mean, which the slowest size dominates: the time of one call at each size.

Each candidate keeps its time at every size beside its objective, so one that is fastest at a size and loses the
objective is shown as such (`fastest`). Measured times are folded by the same objective. It is CAIRN's own number
over its own model or its own runs, never a leaderboard's score, which the leaderboard computes on its machine.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

KINDS = {"geomean": "the geometric mean", "mean": "the arithmetic mean"}


def where(sizes: dict[str, float]) -> str:
    """`n=1e+06, m=64`: one set of sizes as a person reads it."""
    return ", ".join(f"{k}={v:g}" for k, v in sizes.items())


@dataclass(frozen=True)
class Objective:
    """The sizes a search prices each candidate at, the weight of each, and how their times fold into one."""

    sizes: tuple[dict[str, float], ...]
    weights: tuple[float, ...]
    kind: str = "geomean"

    def __post_init__(self) -> None:
        if not self.sizes:
            raise ValueError("Give the sizes to tune for with --at, such as --at n=1e7, or with --shapes.")
        if self.kind not in KINDS:
            raise ValueError(f"The objective is one of {', '.join(KINDS)}, not {self.kind!r}.")
        if len(self.weights) != len(self.sizes) or not all(0 < w < math.inf for w in self.weights):
            raise ValueError("Each size takes one weight, a positive number.")

    @classmethod
    def over(cls, sizes: Sequence[dict[str, float]], weights: Sequence[float] | None = None,
             kind: str = "geomean") -> Objective:  # fmt: skip
        return cls(tuple(sizes), tuple(weights) if weights else (1.0,) * len(sizes), kind)

    def value(self, times: Sequence[float]) -> float:
        """The objective of one candidate's times, one at each size in order."""
        total = sum(self.weights)
        if self.kind == "mean":
            return sum(w * t for w, t in zip(self.weights, times, strict=True)) / total
        if min(times) <= 0:
            return 0.0
        return math.exp(sum(w * math.log(t) for w, t in zip(self.weights, times, strict=True)) / total)

    def said(self, times: str = "") -> str:
        """The objective as words: what folds which times, weighted how; `times` says whose (measured, predicted)."""
        whose = f"{times} " if times else ""
        if len(self.sizes) == 1:
            return f"the {whose}time at {where(self.sizes[0])}"
        even = len(set(self.weights)) == 1
        weighed = "" if even else f", weighted {', '.join(f'{w:g}' for w in self.weights)}"
        return f"{KINDS[self.kind]} of the {whose}times at {'; '.join(where(s) for s in self.sizes)}{weighed}"

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "sizes": list(self.sizes), "weights": list(self.weights), "text": self.said()}


def shapes(data: Any) -> tuple[list[dict[str, float]], list[float]]:
    """The sizes and weights a shapes file lists: `[{"at": "n=1e6", "weight": 2}, {"at": {"n": 1e8}}]`, each entry
    one set of sizes, written as `--at` writes it or as an object, and a weight of 1 unless it says another."""
    from .report import parse_sizes

    if not isinstance(data, list) or not data:
        raise ValueError('A shapes file is a nonempty JSON list of {"at": "n=1e6", "weight": 1}.')
    sizes: list[dict[str, float]] = []
    weights: list[float] = []
    for entry in data:
        extra = set(entry) - {"at", "weight"} if isinstance(entry, dict) else {"at"}
        at = entry.get("at") if isinstance(entry, dict) else None
        if extra or not isinstance(at, (str, dict)) or not at:
            raise ValueError(f'A shape is {{"at": "n=1e6", "weight": 1}}, not {entry!r}.')
        if isinstance(at, str):
            sizes += parse_sizes([at])
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in at.values()):
            sizes.append({str(k): float(v) for k, v in at.items()})
        else:
            raise ValueError(f"The sizes of a shape are numbers: {at!r}.")
        weight = entry.get("weight", 1)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < weight < math.inf:
            raise ValueError(f"A shape's weight is a positive number, not {weight!r}.")
        weights.append(float(weight))
    return sizes, weights


def fastest(times: Mapping[Any, Sequence[float]]) -> list[Any]:
    """The key of the fastest candidate at each size, of `times` by key, one time per size; the first on a tie."""

    def best(i: int) -> Any:
        return min(times, key=lambda k: times[k][i])

    return [best(i) for i in range(len(next(iter(times.values()))) if times else 0)]

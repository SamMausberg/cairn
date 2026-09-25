"""What `guard_counts.py` and `guard_delta.py` count: the programs of a checkout they compile, and the kind of each guard
call in the C++ those programs emit. It imports nothing from the package, so `guard_delta.py` can use it beside a
compiler from another tree.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from sources import cairn_sources

GUARDS = {
    "bounds": r"\bcr::at\(",
    "overflow": r"\bcr::(?:add|sub|mul)<",
    "conversion": r"\bcr::(?:convert|truncate)<",
    "division": r"\bcr::(?:divide|remainder)<",
    "part": r"\bcr::part\(",
    "entry": r"\bcr::(?:view|disjoint)\(",
}


def counts(cpp: str) -> dict[str, int]:
    """The guard calls in `cpp`, by kind."""
    return {kind: len(re.findall(pattern, cpp)) for kind, pattern in GUARDS.items()}


def corpus(root: Path) -> list[tuple[str, Path]]:
    """Every example project's manifest, each single-file example in examples/basics and each preregistered bench
    kernel of the checkout at `root`, sorted by path, each under the name a record gives it."""
    paths = [*root.glob("examples/**/cairn.toml"), *cairn_sources(root / "examples/basics"),
             *root.glob("bench/suite/kernels/*/kernel.cairn")]  # fmt: skip
    return [(str((p.parent if p.name == "cairn.toml" else p).relative_to(root)), p) for p in sorted(paths)]

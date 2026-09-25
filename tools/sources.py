"""The CAIRN source files under a folder, for every tool and test that walks a checkout for them.

It imports nothing from the package, so `bench/codegen/guard_delta.py` can use it beside a compiler from another tree.
"""

from __future__ import annotations

import os
from pathlib import Path

# A walk never enters these, nor any folder whose name starts with a dot: that is tool state, such as the .cairn
# directory `cairn tune` and `cairn mcp` keep beside a program (its name matches *.cairn), .claude, .git and .venv.
GENERATED = frozenset({"build", "dist", "results"})


def cairn_sources(root: Path) -> list[Path]:
    """Every .cairn file under `root`, sorted, outside hidden folders and generated output."""
    found = []
    for folder, subfolders, names in os.walk(root):
        subfolders[:] = [d for d in subfolders if not d.startswith(".") and d not in GENERATED]
        found += [Path(folder, name) for name in names if Path(name).suffix == ".cairn"]
    return sorted(found)

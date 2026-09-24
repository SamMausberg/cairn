#!/usr/bin/env python3
"""Make the repository's labels on GitHub the ones `.github/labels.yml` names, with the colour and description it
gives each: `gh label create NAME --color C --description D --force` creates a label or updates one that exists.

    python3 tools/release/sync_labels.py --dry-run               # print the gh commands; run nothing
    python3 tools/release/sync_labels.py --repo OWNER/NAME       # the owner applies them

Only the owner runs it for real. It refuses to call gh under a test or in CI, and it never deletes a label: one on
GitHub that the file does not name is left for the owner to remove by hand.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yaml_subset import YamlError, read

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / ".github/labels.yml"
PREFIXES = ("kind:", "area:", "backend:", "needs:")  # and `good first issue`, the one name GitHub itself suggests
COLOR = re.compile(r"[0-9a-f]{6}")
GUARDS = ("CI", "GITHUB_ACTIONS", "PYTEST_CURRENT_TEST")  # where a run would reach GitHub unasked


def labels(path: Path = LABELS) -> list[dict[str, str]]:
    """The labels the file names, each held to what GitHub accepts; ValueError naming the first that is not."""
    rows = read(path)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path.name} is a list of labels")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"name", "color", "description"}:
            raise ValueError(f"each label has exactly a name, a color and a description: {row}")
        name, color, description = row["name"], row["color"], row["description"]
        if not all(isinstance(v, str) for v in row.values()):
            raise ValueError(f"{name}: every field is a string")
        if not (name.startswith(PREFIXES) or name == "good first issue") or len(name) > 50 or name in seen:
            raise ValueError(f"{name}: a label is one of {', '.join(PREFIXES)} or good first issue, named once")
        if not COLOR.fullmatch(color) or not 0 < len(description) <= 100:
            raise ValueError(f"{name}: six lower-case hex digits of colour and a description of 1 to 100 characters")
        seen.add(name)
    return rows


def commands(rows: list[dict[str, str]], repo: str | None = None) -> list[list[str]]:
    where = ["--repo", repo] if repo else []
    return [["gh", "label", "create", r["name"], "--color", r["color"], "--description", r["description"], "--force",
             *where] for r in rows]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the gh commands and run none of them")
    parser.add_argument("--repo", help="OWNER/NAME; gh's own default repository when left out")
    parser.add_argument("--labels", type=Path, default=LABELS, help="the labels file (default .github/labels.yml)")
    a = parser.parse_args(argv)
    try:
        planned = commands(labels(a.labels), a.repo)
    except (YamlError, ValueError, OSError) as error:
        print(f"{a.labels}: {error}", file=sys.stderr)
        return 1
    if a.dry_run:
        for command in planned:
            print(shlex.join(command))
        print(f"# {len(planned)} labels; nothing was run", file=sys.stderr)
        return 0
    if guarded := [name for name in GUARDS if os.environ.get(name)]:
        print(f"refused: {guarded[0]} is set, and labels are synced only by the owner at a terminal", file=sys.stderr)
        return 2
    if shutil.which("gh") is None:
        print("refused: gh is not on PATH", file=sys.stderr)
        return 2
    for command in planned:
        print(shlex.join(command))
        subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

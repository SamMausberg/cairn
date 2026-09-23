"""What `cairn new` writes: the default project the guide walks through, or a copy of a packaged template, each
with the AGENTS.md every new project tells an agent. Creation never overwrites a directory."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .project import ProjectError

TEMPLATES = Path(__file__).parents[1] / "templates"  # each a whole project the suite builds, runs and tests as it is
GUIDE = TEMPLATES / "AGENTS.md"  # what every new project tells an agent: the loop, the rules, what not to widen


def guide(destination: Path) -> None:
    """AGENTS.md for any agent, and a CLAUDE.md that imports it, since Claude Code reads that file instead."""
    (destination / "AGENTS.md").write_text(GUIDE.read_text(encoding="utf-8"), encoding="utf-8")
    (destination / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")


def templates() -> list[str]:
    return ["default", *sorted(p.name for p in TEMPLATES.iterdir() if (p / "cairn.toml").is_file())]


def create_project(destination: Path, template: str = "default") -> dict:
    """Create only; never overwrite a directory, even when it is empty. `default` is the average project the guide
    walks through; any other template is copied from `templates/`, under the new project's name."""
    name = destination.name
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
        raise ProjectError("Choose an ASCII project name of 1..64 characters.")
    if template not in templates():
        raise ProjectError(f"No template {template!r}; there are {', '.join(templates())}.")
    if template != "default":
        destination.mkdir(parents=True, exist_ok=False)
        source = TEMPLATES / template
        for path in sorted(p for p in source.rglob("*") if p.is_file() and "build" not in p.relative_to(source).parts):
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            text = path.read_text(encoding="utf-8")
            if path.name == "cairn.toml":
                text = text.replace(f'name = "{template}"', f'name = "{name}"', 1)
            target.write_text(text, encoding="utf-8")
        (destination / ".gitignore").write_text("build/\n")
        guide(destination)
        return {
            "status": "created",
            "project": str(destination.resolve()),
            "template": template,
            "network_access": False,
        }
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "src").mkdir()
    (destination / "tests").mkdir()
    (destination / "cairn.toml").write_text(f'''[project]
name = "{name}"
sources = ["src/math.cairn", "src/main.cairn"]
tests = ["tests/average.json"]

[build]
kind = "exe"
arch = "baseline"
''')
    (destination / "src/math.cairn").write_text("""// Floor average without overflowing the intermediate sum.
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);

test average {
  assert_eq(average(10, 20), 15);
  assert_eq(average(1, 2), 1, "rounds down");
}
""")
    (destination / "src/main.cairn").write_text("""// Prints the average it checks, and exits 0 only when it is right.
fn main() -> i32 {
  let mean = average(10, 20);
  println("average(10, 20) = ", mean);
  if mean != 15 { return 1; }
  return 0;
}
""")
    values = [0, 1, 2, 255, 256, 2**63 - 1, 2**63, 2**64 - 2, 2**64 - 1]
    contract = {
        "schema": "cairn.task/1",
        "symbol": "average",
        "cases": [{"args": {"x": x, "y": y}, "return": (x + y) // 2} for x in values for y in values],
    }
    (destination / "tests/average.json").write_text(json.dumps(contract, indent=2) + "\n")
    (destination / ".gitignore").write_text("build/\n")
    guide(destination)
    return {"status": "created", "project": str(destination.resolve()), "network_access": False}

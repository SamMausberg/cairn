"""What a program drew, for a person or an agent to look at: `cairn shot` and the edit host's shot request.

The program is built and run headless with CAIRN_SHOT naming a fresh directory, and every frame std.draw.capture
wrote there comes back as the path of its PNG, its layout record of named rectangles and the time since the frame
before, beside the effect rows of the functions asked about and what changed in them against another version.
Nothing opens a window and nothing touches a device. The run is the same process `cairn run` makes, with its
limits, and a child that exits abnormally is reported with whatever frames it wrote first.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..compiler.cairnc import compile_source
from ..projects.build import build
from ..projects.project import Project, ProjectError, load_project
from ..verify.testing import limited

FRAME = re.compile(r"frame-(\d{1,9})\.png")
LIMIT = 256  # frames collected from one run


def rows(source: str, names: list[str]) -> dict[str, list[str]]:
    """The effect row of each named function, as the checker infers it for the whole program."""
    functions = compile_source(source)[1]["functions"]
    missing = [n for n in names if n not in functions]
    if missing:
        raise ProjectError(f"No function {missing[0]} to report; name one the program defines.")
    return {n: functions[n]["effects"] for n in names}


def changed(before: dict[str, list[str]], after: dict[str, list[str]]) -> dict[str, dict[str, list[str]]]:
    """What each row gained and lost, for the rows that differ."""
    out = {}
    for name, row in after.items():
        added, removed = sorted(set(row) - set(before[name])), sorted(set(before[name]) - set(row))
        if added or removed:
            out[name] = {"added": added, "removed": removed}
    return out


def frames(directory: Path) -> list[dict[str, Any]]:
    """Every frame in `directory`, in frame order, each with its layout record and the time since the one before."""
    taken = sorted((int(m.group(1)), p) for p in directory.iterdir() if (m := FRAME.fullmatch(p.name)))
    out, previous = [], None
    for k, png in taken[:LIMIT]:
        record: dict[str, Any] = {"frame": k, "png": str(png)}
        meta = png.with_suffix(".json")
        if meta.is_file():
            layout = json.loads(meta.read_text(encoding="utf-8"))
            record["layout"] = layout
            if previous is not None:
                record["since_previous_ns"] = layout["at_ns"] - previous
            previous = layout["at_ns"]
        out.append(record)
    return out


def shot(project: Project, functions: list[str] | tuple[str, ...] = (), since: str | None = None, *,
         cxx: str = "clang++", timeout: int = 60, memory_mib: int = 1024) -> dict[str, Any]:  # fmt: skip
    """Build `project` as an executable, run it once headless, and collect what it captured."""
    names = list(dict.fromkeys(functions))
    now = rows(project.source, names)
    record = build(project, cxx=cxx, kind="exe", timeout=timeout)
    if record["status"] != "native-built":
        return {"schema": "cairn.shot/1", **{k: record[k] for k in ("status", "stderr") if k in record}}
    directory = Path(tempfile.mkdtemp(prefix="shot-", dir=record["directory"]))

    env = {**os.environ, "CAIRN_SHOT": str(directory)}
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, errors="backslashreplace",
                          timeout=timeout, env=env, preexec_fn=lambda: limited(timeout, memory_mib), stdin=subprocess.DEVNULL)  # fmt: skip
    result: dict[str, Any] = {
        "schema": "cairn.shot/1",
        "status": "shot" if done.returncode == 0 else "program-failed",
        "exit_code": done.returncode,
        "stdout": done.stdout[:16000],
        "stderr": done.stderr[:16000],
        "directory": str(directory),
        "frames": frames(directory),
        "effects": now,
        "security_sandbox": False,
    }
    if since is not None:
        result["changed"] = changed(rows(since, names), now)
    return result


def lines(result: dict[str, Any]) -> str:
    """The shot for a person: one line per frame, then each reported row and what changed in it."""
    out = [f"{result['status']}: {len(result.get('frames', []))} frames"]
    if result.get("exit_code"):
        out[0] += f", exit status {result['exit_code']}"
    for frame in result.get("frames", []):
        layout = frame.get("layout", {})
        took = frame.get("since_previous_ns")
        timing = f"  {took / 1e6:.3f} ms after the one before" if took is not None else ""
        out.append(f"  frame {frame['frame']}: {frame['png']}  {len(layout.get('elements', []))} marked{timing}")
    for name, row in result.get("effects", {}).items():
        out.append(f"  {name}: {', '.join(row) or 'no effects'}")
    for name, moved in result.get("changed", {}).items():
        out.append(f"  {name} gained {moved['added'] or 'nothing'} and lost {moved['removed'] or 'nothing'}")
    if result.get("stderr"):
        out.append(result["stderr"].rstrip())
    return "\n".join(out)


def shot_source(source: str, functions: list[str], since: str | None, root: Path, **options) -> dict[str, Any]:
    """A shot of a program given as text, as the edit host holds its candidates. It is built as a project of one
    file under `root`, a directory the host keeps, so the frames stay where the agent can open them."""
    path = root / f"candidate-{hashlib.sha256(source.encode()).hexdigest()[:16]}.cairn"
    path.write_text(source, encoding="utf-8")
    return shot(load_project(path), functions, since, **options)

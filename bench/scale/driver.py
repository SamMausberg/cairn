#!/usr/bin/env python3
"""Run one `cairn` command in this process, optionally with the compiler's size limits lifted, and print what it
cost: wall time and peak resident memory. The measurement harness (measure.py) starts one of these per step, so
every number is a cold process, import time included.

    python3 bench/scale/driver.py [--unlimited] -- check results/scale/p10k --format json

`--unlimited` raises MAX_SOURCE, MAX_FUNCTIONS and MAX_NODES in every module that holds a copy, for measuring past
the limits; nothing else changes, and nothing the repository ships runs this way.
"""

from __future__ import annotations

import contextlib
import io
import json
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

LIFTED = {"MAX_SOURCE": 1 << 28, "MAX_FUNCTIONS": 1 << 30, "MAX_NODES": 1 << 40}


def lift() -> None:
    """Raise the size limits wherever a module imported them by name."""
    from cairn.compiler import expansion, lexing, traits, tree
    from cairn.projects import project

    for module in (tree, lexing, expansion, traits, project):
        for name, value in LIFTED.items():
            if hasattr(module, name):
                setattr(module, name, value)


def lsp(path: Path, edit: tuple[str, str]) -> dict:
    """An editor's view: open `path` in the language server, then change it once (`edit` replaces its first
    argument with its second), and time each refresh. Each refresh analyses the file within its project."""
    from cairn.editor.lsp import Server

    server = Server(io.BytesIO(), io.BytesIO())
    uri, text = path.resolve().as_uri(), path.read_text(encoding="utf-8")
    started = time.perf_counter()
    server.handle("textDocument/didOpen", {"textDocument": {"uri": uri, "text": text}})
    opened = time.perf_counter()
    changed = text.replace(*edit, 1)
    server.handle("textDocument/didChange", {"textDocument": {"uri": uri}, "contentChanges": [{"text": changed}]})
    done = time.perf_counter()
    diagnostics = server.docs[uri].diagnostics
    return {"open_seconds": round(opened - started, 3), "change_seconds": round(done - opened, 3),
            "diagnostics": len(diagnostics)}  # fmt: skip


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--lsp":  # --lsp [--unlimited] FILE OLD NEW
        unlimited = argv[1] == "--unlimited"
        path, old, new = argv[2:] if unlimited else argv[1:]
        started = time.perf_counter()
        if unlimited:
            lift()
        measured = lsp(Path(path), (old, new))
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(json.dumps({"status": 0, "seconds": round(time.perf_counter() - started, 3),
                          "peak_mib": round(peak / 1024, 1), **measured}))  # fmt: skip
        return 0
    unlimited = "--unlimited" in argv[: argv.index("--")] if "--" in argv else False
    command = argv[argv.index("--") + 1 :] if "--" in argv else argv
    started = time.perf_counter()
    if unlimited:
        lift()
    from cairn.cli import main as cli

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = cli(command)
    wall = time.perf_counter() - started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KiB on Linux
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss  # the largest native compiler it started
    text = out.getvalue()
    tail = text[-2000:] if status else ""
    print(json.dumps({"status": status, "seconds": round(wall, 3), "peak_mib": round(peak / 1024, 1),
                      "compiler_peak_mib": round(children / 1024, 1), "tail": tail}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

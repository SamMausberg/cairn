#!/usr/bin/env python3
"""Run one `cairn` command over sources Bazel hands an action, for the rules in defs.bzl.

Bazel lays an action's inputs out as symbolic links, and a CAIRN project refuses a linked source, so this copies
each source into a fresh directory under its path in the workspace, writes a `cairn.toml` that lists them in the
order given (dependencies first), and runs the command there. The manifest stays data: this script writes it, and
the compiler reads it exactly as it reads one a person wrote.

    stage.py check|build|test --cairn BIN --name NAME [--kind exe|library] [--cxx CXX] [--out FILE] -- SRC...
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


def within(source: str) -> str:
    """The source's path in the staged project: its workspace path, with an external repository's `../name/`
    prefix kept as `external/name/` so every segment is one the manifest admits."""
    parts = [p for p in Path(source).parts if p not in ("", ".")]
    if parts and parts[0] == "..":
        parts = ["external", *parts[1:]]
    if any(p == ".." or not SEGMENT.fullmatch(p) for p in parts):
        raise SystemExit(f"stage.py: {source} has a path segment a CAIRN manifest does not admit")
    return "/".join(parts)


def stage(root: Path, name: str, kind: str, sources: list[str]) -> Path:
    listed = []
    for source in sources:
        target = root / within(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)  # the bytes, never the link
        listed.append(within(source))
    lines = ["[project]", f'name = "{name}"', "sources = [" + ", ".join(f'"{s}"' for s in listed) + "]", "",
             "[build]", f'kind = "{kind}"', ""]  # fmt: skip
    (root / "cairn.toml").write_text("\n".join(lines), encoding="utf-8")
    return root


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["check", "build", "test"])
    ap.add_argument("--cairn", required=True, help="The compiler's command-line script, bin/cairn.")
    ap.add_argument("--name", required=True)
    ap.add_argument("--kind", choices=["exe", "library"], default="library")
    ap.add_argument("--cxx", default="clang++")
    ap.add_argument("--out", type=Path, help="check: a stamp written on success; build: the artifact.")
    ap.add_argument("sources", nargs="+")
    args = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", args.name):
        raise SystemExit(f"stage.py: {args.name!r} is not a CAIRN project name")
    with tempfile.TemporaryDirectory(prefix="cairn-") as scratch:
        root = stage(Path(scratch) / args.name, args.name, args.kind, args.sources)
        line = [args.cairn, args.mode, str(root), "--format", "json"]  # its own #! names its Python
        if args.mode == "build":
            line += ["--cxx", args.cxx, "--out", str(Path(scratch) / "build")]
        if args.mode == "test":
            line += ["--cxx", args.cxx]
        done = subprocess.run(line, capture_output=True, text=True)
        if done.returncode:
            sys.stderr.write(done.stdout + done.stderr)
            return done.returncode
        if args.mode == "check" and args.out:
            args.out.write_text(done.stdout, encoding="utf-8")
        if args.mode == "build":
            record = json.loads(done.stdout)
            shutil.copyfile(record["artifact"], args.out)
            args.out.chmod(0o755)
        if args.mode == "test":
            sys.stdout.write(done.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

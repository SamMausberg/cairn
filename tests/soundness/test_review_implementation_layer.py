"""The adversarial review of what 0.9 added: each defect it found pinned by the program or request that showed it,
and the attacks that were correctly refused or held, kept so that a later change cannot reopen them.

`evidence/v0_9/review/README.md` has the write-up.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from cairn.projects import export as exported
from cairn.projects.project import load_project
from emitted import code_of

# --- Fixed: an export's record is data, and a build runs only what toolchain.py gives for it -----------------------


def reidentified(directory: Path, change) -> None:
    """Edit the export's record with `change` and give it the identity of what it now says, as anyone can."""
    path = directory / exported.RECORD
    record = json.loads(path.read_text())
    change(record, directory)
    record["identity"] = exported.identity(record)
    path.write_text(json.dumps(record))


def shipped(record: dict, directory: Path) -> None:  # the export brings its own "compiler"
    tool = directory / "g++"
    tool.write_text(f"#!/bin/sh\ntouch {directory.parent / 'ran'}\necho fake 1.0\n")
    tool.chmod(0o755)
    record["files"]["g++"], record["roles"]["g++"] = hashlib.sha256(tool.read_bytes()).hexdigest(), "program"
    record["compilers"]["cxx"] = {"path": str(tool), "version": "fake 1.0"}


ATTACKS = {
    "a shell for a command": lambda r, d: r.update(command=["sh", "-c", f"touch {d.parent / 'ran'}; touch summed"]),
    "a flag the toolchain never gives": lambda r, d: r["command"].insert(1, f"-fplugin={d.parent / 'ran.so'}"),
    "a compiler the export ships": shipped,
    "an artifact outside the build": lambda r, d: r.update(artifact="../../ran"),
}


@pytest.mark.parametrize("attack", ATTACKS)
def test_a_reidentified_export_record_runs_nothing_it_names(tmp_path, attack):
    """The identity is a digest anyone can recompute. A record whose command was a shell, with the identity recomputed,
    was run by `cairn build DIR` and reported `native-built`, and so was a "compiler" the export shipped, which
    `--version` ran first."""
    if not shutil.which("g++"):
        pytest.skip("g++ unavailable")
    root = tmp_path / "summed"
    (root / "src").mkdir(parents=True)
    (root / "src/main.cairn").write_text("fn main() -> i32 { return 0; }\n")
    (root / "cairn.toml").write_text(
        '[project]\nname = "summed"\nsources = ["src/main.cairn"]\n\n[build]\nkind = "exe"\n'
    )
    out = tmp_path / "out"
    exported.export(load_project(root), out, cxx="g++")
    assert exported.build(out, tmp_path / "first")["status"] == "native-built"  # the export as written builds
    reidentified(out, ATTACKS[attack])
    exported.check(out)  # still intact by its hashes: the identity alone cannot tell
    assert code_of(lambda: exported.build(out, tmp_path / "builds")) in {"E-EXPORT-TAMPERED", "E-EXPORT-TOOLCHAIN"}
    assert not (tmp_path / "ran").exists() and not (tmp_path / "builds").exists()


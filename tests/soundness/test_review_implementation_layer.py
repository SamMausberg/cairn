"""The adversarial review of what 0.9 added: each defect it found pinned by the program or request that showed it,
and the attacks that were correctly refused or held, kept so that a later change cannot reopen them.

`evidence/v0_9/review/README.md` has the write-up.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from cairn.cli import main
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


# --- Fixed: a validation holds only while everything the implementation calls is as it was -------------------------

HELPED = """fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut s:u64 = 0;
  for i in 0..n {
    for j in 0..n { if j == i { s = add_wrap(s, xs[j]); } }
  }
  return s;
}

fn settle(x:u64) -> u64 = x;

fn total_fast(n:usize, xs:ro<u64>[n]) -> u64 implements total {
  let mut s:u64 = 0;
  for i in 0..n { s = add_wrap(s, xs[i]); }
  return settle(s);
}

fn main() -> i32 { return 0; }
"""


def test_a_validation_goes_stale_when_a_helper_of_the_implementation_changes(tmp_path, capsys):
    """An implementation's identity digests the two declarations as written, not what they call. A validation stayed
    current after the helper `settle` changed, and `cairn tune --write` wrote `plan total use total_fast;` for an
    implementation that now fails validation, with the same identity."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    root, history = tmp_path / "helped", tmp_path / "history"
    (root / "src").mkdir(parents=True)
    (root / "src/main.cairn").write_text(HELPED)
    (root / "cairn.toml").write_text('[project]\nname = "helped"\nsources = ["src/main.cairn"]\n')
    validate = ["validate", str(root), "--symbol", "total_fast", "--format", "json"]
    assert main([*validate, "--history", str(history)]) == 0
    first = json.loads(capsys.readouterr().out)
    (root / "src/main.cairn").write_text(
        HELPED.replace("settle(x:u64) -> u64 = x;", "settle(x:u64) -> u64 = add_wrap(x, 1);")
    )
    assert main(validate) == 1  # it now returns the sum plus one
    assert json.loads(capsys.readouterr().out)["identity"] == first["identity"]  # the receipt's identity did not move
    tune = ["tune", str(root), "--symbol", "total", "--at", "n=1e4", "--history", str(history), "--write"]
    assert main([*tune, "--format", "json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    [row] = [c for c in answer["candidates"] if c.get("use") == "total_fast"]
    assert isinstance(row["validated"], str) and "no validation holds" in row["validated"]
    assert "use total_fast" not in (root / "src/main.cairn").read_text()

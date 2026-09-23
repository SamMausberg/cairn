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
from cairn.compiler.cairnc import compile_source
from cairn.projects import export as exported
from cairn.projects.project import load_project
from cairn.verify.validation import validate
from emitted import code_of, refused

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


# --- Fixed: the phase rule follows what a call, a closure, assembly and `&&` leave behind --------------------------

PUT = "fn put(x:rw<usize>, v:usize) { x = v; }\n"
CALL = "fn call(f:ro<fn()>) { f(); }\n"
PHASES = {  # each was accepted; the first three raced under ThreadSanitizer with both compilers
    "a shared index a callee rewrote": (
        "E-COOP-UNDECIDED",
        PUT + "fn f(g:usize, out:rw<u64>[g]) { blocks b in g threads t in 64 { shared s:u64[64] = zeroed;\n"
        "  let mut k:usize = t; put(k, 0); s[k] = u64(t); barrier; if t == 0 { out[b] = s[0]; } } }",
    ),
    "a shared index swap rewrote": (
        "E-COOP-UNDECIDED",
        "fn f(g:usize, out:rw<u64>[g]) { blocks b in g threads t in 64 { shared s:u64[64] = zeroed;\n"
        "  let mut k:usize = t; let mut z:usize = 0; swap(k, z); s[k] = u64(t); barrier; if t == 0 { out[b] = s[0]; } } }",
    ),
    "the else arm of an && whose right side traps": (
        "E-COOP-CONFLICT",
        "fn f(g:usize, n:usize, x:ro<u64>[n], out:rw<u64>[g]) { blocks b in g threads t in 64 { shared s:u64[64] = zeroed;\n"
        "  if x[0] == 7 && t - 5 > 100 { } else { s[max(t, 4)] = u64(t); } barrier; if t == 0 { out[b] = s[4]; } } }",
    ),
    "a closure rewriting a local that indexes a shared array": (
        "E-COOP-UNDECIDED",
        CALL + "fn f(g:usize, out:rw<u64>[g]) { blocks b in g threads t in 64 { shared s:u64[64] = zeroed;\n"
        "  let mut k:usize = t; call(|| { k = 0; }); s[k] = u64(t); barrier; if t == 0 { out[b] = s[0]; } } }",
    ),
    "typed PTX writing a shared array in every thread": (
        "E-COOP-CONFLICT",
        "fn f(out:rw<u32>[64]@device) { blocks g in 1 threads t in 64 { shared sc:u32[64] = zeroed;\n"
        '  unsafe { asm ptx sm_75 "st.shared.u32 [%0], %1;" (sc, 7) effects(write:sc); } out[t] = 1; } }',
    ),
    "typed PTX writing a shared array another thread reads": (
        "E-COOP-UNORDERED",
        "fn f(out:rw<u32>[64]@device) { blocks g in 1 threads t in 64 { shared sc:u32[64] = zeroed;\n"
        '  if t == 0 { unsafe { asm ptx sm_75 "st.shared.u32 [%0], %1;" (sc, 7) effects(write:sc); } }\n'
        "  out[t] = sc[t]; } }",
    ),
}


@pytest.mark.parametrize("name", PHASES)
def test_the_phase_rule_follows_calls_closures_assembly_and_short_circuits(name):
    code, source = PHASES[name]
    refused(code, source)


def test_typed_ptx_one_thread_writes_before_a_barrier_is_still_accepted():
    compile_source(
        "fn f(out:rw<u32>[64]@device) { blocks g in 1 threads t in 64 { shared sc:u32[64] = zeroed;\n"
        '  if t == 0 { unsafe { asm ptx sm_75 "st.shared.u32 [%0], %1;" (sc, 7) effects(write:sc); } }\n'
        "  barrier; out[t] = sc[t]; } }"
    )


# --- Fixed: validation compares what the code returns, and nothing the code prints ---------------------------------


def test_an_implementation_cannot_print_its_own_validation():
    """Each call ran in a fork that kept the child's stdout, which carries the answers to the validator: an
    implementation printing `{"outcome": "return", ...}` lines had them read as every later call's answer, so one
    returning 12345 for x passed, and one printing anything else broke the run with a JSONDecodeError."""
    reference = "fn f(x:u64) -> u64 effects(io, ffi:write) = x;\n"
    forged = reference + (
        "fn g(x:u64) -> u64 implements f {\n"
        '  for i in 0..900 { println("{\\"outcome\\": \\"return\\", \\"after\\": {}, \\"return\\": 0}"); }\n'
        "  return 12345;\n}\n"
    )
    assert validate(forged, "f", "g")["status"] == "failed"
    chatty = reference + 'fn g(x:u64) -> u64 implements f { println("hello"); return x; }\n'
    assert validate(chatty, "f", "g")["status"] == "passed"


def test_negative_zero_is_not_zero_without_a_tolerance():
    """At the default zero tolerance, `x + 0.0` against `x` passed, although it turns -0.0 into 0.0 and Z3's answer in
    the same record gave that counterexample."""
    source = "fn f(x:f64) -> f64 = x;\nfn g(x:f64) -> f64 implements f = x + 0.0;\n"
    record = validate(source, "f", "g")
    assert record["status"] == "failed" and record["finite"]["failed"]["inputs"] == {"x": -0.0}
    tolerant = {"tolerance": {"absolute": 1e-12, "relative": 0.0}}
    assert validate(source, "f", "g", tolerant)["status"] == "passed"

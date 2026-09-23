"""What the cooperative rules refuse really faults when it runs.

Each program here was accepted before the refusal that now names it (tests/soundness/test_reach.py and
test_pipelines.py). Built with that one refusal taken out again, it hangs at a barrier some threads skip, races under
ThreadSanitizer, or reads through the null stage pointer, with both compilers. A fill from
memory the region cannot copy from is refused too (E-PLACEMENT), but it faults only on a device, which nothing here
runs.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler import cooperative, pipelines
from cairn.compiler.cairnc import compile_source
from cairn.projects.toolchain import command
from emitted import emit, refused

CALL = "fn call(f:ro<fn()>) { f(); }\n"
ON_OUT = "fn main() -> i32 { let mut o = Buf[u64](2); f(len(o), o); return 0; }\n"
BARRIER = """fn put(x:rw<usize>, v:usize) { x = v; }
fn f(g:usize) { blocks b in g threads t in 64 { let mut k:usize = 0; put(k, VALUE); if k == 0 { barrier; } } }
fn main() -> i32 { f(2); return 0; }
"""
CLOSURES = {
    "shared": "fn f(g:usize, out:rw<u64>[g]) { blocks b in g threads t in 64 { shared s:u64[64] = zeroed;\n"
    "  call(|| { s[0] = u64(t); }); barrier; if t == 0 { out[b] = s[0]; } } }\n",
    "outside": "fn f(g:usize, out:rw<u64>[g]) { blocks b in g threads t in 64 { call(|| { out[0] = u64(t); }); } }\n",
}
EARLY = """fn first(n:usize, x:ro<u64>[n]) -> u64 = x[0];
fn f(g:usize, n:usize, x:ro<u64>[n], out:rw<u64>[g]) { blocks b in g threads t in 64 {
  pipeline tiles:u64[64] depth 1; let v = first(64, tiles); tiles.fill(x, 0, 64); tiles.wait(); tiles.release();
  if t == 0 { out[b] = v; } } }
fn main() -> i32 { let x = Buf[u64](64); let mut o = Buf[u64](2); f(len(o), len(x), x, o); return 0; }
"""


def native(tmp_path: Path, cpp: str, cxx: str, sanitizer: str, timeout: int) -> subprocess.CompletedProcess:
    """The project's own build of `cpp` under `sanitizer`, run once without address randomization."""
    if not shutil.which(cxx) or not shutil.which("setarch"):
        pytest.skip(f"needs {cxx} and setarch")
    source, executable = emit(tmp_path, cpp)
    line = [*command(cxx, source, executable, kind="exe"), "-g", f"-fsanitize={sanitizer}"]
    built = subprocess.run(line, capture_output=True, text=True, timeout=600)
    assert built.returncode == 0, built.stderr[-3000:]
    env = {**os.environ, "TSAN_OPTIONS": "halt_on_error=1", "ASAN_OPTIONS": "detect_leaks=0"}
    return subprocess.run(["setarch", "-R", executable], capture_output=True, text=True, timeout=timeout, env=env)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_barrier_under_a_local_a_callee_set_per_thread_hangs(tmp_path, cxx, monkeypatch):
    """put(k, t) leaves k = t, so only thread 0 of each block reaches the barrier and the others never do: the run
    does not finish. With put(k, g) every thread arrives and it exits."""
    refused("E-COOP-BARRIER", BARRIER.replace("VALUE", "t"))
    monkeypatch.setattr(cooperative.Reach, "lends", lambda self, e, at: None)  # the rule before the fix
    assert native(tmp_path, compile_source(BARRIER.replace("VALUE", "g"))[0], cxx, "thread", 60).returncode == 0
    with pytest.raises(subprocess.TimeoutExpired):
        native(tmp_path, compile_source(BARRIER.replace("VALUE", "t"))[0], cxx, "thread", 15)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("where", list(CLOSURES))
def test_a_closure_that_writes_an_array_in_every_thread_races(tmp_path, cxx, where, monkeypatch):
    source = CALL + CLOSURES[where] + ON_OUT
    refused("E-COOP-UNDECIDED", source)
    monkeypatch.setattr(cooperative, "closures", lambda c, ss, held: None)  # the rule before the fix
    ran = native(tmp_path, compile_source(source)[0], cxx, "thread", 120)
    assert "ThreadSanitizer: data race" in ran.stderr, ran.stderr[-3000:]


def reads_elements_only(self, e):
    """The stage walk before the fix: only `tiles[i]` read the readable stage."""
    if e.tag == "lambda":
        return
    for a in e.args:
        reads_elements_only(self, a)
    if e.tag == "index" and e.args[0].tag == "name" and e.args[0].val in self.pipelines:
        state = self.states[e.args[0].val]
        if state.readable is None:
            self.unready(e.args[0].val, e, f"{e.args[0].val}[...] at line {e.line} reads")
        state.reads.append(e)
    if e.tag == "call" and isinstance(e.ref, tuple) and e.ref[0] == "stage":
        self.operate(e)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_stage_lent_before_its_wait_reads_through_null(tmp_path, cxx, monkeypatch):
    """Before its first wait the stage pointer is null: under AddressSanitizer the g++ build stops on the read and the
    clang++ build aborts. The same read after the wait runs clean."""
    refused("E-STAGE-UNREADY", EARLY)
    late = EARLY.replace(
        "let v = first(64, tiles); tiles.fill(x, 0, 64); tiles.wait();",
        "tiles.fill(x, 0, 64); tiles.wait(); let v = first(64, tiles);",
    )
    assert late != EARLY and native(tmp_path, compile_source(late)[0], cxx, "address", 120).returncode == 0
    monkeypatch.setattr(pipelines.Stages, "expr", reads_elements_only)
    ran = native(tmp_path, compile_source(EARLY)[0], cxx, "address", 120)
    assert ran.returncode != 0, ran.stdout[-2000:]

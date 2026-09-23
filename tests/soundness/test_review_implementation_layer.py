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


# --- Held: attacks the checker refused, each with the code it must keep ---------------------------------------------

LAYOUT = "layout T = rows(16, 16);\n"
REGION = (
    "fn k(out:rw<f32>[256]@device, c:ro<f32>[256]@device, a:ro<f16>[256]@device, b:ro<f16>[256]@device) {\n"
    "  blocks g in 1 threads t in 64 {\n    shared sc:f32[256] = zeroed;\n"
)
LOAD_A = "mma_load[WmmaA[f16, 16, 16, 16]](a, T, 0, 0)"
FILLED = "WmmaAcc[f32, 16, 16, 16](0.0)"
LANE = "fn f(n:usize, out:rw<u32>[n]@device, xs:ro<u32>[n]@device) {\n  parallel i in n {\n    unsafe { %s }\n    %s\n  }\n}\n"
HOST_LANE = "fn f(n:usize, out:rw<u64>[n], xs:ro<u64>[n]) {\n  parallel i in n {\n    unsafe { %s }\n    %s\n  }\n}\n"
THREADS = (
    "fn f(out:rw<u32>[64]@device) {\n  blocks g in 1 threads t in 64 {\n    shared sc:u32[64] = zeroed;\n"
    "    sc[t] = u32(t);\n    barrier;\n    unsafe { %s }\n    %s\n  }\n}\n"
)


def region(body: str) -> str:
    return LAYOUT + REGION + body + "  }\n}\n"


def x86(body: str) -> str:
    return "fn f(x:u64) -> u64 { unsafe { " + body + " return r; } }\n"


REFUSED = {
    # when: a condition that cannot trap, over the value parameters
    "a when with checked +": ("E-IMPL-WHEN", "fn f(n:usize) -> usize = n;\nfn g(n:usize) -> usize implements f when n + 1 > 3 = n;"),
    "a when with checked *": ("E-IMPL-WHEN", "fn f(n:usize) -> usize = n;\nfn g(n:usize) -> usize implements f when n * 2 > 3 = n;"),
    "a when negating an integer": ("E-IMPL-WHEN", "fn f(x:i64) -> i64 = x;\nfn g(x:i64) -> i64 implements f when -x > 3 = x;"),
    "a when with a narrowing cast": ("E-IMPL-WHEN", "fn f(n:usize) -> usize = n;\nfn g(n:usize) -> usize implements f when u32(n) > 3 = n;"),
    "a when with a float to integer cast": ("E-IMPL-WHEN", "fn f(x:f64) -> f64 = x;\nfn g(x:f64) -> f64 implements f when i64(x) > 1 = x;"),
    "a when dividing by a zero literal": ("E-IMPL-WHEN", "fn f(n:usize) -> usize = n;\nfn g(n:usize) -> usize implements f when n / 0 > 3 = n;"),
    "a when by a zero constant": ("E-IMPL-WHEN", "const Z:usize = 0;\nfn f(n:usize) -> usize = n;\nfn g(n:usize) -> usize implements f when n % Z == 0 = n;"),
    "a when by a hex zero": ("E-IMPL-WHEN", "fn f(n:usize) -> usize = n;\nfn g(n:usize) -> usize implements f when n % 0x0 == 0 = n;"),
    "a when shifting by the width": ("E-IMPL-WHEN", "fn f(n:u32) -> u32 = n;\nfn g(n:u32) -> u32 implements f when shr(n, 32) == 0 = n;"),
    "a when with len of an owner": ("E-IMPL-WHEN", "fn f(n:usize, b:Buf[u64]) -> usize = n;\nfn g(n:usize, b:Buf[u64]) -> usize implements f when len(b) > 3 = n;"),
    "a when calling a function": ("E-IMPL-WHEN", "fn big(n:usize) -> bool = n > 3;\nfn f(n:usize) -> usize = n;\nfn g(n:usize) -> usize implements f when big(n) = n;"),
    "a when indexing a view": ("E-IMPL-WHEN", "fn f(n:usize, xs:ro<u64>[n]) -> u64 = 0;\nfn g(n:usize, xs:ro<u64>[n]) -> u64 implements f when xs[0] > 3 = 0;"),
    "a when reading an rw borrow": ("E-IMPL-WHEN", "fn f(n:rw<u64>) -> u64 = 0;\nfn g(n:rw<u64>) -> u64 implements f when n > 3 = 0;"),
    "a when reading a field": ("E-IMPL-WHEN", "struct S { a:u64; }\nfn f(s:S) -> u64 = 0;\nfn g(s:S) -> u64 implements f when s.a > 3 = 0;"),
    "a when with abs": ("E-IMPL-WHEN", "fn f(x:i64) -> i64 = x;\nfn g(x:i64) -> i64 implements f when abs(x) > 3 = x;"),
    "a when calling a function parameter": ("E-IMPL-WHEN", "fn f(h:fn(u64) -> u64, x:u64) -> u64 = x;\nfn g(h:fn(u64) -> u64, x:u64) -> u64 implements f when h(x) > 3 = x;"),
    "a when through a layout's at": ("E-IMPL-WHEN", "layout T = rows(4, 4);\nfn f(n:usize) -> usize = n;\nfn g(n:usize) -> usize implements f when T.at(n, 0) > 3 = n;"),
    "a when false for every input": ("E-IMPL-WHEN", "fn f(x:i64) -> i64 = x;\nfn g(x:i64) -> i64 implements f when 1 > 2 = x;"),
    # an implementation never reaches its reference, and is reached only through it
    "an implementation reaching its reference through a helper": ("E-IMPL-CALL", "fn f(n:u64) -> u64 = n;\nfn h(n:u64) -> u64 = f(n);\nfn g(n:u64) -> u64 implements f = h(n);"),
    "an implementation passing its reference as a function value": ("E-IMPL-CALL", "fn apply(k:fn(u64) -> u64, n:u64) -> u64 = k(n);\nfn same(n:u64) -> u64 = n;\nfn f(n:u64) -> u64 = apply(same, n);\nfn g(n:u64) -> u64 implements f = apply(f, n);\nplan f use g;"),
    "an implementation called by name": ("E-IMPL-CALL", "fn f(n:u64) -> u64 = n;\nfn g(n:u64) -> u64 implements f = n;\nfn main() -> i32 { let x = g(1); return 0; }"),
    "an implementation as a function value": ("E-IMPL-CALL", "fn f(n:u64) -> u64 = n;\nfn g(n:u64) -> u64 implements f = n;\nfn apply(k:fn(u64) -> u64, n:u64) -> u64 = k(n);\nfn main() -> i32 { let x = apply(g, 1); return 0; }"),
    "an implementation of an implementation": ("E-IMPLEMENTS", "fn f(n:u64) -> u64 = n;\nfn g(n:u64) -> u64 implements f = n;\nfn h(n:u64) -> u64 implements g = n;"),
    "a generic implementation": ("E-IMPLEMENTS", "fn f[T](x:T) -> T = x;\nfn g[T](x:T) -> T implements f = x;"),
    "an implementation of an extern": ("E-IMPLEMENTS", "extern fn abs_c(x:i64) -> i64 effects();\nfn g(x:i64) -> i64 implements abs_c = x;"),
    "an implementation of a kernel": ("E-IMPLEMENTS", "kernel fn f(n:usize, out:rw<u64>[n]@device) { }\nfn g(n:usize, out:rw<u64>[n]@device) implements f { }"),
    "an implementation in another module": ("E-IMPLEMENTS", "module a;\npub fn f(n:u64) -> u64 = n;\nmodule b;\nimport a;\nfn g(n:u64) -> u64 implements a.f = n;"),
    # an implementation keeps its reference's contract
    "renamed parameters": ("E-IMPL-SIGNATURE", "fn f(n:u64) -> u64 = n;\nfn g(m:u64) -> u64 implements f = m;"),
    "rw where the reference reads": ("E-IMPL-SIGNATURE", "fn f(n:usize, xs:ro<u64>[n]) -> u64 = 0;\nfn g(n:usize, xs:rw<u64>[n]) -> u64 implements f = 0;"),
    "a view moved to the device": ("E-IMPL-SIGNATURE", "fn f(n:usize, xs:ro<u64>[n]) -> u64 = 0;\nfn g(n:usize, xs:ro<u64>[n]@device) -> u64 implements f = 0;"),
    "another extent name": ("E-IMPL-SIGNATURE", "fn f(n:usize, xs:ro<u64>[n]) -> u64 = 0;\nfn g(m:usize, xs:ro<u64>[m]) -> u64 implements f = 0;"),
    "allocation under a pure ceiling": ("E-IMPL-EFFECT", "fn f(n:usize, xs:rw<u64>[n]) effects(pure, write:xs) { for i in 0..n { xs[i] = 1; } }\nfn g(n:usize, xs:rw<u64>[n]) implements f { let mut b = Buf[u64](n); for i in 0..n { xs[i] = 1; } }"),
    "lanes under a sequential reference": ("E-IMPL-EFFECT", "fn f(n:usize, xs:rw<u64>[n]) { for i in 0..n { xs[i] = 1; } }\nfn g(n:usize, xs:rw<u64>[n]) implements f { parallel i in n { xs[i] = 1; } }"),
    "a helper with more effects": ("E-IMPL-EFFECT", "fn f(n:u64) -> u64 = n;\nfn h(n:u64) -> u64 { let b = Buf[u64](4); return n; }\nfn g(n:u64) -> u64 implements f = h(n);"),
    "printing where the reference does not": ("E-IMPL-EFFECT", 'fn f(n:u64) -> u64 = n;\nfn g(n:u64) -> u64 implements f { println("x"); return n; }'),
    "assembly where the reference has none": ("E-IMPL-EFFECT", 'fn f(n:u64) -> u64 = n;\nfn g(n:u64) -> u64 implements f { unsafe { asm("nop"); } return n; }'),
    "a loop that may not end under a reference that does": ("E-IMPL-EFFECT", "fn f(n:u64) -> u64 = n;\nfn g(n:u64) -> u64 implements f { let mut k:u64 = 0; while k < 1 { k = mul_wrap(k, 1); } return n; }"),
    "a lane calling a reference whose implementation uses lanes": ("E-PARALLEL-CALL", "fn f(n:usize, xs:rw<u64>[n]) effects(pure, write:xs, par:host) { for i in 0..n { xs[i] = 1; } }\nfn g(n:usize, xs:rw<u64>[n]) implements f when n >= 65536 { parallel i in n { xs[i] = 1; } }\nfn outer(m:usize, out:rw<u64>[m]) { parallel j in m { let mut b = Array[u64, 4](); f(4, b); out[j] = b[0]; } }"),
    "a device lane calling a reference whose implementation allocates": ("E-PARALLEL-CALL", "fn f(x:u64) -> u64 effects(pure, alloc, free) = x;\nfn g(x:u64) -> u64 implements f { let b = Buf[u64](4); return x; }\nfn outer(m:usize, out:rw<u64>[m]@device) { parallel j in m { out[j] = f(u64(j)); } }"),
    "a plan using another function's implementation": ("E-IMPL-USE", "fn f(n:u64) -> u64 = n;\nfn k(n:u64) -> u64 = n;\nfn g(n:u64) -> u64 implements k = n;\nplan f use g;"),
    "two selections": ("E-IMPL-USE", "fn f(n:u64) -> u64 = n;\nfn g(n:u64) -> u64 implements f = n;\nfn h(n:u64) -> u64 implements f = n;\nplan f use g;\nplan f use h;"),
    "a plan using the reference": ("E-IMPL-USE", "fn f(n:u64) -> u64 = n;\nfn g(n:u64) -> u64 implements f = n;\nplan f use f;"),
    # fragments: warp operations, whole warps, and the phase rule around them
    "a fragment load under t % 2 == 0": ("E-COOP-WARP", region(f"    if t % 2 == 0 {{ let x = {LOAD_A}; }}\n")),
    "a fragment load under a condition read from data": ("E-COOP-WARP", region(f"    if c[t] > 0.0 {{ let x = {LOAD_A}; }}\n")),
    "a fragment load under a local copy of t": ("E-COOP-WARP", region(f"    let u = t + 0; if u < 16 {{ let x = {LOAD_A}; }}\n")),
    "an accumulator filled under t < 48": ("E-COOP-WARP", region(f"    if t < 48 {{ let acc = {FILLED}; }}\n")),
    "a fragment load in a loop of per-thread trips": ("E-COOP-WARP", region(f"    for i in 0..t {{ let x = {LOAD_A}; }}\n")),
    "a fragment load after a per-thread break": ("E-COOP-WARP", region(f"    for i in 0..4 {{ if t == 3 {{ break; }} let x = {LOAD_A}; }}\n")),
    "two warps storing one fragment in one phase": ("E-COOP-CONFLICT", region(f"    let acc = {FILLED};\n    mma_store(sc, T, 0, 0, acc);\n")),
    "a store while another warp reads the tile": ("E-COOP-UNORDERED", region(f"    let acc = {FILLED};\n    if t / 32 == 0 {{ mma_store(sc, T, 0, 0, acc); }}\n    if t >= 32 {{ out[t] = sc[t]; }}\n")),
    "a shared tile loaded before the barrier after its write": ("E-COOP-UNORDERED", region("    sc[t] = 1.0;\n    let x = mma_load[WmmaAcc[f32, 16, 16, 16]](sc, T, 0, 0);\n")),
    "a store over a tile another warp loaded in the phase": ("E-COOP-REUSE", region("    let acc = mma_load[WmmaAcc[f32, 16, 16, 16]](sc, T, 0, 0);\n    if t / 32 == 1 { mma_store(sc, T, 0, 0, acc); }\n")),
    "a fragment made in a helper outside a region": ("E-FRAGMENT", LAYOUT + "fn make() -> WmmaAcc[f32, 16, 16, 16] = WmmaAcc[f32, 16, 16, 16](0.0);\nfn k(out:rw<f32>[256]@device) {\n  blocks g in 1 threads t in 32 {\n    shared sc:f32[256] = zeroed;\n    let acc = make();\n    mma_store(sc, T, 0, 0, acc);\n  }\n}\n"),
    "a fragment load in a parallel lane": ("E-FRAGMENT", LAYOUT + "fn k(n:usize, out:rw<f32>[n]@device, a:ro<f16>[256]@device) {\n  parallel i in n {\n    let x = mma_load[WmmaA[f16, 16, 16, 16]](a, T, 0, 0);\n    out[i] = 0.0;\n  }\n}\n"),
    "a fragment stored into a device view": ("E-FRAGMENT", LAYOUT + "fn k(out:rw<f32>[256]@device) {\n  blocks g in 1 threads t in 32 {\n    let acc = WmmaAcc[f32, 16, 16, 16](0.0);\n    mma_store(out, T, 0, 0, acc);\n  }\n}\n"),
    "a fragment loaded from an rw device view": ("E-FRAGMENT", LAYOUT + "fn k(out:rw<f32>[256]@device, a:rw<f16>[256]@device) {\n  blocks g in 1 threads t in 32 {\n    let x = mma_load[WmmaA[f16, 16, 16, 16]](a, T, 0, 0);\n    out[t] = 0.0;\n  }\n}\n"),
    "a layout hidden by a local": ("E-LAYOUT-CONSUMER", LAYOUT + "fn k(out:rw<f32>[256]@device, a:ro<f16>[256]@device) {\n  blocks g in 1 threads t in 32 {\n    let T = 3;\n    let x = mma_load[WmmaA[f16, 16, 16, 16]](a, T, 0, 0);\n    out[t] = 0.0;\n  }\n}\n"),
    "a layout past a constant-length shared array": ("E-LAYOUT-CONSUMER", "layout TILE = rows(16, 16);\nconst CELLS:usize = 128;\nfn tile(out:rw<f32>[128], a:ro<f16>[256]@unified, b:ro<f16>[256]@unified) {\n  blocks g in 1 threads t in 32 {\n    shared sc:f32[CELLS] = zeroed;\n    let x = mma_load[WmmaA[f16, 16, 16, 16]](a, TILE, 0, 0);\n    let y = mma_load[WmmaB[f16, 16, 16, 16]](b, TILE, 0, 0);\n    let mut acc = WmmaAcc[f32, 16, 16, 16](0.0);\n    acc = mma_unordered(acc, x, y);\n    mma_store(sc, TILE, 0, 0, acc);\n    barrier;\n    for i in 0..4 { out[t + 32 * i] = sc[t + 32 * i]; }\n  }\n}\n"),
    # typed assembly: what an address reaches, and where the instructions run
    "a PTX write through an array every lane writes": ("E-PARALLEL-RACE", LANE % ('asm ptx sm_75 "st.global.u32 [%0], %1;" (out, 7) effects(write:out);', "out[i] = 1;")),
    "a PTX write through an ro view": ("E-WRITE-LEASE", LANE % ('asm ptx sm_75 "st.global.u32 [%0], %1;" (xs, 7) effects(write:xs);', "out[i] = 1;")),
    "a PTX address with no declared effect": ("E-ASM-EFFECT", LANE % ('asm ptx sm_75 "ld.global.u32 %0, [%1];" (out v:u32, xs);', "out[i] = v;")),
    "PTX declaring io in a lane": ("E-ASM-LANE", LANE % ('asm ptx sm_75 "trap;" () effects(io);', "out[i] = 1;")),
    "PTX declaring barrier in a cooperative thread": ("E-ASM-LANE", THREADS % ('asm ptx sm_75 "bar.sync 0;" () effects(barrier);', "out[t] = sc[t];")),
    "x86_64 assembly in a device lane": ("E-ASM-TARGET", LANE % ('asm x86_64 "movl %1, %0" (out v:u32, xs[i]);', "out[i] = v;")),
    "host assembly writing through an address in a host lane": ("E-PARALLEL-CALL", HOST_LANE % ('asm x86_64 "movq $1, (%0)" (out) effects(write:out);', "out[i] = 1;")),
    "a template naming an operand past the list": ("E-ASM-OPERANDS", x86('asm x86_64 "movq %10, %0" (out r:u64, x);')),
    "a template with a named operand": ("E-ASM-OPERANDS", x86('asm x86_64 "movq %[x], %0" (out r:u64, x);')),
    "a clobber named memory": ("E-ASM-CLOBBER", x86('asm x86_64 "movq %1, %0" (out r:u64, x) clobbers(memory);')),
    "a clobber of the stack pointer": ("E-ASM-CLOBBER", x86('asm x86_64 "movq %1, %0" (out r:u64, x) clobbers(rsp);')),
    "a u8 operand in PTX": ("E-ASM-CONSTRAINT", LANE % ('asm ptx sm_75 "mov.u32 %0, %1;" (out v:u32, u8(1));', "out[i] = v;")),
    "an address of a leased owner": ("E-LEASED", 'fn fill(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = 1; } }\nfn main() -> i32 { let mut d = Buf[u64](8); let t = spawn fill(len(d), d); unsafe { asm x86_64 "movq (%1), %0" (out r:u64, d) effects(read:d); } wait(t); return 0; }\n'),
    "one array read and written through two operands": ("E-ALIAS", 'fn f(n:usize, xs:rw<u64>[n]) { unsafe { asm x86_64 "movq (%0), %%rax; movq %%rax, (%1)" (xs, xs) clobbers(rax) effects(read:xs, write:xs); } }\n'),
    "a host view reached by PTX in a lane": ("E-PLACEMENT", 'fn f(n:usize, out:rw<u32>[n]@device, h:ro<u32>[n]) {\n  parallel i in n {\n    unsafe { asm ptx sm_75 "ld.global.u32 %0, [%1];" (out v:u32, h) effects(read:h); }\n    out[i] = v;\n  }\n}\n'),
    "an effect naming a view that is no operand": ("E-ASM-EFFECT", 'fn f(n:usize, xs:rw<u64>[n], x:u64) -> u64 { unsafe { asm x86_64 "movq %1, %0" (out r:u64, x) effects(write:xs); return r; } }\n'),
    # cooperative regions
    "a return in a cooperative region": ("E-PARALLEL-CONTROL", "fn f(g:usize) { blocks b in g threads t in 64 { if t == 3 { return; } barrier; } }"),
    "an element loop over a stage before any wait": ("E-STAGE-UNREADY", "fn f(g:usize, n:usize, x:ro<u64>[n], out:rw<u64>[g]) { blocks b in g threads t in 64 { pipeline tiles:u64[64] depth 1; let mut v:u64 = 0; for y in tiles { v += y; } tiles.fill(x, 0, 64); tiles.wait(); tiles.release(); if t == 0 { out[b] = v; } } }"),
    "a barrier under a local assigned from t through seven moves in a loop": ("E-COOP-BARRIER", "fn f(g:usize) { blocks b in g threads t in 32 { let mut a0:usize = 0; let mut a1:usize = 0; let mut a2:usize = 0; let mut a3:usize = 0; let mut a4:usize = 0; let mut a5:usize = 0; let mut a6:usize = 0; for k in 0..20 { if a6 == 0 { barrier; } a6 = a5; a5 = a4; a4 = a3; a3 = a2; a2 = a1; a1 = a0; a0 = t; } } }"),
    "an outside write through a local lent to a call": ("E-COOP-GLOBAL", "fn put(x:rw<usize>, v:usize) { x = v; }\nfn f(g:usize, n:usize, out:rw<u64>[n]) { blocks b in g threads t in 64 { let mut k:usize = t; put(k, 0); if b * 64 + k < n { out[b * 64 + k] = u64(t); } } }"),
}  # fmt: skip


@pytest.mark.parametrize("name", REFUSED)
def test_the_attack_is_refused(name):
    code, source = REFUSED[name]
    refused(code, source)

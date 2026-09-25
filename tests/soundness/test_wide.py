"""Wide loads and stores, `load_wide[K](x, i)` and `store_wide(x, i, v)` (compiler/primitives/wide.py): K adjacent elements in one
access, with a cache hint the device reads and the host ignores.

Every rule has a rejection naming its code. The accesses run natively in host code, in a host `parallel` region and
in a host cooperative region under both compilers, against plain loops, with the address and undefined-behaviour
sanitizers, and the cooperative region under the thread sanitizer. Both guards are shown to trap before the access
reaches memory: past the end, and off the access's width. The device program runs emulated on host threads, and its
device lowering compiles for sm_120 to one 128-bit instruction an access, read back with cuobjdump. Nothing runs on a
GPU.
"""

import re

import pytest

from cairn.agent.explain import explain
from cairn.compiler.cairnc import compile_source
from emitted import assembled, contract, ran_emulated, refused, round_trips, sanitizers, watched

KERNELS = """// Each lane reverses and doubles four adjacent elements: one 16-byte load and one 16-byte store.
fn quads(m:usize, n:usize, out:rw<f32>[n], x:ro<f32>[n]) {
  parallel i in m {
    let v = load_wide[4](x, 4 * i, Cache.streaming);
    let mut w = Array[f32, 4]();
    for k in 0..4 { w[k] = v[3 - k] * 2.0; }
    store_wide(out, 4 * i, w, Cache.streaming);
  }
}

// Each block moves 256 elements through shared memory four at a time, handing each thread's four to thread 63 - t.
fn tiles(g:usize, n:usize, out:rw<u32>[n], x:ro<u32>[n]) {
  blocks b in g threads t in 64 {
    shared s:u32[256] = zeroed;
    let v = load_wide[4](x, 4 * (b * 64 + t), Cache.l2);
    store_wide(s, 4 * t, v);
    barrier;
    let w = load_wide[4](s, 4 * (63 - t));
    store_wide(out, 4 * (b * 64 + t), w, Cache.streaming);
  }
}
"""

CHECKS = """
fn check(m:usize, g:usize) -> i32 {
  let n = 4 * m;
  buffer x:f32[n] = zeroed;
  buffer out:f32[n] = zeroed;
  for i in 0..n { x[i] = f32(i % 1000) * 0.5; }
  QUADS
  for i in 0..n {
    if out[i] != x[i / 4 * 4 + 3 - i % 4] * 2.0 { return 1; }
  }
  let k = g * 256;
  buffer a:u32[k] = zeroed;
  buffer b:u32[k] = zeroed;
  for i in 0..k { a[i] = u32(i) * 7; }
  TILES
  for blk in 0..g {
    for t in 0..64 {
      for e in 0..4 {
        if b[blk * 256 + 4 * t + e] != a[blk * 256 + 4 * (63 - t) + e] { return 2; }
      }
    }
  }
  return 0;
}
"""

HOST = (
    KERNELS
    + CHECKS.replace("QUADS", "quads(m, n, out, x);").replace("TILES", "tiles(g, k, b, a);")
    + """
fn main() -> i32 {
  let answer = check(5000, 5);
  if answer != 0 { return answer; }
  buffer bytes:u8[64] = zeroed;
  for i in 0..64 { bytes[i] = u8(i); }
  let sixteen = load_wide[16](bytes, 16);
  for e in 0..16 { if sixteen[e] != u8(16 + e) { return 3; } }
  buffer halves:f16[16] = zeroed;
  for i in 0..16 { halves[i] = f16(f32(i)); }
  let eight = load_wide[8](halves, 8, Cache.last_use);
  store_wide(halves, 0, eight);
  for e in 0..8 { if f32(halves[e]) != f32(8 + e) { return 4; } }
  buffer pairs:f64[4] = zeroed;
  let mut pair = Array[f64, 2]();
  pair[0] = 1.5;
  pair[1] = -2.5;
  store_wide(pairs, 2, pair, Cache.l2);
  if pairs[2] != 1.5 || pairs[3] != -2.5 || pairs[0] != 0.0 { return 5; }
  return 0;
}
"""
)

DEVICE = (
    KERNELS.replace("[n]", "[n]@device")
    + CHECKS.replace(
        "QUADS",
        "buffer dx:f32[n]@device = zeroed;\n  buffer dout:f32[n]@device = zeroed;\n  transfer(dx, x);\n"
        "  quads(m, n, dout, dx);\n  transfer(out, dout);",
    ).replace(
        "TILES",
        "buffer da:u32[k]@device = zeroed;\n  buffer db:u32[k]@device = zeroed;\n  transfer(da, a);\n"
        "  tiles(g, k, db, da);\n  transfer(b, db);",
    )
    + "\nfn main() -> i32 = check(5000, 5);\n"
)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_wide_accesses_agree_with_plain_loops_everywhere_they_run(tmp_path, cxx):
    """Host code, a host region's lanes and a host block's threads, each held to plain loops; under clang++ with the
    address and undefined-behaviour sanitizers, under g++ for its behaviour alone."""
    done = contract(tmp_path, compile_source(HOST)[0], cxx, *sanitizers(cxx))
    assert done.returncode == 0, (done.returncode, done.stderr[-3000:])


def test_a_block_s_wide_accesses_to_shared_memory_race_nowhere(tmp_path):
    done = watched(tmp_path, compile_source(HOST)[0], "clang++", "thread")
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]


def test_the_thread_sanitizer_sees_the_race_a_missing_barrier_would_make(tmp_path):
    """The oracle bites: without the barrier the checker demands, thread 63 - t reads what thread t writes."""
    cpp = compile_source(HOST)[0]
    done = watched(tmp_path, cpp.replace("cr_blk.sync();", "", 1), "clang++", "thread")
    assert done.returncode != 0 and "ThreadSanitizer: data race" in done.stderr, done.stderr[-3000:]


PAST = """fn main() -> i32 {
  buffer x:f32[6] = zeroed;
  let v = load_wide[4](x, 4);                    // x[4 .. 8] runs past x's six elements
  if v[0] > 1.0 { return 1; }
  return 0;
}
"""

OFF = """fn first(n:usize, x:ro<f32>[n]) -> f32 {
  let v = load_wide[4](x, 0);                    // x[0] is 4 bytes past a 16-byte boundary
  return v[0];
}

fn main() -> i32 {
  buffer x:f32[9] = zeroed;
  if first(8, x[1..9]) > 1.0 { return 1; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("source", [PAST, OFF], ids=["past-the-end", "off-its-width"])
def test_each_guard_traps_before_the_access_reaches_memory(tmp_path, cxx, source):
    """A heap buffer sits on 8 bytes at least, so x[1] sits 4 or 12 bytes past 16. The address sanitizer watches, and
    stays silent: the guard aborts first."""
    done = contract(tmp_path, compile_source(source)[0], cxx, *sanitizers(cxx))
    assert done.returncode in {-6, 134}, (done.returncode, done.stderr[-2000:])
    assert "AddressSanitizer" not in done.stderr and "runtime error" not in done.stderr


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_device_program_runs_emulated_on_host_threads_and_agrees(tmp_path, cxx):
    ran_emulated(tmp_path, compile_source(DEVICE)[0], cxx)


def test_the_device_lowering_is_one_128_bit_instruction_an_access_and_no_local_memory(tmp_path):
    """Compiled for sm_120 and read back with cuobjdump: .cs is the evict-first operator (EF), .cg a load that skips
    L1, the shared array's accesses LDS.128 and STS.128. Nothing is spilled or kept in local memory."""
    source = KERNELS.replace("[n]", "[n]@device") + READ_ONLY
    sass, _ = assembled(tmp_path, compile_source(source)[0])
    for wanted in ("LDG.E.EF.128", "STG.E.EF.128", "LDS.128", "STS.128", "LDG.E.128.CONSTANT"):
        assert wanted in sass, wanted
    assert not re.search(r"\b(LDL|STL)\b", sass)


READ_ONLY = """
fn doubled(m:usize, n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in m {
    let v = load_wide[4](x, 4 * i, Cache.read_only);
    let mut w = Array[f32, 4]();
    for k in 0..4 { w[k] = v[k] + v[k]; }
    store_wide(out, 4 * i, w);
  }
}
"""


def test_the_row_and_the_receipt_say_what_a_wide_access_costs():
    receipt = compile_source(KERNELS)[1]["functions"]
    assert {"read:x", "write:out", "trap", "par:host"} <= set(receipt["quads"]["effects"])
    assert receipt["quads"]["syntactic_check_sites"]["wide"] == 2
    assert receipt["tiles"]["syntactic_check_sites"]["wide"] == 4


def test_cairn_explain_names_each_access_s_width_and_cache_operator():
    found = explain(KERNELS, cxx="g++")["functions"]
    loads = [w for w in found["quads"]["wide"] if w["operation"] == "load_wide"]
    assert loads == [{"at": "program.cairn:4", "operation": "load_wide", "array": "x", "elements": 4, "bytes": 16,
                      "device": "one 16-byte access, .cs (streaming)", "host": "4 ordinary loads, no hint"}]  # fmt: skip
    assert {w["device"] for w in found["tiles"]["wide"]} == {
        "one 16-byte access, .cg (l2)",
        "one 16-byte shared-memory access",
        "one 16-byte access, .cs (streaming)",
    }


def test_the_canonical_projection_compiles_to_the_same_code():
    round_trips(HOST)


LANE = "fn f(n:usize, x:ro<f32>[n]@device, out:rw<f32>[n]@device) {\n  parallel i in n / 4 {\n    BODY\n  }\n}\n"
BLOCK = "fn f(g:usize, n:usize, x:ro<u32>[n]@device, out:rw<u32>[n]@device) {\n  blocks b in g threads t in 64 {\n    BODY\n  }\n}\n"


@pytest.mark.parametrize(
    ("code", "source", "said"),
    [
        ("E-WIDE", LANE.replace("BODY", "let v = load_wide[8](x, 8 * i);"), "f32 takes 1 to 4, and 8"),
        ("E-WIDE", LANE.replace("BODY", "let v = load_wide[3](x, 3 * i);"), "3 is not one of them"),
        ("E-WIDE", LANE.replace("BODY", "let v = load_wide(x, 4 * i);"), "K a constant"),
        ("E-WIDE", LANE.replace("BODY", 'let v = load_wide[4](x, 4 * i, "cs");'), "written by name"),
        (
            "E-WIDE",
            LANE.replace("BODY", "let v = load_wide[4](x, 4 * i);\n    store_wide(out, 4 * i, v, Cache.last_use);"),
            "no last_use store",
        ),
        (
            "E-WIDE",
            LANE.replace("BODY", "let v = load_wide[4](out, 4 * i, Cache.read_only);\n    store_wide(out, 4 * i, v);"),
            "an ro view of device memory",
        ),
        (
            "E-WIDE",
            BLOCK.replace("BODY", "shared s:u32[256] = zeroed;\n    let v = load_wide[4](s, 4 * t, Cache.streaming);"),
            "no caches to hint",
        ),
        ("E-WIDE", "struct P { a:u32; }\nfn f(n:usize, x:ro<P>[n]) { let v = load_wide[2](x, 0); }", "P is not one"),
        ("E-WIDE", "fn f(n:usize, x:rw<f32>[n], v:u32) { store_wide(x, 0, v); }", "stores an Array"),
        ("E-TYPE-MISMATCH", "fn f(n:usize, x:rw<f32>[n]) { store_wide(x, 0, Array[u32, 4]()); }", "Array of f32"),
        ("E-WRITE-LEASE", "fn f(n:usize, x:ro<f32>[n]) { store_wide(x, 0, load_wide[4](x, 0)); }", "read-only"),
        ("E-ARITY", "fn f(n:usize, x:ro<f32>[n]) { let v = load_wide[4](x); }", "an index"),
        (
            "E-PLACEMENT",
            "fn f(n:usize, x:ro<f32>[n]@device) -> f32 { let v = load_wide[4](x, 0); return v[0]; }",
            "not addressable from host",
        ),
        (
            "E-PARALLEL-RACE",
            LANE.replace("BODY", "let v = load_wide[4](x, 4 * i);\n    store_wide(out, 2 * i, v);"),
            "its own block",
        ),
        (
            "E-PARALLEL-RACE",
            LANE.replace("BODY", "let v = load_wide[4](out, 4 * i + 4);\n    out[4 * i] = v[0];"),
            "its own block",
        ),
        (
            "E-COOP-GLOBAL",
            BLOCK.replace(
                "BODY", "let v = load_wide[4](x, 4 * (b * 64 + t));\n    store_wide(out, 2 * (b * 64 + t), v);"
            ),
            "not shown to exceed",
        ),
        (
            "E-COOP-CONFLICT",
            BLOCK.replace("BODY", "shared s:u32[256] = zeroed;\n    store_wide(s, 2 * t, load_wide[4](x, 0));"),
            "written by thread t = 0 and by thread t = 1",
        ),
        (
            "E-COOP-UNORDERED",
            BLOCK.replace(
                "BODY",
                "shared s:u32[256] = zeroed;\n    store_wide(s, 4 * t, load_wide[4](x, 0));\n"
                "    let w = load_wide[4](s, 4 * (63 - t));",
            ),
            "Put a barrier",
        ),
    ],
)
def test_every_wide_rule_refuses_with_its_code(code, source, said):
    assert said in refused(code, source)["message"]

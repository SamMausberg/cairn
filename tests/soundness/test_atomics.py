"""Atomic updates of one element, `atomic_add_wrap(x[i], v)` and its kin (compiler/primitives/atomics.py): from host code, host
and device lanes, and cooperative threads, relaxed, each returning the element's old value.

Every rule has a rejection naming its code. The updates run on host lanes and host threads under both compilers,
held to plain loops, with the address and undefined-behaviour sanitizers under clang++; under the thread sanitizer
they are atomics, and the same program with one update made a plain read and write is reported, so the oracle bites.
A float sum lands within its stated bound of the exact one. The device program runs emulated on host threads and, under
`make gpu` alone, on the device, and its lowering compiles for sm_120 to RED, ATOMG and ATOMS, read back with cuobjdump.
"""

import re

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import assembled, contract, ran_emulated, ran_on_device, refused, round_trips, sanitizers, watched

KERNELS = """// Each lane adds its value's low byte to one of 256 bins, and folds it into running totals, extremes and masks.
fn tally(n:usize, x:ro<u32>[n], bins:rw<u32>[256], sum:rw<u64>[1], total:rw<f32>[1], ends:rw<i64>[2],
         masks:rw<u32>[3]) {
  parallel i in n {
    let v = x[i];
    atomic_add_wrap(bins[usize(v & 255)], 1);
    atomic_add_wrap(sum[0], u64(v));
    atomic_add_unordered(total[0], f32(v % 64));
    let low = atomic_min(ends[0], i64(v) - 500000);
    let high = atomic_max(ends[1], i64(v) - 500000);
    atomic_and(masks[0], v | 1);
    atomic_or(masks[1], v);
    atomic_xor(masks[2], v);
  }
}

// Each lane claims slot i % 8 when it is still free: exactly one lane gets each slot, and every lane learns whether it
// was that one from the old value it gets back.
fn claim(n:usize, owner:rw<usize>[8], won:rw<u32>[n]) {
  parallel i in n {
    let old = atomic_cas(owner[i % 8], 0, i + 1);
    if old == 0 { won[i] = 1; } else { won[i] = 0; }
  }
}

// Each block counts its elements' low bytes in shared memory, and after a barrier adds its counts to the grid's.
fn blocked(g:usize, n:usize, x:ro<u32>[n], bins:rw<u32>[256]) {
  blocks b in g threads t in 256 {
    shared local:u32[256] = zeroed;
    let i = b * 256 + t;
    if i < n { atomic_add_wrap(local[usize(x[i] & 255)], 1); }
    barrier;
    if local[t] > 0 { atomic_add_wrap(bins[t], local[t]); }
  }
}
"""

CHECKS = """
fn check(n:usize, g:usize) -> i32 {
  buffer x:u32[n] = zeroed;
  for i in 0..n { x[i] = u32(mul_wrap(u64(i), 2654435761) % 1000003); }
  buffer bins:u32[256] = zeroed;
  buffer sum:u64[1] = zeroed;
  buffer total:f32[1] = zeroed;
  buffer ends:i64[2] = zeroed;
  buffer masks:u32[3] = zeroed;
  masks[0] = 4294967295;
  ends[0] = 1000000;
  ends[1] = -1000000;
  TALLY
  let mut want_sum:u64 = 0;
  let mut want_total:f64 = 0.0;
  let mut low:i64 = 1000000;
  let mut high:i64 = -1000000;
  let mut all:u32 = 4294967295;
  let mut any:u32 = 0;
  let mut odd:u32 = 0;
  buffer want:u32[256] = zeroed;
  for i in 0..n {
    let v = x[i];
    want[usize(v & 255)] += 1;
    want_sum += u64(v);
    want_total += f64(v % 64);
    low = min(low, i64(v) - 500000);
    high = max(high, i64(v) - 500000);
    all = all & (v | 1);
    any = any | v;
    odd = odd ^ v;
  }
  for k in 0..256 { if bins[k] != want[k] { return 1; } }
  if sum[0] != want_sum || f64(total[0]) != want_total { return 2; }
  if ends[0] != low || ends[1] != high || masks[0] != all || masks[1] != any || masks[2] != odd { return 3; }
  buffer owner:usize[8] = zeroed;
  buffer won:u32[n] = zeroed;
  CLAIM
  for k in 0..8 {
    let first = owner[k];
    if first == 0 || first > n || (first - 1) % 8 != k || won[first - 1] != 1 { return 4; }
  }
  let mut winners:u32 = 0;
  for i in 0..n { winners += won[i]; }
  if winners != 8 { return 5; }
  buffer counted:u32[256] = zeroed;
  BLOCKED
  for k in 0..256 { if counted[k] != want[k] { return 6; } }
  return 0;
}
"""

HOST = (
    KERNELS
    + CHECKS.replace("TALLY", "tally(n, x, bins, sum, total, ends, masks);")
    .replace("CLAIM", "claim(n, owner, won);")
    .replace("BLOCKED", "blocked(g, n, x, counted);")
    + "\nfn main() -> i32 = check(100000, 400);\n"
)

DEVICE = (
    KERNELS.replace("]) {", "]@device) {")
    .replace("x:ro<u32>[n], bins", "x:ro<u32>[n]@device, bins")
    .replace(
        "bins:rw<u32>[256], sum:rw<u64>[1], total:rw<f32>[1], ends:rw<i64>[2]",
        "bins:rw<u32>[256]@device, sum:rw<u64>[1]@device, total:rw<f32>[1]@device, ends:rw<i64>[2]@device",
    )
    .replace("owner:rw<usize>[8], won", "owner:rw<usize>[8]@device, won")
    .replace("x:ro<u32>[n], bins:rw<u32>[256]@device", "x:ro<u32>[n]@device, bins:rw<u32>[256]@device")
    + CHECKS.replace(
        "TALLY",
        "buffer dx:u32[n]@device = zeroed;\n  transfer(dx, x);\n  buffer dbins:u32[256]@device = zeroed;\n"
        "  buffer dsum:u64[1]@device = zeroed;\n  buffer dtotal:f32[1]@device = zeroed;\n"
        "  buffer dends:i64[2]@device = zeroed;\n  buffer dmasks:u32[3]@device = zeroed;\n  transfer(dends, ends);\n"
        "  transfer(dmasks, masks);\n  tally(n, dx, dbins, dsum, dtotal, dends, dmasks);\n  transfer(bins, dbins);\n"
        "  transfer(sum, dsum);\n  transfer(total, dtotal);\n  transfer(ends, dends);\n  transfer(masks, dmasks);",
    )
    .replace(
        "CLAIM",
        "buffer downer:usize[8]@device = zeroed;\n  buffer dwon:u32[n]@device = zeroed;\n"
        "  claim(n, downer, dwon);\n  transfer(owner, downer);\n  transfer(won, dwon);",
    )
    .replace(
        "BLOCKED",
        "buffer dcounted:u32[256]@device = zeroed;\n  blocked(g, n, dx, dcounted);\n  transfer(counted, dcounted);",
    )
    + "\nfn main() -> i32 = check(100000, 400);\n"
)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_atomic_updates_agree_with_plain_loops_on_host_lanes_and_threads(tmp_path, cxx):
    """A hundred thousand lanes on the lane pool and 400 blocks of real threads; the float sum is of whole numbers
    below 2^24, which every order adds exactly."""
    done = contract(tmp_path, compile_source(HOST)[0], cxx, *sanitizers(cxx))
    assert done.returncode == 0, (done.returncode, done.stderr[-3000:])


def test_the_thread_sanitizer_sees_atomics_where_the_program_has_them(tmp_path):
    done = watched(tmp_path, compile_source(HOST)[0], "clang++", "thread")
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]


PLAIN = """template<class T> T plain_add(T* p, std::size_t i, std::size_t n, T v) noexcept {
  if(i >= n) cr::trap();
  const T old = p[i];
  p[i] = static_cast<T>(old + v);
  return old;
}
"""


def test_the_thread_sanitizer_reports_an_update_made_plain(tmp_path):
    """The oracle bites: with the bins' update a plain read and write, two lanes race on one bin."""
    cpp = compile_source(HOST)[0]
    head, _, rest = cpp.partition("\nvoid ")
    cpp = head + "\n" + PLAIN + "void " + rest.replace("cr::atomic::add_wrap(v_bins,", "plain_add(v_bins,", 1)
    done = watched(tmp_path, cpp, "clang++", "thread")
    assert done.returncode != 0 and "ThreadSanitizer: data race" in done.stderr, done.stderr[-3000:]


FLOATS = """fn spread(n:usize, x:ro<f64>[n], total:rw<f64>[1], narrow:rw<f32>[1]) {
  parallel i in n {
    atomic_add_unordered(total[0], x[i]);
    atomic_add_unordered(narrow[0], f32(x[i]));
  }
}

fn main() -> i32 {
  let n:usize = 200000;
  buffer x:f64[n] = zeroed;
  for i in 0..n { x[i] = f64(mul_wrap(u64(i), 2654435761) % 1000003) / 1000.0 - 400.0; }
  buffer total:f64[1] = zeroed;
  buffer narrow:f32[1] = zeroed;
  spread(n, x, total, narrow);
  let mut exact32:f64 = 0.0;
  let mut size32:f64 = 0.0;
  let mut exact64:f64 = 0.0;
  let mut size64:f64 = 0.0;
  for i in 0..n {
    exact32 += f64(f32(x[i]));
    size32 += abs(f64(f32(x[i])));
    exact64 += x[i];
    size64 += abs(x[i]);
  }
  let k = f64(n);
  if abs(f64(narrow[0]) - exact32) > k * size32 / 8388608.0 { return 1; }       // k * 2^-23 * sum |v|
  if abs(total[0] - exact64) > k * size64 / 4503599627370496.0 { return 2; }    // two orders, each within k * 2^-53
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_an_unordered_float_sum_lands_within_its_stated_bound(tmp_path, cxx):
    """Two hundred thousand fractions added by lanes in whatever order they arrive, each sum held to the plain loop's:
    the f32 one within the contract's k * 2^-23 of the sum of magnitudes, and the f64 one within twice k * 2^-53, since
    the plain loop rounds too."""
    done = contract(tmp_path, compile_source(FLOATS)[0], cxx, *sanitizers(cxx))
    assert done.returncode == 0, (done.returncode, done.stderr[-3000:])


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_device_program_runs_emulated_on_host_threads_and_agrees(tmp_path, cxx):
    ran_emulated(tmp_path, compile_source(DEVICE)[0], cxx)


def test_the_device_program_agrees_on_the_device(tmp_path):
    ran_on_device(tmp_path, compile_source(DEVICE)[0])


def test_the_device_lowering_is_one_atomic_instruction_an_update(tmp_path):
    """Compiled for sm_120 and read back with cuobjdump: RED where the old value goes unused, ATOMG where it is used,
    ATOMS on shared memory, and REDG.E.ADD.F32.FTZ for the f32 add, whose flush the contract states."""
    sass, _ = assembled(tmp_path, compile_source(DEVICE.split("\nfn check(")[0])[0])
    for wanted in ("REDG.E.ADD.F32.FTZ.RN", "REDG.E.MIN.S64", "REDG.E.MAX.S64", "ATOMG.E.CAS.64", "ATOMS."):
        assert wanted in sass, wanted
    assert not re.search(r"\b(LDL|STL)\b", sass)


def test_the_row_and_the_receipt_say_what_an_atomic_costs_and_rounds():
    functions = compile_source(KERNELS)[1]["functions"]
    assert {"atomic", "read:bins", "write:bins", "trap", "par:host"} <= set(functions["tally"]["effects"])
    (added,) = functions["tally"]["numerics"]
    assert added["op"] == "atomic_add" and added["rounding"] == "unordered-f32"
    assert added["subnormals"] == "flushed-on-device" and "2^-125" in added["bound"]


def test_the_canonical_projection_compiles_to_the_same_code():
    round_trips(HOST)


LANE = "fn f(n:usize, x:ro<u32>[n]@device, out:rw<u32>[n]@device) {\n  parallel i in n {\n    BODY\n  }\n}\n"
BLOCK = "fn f(g:usize, n:usize, x:ro<u32>[n]@device, out:rw<u32>[n]@device) {\n  blocks b in g threads t in 64 {\n    BODY\n  }\n}\n"


@pytest.mark.parametrize(
    ("code", "source", "said"),
    [
        ("E-ATOMIC", LANE.replace("BODY", "let v:u32 = 0;\n    atomic_add_wrap(v, 1);"), "written in place"),
        ("E-ATOMIC", "fn f(n:usize, out:rw<i32>[n]) { atomic_add_wrap(out[0], 1); }", "on an unsigned one"),
        ("E-ATOMIC", "fn f(n:usize, out:rw<f32>[n]) { atomic_add_wrap(out[0], 1.0); }", "on an unsigned one"),
        ("E-ATOMIC", "fn f(n:usize, out:rw<u8>[n]) { atomic_max(out[0], 1); }", "u8"),
        ("E-ATOMIC", "fn f(n:usize, out:rw<i64>[n]) { atomic_or(out[0], 1); }", "i64"),
        ("E-ATOMIC", "fn f(n:usize, out:rw<u32>[n]) { atomic_add_unordered(out[0], 1); }", "f32, f64"),
        ("E-ARITY", "fn f(n:usize, out:rw<u32>[n]) { atomic_cas(out[0], 1); }", "two values"),
        ("E-TYPE-MISMATCH", "fn f(n:usize, out:rw<u32>[n], v:u64) { atomic_add_wrap(out[0], v); }", "Expected u32"),
        ("E-WRITE-LEASE", "fn f(n:usize, out:ro<u32>[n]) { atomic_add_wrap(out[0], 1); }", "read-only"),
        (
            "E-PLACEMENT",
            "fn f(n:usize, x:ro<u32>[n]@device, h:rw<u32>[n]) { parallel i in n { atomic_add_wrap(h[0], x[i]); } }",
            "not addressable",
        ),
        (
            "E-EFFECT-ORDER",
            "fn f(n:usize, out:rw<u32>[n]) -> u32 { let s = atomic_add_wrap(out[0], 1) + 1; return s; }",
            "own statement",
        ),
        ("E-EFFECT-CEILING", "fn f(n:usize, out:rw<u32>[n]) pure { atomic_add_wrap(out[0], 1); }", "exceeds"),
        (
            "E-ATOMIC-MIXED",
            LANE.replace("BODY", "atomic_add_wrap(out[usize(x[i]) % n], 1);\n    out[i] = 0;"),
            "same region",
        ),
        ("E-ATOMIC-MIXED", LANE.replace("BODY", "let v = out[i];\n    atomic_max(out[0], x[i]);"), "same region"),
        (
            "E-ATOMIC-MIXED",
            BLOCK.replace("BODY", "atomic_add_wrap(out[0], 1);\n    if t == 0 { out[b + 1] = 2; }"),
            "by the region's threads",
        ),
        (
            "E-ATOMIC-MIXED",
            BLOCK.replace(
                "BODY", "shared s:u32[64] = zeroed;\n    atomic_add_wrap(s[(t + 1) % 64], 1);\n    let v = s[t];"
            ),
            "reads or writes it plainly",
        ),
        (
            "E-ATOMIC-MIXED",
            BLOCK.replace(
                "BODY", "shared s:u32[64] = zeroed;\n    atomic_add_wrap(s[usize(x[t]) % 64], 1);\n    s[t] = 0;"
            ),
            "cannot tell from it",
        ),
        (
            "E-COOP-BARRIER",
            BLOCK.replace("BODY", "let k = atomic_add_wrap(out[0], 1);\n    if k == 0 { barrier; }"),
            "thread to thread",
        ),
    ],
)
def test_every_atomic_rule_refuses_with_its_code(code, source, said):
    assert said in refused(code, source)["message"]


def test_updates_of_one_element_by_many_threads_are_accepted_where_plain_writes_are_not():
    """The class of their own: every lane or thread may update bin 0 atomically, where a plain write races."""
    compile_source(LANE.replace("BODY", "atomic_add_wrap(out[0], x[i]);"))
    compile_source(BLOCK.replace("BODY", "shared s:u32[64] = zeroed;\n    atomic_add_wrap(s[0], 1);\n    barrier;\n"
                                 "    if t == 0 { atomic_add_wrap(out[0], s[0]); }"))  # fmt: skip
    refused("E-PARALLEL-RACE", LANE.replace("BODY", "out[0] = x[i];"))

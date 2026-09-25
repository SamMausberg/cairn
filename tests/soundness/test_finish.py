"""A cooperative region's finish, `blocks ... { } then threads t in T { }` (compiler/cooperative/finish.py): one block that runs
once, after every block of the region, and sees everything they wrote.

Every rule has a rejection naming its code. A one-pass reduction runs on host threads under both compilers, for grids
of no blocks to seventy, held to a plain loop, and is clean under the thread sanitizer; run with each finish before its
blocks, it fails its own checks, so the oracle bites. The device program runs emulated on host threads, and
its device lowering, one launch whose last block finishes, compiles for sm_120, read back with cuobjdump. Nothing runs
on a GPU.
"""

import re

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import assembled, contract, emulated, refused, round_trips, sanitizers, watched

KERNELS = """// The sum of x in one region: each block adds a grid-stride share into partial[b], and the finish adds the partials.
fn total(n:usize, x:ro<u64>[n], g:usize, partial:rw<u64>[g], out:rw<u64>[1]) {
  blocks b in g threads t in 64 {
    shared warps:u64[2] = zeroed;
    let mut sum:u64 = 0;
    let mut i = b * 64 + t;
    while i < n {
      sum = add_wrap(sum, x[i]);
      i += g * 64;
    }
    let w = reduce add_wrap warp yield sum;
    if t % 32 == 0 { warps[t / 32] = w; }
    barrier;
    if t == 0 { partial[b] = add_wrap(warps[0], warps[1]); }
  } then threads t in 64 {
    shared warps:u64[2] = zeroed;
    let mut sum:u64 = 0;
    let mut k = t;
    while k < g {
      sum = add_wrap(sum, partial[k]);
      k += 64;
    }
    let w = reduce add_wrap warp yield sum;
    if t % 32 == 0 { warps[t / 32] = w; }
    barrier;
    if t == 0 { out[0] = add_wrap(warps[0], warps[1]); }
  }
}

// Bins counted atomically by every block, then read plainly by the finish, which no block of the region may do.
fn largest_bin(n:usize, x:ro<u32>[n], g:usize, bins:rw<u32>[64], out:rw<u32>[1]) {
  blocks b in g threads t in 32 {
    let mut i = b * 32 + t;
    while i < n {
      atomic_add_wrap(bins[usize(x[i] % 64)], 1);
      i += g * 32;
    }
  } then threads t in 32 {
    let most = reduce max warp yield max(bins[t], bins[t + 32]);
    if t == 0 { out[0] = most; }
  }
}
"""

CHECKS = """
fn check(n:usize, g:usize) -> i32 {
  buffer x:u64[n] = zeroed;
  buffer small:u32[n] = zeroed;
  for i in 0..n {
    x[i] = mul_wrap(u64(i), 2654435761);
    small[i] = u32(i % 1000);
  }
  buffer partial:u64[g] = zeroed;
  buffer out:u64[1] = zeroed;
  buffer bins:u32[64] = zeroed;
  buffer most:u32[1] = zeroed;
  out[0] = 7;
  most[0] = 7;
  RUN
  let mut want:u64 = 0;
  for i in 0..n { want = add_wrap(want, x[i]); }
  if g > 0 && out[0] != want { return 1; }
  if g == 0 && out[0] != 0 { return 4; }                  // no block ran: the finish added no partials
  buffer counts:u32[64] = zeroed;
  for i in 0..n { counts[usize(small[i] % 64)] += 1; }
  let mut top:u32 = 0;
  for k in 0..64 { top = max(top, counts[k]); }
  if g > 0 && most[0] != top { return 2; }
  if g == 0 && most[0] != 0 { return 3; }                // no block ran: the finish read the bins as they were
  return 0;
}

fn main() -> i32 {
  buffer sizes:usize[4] = zeroed;
  buffer grids:usize[6] = zeroed;
  sizes[1] = 1;
  sizes[2] = 1000;
  sizes[3] = 100000;
  grids[1] = 1;
  grids[2] = 2;
  grids[3] = 3;
  grids[4] = 7;
  grids[5] = 70;
  for a in 0..4 {
    for c in 0..6 {
      let answer = check(sizes[a], grids[c]);
      if answer != 0 { return answer; }
    }
  }
  return 0;
}
"""


def program(device: bool) -> str:
    if not device:
        run = "total(n, x, g, partial, out);\n  largest_bin(n, small, g, bins, most);"
        return KERNELS + CHECKS.replace("RUN", run)
    kernels = KERNELS.replace("[n], g", "[n]@device, g").replace(
        "[g], out:rw<u64>[1]", "[g]@device, out:rw<u64>[1]@device"
    )
    kernels = kernels.replace("bins:rw<u32>[64], out:rw<u32>[1]", "bins:rw<u32>[64]@device, out:rw<u32>[1]@device")
    run = """buffer dx:u64[n]@device = zeroed;
  buffer dsmall:u32[n]@device = zeroed;
  buffer dpartial:u64[g]@device = zeroed;
  buffer dout:u64[1]@device = zeroed;
  buffer dbins:u32[64]@device = zeroed;
  buffer dmost:u32[1]@device = zeroed;
  transfer(dx, x);
  transfer(dsmall, small);
  transfer(dout, out);
  transfer(dmost, most);
  total(n, dx, g, dpartial, dout);
  largest_bin(n, dsmall, g, dbins, dmost);
  transfer(out, dout);
  transfer(most, dmost);"""
    return kernels + CHECKS.replace("RUN", run)


HOST, DEVICE = program(False), program(True)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_one_pass_reduction_agrees_with_a_plain_loop_for_every_grid(tmp_path, cxx):
    """Grids of 0 to 70 blocks over 0 to 100000 elements; with no block the finish still runs once, and writes the
    sum of nothing."""
    done = contract(tmp_path, compile_source(HOST)[0], cxx, *sanitizers(cxx))
    assert done.returncode == 0, (done.returncode, done.stderr[-3000:])


def test_the_finish_reads_what_every_block_wrote_without_a_race(tmp_path):
    done = watched(tmp_path, compile_source(HOST)[0], "clang++", "thread")
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]


BEFORE = """namespace cr::coop {
template<unsigned THREADS, std::size_t BYTES, class F, class G> void before(std::size_t grid, F body, G finish) noexcept {
  run<THREADS, BYTES>(1, finish);
  run<THREADS, BYTES>(grid, body);
}
}  // namespace cr::coop
"""


def test_a_finish_run_before_the_blocks_it_waits_for_reads_what_is_not_there_yet(tmp_path):
    """The oracle bites: with the emitted C++ running each finish before its blocks, which the lowering never does, the
    finish reads partial sums and bins no block has written, and the program's own checks fail. (A finish started
    beside its blocks is a race the thread sanitizer reports only when both run at once, which a busy machine does
    not always arrange, so the order is reversed here instead.)"""
    cpp = compile_source(HOST)[0]
    head, _, rest = cpp.partition("\nvoid ")
    cpp = head + "\n" + BEFORE + "void " + rest.replace("cr::coop::run_then<", "cr::coop::before<")
    done = contract(tmp_path, cpp, "clang++")
    assert done.returncode in {1, 2}, (done.returncode, done.stderr[-2000:])


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_device_program_runs_emulated_on_host_threads_and_agrees(tmp_path, cxx):
    emulated(tmp_path, compile_source(DEVICE)[0], cxx)


def test_the_device_lowering_is_one_launch_whose_last_block_finishes(tmp_path):
    """Compiled for sm_120 and read back with cuobjdump: one kernel for the region and its finish, a block counted by
    a compare-and-swap that claims its launch's word and an add, between two device-wide fences, each also
    invalidating L1 so the finish reads what other SMs wrote. Nothing is kept in local memory."""
    kernels = DEVICE.split("\nfn check(")[0]
    cpp = compile_source(kernels)[0]
    assert cpp.count("cr::coop::launch_then<64, 128>") == 1 and cpp.count("cr::coop::launch_then<32, 0>") == 1
    sass, _ = assembled(tmp_path, cpp)
    kernel = sass.split("blocks_then")[2]  # the first region's kernel
    assert kernel.count("MEMBAR.SC.GPU") == 2 and "CCTL.IVALL" in kernel
    assert "ATOMG.E.CAS.64" in kernel and "ATOMG.E.ADD.64" in kernel
    assert not re.search(r"\b(LDL|STL)\b", sass)


def test_a_finish_is_one_region_in_the_receipt_and_its_arrays_its_own():
    functions = compile_source(KERNELS)[1]["functions"]
    assert functions["total"]["syntactic_check_sites"]["cooperative_regions"] == 1
    kinds = [x["kind"] for x in functions["total"]["local_storage"]]
    assert kinds.count("blocks") == 1 and kinds.count("shared") == 2
    assert {"par:host", "atomic"} <= set(functions["largest_bin"]["effects"])


def test_the_canonical_projection_compiles_to_the_same_code():
    canonical = round_trips(HOST)
    assert "} then threads t in 64 {" in canonical


REGION = "fn f(g:usize, n:usize, x:ro<u64>[n], partial:rw<u64>[g], out:rw<u64>[4]) {\n  blocks b in g threads t in 64 {\n    if t == 0 { partial[b] = 1; }\n  } then threads FINISH {\n    BODY\n  }\n}\n"


@pytest.mark.parametrize(
    ("code", "finish", "body", "said"),
    [
        ("E-COOP-SHAPE", "t in 32", "let v = t;", "the region's 64 threads"),
        ("E-COOP-SHAPE", "t in 48", "let v = t;", "whole warps"),
        ("E-UNBOUND", "t in 64", "let v = b;", "b"),
        ("E-COOP-GLOBAL", "t in 64", "out[0] = u64(t);", "every t writes the same element"),
        ("E-COOP-BARRIER", "t in 64", "if t < 5 { barrier; }", "every thread of the block"),
        ("E-COOP-UNORDERED", "t in 64", "shared s:u64[64] = zeroed;\n    s[t] = 1;\n    let v = s[63 - t];", "barrier"),
        ("E-PARALLEL-WRITE", "t in 64", "n = 3;", "Every lane would write"),
    ],
)
def test_every_finish_rule_refuses_with_its_code(code, finish, body, said):
    source = REGION.replace("FINISH", finish).replace("BODY", body)
    if code == "E-PARALLEL-WRITE":
        source = source.replace("n:usize, x:ro<u64>[n]", "m:usize, x:ro<u64>[m]").replace(
            "  blocks", "  let mut n:usize = 0;\n  blocks"
        )
    assert said in refused(code, source)["message"]


def test_a_host_region_s_finish_reaches_no_device_memory():
    source = "fn f(g:usize, h:rw<u64>[g], d:rw<u64>[1]@device) {\n  blocks b in g threads t in 32 {\n"
    source += "    if t == 0 { h[b] = 1; }\n  } then threads t in 32 {\n    if t == 0 { d[0] = 2; }\n  }\n}\n"
    assert "run on the host" in refused("E-PLACEMENT", source)["message"]


def test_a_finish_s_names_are_its_own_and_the_region_s_locals_end_before_it():
    source = REGION.replace("FINISH", "u, v in 32, 2").replace("BODY", "if u == 0 && v == 0 { out[0] = partial[0]; }")
    compile_source(source)  # 32 x 2 threads is the region's 64, named its own way
    local = REGION.replace("if t == 0 { partial[b] = 1; }", "let kept = 4;\n    if t == 0 { partial[b] = kept; }")
    refused("E-UNBOUND", local.replace("FINISH", "t in 64").replace("BODY", "let v = kept;"))

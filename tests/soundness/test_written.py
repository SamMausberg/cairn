"""Shared arrays with no zero fill, `shared s:f32[256];` (compiler/cooperative/written.py): accepted when every element a thread
reads was written first, by that thread earlier or by any thread before a barrier between them.

Every rule has a rejection naming E-COOP-UNWRITTEN. A block reduction and a transpose that keep their shared arrays
unzeroed run on host threads under both compilers, held to plain loops, clean under the thread sanitizer. On the host
an unzeroed array starts each block filled with a pattern, and the same program with one write taken out of the
emitted C++ reads it and fails, so the oracle bites. The device lowering zeroes nothing, which the PTX shows; the
device program runs emulated on host threads. Nothing runs on a GPU.
"""

import re
import shutil

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import contract, device_build, emulated, refused, round_trips, sanitizers, watched

KERNELS = """// Block sums through a tree in shared memory that nobody zeroes: the first phase writes every element.
fn block_sums(n:usize, x:ro<u64>[n], g:usize, out:rw<u64>[g]) {
  blocks b in g threads t in 256 {
    shared partial:u64[256];
    let i = b * 256 + t;
    let mut v:u64 = 0;
    if i < n { v = x[i]; }
    partial[t] = v;
    barrier;
    for k in 0..3 {
      let s:usize = shr(128, k);
      if t < s { partial[t] = partial[t] + partial[t + s]; }
      barrier;
    }
    shared warps:u64[1];
    if t < 32 {
      let total = reduce + warp yield partial[t];
      if t == 0 { warps[0] = total; }
    }
    barrier;
    if t == 0 { out[b] = warps[0]; }
  }
}

// out is x transposed, x holding 32 * gy rows of 32 * gx elements, through a tile each block fills whole.
fn transpose(gx:usize, gy:usize, n:usize, out:rw<f32>[n], x:ro<f32>[n]) {
  let w = gx * 32;
  let h = gy * 32;
  blocks bx, by in gx, gy threads tx, ty in 32, 8 {
    shared tile:f32[1056];
    for k in 0..4 {
      let r = ty + k * 8;
      tile[r * 33 + tx] = x[(by * 32 + r) * w + bx * 32 + tx];
    }
    barrier;
    for k in 0..4 {
      let r = ty + k * 8;
      out[(bx * 32 + r) * h + by * 32 + tx] = tile[tx * 33 + r];
    }
  }
}
"""

CHECKS = """
fn check(n:usize, gx:usize, gy:usize) -> i32 {
  let g = (n + 255) / 256 + 1;
  buffer x:u64[n] = zeroed;
  buffer out:u64[g] = zeroed;
  for i in 0..n { x[i] = mul_wrap(u64(i), 2654435761) % 1000003; }
  SUMS
  for b in 0..g {
    let mut want:u64 = 0;
    for i in b * 256..min(b * 256 + 256, n) { want += x[i]; }
    if out[b] != want { return 1; }
  }
  let w = gx * 32;
  let h = gy * 32;
  let m = w * h;
  buffer y:f32[m] = zeroed;
  buffer t:f32[m] = zeroed;
  for i in 0..m { y[i] = f32(i % 977) - 400.0; }
  TRANSPOSE
  for r in 0..h {
    for c in 0..w { if to_bits(t[c * h + r]) != to_bits(y[r * w + c]) { return 2; } }
  }
  return 0;
}

fn main() -> i32 {
  let first = check(1000, 3, 2);
  if first != 0 { return first; }
  return check(70000, 2, 5);
}
"""

HOST = KERNELS + CHECKS.replace("SUMS", "block_sums(n, x, g, out);").replace("TRANSPOSE", "transpose(gx, gy, m, t, y);")
DEVICE = KERNELS.replace(
    "x:ro<u64>[n], g:usize, out:rw<u64>[g]", "x:ro<u64>[n]@device, g:usize, out:rw<u64>[g]@device"
).replace("out:rw<f32>[n], x:ro<f32>[n]", "out:rw<f32>[n]@device, x:ro<f32>[n]@device") + CHECKS.replace(
    "SUMS",
    "buffer dx:u64[n]@device = zeroed;\n  buffer dout:u64[g]@device = zeroed;\n  transfer(dx, x);\n"
    "  block_sums(n, dx, g, dout);\n  transfer(out, dout);",
).replace(
    "TRANSPOSE",
    "buffer dy:f32[m]@device = zeroed;\n  buffer dt:f32[m]@device = zeroed;\n  transfer(dy, y);\n"
    "  transpose(gx, gy, m, dt, dy);\n  transfer(t, dt);",
)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_unzeroed_arrays_give_the_plain_loops_answers(tmp_path, cxx):
    done = contract(tmp_path, compile_source(HOST)[0], cxx, *sanitizers(cxx))
    assert done.returncode == 0, (done.returncode, done.stderr[-3000:])


def test_their_threads_race_nowhere(tmp_path):
    done = watched(tmp_path, compile_source(HOST)[0], "clang++", "thread")
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]


def test_a_read_before_any_write_reads_the_host_s_pattern(tmp_path):
    """The oracle bites: with the first phase's write taken out of the emitted C++, which the rule would refuse, the
    tree adds the pattern the host fills an unzeroed array with: a checked sum of 0x7f7f... overflows and traps, or
    the sums come out wrong."""
    cpp = compile_source(HOST)[0]
    write = "v_partial[v_t] = v_v;"
    assert write in cpp
    done = contract(tmp_path, cpp.replace(write, "", 1), "clang++")
    assert done.returncode in {1, -6}, (done.returncode, done.stderr[-2000:])


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_device_program_runs_emulated_on_host_threads_and_agrees(tmp_path, cxx):
    emulated(tmp_path, compile_source(DEVICE)[0], cxx)


def test_the_device_kernel_zeroes_nothing_where_its_arrays_are_unzeroed(tmp_path):
    """Compiled for sm_120 to PTX, nothing run: the zeroed kernel stores a zero byte at a time into shared memory where
    each block starts, and the unzeroed one does not."""
    if not shutil.which("nvcc"):
        pytest.skip("needs nvcc")
    kernel = DEVICE.split("\n// out is x transposed")[0]
    cpp = compile_source(kernel)[0]
    assert "cr::coop::launch<256, 2176, 0>" in cpp  # 2048 bytes of partial and 128 of warps, none of them zeroed
    (tmp_path / "unzeroed").mkdir()
    (tmp_path / "zeroed").mkdir()
    unzeroed = device_build(tmp_path / "unzeroed", cpp, ptx=True).read_text()
    zeroed = compile_source(kernel.replace("[256];", "[256] = zeroed;").replace("[1];", "[1] = zeroed;"))[0]
    assert "cr::coop::launch<256, 2176>" in zeroed
    zeroed_ptx = device_build(tmp_path / "zeroed", zeroed, ptx=True).read_text()
    byte = re.compile(r"st\.shared\.[bu]8\b")  # a byte store: CUDA 13 writes it .b8, CUDA 12.9 .u8
    assert byte.search(zeroed_ptx) and not byte.search(unzeroed)


def test_the_row_loses_zero_init_and_the_receipt_says_how_each_array_starts():
    receipt = compile_source(KERNELS)[1]["functions"]["block_sums"]
    assert "zero_init" not in receipt["effects"] and "par:host" in receipt["effects"]
    shared = [x for x in receipt["local_storage"] if x["kind"] == "shared"]
    assert {x["initialization"] for x in shared} == {"written before read"}
    mixed = KERNELS.replace("shared warps:u64[1];", "shared warps:u64[1] = zeroed;")
    assert "zero_init" in compile_source(mixed)[1]["functions"]["block_sums"]["effects"]


def test_a_zeroed_array_is_laid_out_before_the_unzeroed_ones_whatever_their_order():
    mixed = compile_source(KERNELS.replace("shared warps:u64[1];", "shared warps:u64[1] = zeroed;"))[0]
    assert "cr::coop::run<256, 2176, 128>" in mixed  # warps zeroed at 0, partial after it at 128
    assert "v_warps = reinterpret_cast<std::uint64_t*>(cr_blk.shared + 0)" in mixed
    assert "v_partial = reinterpret_cast<std::uint64_t*>(cr_blk.shared + 128)" in mixed


def test_the_canonical_projection_compiles_to_the_same_code():
    canonical = round_trips(HOST)
    assert "shared partial:u64[256];" in canonical


HEAD = "fn f(g:usize, n:usize, x:ro<f32>[n], out:rw<f32>[g]) {\n  blocks b in g threads t in 256 {\n"
TAIL = "  }\n}\n"
HELPER = "fn peek(k:usize, v:rw<f32>[k]) -> f32 = v[0];\n"


@pytest.mark.parametrize(
    ("body", "said"),
    [
        (
            "shared s:f32[8];\n    if t % 32 == 0 { s[t / 32] = 1.0; }\n    if t < 8 { let v = s[t]; }\n",
            "s[1] at line 5",
        ),
        (
            "shared s:f32[256];\n    if t < 128 { s[t] = 1.0; }\n    barrier;\n    let v = s[t];\n",
            "t = 128 reads s[128]",
        ),
        (
            "shared s:f32[256];\n    for k in 0..n {\n      s[t] = 1.0;\n      barrier;\n    }\n    let v = s[t];\n",
            "s[0]",
        ),
        (
            "shared s:f32[256];\n    if t > 0 { s[t] = 1.0; }\n    barrier;\n    let v = s[usize(x[t % n]) % 256];\n",
            "cannot follow",
        ),
        ("shared s:f32[256];\n    if n > 5 { s[t] = 1.0; }\n    barrier;\n    let v = s[255 - t];\n", "s[255]"),
        ("shared s:u32[256];\n    atomic_add_wrap(s[t], 1);\n", "s[0]"),
        ("shared s:f32[256];\n    let v = s[t];\n    s[t] = v;\n", "no thread surely wrote"),
        ("shared s:f32[256];\n    if t == 0 { let v = peek(256, s); }\n", "s[0]"),
    ],
)
def test_a_read_of_an_element_nobody_surely_wrote_is_refused(body, said):
    source = (HELPER if "peek" in body else "") + HEAD + body + TAIL
    assert said in refused("E-COOP-UNWRITTEN", source)["message"]


@pytest.mark.parametrize(
    "body",
    [
        "shared s:f32[256];\n    s[t] = 1.0;\n    let v = s[t];\n",  # its own write, earlier in the phase
        "shared s:f32[256];\n    if n > 5 { s[t] = 1.0; } else { s[t] = 2.0; }\n    barrier;\n    let v = s[255 - t];\n",
        "shared s:f32[256];\n    for k in 0..n {\n      s[t] = f32(k);\n      barrier;\n      let v = s[255 - t];\n"
        "      barrier;\n    }\n",
        "shared s:f32[1024];\n    store_wide(s, 4 * t, Array[f32, 4]());\n    barrier;\n    let v = load_wide[4](s, 4 * (255 - t));\n",
        "shared s:u32[256];\n    s[t] = 0;\n    barrier;\n    atomic_add_wrap(s[(t + 1) % 256], 1);\n",
    ],
)
def test_every_way_of_writing_first_is_accepted(body):
    compile_source(HEAD + body + TAIL)

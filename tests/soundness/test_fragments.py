"""Tensor-core fragments: `WmmaA`, `WmmaB`, `WmmaAcc`, `MmaA`, `MmaB`, `MmaAcc`, `mma_load`, `mma_store` and
`mma_unordered(acc, a, b)` (compiler/fragments.py, runtime/cairn_fragment.hpp).

On the host every thread of a warp holds each fragment whole and stores only the elements its lane holds on the
device; tests/runtime/fragment_runtime.cpp runs a warp as 32 real threads under the thread sanitizer and holds every
output to the reference loop bit for bit. The device operations are compiled for sm_120 here and their SASS
inspected, never run: that the tensor cores keep the contract is `make gpu`'s question.
"""

import re
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

from cairn.compiler import layout_algebra as A
from cairn.compiler import layouts as L
from cairn.compiler.cairnc import compile_program, compile_source
from emitted import NVCC_HOST, device_build, refused, run, sanitized

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src/cairn/runtime"
NATIVE = ROOT / "tests/runtime"

TILE = "layout SA = rows(16, 16);\nlayout SW = swizzle(rows(16, 16), 1, 3, 3);\nlayout NARROW = pad(rows(16, 16), 4);\n"
FRAGMENTS = "acc:WmmaAcc[f32, 16, 16, 16], a:WmmaA[f16, 16, 16, 16], b:WmmaB[f16, 16, 16, 16]"


@pytest.mark.parametrize(
    ("code", "source", "said"),
    [
        ("E-FRAGMENT", TILE + "fn f(a:ro<f16>[256]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, SA, 0, 0); }",
         "inside a cooperative region"),
        ("E-FRAGMENT", "fn f() { let acc = WmmaAcc[f32, 16, 16, 16](0.0); }", "inside a cooperative region"),
        ("E-FRAGMENT", "fn f() { let acc = WmmaAcc[f32, 16, 8, 16](0.0); }", "shapes wmma has"),
        ("E-FRAGMENT", "fn f() { let acc = MmaAcc[f32, 16, 16, 16](0.0); }", "16 x 8 x 16"),
        ("E-FRAGMENT", "fn f() { let acc = WmmaAcc[f16, 16, 16, 16](0.0); }", "accumulate in f32"),
        ("E-FRAGMENT", TILE + "fn f(a:ro<f8e4m3>[256]@device) { let x = mma_load[WmmaA[f8e4m3, 16, 16, 16]](a, SA, 0, 0); }",
         "operands are f16 or bf16"),
        ("E-FRAGMENT", "fn f() { let a = WmmaA[f16, 16, 16, 16](0.0); }", "loaded from a tile"),
        ("E-FRAGMENT", TILE + "fn f(a:ro<f16>[256]) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, SA, 0, 0); }",
         "host memory"),
        ("E-TARGET-FEATURE", "fn f() { let acc = TmemAcc[f32, 128, 256, 16](0.0); }", "sm_100a"),
        ("E-LAYOUT-CONSUMER", TILE + "fn f(a:ro<f16>[256]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, SW, 0, 0); }",
         "no swizzle"),
        ("E-LAYOUT-CONSUMER", TILE + "fn f(a:ro<f16>[512]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, NARROW, 0, 0); }",
         "40 bytes apart"),
        ("E-LAYOUT-CONSUMER", "layout T = cols(16, 16);\nfn f(a:ro<f16>[256]@device) { let x = mma_load[WmmaB[f16, 16, 16, 16]](a, T, 0, 0); }",
         "row-major"),
        ("E-LAYOUT-CONSUMER", "layout T = rows(16, 24);\nfn f(a:ro<f16>[384]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, T, 0, 0); }",
         "whole 16 x 16 fragments"),
        ("E-LAYOUT-CONSUMER", "fn f(a:ro<f16>[256]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, a, 0, 0); }",
         "names none"),
        ("E-LAYOUT-CONSUMER", TILE + "fn f(a:ro<f16>[100]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, SA, 0, 0); }",
         "past the 100 elements"),
        ("E-LAYOUT-CONSUMER", TILE + "fn f(b:ro<f16>[256]@device) { let x = mma_load[WmmaB[f16, 32, 8, 16]](b, SA, 0, 1); }",
         "starts 16 bytes in"),
        ("E-TYPE-MISMATCH", TILE + "fn f(a:ro<bf16>[256]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, SA, 0, 0); }",
         "array of f16"),
        ("E-TYPE-MISMATCH", TILE + f"fn f(c:ro<f32>[256]@device, {FRAGMENTS}) {{ mma_store(c, SA, 0, 0, acc); }}", "an rw array"),
        ("E-TYPE-MISMATCH", TILE + f"fn f(c:rw<f32>[256]@device, {FRAGMENTS}) {{ mma_store(c, SA, 0, 0, a); }}", "accumulator"),
        ("E-MMA", "fn f(a:u32, b:u32, c:u32) { let x = mma_unordered(a, b, c); }", "multiplies fragments"),
        ("E-MMA", f"fn f({FRAGMENTS}) {{ let x = mma_unordered(a, acc, b); }}", "in that order"),
        ("E-MMA", f"fn f({FRAGMENTS.replace('WmmaB[f16', 'WmmaB[bf16')}) {{ let x = mma_unordered(acc, a, b); }}",
         "one format"),
        ("E-MMA", f"fn f({FRAGMENTS.replace('WmmaB', 'MmaB').replace('16, 16, 16], b', '16, 8, 16], b')}) "
         "{ let x = mma_unordered(acc, a, b); }", "one family"),
        ("E-INFER", TILE + "fn f(a:ro<f16>[256]@device) { let x = mma_load[u32](a, SA, 0, 0); }", "fragment type"),
        ("E-ARITY", TILE + "fn f(a:ro<f16>[256]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, SA, 0); }", "coordinates"),
    ],
)  # fmt: skip
def test_rejections(code, source, said):
    assert said in refused(code, source)["message"]


def test_the_holder_of_each_accumulator_element_is_the_spread_the_ptx_isa_states():
    """runtime/cairn_fragment.hpp's `holder` and the mma.sync accumulator's lanes are one spread: 8 x 4 lanes with two
    columns each, repeated down, so the layout rule (Layout.lean ok_one_writer) says each element has one holder."""
    _, checker, _ = compile_program("layout ACC = spread(rows(16, 8), 8, 4, 1, 2);")
    acc = L.value(checker, "ACC")
    for r in range(16):
        for c in range(8):
            ((lane, value),) = A.owner(acc, (r, c))
            g, t = lane // 4, lane % 4
            assert lane == (r % 8) * 4 + (c % 8) // 2
            assert (r, c) == (g + 8 * (value // 2), 2 * t + value % 2)  # the ISA's c0..c3 of lane (g, t)


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_a_warp_of_threads_on_the_host_equals_the_reference_and_never_races(tmp_path, cxx):
    if not shutil.which(cxx) or not shutil.which("setarch"):
        pytest.skip(f"needs {cxx} and setarch")
    exe = tmp_path / "fragment_runtime"
    line = [cxx, "-std=c++20", "-O1", "-g", "-ffp-contract=off", "-fno-fast-math", "-fsanitize=thread", "-Wall",
            "-Wextra", "-Werror", f"-I{RUNTIME}", str(NATIVE / "fragment_runtime.cpp"), "-o", str(exe)]  # fmt: skip
    built = subprocess.run(line, capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stderr[-3000:]
    done = subprocess.run(["setarch", "-R", str(exe)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stdout + done.stderr[-3000:]
    assert "every lane's fragments equal the reference" in done.stdout


def test_the_device_operations_compile_for_sm_120_to_tensor_core_instructions(tmp_path):
    """Compiled to a cubin and read back with cuobjdump; nothing runs on a device."""
    if not shutil.which("nvcc") or not shutil.which("cuobjdump"):
        pytest.skip("needs nvcc and cuobjdump")
    cubin = tmp_path / "fragment_device.cubin"
    line = ["nvcc", "-std=c++20", "-arch=sm_120", "-ccbin", NVCC_HOST, "-cubin", f"-I{RUNTIME}",
            str(NATIVE / "fragment_device.cu"), "-o", str(cubin)]  # fmt: skip
    built = subprocess.run(line, capture_output=True, text=True, timeout=600)
    assert built.returncode == 0, built.stderr[-3000:]
    sass = subprocess.run(["cuobjdump", "-sass", str(cubin)], capture_output=True, text=True, timeout=120).stdout
    found = set(re.findall(r"\b(HMMA\.16816\.F32(?:\.BF16)?|LDSM\.16\.M(?:T)?88\.[24])\b", sass))
    assert {"HMMA.16816.F32", "HMMA.16816.F32.BF16", "LDSM.16.M88.4", "LDSM.16.MT88.2"} <= found, found


BIASED = """layout TILE_A = rows(16, 16);
layout TILE_B = rows(16, 8);
layout TILE_C = rows(16, 8);
layout SHARE = spread(rows(16, 8), 8, 4, 1, 2);    // the lanes' share of an mma.sync accumulator

// One warp: out = a * b plus bias[j] in every column j, the bias added where each lane holds its elements.
fn biased(out:rw<f32>[128], a:ro<f16>[256], b:ro<f16>[128], bias:ro<f32>[8]) {
  blocks g in 1 threads t in 32 {
    shared sa:f16[256] = zeroed;
    shared sb:f16[128] = zeroed;
    shared sc:f32[128] = zeroed;
    for i in 0..8 { sa[t + 32 * i] = a[t + 32 * i]; }
    for i in 0..4 { sb[t + 32 * i] = b[t + 32 * i]; }
    barrier;
    let x = mma_load[MmaA[f16, 16, 8, 16]](sa, TILE_A, 0, 0);
    let y = mma_load[MmaB[f16, 16, 8, 16]](sb, TILE_B, 0, 0);
    let mut acc = MmaAcc[f32, 16, 8, 16](0.0);
    acc = mma_unordered(acc, x, y);
    for v in 0..4 { acc = mma_set(acc, v, mma_get(acc, v) + bias[SHARE.col(t, v)]); }
    mma_store(sc, TILE_C, 0, 0, acc);
    barrier;
    for i in 0..4 { out[t + 32 * i] = sc[t + 32 * i]; }
  }
}

fn main() -> i32 {
  buffer a:f16[256] = zeroed;
  buffer b:f16[128] = zeroed;
  buffer bias:f32[8] = zeroed;
  buffer out:f32[128] = zeroed;
  for e in 0..256 { a[e] = f16(f64(e % 7) - 3.0); }
  for e in 0..128 { b[e] = f16(f64(e % 5) - 2.0); }
  for j in 0..8 { bias[j] = f32(j) * 0.5; }
  biased(out, a, b, bias);
  for i in 0..16 {
    for j in 0..8 {
      let mut want:f32 = bias[j];
      for p in 0..16 { want = want + f32(a[i * 16 + p]) * f32(b[p * 8 + j]); }
      if out[i * 8 + j] != want { return 1; }
    }
  }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_each_lane_reaches_the_accumulator_elements_the_isa_gives_it(tmp_path, cxx):
    """mma_get and mma_set on the host, where each lane's copy is right at the elements it holds: a bias added
    through the layout of the lanes' share lands in the right column, checked against a loop written out."""
    done = run(tmp_path, compile_source(BIASED)[0], *sanitized(cxx), "-pthread", cxx=cxx)
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


def test_elementwise_access_compiles_for_sm_120_to_the_registers_it_names(tmp_path):
    if not shutil.which("nvcc"):
        pytest.skip("needs nvcc")
    kernel = BIASED[: BIASED.index("fn main")]
    head = kernel[kernel.index("fn biased") : kernel.index("{", kernel.index("fn biased"))]
    kernel = kernel.replace(head, re.sub(r"\[(\d+)\]", r"[\1]@device", head))
    kernel = kernel.replace("out[t + 32 * i] =", "out[t + 32 * i + 128 * g] =")  # the device build refuses an unused g
    device_build(tmp_path, compile_source(kernel)[0], timeout=900)


@pytest.mark.parametrize(
    ("code", "source", "said"),
    [
        ("E-FRAGMENT", "fn f(acc:WmmaAcc[f32, 16, 16, 16]) -> f32 = mma_get(acc, 0);", "unspecified"),
        ("E-FRAGMENT", "fn f(acc:MmaAcc[f32, 16, 8, 16]) -> f32 = mma_get(acc, 0);", "cooperative region"),
        ("E-TYPE-MISMATCH", f"fn f({FRAGMENTS}) -> f32 = mma_get(a, 0);", "accumulator"),
        ("E-ARITY", "fn f(acc:MmaAcc[f32, 16, 8, 16]) { let x = mma_set(acc, 0); }", "an f32"),
    ],
)  # fmt: skip
def test_elementwise_access_is_refused_where_it_is_not_defined(code, source, said):
    assert said in refused(code, source)["message"]


# A tile whose length the checker cannot know: its guard runs where the load does, before any element is read. On
# the host, unified memory is host memory, and a C++ caller passes a shorter array than the layout places.
SHORT = """layout TILE = rows(16, 16);
fn tile(n:usize, out:rw<f32>[256]@unified, a:ro<f16>[n]@unified, b:ro<f16>[256]@unified) {
  blocks g in 1 threads t in 32 {
    shared sc:f32[256] = zeroed;
    let x = mma_load[WmmaA[f16, 16, 16, 16]](a, TILE, 0, 0);
    let y = mma_load[WmmaB[f16, 16, 16, 16]](b, TILE, 0, 0);
    let acc = mma_unordered(WmmaAcc[f32, 16, 16, 16](0.0), x, y);
    mma_store(sc, TILE, 0, 0, acc);
    barrier;
    for i in 0..8 { out[t + 32 * i] = sc[t + 32 * i]; }
  }
}
"""
CALLER = """
int main() {
  float* out = new float[256]();
  cr::f16* a = new cr::f16[N]();
  cr::f16* b = new cr::f16[256]();
  cf_tile(N, out, a, b);
  delete[] out;
  delete[] a;
  delete[] b;
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
@pytest.mark.parametrize("n", [16, 255, 256])
def test_a_tile_shorter_than_its_layout_traps_before_the_load_reads_it(tmp_path, cxx, n):
    cpp = compile_source(SHORT)[0] + CALLER.replace("N", str(n))
    done = run(tmp_path, cpp, *sanitized(cxx), "-pthread", cxx=cxx, entry=None)
    want = 0 if n == 256 else -signal.SIGABRT
    assert done.returncode == want and "Sanitizer" not in done.stderr, (n, done.returncode, done.stderr[-2000:])


def test_the_tile_s_guard_compiles_into_the_device_region(tmp_path):
    """The same guard in a device lane, compiled for sm_120 and never run."""
    if not shutil.which("nvcc"):
        pytest.skip("needs nvcc")
    device_build(tmp_path, compile_source(SHORT.replace("@unified", "@device"))[0], timeout=900)


def warped(body: str, head: str = "") -> str:
    """A cooperative region of one warp with a shared A tile, a shared B tile and a shared accumulator tile."""
    return f"""layout T = rows(32, 16);
layout A = rows(16, 16);
layout T8 = rows(32, 8);
{head}fn k(out:rw<f32>[512], x:ro<f16>[512]) {{
  blocks g in 1 threads t in 32 {{
    shared sa:f16[512] = zeroed;
    shared sc:f32[512] = zeroed;
    for i in 0..16 {{ sa[t + 32 * i] = x[t + 32 * i]; }}
    barrier;
{body}
    barrier;
    for i in 0..16 {{ out[t + 32 * i] = sc[t + 32 * i]; }}
  }}
}}
"""


WMMA_A = "mma_load[WmmaA[f16, 16, 16, 16]]"
WMMA_B = "mma_load[WmmaB[f16, 16, 16, 16]]"
WMMA_ACC = "WmmaAcc[f32, 16, 16, 16]"


@pytest.mark.parametrize(
    ("body", "said"),
    [
        (f"let a = {WMMA_A}(sa, T, t % 2, 0);", "mma_load's fragment coordinate"),
        (f"let acc = {WMMA_ACC}(1.0);\n    mma_store(sc, T, t % 2, 0, acc);", "mma_store's fragment coordinate"),
        (f"let acc = {WMMA_ACC}(f32(t));\n    mma_store(sc, A, 0, 0, acc);", "fills its accumulator with"),
        ("let a = mma_load[MmaA[f16, 16, 8, 16]](sa, T, t % 2, 0);", "mma_load's fragment coordinate"),
        (f"let mut i:usize = 0;\n    if t == 3 {{ i = 1; }}\n    let a = {WMMA_A}(sa, T, i, 0);", "line 11"),
        (f"let mut a = {WMMA_A}(sa, T, 0, 0);\n    let other = {WMMA_A}(sa, T, 1, 0);\n    if t < 16 {{ a = other; }}\n"
         f"    let acc = mma_unordered({WMMA_ACC}(0.0), a, {WMMA_B}(sa, A, 0, 0));", "An A or B fragment"),
        (f"let mut acc = {WMMA_ACC}(0.0);\n    let one = {WMMA_ACC}(1.0);\n    if t == 0 {{ acc = one; }}\n"
         "    mma_store(sc, A, 0, 0, acc);", "A WMMA accumulator"),
        (f"let a = {WMMA_A}(sa, T, 0, 0);\n    let b = {WMMA_B}(sa, A, 0, 0);\n    let mut acc = {WMMA_ACC}(0.0);\n"
         f"    let one = {WMMA_ACC}(1.0);\n    if t == 0 {{ acc = one; }}\n    acc = mma_unordered(acc, a, b);",
         "A WMMA accumulator"),
    ],
)  # fmt: skip
def test_what_names_a_fragment_is_the_same_in_every_thread_of_a_warp(body, said):
    """Every lane passes the coordinates, the fill value and the fragments, and the hardware takes the warp's one
    fragment from all of them: a value that differs within a warp is undefined on the device (E-COOP-WARP)."""
    assert said in refused("E-COOP-WARP", warped(body))["message"]


def test_the_warp_s_number_names_its_fragment_and_an_mma_sync_accumulator_may_differ_by_lane():
    """`t / 32` is the same in a warp; an mma.sync accumulator's lanes hold their own elements, which mma_set
    changes one lane at a time, and every output of the multiply reads only its own lane's accumulator element."""
    compile_source(warped(f"let a = {WMMA_A}(sa, T, t / 32, 0);\n    let acc = mma_unordered({WMMA_ACC}(0.0), a, "
                          f"{WMMA_B}(sa, A, 0, 0));\n    mma_store(sc, A, 0, 0, acc);"))  # fmt: skip
    compile_source(warped("let a = mma_load[MmaA[f16, 16, 8, 16]](sa, T, 0, 0);\n"
                          "    let b = mma_load[MmaB[f16, 16, 8, 16]](sa, T8, 0, 0);\n"
                          "    let mut acc = MmaAcc[f32, 16, 8, 16](0.0);\n"
                          "    acc = mma_set(acc, 0, f32(t));\n"
                          "    acc = mma_unordered(acc, a, b);\n"
                          "    mma_store(sc, T8, 0, 0, acc);"))  # fmt: skip


LOADED = """layout TILE = rows(16, 16);
fn tile(out:rw<f32>[256]@PLACE, a:ro<f16>[256]@device, b:ro<f16>[256]@device) {
  blocks g in 1 threads t in 32 {
    shared sc:f32[256] = zeroed;
    let x = mma_load[WmmaA[f16, 16, 16, 16]](a, TILE, 0, 0);
    let y = mma_load[WmmaB[f16, 16, 16, 16]](b, TILE, 0, 0);
    mma_store(sc, TILE, 0, 0, mma_unordered(WmmaAcc[f32, 16, 16, 16](0.0), x, y));
    barrier;
    for i in 0..8 { out[t + 32 * i] = sc[t + 32 * i]; }
  }
}
"""


def test_a_region_that_loads_a_fragment_from_the_device_runs_there():
    """A fragment's tile places the region as an index does: from a @device view, on the device."""
    receipt = compile_source(LOADED.replace("PLACE", "unified"))[1]["functions"]["tile"]
    assert "par:device" in receipt["effects"] and "par:host" not in receipt["effects"]
    refused("E-PLACEMENT", LOADED.replace("@PLACE", ""))


WMMA_STORE = """layout TILE = rows(16, 16);
fn tile(out:rw<f32>[32]@device, c:ro<f32>[256]@device) {
  blocks g in 1 threads t in 32 {
    shared sc:f32[256] = zeroed;
    let acc = mma_load[ACC](c, TILE, 0, 0);
    mma_store(sc, TILE, I, 0, acc);
BETWEEN
    out[t] = sc[(t / 4) * 16 + 2 * (t % 4)];
  }
}
"""


@pytest.mark.parametrize(
    ("acc", "i", "between", "code"),
    [
        ("WmmaAcc[f32, 16, 16, 16]", "0", "", "E-COOP-UNORDERED"),  # its own lane's element: WMMA names no lane
        ("WmmaAcc[f32, 16, 16, 16]", "0", "    mma_store(sc, TILE, 0, 0, acc);", "E-COOP-CONFLICT"),
        ("MmaAcc[f32, 16, 8, 16]", "g", "", "E-COOP-UNDECIDED"),  # an unknown fragment is still a write
        ("WmmaAcc[f32, 16, 16, 16]", "0", "    barrier;", None),
        ("MmaAcc[f32, 16, 8, 16]", "0", "", None),  # the PTX ISA names the lane: its own element, no barrier
    ],
)  # fmt: skip
def test_the_phase_rule_takes_a_wmma_store_as_the_warp_s_with_no_lane_named(acc, i, between, code):
    source = WMMA_STORE.replace("ACC", acc).replace("I,", f"{i},").replace("BETWEEN\n", between + "\n" * bool(between))
    if "16, 8" in acc:
        source = source.replace("rows(16, 16)", "rows(16, 8)").replace("[256]", "[128]").replace("* 16 +", "* 8 +")
    if code is None:
        compile_source(source)
    else:
        assert "a lane of warp 0" in refused(code, source)["message"]

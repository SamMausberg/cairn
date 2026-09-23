"""Tensor-core fragments: `WmmaA`, `WmmaB`, `WmmaAcc`, `MmaA`, `MmaB`, `MmaAcc`, `load`, `store` and
`mma_unordered(acc, a, b)` (compiler/fragments.py, runtime/cairn_fragment.hpp).

On the host every thread of a warp holds each fragment whole and stores only the elements its lane holds on the
device; tests/runtime/fragment_runtime.cpp runs a warp as 32 real threads under the thread sanitizer and holds every
output to the reference loop bit for bit. The device operations are compiled for sm_120 here and their SASS
inspected, never run: that the tensor cores keep the contract is `make gpu`'s question.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler import layouts as L
from cairn.compiler.cairnc import compile_program
from emitted import refused

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src/cairn/runtime"
NATIVE = ROOT / "tests/runtime"

TILE = "layout SA = rows(16, 16);\nlayout SW = swizzle(rows(16, 16), 1, 3, 3);\nlayout NARROW = pad(rows(16, 16), 4);\n"
FRAGMENTS = "acc:WmmaAcc[f32, 16, 16, 16], a:WmmaA[f16, 16, 16, 16], b:WmmaB[f16, 16, 16, 16]"


@pytest.mark.parametrize(
    ("code", "source", "said"),
    [
        ("E-FRAGMENT", TILE + "fn f(a:ro<f16>[256]@device) { let x = load[WmmaA[f16, 16, 16, 16]](a, SA, 0, 0); }",
         "inside a cooperative region"),
        ("E-FRAGMENT", "fn f() { let acc = WmmaAcc[f32, 16, 16, 16](0.0); }", "inside a cooperative region"),
        ("E-FRAGMENT", "fn f() { let acc = WmmaAcc[f32, 16, 8, 16](0.0); }", "shapes wmma has"),
        ("E-FRAGMENT", "fn f() { let acc = MmaAcc[f32, 16, 16, 16](0.0); }", "16 x 8 x 16"),
        ("E-FRAGMENT", "fn f() { let acc = WmmaAcc[f16, 16, 16, 16](0.0); }", "accumulate in f32"),
        ("E-FRAGMENT", TILE + "fn f(a:ro<f8e4m3>[256]@device) { let x = load[WmmaA[f8e4m3, 16, 16, 16]](a, SA, 0, 0); }",
         "operands are f16 or bf16"),
        ("E-FRAGMENT", "fn f() { let a = WmmaA[f16, 16, 16, 16](0.0); }", "loaded from a tile"),
        ("E-FRAGMENT", TILE + "fn f(a:ro<f16>[256]) { let x = load[WmmaA[f16, 16, 16, 16]](a, SA, 0, 0); }",
         "host memory"),
        ("E-TARGET-FEATURE", "fn f() { let acc = TmemAcc[f32, 128, 256, 16](0.0); }", "sm_100a"),
        ("E-LAYOUT-CONSUMER", TILE + "fn f(a:ro<f16>[256]@device) { let x = load[WmmaA[f16, 16, 16, 16]](a, SW, 0, 0); }",
         "no swizzle"),
        ("E-LAYOUT-CONSUMER", TILE + "fn f(a:ro<f16>[512]@device) { let x = load[WmmaA[f16, 16, 16, 16]](a, NARROW, 0, 0); }",
         "40 bytes apart"),
        ("E-LAYOUT-CONSUMER", "layout T = cols(16, 16);\nfn f(a:ro<f16>[256]@device) { let x = load[WmmaB[f16, 16, 16, 16]](a, T, 0, 0); }",
         "row-major"),
        ("E-LAYOUT-CONSUMER", "layout T = rows(16, 24);\nfn f(a:ro<f16>[384]@device) { let x = load[WmmaA[f16, 16, 16, 16]](a, T, 0, 0); }",
         "whole 16 x 16 fragments"),
        ("E-LAYOUT-CONSUMER", "fn f(a:ro<f16>[256]@device) { let x = load[WmmaA[f16, 16, 16, 16]](a, a, 0, 0); }",
         "names none"),
        ("E-LAYOUT-CONSUMER", TILE + "fn f(a:ro<f16>[100]@device) { let x = load[WmmaA[f16, 16, 16, 16]](a, SA, 0, 0); }",
         "past the 100 elements"),
        ("E-TYPE-MISMATCH", TILE + "fn f(a:ro<bf16>[256]@device) { let x = load[WmmaA[f16, 16, 16, 16]](a, SA, 0, 0); }",
         "array of f16"),
        ("E-TYPE-MISMATCH", TILE + f"fn f(c:ro<f32>[256]@device, {FRAGMENTS}) {{ store(c, SA, 0, 0, acc); }}", "an rw array"),
        ("E-TYPE-MISMATCH", TILE + f"fn f(c:rw<f32>[256]@device, {FRAGMENTS}) {{ store(c, SA, 0, 0, a); }}", "accumulator"),
        ("E-MMA", "fn f(a:u32, b:u32, c:u32) { let x = mma_unordered(a, b, c); }", "multiplies fragments"),
        ("E-MMA", f"fn f({FRAGMENTS}) {{ let x = mma_unordered(a, acc, b); }}", "in that order"),
        ("E-MMA", f"fn f({FRAGMENTS.replace('WmmaB[f16', 'WmmaB[bf16')}) {{ let x = mma_unordered(acc, a, b); }}",
         "one format"),
        ("E-MMA", f"fn f({FRAGMENTS.replace('WmmaB', 'MmaB').replace('16, 16, 16], b', '16, 8, 16], b')}) "
         "{ let x = mma_unordered(acc, a, b); }", "one family"),
        ("E-INFER", TILE + "fn f(a:ro<f16>[256]@device) { let x = load[u32](a, SA, 0, 0); }", "fragment type"),
        ("E-ARITY", TILE + "fn f(a:ro<f16>[256]@device) { let x = load[WmmaA[f16, 16, 16, 16]](a, SA, 0); }", "coordinates"),
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
            ((lane, value),) = L.owner(acc, (r, c))
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
    line = ["nvcc", "-std=c++20", "-arch=sm_120", "-cubin", f"-I{RUNTIME}", str(NATIVE / "fragment_device.cu"),
            "-o", str(cubin)]  # fmt: skip
    built = subprocess.run(line, capture_output=True, text=True, timeout=600)
    assert built.returncode == 0, built.stderr[-3000:]
    sass = subprocess.run(["cuobjdump", "-sass", str(cubin)], capture_output=True, text=True, timeout=120).stdout
    found = set(re.findall(r"\b(HMMA\.16816\.F32(?:\.BF16)?|LDSM\.16\.M(?:T)?88\.[24])\b", sass))
    assert {"HMMA.16816.F32", "HMMA.16816.F32.BF16", "LDSM.16.M88.4", "LDSM.16.MT88.2"} <= found, found

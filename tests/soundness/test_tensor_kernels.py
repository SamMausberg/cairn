"""Tensor-core kernels written in CAIRN with layouts and fragments (examples/tensor), inside cooperative regions.

`tile64` is the tiling `mma_unordered` fixes, 64 x 64 tiles, four warps, K in steps of 32 through two stages, with
WMMA fragments over padded tiles; `tile32` is another, 64 x 32 tiles, eight warps, one stage, with mma.sync fragments
over a swizzled tile. Both compute out = c + a * b. On the host every output must lie within the contract's bound
of the exact sum, measured here with Python's rationals, and equal the reference loop's bit for bit, since both add
in increasing k; the shapes are generated, partial tiles in every direction included. The host run is a real one,
each block's threads real threads, and runs again under the thread sanitizer. The device build is compiled for
sm_120 and its SASS read, never run; the comparison on the device is `make gpu`'s.

`transpose` is one logical operation through a shared tile stored three ways, row-major, padded and swizzled.
"""

import ctypes as C
import random
import re
import shutil
import struct
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import contract, device_build, library, on_device, refused, run, sanitized, watched

ROOT = Path(__file__).resolve().parents[2]
TENSOR = ROOT / "examples/tensor"
KERNELS = {"tile64": ("f16",), "tile32": ("bf16",)}  # each kernel and the format of its a and b
REFERENCE = """
fn reference(m:usize, n:usize, k:usize, cn:usize, c:rw<f32>[cn], an:usize, a:ro<T>[an], bn:usize, b:ro<T>[bn]) {
  mma_unordered(m, n, k, c, a, b);
}
"""
EDGES = [(1, 1, 1), (64, 64, 32), (65, 63, 33), (0, 4, 4), (3, 5, 0), (64, 32, 16)]


def host(name: str) -> str:
    """The kernel for host threads: the same source with its views on the host."""
    return (TENSOR / f"{name}.cairn").read_text().replace("@device", "")


def pattern(element: str, x: float) -> int:
    """The bits of x in `element`; every value used is exact in both formats."""
    if element == "f16":
        return struct.unpack("H", struct.pack("e", x))[0]
    return struct.unpack("I", struct.pack("f", x))[0] >> 16


def f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def shapes(seed: str) -> list[tuple[int, int, int]]:
    rng = random.Random(seed)
    return EDGES + [(rng.randint(1, 150), rng.randint(1, 150), rng.randint(0, 100)) for _ in range(6)]


@pytest.fixture(scope="module", params=["g++", "clang++"])
def built(request, tmp_path_factory):
    out = {}
    for name, (element,) in KERNELS.items():
        directory = tmp_path_factory.mktemp(f"{name}-{request.param.replace('+', 'p')}")
        out[name] = library(directory, compile_source(host(name) + REFERENCE.replace("T", element))[0], request.param)
    return out


@pytest.mark.parametrize("name", KERNELS)
def test_each_kernel_keeps_the_contract_and_equals_the_reference_loop(built, name):
    lib, element = built[name], KERNELS[name][0]
    rng = random.Random(name)
    for m, n, k in shapes(name):
        av = [rng.randint(-30, 30) / 16 for _ in range(m * k)]
        bv = [rng.randint(-30, 30) / 16 for _ in range(k * n)]
        cv = [f32(rng.uniform(-4, 4)) for _ in range(m * n)]
        a = (C.c_uint16 * max(1, m * k))(*(pattern(element, x) for x in av))
        b = (C.c_uint16 * max(1, k * n))(*(pattern(element, x) for x in bv))
        c = (C.c_float * max(1, m * n))(*cv)
        out = (C.c_float * max(1, m * n))()
        ref = (C.c_float * max(1, m * n))(*cv)
        s = C.c_size_t
        getattr(lib, f"cf_{name}")(s(m), s(n), s(k), s(m * n), out, c, s(m * k), a, s(k * n), b)
        lib.cf_reference(s(m), s(n), s(k), s(m * n), ref, s(m * k), a, s(k * n), b)
        for i in range(m):
            for j in range(n):
                products = [Fraction(av[i * k + p]) * Fraction(bv[p * n + j]) for p in range(k)]
                exact = Fraction(cv[i * n + j]) + sum(products)
                size = abs(Fraction(cv[i * n + j])) + sum(abs(x) for x in products)
                got = out[i * n + j]
                assert abs(Fraction(got) - exact) <= (k + 1) * Fraction(1, 2**22) * size, (name, m, n, k, i, j)
                assert got == ref[i * n + j], (name, m, n, k, i, j, got, ref[i * n + j])


MAIN = """
fn main() -> i32 {
  let m:usize = 70;
  let n:usize = 45;
  let k:usize = 40;
  let mk = m * k;
  let kn = k * n;
  let mn = m * n;
  buffer a:T[mk] = zeroed;
  buffer b:T[kn] = zeroed;
  buffer c:f32[mn] = zeroed;
  buffer out:f32[mn] = zeroed;
  buffer want:f32[mn] = zeroed;
  for e in 0..mk { a[e] = T(f32(i64(e % 13) - 6) / 4.0); }
  for e in 0..kn { b[e] = T(f32(i64(e % 7) - 3) / 8.0); }
  for e in 0..mn { c[e] = f32(e % 5); want[e] = c[e]; }
  NAME(m, n, k, mn, out, c, mk, a, kn, b);
  mma_unordered(m, n, k, want, a, b);
  for e in 0..mn { if out[e] != want[e] { return 1; } }
  return 0;
}
"""


@pytest.mark.parametrize("name", KERNELS)
def test_each_kernel_s_threads_never_race_under_the_thread_sanitizer(tmp_path, name):
    source = host(name) + MAIN.replace("T", KERNELS[name][0]).replace("NAME", name)
    done = watched(tmp_path, compile_source(source)[0], "clang++", "thread")
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]


@pytest.mark.parametrize(("name", "family", "loads"), [("tile64", "HMMA.16816.F32", "LDSM"),
                                                       ("tile32", "HMMA.16816.F32.BF16", "LDSM.16.MT88.2")])  # fmt: skip
def test_each_kernel_compiles_for_sm_120_to_tensor_core_instructions(tmp_path, name, family, loads):
    """The device build, compiled to a cubin and read back with cuobjdump; nothing runs on a device."""
    if not shutil.which("cuobjdump"):
        pytest.skip("needs cuobjdump")
    cpp, receipt = compile_source((TENSOR / f"{name}.cairn").read_text())
    wanted = {"tile64": {"wmma"}, "tile32": {"mma_sync", "bf16"}}[name]
    assert wanted <= set(receipt["device_features"])
    element = KERNELS[name][0]  # the `make gpu` harness below is checked here too, though only it runs
    compile_source((TENSOR / f"{name}.cairn").read_text() + DEVICE.replace("T", element).replace("NAME", name))
    ptx = device_build(tmp_path, cpp, ptx=True, timeout=900)
    cubin = tmp_path / "p.cubin"
    assembled = subprocess.run(["ptxas", "-arch=sm_120", str(ptx), "-o", str(cubin)], capture_output=True, text=True)
    assert assembled.returncode == 0, assembled.stderr[-3000:]
    sass = subprocess.run(["cuobjdump", "-sass", str(cubin)], capture_output=True, text=True, timeout=120).stdout
    assert re.search(rf"\b{re.escape(family)}\b", sass) and loads in sass


@pytest.mark.parametrize("name", KERNELS)
def test_each_kernel_keeps_the_contract_on_the_device(tmp_path, name):
    with on_device():  # runs only under `make gpu`
        source = (TENSOR / f"{name}.cairn").read_text() + DEVICE.replace("T", KERNELS[name][0]).replace("NAME", name)
        done = contract(tmp_path, compile_source(source)[0], "g++", cuda=True)
        assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


DEVICE = """
fn check(m:usize, n:usize, k:usize) -> i32 {
  let mk = m * k;
  let kn = k * n;
  let mn = m * n;
  buffer a:T[mk] = zeroed;
  buffer b:T[kn] = zeroed;
  buffer c:f32[mn] = zeroed;
  buffer want:f32[mn] = zeroed;
  for e in 0..mk { a[e] = T(f32(i64(e % 13) - 6) / 4.0); }
  for e in 0..kn { b[e] = T(f32(i64(e % 7) - 3) / 8.0); }
  for e in 0..mn { c[e] = f32(e % 5); want[e] = c[e]; }
  mma_unordered(m, n, k, want, a, b);
  buffer da:T[mk]@device = zeroed;
  buffer db:T[kn]@device = zeroed;
  buffer dc:f32[mn]@device = zeroed;
  buffer dout:f32[mn]@device = zeroed;
  transfer(da, a);
  transfer(db, b);
  transfer(dc, c);
  NAME(m, n, k, mn, dout, dc, mk, da, kn, db);
  buffer got:f32[mn] = zeroed;
  transfer(got, dout);
  for i in 0..m {
    for j in 0..n {
      let mut size:f64 = abs(f64(c[i * n + j]));
      for p in 0..k { size += abs(f64(a[i * k + p]) * f64(b[p * n + j])); }
      if abs(f64(got[i * n + j]) - f64(want[i * n + j])) > 2.0 * f64(k + 1) * size / 4194304.0 { return 1; }
    }
  }
  return 0;
}
fn main() -> i32 {
  let one = check(1, 1, 1);
  let tails = check(65, 63, 33);
  let wide = check(130, 70, 100);
  let whole = check(64, 64, 32);
  return one + tails + wide + whole;
}
"""


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_one_transpose_through_three_shared_layouts(tmp_path, cxx):
    done = run(tmp_path, compile_source((TENSOR / "transpose.cairn").read_text())[0], *sanitized(cxx), "-pthread",
               cxx=cxx)  # fmt: skip
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


def test_the_transpose_s_threads_never_race_under_the_thread_sanitizer(tmp_path):
    done = watched(tmp_path, compile_source((TENSOR / "transpose.cairn").read_text())[0], "clang++", "thread")
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]


def test_explain_tells_the_three_layouts_apart():
    from cairn.agent.explain import explain

    said = explain((TENSOR / "transpose.cairn").read_text(), cxx="g++")["layouts"]
    assert {k: said[f"{k}_OUT"]["bank_ways"]["4"] for k in ("ROWS", "PADDED", "SWIZZLED")} == {
        "ROWS": 32, "PADDED": 1, "SWIZZLED": 1}  # fmt: skip
    assert said["ROWS_IN"]["conversions"] == {"ROWS_OUT": "shared"}


# The rules a cooperative body with fragments and layouts must keep, each refused with its code.
TILE = """layout T = rows(16, 16);
layout ACC = rows(16, 16);
const CELLS:usize = T.cosize();
"""


def region(body: str, threads: str = "32, 1") -> str:
    return (
        TILE
        + f"""fn f(x:ro<f16>[256]@device) {{
  blocks b in 1 threads tx, ty in {threads} {{
    shared sa:f16[CELLS] = zeroed;
    shared sb:f16[CELLS] = zeroed;
    shared sc:f32[CELLS] = zeroed;
{body}
  }}
}}
"""
    )


STAGE = "    for i in 0..8 { sa[T.at(i * 2 + tx / 16, tx % 16)] = x[(i * 2 + tx / 16) * 16 + tx % 16]; }\n"
LOAD = "    let a = mma_load[WmmaA[f16, 16, 16, 16]](sa, T, 0, 0);\n"
FRAGMENTS = (
    LOAD
    + """    let bb = mma_load[WmmaB[f16, 16, 16, 16]](sb, T, 0, 0);
    let mut acc = WmmaAcc[f32, 16, 16, 16](0.0);
    acc = mma_unordered(acc, a, bb);
"""
)


def test_a_warp_that_stages_a_tile_then_multiplies_it_is_accepted():
    compile_source(region(STAGE + "    barrier;\n" + FRAGMENTS + "    mma_store(sc, ACC, 0, 0, acc);\n"))


@pytest.mark.parametrize(
    ("code", "body", "said"),
    [
        ("E-COOP-UNORDERED", STAGE + LOAD, "barrier"),  # the fragment reads the tile before the barrier
        ("E-COOP-WARP", "    if tx < 16 {\n" + LOAD + "    }\n", "every thread of a warp"),
        (
            "E-COOP-CONFLICT",
            FRAGMENTS + "    mma_store(sc, ACC, 0, 0, acc);\n",
            "two threads write one element",
        ),  # two warps, one fragment
        ("E-COOP-REUSE", "    let y = sc[3];\n" + FRAGMENTS + "    mma_store(sc, ACC, 0, 0, acc);\n", "barrier"),
    ],
)
def test_fragments_in_a_region_keep_its_phase_rule(code, body, said):
    threads = "32, 2" if code == "E-COOP-CONFLICT" else "32, 1"
    assert said in refused(code, region(body, threads))["message"]


def test_mma_sync_reads_a_swizzled_shared_tile_and_refuses_one_whose_rows_it_cannot_address():
    swizzled = "layout SW = swizzle(rows(16, 16), 1, 3, 3);\n"
    body = "    let a = mma_load[MmaA[f16, 16, 8, 16]](sa, SW, 0, 0);\n"
    compile_source(swizzled + region(body))
    narrow = "layout SW = swizzle(rows(16, 16), 1, 2, 3);\n"  # a base of 2 splits the 16-byte runs
    assert "ldmatrix" in refused("E-LAYOUT-CONSUMER", narrow + region(body))["message"]


@pytest.mark.parametrize("name", [*KERNELS, "transpose"])
def test_the_canonical_projection_of_each_program_lowers_to_the_same_code(name):
    from cairn.agent.projection import canonical_source

    source = (TENSOR / f"{name}.cairn").read_text()
    assert compile_source(canonical_source(source))[0] == compile_source(source)[0]


def test_the_receipt_states_each_fragment_step_s_contract_and_the_value_model_answers_unknown():
    from cairn.verify.scalar_semantics import equivalent

    source = (TENSOR / "tile32.cairn").read_text()
    steps = [s for s in compile_source(source)[1]["functions"]["tile32"]["numerics"] if s["op"] == "mma"]
    assert len(steps) == 2 and all(s["rounding"] == "unordered-f32" and s["k"] == "16" for s in steps)
    assert steps[0]["from"] == "bf16" and steps[0]["bound"] == "(k + 1) * 2^-22 * (|c| + sum |a * b|)"
    assert equivalent(source, source, "tile32")["status"] == "unknown"


def test_the_performance_model_says_a_fragment_step_is_not_priced():
    from cairn.compiler.cairnc import compile_program
    from cairn.perf.work import count

    p, checker, _ = compile_program((TENSOR / "tile64.cairn").read_text())
    assert any("fragment step is not priced" in why for why in count(p, checker)["tile64"].unknown)


@pytest.mark.parametrize(("name", "target", "feature"), [("tile32", "sm_75", "mma_sync"), ("tile64", "sm_75", None)])
def test_a_build_for_a_target_without_a_kernel_s_family_is_refused_before_nvcc(tmp_path, name, target, feature):
    """The family a fragment needs is a feature the device target must provide (projects/target.py): mma.sync starts
    at sm_80, and WMMA on f16 at sm_75, which therefore builds tile64 as far as nvcc."""
    from cairn.compiler.cairnc import Diagnostic
    from cairn.projects.build import build
    from cairn.projects.project import load_project

    path = tmp_path / f"{name}.cairn"
    path.write_text((TENSOR / f"{name}.cairn").read_text())
    if feature is None:
        from cairn.projects.target import parse

        assert parse(target).require(compile_source(path.read_text())[1]["device_features"])
        return
    with pytest.raises(Diagnostic) as refused_build:
        build(load_project(path), output=tmp_path / "build", cxx="g++", device_target=target)
    said = refused_build.value.data
    assert said["code"] == "E-TARGET-FEATURE" and said["feature"] == feature

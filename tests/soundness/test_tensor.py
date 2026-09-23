"""`mma_unordered(m, n, k, c, a, b)`: the tensor-core multiply and its numerical contract.

On the host the multiply is its own reference, every product then every sum in increasing p, so an independent
replay of that order in Python (tests/oracles/float_formats.py decodes the inputs; every f32 operation is rounded
through struct, which double rounding cannot disturb for one addition or product of floats) must match it bit for
bit, and the contract's bound must hold against the exact rational sum, old value included. The tile the device runs is checked on the
host, phase by phase, by tests/runtime/tensor_runtime.cpp. The device itself is compiled for here, never run: the
comparison of the tensor cores with the reference, within the contract, is in `make gpu`.
"""

import ctypes as C
import random
import shutil
import signal
import struct
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.verify.scalar_semantics import equivalent
from emitted import contract, device_build, library, on_device, refused, run
from oracles.float_formats import FORMATS, Format

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src/cairn/runtime"
WIDTH = {"f16": C.c_uint16, "bf16": C.c_uint16, "f8e4m3": C.c_uint8, "f8e5m2": C.c_uint8}


def entry(name: str, place: str = "") -> str:
    at = f"@{place}" if place else ""
    return f"""
fn mma_{name}(m:usize, n:usize, k:usize, cn:usize, c:rw<f32>[cn]{at}, an:usize, a:ro<{name}>[an]{at}, bn:usize,
              b:ro<{name}>[bn]{at}) {{
  mma_unordered(m, n, k, c, a, b);
}}
"""


@pytest.fixture(scope="module", params=["g++", "clang++"])
def lib(request, tmp_path_factory):
    directory = tmp_path_factory.mktemp(request.param.replace("+", "p"))
    return library(directory, compile_source("".join(map(entry, FORMATS)))[0], request.param)


def f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def bits(x: float) -> int:
    return struct.unpack("I", struct.pack("f", x))[0]


def finite_patterns(fmt: Format, rng: random.Random, count: int, limit: float) -> list[int]:
    chosen = []
    while len(chosen) < count:
        p = rng.randrange(1 << fmt.width)
        v = fmt.value(p)
        if v == v and abs(v) <= limit:  # finite, and small enough that no sum of k products overflows f32
            chosen.append(p)
    return chosen


def multiplied(lib, name: str, m: int, n: int, k: int, c: list[float], a: list[int], b: list[int]) -> list[float]:
    cs, as_, bs = (C.c_float * (m * n))(*c), (WIDTH[name] * (m * k))(*a), (WIDTH[name] * (k * n))(*b)
    size = C.c_size_t
    getattr(lib, f"cf_mma_{name}")(size(m), size(n), size(k), size(m * n), cs, size(m * k), as_, size(k * n), bs)
    return list(cs)


SHAPES = [(1, 1, 1), (3, 5, 7), (8, 8, 8), (17, 3, 33), (2, 40, 5), (4, 4, 0), (0, 3, 3), (12, 13, 64)]


@pytest.mark.parametrize("name", FORMATS)
def test_the_host_multiply_is_its_reference_bit_for_bit_and_within_the_contract(lib, name):
    fmt, rng = Format(name), random.Random(f"mma-{name}")
    limit = 448.0 if name != "bf16" else 1e6
    for m, n, k in SHAPES:
        a, b = finite_patterns(fmt, rng, m * k, limit), finite_patterns(fmt, rng, k * n, limit)
        c = [f32(rng.uniform(-4, 4)) for _ in range(m * n)]
        got = multiplied(lib, name, m, n, k, c, a, b)
        for i in range(m):
            for j in range(n):
                acc, exact, magnitude = c[i * n + j], Fraction(c[i * n + j]), abs(Fraction(c[i * n + j]))
                for p in range(k):
                    x, y = fmt.value(a[i * k + p]), fmt.value(b[p * n + j])
                    acc = f32(acc + f32(x * y))  # the order the host writes: old value, then p upward
                    exact += Fraction(x) * Fraction(y)
                    magnitude += abs(Fraction(x) * Fraction(y))
                assert bits(got[i * n + j]) == bits(acc), (name, m, n, k, i, j)
                assert abs(Fraction(got[i * n + j]) - exact) <= (k + 1) * Fraction(1, 2**22) * magnitude


@pytest.mark.parametrize(
    ("code", "source"),
    [
        ("E-MMA", "fn f(n:usize, c:rw<f64>[n], a:ro<f16>[n], b:ro<f16>[n]) { mma_unordered(1, 1, n, c, a, b); }"),
        ("E-MMA", "fn f(n:usize, c:ro<f32>[n], a:ro<f16>[n], b:ro<f16>[n]) { mma_unordered(1, 1, n, c, a, b); }"),
        ("E-MMA", "fn f(n:usize, c:rw<f32>[n], a:ro<f16>[n], b:ro<bf16>[n]) { mma_unordered(1, 1, n, c, a, b); }"),
        ("E-MMA", "fn f(n:usize, c:rw<f32>[n], a:ro<f32>[n], b:ro<f32>[n]) { mma_unordered(1, 1, n, c, a, b); }"),
        ("E-PLACEMENT", "fn f(n:usize, c:rw<f32>[n]@device, a:ro<f16>[n], b:ro<f16>[n]) { mma_unordered(1, 1, n, c, a, b); }"),
        ("E-PARALLEL-NEST", "fn f(n:usize, c:rw<f32>[n], a:ro<f16>[n], b:ro<f16>[n]) { parallel i in n { mma_unordered(1, 1, n, c, a, b); } }"),
        ("E-ARITY", "fn f(n:usize, c:rw<f32>[n], a:ro<f16>[n]) { mma_unordered(1, 1, n, c, a); }"),
        ("E-TYPE-MISMATCH", "fn f(n:usize, c:rw<f32>[n], a:ro<f16>[n], b:ro<f16>[n]) { mma_unordered(1.0, 1, n, c, a, b); }"),
    ],
)  # fmt: skip
def test_rejections(code, source):
    refused(code, source)


def test_a_square_of_one_matrix_reads_it_twice():
    compile_source("fn f(n:usize, c:rw<f32>[n], a:ro<f16>[n]) { mma_unordered(1, 1, n, c, a, a); }")


def test_the_extents_the_call_writes_are_checked_against_its_views(tmp_path):
    source = """fn main() -> i32 {
  let mut c = Buf[f32](6);
  let a = Buf[f16](6);
  let b = Buf[f16](6);
  mma_unordered(2, 3, 3, c, a, b);                  // a holds 2 * 3, b holds 3 * 3 would be 9: a guard
  return 0;
}
"""
    done = run(tmp_path, compile_source(source)[0], "-std=c++20", "-O2")
    assert done.returncode == -signal.SIGABRT, (done.returncode, done.stderr)


def test_the_receipt_names_the_contract_and_the_row_what_it_touches():
    receipt = compile_source(entry("f16") + entry("bf16", "device"))[1]
    host, device = receipt["functions"]["mma_f16"], receipt["functions"]["mma_bf16"]
    assert {"read:a", "read:b", "write:c", "trap"} <= set(host["effects"]) and "par:device" not in host["effects"]
    assert "par:device" in device["effects"]
    said = host["numerics"][0]
    assert said["op"] == "mma" and said["from"] == "f16" and said["rounding"] == "unordered-f32"
    assert said["bound"] == "(k + 1) * 2^-22 * (|c| + sum |a * b|)"


def test_the_model_prices_a_device_multiply_at_the_published_tensor_peak_and_says_it_is_a_roofline():
    from cairn.compiler.cairnc import compile_program
    from cairn.perf import model
    from cairn.perf.profile import packaged
    from cairn.perf.work import count

    p, checker, _ = compile_program(entry("f16", "device") + entry("bf16"))
    costs, machine = count(p, checker), packaged("zen4-7800x3d")
    side = 4096
    sizes = {"m": side, "n": side, "k": side, "cn": side * side, "an": side * side, "bn": side * side}
    device = model.predict(costs["mma_f16"], machine, sizes)
    part = device["parts"][0]
    assert part["bound"] == "tensor cores" and part["peak_ops_per_ns"] == 87900.0  # dense f16, f32 accumulate
    assert abs(part["compute_ns"] - 2 * side**3 / 87900.0) < 1.0
    assert device["confidence"] == "low" and any("roofline" in why for why in device["why"])
    host = model.predict(costs["mma_bf16"], machine, sizes)  # the reference loop, on one core
    assert host["ns"] > 100 * device["ns"]


def test_a_function_that_multiplies_is_unknown_to_the_value_model():
    source = entry("f16")
    assert equivalent(source, source, "mma_f16")["status"] == "unknown"


def test_the_multiply_is_a_call_in_the_canonical_projection():
    source = entry("f8e4m3")
    assert "mma_unordered(m, n, k, c, a, b);" in canonical_source(source)
    assert compile_source(canonical_source(source))[0] == compile_source(source)[0]


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_every_tile_of_the_device_multiply_equals_the_reference_on_the_host(tmp_path, cxx):
    """The tile's indexing, tails, zero fill and two stages, every thread phase by phase, with a model of the
    tensor-core operations; under the sanitizers so a stray index shows even where the values would agree."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    exe = tmp_path / "tensor_runtime"
    line = [cxx, "-std=c++20", "-O1", "-g", "-ffp-contract=off", "-fno-fast-math", "-fsanitize=address,undefined",
            "-fno-sanitize-recover=all", "-Wall", "-Wextra", "-Werror", f"-I{RUNTIME}",
            str(ROOT / "tests/runtime/tensor_runtime.cpp"), "-o", str(exe)]  # fmt: skip
    built = subprocess.run(line, capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stderr[-3000:]
    done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, done.stdout + done.stderr[-3000:]
    assert "every tile equals the reference" in done.stdout


# On the device, the tensor cores against the host's reference, within the contract, for each format: shapes with
# tails in every direction, and for the 2-byte formats shapes whose k and n are multiples of 8, which cross in
# asynchronous 16-byte copies, beside shapes that do not.
ON_DEVICE = """
fn bound(m:usize, n:usize, k:usize, an:usize, a:ro<T>[an], bn:usize, b:ro<T>[bn], i:usize, j:usize) -> f64 {
  let mut total:f64 = 0.0;
  for p in 0..k { total += abs(f64(a[i * k + p]) * f64(b[p * n + j])); }
  return f64(k + 1) * total / 4194304.0;
}
fn check(m:usize, n:usize, k:usize, seed:u64) -> i32 {
  let mn = m * n;
  let mk = m * k;
  let kn = k * n;
  buffer a:T[mk] = zeroed;
  buffer b:T[kn] = zeroed;
  for e in 0..mk { a[e] = T(f32(i64(mul_wrap(u64(e) + seed, 2654435761) % 61) - 30) / 16.0); }
  for e in 0..kn { b[e] = T(f32(i64(mul_wrap(u64(e) + seed * 3, 40503) % 61) - 30) / 16.0); }
  buffer want:f32[mn] = zeroed;
  mma_unordered(m, n, k, want, a, b);
  buffer da:T[mk]@device = zeroed;
  buffer db:T[kn]@device = zeroed;
  buffer dc:f32[mn]@device = zeroed;
  transfer(da, a);
  transfer(db, b);
  mma_unordered(m, n, k, dc, da, db);
  buffer got:f32[mn] = zeroed;
  transfer(got, dc);
  for i in 0..m {
    for j in 0..n {
      let d = f64(got[i * n + j]) - f64(want[i * n + j]);
      if abs(d) > 2.0 * bound(m, n, k, a, b, i, j) { return 1; }
    }
  }
  return 0;
}
fn main() -> i32 {
  let one = check(1, 1, 1, 1);          // one element
  let tails = check(65, 63, 33, 2);     // a tail in every direction
  let wide = check(130, 70, 100, 3);
  let whole = check(64, 64, 32, 4);     // whole tiles, in chunks for a 2-byte format
  let even = check(72, 80, 96, 5);
  let short = check(129, 136, 8, 6);
  let none = check(3, 4, 0, 7);         // no products: every output keeps its old value
  return one + tails + wide + whole + even + short + none;
}
"""


@pytest.mark.parametrize("name", FORMATS)
def test_the_device_multiply_compiles_for_sm_120_without_touching_it(tmp_path, name):
    device_build(tmp_path, compile_source(ON_DEVICE.replace("T", name))[0], entry="main", timeout=900)


@pytest.mark.parametrize("name", FORMATS)
def test_the_tensor_cores_agree_with_the_reference_within_the_contract(tmp_path, name):
    with on_device():  # runs only under `make gpu`
        done = contract(tmp_path, compile_source(ON_DEVICE.replace("T", name))[0], "g++", cuda=True)
        assert done.returncode == 0, (done.returncode, done.stderr[-2000:])

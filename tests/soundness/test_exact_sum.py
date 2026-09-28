"""`reduce + parallel` over f32 or f64 is the exact sum of its terms rounded once to nearest, ties to even.

The emitted program runs on the host lane pool (runtime/cairn_sum.hpp) and is held bit for bit to an exact oracle
in Python (tests/oracles/exact_sum.py) on adversarial terms: every rounding boundary we could name, special values,
subnormals, overflow and cancellation, and random terms of narrow and wide range at sizes that cross the chunk, the
block and the pool's cutoff. Every lane count from 1 to 16 gives the same bits, under clang++ and g++ with the
project's flags, and under the address and undefined-behaviour sanitizers; clang++'s thread sanitizer watches the
blocks merge. The element form and a fused chain give the same bits as the bound total. A float product on the pool
is still refused.
"""

from __future__ import annotations

import math
import os
import random
import struct
import subprocess

import pytest

from cairn.compiler.cairnc import compile_program, compile_source
from cairn.perf.work import Counter
from emitted import ADDRESS_AND_UB, contract, refused, round_trips, watched
from oracles.exact_sum import correct, f32, value

SOURCE = """
fn total64(n:usize, x:ro<f64>[n]) -> f64 {
  let s = reduce + parallel i in n yield x[i];
  return s;
}

fn total32(n:usize, x:ro<f32>[n]) -> f32 {
  let s = reduce + parallel i in n yield x[i];
  return s;
}

fn into64(n:usize, x:ro<f64>[n], out:rw<f64>[1]) { reduce + out[0] parallel i in n yield x[i]; }

fn fused64(n:usize, x:ro<f64>[n]) -> f64 {
  buffer held:f64[n] = zeroed;
  parallel i in n { held[i] = x[i]; }
  let s = reduce + parallel j in n yield held[j];
  return s;
}

plan fused64 { fuse 2; }
"""

# Reads records of (width byte, u64 count, terms) and prints each sum's pattern in hex. The f64 sum is taken three
# ways, bound, into an element and through the fused chain, and a disagreement among them is printed as such.
MAIN = r"""
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
extern "C" double cf_total64(std::size_t, const double*) noexcept;
extern "C" float cf_total32(std::size_t, const float*) noexcept;
extern "C" void cf_into64(std::size_t, const double*, double*) noexcept;
extern "C" double cf_fused64(std::size_t, const double*) noexcept;
template<class T> static std::uint64_t pattern(T x) {
  std::uint64_t u = 0;
  std::memcpy(&u, &x, sizeof x);
  return u;
}
int main(int argc, char** argv) {
  if(argc != 2) return 2;
  std::FILE* in = std::fopen(argv[1], "rb");
  if(!in) return 2;
  unsigned char kind = 0;
  std::uint64_t n = 0;
  while(std::fread(&kind, 1, 1, in) == 1 && std::fread(&n, 8, 1, in) == 1) {
    if(kind == 8) {
      std::vector<double> x(n + 1);
      if(std::fread(x.data(), 8, n, in) != n) return 3;
      double out[1] = {1.0};
      const double a = cf_total64(n, x.data());
      cf_into64(n, x.data(), out);
      const double b = cf_fused64(n, x.data());
      if(pattern(a) != pattern(out[0]) || pattern(a) != pattern(b)) std::printf("disagree\n");
      else std::printf("%llx\n", static_cast<unsigned long long>(pattern(a)));
    } else {
      std::vector<float> x(n + 1);
      if(std::fread(x.data(), 4, n, in) != n) return 3;
      std::printf("%llx\n", static_cast<unsigned long long>(pattern(cf_total32(n, x.data()))));
    }
  }
  return 0;
}
"""

LIMITS = {"f64": (value(0x7FEFFFFFFFFFFFFF, "f64"), value(1, "f64"), 53, -1022, 1023),
          "f32": (value(0x7F7FFFFF, "f32"), value(1, "f32"), 24, -126, 127)}  # fmt: skip


def boundaries(fmt: str):
    """Terms whose sums sit on or beside a rounding boundary of `fmt`, and the special values."""
    big, tiny, p, emin, emax = LIMITS[fmt]
    half = 2.0**-p  # half an ulp of 1
    top = 2.0 ** (emax - p)  # half an ulp of the largest value
    least_normal = 2.0**emin
    yield "empty", []
    for v in (1.0, 3.0, -0.0, 0.0, tiny, -tiny, least_normal, big, -big, math.inf, -math.inf, math.nan):
        yield f"one {v!r}", [v]
    yield "every term -0.0", [-0.0] * 9
    yield "-0.0 and +0.0", [-0.0, 0.0, -0.0]
    yield "a value and its negation", [1.5, -1.5]
    yield "cancellation", [big / 2, 1.0, -big / 2]
    yield "cancellation below", [2.0**60, 1.0, -(2.0**60), 2.0**-30]
    yield "tie to even, down", [1.0, half]
    yield "tie to even, up", [1.0 + 2 * half, half]
    yield "just above a tie", [1.0, half, tiny]
    yield "just below a tie", [1.0, half, -tiny]
    yield "a tie below a power of two", [1.0, 1.0, -half]
    yield "just above that tie", [2.0, -half, tiny]
    yield "just below that tie", [2.0, -half, -tiny]
    yield "one ulp below a power of two", [2.0, -2 * half]
    yield "the largest value and half its ulp", [big, top]
    yield "just below overflow", [big, top, -tiny]
    yield "over the top and back", [big, big, -big]
    yield "overflow", [big, big]
    yield "negative overflow", [-big, -big, 1.0]
    yield "subnormals", [tiny] * 7
    yield "the least normal less the least subnormal", [least_normal, -tiny]
    yield "NaN", [1.0, math.nan, 2.0]
    yield "an infinity beside finite overflow", [math.inf, -big, -big]
    yield "both infinities", [math.inf, 1.0, -math.inf]
    yield "minus infinity twice", [-math.inf, 5.0, -math.inf]
    if fmt == "f32":  # exact sums a double would round onto an f32 tie: rounding twice would get these wrong
        yield "no double rounding, up", [1.0, 2.0**-24, 2.0**-100]
        yield "no double rounding, down", [1.0, 2.0**-24, -(2.0**-100)]
        yield "no double rounding at the top", [big, 2.0**103, -(2.0**-60)]


def randoms(fmt: str):
    """Random terms at sizes around the chunk (256), the pool's cutoff (16384) and many blocks."""
    rng = random.Random(1 if fmt == "f64" else 2)
    _, _, _, emin, emax = LIMITS[fmt]
    least = emin - (53 if fmt == "f64" else 24) + 1
    for n in (255, 256, 257, 16_383, 16_385, 65_537, 1_000_003):
        yield f"uniform {n}", [rng.random() for _ in range(n)]
        yield f"signed {n}", [rng.uniform(-1, 1) for _ in range(n)]
        if n > 70_000:
            continue
        yield f"wide {n}", [rng.choice((-1, 1)) * rng.random() * 2.0 ** rng.randint(least, emax - 40) for _ in range(n)]
        yield f"medium {n}", [rng.uniform(-1, 1) * 2.0 ** rng.randint(-60, 60) for _ in range(n)]
        pairs = [rng.uniform(-1, 1) * 2.0 ** rng.randint(-30, 30) for _ in range(n // 2)]
        yield f"pairs that cancel {n}", [*pairs, *(-x for x in reversed(pairs)), 2.0**-40]
        yield f"subnormal {n}", [rng.choice((-1, 1)) * rng.randint(1, 2**20) * 2.0**least for _ in range(n)]
    many = [rng.uniform(-1, 1) for _ in range(300_000)]
    for at, special in ((123_457, math.nan), (5, math.inf), (299_999, -math.inf)):
        yield f"{special!r} among many", [*many[:at], special, *many[at + 1 :]]
    if fmt == "f64":  # terms near the top: a chunk of them overflows its expansions and is added term by term
        huge = [*many, 2.0**1020, 2.0**1020, -(2.0**1020), 2.0**1015]
        yield "near overflow among many", huge
        yield "overflow among many", [*many, 2.0**1023, 2.0**1023]
    yield "every term -0.0, many", [-0.0] * 70_000


def cases(fmt: str):
    for name, terms in [*boundaries(fmt), *randoms(fmt)]:
        yield name, [t if fmt == "f64" else f32(t) for t in terms]


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    """Every case written to one file, with the oracle's pattern for each."""
    path = tmp_path_factory.mktemp("exact_sum") / "cases.bin"
    names, wanted, records = [], [], []
    for fmt in ("f64", "f32"):
        for name, terms in cases(fmt):
            code = "<d" if fmt == "f64" else "<f"
            records.append(bytes([8 if fmt == "f64" else 4]) + struct.pack("<Q", len(terms)))
            records.append(struct.pack(f"<{len(terms)}{code[1]}", *terms))
            names.append(f"{fmt} {name}")
            wanted.append(correct(terms, fmt))
    path.write_bytes(b"".join(records))
    return path, names, wanted


def agree(done, suite) -> None:
    _, names, wanted = suite
    assert done.returncode == 0, (done.returncode, done.stderr[-3000:])
    got = done.stdout.split()
    assert len(got) == len(names)
    wrong = [f"{n}: {g}, not {w:x}" for n, g, w in zip(names, got, wanted, strict=True) if g != f"{w:x}"]
    assert not wrong, wrong[:10]


def lanes(tmp_path, cxx, suite, counts, *flags, env=()):
    """The program built once by the project's command line for `cxx` plus `flags`, then run at each lane count."""
    first, *rest = counts
    at = {**os.environ, **dict(env), "CAIRN_LANES": str(first)}
    agree(contract(tmp_path, compile_source(SOURCE)[0], cxx, *flags, entry=None, beside={"main.cpp": MAIN}, env=at,
                   args=(str(suite[0]),)), suite)  # fmt: skip
    for count in rest:
        at["CAIRN_LANES"] = str(count)
        agree(subprocess.run([str(tmp_path / "p"), str(suite[0])], capture_output=True, text=True, env=at,
                             timeout=300), suite)  # fmt: skip


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_lane_count_gives_the_correctly_rounded_sum(tmp_path, cxx, suite):
    lanes(tmp_path, cxx, suite, range(1, 17))


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_sum_is_clean_under_the_address_and_undefined_behaviour_sanitizers(tmp_path, cxx, suite):
    lanes(tmp_path, cxx, suite, (1, 3, 16), *ADDRESS_AND_UB, "-fno-omit-frame-pointer",
          env={"ASAN_OPTIONS": "detect_leaks=1"})  # fmt: skip


def test_the_blocks_merge_clean_under_the_thread_sanitizer(tmp_path, suite):
    done = watched(tmp_path, compile_source(SOURCE)[0], "clang++", "thread", entry=None, beside={"main.cpp": MAIN},
                   args=(str(suite[0]),))  # fmt: skip
    assert "WARNING: ThreadSanitizer" not in done.stderr
    agree(done, suite)


@pytest.mark.parametrize("ty", ["f64", "f32"])
def test_a_float_product_on_the_pool_is_refused(ty):
    source = f"fn f(n:usize, x:ro<{ty}>[n]) -> {ty} {{ let p = reduce * parallel i in n yield x[i]; return p; }}"
    said = refused("E-REDUCE-ORDER", source)
    assert "reduce + parallel is the exact sum" in said["message"]


def test_the_receipt_names_the_sum_and_the_projection_keeps_it():
    cpp, receipt = compile_source(SOURCE)
    rows = receipt["functions"]
    assert rows["total64"]["numerics"] == [
        {"line": 3, "op": "sum", "from": "f64", "to": "f64", "rounding": "nearest-even", "sum": "exact"}
    ]
    assert rows["total32"]["numerics"][0]["to"] == "f32" and "par:host" in rows["total32"]["effects"]
    assert cpp.count("cr::par::sum<double>(") == 3 and "cr::par::sum<float>(" in cpp
    canonical = round_trips(SOURCE)
    assert "reduce + parallel i in n yield x[i]" in canonical and "reduce + out[0] parallel i" in canonical


def test_an_implementation_may_not_trade_the_written_order_for_the_exact_sum():
    source = """
fn total(n:usize, x:ro<f64>[n]) -> f64 effects(read:x, trap, ffi_precondition, par:host) {
  let s = reduce + for i in n yield x[i];
  return s;
}

fn pooled(n:usize, x:ro<f64>[n]) -> f64 implements total {
  let s = reduce + parallel i in n yield x[i];
  return s;
}
"""
    assert "rounds where total does not" in refused("E-IMPL-NUMERICS", source)["message"]


def test_the_model_prices_independent_additions_and_no_fold_chain():
    program, checker, _ = compile_program(SOURCE)
    counter = Counter(program, checker)
    functions = {f.name: f for f in program.functions}
    region = counter.function(functions["total64"]).regions[0]
    assert region.kind == "pooled" and "f64_fold" not in region.body.ops
    assert region.body.ops["f64"].value({}) == 12
    wider = counter.function(functions["total32"]).regions[0]
    assert wider.body.ops["convert"].value({}) == 1 and "f32_fold" not in wider.body.ops

"""The model against the machine: a native build of the fragment agrees with the concrete evaluator."""

import random
import shutil
import subprocess

import pytest

from cairn.compiler.cairnc import RUNTIME_FILES, compile_source
from cairn.compiler.tree import CPP, VOID, is_view
from cairn.verify.scalar_concrete import Concrete
from cairn.verify.scalar_semantics import equivalent, prepared
from cairn.verify.scalar_values import bounds, decoded, encoded, rounded

# The model against the machine ----------------------------------------------------------------

NATIVE = """
struct Pair { a:u64; b:u64; }
enum Sign { Neg(u64); Zero; Pos(u64); }
fn classify(x:i64) -> Sign {
  if x < 0 { return Sign.Neg(u64(-x)); }
  if x == 0 { return Sign.Zero; }
  return Sign.Pos(u64(x));
}
fn magnitude(x:i64) -> u64 {
  match classify(x) { Sign.Neg(v) => { return v; } Sign.Zero => { return 0; } Sign.Pos(v) => { return v; } }
}
fn window(x:u64) -> u64 {
  stack a:u64[4] = zeroed;
  for i in 0..4 { a[i] = add_wrap(x, u64(i)); }
  let mut t:u64 = 0;
  for i in 0..4 { if (i & 1) == 1 { continue; } t = add_wrap(t, a[i]); }
  return t;
}
fn scaled(x:f64) -> u32 { let p = Pair(1, 2); return u32((x * 2.0) + f64(p.b)); }
fn narrow(x:f64, y:f64) -> f32 { return f32(x) / f32(y); }
fn guarded(x:f32, n:u64) -> f32 { if n == 0 { return x; } return x + f32(n); }
fn element(n:usize, xs:ro<u64>[n], i:usize) -> u64 { return xs[i]; }
fn stretch(n:usize, xs:ro<u8>[n], out:rw<u8>[n], k:u8) { for i in 0..n { out[i] = mul_wrap(xs[i], k); } }
fn picked(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) -> usize {
  let used = compact out for i in n where xs[i] > 3 yield xs[i];
  return used;
}
fn summed(n:usize, xs:ro<u8>[n]) -> u8 { let s = reduce + for i in n yield xs[i]; return s; }
fn total(m:usize, ys:ro<u8>[m]) -> u8 { let mut t:u8 = 0; for i in 0..m { t = add_wrap(t, ys[i]); } return t; }
fn halves(n:usize, xs:ro<u8>[n], mid:usize) -> u8 {
  return add_wrap(total(mid, xs[0..mid]), total(n-mid, xs[mid..n]));
}
fn bumped(p:rw<u64>, k:u64) -> u64 { p = add_wrap(p, k); return p; }
fn moved(n:usize, xs:ro<u8>[n]) -> u8 {
  let mut a = Buf[u8](n);
  let mut b = Buf[u8](2);
  for i in 0..n { a[i] = add_wrap(xs[i], 1); }
  swap(a, b);
  let c = take(b);
  let mut t:u8 = u8(len(a));
  for i in 0..len(c) { t = add_wrap(t, c[i]); }
  return add_wrap(t, u8(len(b)));
}
fn pair(a:usize, xs:rw<u64>[a], b:usize, ys:rw<u64>[b]) { for i in 0..a { xs[i] = 1; } for i in 0..b { ys[i] = 2; } }
fn split(n:usize, zs:rw<u64>[n], mid:usize) { if mid > n { return; } pair(mid, zs[0..mid], n - mid, zs[mid..n]); }
fn scratch(n:usize, xs:ro<u8>[n]) -> u8 {
  buffer tmp:u8[n] = zeroed;
  for i in 0..n { tmp[i] = add_wrap(xs[i], 1); }
  let mut t:u8 = 0;
  for i in 0..n { t = add_wrap(t, tmp[i]); }
  return t;
}
"""
POOL = [0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 2.0, 255.5, -255.5, 4294967295.5, 1e18, 1e308, -1e308, 2.0**53 + 1]
POOL += [float("inf"), float("-inf"), float("nan"), 1.1754943508222875e-38, 3.4028234663852886e38]


def sampled(ty, rng):
    if ty == "bool":
        return rng.random() < 0.5
    if ty in {"f32", "f64"}:
        raw = rng.choice(POOL) if rng.random() < 0.5 else decoded(rng.getrandbits(64), "f64")
        return rounded(raw, ty)
    lo, hi = bounds(ty)
    return rng.choice([lo, hi, 0, 1, rng.randint(lo, hi)])


def word(value, ty):
    """One 64-bit observation, the way the generated program prints its result."""
    if ty in {"f32", "f64"}:
        return encoded(value, ty)
    return int(value) % (1 << 64)


def literal(value, ty):
    if ty == "f32":
        return f"asf32({encoded(value, 'f32')}u)"
    if ty == "f64":
        return f"asf64({encoded(value, 'f64')}ull)"
    return f"static_cast<{CPP[ty]}>({int(value) % (1 << 64)}ull)"


HARNESS = """
#include <cstdio>
#include <cstdlib>
#include <cstring>
static float asf32(unsigned int b) { float v; std::memcpy(&v, &b, 4); return v; }
static double asf64(unsigned long long b) { double v; std::memcpy(&v, &b, 8); return v; }
static unsigned long long word(float v) { unsigned int b; std::memcpy(&b, &v, 4); return b; }
static unsigned long long word(double v) { unsigned long long b; std::memcpy(&b, &v, 8); return b; }
template <class T> unsigned long long word(T v) {
  return static_cast<unsigned long long>(static_cast<std::uint64_t>(v));
}
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  switch (std::atoi(argv[1])) {
%s
    default: return 2;
  }
  return 0;
}
"""


PERTURBED = [
    ("magnitude", "Sign.Neg(v) => { return v; }", "Sign.Neg(v) => { return add_wrap(v, 1); }", "true"),
    ("window", "if (i & 1) == 1 { continue; }", "if (i & 1) == 0 { continue; }", "true"),
    ("scaled", "f64(p.b)", "f64(p.a)", "true"),
    ("narrow", "return f32(x) / f32(y);", "return f32(x / y);", "true"),
    ("guarded", "if n == 0 { return x; }", "if n == 1 { return x; }", "true"),
    ("element", "return xs[i];", "if i < n { return xs[i]; } return 0;", "true"),
    ("stretch", "out[i] = mul_wrap(xs[i], k);", "out[i] = add_wrap(xs[i], k);", "n<=3"),
    ("picked", "where xs[i] > 3", "where xs[i] >= 3", "n<=2"),
    ("summed", "reduce + for", "reduce add_wrap for", "n<=3"),
    ("halves", "total(n-mid, xs[mid..n])", "total(n-mid, xs[0..n-mid])", "n<=3"),
    ("bumped", "p = add_wrap(p, k);", "p = add_wrap(k, 1);", "true"),
    ("split", "ys[i] = 2;", "ys[i] = 3;", "n<=3"),
    ("moved", "a[i] = add_wrap(xs[i], 1);", "a[i] = add_wrap(xs[i], 2);", "n>=1 && n<=3"),
    ("scratch", "tmp[i] = add_wrap(xs[i], 1);", "tmp[i] = add_wrap(xs[i], 2);", "n<=2"),
]


def drawn(f, rng):
    """One input the entry guards admit: an extent, then storage holding exactly that many elements."""
    extents = {t.extent for _, t in f.params if is_view(t)}
    args: dict = {}
    for n, t in f.params:
        if is_view(t):
            args[n] = [sampled(t.name, rng) for _ in range(int(t.extent) if t.extent.isdigit() else args[t.extent])]
        elif n in extents or (t.name == "usize" and rng.random() < 0.5):
            args[n] = rng.choice([0, 1, 2, 3])  # A small extent, or an index that is often inside one.
        else:
            args[n] = sampled(t.name, rng)
    return args


def arm(k, f, name, args):
    """The switch arm that lends this input to the native function and prints what a caller observes."""
    lines, passed, shown = [], [], []
    for i, (n, t) in enumerate(f.params):
        held = f"s{i}"
        if is_view(t):
            items = ", ".join(literal(v, t.name) for v in args[n]) or literal(0, t.name)
            lines.append(f"{CPP[t.name]} {held}[] = {{{items}}};")  # Never empty: the pointer must be valid.
            passed.append(held)
            shown += [f"{held}[{j}]" for j in range(len(args[n]))] if t.mode == "rw" else []
        elif t.mode == "rw":
            lines.append(f"{CPP[t.name]} {held} = {literal(args[n], t.name)};")
            passed += [held]
            shown += [held]
        else:
            passed.append(literal(args[n], t.name))
    call = f"cf_{name}({', '.join(passed)})"
    lines.append(f"{call};" if f.ret == VOID else f'std::printf("%llu ", word({call}));')
    lines += [f'std::printf("%llu ", word({x}));' for x in shown]
    return f"    case {k}: {{ " + " ".join(lines) + ' std::printf("\\n"); break; }'


def expected_words(src, f, outcome):
    """What the harness prints for an outcome that returned: the result, then every rw parameter."""
    words = [] if f.ret == VOID else [word(outcome["return"], f.ret.name)]
    for n, t in f.params:
        if t.mode != "rw":
            continue
        held = outcome["written"][n]
        words += [word(v, t.name) for v in held] if is_view(t) else [word(held, t.name)]
    return words


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_the_model_agrees_with_the_machine(tmp_path):
    """Build the fragment natively and confirm the concrete evaluator predicts what it does."""
    src = prepared(NATIVE)
    rng = random.Random(20260918)
    cases = []
    for name, before, after, assume in PERTURBED:  # Every solver counterexample is also run on the machine.
        r = equivalent(NATIVE, NATIVE.replace(before, after), name, assume=assume,
                       allow_reference_traps=True, timeout_ms=20000)  # fmt: skip
        assert r["status"] == "counterexample", r
        cases.append((name, r["counterexample"]))
    for name in [n for n, _, _, _ in PERTURBED]:
        for _ in range(10):
            cases.append((name, drawn(src.functions[name], rng)))
    arms = [arm(k, src.functions[name], name, args) for k, (name, args) in enumerate(cases)]
    (tmp_path / "p.cpp").write_text(compile_source(NATIVE)[0] + HARNESS % "\n".join(arms))
    for header, text in RUNTIME_FILES.items():
        (tmp_path / header).write_text(text)
    build = ["clang++", "-std=c++20", "-O1", "-fno-exceptions", str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")]
    subprocess.run(build, check=True, timeout=180, capture_output=True)
    traps = 0
    for k, (name, args) in enumerate(cases):
        native = subprocess.run([tmp_path / "p", str(k)], capture_output=True, timeout=60)
        model = Concrete(src).outcome(name, args)
        if not model["defined"]:
            traps += 1
            assert native.returncode == -6, (name, args, native)
            continue
        assert native.returncode == 0, (name, args, native.stderr)
        expected = expected_words(src, src.functions[name], model)
        assert [int(w) for w in native.stdout.split()] == expected, (name, args, model, native.stdout)
    assert traps >= 10, "The sample must exercise the guards, not only the total cases."

"""Storage floats: f16, bf16, f8e4m3 and f8e5m2 hold a value and convert, and never compute.

The compiled conversions are held to an independent model (tests/oracles/float_formats.py) that knows every value
each format holds as an exact rational and rounds by searching for the nearest. Every pattern is widened and
rounded back, every tie between two neighbours and the doubles either side of it are rounded, and random doubles
from the subnormals to the overflow cover the rest, under both compilers.
"""

import ctypes as C
import math
import random
import shutil
import subprocess

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.projects.toolchain import command
from cairn.verify.scalar_semantics import equivalent
from emitted import WARNINGS, emit, native, refused, run, sanitized
from oracles.float_formats import (
    FORMATS,
    TRAP,
    Format,
    double_bits,
    f16_by_struct,
    from_double_bits,
    quantize_integer,
    quantize_integer_stochastic,
)

PATTERN = {"f16": "u16", "bf16": "u16", "f8e4m3": "u8", "f8e5m2": "u8"}
CTYPE = {"u8": C.c_uint8, "u16": C.c_uint16, "u32": C.c_uint32, "u64": C.c_uint64}
INTEGERS = {"i8": (-128, 127, C.c_int8), "u8": (0, 255, C.c_uint8), "i16": (-32768, 32767, C.c_int16),
            "u16": (0, 65535, C.c_uint16)}  # fmt: skip


def conversions(name: str) -> str:
    u = PATTERN[name]
    return f"""
fn widen_{name}(n:usize, ps:ro<{u}>[n], out:rw<u64>[n]) {{ for i in 0..n {{ out[i] = to_bits(f64(from_bits[{name}](ps[i]))); }} }}
fn widen32_{name}(n:usize, ps:ro<{u}>[n], out:rw<u32>[n]) {{ for i in 0..n {{ out[i] = to_bits(f32(from_bits[{name}](ps[i]))); }} }}
fn narrow_{name}(n:usize, xs:ro<u64>[n], out:rw<{u}>[n]) {{ for i in 0..n {{ out[i] = to_bits({name}(from_bits[f64](xs[i]))); }} }}
fn narrow32_{name}(n:usize, xs:ro<u32>[n], out:rw<{u}>[n]) {{ for i in 0..n {{ out[i] = to_bits({name}(from_bits[f32](xs[i]))); }} }}
fn quantize_{name}(n:usize, xs:ro<u32>[n], scale:f32, out:rw<{u}>[n]) {{
  for i in 0..n {{ out[i] = to_bits(quantize[{name}](from_bits[f32](xs[i]), scale)); }}
}}
"""


def integer_quantizer(name: str) -> str:
    return f"""
fn quantize_{name}(n:usize, xs:ro<u32>[n], scale:f32, out:rw<{name}>[n]) {{
  for i in 0..n {{ out[i] = quantize[{name}](from_bits[f32](xs[i]), scale); }}
}}
"""


def stochastic(name: str) -> str:
    u = PATTERN.get(name, name)
    wrap = "to_bits" if name in PATTERN else ""
    return f"""
fn stochastic_{name}(n:usize, xs:ro<u32>[n], scale:f32, noise:ro<u32>[n], out:rw<{u}>[n]) {{
  for i in 0..n {{ out[i] = {wrap}(quantize_stochastic[{name}](from_bits[f32](xs[i]), scale, noise[i])); }}
}}
"""


SOURCE = "".join(map(conversions, FORMATS)) + "".join(map(integer_quantizer, INTEGERS))
SOURCE += "".join(map(stochastic, [*FORMATS, *INTEGERS]))


@pytest.fixture(scope="module", params=["g++", "clang++"])
def lib(request, tmp_path_factory):
    if not shutil.which(request.param):
        pytest.skip(f"{request.param} unavailable")
    directory = tmp_path_factory.mktemp(request.param.replace("+", "p"))
    source, artifact = emit(directory, compile_source(SOURCE)[0], entry=None)
    subprocess.run(command(request.param, source, artifact + ".so", kind="library"), check=True, timeout=240)
    return C.CDLL(artifact + ".so")


def call(lib, symbol: str, inputs: list[int], in_type: str, out_type: str, *extra) -> list[int]:
    n = len(inputs)
    xs, out = (CTYPE[in_type] * n)(*inputs), (CTYPE[out_type] * n)()
    getattr(lib, "cf_" + symbol)(C.c_size_t(n), xs, *extra, out)
    return list(out)


def agree(fmt: Format, got: int, want) -> bool:
    if isinstance(want, tuple):  # a NaN: which one is not part of the contract, its sign is
        return fmt.is_nan(got) and (got & fmt.sign) == want[1]
    return got == want


@pytest.mark.parametrize("name", FORMATS)
def test_every_pattern_widens_to_the_value_it_denotes(lib, name):
    fmt = Format(name)
    patterns = list(range(2**fmt.width))
    wide = call(lib, "widen_" + name, patterns, PATTERN[name], "u64")
    single = call(lib, "widen32_" + name, patterns, PATTERN[name], "u32")
    for p, d, f in zip(patterns, wide, single, strict=True):
        want = fmt.value(p)
        got = from_double_bits(d)
        assert (math.isnan(got) and math.isnan(want)) or double_bits(got) == double_bits(want), hex(p)
        assert math.isnan(got) or f == C.c_uint32.from_buffer_copy(C.c_float(want)).value, hex(p)


@pytest.mark.parametrize("name", FORMATS)
def test_every_value_rounds_to_itself_and_every_tie_to_the_even_neighbour(lib, name):
    fmt = Format(name)
    cases: dict[float, object] = {}
    finite = fmt.values[:-1]
    for i, (v, p) in enumerate(zip(finite, fmt.patterns[:-1], strict=True)):
        for sign in (0, fmt.sign):
            s = -1.0 if sign else 1.0
            cases[s * float(v)] = sign | p
            if i + 1 < len(finite):
                mid, q = (v + finite[i + 1]) / 2, fmt.patterns[i + 1]
                even = p if p % 2 == 0 else q
                cases[s * float(mid)] = sign | even
                cases[math.nextafter(s * float(mid), s * math.inf)] = sign | q
                cases[math.nextafter(s * float(mid), 0.0)] = sign | p
    cases.pop(-0.0, None)
    cases[-0.0] = fmt.sign
    xs = list(cases)
    got = call(lib, "narrow_" + name, [double_bits(x) for x in xs], "u64", PATTERN[name])
    wrong = [(x, hex(g), cases[x]) for x, g in zip(xs, got, strict=True) if g != cases[x]]
    assert not wrong, wrong[:5]


@pytest.mark.parametrize("name", FORMATS)
def test_random_doubles_round_as_the_nearest_value_search_says(lib, name):
    fmt = Format(name)
    rng = random.Random(20260922)
    xs = [math.nan, -math.nan, 0.0, -0.0, 5e-324, 1e308, -1e308]
    top = float(fmt.values[-2])
    for _ in range(4000):
        xs.append(rng.choice((-1, 1)) * math.ldexp(rng.random(), rng.randint(-40, math.frexp(top)[1] + 2)))
        xs.append(from_double_bits(rng.getrandbits(64)))
    wanted = [fmt.narrow(x) for x in xs]
    if not fmt.infinite:  # f8e4m3 traps where IEEE would give infinity; those run in their own process below
        xs, wanted = [x for x, w in zip(xs, wanted, strict=True) if w != TRAP], [w for w in wanted if w != TRAP]
    got = call(lib, "narrow_" + name, [double_bits(x) for x in xs], "u64", PATTERN[name])
    wrong = [(x, hex(g), w) for x, g, w in zip(xs, got, wanted, strict=True) if not agree(fmt, g, w)]
    assert not wrong, wrong[:5]
    singles = [struct_single(x) for x in xs if math.isfinite(x) and abs(x) < 3.4e38]
    got = call(lib, "narrow32_" + name, [bits for _, bits in singles], "u32", PATTERN[name])
    wrong = [(x, hex(g)) for (x, _), g in zip(singles, got, strict=True) if not agree(fmt, g, fmt.narrow(x))]
    assert not wrong, wrong[:5]


def struct_single(x: float) -> tuple[float, int]:
    bits = C.c_uint32.from_buffer_copy(C.c_float(x)).value
    return C.c_float.from_buffer_copy(C.c_uint32(bits)).value, bits


def test_f16_agrees_with_python_s_own_binary16_packing(lib):
    rng = random.Random(7)
    xs = [math.ldexp(rng.random(), rng.randint(-30, 17)) * rng.choice((-1, 1)) for _ in range(20000)]
    got = call(lib, "narrow_f16", [double_bits(x) for x in xs], "u64", "u16")
    assert [hex(g) for g in got] == [hex(f16_by_struct(x)) for x in xs]


@pytest.mark.parametrize("name", FORMATS)
def test_quantize_divides_rounds_once_and_saturates(lib, name):
    fmt = Format(name)
    rng = random.Random(11)
    scales = [1.0, 0.25, 3.0, 1e-3, 7.3e-5, 1e30]
    for scale in scales:
        xs = [struct_single(float(v) * scale)[0] for v in fmt.values[:-1]]  # exact multiples and their ties
        xs += [struct_single(float((a + b) / 2) * scale)[0] for a, b in zip(fmt.values, fmt.values[1:-1])]
        xs += [struct_single(rng.uniform(-2, 2) * float(fmt.values[-2]) * scale)[0] for _ in range(2000)]
        xs += [math.inf, -math.inf, math.nan, -0.0]
        bits = [struct_single(x)[1] for x in xs]
        scaled = struct_single(scale)[0]
        got = call(lib, "quantize_" + name, bits, "u32", PATTERN[name], C.c_float(scaled))
        wrong = [(x, hex(g)) for x, g in zip(xs, got, strict=True) if not agree(fmt, g, fmt.quantize(x, scaled))]
        assert not wrong, (scale, wrong[:5])


@pytest.mark.parametrize("name", INTEGERS)
def test_integer_quantize_rounds_half_to_even_and_clamps(lib, name):
    low, high, ctype = INTEGERS[name]
    rng = random.Random(3)
    for scale in (struct_single(s)[0] for s in (1.0, 0.5, 0.1, 17.0)):
        xs = [struct_single((k + 0.5) * scale)[0] for k in range(low - 3, high + 3)][::7]
        xs += [struct_single(rng.uniform(1.5 * low - 10, 1.5 * high + 10) * scale)[0] for _ in range(3000)]
        xs += [math.inf, -math.inf, -0.0]
        n = len(xs)
        raw, out = (C.c_uint32 * n)(*(struct_single(x)[1] for x in xs)), (ctype * n)()
        getattr(lib, "cf_quantize_" + name)(C.c_size_t(n), raw, C.c_float(scale), out)
        wrong = [(x, g) for x, g in zip(xs, out, strict=True) if g != quantize_integer(x, scale, low, high)]
        assert not wrong, (scale, wrong[:5])


@pytest.mark.parametrize("name", [*FORMATS, *INTEGERS])
def test_stochastic_rounding_goes_up_exactly_where_the_noise_is_below_the_fraction(lib, name):
    rng = random.Random(17)
    xs, noise = [], []
    for _ in range(6000):
        xs.append(struct_single(rng.uniform(-1.2, 1.2) * rng.choice((1.0, 3.0, 200.0, 5e4)))[0])
        noise.append(rng.getrandbits(32))
    xs += [0.0, -0.0, 1.0, 2.5, -2.5, 1e30, -1e30]
    noise += [0, 2**32 - 1, 0, 0, 2**32 - 1, 5, 5]
    bits = [struct_single(x)[1] for x in xs]
    for scale in (1.0, 0.03125, struct_single(0.37)[0]):
        n = len(xs)
        raw, grain = (C.c_uint32 * n)(*bits), (C.c_uint32 * n)(*noise)
        if name in FORMATS:
            fmt, out = Format(name), (CTYPE[PATTERN[name]] * n)()
            getattr(lib, "cf_stochastic_" + name)(C.c_size_t(n), raw, C.c_float(scale), grain, out)
            wanted = [fmt.stochastic(x, scale, k) for x, k in zip(xs, noise, strict=True)]
            wrong = [(x, k, hex(g)) for x, k, g, w in zip(xs, noise, out, wanted, strict=True) if not agree(fmt, g, w)]
        else:
            low, high, ctype = INTEGERS[name]
            out = (ctype * n)()
            getattr(lib, "cf_stochastic_" + name)(C.c_size_t(n), raw, C.c_float(scale), grain, out)
            wanted = [quantize_integer_stochastic(x, scale, low, high, k) for x, k in zip(xs, noise, strict=True)]
            wrong = [(x, k, g) for x, k, g, w in zip(xs, noise, out, wanted, strict=True) if g != w]
        assert not wrong, (scale, wrong[:5])


def test_stochastic_rounding_is_unbiased_over_evenly_spread_noise(lib):
    """Over 4096 noises spread evenly across the u32 range, a value 0.3 of the way from 1 to 2 rounds up 1229
    times: the fraction to within one part in 4096."""
    n = 4096
    xs = (C.c_uint32 * n)(*[struct_single(1.3)[1]] * n)
    grain = (C.c_uint32 * n)(*[k * 2**20 for k in range(n)])
    out = (C.c_int8 * n)()
    lib.cf_stochastic_i8(C.c_size_t(n), xs, C.c_float(1.0), grain, out)
    ups = sum(v == 2 for v in out)
    assert set(out) == {1, 2} and abs(ups / n - 0.3) <= 1 / n


TRAPS = [
    ("f8e4m3(465.0)", "beyond 448, where IEEE would give infinity"),
    ("f8e4m3(-1e300)", "far beyond"),
    ("f8e4m3(from_bits[f64](0x7ff0000000000000))", "infinity itself"),
    ("quantize[f16](1.0, 0.0)", "a zero scale"),
    ("quantize[f16](1.0, -2.0)", "a negative scale"),
    ("quantize[bf16](1.0, from_bits[f32](0x7f800000))", "an infinite scale"),
    ("quantize[i8](from_bits[f32](0x7fc00000), 1.0)", "a NaN, which an integer cannot hold"),
]


@pytest.mark.parametrize("expression,why", TRAPS)
def test_what_a_format_cannot_hold_traps(tmp_path, expression, why):
    with pytest.raises(AssertionError, match="exit -6"):
        native(tmp_path, f"fn main() -> i32 {{ let x = {expression}; return 0; }}\n")


def test_the_largest_values_and_the_tie_at_464_do_not_trap(tmp_path):
    native(
        tmp_path,
        """fn main() -> i32 {
  if to_bits(f8e4m3(448.0)) != 0x7e || to_bits(f8e4m3(464.0)) != 0x7e || to_bits(f8e4m3(-448.0)) != 0xfe { return 1; }
  if to_bits(f8e5m2(61440.0)) != 0x7c || to_bits(f16(65520.0)) != 0x7c00 || to_bits(f16(65519.0)) != 0x7bff { return 2; }
  if to_bits(quantize[f8e4m3](1e30, 1.0)) != 0x7e || to_bits(quantize[f16](-1e30, 1.0)) != 0xfbff { return 3; }
  if to_bits(f8e4m3(from_bits[f64](0x7ff8000000000000))) != 0x7f { return 4; }
  return 0;
}
""",
    )


SELF_CHECK = """
fn main() -> i32 {
  for i in 0..65536 {
    let p = u16(i);
    let x = f64(from_bits[f16](p));
    if x == x && (to_bits(f16(x)) != p || to_bits(f16(f32(from_bits[f16](p)))) != p) { return 1; }
    let y = f64(from_bits[bf16](p));
    if y == y && (to_bits(bf16(y)) != p || to_bits(bf16(f32(from_bits[bf16](p)))) != p) { return 2; }
  }
  for i in 0..256 {
    let p = u8(i);
    let x = f64(from_bits[f8e4m3](p));
    if x == x && to_bits(f8e4m3(x)) != p { return 3; }
    let y = f64(from_bits[f8e5m2](p));
    if y == y && to_bits(f8e5m2(y)) != p { return 4; }
    if x == x && to_bits(quantize[f8e4m3](f32(x), 1.0)) != p { return 5; }
  }
  if quantize[i8](2.5, 1.0) != 2 || quantize[i8](3.5, 1.0) != 4 || quantize[u8](-7.0, 1.0) != 0 { return 6; }
  if quantize[i16](1e9, 3.0) != 32767 || quantize[u16](65535.5, 1.0) != 65535 { return 7; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_pattern_round_trips_under_the_sanitizers(tmp_path, cxx):
    done = run(tmp_path, compile_source(SELF_CHECK)[0], *sanitized(cxx), *WARNINGS, cxx=cxx)
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


@pytest.mark.parametrize(
    "code,source",
    [
        ("E-OPERATOR", "fn f(a:f16, b:f16) -> f16 = a + b;"),  # arithmetic happens in f32, after an exact widening
        ("E-OPERATOR", "fn f(a:bf16, b:bf16) -> bool = a < b;"),
        ("E-OPERATOR", "fn f(a:f8e4m3) -> bool = a == a;"),
        ("E-OPERATOR", "fn f(a:f16) -> f16 = -a;"),
        ("E-TYPE-MISMATCH", "fn f() -> f16 = 1.5;"),  # a literal is an f64 or an f32; f16(1.5) rounds it
        ("E-CAST", "fn f(x:u32) -> f16 = f16(x);"),  # an integer converts to f32 or f64 first
        ("E-CAST", "fn f(x:f16) -> bf16 = bf16(x);"),  # between storage formats, through f32: one rounding
        ("E-CAST", "fn f(x:f16) -> i32 = i32(x);"),
        ("E-QUANTIZE", "fn f(x:f32) -> u32 = quantize[u32](x, 1.0);"),  # one rounding is exact only to 16 bits
        ("E-QUANTIZE", "fn f(x:f32) -> f32 = quantize[f32](x, 1.0);"),
        ("E-TYPE-MISMATCH", "fn f(x:f64) -> f16 = quantize[f16](x, 1.0);"),  # the value and the scale are f32
        ("E-ARITY", "fn f(x:f32) -> f16 = quantize[f16](x);"),
        ("E-MATH-TYPE", "fn f(x:u8) -> u8 = from_bits[u8](x);"),
        ("E-TYPE-MISMATCH", "fn f(x:u32) -> f16 = from_bits[f16](x);"),  # the pattern of its own width
        ("E-MATH-TYPE", "fn f(x:f16) -> f16 = abs(x);"),
        ("E-CONST", "const HALF:f16 = f16(0.5);"),
        ("E-ARITY", "fn f(x:f32) -> i8 = quantize_stochastic[i8](x, 1.0);"),  # the noise is the caller's to draw
        ("E-TYPE-MISMATCH", "fn f(x:f32, k:u64) -> i8 = quantize_stochastic[i8](x, 1.0, k);"),
    ],
)
def test_what_the_storage_floats_refuse(code, source):
    refused(code, source)


@pytest.mark.parametrize(
    "source",
    ["fn f(x:f32) -> f32 = f32(f16(x));", "fn f(x:f16) -> f32 = f32(x);", "fn f(x:f32) -> i8 = quantize[i8](x, 2.0);"],
)
def test_the_value_model_answers_unknown_and_never_equivalent(source):
    assert equivalent(source, source, "f")["status"] == "unknown"


def test_the_receipt_states_each_rounding_the_source_wrote():
    rows = compile_source(
        "fn pack(x:f32, s:f32) -> f8e4m3 = quantize[f8e4m3](x, s);\n"
        "fn half(x:f64) -> f16 = f16(x);\n"
        "fn tight(x:f32) -> f8e4m3 = f8e4m3(x);\n"
        "fn wide(x:bf16) -> f32 = f32(x);\n"
    )[1]["functions"]
    assert rows["pack"]["numerics"] == [{"line": 1, "op": "quantize", "from": "f32", "to": "f8e4m3", "rounding":
        "nearest-even", "scale": "positive-finite", "overflow": "saturate", "nan": "nan"}]  # fmt: skip
    assert rows["pack"]["effects"] == ["trap"]  # the scale's guard
    assert rows["half"]["numerics"][0]["overflow"] == "infinity" and rows["half"]["effects"] == []
    assert rows["tight"]["numerics"][0]["overflow"] == "trap" and rows["tight"]["effects"] == ["trap"]
    assert "numerics" not in rows["wide"] and rows["wide"]["effects"] == []  # widening is exact
    noisy = compile_source("fn f(x:f32, k:u32) -> i8 = quantize_stochastic[i8](x, 0.5, k);")[1]["functions"]["f"]
    assert noisy["numerics"][0]["rounding"] == "stochastic-u32" and noisy["numerics"][0]["nan"] == "trap"


def test_the_canonical_projection_emits_the_same_program():
    assert compile_source(canonical_source(SELF_CHECK))[0] == compile_source(SELF_CHECK)[0]
    assert compile_source(canonical_source(SOURCE))[0] == compile_source(SOURCE)[0]


def test_storage_floats_are_values_that_fields_arrays_sums_and_owners_hold(tmp_path):
    native(
        tmp_path,
        """struct Row { scale:f32; w:Array[f8e4m3, 4]; }
enum Held { Half(f16); Nothing; }

fn widened(h:Held) -> f32 {
  match h {
    Half(v) => return f32(v);
    Nothing => return 0.0;
  }
}

fn main() -> i32 {
  let mut r = Row(0.5, Array[f8e4m3, 4]());
  for i in 0..4 { r.w[i] = quantize[f8e4m3](f32(i), r.scale); }
  let mut xs = Buf[bf16](3);
  xs[2] = bf16(3.0);
  let moved = take(xs);
  if f32(r.w[3]) * r.scale != 3.0 || f32(moved[2]) != 3.0 || len(xs) != 0 { return 1; }
  if widened(Held.Half(f16(0.25))) != 0.25 || to_bits(r.w[0]) != 0 { return 2; }
  return 0;
}
""",
    )


def test_a_device_lane_compiles_the_same_conversions(tmp_path):
    if not shutil.which("nvcc"):
        pytest.skip("nvcc unavailable")
    cpp = compile_source(
        "fn pack(n:usize, xs:ro<f32>[n]@device, out:rw<f8e4m3>[n]@device, h:rw<f16>[n]@device) {\n"
        "  parallel i in n { out[i] = quantize[f8e4m3](xs[i], 0.5); h[i] = f16(f32(out[i]) * 2.0); }\n"
        "}\n"
    )[0]
    source, artifact = emit(tmp_path, cpp, entry=None)
    line = [f for f in command("g++", source, artifact + ".o", cuda=True) if f not in {"-arch=native", "-shared"}]
    line[line.index("-o") : line.index("-o")] = ["-arch=sm_120", "-c"]  # a named architecture: nothing asks the device
    done = subprocess.run(line, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]  # compiled only; device code runs under `make gpu` alone

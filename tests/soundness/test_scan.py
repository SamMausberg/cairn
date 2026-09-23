"""`scan op [exclusive] out for|parallel i in n yield e` writes every prefix of the yields into out.

out[i] is op over e(0..i], or over e(0..i) when exclusive, and the scan's value is op over all of them. The
operators are reduce's, so each is associative on what it takes: a pooled or device scan gives exactly the
in-order answer, and checked unsigned `+` traps exactly when the in-order total overflows, since every prefix,
block total and offset is at most that total. Floats scan only in the written order. The yield runs once per
index and may read out only at its own element, which makes an in-place scan well defined.
"""

import json
import os
import shutil
import signal

import pytest

from cairn.agent.projection import canonical_source
from cairn.cli import main
from cairn.compiler.cairnc import compile_source
from cairn.compiler.syntax import Parser
from cairn.verify.scalar_semantics import equivalent
from emitted import contract, device_build, native, on_device, refused, watched

VIEWS = "fn f(n:usize, out:rw<u64>[n], x:ro<u64>[n], s:ro<i32>[n], d:ro<f64>[n], w:rw<i32>[n], g:rw<f64>[n]) {\n  "


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-SCAN-OP", "scan + w for i in n yield s[i];"),  # A signed prefix may overflow while the total fits.
        ("E-SCAN-OP", "scan * out for i in n yield x[i];"),  # So may a checked product before a zero.
        ("E-SCAN-OP", "scan & g for i in n yield d[i];"),
        ("E-SCAN-ORDER", "scan + g parallel i in n yield d[i];"),  # Blocks reassociate a float sum.
        ("E-SCAN-TARGET", "scan add_wrap x for i in n yield x[i];"),  # A read-only view cannot be written.
        ("E-SCAN-TARGET", "let mut k:u64 = 0; scan add_wrap k for i in n yield x[i];"),
        ("E-SCAN-EXTENT", "let m = n / 2; scan add_wrap out for i in m yield x[i];"),
        ("E-PARALLEL-RACE", "scan add_wrap out for i in n yield out[n - 1 - i];"),  # Only out[i] may be read.
        ("E-PARALLEL-NEST", "parallel j in n { scan add_wrap out for i in n yield x[i]; }"),
        ("E-COLLECT-BINDING", "let mut t = scan add_wrap out for i in n yield x[i];"),
        ("E-SHADOW", "let t = scan add_wrap out for n in n yield x[0];"),
        ("E-TYPE-MISMATCH", "let t:u32 = scan add_wrap out for i in n yield x[i];"),
    ],
)
def test_rejections(code, body):
    refused(code, VIEWS + body + "\n}")


def test_scan_is_an_ordinary_name_everywhere_else():
    source = (
        "fn scan(exclusive:u64) -> u64 = exclusive + 1;\nfn f() -> u64 { let scan_of = scan(1); return scan_of; }\n"
    )
    assert compile_source(source)[1]["functions"]["f"]


def test_an_output_may_be_named_exclusive():
    source = "fn f(n:usize, exclusive:rw<u64>[n]) { scan add_wrap exclusive for i in n yield exclusive[i]; }\n"
    scan = Parser(source).parse().functions[0].body[0]
    assert scan.tag == "scan" and not scan.exclusive and scan.exprs[0].val == "exclusive"
    assert compile_source(canonical_source(source))[0] == compile_source(source)[0]


def test_rows_say_where_a_scan_runs_and_what_it_writes():
    source = (
        "fn plain(n:usize, out:rw<u64>[n], x:ro<u64>[n]) -> u64 { let t = scan + out for i in n yield x[i]; return t; }\n"
        "fn pooled(n:usize, out:rw<u64>[n], x:ro<u64>[n]) { scan add_wrap exclusive out parallel i in n yield x[i]; }\n"
        "fn device(n:usize, out:rw<u32>[n]@device, x:ro<u32>[n]@device) -> u32 {\n"
        "  let t = scan + out parallel i in n yield x[i];\n  return t;\n}\n"
        "fn floats(n:usize, out:rw<f64>[n], x:ro<f64>[n]) { scan + out for i in n yield x[i]; }\n"
    )
    cpp, receipt = compile_source(source)
    rows = {name: set(row["effects"]) for name, row in receipt["functions"].items()}
    assert {"write:out", "read:x", "trap"} <= rows["plain"] and not any(e.startswith("par:") for e in rows["plain"])
    assert {"write:out", "par:host"} <= rows["pooled"] and "alloc" not in rows["pooled"]
    assert {"par:device", "gpu_alloc", "gpu_free", "trap"} <= rows["device"]  # CUB's scratch, and the checked sum
    assert (
        "cr::par::scan<true, std::uint64_t>(" in cpp
        and "cr::gpu::scan_on<false, cr::Sum<std::uint32_t>>(cr::gpu::here(), " in cpp
    )
    guards = receipt["functions"]["plain"]
    assert guards["discharged_check_sites"]["bounds"] == guards["syntactic_check_sites"]["bounds"] == 2  # x[i], out[i]


@pytest.mark.parametrize(
    "line",
    [
        "let t = scan + out for i in n yield x[i];",
        "let t = scan max exclusive out parallel i in n yield x[i];",
        "scan add_wrap out for i in n yield (out[i] ^ x[i]);",  # the projection writes every binary in parentheses
    ],
)
def test_the_canonical_projection_keeps_the_form(line):
    source = f"fn f(n:usize, out:rw<u64>[n], x:ro<u64>[n]) {{ {line} }}\n"
    canonical = canonical_source(source)
    assert line in canonical
    assert compile_source(canonical)[0] == compile_source(source)[0] and canonical_source(canonical) == canonical


# Every operator, inclusive and exclusive, pooled and in order, against an independent prefix computed in the
# program itself; each count straddles a block edge. The status names the first disagreement.
AGREES = """
fn check(n:usize, x:ro<u64>[n], z:ro<i32>[n], a:rw<u64>[n], b:rw<u64>[n], c:rw<i32>[n], k:rw<i32>[n]) -> i32 {
  let t1 = scan add_wrap a parallel i in n yield x[i];
  let t2 = scan add_wrap b for i in n yield x[i];
  let mut run:u64 = 0;
  for i in 0..n {
    run = add_wrap(run, x[i]);
    if a[i] != run || b[i] != run { return 1; }
  }
  if t1 != run || t2 != run { return 2; }
  let e1 = scan mul_wrap exclusive a parallel i in n yield x[i] | 1;
  let e2 = scan mul_wrap exclusive b for i in n yield x[i] | 1;
  let mut product:u64 = 1;
  for i in 0..n {
    if a[i] != product || b[i] != product { return 3; }
    product = mul_wrap(product, x[i] | 1);
  }
  if e1 != product || e2 != product { return 4; }
  let lo1 = scan min c parallel i in n yield z[i];
  let lo2 = scan min k for i in n yield z[i];
  let mut lowest:i32 = 2147483647;
  for i in 0..n {
    lowest = min(lowest, z[i]);
    if c[i] != lowest || k[i] != lowest { return 5; }
  }
  if lo1 != lowest || lo2 != lowest { return 6; }
  let hi1 = scan max exclusive c parallel i in n yield z[i];
  let hi2 = scan max exclusive k for i in n yield z[i];
  let mut highest:i32 = -2147483647 - 1;
  for i in 0..n {
    if c[i] != highest || k[i] != highest { return 7; }
    highest = max(highest, z[i]);
  }
  if hi1 != highest || hi2 != highest { return 8; }
  let o1 = scan | a parallel i in n yield x[i] & 0x0f0f;
  let o2 = scan ^ exclusive b parallel i in n yield x[i];
  let mut ored:u64 = 0;
  let mut xored:u64 = 0;
  for i in 0..n {
    if b[i] != xored { return 9; }
    ored |= x[i] & 0x0f0f;
    xored ^= x[i];
    if a[i] != ored { return 10; }
  }
  if o1 != ored || o2 != xored { return 11; }
  let s1 = scan + exclusive a parallel i in n yield x[i] & 0xffff;
  let s2 = scan & b parallel i in n yield x[i] | 0xf0f0;
  let mut sum:u64 = 0;
  let mut anded:u64 = 0xffffffffffffffff;
  for i in 0..n {
    if a[i] != sum { return 12; }
    sum += x[i] & 0xffff;
    anded &= x[i] | 0xf0f0;
    if b[i] != anded { return 13; }
  }
  if s1 != sum || s2 != anded { return 14; }
  for i in 0..n { b[i] = a[i]; }
  scan add_wrap a parallel i in n yield a[i] + 1;  // in place: each lane reads its own element first
  let mut again:u64 = 0;
  for i in 0..n {
    again = add_wrap(again, b[i] + 1);
    if a[i] != again { return 15; }
  }
  return 0;
}

fn main() -> i32 {
  stack sizes:usize[8] = zeroed;
  SIZES
  for at in 0..8 {
    let n = sizes[at];
    buffer x:u64[n] = zeroed;
    buffer z:i32[n] = zeroed;
    buffer a:u64[n] = zeroed;
    buffer b:u64[n] = zeroed;
    buffer c:i32[n] = zeroed;
    buffer k:i32[n] = zeroed;
    for i in 0..n {
      x[i] = mul_wrap(u64(i) + 1, 0x9e3779b97f4a7c15);
      z[i] = i32((i * 7919) % 1000001) - 500000;
    }
    let bad = check(n, x, z, a, b, c, k);
    if bad != 0 { return bad; }
  }
  return 0;
}
"""
SIZES = [0, 1, 2, 16383, 16384, 16385, 24577, 262147]  # one block, the cutoff's edges, and thirty-two blocks


def agrees() -> str:
    """AGREES at each size, one after another."""
    return AGREES.replace("SIZES", " ".join(f"sizes[{k}] = {n};" for k, n in enumerate(SIZES)))


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("lanes", ["1", "3", "16"])
def test_every_operator_scans_to_the_in_order_answer_on_any_number_of_lanes(tmp_path, cxx, lanes):
    done = contract(tmp_path, compile_source(agrees())[0], cxx, env={**os.environ, "CAIRN_LANES": lanes})
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_the_pooled_scan_is_clean_under_thread_sanitizer(tmp_path):
    done = watched(tmp_path, compile_source(agrees())[0], "clang++", "thread")
    assert done.returncode == 0, done.stderr[-4000:]
    assert "WARNING: ThreadSanitizer" not in done.stderr


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_the_scans_are_clean_under_address_and_undefined_sanitizers(tmp_path):
    done = watched(tmp_path, compile_source(agrees())[0], "clang++", "address,undefined")
    assert done.returncode == 0, done.stderr[-4000:]


# A checked sum whose every block total fits and whose whole does not: in order it traps at the prefix that
# overflows; pooled it traps where the block offsets are combined. Either way it traps, and only then.
OVERFLOWS = """
fn prefix(n:usize, out:rw<u32>[n], x:ro<u32>[n]) -> u32 { let s = scan + out MODE i in n yield x[i]; return s; }
fn main() -> i32 {
  let n:usize = 100000;
  buffer x:u32[n] = zeroed;
  buffer out:u32[n] = zeroed;
  for i in 0..n { x[i] = EACH; }
  let t = prefix(n, out, x);
  if u64(t) != u64(n) * u64(EACH) || u64(out[n - 1]) != u64(t) { return 1; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("mode", ["for", "parallel"])
def test_a_checked_scan_traps_exactly_when_the_total_overflows(tmp_path, cxx, mode):
    (tmp_path / "fits").mkdir()
    (tmp_path / "over").mkdir()
    source = OVERFLOWS.replace("MODE", mode)
    fits = contract(tmp_path / "fits", compile_source(source.replace("EACH", "40000"))[0], cxx)
    assert fits.returncode == 0, fits.stderr[-2000:]
    over = contract(tmp_path / "over", compile_source(source.replace("EACH", "50000"))[0], cxx)
    assert over.returncode == -signal.SIGABRT, (over.returncode, over.stderr[-2000:])


def test_a_float_scan_in_order_is_the_running_sum_in_order(tmp_path):
    source = """
fn running(n:usize, out:rw<f64>[n], x:ro<f64>[n]) -> f64 { let t = scan + out for i in n yield x[i]; return t; }
fn main() -> i32 {
  let n:usize = 1000;
  buffer x:f64[n] = zeroed;
  buffer out:f64[n] = zeroed;
  for i in 0..n { x[i] = 1.0 / f64(i + 1); }
  let t = running(n, out, x);
  let mut s:f64 = 0.0;
  for i in 0..n {
    s = s + x[i];
    if out[i] != s { return 1; }  // bit for bit: the same additions in the same order
  }
  if t != s { return 2; }
  return 0;
}
"""
    native(tmp_path, source)


# The library's radix sort on xorshift keys of BITS bits, which Python replays, against Python's sorted.
SORTED = """
import std.sort;
import std.io;

fn main() -> i32 {
  let n:usize = COUNT;
  buffer xs:KEY[n] = zeroed;
  buffer spare:KEY[n] = zeroed;
  let mut state:u64 = SEED;
  for i in 0..n {
    state ^= shl_wrap(state, 13);
    state ^= shr(state, 7);
    state ^= shl_wrap(state, 17);
    xs[i] = KEY(shr(state, DROP));
  }
  sort.radix_sort(xs, spare);
  for x in xs {
    io.print_u64(u64(x));
    io.newline();
  }
  return 0;
}
"""


def xorshift(seed: int, count: int, bits: int) -> list[int]:
    keys, state, mask = [], seed, (1 << 64) - 1
    for _ in range(count):
        state ^= (state << 13) & mask
        state ^= state >> 7
        state ^= (state << 17) & mask
        keys.append(state >> (64 - bits))
    return keys


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize(
    ("key", "bits", "count"),
    [("u64", 64, 20000), ("u64", 12, 3000), ("u32", 32, 777), ("u8", 8, 300), ("u64", 64, 0), ("u16", 16, 1)],
)
def test_the_radix_sort_agrees_with_pythons_sorted(tmp_path, cxx, key, bits, count):
    seed = 0x2545F4914F6CDD1D + count
    program = SORTED.replace("COUNT", str(count)).replace("KEY", key).replace("SEED", str(seed))
    cpp = compile_source(program.replace("DROP", str(64 - bits)))[0]
    extra = ["-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"] if cxx == "clang++" else []
    done = contract(tmp_path, cpp, cxx, *extra)
    assert done.returncode == 0, done.stderr[-2000:]
    assert [int(line) for line in done.stdout.split()] == sorted(xorshift(seed, count, bits))


def test_the_radix_sort_moves_no_allocation_into_its_row():
    _, receipt = compile_source(
        "import std.sort;\nfn f(n:usize, xs:rw<u32>[n], s:rw<u32>[n]) { sort.radix_sort(xs, s); }\n"
    )
    row = set(receipt["functions"]["f"]["effects"])
    assert "alloc" not in row and "diverge" not in row and "stack_storage" in row  # the digit counts


PRICED = """
fn written(n:usize, out:rw<u64>[n], x:ro<u64>[n]) -> u64 {
  let mut t:u64 = 0;
  for i in 0..n {
    t = add_wrap(t, x[i]);
    out[i] = t;
  }
  return t;
}
fn plain(n:usize, out:rw<u64>[n], x:ro<u64>[n]) -> u64 { let t = scan add_wrap out for i in n yield x[i]; return t; }
fn pooled(n:usize, out:rw<u64>[n], x:ro<u64>[n]) -> u64 { let t = scan add_wrap out parallel i in n yield x[i]; return t; }
"""


def test_a_scan_is_priced_as_the_loop_it_writes_and_a_pooled_one_as_a_region(tmp_path, capsys):
    source = tmp_path / "priced.cairn"
    source.write_text(PRICED)
    assert main(["predict", str(source), "--format", "json", "--at", "n=1000000"]) == 0
    rows = {name: row["predictions"][0] for name, row in json.loads(capsys.readouterr().out)["functions"].items()}
    assert rows["plain"]["ns"] == rows["written"]["ns"] and rows["plain"]["confidence"] == "high"
    assert rows["pooled"]["ns"] < rows["plain"]["ns"] and rows["pooled"]["confidence"] == "medium"


def test_a_scan_is_outside_the_value_model_and_verify_says_unknown():
    source = "fn f(n:usize, out:rw<u64>[4]) -> u64 { let t = scan add_wrap out for i in 4 yield out[i]; return t; }\n"
    assert equivalent(source, source, "f")["status"] == "unknown"


def test_derive_grad_refuses_a_scan_by_name():
    source = (
        "fn run(n:usize, out:rw<f64>[n], x:ro<f64>[n]) -> f64 { let t = scan + out for i in n yield x[i]; return t; }\n"
        "derive grad[x] for run;\n"
    )
    assert refused("E-GRAD-FORM", source)["message"]


DEVICE = """
fn prefix(n:usize, out:rw<u32>[n]@device, x:ro<u32>[n]@device) -> u32 {
  let t = scan + exclusive out parallel i in n yield x[i];
  return t;
}
fn main() -> i32 {
  let n:usize = 100003;
  buffer host:u32[n] = zeroed;
  buffer back:u32[n] = zeroed;
  buffer x:u32[n]@device = zeroed;
  buffer out:u32[n]@device = zeroed;
  for i in 0..n { host[i] = u32(i % 7); }
  transfer(x, host);
  let t = prefix(n, out, x);
  transfer(back, out);
  let mut sum:u32 = 0;
  for i in 0..n {
    if back[i] != sum { return 1; }
    sum += host[i];
  }
  if t != sum { return 2; }
  return 0;
}
"""


def test_a_device_scan_compiles_for_the_device_without_touching_it(tmp_path):
    device_build(tmp_path, compile_source(DEVICE)[0], entry="main")


def test_a_device_scan_is_the_host_prefix(tmp_path):
    with on_device():  # runs only under `make gpu`
        done = contract(tmp_path, compile_source(DEVICE)[0], "g++", cuda=True)
        assert done.returncode == 0, (done.returncode, done.stderr[-2000:])

"""A guard is left out of the emitted C++ only where the checker established that it cannot fail.

`compiler/facts.py` records what a loop, a lane, a `let`, a condition or an early exit says about usize values, and
lowering drops an index, `+`, `-` or conversion guard whose condition follows. These tests hold both halves: the
guard goes where its condition holds, and it stays on every counterexample. The last test checks the emitted code
against an evaluator written here, over generated programs, under AddressSanitizer: a guard dropped wrongly shows
up as a missing trap or a sanitizer report.
"""

import random
import re
import shutil
import signal
import subprocess

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import SANITIZED, build, sanitized

GUARDS = {
    "at": r"\bcr::at\(",
    "add": r"\bcr::add<",
    "sub": r"\bcr::sub<",
    "convert": r"\bcr::convert<",
    "shr": r"\bcr::shr<",
}


def body(cpp: str, name: str) -> str:
    """The checked entry and the lean body of `name`, together: what one call from outside CAIRN runs."""
    return "".join(re.findall(rf"^[^;\n]*\bc[fi]_{name}\([^;\n]*\{{\n(.*?)^\}}", cpp, re.S | re.M))


def guards(source: str, name: str) -> dict[str, int]:
    text = body(compile_source(source)[0], name)
    return {kind: len(re.findall(pattern, text)) for kind, pattern in GUARDS.items() if re.search(pattern, text)}


ESTABLISHED = {
    "lanes": "fn f(n:usize, out:rw<f32>[n], x:ro<f32>[n], a:f32) { parallel i in n { out[i] = a * x[i]; } }",
    "stencil": (
        "fn f(n:usize, out:rw<f32>[n], x:ro<f32>[n]) {\n  parallel i in n {\n"
        "    if i > 0 && i + 1 < n { out[i] = x[i - 1] + x[i + 1]; } else { out[i] = x[i]; }\n  }\n}"
    ),
    "masked_bin": (
        "fn f(n:usize, out:rw<u64>[256], x:ro<u32>[n]) {\n"
        "  each i in n { let b = usize(x[i] & 255); out[b] = add_wrap(out[b], 1); }\n}"
    ),
    "reversed": "fn f(n:usize, out:rw<u64>[n], x:ro<u64>[n]) { for i in 0..n { out[n - 1 - i] = x[i]; } }",
    "early_exit": "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { if k >= n { return 0; } return x[k]; }",
    "else_arm": "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { if n <= k { return 0; } else { return x[k]; } }",
    "reduction": "fn f(n:usize, x:ro<u64>[n]) -> u64 { let s = reduce add_wrap for i in n yield x[i]; return s; }",
    "collector": (
        "fn f(n:usize, out:rw<u64>[n], x:ro<u64>[n]) -> u64 {\n"
        "  let used = compact out for i in n where x[i] > 3 yield x[i];\n"
        "  if used == 0 { return 0; }\n  return out[used - 1];\n}"
    ),
    "owner_length": (
        "fn f(n:usize) -> u64 {\n  let data = Buf[u64](n);\n  let mut t:u64 = 0;\n"
        "  for i in 0..len(data) { t = add_wrap(t, data[i]); }\n  for i in 0..n { t = add_wrap(t, data[i]); }\n"
        "  return t;\n}"
    ),
    "declared_extent": (
        "struct Col { rows:usize; price:Buf[u64][rows]; }\n"
        "fn f(c:ro<Col>) -> u64 { let mut t:u64 = 0; for i in 0..c.rows { t = add_wrap(t, c.price[i]); } return t; }"
    ),
    "small_conversion": "fn f(n:usize, out:rw<u32>[n]) { for i in 0..min(n, 4096) { out[i] = u32(i); } }",
    "scaled_row": (
        "fn f(k:usize) -> u64 {\n  let rows = k * 256;\n  buffer p:u64[rows] = zeroed;\n"
        "  for b in 0..k { let row = b * 256; for v in 0..256 { p[row + v] = u64(v); } }\n  return u64(k);\n}"
    ),
    "constant_shift": "fn f(v:u64) -> u64 = mul_wrap(v ^ shr(v, 29), 3);",
    "static_array": "fn f() -> u64 { let a = Array[u64, 8](); let mut t:u64 = 0; each i in 8 { t = add_wrap(t, a[i]); } return t; }",
    "and_then": "fn f(n:usize, x:ro<u64>[n], k:usize) -> bool = k < n && x[k] > 3;",
    "or_else": "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { if k >= n || x[k] == 0 { return 0; } return x[k]; }",
    "next_test": "fn f(n:usize, x:ro<u64>[n], k:usize) -> bool = k < n && k + 1 < n && x[k + 1] == 1;",
    "kept_projection": (
        "fn f(n:usize, out:rw<u64>[n], x:ro<u64>[n]) -> usize {\n"
        "  let used = compact out for i in n where i + 1 < n yield x[i + 1];\n  return used;\n}"
    ),
}


@pytest.mark.parametrize("name", ESTABLISHED)
def test_an_established_site_loses_its_guard(name):
    assert guards(ESTABLISHED[name], "f") == {}, ESTABLISHED[name]


def test_the_receipt_counts_what_was_discharged_beside_what_was_written():
    rows = compile_source(ESTABLISHED["stencil"])[1]["functions"]["f"]
    assert rows["syntactic_check_sites"]["bounds"] == 5 and rows["discharged_check_sites"]["bounds"] == 5
    assert rows["syntactic_check_sites"]["overflow"] == rows["discharged_check_sites"]["overflow"] == 3
    assert "trap" in rows["effects"]  # The row still says what the source could do.


KEPT = {
    "another_extent": (
        "fn f(n:usize, m:usize, x:ro<u64>[m]) -> u64 { let mut t:u64 = 0; "
        "for i in 0..n { t = add_wrap(t, x[i]); } return t; }",
        {"at": 1},
    ),
    "mutable_bound": (
        "fn f(n:usize, x:ro<u64>[n]) -> u64 { let mut hi = n; hi = hi + 1; let mut t:u64 = 0; "
        "for i in 0..hi { t = add_wrap(t, x[i]); } return t; }",
        {"at": 1, "add": 1},
    ),
    "mutable_index": (
        "fn f(n:usize, x:ro<u64>[n]) -> u64 { let mut j:usize = 0; let mut t:u64 = 0; "
        "for i in 0..n { t = add_wrap(t, x[j]); j = i + 1; } return t; }",
        {"at": 1},
    ),
    "either": (
        "fn f(n:usize, x:ro<u64>[n], k:usize, flag:bool) -> u64 { if k < n || flag { return x[k]; } return 0; }",
        {"at": 1},
    ),
    "other_arm": (
        "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { let mut t:u64 = 0; "
        "if k < n { t = x[k]; } else { t = x[k]; } return t; }",
        {"at": 1},
    ),
    "after_a_branch": (
        "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { let mut t:u64 = 0; "
        "if k < n { t = 1; } return add_wrap(t, x[k]); }",
        {"at": 1},
    ),
    "last_of_maybe_empty": ("fn f(n:usize, x:ro<u64>[n]) -> u64 { return x[n - 1]; }", {"sub": 1}),
    "wide_conversion": ("fn f(k:usize) -> u32 { return u32(k); }", {"convert": 1}),
    "uncomputed_product": (
        "fn f(k:usize, b:usize) -> usize { if b < k { let r = b * 4; return r + 4; } return 0; }",
        {"add": 1},
    ),
    "counted_shift": ("fn f(v:u64, s:usize) -> u64 = shr(v, s);", {"shr": 1}),
    "unbounded_sum": ("fn f(k:usize) -> usize { return k + 1; }", {"add": 1}),
    "one_past": (
        "fn f(n:usize, x:ro<u64>[n]) -> u64 { let mut t:u64 = 0; "
        "for i in 0..n { t = add_wrap(t, x[i + 1]); } return t; }",
        {"at": 1},
    ),
    "replaced_owner": (
        "fn f(n:usize, m:usize) -> u64 { let mut d = Buf[u64](n); d = Buf[u64](m); "
        "let mut t:u64 = 0; for i in 0..n { t = add_wrap(t, d[i]); } return t; }",
        {"at": 1},
    ),
    "lent_record": (
        "struct Col { rows:usize; price:Buf[u64][rows]; }\n"
        "fn f(c:rw<Col>) -> u64 { let mut t:u64 = 0; "
        "for i in 0..c.rows { t = add_wrap(t, c.price[i]); } return t; }",
        {"at": 1},
    ),
    "before_the_test": ("fn f(n:usize, x:ro<u64>[n], k:usize) -> bool = x[k] > 3 && k < n;", {"at": 1}),
    "or_on_success": ("fn f(n:usize, x:ro<u64>[n], k:usize) -> bool = k < n || x[k] > 3;", {"at": 1}),
    "after_the_or": (
        "fn f(n:usize, x:ro<u64>[n], k:usize, flag:bool) -> u64 { if k < n && flag { return 1; } return x[k]; }",
        {"at": 1},
    ),
    "fact_out_of_scope": (
        "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { let mut t:u64 = 0; "
        "for i in 0..n { t = add_wrap(t, x[i]); } return add_wrap(t, x[k]); }",
        {"at": 1},
    ),
}


@pytest.mark.parametrize("name", KEPT)
def test_a_guard_stays_where_nothing_establishes_it(name):
    source, kept = KEPT[name]
    assert guards(source, "f") == kept, source


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_kept_guard_still_traps_natively_and_a_dropped_one_computes_the_same(tmp_path, cxx):
    source = (
        ESTABLISHED["stencil"].replace("fn f(", "fn blend(")
        + "\nfn at(n:usize, x:ro<f32>[n], k:usize) -> f32 { if k < n { return x[k]; } return x[k - n]; }\n"
        "fn main() -> i32 {\n  let n:usize = 64;\n  buffer out:f32[n] = zeroed;\n  buffer x:f32[n] = zeroed;\n"
        "  for i in 0..n { x[i] = f32(i); }\n  blend(n, out, x);\n"
        "  for i in 1..n - 1 { if out[i] != 2.0 * f32(i) { return 1; } }\n"
        "  if at(n, x, n + 3) != 3.0 { return 2; }\n  return i32(at(n, x, 2 * n));\n}"
    )
    cpp = compile_source(source)[0]
    assert "cr::at(v_x, (v_k - v_n), v_n)" in cpp  # k >= n makes k - n safe, and says nothing of k - n < n.
    exe = build(tmp_path, cpp, *sanitized(cxx), cxx=cxx)
    assert subprocess.run([exe], capture_output=True).returncode == -signal.SIGABRT


# Generated programs against an evaluator written here. Each is one loop, a condition and an indexed read, over
# views of exact length, run for every small n, m and k; the emitted code must trap exactly where the evaluator
# does and otherwise return what it returns. A dropped guard that should have stayed reads out of bounds, which
# AddressSanitizer reports, or returns a value the evaluator does not.


class Trap(Exception):
    pass


def sub(a: int, b: int) -> int:
    if b > a:
        raise Trap
    return a - b


def checked(a: int, b: int, op) -> int:
    if b == 0:
        raise Trap
    return op(a, b)


LOWS = {"0": lambda n, m, k: 0, "1": lambda n, m, k: 1, "k": lambda n, m, k: k}
HIGHS = {"n": lambda n, m, k: n, "m": lambda n, m, k: m, "k": lambda n, m, k: k, "n - 1": lambda n, m, k: sub(n, 1),
         "len(x)": lambda n, m, k: n, "min(n, m)": lambda n, m, k: min(n, m)}  # fmt: skip
INDEXES = {"i": lambda n, m, k, i: i, "i + 1": lambda n, m, k, i: i + 1, "i - 1": lambda n, m, k, i: sub(i, 1),
           "k": lambda n, m, k, i: k, "n - 1 - i": lambda n, m, k, i: sub(sub(n, 1), i),
           "n - i": lambda n, m, k, i: sub(n, i), "i / 2": lambda n, m, k, i: i // 2,
           "i % m": lambda n, m, k, i: checked(i, m, int.__mod__), "usize(u8(i & 7))": lambda n, m, k, i: i & 7,
           "k * 2 + i % 2": lambda n, m, k, i: k * 2 + i % 2, "i * 2": lambda n, m, k, i: i * 2,
           "m - 1": lambda n, m, k, i: sub(m, 1)}  # fmt: skip
CONDITIONS = {"i < m": lambda n, m, k, i: i < m, "i + 1 < n": lambda n, m, k, i: i + 1 < n,
              "k < n": lambda n, m, k, i: k < n, "i > 0 && i < m": lambda n, m, k, i: i > 0 and i < m,
              "!(i >= m)": lambda n, m, k, i: not i >= m, "i < n || k < m": lambda n, m, k, i: i < n or k < m,
              "i != n": lambda n, m, k, i: i != n, "i != 0": lambda n, m, k, i: i != 0}  # fmt: skip
SIZES = [0, 1, 2, 5, 9]


def generate(rng: random.Random, number: int) -> tuple[str, tuple]:
    shape = (rng.choice(list(LOWS)), rng.choice(list(HIGHS)), rng.choice(["", *CONDITIONS]), rng.choice("xy"),
             rng.choice(list(INDEXES)), rng.random() < 0.3)  # fmt: skip
    low, high, cond, view, index, leave = shape
    read = f"t = add_wrap(t, {view}[{index}]);"
    step = f"if {cond} {{ {read} }}" if cond and not leave else f"if !({cond}) {{ return 7; }} {read}" if cond else read
    source = (f"fn g{number}(n:usize, m:usize, k:usize, x:ro<u64>[n], y:ro<u64>[m]) -> u64 {{\n"
              f"  let mut t:u64 = 0;\n  for i in {low}..{high} {{ {step} }}\n  return t;\n}}\n")  # fmt: skip
    return source, shape


def evaluate(shape: tuple, n: int, m: int, k: int) -> str:
    """What the program returns, or "trap", computed from the language's rules and nothing the compiler wrote."""
    low, high, cond, view, index, leave = shape
    data = [3 * j + 1 for j in range(n)] if view == "x" else [5 * j + 2 for j in range(m)]
    try:
        t = 0
        for i in range(LOWS[low](n, m, k), HIGHS[high](n, m, k)):
            if cond and not CONDITIONS[cond](n, m, k, i):
                if leave:
                    return "7"
                continue
            j = INDEXES[index](n, m, k, i)
            if j >= len(data):
                raise Trap
            t = (t + data[j]) % 2**64
        return str(t)
    except Trap:
        return "trap"


DRIVER = """
#include <csignal>
#include <cstdio>
#include <sys/wait.h>
#include <unistd.h>
int main() {
  const std::size_t sizes[] = {SIZES};
  for(auto f : FUNCTIONS) for(std::size_t n : sizes) for(std::size_t m : sizes) for(std::size_t k : sizes) {
    std::fflush(stdout);
    const pid_t child = fork();
    if(child == 0) {
      auto* x = new std::uint64_t[n]; auto* y = new std::uint64_t[m];
      for(std::size_t j = 0; j < n; ++j) x[j] = 3 * j + 1;
      for(std::size_t j = 0; j < m; ++j) y[j] = 5 * j + 2;
      std::printf("%llu\\n", static_cast<unsigned long long>(f(n, m, k, x, y)));
      delete[] x; delete[] y;
      std::fflush(stdout);
      _exit(0);
    }
    int status = 0;
    waitpid(child, &status, 0);
    if(WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT) std::printf("trap\\n");
    else if(!WIFEXITED(status) || WEXITSTATUS(status) != 0) std::printf("status %d\\n", status);
  }
}
"""


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_generated_programs_trap_exactly_where_an_independent_evaluator_does(tmp_path):
    rng = random.Random(20260922)
    programs = [generate(rng, number) for number in range(48)]
    cpp = compile_source("".join(source for source, _ in programs))[0]
    functions = "{" + ", ".join(f"&cf_g{number}" for number in range(len(programs))) + "}"
    driver = DRIVER.replace("SIZES", ", ".join(map(str, SIZES))).replace("FUNCTIONS", functions)
    exe = build(tmp_path, cpp + driver, *SANITIZED, entry=None, timeout=300)
    lines = subprocess.run([exe], capture_output=True, text=True, timeout=600).stdout.split()
    expected = [evaluate(shape, n, m, k) for _, shape in programs for n in SIZES for m in SIZES for k in SIZES]
    assert "status" not in lines
    mismatches = [
        (i // len(SIZES) ** 3, want, got) for i, (want, got) in enumerate(zip(expected, lines)) if want != got
    ]
    assert len(lines) == len(expected) and not mismatches, (
        mismatches[:5],
        [programs[j][0] for j, *_ in mismatches[:2]],
    )
    assert sum(r == "trap" for r in expected) > 100 and sum(r != "trap" for r in expected) > 1000

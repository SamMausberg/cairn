"""Two spellings of one function emit the same checks and behave the same.

Each pair below is one computation written two ways an author or an agent might choose between: an extent written
or left out, a length named or written out, an early exit or a short-circuit test, a block or an expression body.
For every pair the emitted bodies must hold the same guards, kind by kind, and native builds under both compilers
must return the same value, or trap, on every input of a grid that includes empty views and the largest usize. A pair
the compiler does not yet make equal is listed in `APART` with the reason, and fails the suite once it becomes equal,
so the list only shrinks.
"""

import re
import subprocess
from collections import Counter

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import build, sanitized

HEAD = (
    "fn sum(c:usize, v:ro<u64>[c]) -> u64 { let mut t:u64 = 0; for i in 0..c { t = add_wrap(t, v[i]); } return t; }\n"
    "fn equal(c:usize, v:ro<u64>[c], e:usize, w:ro<u64>[e]) -> bool {\n"
    "  if c != e { return false; }\n  for i in 0..c { if v[i] != w[i] { return false; } }\n  return true;\n}\n"
)
SIGNATURE = "(n:usize, x:ro<u64>[n], k:usize, m:usize) -> u64"

PAIRS = {
    "an extent left out of a whole view": ("{ return sum(len(x), x); }", "= sum(x);"),
    "an extent left out of a part": (
        "{ if k > m || m > n { return 0; } return sum(m - k, x[k..m]); }",
        "{ if k > m || m > n { return 0; } return sum(x[k..m]); }",
    ),
    "an unguarded part's extent written or left out": ("= sum(m - k, x[k..m]);", "= sum(x[k..m]);"),
    "a length named or written out": (
        "{ if k > m || m > n { return 0; } let w = m - k; return sum(w, x[k..m]); }",
        "{ if k > m || m > n { return 0; } return sum(m - k, x[k..m]); }",
    ),
    "a length named or the part's own span": (
        "{ let w = m - k; if w == 0 { return 0; } return sum(w, x[k..m]); }",
        "{ let w = m - k; if w == 0 { return 0; } return sum(x[k..m]); }",
    ),
    "a start named or written out": (
        "{ if m > n { return 0; } let lo = n - m; return sum(x[lo..n]); }",
        "{ if m > n { return 0; } return sum(x[n - m..n]); }",
    ),
    "an early exit or a short-circuit test": (
        "{ if k >= n { return 0; } if x[k] > 3 { return 1; } return 0; }",
        "{ if k < n && x[k] > 3 { return 1; } return 0; }",
    ),
    "an early exit or an else arm": (
        "{ if k >= n { return 0; } return x[k]; }",
        "{ if k < n { return x[k]; } else { return 0; } }",
    ),
    "a suffix test as a block or an expression": (
        "{ if m > n { return 0; } if equal(x[n - m..n], x[0..m]) { return 1; } return 0; }",
        "{ if m <= n && equal(x[n - m..n], x[0..m]) { return 1; } return 0; }",
    ),
    "a block body or an expression body": ("{ return u64(add_wrap(k, m)); }", "= u64(add_wrap(k, m));"),
    "a bound as the view's length or its extent": (
        "{ let mut t:u64 = 0; for i in 0..len(x) { t = add_wrap(t, x[i]); } return t; }",
        "{ let mut t:u64 = 0; for i in 0..n { t = add_wrap(t, x[i]); } return t; }",
    ),
    "a bound named or written out": (
        "{ if k > n { return 0; } let w = n - k; let mut t:u64 = 0; for i in 0..w { t = add_wrap(t, x[i + k]); } return t; }",
        "{ if k > n { return 0; } let mut t:u64 = 0; for i in 0..n - k { t = add_wrap(t, x[i + k]); } return t; }",
    ),
}
APART = {  # Pairs that still differ, each with the reason; the test fails when one of them becomes equal.
    "a length named before its part": (
        (
            "{ if m > n { return 0; } let w = m - k; return sum(w, x[k..m]); }",
            "{ if m > n { return 0; } return sum(m - k, x[k..m]); }",
        ),
        "the `let` checks its subtraction before the part does; leaving it out would need the part to be reached "
        "with nothing observable in between, which the checker does not track",
    )
}
GUARD = re.compile(r"\bcr::(at|part|view|disjoint|add|sub|mul|divide|remainder|convert|truncate|shr|shl_wrap)\b")


def program(pairs: dict) -> str:
    out = [HEAD]
    for i, (a, b) in enumerate(pairs.values()):
        out += [f"fn a{i}{SIGNATURE} {a}\n", f"fn b{i}{SIGNATURE} {b}\n"]
    return "".join(out)


def guards(cpp: str, name: str) -> Counter:
    body = re.search(rf"^[^;\n]*\bcf_{name}\([^;\n]*\{{\n(.*?)^\}}", cpp, re.S | re.M).group(1)
    return Counter(GUARD.findall(body))


@pytest.mark.parametrize("index", range(len(PAIRS)), ids=list(PAIRS))
def test_both_spellings_emit_the_same_checks(index):
    cpp = compile_source(program(PAIRS))[0]
    assert guards(cpp, f"a{index}") == guards(cpp, f"b{index}"), list(PAIRS.values())[index]


@pytest.mark.parametrize("name", APART)
def test_a_pair_listed_apart_still_differs(name):
    cpp = compile_source(program({name: APART[name][0]}))[0]
    assert guards(cpp, "a0") != guards(cpp, "b0"), f"{name} is equal now: move it to PAIRS"


GRID_N, GRID_K = (0, 1, 3, 6), (0, 1, 2, 5, 2**64 - 1)
DRIVER = """
#include <cstdio>
#include <sys/wait.h>
#include <unistd.h>
using F = std::uint64_t (*)(std::size_t, const std::uint64_t*, std::size_t, std::size_t);
int main() {
  const F pairs[][2] = {PAIRS};
  const std::size_t ns[] = {NS};
  const std::size_t ks[] = {KS};
  for(auto& pair : pairs) for(std::size_t n : ns) for(std::size_t k : ks) for(std::size_t m : ks) {
    for(int side = 0; side < 2; ++side) {
      std::fflush(stdout);
      const pid_t child = fork();
      if(child == 0) {
        auto* x = new std::uint64_t[n + 1];
        for(std::size_t j = 0; j < n; ++j) x[j] = (j * 7) % 5;
        std::printf("%llu ", static_cast<unsigned long long>(pair[side](n, x, k, m)));
        std::fflush(stdout);
        _exit(0);
      }
      int status = 0;
      waitpid(child, &status, 0);
      if(WIFSIGNALED(status) && WTERMSIG(status) == 6) std::printf("trap ");
      else if(!WIFEXITED(status) || WEXITSTATUS(status) != 0) std::printf("status%d ", status);
    }
    std::printf("\\n");
  }
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_both_spellings_behave_alike_natively(tmp_path, cxx):
    cpp = compile_source(program(PAIRS))[0]
    pairs = ", ".join(f"{{&cf_a{i}, &cf_b{i}}}" for i in range(len(PAIRS)))
    ks = ", ".join(f"{k}ULL" for k in GRID_K)
    main = DRIVER.replace("PAIRS", pairs).replace("NS", ", ".join(map(str, GRID_N))).replace("KS", ks)
    exe = build(tmp_path, cpp + main, *sanitized(cxx), cxx=cxx, entry=None, timeout=300)
    lines = subprocess.run([exe], capture_output=True, text=True, timeout=600).stdout.splitlines()
    per = len(GRID_N) * len(GRID_K) ** 2
    assert len(lines) == per * len(PAIRS)
    differ = [(list(PAIRS)[i // per], line) for i, line in enumerate(lines) if len(set(line.split())) != 1]
    assert not differ, differ[:5]
    assert any("trap" in line for line in lines) and not any("status" in line for line in lines)

"""A function that takes views has a checked entry for callers outside CAIRN and a lean body for CAIRN's own calls.

`cf_f` is the C symbol: it checks that every view is live, aligned and inside the address space and that a mutable
one overlaps no other, and then runs `ci_f`, the body. A direct call from CAIRN code reaches `ci_f`, because each
view it can pass is one its caller was given and checked, storage the caller holds, a string, or a part kept inside
one of those, and E-ALIAS has already shown a mutable one overlaps no other argument. These tests hold that the
foreign boundary still traps on a bad view, that CAIRN's calls skip what they have established and compute the same,
and that a conservative build sends every call through the checked entry. A function type and a `dyn` member carry
no array view, so every call that reaches a view-taking function is a direct one.
"""

import re
import signal
import subprocess

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import build, sanitized

SOURCE = """
fn total(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = add_wrap(t, xs[i]); } return t; }
fn scale(n:usize, out:rw<u64>[n], xs:ro<u64>[n], k:u64) { for i in 0..n { out[i] = mul_wrap(xs[i], k); } }
fn twice(n:usize, out:rw<u64>[n], xs:ro<u64>[n]) -> u64 {
  scale(out, xs, 2);
  if n < 2 { return total(out); }
  return total(out[0..n / 2]) + total(out[n / 2..n]);
}
fn main() -> i32 {
  let n:usize = 6;
  buffer out:u64[n] = zeroed;
  buffer xs:u64[n] = zeroed;
  for i in 0..n { xs[i] = u64(i); }
  let got = twice(n, out, xs);
  if got != 30 { return 1; }
  return 0;
}
"""


def definition(cpp: str, symbol: str) -> str:
    return re.search(rf"^[^;\n]*\b{symbol}\([^;\n]*\{{\n(.*?)^\}}", cpp, re.S | re.M).group(1)


def test_a_call_from_cairn_reaches_the_lean_body_and_the_c_symbol_checks_first():
    cpp = compile_source(SOURCE)[0]
    entry, body = definition(cpp, "cf_twice"), definition(cpp, "ci_twice")
    assert "cr::view(v_out, v_n);" in entry and "cr::disjoint(v_out, v_n, v_xs, v_n);" in entry
    assert entry.strip().endswith("return ci_twice(v_n, v_out, v_xs);")
    assert "cr::view" not in body and "cr::disjoint" not in body
    assert "ci_scale(" in body and "ci_total(" in body and "cf_scale(" not in body and "cf_total(" not in body
    assert "ci_twice(v_n, v_out, v_xs)" in definition(cpp, "cf_main")  # main has no view: one symbol


def test_a_conservative_build_sends_every_call_through_the_checked_entry():
    cpp = compile_source(SOURCE, keep_guards=True)[0]
    body = definition(cpp, "ci_twice")
    assert "cf_scale(" in body and "cf_total(" in body and "ci_scale(" not in body


DRIVER = """
#include <cstdio>
#include <cstring>
#include <sys/wait.h>
#include <unistd.h>
static int outcome(void (*call)()) {
  std::fflush(stdout);
  const pid_t child = fork();
  if (child == 0) { call(); _exit(0); }
  int status = 0;
  waitpid(child, &status, 0);
  return WIFSIGNALED(status) ? -WTERMSIG(status) : WEXITSTATUS(status);
}
static std::uint64_t data[8] = {0, 1, 2, 3, 4, 5, 6, 7};
static std::uint64_t out[8];
int main(int argc, char**) {
  if (cf_main() != 0) return 10;                                             // CAIRN calling CAIRN, lean
  if (cf_twice(6, out, data) != 30) return 11;                               // a foreign call, checked, same value
  if (outcome([] { cf_twice(6, data + 1, data); }) != -SIGNAL) return 12;    // overlapping views
  if (outcome([] { cf_total(4, nullptr); }) != -SIGNAL) return 13;           // no storage behind a nonempty view
  if (outcome([] { cf_total(2, reinterpret_cast<const std::uint64_t*>(reinterpret_cast<const char*>(data) + 1)); })
      != -SIGNAL) return 14;                                                 // misaligned
  if (outcome([] { cf_total(0, nullptr); }) != 0) return 15;                 // an empty view needs nothing
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_foreign_boundary_traps_on_a_bad_view_and_a_good_call_computes_what_cairn_does(tmp_path, cxx):
    cpp = compile_source(SOURCE)[0]
    exe = build(tmp_path, cpp + DRIVER.replace("-SIGNAL", str(-int(signal.SIGABRT))), *sanitized(cxx), cxx=cxx,
                entry=None)  # fmt: skip
    assert subprocess.run([exe], capture_output=True, timeout=120).returncode == 0

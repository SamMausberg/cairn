"""`_` binds nothing: as the binder of a payload arm it drops the payload, releasing an owner where the arm ends and
refusing a linear one, and as a loop's binder it only counts. Nothing reads it, so it repeats in nested arms and
loops. The accepted programs run natively under both compilers, an owner dropped by `_` is released exactly once
under AddressSanitizer's leak check, and every refusal names its code.
"""

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.editor.formatting import format_source
from emitted import WARNINGS, refused, run, sanitized, watched

BLANK = """
import std.core (Option, Result);

fn depth(a:Option[u64], b:Option[Buf[u8]]) -> u64 {
  let mut n:u64 = 0;
  match a {
    Some(_) => { match b { Some(_) => n = 2; None => n = 1; } }
    None => return 0;
  }
  for _ in 0..3 { for _ in 0..2 { n += 1; } }
  return n;
}

fn code(r:Result[u64, u8]) -> u64 {
  match r {
    Ok(v) => return v;
    Err(_) => return 0;
  }
}

fn main() -> i32 {
  for _ in 0..1000 {
    if depth(Some(7), Some(Buf[u8](64))) != 8 { return 1; }
  }
  if depth(None, None) != 0 || code(Ok(4)) != 4 || code(Err(9)) != 0 { return 2; }
  return 0;
}
"""


def test_a_blank_binder_repeats_and_drops_an_owner_where_its_arm_ends():
    receipt = compile_source(BLANK)[1]
    assert "free" in receipt["functions"]["depth"]["effects"]  # the Buf `Some(_)` dropped is released
    assert "free" not in receipt["functions"]["code"]["effects"]
    assert compile_source(canonical_source(BLANK))[0] == compile_source(BLANK)[0]
    assert "Some(_) => n = 2;" in canonical_source(BLANK) and format_source(BLANK) == format_source(
        format_source(BLANK)
    )


@pytest.mark.parametrize(
    ("code", "source", "said"),
    [
        ("E-UNBOUND", "import std.core (Option);\nfn f(x:Option[u64]) -> u64 { match x { Some(_) => return _; None => "
         "return 0; } }", "_ binds nothing"),
        ("E-UNBOUND", "fn f() -> u64 { let mut t:u64 = 0; for _ in 0..3 { t += u64(_); } return t; }", "_ binds nothing"),
        ("E-LINEAR-LEAK", "import std.core (Option);\nlinear struct Lease { id:u64; }\nfn f(l:Option[Lease]) -> u64 { "
         "match l { Some(_) => return 1; None => return 0; } }", "Some(_) would drop a linear Lease"),
    ],
)  # fmt: skip
def test_what_a_blank_binder_refuses(code, source, said):
    assert said in refused(code, source)["message"]


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_blank_binders_run_natively(tmp_path, cxx):
    assert run(tmp_path, compile_source(BLANK)[0], *sanitized(cxx), *WARNINGS, cxx=cxx).returncode == 0


def test_an_owner_dropped_by_a_blank_binder_is_released_once(tmp_path):
    done = watched(tmp_path, compile_source(BLANK)[0], "clang++", "address")
    assert done.returncode == 0 and "LeakSanitizer" not in done.stderr and "AddressSanitizer" not in done.stderr

"""Task groups: `Group[T](n)`, `spawn f(args) into g;`, `collect(g)` and `wait(g)`.

Results come back in the order the tasks finish, every lease a task takes is held by the group until
wait(g), and the group's whole storage is taken where it is declared, so a submission never allocates.
Accepted programs run natively under both compilers with the sanitizers that bite; every rule has a
rejection naming its code.
"""

import pytest
from test_concurrency import HELPERS, build_and_run

from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.projects.toolchain import audit_effects
from emitted import watched

NAP = """
extern fn usleep(us:u32) -> i32 effects(io);
fn nap(us:u32, tag:u64) -> u64 { unsafe { let r = usleep(us); } return tag; }
fn make(n:usize) -> Buf[u64] { let mut b = Buf[u64](n); b[0] = u64(n); return b; }
"""

GROUPS = """
fn main() -> i32 {
  let g = Group[u64](3);
  spawn nap(150000, 1) into g;                 // the slowest task, submitted first
  spawn nap(50000, 2) into g;
  spawn nap(100000, 3) into g;
  let first = collect(g);                      // whichever finished first
  let second = collect(g);
  let third = collect(g);
  wait(g);
  if first != 2 || second != 3 || third != 1 { return 1; }

  let n:usize = 900;
  let a:usize = 300;
  let b:usize = 600;
  let mut data = Buf[u64](n);
  let parts = Group[void](3);
  spawn fill(a, data[0..a], 0) into parts;     // three visibly disjoint parts, leased until wait(parts)
  spawn fill(b - a, data[a..b], 300) into parts;
  spawn fill(n - b, data[b..n], 600) into parts;
  collect(parts);
  wait(parts);                                 // joins the two still running, then returns the leases
  if sum(len(data), data) != 404550 { return 2; }

  let owners = Group[Buf[u64]](3);
  spawn make(4) into owners;
  spawn make(5) into owners;
  spawn make(6) into owners;
  let got = collect(owners);                   // one result moves out; wait drops the other two
  wait(owners);
  if got[0] < 4 || got[0] > 6 { return 3; }
  return 0;
}
"""

FULL = "fn main() -> i32 { let g = Group[u64](1); spawn nap(1000, 1) into g; spawn nap(1000, 2) into g; wait(g); return 0; }"
EMPTY = "fn main() -> i32 { let g = Group[u64](2); let r = collect(g); wait(g); return i32(r); }"


def test_a_group_is_declared_with_its_costs():
    """The declaration takes the whole storage (alloc, free), a submission is a spawn behind a guard, a collect
    is a join behind a guard, and wait is the join that returns the leases."""
    _, receipt = compile_source(HELPERS + NAP + GROUPS)
    main = receipt["functions"]["main"]
    assert {"spawn", "join", "alloc", "free", "trap"} <= set(main["effects"])
    assert main["syntactic_check_sites"]["submit"] == 9 and main["syntactic_check_sites"]["collect"] == 5
    with pytest.raises(Exception, match="freestanding target has no hosted runtime"):
        audit_effects(receipt["functions"])  # A bare-metal image has no threads: the row says so by name.


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("sanitizer", ["thread", "address,undefined"])
def test_results_arrive_in_completion_order_and_owners_are_released_once(tmp_path, cxx, sanitizer):
    """ThreadSanitizer watches the three tasks that write three parts and the results crossing back; the address
    and leak sanitizers watch a collected owner and the two that wait drops, each released exactly once."""
    ran = watched(tmp_path, compile_source(HELPERS + NAP + GROUPS)[0], cxx, sanitizer)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "Sanitizer" not in ran.stderr and "runtime error" not in ran.stderr, ran.stderr


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("program", [FULL, EMPTY], ids=["a full group", "an empty group"])
def test_a_full_or_empty_group_traps(tmp_path, cxx, program):
    """A submission past the capacity, or a collect with nothing outstanding, is a guard failure, never growth
    or a wait that can never end."""
    code, _ = build_and_run(tmp_path, NAP + program, cxx)
    assert code in (-6, 134)


@pytest.mark.parametrize(
    "code,source",
    [
        ("E-PINNED", "struct H { g:Group[u64]; }"),
        ("E-PINNED", "fn f(g:Group[u64]) {}"),
        ("E-PINNED", "fn f() -> Group[u64] = Group[u64](2);"),
        ("E-PINNED", "fn main() -> i32 { let g = Group[u64](2); let h = g; wait(h); return 0; }"),
        ("E-LINEAR-LEAK", "fn main() -> i32 { let g = Group[u64](2); return 0; }"),
        ("E-LINEAR-BRANCH", "fn main() -> i32 { let g = Group[u64](2); if true { wait(g); } return 0; }"),
        ("E-MOVED", "fn main() -> i32 { let g = Group[u64](2); wait(g); let r = collect(g); return 0; }"),
        ("E-MOVED", "fn tag(x:u64) -> u64 = x; fn main() -> i32 { let g = Group[u64](2); wait(g); spawn tag(1) into g; return 0; }"),
        ("E-LEASED", "fn fill(n:usize, out:rw<u64>[n], s:u64) {} fn main() -> i32 { let mut d = Buf[u64](4); let g = Group[void](2);"
         " spawn fill(len(d), d, 0) into g; d[0] = 1; wait(g); return 0; }"),
        ("E-LEASED", "fn fill(n:usize, out:rw<u64>[n], s:u64) {} fn main() -> i32 { let mut d = Buf[u64](4); let g = Group[void](2);"
         " spawn fill(len(d), d, 0) into g; collect(g); d[0] = 1; wait(g); return 0; }"),  # collect returns no lease
        ("E-LEASED", "fn fill(n:usize, out:rw<u64>[n], s:u64) {} fn main() -> i32 { let mut d = Buf[u64](8); let g = Group[void](4);"
         " for i in 0..4 { spawn fill(2, d[i..i + 2], 0) into g; } wait(g); return 0; }"),  # the next iteration lends it again
        ("E-SPAWN", "fn apply(f:ro<fn(u64) -> u64>) -> u64 = f(1); fn main() -> i32 { let g = Group[u64](2);"
         " spawn apply(|x:u64| -> u64 { return x; }) into g; wait(g); return 0; }"),
        ("E-SPAWN", "fn main() -> i32 { let n:usize = 4; buffer x:f32[n]@device = zeroed; buffer h:f32[n] = zeroed;"
         " let g = Group[void](1); spawn transfer(x, h) into g; wait(g); return 0; }"),
        ("E-SPAWN", "fn tag(x:u64) -> u64 = x; fn main() -> i32 { let n:usize = 4; buffer o:u64[n] = zeroed;"
         " parallel i in n { let g = Group[u64](1); spawn tag(1) into g; wait(g); o[i] = 1; } return 0; }"),
        ("E-TYPE-MISMATCH", "fn narrow(x:u64) -> u32 = u32(x); fn main() -> i32 { let g = Group[u64](2); spawn narrow(1) into g; wait(g); return 0; }"),
        ("E-TYPE-MISMATCH", "fn tag(x:u64) -> u64 = x; fn main() -> i32 { let g:u64 = 1; spawn tag(1) into g; return 0; }"),
        ("E-TYPE-MISMATCH", "fn tag(x:u64) -> u64 = x; fn main() -> i32 { let t = spawn tag(1); let r = collect(t); let s = wait(t); return 0; }"),
        ("E-LINEAR-STORAGE", "linear struct L { id:u64; } fn main() -> i32 { let g = Group[L](2); wait(g); return 0; }"),
        ("E-PLACEMENT", "fn f(n:usize, d:rw<u64>[n]@device) { parallel i in n { let g = Group[u64](1); wait(g); d[i] = 1; } }"),
    ],
)  # fmt: skip
def test_group_rejections(code, source):
    with pytest.raises(Diagnostic) as e:
        compile_source(source)
    assert e.value.data["code"] == code


def test_a_loop_may_lend_a_group_what_tasks_only_read():
    """Read-only lending is shared, so the same view goes to every task of a loop; a writer would be lent twice."""
    source = (
        "fn total(n:usize, xs:ro<u64>[n]) -> u64 = xs[0];\n"
        "fn main() -> i32 { let d = Buf[u64](8); let g = Group[u64](4); for i in 0..4 { spawn total(len(d), d) into g; }"
        " let r = collect(g); wait(g); return i32(r); }"
    )
    assert compile_source(source)[1]["functions"]["main"]["syntactic_check_sites"]["submit"] == 1

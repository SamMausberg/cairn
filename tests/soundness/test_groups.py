"""Task groups: `Group[T](n)`, `spawn f(args) into g;`, `collect(g)` and `wait(g)`.

Results come back in the order the tasks finish, every lease a task takes is held by the group until
wait(g), and the group's whole storage is taken where it is declared, so a submission never allocates.
Accepted programs run natively under both compilers with the sanitizers that bite; every rule has a
rejection naming its code.
"""

import pytest
from test_concurrency import BACKWARDS_PART, HELPERS, build_and_run

from cairn.compiler.cairnc import compile_source
from cairn.projects.toolchain import audit_effects
from emitted import contract, refused, watched

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
    refused(code, source)


SUBMITTED = "fn fill(n:usize, out:rw<u64>[n], s:u64) {} fn peek(n:usize, xs:ro<u64>[n]) -> u64 = xs[0]; fn flag() -> bool = true;"


@pytest.mark.parametrize(
    "source",
    [
        # Each branch lends the group a different buffer; the join used to keep only the last branch's lease.
        "fn main() -> i32 { let mut a = Buf[u64](8); let mut b = Buf[u64](8); let g = Group[void](1);"
        " if flag() { spawn fill(len(a), a, 1) into g; } else { spawn fill(len(b), b, 2) into g; }"
        " a[0] = 7; wait(g); return 0; }",
        # d[a..b] orders d[0..a] before d[b..n] only where it was formed, and so guarded; here it may not have been.
        "fn main() -> i32 { let a:usize = 6; let b:usize = 3; let n:usize = 9; let mut d = Buf[u64](n);"
        " let g = Group[void](3); if flag() { spawn fill(b - a, d[a..b], 1) into g; }"
        " spawn fill(a, d[0..a], 2) into g; spawn fill(n - b, d[b..n], 3) into g; wait(g); return 0; }",
        # The same fact from a loop that may run no iteration at all.
        "fn main() -> i32 { let a:usize = 6; let b:usize = 3; let n:usize = 9; let k:usize = 0;"
        " let mut d = Buf[u64](n); let h = Group[u64](4); let g = Group[void](2);"
        " for i in 0..k { spawn peek(b - a, d[a..b]) into h; }"
        " spawn fill(a, d[0..a], 2) into g; spawn fill(n - b, d[b..n], 3) into g; wait(g); wait(h); return 0; }",
        # The second iteration writes what the first iteration's task still reads.
        "fn main() -> i32 { let mut d = Buf[u64](8); let h = Group[u64](4);"
        " for i in 0..4 { d[0] = u64(i); spawn peek(len(d), d) into h; } wait(h); return 0; }",
    ],
    ids=["one lease per branch", "a fact from one branch", "a fact from a loop", "a write before the next submission"],
)
def test_what_a_group_holds_survives_every_path(source):
    """Soundness: a group keeps every lease any path lent it, and a part's bounds order other parts only where
    every path formed it. Each of these ran under ThreadSanitizer and raced before the checker refused it."""
    refused("E-LEASED", SUBMITTED + source)


BRANCHED = """
fn flip() -> bool = true;
fn main() -> i32 {
  let n:usize = 64;
  let mut x = Buf[u64](n);
  let mut y = Buf[u64](n);
  let chosen = Group[void](1);
  if flip() { spawn fill(len(x), x, 1) into chosen; } else { spawn fill(len(y), y, 2) into chosen; }
  wait(chosen);                                // both buffers are lent until here, whichever path ran
  x[0] = 7;
  let readers = Group[u64](4);
  for i in 0..4 { spawn sum(len(x), x) into readers; }
  let mut seen:u64 = 0;
  for i in 0..4 { seen = seen + collect(readers); }
  wait(readers);
  if seen != 4 * 2086 { return 1; }            // 1..64 from fill, with x[0] now 7
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_group_filled_on_one_path_or_in_a_loop_runs_race_free(tmp_path, cxx):
    """The accepted neighbours of the programs above: ThreadSanitizer watches a branch's task and four readers."""
    ran = watched(tmp_path, compile_source(HELPERS + BRANCHED)[0], cxx, "thread")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "Sanitizer" not in ran.stderr, ran.stderr


def test_a_loop_may_lend_a_group_what_tasks_only_read():
    """Read-only lending is shared, so the same view goes to every task of a loop; a writer would be lent twice."""
    source = (
        "fn total(n:usize, xs:ro<u64>[n]) -> u64 = xs[0];\n"
        "fn main() -> i32 { let d = Buf[u64](8); let g = Group[u64](4); for i in 0..4 { spawn total(len(d), d) into g; }"
        " let r = collect(g); wait(g); return i32(r); }"
    )
    assert compile_source(source)[1]["functions"]["main"]["syntactic_check_sites"]["submit"] == 1


def test_a_group_or_a_ticket_is_never_lent():
    """Spawning into a group that a callee only borrowed would record the task's lease in the callee's scope, which
    ends at its return while the task still writes: the caller could then touch what the task writes. 0.8.3 refused a
    group passed by value and accepted one lent by `rw`; now neither a group nor a ticket is a parameter at all."""
    fill = "fn fill(n:usize, out:rw<u64>[n]) -> u64 { for i in 0..n { out[i] = 1; } return 0; }\n"
    lent = "fn helper(n:usize, g:rw<Group[u64]>, xs:rw<u64>[n]) { spawn fill(xs) into g; }\n"
    main = "fn main() -> i32 { let mut d = Buf[u64](8); let mut g = Group[u64](2); helper(g, d); d[0] = 5; wait(g); return 0; }"
    refused("E-PINNED", fill + lent + main)
    refused("E-PINNED", fill + "fn peek(t:ro<Ticket[u64]>) -> u64 = 0;\n")


def test_a_submitted_part_is_guarded_before_its_task_starts(tmp_path):
    """The Lean calculus assumes of `submit` what it assumes of `spawn`: the `lo <= hi` of a lent part runs on the
    submitting thread before the task starts. The capture list is evaluated where the lambda is written."""
    source = BACKWARDS_PART.replace(
        "let t = spawn work(0, d[lo..hi]);", "let g = Group[void](1);\n  spawn work(0, d[lo..hi]) into g;"
    )
    source = source.replace("wait(t);", "wait(g);")
    cpp = compile_source(source)[0]
    submit = next(line for line in cpp.splitlines() if ".submit(" in line)
    captures, body = submit.split("]() mutable noexcept {")
    assert "cr::part(" in captures and "cr::part(" not in body
    done = contract(tmp_path, cpp, "clang++")
    assert done.returncode == -6 and done.stdout == "spawning\n"  # Aborted at the slice: no task, no next statement.

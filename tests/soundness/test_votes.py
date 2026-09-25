"""Warp votes and `shuffle_up` (compiler/cooperative/cooperative.py): `warp_ballot(c)`, `warp_any(c)`, `warp_all(c)`,
`warp_match(v)` and `shuffle_up(v, delta)`, each one instruction for the whole warp.

Each needs every thread of its warp (E-COOP-WARP) and refuses a wrong argument with its code. On host threads under
both compilers and the thread sanitizer, each agrees with a plain loop over the warp's lanes; the device program runs
emulated, and compiles for sm_120 to VOTE.ANY, VOTE.ALL, MATCH.ANY and SHFL.UP. Nothing runs on a GPU.
"""

import re

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import assembled, emulated, refused, round_trips, watched

KERNEL = """// Every thread of each warp votes on its value, finds the lanes that share its value mod 4, and reads its lower
// neighbour's value; out[i] packs what it learned.
fn votes(g:usize, n:usize, x:ro<u32>[n], masks:rw<u32>[n], same:rw<u32>[n], flags:rw<u32>[n]) {
  blocks b in g threads t in 64 {
    let i = b * 64 + t;
    let mut v:u32 = 0;
    if i < n { v = x[i]; }
    let third = warp_ballot(v % 3 == 0);
    let big = warp_any(v > 900);
    let small = warp_all(v < 990);
    let group = warp_match(v % 4);
    let below = shuffle_up(v, 1);
    if i < n {
      masks[i] = third;
      same[i] = group;
      let mut f:u32 = below;
      if big { f += 1000000; }
      if small { f += 2000000; }
      flags[i] = f;
    }
  }
}
"""

CHECK = """
fn check(n:usize, g:usize) -> i32 {
  let m = g * 64;
  buffer x:u32[n] = zeroed;
  for i in 0..n { x[i] = u32(mul_wrap(u64(i), 2654435761) % 1000); }
  buffer masks:u32[n] = zeroed;
  buffer same:u32[n] = zeroed;
  buffer flags:u32[n] = zeroed;
  RUN
  for i in 0..min(n, m) {
    let first = i / 32 * 32;
    let mut third:u32 = 0;
    let mut group:u32 = 0;
    let mut big = false;
    let mut small = true;
    for l in 0..32 {
      let mut v:u32 = 0;
      if first + l < n { v = x[first + l]; }
      if v % 3 == 0 { third = third | shl_wrap(1, l); }
      if v % 4 == x[i] % 4 { group = group | shl_wrap(1, l); }
      if v > 900 { big = true; }
      if v >= 990 { small = false; }
    }
    let mut want:u32 = x[i];
    if i % 32 > 0 { want = x[i - 1]; }
    if big { want += 1000000; }
    if small { want += 2000000; }
    if masks[i] != third || same[i] != group || flags[i] != want { return 1; }
  }
  return 0;
}

fn main() -> i32 {
  let first = check(1000, 16);
  if first != 0 { return first; }
  return check(100, 3);
}
"""

HOST = KERNEL + CHECK.replace("RUN", "votes(g, n, x, masks, same, flags);")
DEVICE = KERNEL.replace("[n]", "[n]@device") + CHECK.replace(
    "RUN",
    "buffer dx:u32[n]@device = zeroed;\n  buffer dm:u32[n]@device = zeroed;\n  buffer ds:u32[n]@device = zeroed;\n"
    "  buffer df:u32[n]@device = zeroed;\n  transfer(dx, x);\n  votes(g, n, dx, dm, ds, df);\n  transfer(masks, dm);\n"
    "  transfer(same, ds);\n  transfer(flags, df);",
)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_vote_agrees_with_a_plain_loop_over_the_warp_under_the_thread_sanitizer(tmp_path, cxx):
    done = watched(tmp_path, compile_source(HOST)[0], cxx, "thread")
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, (done.returncode, done.stderr[-3000:])


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_device_program_runs_emulated_and_agrees(tmp_path, cxx):
    emulated(tmp_path, compile_source(DEVICE)[0], cxx)


def test_each_vote_compiles_for_sm_120_to_one_warp_instruction(tmp_path):
    sass, _ = assembled(tmp_path, compile_source(KERNEL.replace("[n]", "[n]@device"))[0])
    for wanted in ("VOTE.ANY", "VOTE.ALL", "MATCH.ANY", "SHFL.UP"):
        assert wanted in sass, wanted
    assert not re.search(r"\b(LDL|STL)\b", sass)


def test_a_ballot_is_the_same_in_every_thread_of_its_warp_and_a_match_is_not():
    """A vote's answer is the warp's, so a warp operation may sit under it; a match differs from lane to lane."""
    shape = "fn f(g:usize) {\n  blocks b in g threads t in 64 {\n    let c = VOTE;\n    if COND { let v = shuffle(t, 0); }\n  }\n}\n"
    compile_source(shape.replace("VOTE", "warp_any(t % 2 == 0)").replace("COND", "c"))
    compile_source(shape.replace("VOTE", "warp_ballot(t % 2 == 0)").replace("COND", "c > 0"))
    refused("E-COOP-WARP", shape.replace("VOTE", "warp_match(t % 2)").replace("COND", "c > 1"))


def test_the_canonical_projection_compiles_to_the_same_code():
    round_trips(HOST)


BLOCK = "fn f(g:usize) {\n  blocks b in g threads t in 64 {\n    BODY\n  }\n}\n"


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-COOP-WARP", "if t % 2 == 0 { let v = warp_ballot(true); }"),
        ("E-COOP-WARP", "if t < 3 { let v = shuffle_up(t, 1); }"),
        ("E-TYPE-MISMATCH", "let v = warp_any(t);"),
        ("E-TYPE-MISMATCH", "let v = warp_match(true);"),
        ("E-ARITY", "let v = warp_all(true, false);"),
    ],
)
def test_every_vote_rule_refuses_with_its_code(code, body):
    refused(code, BLOCK.replace("BODY", body))


def test_a_vote_joins_the_threads_of_a_cooperative_block_only():
    refused("E-COOP-WARP", "fn f(c:bool) -> u32 = warp_ballot(c);")

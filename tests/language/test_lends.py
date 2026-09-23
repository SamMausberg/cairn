"""A record that lends a view (`lends data[0..len];`): named where an array view is expected, it is that part
written out, and walked by `for`, it is the index loop over that part. Every accepted form emits the C++ of its
written-out form, runs natively under both compilers, and every refusal names its code.
"""

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.editor.formatting import format_source
from emitted import WARNINGS, refused, run, sanitized, watched

TEXT = "struct Text { data:Buf[u8]; len:usize; lends data[0..len]; }\n"
WINDOW = "struct Window { data:Buf[u64]; lo:usize; hi:usize; lends data[lo..hi]; }\n"
HELPERS = """
fn sum(n:usize, bytes:ro<u8>[n]) -> u64 {
  let mut t:u64 = 0;
  for i in 0..n { t += u64(bytes[i]); }
  return t;
}
fn fill(n:usize, out:rw<u8>[n], v:u8) { for i in 0..n { out[i] = v; } }
fn copy(n:usize, a:ro<u8>[n], b:rw<u8>[n]) { for i in 0..n { b[i] = a[i]; } }
fn gsum[T:integer](n:usize, xs:ro<T>[n]) -> T {
  let mut t = xs[0];
  for i in 1..n { t += xs[i]; }
  return t;
}
"""
LENT = """
fn main() -> i32 {
  let mut t = Text(Buf[u8](8), 3);
  fill(t, 2);
  let mut u = Text(Buf[u8](8), 3);
  copy(t, u);
  if sum(t) != 6 || t.sum() != 6 || gsum(u) != 6 || sum(t.len, t) != 6 { return 1; }
  let mut ts = Buf[Text](2);
  ts[1] = Text(Buf[u8](4), 4);
  fill(ts[1], 1);
  if sum(ts[1]) != 4 { return 2; }
  return 0;
}
"""
WRITTEN = """
fn main() -> i32 {
  let mut t = Text(Buf[u8](8), 3);
  fill(t.data[0..t.len], 2);
  let mut u = Text(Buf[u8](8), 3);
  copy(t.data[0..t.len], u.data[0..u.len]);
  if sum(t.data[0..t.len]) != 6 || sum(t.data[0..t.len]) != 6 || gsum(u.data[0..u.len]) != 6 || sum(t.len, t.data[0..t.len]) != 6 { return 1; }
  let mut ts = Buf[Text](2);
  ts[1] = Text(Buf[u8](4), 4);
  fill(ts[1].data[0..ts[1].len], 1);
  if sum(ts[1].data[0..ts[1].len]) != 4 { return 2; }
  return 0;
}
"""
WALKED = """
fn main() -> i32 {
  let mut t = Text(Buf[u8](8), 3);
  for i in 0..8 { t.data[i] = u8(i + 1); }
  let mut total:u64 = 0;
  for b in t { total += u64(b); }
  let mut w = Window(Buf[u64](10), 4, 7);
  for i in 0..10 { w.data[i] = u64(i); }
  let mut seen:u64 = 0;
  for k, v in w { seen += v * u64(k + 1); }
  if total != 6 || seen != 4 * 1 + 5 * 2 + 6 * 3 { return 1; }
  return 0;
}
"""
INDEXED = """
fn main() -> i32 {
  let mut t = Text(Buf[u8](8), 3);
  for i in 0..8 { t.data[i] = u8(i + 1); }
  let mut total:u64 = 0;
  for b_index in 0..t.len { let b = t.data[b_index]; total += u64(b); }
  let mut w = Window(Buf[u64](10), 4, 7);
  for i in 0..10 { w.data[i] = u64(i); }
  let mut seen:u64 = 0;
  for k in 0..w.hi - w.lo { let v = w.data[w.lo + k]; seen += v * u64(k + 1); }
  if total != 6 || seen != 4 * 1 + 5 * 2 + 6 * 3 { return 1; }
  return 0;
}
"""


def test_a_lent_record_is_the_part_it_names():
    """`sum(t)`, `t.sum()`, a generic instance, an explicit extent and an element of an array of records each emit
    exactly what the part `t.data[0..t.len]` emits: its guard, its unguarded span as the extent, nothing else."""
    lent, written = compile_source(TEXT + HELPERS + LENT)[0], compile_source(TEXT + HELPERS + WRITTEN)[0]
    assert lent == written
    assert "cr::part((v_t).v_data.data(), static_cast<std::size_t>(0ULL), (v_t).v_len, (v_t).v_data.size()" in lent


def test_a_lent_record_is_walked_as_its_index_loop():
    """`for b in t` is `for b_index in 0..t.len { let b = t.data[b_index]; }`, and a view that starts at a field is
    walked from it: the loop std writes by hand, each element read paying the guard of `data` it reads."""
    walked = compile_source(TEXT + WINDOW + WALKED)[0]
    assert walked == compile_source(TEXT + WINDOW + INDEXED)[0]
    assert "cr::at((v_t).v_data.data(), v_b_index, (v_t).v_data.size())" in walked


def test_the_projection_and_the_formatter_keep_the_clause():
    source = TEXT + WINDOW + WALKED
    projected = canonical_source(source)
    assert "struct Text { data:Buf[u8]; len:usize; lends data[0..len]; }" in projected
    assert "lends data[lo..hi];" in projected and compile_source(projected)[0] == compile_source(source)[0]
    assert format_source(source) == source and format_source(format_source(source)) == format_source(source)


def test_a_field_may_still_be_named_lends():
    compile_source("struct Odd { lends:usize; data:Buf[u8]; }\nfn main() -> i32 { let o = Odd(1, Buf[u8](1)); "
                   "return i32(o.lends) - 1; }")  # fmt: skip


@pytest.mark.parametrize(
    ("code", "source", "said"),
    [
        ("E-LENDS", "struct T { data:u64; len:usize; lends data[0..len]; }\nfn main() -> i32 { let t = T(1, 2); "
         "return 0; }", "a record lends a Buf field"),
        ("E-LENDS", "struct T { data:Buf[u8]; len:u32; lends data[0..len]; }\nfn main() -> i32 { "
         "let t = T(Buf[u8](1), 0); return 0; }", "len is u32"),
        ("E-LENDS", "struct T { data:Buf[u8]; len:usize; lends data[0..size]; }\nfn main() -> i32 { "
         "let t = T(Buf[u8](1), 0); return 0; }", "size is no field of it"),
        ("E-LENDS", "struct T { data:Buf[u8]; len:usize; lends data[0..len]; lends data[0..1]; }", "lends one view"),
        ("E-LENDS", "struct T { data:Buf[u8]; len:usize; lends data[4..2]; }\nfn main() -> i32 { "
         "let t = T(Buf[u8](1), 0); return 0; }", "ends before it begins"),
        ("E-LEASED", TEXT + HELPERS + "fn main() -> i32 { let mut t = Text(Buf[u8](4), 4); let h = spawn fill(t, 1); "
         "t.data[0] = 2; wait(h); return 0; }", "lent to h"),
        ("E-LEASED", TEXT + HELPERS + "fn main() -> i32 { let mut t = Text(Buf[u8](4), 4); let h = spawn fill(t, 1); "
         "t.data = Buf[u8](2); wait(h); return 0; }", "lent to h"),
        ("E-ALIAS", TEXT + HELPERS + "fn main() -> i32 { let mut t = Text(Buf[u8](4), 4); copy(t, t); return 0; }",
         "overlapping"),
        ("E-TYPE-MISMATCH", TEXT + HELPERS + "fn g(t:ro<Text>) { fill(t, 1); }", "rw<u8>"),
        ("E-CALL-SHAPE", TEXT + HELPERS + "fn pick() -> usize = 1;\nfn main() -> i32 { let mut ts = Buf[Text](2); "
         "return i32(sum(ts[pick()])); }", "bind a call first"),
        ("E-MOVED", TEXT + HELPERS + "fn eat(t:Text) {}\nfn main() -> i32 { let t = Text(Buf[u8](4), 4); eat(t); "
         "return i32(sum(t)); }", "t was moved"),
        ("E-ELEMENT-LOOP", "struct Rows { data:Buf[Buf[u8]]; len:usize; lends data[0..len]; }\nfn main() -> i32 { "
         "let r = Rows(Buf[Buf[u8]](2), 2); for row in r { } return 0; }", "is not copyable"),
    ],
)  # fmt: skip
def test_what_a_lent_view_refuses(code, source, said):
    assert said in refused(code, source)["message"]


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_lent_records_run_natively(tmp_path, cxx):
    for body in (LENT, WALKED):
        cpp = compile_source(TEXT + WINDOW + HELPERS + body)[0]
        assert run(tmp_path, cpp, *sanitized(cxx), *WARNINGS, cxx=cxx).returncode == 0


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_length_past_its_storage_traps_at_the_part(tmp_path, cxx):
    """The bounds are read again at every call and nothing about them is assumed: a length past the storage traps
    at the part's own guard before the callee reads anything, and never reaches AddressSanitizer."""
    past = TEXT + HELPERS + "fn main() -> i32 { let mut t = Text(Buf[u8](8), 3); t.len = 20; return i32(sum(t)); }"
    done = run(tmp_path, compile_source(past)[0], *sanitized(cxx), cxx=cxx)
    assert done.returncode == -6 and "AddressSanitizer" not in done.stderr


def test_a_task_holding_lent_elements_races_with_nothing(tmp_path):
    """A task leases the elements, not the length: the spawner may set `t.len` while the task fills what it was
    lent, since the task holds its own pointer and count. ThreadSanitizer sees no race."""
    source = (
        TEXT
        + HELPERS
        + """
fn main() -> i32 {
  let mut t = Text(Buf[u8](40000), 40000);
  let h = spawn fill(t, 3);
  t.len = 1;
  wait(h);
  t.len = 40000;
  if sum(t) != 120000 { return 1; }
  return 0;
}
"""
    )
    done = watched(tmp_path, compile_source(source)[0], "clang++", "thread")
    assert done.returncode == 0 and "WARNING: ThreadSanitizer" not in done.stderr, done.stderr[-2000:]


def test_a_packet_that_shows_a_lending_record_carries_its_card():
    """The card is chosen by what the packet shows: a record declaring `lends`, or the word itself."""
    from cairn.agent.agent_tools import EditSession

    source = TEXT + HELPERS + "fn total(t:ro<Text>) -> u64 = sum(t);\nfn main() -> i32 { return 0; }\n"
    assert "lends" in EditSession(source, "total").packet()["rule_cards"]
    assert "lends" not in EditSession(HELPERS + "fn main() -> i32 { return 0; }\n", "sum").packet()["rule_cards"]

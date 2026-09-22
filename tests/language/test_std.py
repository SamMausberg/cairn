"""The standard library as its first user sees it: CAIRN programs that must build and exit 0.

Collections, text, sorting and memory are exercised natively here, and every generic of the library
is instantiated once; files, sockets and the system calls are in test_std_io.py. The rejection
table records the API guarantees that the type system, not a convention, is responsible for:
overlapping views cannot be handed to one call, an unhashable key cannot key a map.

g++ is used wherever the program stays inside copyable sums. A sum that carries an owner emits
a designated initializer that g++ rejects under -Werror=missing-field-initializers (see
docs/library.md and the issue list), so those programs are built with clang++ only.
"""

import pathlib
import subprocess

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import native, refused

BOTH = ["clang++", "g++"]


SIGNED_TEXT = """
import std.core (Option, Result);
import std.io;
import std.text;
import std.vec (Vec);

fn parsed(n:usize, s:ro<u8>[n], want:i64) -> bool {
  match text.parse_i64(n, s) { Result.Ok(v) => { return v == want; } Result.Err(why) => { return false; } }
}

fn refused(n:usize, s:ro<u8>[n], at:usize, overflow:bool) -> bool {
  match text.parse_i64(n, s) {
    Result.Ok(v) => { return false; }
    Result.Err(why) => {
      match why {
        text.ParseError.Invalid(i) => { return !overflow && i == at; }
        text.ParseError.Overflow(i) => { return overflow && i == at; }
        text.ParseError.Empty => { return false; }
      }
    }
  }
}

fn round_trip(value:i64) -> bool {
  stack out:u8[21] = zeroed;
  let used = text.write_i64(len(out), out, value);
  if used == 0 { return false; }
  return parsed(used, out[0..used], value);
}

fn main() -> i32 {
  if !parsed(len("-42"), "-42", -42) || !parsed(len("42"), "42", 42) || !parsed(len("-0"), "-0", 0) { return 1; }
  if !parsed(len("9223372036854775807"), "9223372036854775807", 9223372036854775807) { return 2; }
  if !parsed(len("-9223372036854775808"), "-9223372036854775808", -9223372036854775807 - 1) { return 3; }
  if !refused(len("9223372036854775808"), "9223372036854775808", 18, true) { return 4; }
  if !refused(len("-9223372036854775809"), "-9223372036854775809", 19, true) { return 5; }
  if !refused(len("-"), "-", 1, false) || !refused(len("12x"), "12x", 2, false) { return 6; }
  if !round_trip(-9223372036854775807 - 1) || !round_trip(0) || !round_trip(-7) || !round_trip(1234567) { return 7; }
  stack tiny:u8[2] = zeroed;
  let short = text.write_i64(len(tiny), tiny, -12);
  let two = text.write_i64(len(tiny), tiny, -1);
  if short != 0 || two != 2 || tiny[0] != 45 { return 8; }
  let mut line = vec.new[u8]();
  text.push_i64(line, -305);
  if line.len != 4 || line.data[0] != 45 || line.data[3] != 53 { return 9; }
  let path = "log/readings.csv";
  if !text.starts_with(len(path), path, len("log/"), "log/") || text.starts_with(len(path), path, len("logs"), "logs") { return 10; }
  if !text.ends_with(len(path), path, len(".csv"), ".csv") || text.ends_with(len(path), path, len("x.csv"), "x.csv") { return 11; }
  if text.starts_with(len(path), path, len("log/readings.csv!"), "log/readings.csv!") { return 12; }
  match text.find_last_byte(len(path), path, 46) { Option.Some(at) => { if at != 12 { return 13; } } Option.None => { return 14; } }
  match text.find_last_byte(len(path), path, 47) { Option.Some(at) => { if at != 3 { return 15; } } Option.None => { return 16; } }
  match text.find_last_byte(len(path), path, 33) { Option.Some(at) => { return 17; } Option.None => {} }
  io.print_i64(-9223372036854775807 - 1);
  io.newline();
  match io.read_stdin(0, tiny[0..0]) { Result.Ok(got) => { if got != 0 { return 18; } } Result.Err(why) => { return 19; } }
  return 0;
}
"""


VEC_EDITS = """
import std.core (Option, Eq);
import std.map;
import std.vec (Vec);

fn main() -> i32 {
  let mut xs = vec.new[u64]();
  for i in 0..5 { vec.push(xs, u64(i) * 10); }          // 0 10 20 30 40
  vec.insert(xs, 2, 15);                                 // 0 10 15 20 30 40
  vec.insert(xs, 6, 50);                                 // append at the end
  if xs.len != 7 || vec.get(xs, 2) != 15 || vec.get(xs, 3) != 20 || vec.get(xs, 6) != 50 { return 1; }
  match vec.remove(xs, 2) { Option.Some(v) => { if v != 15 { return 2; } } Option.None => { return 3; } }
  if xs.len != 6 || vec.get(xs, 2) != 20 || vec.get(xs, 5) != 50 { return 4; }
  match vec.swap_remove(xs, 1) { Option.Some(v) => { if v != 10 { return 5; } } Option.None => { return 6; } }
  if xs.len != 5 || vec.get(xs, 1) != 50 || vec.get(xs, 4) != 40 { return 7; }
  match vec.remove(xs, 5) { Option.Some(v) => { return 8; } Option.None => {} }
  match vec.swap_remove(xs, 9) { Option.Some(v) => { return 9; } Option.None => {} }
  let wanted:u64 = 30;
  match vec.find(xs, wanted) { Option.Some(at) => { if at != 3 { return 10; } } Option.None => { return 11; } }
  let missing:u64 = 31;
  match vec.find(xs, missing) { Option.Some(at) => { return 12; } Option.None => {} }
  let mut seen = map.new[u64, u64]();
  map.insert(seen, 7, 1);
  let present:u64 = 7;
  let absent:u64 = 8;
  if !map.contains(seen, present) || map.contains(seen, absent) { return 13; }
  return 0;
}
"""

VEC_OF_OWNERS_EDITS = """
import std.core (Option);
import std.vec (Vec);

fn line(byte:u8) -> Vec[u8] { let mut v = vec.new[u8](); vec.push(v, byte); return v; }

fn main() -> i32 {
  let mut lines = vec.new[Vec[u8]]();
  for i in 0..4 { let l = line(u8(65 + i)); vec.push(lines, l); }   // A B C D
  let inserted = line(90);
  vec.insert(lines, 1, inserted);                                    // A Z B C D
  match vec.remove(lines, 3) {                                       // A Z B D
    Option.Some(taken) => { if taken.len != 1 || taken.data[0] != 67 { return 1; } }
    Option.None => { return 2; }
  }
  match vec.swap_remove(lines, 0) {                                  // D Z B
    Option.Some(taken) => { if taken.data[0] != 65 { return 3; } }
    Option.None => { return 4; }
  }
  if lines.len != 3 || lines.data[0].data[0] != 68 || lines.data[1].data[0] != 90 || lines.data[2].data[0] != 66 { return 5; }
  return 0;
}
"""


MEM_TEXT_SORT = """
import std.core (Option, Result);
import std.mem;
import std.sort;
import std.text;
import std.vec (Vec);

fn main() -> i32 {
  buffer a:u8[8] = zeroed;
  buffer b:u8[8] = zeroed;
  mem.fill(len(a), a, 7);
  mem.copy(len(b), b, a);
  if !mem.equal(len(a), a, len(b), b) { return 1; }
  b[3] = 1;
  if mem.equal(len(a), a, len(b), b) { return 2; }

  buffer out:u8[32] = zeroed;
  let used = text.write_u64(len(out), out, 18446744073709551615);
  if used != 20 { return 3; }
  match text.parse_u64(used, out[0..used]) {
    Result.Ok(value) => { if value != 18446744073709551615 { return 4; } }
    Result.Err(e) => { return 5; }
  }
  match text.parse_u64(3, "1x2") {
    Result.Ok(value) => { return 6; }
    Result.Err(e) => { match e { text.ParseError.Invalid(at) => { if at != 1 { return 7; } }
                                 text.ParseError.Empty => { return 8; }
                                 text.ParseError.Overflow(at) => { return 9; } } }
  }
  match text.parse_u64(21, "999999999999999999999") {
    Result.Ok(value) => { return 10; }
    Result.Err(e) => {}
  }
  let short = text.write_u64(2, out[0..2], 12345);
  if short != 0 { return 11; }
  let hexed = text.write_hex(len(out), out, 48879, 4);
  if hexed != 4 { return 12; }
  if out[0] != 'b' || out[1] != 'e' || out[2] != 'e' || out[3] != 'f' { return 13; }

  let hello = "hello world";
  let world = "world";
  match text.find(len(hello), hello, len(world), world) {
    Option.Some(at) => { if at != 6 { return 14; } }
    Option.None => { return 15; }
  }
  match text.find_byte(len(hello), hello, ' ', 0) {
    Option.Some(at) => { if at != 5 { return 16; } }
    Option.None => { return 17; }
  }
  if text.compare(len(hello), hello, len(world), world) >= 0 { return 18; }
  if !text.equal(len(world), world, len(world), world) { return 19; }

  let mut line = vec.new[u8]();
  text.push_u64(line, 42);
  line.extend_from(len(world), world);
  if line.len != 7 || line.data[0] != '4' || line.data[2] != 'w' { return 20; }
  line.truncate(2);
  if line.len != 2 { return 21; }

  buffer xs:u64[7] = zeroed;
  xs[0]=9; xs[1]=1; xs[2]=8; xs[3]=1; xs[4]=7; xs[5]=0; xs[6]=5;
  sort.sort(len(xs), xs);
  for i in 1..7 { if xs[i-1] > xs[i] { return 22; } }
  let key:u64 = 8;
  match sort.search(len(xs), xs, key) {
    Option.Some(at) => { if xs[at] != 8 { return 23; } }
    Option.None => { return 24; }
  }
  let absent:u64 = 6;
  match sort.search(len(xs), xs, absent) {
    Option.Some(at) => { return 25; }
    Option.None => {}
  }
  sort.sort_by(len(xs), xs, |p:ro<u64>, q:ro<u64>| -> bool { return p > q; });
  if xs[0] != 9 || xs[6] != 0 { return 26; }
  return 0;
}
"""

MAP_SCALARS = """
import std.core (Option);
import std.map (Map);

fn main() -> i32 {
  let mut m = map.new[u64, u64]();
  for i in 0..500 { m.insert(u64(i), u64(i) * 3); }
  if m.count() != 500 { return 1; }
  for i in 0..500 {
    let k = u64(i);
    match m.find(k) {
      Option.Some(slot) => { if m.vals[slot] != u64(i) * 3 { return 2; } }
      Option.None => { return 3; }
    }
  }
  let replaced:u64 = 8;  // even, so the removal pass below takes it away again
  m.insert(replaced, 1);
  if m.count() != 500 { return 4; }
  match m.find(replaced) {
    Option.Some(slot) => { if m.vals[slot] != 1 { return 5; } }
    Option.None => { return 6; }
  }
  for i in 0..250 {
    let k = u64(i) * 2;
    match m.remove(k) {
      Option.Some(value) => {}
      Option.None => { return 7; }
    }
  }
  if m.count() != 250 { return 8; }
  let mut live:usize = 0;
  for slot in 0..m.slots() { if m.live(slot) { live = live + 1; } }
  if live != 250 { return 9; }
  let gone:u64 = 4;
  match m.find(gone) {
    Option.Some(slot) => { return 10; }
    Option.None => {}
  }
  // erased slots must not break the probe path of a key behind them
  for i in 0..250 {
    let k = u64(i) * 2 + 1;
    match m.find(k) {
      Option.Some(slot) => { if m.vals[slot] != k * 3 { return 11; } }
      Option.None => { return 12; }
    }
  }
  return 0;
}
"""

MAP_OWNERS = """
import std.core (Option);
import std.map (Map);
import std.vec (Vec);

fn main() -> i32 {
  let mut m = map.new[u64, Vec[u8]]();
  for i in 0..200 {
    let mut v = vec.new[u8]();
    v.push(u8(i % 256));
    v.push(u8(i % 251));
    m.insert(u64(i), v);
  }
  if m.count() != 200 { return 1; }
  let key:u64 = 7;
  match m.find(key) {
    Option.Some(slot) => { if m.vals[slot].len != 2 || m.vals[slot].data[0] != 7 { return 2; } }
    Option.None => { return 3; }
  }
  // replacing an owner value releases the old one inside the map
  let mut fresh = vec.new[u8]();
  fresh.push(99);
  m.insert(key, fresh);
  match m.find(key) {
    Option.Some(slot) => { if m.vals[slot].len != 1 || m.vals[slot].data[0] != 99 { return 4; } }
    Option.None => { return 5; }
  }
  match m.remove(key) {
    Option.Some(taken) => { if taken.len != 1 { return 6; } }
    Option.None => { return 7; }
  }
  if m.count() != 199 { return 8; }
  return 0;
}
"""

ARENA_GRAPH = """
import std.core (Option);
import std.arena (Arena, Handle);
import std.vec (Vec);

// A cyclic graph: owners cannot point at each other, handles can.
struct Node { name:u64; edges:Vec[Handle]; }

fn link(a:rw<Arena[Node]>, from:Handle, to:Handle) -> bool {
  match a.find(from) {
    Option.Some(slot) => { a.items[slot].edges.push(to); return true; }
    Option.None => { return false; }
  }
}

fn main() -> i32 {
  let mut a = arena.new[Node]();
  let no_edges = vec.new[Handle]();
  let first = a.insert(Node(1, no_edges));
  let more = vec.new[Handle]();
  let second = a.insert(Node(2, more));
  let out_edge = link(a, first, second);   // a call that writes cannot be a nested operand
  if !out_edge { return 1; }
  let back_edge = link(a, second, first);
  if !back_edge { return 2; }
  match a.find(first) {
    Option.Some(slot) => {
      let out = a.items[slot].edges.data[0];
      match a.find(out) {
        Option.Some(other) => {
          if a.items[other].name != 2 { return 3; }
          let back = a.items[other].edges.data[0];
          match a.find(back) {
            Option.Some(home) => { if a.items[home].name != 1 { return 4; } }
            Option.None => { return 5; }
          }
        }
        Option.None => { return 6; }
      }
    }
    Option.None => { return 7; }
  }
  match a.remove(second) {
    Option.Some(node) => { if node.name != 2 { return 8; } }
    Option.None => { return 9; }
  }
  match a.find(second) {
    Option.Some(slot) => { return 10; }
    Option.None => {}
  }
  // the slot comes back, the handle does not
  let again = vec.new[Handle]();
  let third = a.insert(Node(3, again));
  if third.slot != second.slot { return 11; }
  if third.generation == second.generation { return 12; }
  match a.find(second) {
    Option.Some(slot) => { return 13; }
    Option.None => {}
  }
  if a.count() != 2 { return 14; }
  let mut counted:usize = 0;
  for slot in 0..a.slots() { if a.alive(slot) { counted = counted + 1; } }
  if counted != 2 { return 15; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", BOTH)
def test_mem_text_sort_and_vec(tmp_path, cxx):
    native(tmp_path, MEM_TEXT_SORT, cxx)


@pytest.mark.parametrize("cxx", BOTH)
def test_signed_decimal_prefixes_and_the_last_byte(tmp_path, cxx):
    done = native(tmp_path, SIGNED_TEXT, cxx)
    assert done.stdout == "-9223372036854775808\n"


@pytest.mark.parametrize("cxx", BOTH)
def test_vec_edits_and_map_contains(tmp_path, cxx):
    native(tmp_path, VEC_EDITS, cxx)


def test_vec_edits_move_owners_without_copying(tmp_path):
    native(tmp_path, VEC_OF_OWNERS_EDITS)


@pytest.mark.parametrize("cxx", BOTH)
def test_map_of_scalars(tmp_path, cxx):
    native(tmp_path, MAP_SCALARS, cxx)


def test_map_of_owners(tmp_path):
    native(tmp_path, MAP_OWNERS)


def test_arena_holds_a_cyclic_graph(tmp_path):
    native(tmp_path, ARENA_GRAPH)


def test_sort_moves_owners_without_copying(tmp_path):
    """Heapsort only swaps, so it sorts affine elements as well as scalars."""
    native(
        tmp_path,
        """
        import std.core (Ord);
        import std.sort;
        struct Boxed { rank:u64; data:Buf[u64]; }
        impl Ord for Boxed { fn less(a:ro<Boxed>, b:ro<Boxed>) -> bool = a.rank < b.rank; }
        fn main() -> i32 {
          let mut xs = Buf[Boxed](5);
          for i in 0..5 {
            let mut payload = Buf[u64](1);
            payload[0] = u64(i);
            xs[i] = Boxed(u64(5 - i), payload);
          }
          sort.sort(len(xs), xs);
          for i in 0..5 {
            if xs[i].rank != u64(i + 1) { return 1; }
            if xs[i].data[0] != u64(4 - i) { return 2; }
          }
          return 0;
        }
        """,
    )


# The API guarantees that are types, not documentation ------------------------------------------

UPDATE_REACHES_THE_MAP = """
import std.map (Map);
fn main() -> i32 {
  let mut m = map.new[u64, u64]();
  m.insert(1, 10);
  let found = m.update(1, |v:rw<u64>| { m.insert(2, 20); v = 0; });
  return 0;
}
"""

OVERLAPPING_COPY = """
import std.mem;
fn main() -> i32 {
  buffer b:u8[8] = zeroed;
  let mid:usize = 3;
  mem.copy(3, b[0..mid], b[1..4]);
  return 0;
}
"""

UNHASHABLE_KEY = """
import std.map (Map);
struct Point { x:u64; y:u64; }
fn main() -> i32 {
  let mut m = map.new[Point, u64]();
  m.insert(Point(1, 2), 3);
  return 0;
}
"""

WRITE_THROUGH_READONLY = """
import std.mem;
fn wrong(n:usize, xs:ro<u8>[n]) { xs[0] = 1; }
fn main() -> i32 = 0;
"""

FILL_A_READONLY_VIEW = """
import std.mem;
fn wrong(n:usize, xs:ro<u8>[n]) { mem.fill(n, xs, 0); }
fn main() -> i32 = 0;
"""

CLOSURE_MOVES_OWNER = """
import std.sort;
fn main() -> i32 {
  buffer xs:u64[4] = zeroed;
  let held = Buf[u64](1);
  sort.sort_by(len(xs), xs, |a:ro<u64>, b:ro<u64>| -> bool { let stolen = held; return a < b; });
  return 0;
}
"""

MISMATCHED_EXTENT = """
import std.mem;
fn main() -> i32 {
  buffer a:u8[4] = zeroed;
  buffer b:u8[8] = zeroed;
  mem.copy(len(a), a, b);
  return 0;
}
"""


@pytest.mark.parametrize(
    "code,source",
    [
        ("E-ALIAS", OVERLAPPING_COPY),
        ("E-ALIAS", UPDATE_REACHES_THE_MAP),  # the closure is lent the value while the call holds the map
        ("E-TRAIT-IMPL", UNHASHABLE_KEY),
        ("E-WRITE-LEASE", WRITE_THROUGH_READONLY),
        ("E-TYPE-MISMATCH", FILL_A_READONLY_VIEW),
        ("E-MOVE-IN-LOOP", CLOSURE_MOVES_OWNER),
        ("E-TYPE-MISMATCH", MISMATCHED_EXTENT),
    ],
)
def test_the_type_system_protects_the_api(code, source):
    refused(code, source)


# Effect rows are part of each module's contract ------------------------------------------------


def rows(source):
    return compile_source(source)[1]["functions"]


def test_sort_allocates_nothing_and_does_not_recurse():
    functions = rows("""
        import std.sort;
        fn main() -> i32 { buffer xs:u64[4] = zeroed; sort.sort(len(xs), xs); return 0; }
        """)
    row = set(functions["std.sort.sort[u64]"]["effects"])
    assert not {"alloc", "free", "io"} & row  # `diverge` is there: a while loop may not end
    assert {"read:xs", "write:xs", "indirect_call"} <= row


def test_map_growth_is_visible_in_the_callers_row():
    functions = rows("""
        import std.map (Map);
        fn main() -> i32 { let mut m = map.new[u64, u64](); m.insert(1, 2); return 0; }
        """)
    assert {"alloc", "free", "zero_init"} <= set(functions["std.map.insert[u64, u64]"]["effects"])
    assert {"alloc", "free"} <= set(functions["main"]["effects"])


def test_a_release_charges_free_without_charging_alloc():
    """A map of owning values releases the value it replaces, which `place` says and never allocates for."""
    functions = rows("""
        import std.map (Map);
        import std.vec (Vec);
        fn drops(v:Vec[u8]) {}
        fn main() -> i32 {
          let mut m = map.new[u64, Vec[u8]]();
          let mut first = vec.new[u8]();
          first.push(1);
          m.insert(7, first);
          let mut second = vec.new[u8]();
          m.insert(7, second);
          let mut third = vec.new[u8]();
          drops(third);
          return 0;
        }
        """)
    place = set(functions["std.map.place[u64, std.vec.Vec[u8]]"]["effects"])
    assert "free" in place and "alloc" not in place
    assert functions["drops"]["effects"] == ["free"]  # A function that only drops what it was given.
    assert {"alloc", "free"} <= set(functions["std.map.insert[u64, std.vec.Vec[u8]]"]["effects"])


def test_parallel_and_device_placement_are_separate_effects():
    functions = rows("""
        fn on_threads(n:usize, out:rw<u64>[n], src:ro<u64>[n]) { parallel i in n { out[i] = src[i] + 1; } }
        fn on_lanes(n:usize, out:rw<u64>[n]@device, src:ro<u64>[n]@device) { parallel i in n { out[i] = src[i] + 1; } }
        fn main() -> i32 = 0;
        """)
    assert "par:host" in functions["on_threads"]["effects"]
    assert "par:device" in functions["on_lanes"]["effects"]


MAP_SLOTS = """
import std.core (Option);
import std.map (Map, Slot);

// A Slot names one entry: it resolves while that entry lives where it was, and never to another.
fn resolves(m:ro<Map[u64, u64]>, s:Slot) -> bool {
  match m.resolve(s) { Option.Some(at) => { return true; } Option.None => { return false; } }
}

fn slot_of(m:ro<Map[u64, u64]>, key:u64) -> Slot {
  match m.slot(key) { Option.Some(s) => { return s; } Option.None => { return Slot(0, 0); } }
}

fn main() -> i32 {
  let mut m = map.new[u64, u64]();
  m.insert(1, 10);                                  // capacity 8, and 1 and 9 start their probes alike
  let first = slot_of(m, 1);
  m.insert(1, 11);                                  // a new value for the same key: the same entry
  if !resolves(m, first) { return 1; }
  let gone = m.remove(1);
  if resolves(m, first) { return 2; }               // removed
  m.insert(9, 90);                                  // lands in the tombstone the removal left
  let ninth = slot_of(m, 9);
  if ninth.at != first.at { return 3; }             // the very slot, so an index alone would lie
  if resolves(m, first) || !resolves(m, ninth) { return 4; }
  for k in 100..120 { m.insert(u64(k), 0); }        // growth rehashes every entry
  if resolves(m, ninth) { return 5; }               // moved: look the key up again
  if !resolves(m, slot_of(m, 9)) { return 6; }
  if resolves(m, Slot(0, 0)) || resolves(m, Slot(1000000, 1)) { return 7; }   // forged
  let changed = m.update(9, |v:rw<u64>| { v = v + 1; });
  let missing = m.update(2, |v:rw<u64>| { v = 0; });
  match m.get(9) {
    Option.Some(v) => { if !changed || missing || v != 91 { return 8; } }
    Option.None => { return 9; }
  }
  match m.get(2) { Option.Some(v) => { return 10; } Option.None => {} }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", BOTH)
def test_a_map_slot_never_names_another_entry(tmp_path, cxx):
    native(tmp_path, MAP_SLOTS, cxx)


COVERAGE = """
import std.core (Option);
import std.arena (Arena, Handle);
import std.map (Map);
import std.mem;
import std.sort;
import std.vec (Vec);

// Touch every generic the library declares: an instance, not its template, is what typechecks,
// so anything left uninstantiated here would ship unchecked.
fn main() -> i32 {
  let mut v = vec.with_capacity[u8](4);
  v.push('a');
  v.reserve(16);
  if v.capacity() < 16 { return 1; }
  v.set(0, 'z');
  if v.get(0) != 'z' { return 2; }
  v.extend_from(3, "abc");
  if v.len != 4 { return 3; }
  v.truncate(2);
  match v.pop() {
    Option.Some(b) => { if b != 'a' { return 4; } }
    Option.None => { return 5; }
  }
  v.clear();
  if v.len != 0 { return 6; }
  v.insert(0, 'q');
  v.insert(1, 'r');
  let q:u8 = 'q';
  match v.find(q) {
    Option.Some(at) => { if at != 0 { return 22; } }
    Option.None => { return 23; }
  }
  match v.remove(0) {
    Option.Some(b) => { if b != 'q' { return 24; } }
    Option.None => { return 25; }
  }
  match v.swap_remove(0) {
    Option.Some(b) => { if b != 'r' { return 26; } }
    Option.None => { return 27; }
  }

  buffer xs:u64[4] = zeroed;
  buffer ys:u64[4] = zeroed;
  mem.fill(len(xs), xs, 3);
  mem.copy(len(ys), ys, xs);
  if !mem.equal(len(xs), xs, len(ys), ys) { return 7; }
  sort.sort(len(xs), xs);
  sort.sort_by(len(xs), xs, |a:ro<u64>, b:ro<u64>| -> bool { return a < b; });
  let three:u64 = 3;
  match sort.search(len(xs), xs, three) {
    Option.Some(at) => {}
    Option.None => { return 8; }
  }

  let mut m = map.new[u64, u64]();
  for i in 0..32 { m.insert(u64(i), u64(i)); }
  if m.count() != 32 || m.slots() < 32 { return 9; }
  match m.find(three) {
    Option.Some(slot) => { if !m.live(slot) { return 10; } }
    Option.None => { return 11; }
  }
  match m.remove(three) {
    Option.Some(value) => { if value != 3 { return 12; } }
    Option.None => { return 13; }
  }
  if m.contains(three) { return 28; }
  match m.slot(4) {
    Option.Some(s) => { match m.resolve(s) { Option.Some(at) => {} Option.None => { return 29; } } }
    Option.None => { return 30; }
  }
  let bumped = m.update(4, |x:rw<u64>| { x = x + 1; });
  match m.get(4) {
    Option.Some(x) => { if !bumped || x != 5 { return 31; } }
    Option.None => { return 32; }
  }

  let mut a = arena.new[u64]();
  let mut last = a.insert(0);
  for i in 0..32 { last = a.insert(u64(i)); }
  if a.count() != 33 || a.slots() < 33 { return 14; }
  if !a.alive(last.slot) { return 15; }
  let same = a.handle(last.slot);
  if same.generation != last.generation { return 16; }
  match a.find(last) {
    Option.Some(slot) => { if a.items[slot] != 31 { return 17; } }
    Option.None => { return 18; }
  }
  match a.remove(last) {
    Option.Some(value) => { if value != 31 { return 19; } }
    Option.None => { return 20; }
  }
  match a.remove(last) {
    Option.Some(value) => { return 21; }
    Option.None => {}
  }
  let recycled = a.insert(7);
  if recycled.slot != last.slot { return 22; }
  return 0;
}
"""


def test_every_generic_in_the_library_is_instantiated_and_runs(tmp_path):
    receipt = compile_source(COVERAGE)[1]
    assert receipt["uninstantiated_templates"] == []
    native(tmp_path, COVERAGE)


def test_no_packaged_source_is_hidden_from_version_control():
    """`core.*` once ignored std/core.cairn: the suite was green here and broken in every fresh checkout."""
    root = pathlib.Path(__file__).resolve().parents[2]
    files = [str(p) for p in (root / "src" / "cairn").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    done = subprocess.run(["git", "check-ignore", *files], cwd=root, capture_output=True, text=True)
    if done.returncode == 128:
        pytest.skip("not a git checkout")
    assert done.stdout == ""


def test_every_packaged_template_needs_only_what_its_bounds_promise():
    """The library's generics are checked once against their bounds, so a misuse is reported at the call
    (`Token is linear, not affine; std.vec.push needs [T:affine]`), never from inside the library."""
    from cairn.compiler.cairnc import certify_templates

    modules = sorted(p.stem for p in (pathlib.Path(__file__).resolve().parents[2] / "src/cairn/std").glob("*.cairn"))
    source = "".join(f"import std.{m};\n" for m in modules) + "fn main() -> i32 { return 0; }\n"
    verdicts = certify_templates(source)
    assert len(verdicts) >= 40 and {n: v for n, v in verdicts.items() if v != "ok"} == {}
    token = "import std.vec as vec;\nlinear struct Token { id:u64; }\n"
    said = refused("E-BOUND", token + "fn main() -> i32 { let mut v = vec.new[Token](); return 0; }")["message"]
    assert "std.vec.new needs [T:affine]" in said


def test_the_api_reference_is_what_the_compiler_says_today():
    """docs/std_api.md is generated (`cairn doc --std`): signatures, bounds, comments and inferred effect rows."""
    from cairn.editor.docs import document, standard_library

    root = pathlib.Path(__file__).resolve().parents[2]
    assert (root / "docs/std_api.md").read_text(encoding="utf-8") == standard_library(), "run `make docs`"
    own = document("module m;\n// Doubles.\npub fn twice[T: integer](x:T) -> T = x + x;\nfn hidden() {}\n")
    assert "pub fn twice[T:integer](x:T) -> T" in own and "Doubles." in own and "hidden" not in own
    assert "pub fn twice[T:integer](x:T) -> T  // effects: trap" in own
    assert own.startswith("A generic function's effects are what it may do for any arguments within its bounds")

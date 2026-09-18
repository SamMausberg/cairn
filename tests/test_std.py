"""The standard library as its first user sees it: CAIRN programs that must build and exit 0.

Every module is exercised natively. The rejection table records the API guarantees that the
type system, not a convention, is responsible for: a closed File cannot be used, a Socket
cannot be leaked, overlapping views cannot be handed to one call.

g++ is used wherever the program stays inside copyable sums. A sum that carries an owner emits
a designated initializer that g++ rejects under -Werror=missing-field-initializers (see
docs/std.md and the issue list), so those programs are built with clang++ only.
"""

import pathlib
import shutil
import subprocess

import pytest

from cairn.build import build
from cairn.cairnc import Diagnostic, compile_source
from cairn.project import load_project

BOTH = ["clang++", "g++"]


def native(tmp_path, source, cxx="clang++", timeout=180):
    """Build one CAIRN program in its own directory and run it; a nonzero exit is a failure."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    path = tmp_path / "program.cairn"
    path.write_text(source, encoding="utf-8")
    record = build(load_project(path), kind="exe", cxx=cxx, timeout=timeout)
    assert record["status"] == "native-built", record.get("stderr", "")[:4000]
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"
    return done


def rejects(code, source):
    with pytest.raises(Diagnostic) as error:
        compile_source(source)
    assert error.value.data["code"] == code, error.value.data


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

IO_FILES = """
import std.core (Option, Result);
import std.io (IoError);
import std.sys;
import std.text;
import std.vec (Vec);

fn path() -> Vec[u8] {
  let mut p = vec.new[u8]();
  let prefix = "/tmp/cairn-std-io-";
  p.extend_from(len(prefix), prefix);
  unsafe { text.push_u64(p, u64(sys.getpid())); }
  let suffix = ".txt";
  p.extend_from(len(suffix), suffix);
  p.push(0);
  return p;
}

fn scenario() -> Result[usize, IoError] {
  let mut name = path();
  let n = name.len;
  {
    let f = try io.open(n, name.data[0..n], io.TRUNCATE);
    defer io.close(f);
    let a = try io.write(f, 6, "alpha\\n");
    let b = try io.write(f, 5, "beta\\n");
    let flushed = try io.sync(f);
    let total = try io.size(f);
    if total != 11 { return Result.Ok(1); }
  }
  {
    let mut body = try io.read_file(n, name.data[0..n]);
    if body.len != 11 { return Result.Ok(2); }
    let m = body.len;
    let wanted = "alpha\\nbeta\\n";
    if !text.equal(m, body.data[0..m], len(wanted), wanted) { return Result.Ok(3); }
  }
  {
    let f = try io.open(n, name.data[0..n], io.APPEND);
    defer io.close(f);
    let where_at = try io.seek(f, 6, io.SET);
    if where_at != 6 { return Result.Ok(4); }
    buffer into:u8[5] = zeroed;
    let got = try io.read_full(f, len(into), into);
    if got != 5 || !text.equal(got, into[0..got], 5, "beta\\n") { return Result.Ok(5); }
    let cut = try io.truncate(f, 6);
    let after = try io.size(f);
    if after != 6 { return Result.Ok(6); }
  }
  let gone = try io.remove(n, name.data[0..n]);
  match io.read_file(n, name.data[0..n]) {
    Result.Ok(body) => { return Result.Ok(7); }
    Result.Err(e) => { if e.code != 2 { return Result.Ok(8); } }
  }
  return Result.Ok(0);
}

fn main() -> i32 {
  match scenario() {
    Result.Ok(step) => { return i32(step); }
    Result.Err(e) => { return 100; }
  }
}
"""

NET_LOOPBACK = """
import std.core (Result);
import std.io (IoError);
import std.net;
import std.sys;
import std.text;

// One process plays both ends: the listen backlog holds the connection until accept takes it.
fn round_trip(port:u16) -> Result[usize, IoError] {
  let server = try net.listen_on("\\x7f\\x00\\x00\\x01", port, 8);
  defer net.close(server);
  let client = try net.connect_to("\\x7f\\x00\\x00\\x01", port);
  defer net.close(client);
  let session = try net.accept(server);
  defer net.close(session);
  let sent = try net.send(client, 6, "ping\\r\\n");
  buffer into:u8[64] = zeroed;
  let got = try net.recv(session, len(into), into);
  if got != 6 { return Result.Err(IoError(1)); }
  if !text.equal(got, into[0..got], 6, "ping\\r\\n") { return Result.Err(IoError(2)); }
  let back = try net.send(session, 3, "ok\\n");
  let echo = try net.recv(client, len(into), into);
  if echo != 3 { return Result.Err(IoError(3)); }
  return Result.Ok(sent + back);
}

fn main() -> i32 {
  // The port is derived from the pid, below the ephemeral range (32768..60999 here), so
  // parallel test workers collide neither with each other nor with a borrowed client port.
  unsafe {
    let port = u16(u64(sys.getpid()) % 12000 + 20000);
    match round_trip(port) {
      Result.Ok(total) => { if total != 9 { return 1; } }
      Result.Err(e) => { return 2; }
    }
  }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", BOTH)
def test_mem_text_sort_and_vec(tmp_path, cxx):
    native(tmp_path, MEM_TEXT_SORT, cxx)


@pytest.mark.parametrize("cxx", BOTH)
def test_map_of_scalars(tmp_path, cxx):
    native(tmp_path, MAP_SCALARS, cxx)


def test_map_of_owners(tmp_path):
    native(tmp_path, MAP_OWNERS)


def test_arena_holds_a_cyclic_graph(tmp_path):
    native(tmp_path, ARENA_GRAPH)


def test_io_files(tmp_path):
    native(tmp_path, IO_FILES)


def test_net_loopback(tmp_path):
    native(tmp_path, NET_LOOPBACK)


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

CLOSED_FILE = """
import std.core (Result);
import std.io (IoError);
fn main() -> i32 {
  match io.open(6, "a.txt\\x00", io.READ) {
    Result.Ok(f) => {
      io.close(f);
      let wrote = io.write(f, 1, "x");
      return 0;
    }
    Result.Err(e) => { return 1; }
  }
}
"""

LEAKED_SOCKET = """
import std.core (Result);
import std.net;
fn main() -> i32 {
  match net.listen_on("\\x7f\\x00\\x00\\x01", 40404, 1) {
    Result.Ok(s) => { return 0; }
    Result.Err(e) => { return 1; }
  }
}
"""

UNCLOSED_FILE = """
import std.core (Result);
import std.io (IoError);
fn open_only() -> Result[usize, IoError] {
  let f = try io.open(6, "a.txt\\x00", io.READ);
  return Result.Ok(0);
}
fn main() -> i32 = 0;
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
        ("E-MOVED", CLOSED_FILE),
        ("E-LINEAR-LEAK", LEAKED_SOCKET),
        ("E-LINEAR-LEAK", UNCLOSED_FILE),
        ("E-ALIAS", OVERLAPPING_COPY),
        ("E-TRAIT-IMPL", UNHASHABLE_KEY),
        ("E-WRITE-LEASE", WRITE_THROUGH_READONLY),
        ("E-TYPE-MISMATCH", FILL_A_READONLY_VIEW),
        ("E-MOVE-IN-LOOP", CLOSURE_MOVES_OWNER),
        ("E-TYPE-MISMATCH", MISMATCHED_EXTENT),
    ],
)
def test_the_type_system_protects_the_api(code, source):
    rejects(code, source)


# Effect rows are part of each module's contract ------------------------------------------------


def rows(source):
    return compile_source(source)[1]["functions"]


def test_io_effects_name_the_foreign_symbol():
    functions = rows("""
        import std.io;
        fn main() -> i32 { let text = "hi"; io.println(len(text), text); return 0; }
        """)
    assert {"io", "ffi:write"} <= set(functions["std.io.put"]["effects"])
    assert {"io", "ffi:write"} <= set(functions["main"]["effects"])


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


def test_parallel_and_device_placement_are_separate_effects():
    functions = rows("""
        fn on_threads(n:usize, out:rw<u64>[n], src:ro<u64>[n]) { parallel i in n { out[i] = src[i] + 1; } }
        fn on_lanes(n:usize, out:rw<u64>[n]@device, src:ro<u64>[n]@device) { parallel i in n { out[i] = src[i] + 1; } }
        fn main() -> i32 = 0;
        """)
    assert "par:host" in functions["on_threads"]["effects"]
    assert "par:device" in functions["on_lanes"]["effects"]


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
    root = pathlib.Path(__file__).resolve().parents[1]
    files = [str(p) for p in (root / "src" / "cairn").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    done = subprocess.run(["git", "check-ignore", *files], cwd=root, capture_output=True, text=True)
    if done.returncode == 128:
        pytest.skip("not a git checkout")
    assert done.stdout == ""

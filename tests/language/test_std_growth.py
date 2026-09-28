"""How a Vec grows and how a file is read to its end, as programs built under both compilers with the address,
leak and undefined-behaviour sanitizers.

Growth moves every element through two views of one extent, so owners are exchanged and never copied, and a
copyable element costs one unguarded pass; `extend_from` copies into one part, which the emitted C++ shows.
`read_to_end` fills the room its Vec has spare and doubles a full one, and after its first read sizes the rest of a
file, so a directory is refused by that read before its size is asked. A `Buf` of plain values takes its zeros from
`calloc` and goes back with `free`, every other `Buf` from `new T[n]()`, and the pages of a large one that nothing
writes never become resident.
"""

import pytest
from test_established import body

from cairn.compiler.cairnc import compile_source
from emitted import native, watched

BOTH = ["clang++", "g++"]

GROWTH = """
import std.core (Option);
import std.vec (Vec);

fn line(byte:u8, count:usize) -> Vec[u8] {
  let mut v = vec.new[u8]();
  for _ in 0..count { vec.push(v, byte); }
  return v;
}

fn main() -> i32 {
  let mut xs = vec.new[u64]();
  for i in 0..1000 { vec.push(xs, u64(i) * 7); }
  for i in 0..1000 { if xs.data[i] != u64(i) * 7 { return 1; } }
  if vec.capacity(xs) < 1000 || vec.capacity(xs) > 2000 { return 2; }   // doubling from 4: 1024

  let mut rows = vec.new[Vec[u8]]();                                     // owners move by swap as it grows
  for i in 0..300 { let r = line(u8(i % 251), i % 5 + 1); vec.push(rows, r); }
  for i in 0..300 {
    if rows.data[i].len != i % 5 + 1 || rows.data[i].data[0] != u8(i % 251) { return 3; }
  }
  match vec.pop(rows) { Some(last) => { if last.len != 299 % 5 + 1 { return 4; } } None => return 5; }

  let mut bytes = vec.new[u8]();
  vec.extend_from(bytes, 0, "");                                         // nothing onto nothing
  if bytes.len != 0 || vec.capacity(bytes) != 0 { return 6; }
  let mut pattern = Buf[u8](10000);
  for i in 0..10000 { pattern[i] = u8(i % 253); }
  vec.extend_from(bytes, 3, "abc");
  vec.extend_from(bytes, pattern);
  vec.extend_from(bytes, 3, "xyz");
  if bytes.len != 10006 || bytes.data[0] != 'a' || bytes.data[10005] != 'z' { return 7; }
  for i in 0..10000 { if bytes.data[3 + i] != u8(i % 253) { return 8; } }

  let mut exact = vec.with_capacity[u8](6);
  vec.extend_from(exact, 6, "sixsix");                                   // fills the capacity, no growth
  if vec.capacity(exact) != 6 || exact.data[5] != 'x' { return 9; }
  vec.reserve(exact, 6);
  vec.reserve(exact, 7);
  if vec.capacity(exact) != 12 || exact.len != 6 || exact.data[0] != 's' { return 10; }
  return 0;
}
"""

READ_TO_END = """
import std.core (Result);
import std.env (Args);
import std.fs;
import std.io (IoError, File);
import std.text;
import std.vec (Vec);

// A regular file read from an offset onto a Vec that already holds three bytes.
fn from_a_file(n:usize, path:ro<u8>[n]) -> Result[usize, IoError] {
  let mut body = Buf[u8](100000);
  for i in 0..100000 { body[i] = u8(i % 251); }
  try fs.write(path, body);
  let f = try fs.open(path, io.READ);
  defer io.close(f);
  stack head:u8[10] = zeroed;
  try io.read_full(f, head);
  let mut into = vec.new[u8]();
  vec.extend_from(into, 3, "abc");
  let got = try io.read_to_end(f, into);
  if got != 99990 || into.len != 99993 || into.data[2] != 'c' { return Ok(1); }
  for i in 0..99990 { if into.data[3 + i] != u8((i + 10) % 251) { return Ok(2); } }
  if vec.capacity(into) != into.len + 1 { return Ok(3); }               // sized after one read: one allocation
  let again = try io.read_to_end(f, into);                              // at the end already
  if again != 0 || into.len != 99993 { return Ok(4); }
  return Ok(0);
}

// A file with no size to ask for, which still reads whole.
fn from_proc() -> Result[usize, IoError] {
  let f = try io.open(18, "/proc/self/status\\x00", io.READ);
  defer io.close(f);
  let mut into = vec.new[u8]();
  let got = try io.read_to_end(f, into);
  if got < 20 || !text.starts_with(into, "Name:") { return Ok(5); }
  return Ok(0);
}

// Standard input, a pipe here: every byte the test wrote, in order.
fn from_a_pipe() -> Result[usize, IoError] {
  let stdin = File(0);
  let mut into = vec.new[u8]();
  let read = io.read_to_end(stdin, into);
  io.close(stdin);
  let got = try read;
  if got != 300000 || into.len != 300000 { return Ok(6); }
  for i in 0..300000 { if into.data[i] != u8(97 + i % 26) { return Ok(7); } }
  return Ok(0);
}

// A directory is refused by its first read, EISDIR, before its size is asked: ext4 gives its end as 2^63 - 1.
fn from_a_directory(n:usize, dir:ro<u8>[n]) -> Result[usize, IoError] {
  let f = try fs.open(dir, io.READ);
  defer io.close(f);
  let mut into = vec.new[u8]();
  match io.read_to_end(f, into) {
    Ok(got) => { return Ok(8); }
    Err(e) => { if e.code != 21 || into.len != 0 { return Ok(9); } }
  }
  match fs.read(dir) {
    Ok(bytes) => { return Ok(10); }
    Err(e) => { if e.code != 21 { return Ok(11); } }
  }
  return Ok(0);
}

// Argument 1 is a directory the test made; the file goes inside it.
fn run() -> Result[usize, IoError] {
  let a = try env.args();
  let lo = a.begin(1);
  let hi = a.end(1);
  let mut path = vec.new[u8]();
  vec.extend_from(path, a.text.data[lo..hi]);
  vec.extend_from(path, 5, "/data");
  let mut step = try from_a_directory(a.text.data[lo..hi]);
  if step == 0 { step = try from_a_file(path); }
  if step == 0 { step = try from_proc(); }
  if step == 0 { step = try from_a_pipe(); }
  return Ok(step);
}

fn main() -> i32 {
  match run() { Ok(step) => { return i32(step); } Err(e) => { return 100 + e.code; } }
}
"""


# Zeroed storage of every kind a Buf holds: bytes, floats, a record, a sum, a fixed array, a record aligned past what
# calloc promises, and owners, each read back as zero and then released by an assignment over it, which the address
# sanitizer watches.
ZEROED = """
struct Wide { a:u64; b:f64; on:bool; }
struct Slot align(64) { hits:u64; }
struct Held { bytes:Buf[u8]; used:usize; }
enum Shape { Dot; Box(u64); }

fn main() -> i32 {
  let mut bytes = Buf[u8](1048576);
  for b in bytes { if b != 0 { return 1; } }
  let mut floats = Buf[f64](1000);
  let mut wides = Buf[Wide](1000);
  let mut slots = Buf[Slot](100);
  let mut helds = Buf[Held](8);
  let mut shapes = Buf[Shape](100);
  let mut rows = Buf[Array[u32, 4]](100);
  for i in 0..1000 { if floats[i] != 0.0 || wides[i].a != 0 || wides[i].b != 0.0 || wides[i].on { return 2; } }
  for i in 0..100 { if slots[i].hits != 0 { return 3; } }
  for i in 0..8 { if len(helds[i].bytes) != 0 || helds[i].used != 0 { return 4; } }
  for i in 0..100 {
    match shapes[i] { Dot => {} Box(_) => return 6; }                   // the first variant, as new T[n]() gives
    for k in 0..4 { if rows[i][k] != 0 { return 7; } }
  }
  helds[3] = Held(Buf[u8](5), 5);
  bytes[1048575] = 9;
  bytes = Buf[u8](16);
  floats = Buf[f64](2);
  wides = Buf[Wide](2);
  slots = Buf[Slot](2);
  helds = Buf[Held](2);
  shapes = Buf[Shape](2);
  rows = Buf[Array[u32, 4]](2);
  if len(bytes) + len(floats) + len(wides) + len(slots) + len(helds) + len(shapes) + len(rows) != 28 { return 5; }
  return 0;
}
"""

# A Buf of 256 MiB of which two bytes are written adds little to the process's peak resident set.
RESIDENT = """
import std.core (Option, Result);
import std.fs;
import std.text as text;
import std.vec (Vec);

// The process's peak resident set in KiB, from /proc/self/status, or 0 when it cannot be read.
fn peak_kib() -> u64 {
  match fs.read("/proc/self/status") {
    Ok(status) => {
      match text.find(status, "VmHWM:") {
        Some(at) => {
          let mut value:u64 = 0;
          for i in at + 6..status.len {
            let c = status.data[i];
            if c >= '0' && c <= '9' { value = value * 10 + u64(c - '0'); } else if value > 0 { return value; }
          }
          return value;
        }
        None => return 0;
      }
    }
    Err(_) => return 0;
  }
}

fn main() -> i32 {
  let before = peak_kib();
  let mut big = Buf[u8](268435456);
  big[0] = 1;
  big[268435455] = 2;
  let after = peak_kib();
  if before == 0 || after < before { return 1; }
  println(after - before);
  return 0;
}
"""


@pytest.mark.parametrize("cxx", BOTH)
def test_every_kind_of_zeroed_storage_reads_zero_and_is_released_once(tmp_path, cxx):
    done = watched(tmp_path, compile_source(ZEROED)[0], cxx, "address,undefined")
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, (done.returncode, done.stderr[-3000:])


@pytest.mark.parametrize("cxx", BOTH)
def test_a_large_zeroed_buf_is_resident_only_where_it_is_written(tmp_path, cxx):
    grown = int(native(tmp_path, RESIDENT, cxx).stdout)
    assert grown < 16384, f"{grown} KiB more for two bytes of a 256 MiB Buf"


@pytest.mark.parametrize("cxx", BOTH)
def test_a_vec_grows_by_doubling_and_moves_its_owners(tmp_path, cxx):
    done = watched(tmp_path, compile_source(GROWTH)[0], cxx, "address,undefined")
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, (done.returncode, done.stderr[-3000:])


@pytest.mark.parametrize("cxx", BOTH)
def test_read_to_end_sizes_a_file_after_a_read_and_refuses_a_directory(tmp_path, cxx):
    stdin = "".join(chr(97 + i % 26) for i in range(300000))
    (tmp_path / "files").mkdir()
    done = watched(tmp_path, compile_source(READ_TO_END)[0], cxx, "address,undefined", input=stdin,
                   args=[str(tmp_path / "files")])  # fmt: skip
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, (done.returncode, done.stderr[-3000:])


def test_growth_and_extend_from_guard_no_element():
    """What the timings in evidence/v1_2/std rest on: main's `reserve` guarded both indexes of every swap and
    `extend_from` every store (`cr::at`); now each takes its views as parts, and the loops index them bare."""
    cpp = compile_source(GROWTH)[0]
    for name in ("std_vec_reserve_u8", "std_vec_exchange_u8", "std_vec_extend_from_u8", "std_mem_copy_u8"):
        assert body(cpp, name) and "cr::at(" not in body(cpp, name), name
    for element in ("u8", "u64", "std_vec_Vec_u8"):  # an owner is exchanged, never copied
        assert "std::swap(v_a[v_i], v_b[v_i]);" in body(cpp, f"std_vec_exchange_{element}"), element
    assert body(cpp, "std_vec_extend_from_u8").count("cr::part(") == 1

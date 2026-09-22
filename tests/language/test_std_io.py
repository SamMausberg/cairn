"""The standard library's files, sockets and system calls, as programs that must build and exit 0.

The rejection table records what the type system guarantees about them: a closed File cannot be used, and
neither a File nor a Socket can be leaked. The effect rows name every foreign symbol they reach.
"""

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import native, refused

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


def test_io_files(tmp_path):
    native(tmp_path, IO_FILES)


def test_net_loopback(tmp_path):
    native(tmp_path, NET_LOOPBACK)


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


@pytest.mark.parametrize(
    "code,source", [("E-MOVED", CLOSED_FILE), ("E-LINEAR-LEAK", LEAKED_SOCKET), ("E-LINEAR-LEAK", UNCLOSED_FILE)]
)
def test_the_type_system_protects_files_and_sockets(code, source):
    refused(code, source)


def test_io_effects_name_the_foreign_symbol():
    functions = compile_source("""
        import std.io;
        fn main() -> i32 { let text = "hi"; io.println(len(text), text); return 0; }
        """)[1]["functions"]
    assert {"io", "ffi:write"} <= set(functions["std.io.put"]["effects"])
    assert {"io", "ffi:write"} <= set(functions["main"]["effects"])

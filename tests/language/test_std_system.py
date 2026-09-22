"""std.env and std.fs: what a whole program needs from the system, run for real.

The arguments and the environment are compared with what Python started the program with, and files with
what Python finds on disk afterwards. `cairn run PATH -- ARGS` is checked to hand the arguments on, on the
JSON path and on the terminal path alike.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.build import build
from cairn.projects.project import load_project
from emitted import refused

ROOT = Path(__file__).resolve().parents[2]


def built(tmp_path: Path, source: str) -> str:
    path = tmp_path / "program.cairn"
    path.write_text(source, encoding="utf-8")
    record = build(load_project(path), kind="exe", cxx="clang++", timeout=180)
    assert record["status"] == "native-built", record.get("stderr", "")[:4000]
    return record["artifact"]


ECHO = """
import std.core (Option, Result);
import std.env (Args);
import std.io (IoError);

// Each argument on its own line, then the variable CAIRN_PROBE or <unset>.
fn run() -> Result[usize, IoError] {
  let a = try env.args();
  for i in 0..a.count() {
    let lo = a.begin(i);
    let hi = a.end(i);
    if a.text.data[hi] != 0 { return Ok(1); }     // each argument keeps its NUL for C
    io.println(a.text.data[lo..hi]);
  }
  match try env.var("CAIRN_PROBE") {
    Some(v) => { io.println(v.data[0..v.len]); }
    None => { io.println("<unset>"); }
  }
  match try env.var("CAIRN_PROB") {                // a prefix of a name is not the name
    Some(v) => { return Ok(2); }
    None => {}
  }
  return Ok(0);
}

fn main() -> i32 {
  match run() {
    Ok(code) => { return i32(code); }
    Err(e) => { return 100; }
  }
}
"""


def test_arguments_and_environment_are_what_the_program_was_started_with(tmp_path):
    artifact = built(tmp_path, ECHO)
    arguments = ["plain", "two words", "", "=", "café"]
    env = {**os.environ, "CAIRN_PROBE": "a=b c"}
    done = subprocess.run([artifact, *arguments], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split("\n") == [artifact, *arguments, "a=b c", ""]
    env.pop("CAIRN_PROBE")
    quiet = subprocess.run([artifact], capture_output=True, text=True, env=env, timeout=60)
    assert quiet.stdout == f"{artifact}\n<unset>\n"


def cairn(*args: str, tty: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, "CAIRN_FORMAT": "human" if tty else "json"}
    return subprocess.run([sys.executable, str(ROOT / "bin/cairn"), *args], capture_output=True, text=True, env=env)


def test_cairn_run_hands_on_what_follows_the_double_dash(tmp_path):
    source = tmp_path / "echo.cairn"
    source.write_text(ECHO, encoding="utf-8")
    record = json.loads(cairn("run", str(source), "--", "-x", "two words").stdout)
    assert record["exit_code"] == 0 and record["stdout"].split("\n")[1:3] == ["-x", "two words"]
    shown = cairn("run", str(source), "--format", "human", "--", "--help", tty=True)
    assert shown.returncode == 0 and shown.stdout.split("\n")[1] == "--help"  # the program's, not cairn's
    refusal = cairn("check", str(source), "--", "x")
    assert refusal.returncode == 2 and "only `cairn run PATH -- ARGS`" in refusal.stderr


FILES = """
import std.core (Result);
import std.fs;
import std.io (IoError);
import std.vec (Vec);

fn run(n:usize, dir:ro<u8>[n]) -> Result[usize, IoError] {
  let mut path = vec.new[u8]();
  path.extend_from(dir);
  path.extend_from("/notes.txt");
  let mut moved = vec.new[u8]();
  moved.extend_from(dir);
  moved.extend_from("/kept.txt");
  let p = path.len;
  let q = moved.len;
  if fs.exists(path.data[0..p]) { return Ok(1); }
  let a = try fs.write(path.data[0..p], "one\\n");
  let b = try fs.append(path.data[0..p], "two\\n");
  let back = try fs.read(path.data[0..p]);
  if back.len != 8 || a + b != 8 || !fs.exists(path.data[0..p]) { return Ok(2); }
  let c = try fs.write(path.data[0..p], "three\\n");   // write replaces
  let renamed = try fs.rename(path.data[0..p], moved.data[0..q]);
  if fs.exists(path.data[0..p]) || !fs.exists(moved.data[0..q]) { return Ok(3); }
  match fs.read(path.data[0..p]) {
    Ok(gone) => { return Ok(4); }
    Err(e) => { if e.code != 2 { return Ok(5); } }  // ENOENT, as a value
  }
  match fs.read("bad\\x00path") {
    Ok(bad) => { return Ok(6); }
    Err(e) => { if e.code != 22 { return Ok(7); } }  // EINVAL: a NUL inside a path
  }
  let proc = try fs.read("/proc/self/stat");       // its size says 0; it still reads whole
  if proc.len == 0 { return Ok(8); }
  return Ok(0);
}

fn main() -> i32 {
  match run(len("DIR"), "DIR") {
    Ok(code) => { return i32(code); }
    Err(e) => { return 100; }
  }
}
"""


def test_files_by_path_leave_what_python_finds(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    artifact = built(tmp_path, FILES.replace("DIR", str(work)))
    done = subprocess.run([artifact], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in work.iterdir()) == ["kept.txt"]
    assert (work / "kept.txt").read_text() == "three\n"


def test_a_path_too_long_for_the_kernel_is_an_error_value(tmp_path):
    source = FILES.split("fn run(")[0] + (
        "fn main() -> i32 {\n  buffer long:u8[5000] = zeroed;\n  for i in 0..5000 { long[i] = 'a'; }\n"
        "  match fs.read(long) { Ok(b) => { return 1; } Err(e) => { if e.code != 36 { return 2; } } }\n  return 0;\n}\n"
    )
    done = subprocess.run([built(tmp_path, source)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_rows_name_the_system_calls():
    rows = compile_source('import std.fs;\nfn seen() -> bool = fs.exists("/");\n')[1]["functions"]
    assert {"ffi:access", "io", "stack_storage"} <= set(rows["seen"]["effects"])


def test_arguments_are_read_not_written():
    refused("E-WRITE-LEASE", "import std.env (Args);\nfn f(a:ro<Args>) { a.text.data[0] = 0; }")


@pytest.mark.parametrize("module", ["fmt", "fs", "env"])
def test_each_new_module_is_documented(module):
    assert f"## std.{module}" in (ROOT / "docs/library.md").read_text()

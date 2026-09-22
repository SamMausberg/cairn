"""std.fs: files by path, run for real.

Files are compared with what Python finds on disk afterwards.
"""

import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.build import build
from cairn.projects.project import load_project

ROOT = Path(__file__).resolve().parents[2]


def built(tmp_path: Path, source: str) -> str:
    path = tmp_path / "program.cairn"
    path.write_text(source, encoding="utf-8")
    record = build(load_project(path), kind="exe", cxx="clang++", timeout=180)
    assert record["status"] == "native-built", record.get("stderr", "")[:4000]
    return record["artifact"]


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


@pytest.mark.parametrize("module", ["fmt", "fs"])
def test_each_new_module_is_documented(module):
    assert f"## std.{module}" in (ROOT / "docs/library.md").read_text()

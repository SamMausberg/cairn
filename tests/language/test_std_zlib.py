"""std.zlib and the system libraries a build links: Python's zlib is the independent oracle for every stream and
checksum, and a library is linked only through a row of the toolchain's closed table."""

import json
import os
import subprocess
import zlib
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import compile_source
from cairn.projects.build import build
from cairn.projects.project import ProjectError, load_project
from emitted import SANITIZED, contract, native

INPUTS = [b"", b"a", b"hello " * 40, bytes(range(256)) * 3]


def literal(data: bytes) -> str:
    return '"' + "".join(f"\\x{b:02x}" for b in data) + '"'


def program(level: int) -> str:
    """Each input compressed at `level` into z<k>.out, and its CRC-32 and Adler-32 held to Python's."""
    cases = []
    for k, data in enumerate(INPUTS):
        cases.append(f"""  {{
    let data = {literal(data)};
    match zlib.compress(data, {level}) {{
      Ok(z) => {{
        match fs.write("z{k}.out", z.data[0..z.len]) {{
          Ok(n) => {{}}
          Err(e) => return 20;
        }}
      }}
      Err(e) => return 10;
    }}
    if zlib.crc32(0, data) != {zlib.crc32(data)} {{ return {30 + k}; }}
    if zlib.adler32(1, data) != {zlib.adler32(data)} {{ return {40 + k}; }}
  }}""")
    return "import std.fs;\nimport std.zlib;\n\nfn main() -> i32 {\n" + "\n".join(cases) + "\n  return 0;\n}\n"


@pytest.mark.parametrize("level", [0, 1, 9, -1])
@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_stream_inflates_to_its_input_and_every_checksum_is_zlib_s(tmp_path, level, cxx, monkeypatch):
    monkeypatch.chdir(tmp_path)
    native(tmp_path, program(level), cxx)
    for k, data in enumerate(INPUTS):
        assert zlib.decompress((tmp_path / f"z{k}.out").read_bytes()) == data


def test_the_bindings_are_address_and_leak_clean(tmp_path):
    source = program(9).replace('fs.write("z', 'fs.write("' + str(tmp_path) + "/z")
    generated = compile_source(source, roots=("main",))[0]
    env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1"}
    done = contract(tmp_path, generated, "clang++", *SANITIZED[1:], "-lz", env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    assert zlib.decompress((tmp_path / "z3.out").read_bytes()) == INPUTS[3]


def test_a_level_outside_zlib_s_range_is_an_error_value(tmp_path):
    source = """import std.zlib;
fn main() -> i32 {
  match zlib.compress("x", 10) {
    Ok(z) => return 1;
    Err(e) => { if e.code != -2 { return 2; } }
  }
  return 0;
}
"""
    native(tmp_path, source)


ABC = zlib.crc32(b"abc")
OWN = f"""extern "crc32_z" fn crc(crc:u64, data:ro<u8>[n], n:usize) -> u64 effects();
fn main() -> i32 {{
  let mut c:u64 = 0;
  unsafe {{ c = crc(0, "abc", 3); }}
  if c != {ABC} {{ return 1; }}
  return 0;
}}
"""


def project(tmp_path: Path, build_table: str) -> Path:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src/main.cairn").write_text(OWN)
    (tmp_path / "cairn.toml").write_text(
        f'[project]\nname = "own"\nsources = ["src/main.cairn"]\n\n[build]\nkind = "exe"\n{build_table}'
    )
    return tmp_path


def test_a_manifest_names_the_library_its_own_externs_call(tmp_path):
    root = project(tmp_path, 'libraries = ["z"]\n')
    record = build(load_project(root), cxx="clang++")
    assert record["status"] == "native-built" and record["libraries"] == ["z"] and record["command"][-1] == "-lz"
    assert record["project"]["libraries"] == ["z"]
    assert subprocess.run([record["artifact"]], timeout=10).returncode == 0


def test_without_the_row_the_link_fails_and_names_the_symbol(tmp_path):
    record = build(load_project(project(tmp_path, "")), cxx="clang++")
    assert record["status"] == "native-build-failed" and "crc32_z" in record["stderr"]


@pytest.mark.parametrize(
    "table,why",
    [
        ('libraries = ["curl"]\n', "known"),
        ('libraries = ["-lz"]\n', "known"),
        ('libraries = ["z", "z"]\n', "once"),
        ('libraries = "z"\n', "list"),
        ('libraries = ["z"]\ntarget = "aarch64-virt"\n', "freestanding"),
    ],
)
def test_a_library_is_a_row_of_the_table_and_never_a_flag(tmp_path, table, why):
    with pytest.raises(ProjectError, match=why):
        load_project(project(tmp_path, table))


def test_a_run_that_prints_bytes_that_are_not_utf8_still_gives_its_record(tmp_path, capsys):
    path = tmp_path / "bytes.cairn"
    path.write_text('import std.io;\nfn main() -> i32 { io.print("\\xda\\x78\\x01"); return 0; }\n')
    assert main(["run", str(path), "--format", "json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["exit_code"] == 0 and record["stdout"] == "\\xdax\x01"  # 0xda escaped, never a decoding error


def test_importing_std_zlib_links_it_without_a_manifest(tmp_path, capsys):
    path = tmp_path / "one.cairn"
    path.write_text(
        f'import std.zlib;\nfn main() -> i32 {{ if zlib.crc32(0, "abc") != {ABC} {{ return 1; }} return 0; }}\n'
    )
    assert main(["build", str(path), "--kind", "exe", "--format", "json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["libraries"] == ["z"] and subprocess.run([record["artifact"]], timeout=10).returncode == 0

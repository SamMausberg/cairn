"""A C or C++ program calls a CAIRN library through the header `cairn build --header` writes.

The header's layouts are computed once and checked by both sides: `_Static_assert` against the C or C++
compiler that includes it, and `static_assert` in the library's own C++ against the structs it emits, so a
wrong layout fails to build instead of corrupting a call. A foreign caller still meets the checked entry.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.header import header
from cairn.projects.build import build
from cairn.projects.project import ProjectError, load_project
from emitted import SANITIZED, WARNINGS

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/interop"
COMPILERS = [pytest.param(cxx, marks=pytest.mark.skipif(not shutil.which(cxx), reason=f"{cxx} unavailable"))
             for cxx in ("clang++", "g++")]  # fmt: skip
C_OF = {"clang++": "clang", "g++": "gcc"}


def library(tmp_path: Path, cxx: str, root: Path = EXAMPLE) -> Path:
    record = build(load_project(root), kind="library", cxx=cxx, header=True, output=tmp_path / "out", timeout=180)
    assert record["status"] == "native-built", record.get("stderr", "")[:4000]
    return Path(record["header"]).parent


def compiled(*command, timeout=180):
    done = subprocess.run([str(c) for c in command], capture_output=True, text=True, timeout=timeout)
    assert done.returncode == 0, done.stderr[:4000]


@pytest.mark.parametrize("cxx", COMPILERS)
def test_a_cpp_program_built_on_its_own_calls_the_library_through_its_header(tmp_path, cxx):
    built = library(tmp_path, cxx)
    host = tmp_path / "host"
    compiled(cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", EXAMPLE / "host/main.cpp", f"-I{built}", f"-L{built}",
             "-lstats", f"-Wl,-rpath,{built}", "-o", host)  # fmt: skip
    done = subprocess.run([host], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines() == [
        "count 6, min 4, max 42, total 108",
        "windows of 3: 0 0 27 39 54 81",
        "trend up by 38, spread 38",
        "scaled by 3/2: 6 .. 63",
    ]


@pytest.mark.parametrize("cxx", COMPILERS)
def test_the_same_program_is_clean_under_address_and_undefined_behaviour_sanitizers(tmp_path, cxx):
    """The library's C++ and the host in one sanitized executable: every call, every view, every layout."""
    built = library(tmp_path, cxx)
    host = tmp_path / "host"
    compiled(cxx, *SANITIZED, *WARNINGS, f"-I{built}", built / "program.cpp", EXAMPLE / "host/main.cpp", "-o", host)
    done = subprocess.run([host], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "runtime error" not in done.stderr, done.stderr[:4000]


MISUSE = r"""
#include <cstdint>
#include <cstring>
#include "stats.h"
int main(int argc, char** argv) {
  alignas(8) std::int64_t xs[9] = {1, 2, 3, 4, 5, 6, 7, 8, 9};
  const char* which = argc > 1 ? argv[1] : "";
  if (!std::strcmp(which, "overlap")) cf_window_sums(4, xs, 2, xs + 2);   // out overlaps what it reads
  if (!std::strcmp(which, "misaligned"))
    cf_summarize(4, reinterpret_cast<const std::int64_t*>(reinterpret_cast<char*>(xs) + 1));
  if (!std::strcmp(which, "null")) cf_summarize(4, nullptr);
  if (!std::strcmp(which, "zero")) cf_scale(4, xs, 1, 0);                  // a guard of the body
  if (!std::strcmp(which, "apart")) cf_window_sums(4, xs, 2, xs + 4);     // touching, not overlapping
  if (!std::strcmp(which, "empty")) cf_summarize(0, nullptr);              // an empty view may be null
  return 0;
}
"""


@pytest.mark.parametrize("cxx", COMPILERS)
def test_a_foreign_caller_still_meets_the_checked_entry(tmp_path, cxx):
    built = library(tmp_path, cxx)
    (tmp_path / "misuse.cpp").write_text(MISUSE)
    host = tmp_path / "misuse"
    compiled(cxx, "-std=c++17", "-Wall", "-Werror", tmp_path / "misuse.cpp", f"-I{built}", f"-L{built}", "-lstats",
             f"-Wl,-rpath,{built}", "-o", host)  # fmt: skip
    status = {case: subprocess.run([host, case], capture_output=True, timeout=30).returncode
              for case in ("overlap", "misaligned", "null", "zero", "apart", "empty")}  # fmt: skip
    assert status == {"overlap": -6, "misaligned": -6, "null": -6, "zero": -6, "apart": 0, "empty": 0}


def test_a_call_inside_cairn_reaches_the_lean_body_and_a_foreign_one_the_checked_entry(tmp_path):
    cpp = (library(tmp_path, "clang++") / "program.cpp").read_text()
    spread = cpp[cpp.index("std::int64_t ci_spread(std::size_t v_n, const std::int64_t* v_xs) noexcept {") :]
    spread = spread.split("\n}\n", 1)[0]
    assert "ci_summarize(" in spread and "cf_summarize(" not in spread
    entry = cpp[cpp.index('extern "C" std::int64_t cf_spread(std::size_t v_n, const std::int64_t* v_xs) noexcept {') :]
    entry = entry.split("\n}\n", 1)[0]
    assert "cr::view(v_xs" in entry and "return ci_spread(" in entry


def test_a_library_whose_header_would_lie_about_a_layout_does_not_build(tmp_path):
    built = library(tmp_path, "clang++")
    cpp = (built / "program.cpp").read_text()
    assert "static_assert(sizeof(ct_Summary) == 32 && alignof(ct_Summary) == 8" in cpp
    (built / "program.cpp").write_text(cpp.replace("sizeof(ct_Summary) == 32", "sizeof(ct_Summary) == 24"))
    done = subprocess.run(["clang++", "-std=c++20", "-fsyntax-only", f"-I{built}", built / "program.cpp"],
                          capture_output=True, text=True, timeout=120)  # fmt: skip
    assert done.returncode != 0 and "static assertion failed" in done.stderr


LAYOUTS = """
// Nested, packed, aligned, arrays and storage floats: every rule the header lays out by hand.
struct Inner { a:u8; b:u64; }
struct Outer { flag:bool; inner:Inner; tail:u16; }
struct Wire packed { kind:u8; size:u32; crc:u16; }
struct Slot align(64) { hits:u64; }
struct Cell { tag:Array[u8, 3]; value:f32; half:f16; small:f8e4m3; }
enum Mode { Off; On; }
enum Event { Nothing; Moved(Inner); Pressed(u8); Scaled(f64); }
struct Record { mode:Mode; event:Event; cell:Cell; }

fn outer(o:Outer) -> u64 = o.inner.b + u64(o.tail);
fn wire(w:Wire) -> u32 = w.size;
fn slot(s:rw<Slot>) { s.hits += 1; }
fn cell(c:ro<Cell>) -> u8 = c.tag[2];
fn record(n:usize, rs:ro<Record>[n]) -> u64 {
  let mut t:u64 = 0;
  for r in rs { if r.mode == On { t += 1; } }
  return t;
}
fn event(e:Event) -> u8 {
  match e {
    Nothing => return 0;
    Moved(i) => return i.a;
    Pressed(k) => return k;
    Scaled(x) => return 3;
  }
}
"""


@pytest.mark.parametrize("cxx", COMPILERS)
def test_every_layout_the_header_states_is_the_one_both_compilers_make(tmp_path, cxx):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/layouts.cairn").write_text(LAYOUTS)
    (tmp_path / "cairn.toml").write_text('[project]\nname = "layouts"\nsources = ["src/layouts.cairn"]\n')
    built = library(tmp_path, cxx, tmp_path)  # the library's own C++ carries the checks, so building proves them
    text = (built / "layouts.h").read_text()
    assert "struct __attribute__((packed)) ct_Wire" in text and "__attribute__((aligned(64))) ct_Slot" in text
    assert "uint8_t tag[3];" in text and "cairn_f16 half;" in text and "cairn_f8e4m3 small;" in text
    (tmp_path / "c.c").write_text('#include "layouts.h"\nint main(void) { return 0; }\n')
    (tmp_path / "c.cpp").write_text('#include "layouts.h"\nint main() { return 0; }\n')
    c = C_OF[cxx]
    compiled(
        c, "-std=c11", "-pedantic", "-Wall", "-Wextra", "-Werror", f"-I{built}", tmp_path / "c.c", "-o", tmp_path / "c"
    )
    compiled(cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", f"-I{built}", tmp_path / "c.cpp", "-o", tmp_path / "cc")


WITHHELD = """
module m;
pub linear struct Token { id:u64; }
pub trait Shape { fn area(s:ro<Self>) -> u64; }
pub struct Square { side:u64; }
impl Shape for Square { fn area(s:ro<Square>) -> u64 = s.side * s.side; }
pub fn make(n:usize) -> Buf[u8] = Buf[u8](n);
pub fn apply(f:fn(u64) -> u64, x:u64) -> u64 = f(x);
pub fn measure(s:ro<dyn Shape>) -> u64 = s.area();
pub fn pair(a:Array[u64, 2]) -> u64 = a[0] + a[1];
pub fn spend(t:Token) -> u64 { let Token(id) = t; return id; }
pub fn pick[T:copy](x:T) -> T = x;
fn hidden(x:u64) -> u64 = x;
pub fn kept(x:u64) -> u64 = hidden(x) + 1;
test kept_adds_one { assert(kept(1) == 2); }
"""


def test_what_cannot_cross_the_boundary_is_listed_with_its_reason_and_never_declared():
    text = header(WITHHELD, "m")[0]
    declared = set(re.findall(r"\bcf_(\w+)\(", text))
    assert declared == {"m_kept"}, declared
    withheld = text[text.index("Not declared") :]
    for name, why in [
        ("m.make", "Buf[u8] is an owner or a linear value"),
        ("m.apply", "a function value"),
        ("m.measure", "a dyn reference"),
        ("m.pair", "an Array passed by value"),
        ("m.spend", "m.Token is an owner or a linear value"),
    ]:
        assert f"{name}: {why}" in withheld, withheld
    assert "hidden" not in text and "pick" not in text and "kept_adds_one" not in text and "area" not in declared


def test_emit_prints_the_header_build_writes_and_only_a_hosted_library_has_one(tmp_path, capsys):
    built = library(tmp_path, "clang++")
    assert main(["emit", str(EXAMPLE), "--header"]) == 0
    assert capsys.readouterr().out == (built / "stats.h").read_text()
    with pytest.raises(ProjectError, match="--kind library"):
        build(load_project(EXAMPLE), kind="exe", header=True, output=tmp_path / "exe")
    with pytest.raises(ProjectError, match="--kind library"):
        build(load_project(EXAMPLE), kind="library", header=True, incremental=True, output=tmp_path / "inc")

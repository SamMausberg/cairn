"""A build whose compiler refuses the C++ CAIRN generated names the `.cairn` line that C++ was lowered from, and says
the fault is the CAIRN compiler's, since a program that checks should always build. The line is read through the
#line directives of the same program generated for a debugger, which for every example differs from the program built
by those lines alone. Each case here breaks the lowering of one statement on purpose, under clang++, g++ and nvcc, which
only compiles; a refusal elsewhere, in a vendored source or at the link, or a standard header the compiler's
installation lacks, claims no defect (tests/projects/test_foreign.py, tests/language/test_std_zlib.py).
"""

import shutil
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import compile_source
from cairn.compiler.lower.codegen import Emitter
from cairn.projects.build import DIRECTIVE, build, emitted, refusal
from cairn.projects.project import load_project
from emitted import NVCC_HOST

ROOT = Path(__file__).resolve().parents[2]

HOST = """import std.core (Option);

fn first(x:Option[u64]) -> u64 {
  match x {
    Some(v) => return v;
    None => return 0;
  }
}

fn main() -> i32 {
  let doomed:u64 = first(Option.Some(u64(3)));
  return i32(doomed) - 3;
}
"""

DEVICE = """fn fill(n:usize, a:rw<u64>[n]@device) {
  parallel i in n { a[i] = 1; }
}

fn main() -> i32 {
  let doomed:u64 = 3;
  buffer d:u64[4]@device = zeroed;
  fill(d);
  return i32(doomed) - 3;
}
"""


@pytest.fixture
def broken(monkeypatch):
    """The lowering of `let doomed` writes a line no C++ compiler takes, as a defect in the emitter would."""
    lowered = Emitter.s_let

    def s_let(self, s, es):
        if s.name == "doomed":
            self.put("this is not C++;")
        lowered(self, s, es)

    monkeypatch.setattr(Emitter, "s_let", s_let)


def refused(tmp_path, source: str, **options) -> dict:
    path = tmp_path / "doomed.cairn"
    path.write_text(source, encoding="utf-8")
    record = build(load_project(path), kind="exe", **options)
    assert record["status"] == "native-build-failed", record.get("stderr", "")[-2000:]
    return record


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("debug", [False, True])
def test_a_refused_build_names_the_cairn_line_and_says_the_compiler_is_at_fault(tmp_path, broken, cxx, debug):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    record = refused(tmp_path, HOST, cxx=cxx, debug=debug)  # a debug build's own directives name the line
    assert record["compiler_defect"] and record["refused_at"] == "doomed.cairn:11"
    said = record["message"]
    assert said.startswith(f"{cxx} refused the C++ generated from doomed.cairn:11.")
    assert "fault of the CAIRN compiler, never of the program: report it with the program" in said
    assert "program.cpp:" in said or "doomed.cairn:11:" in said  # the compiler's own first error line


def test_nvcc_s_refusal_names_the_cairn_line(tmp_path, broken):
    """nvcc writes `file(line)`; it compiles, and the build stops there: nothing runs on a device."""
    if not shutil.which("nvcc") or not shutil.which(NVCC_HOST):
        pytest.skip(f"needs nvcc and {NVCC_HOST}")
    record = refused(tmp_path, DEVICE, cxx=NVCC_HOST, device_target="sm_120")
    assert record["refused_at"] == "doomed.cairn:6" and record["message"].startswith("nvcc refused the C++ generated")
    assert "program.cpp(" in record["message"]


def test_a_terminal_reads_the_line_and_the_fault(tmp_path, broken, capsys):
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    path = tmp_path / "doomed.cairn"
    path.write_text(HOST, encoding="utf-8")
    assert main(["build", str(path), "--kind", "exe", "--out", str(tmp_path), "--format", "human"]) == 2
    shown = capsys.readouterr().out
    assert "native-build-failed: clang++ refused the C++ generated from doomed.cairn:11." in shown


@pytest.mark.parametrize(
    ("compiler", "said"),
    [
        ("clang++", "cairn_runtime.hpp:3:10: fatal error: '{}' file not found"),
        ("g++", "cairn_runtime.hpp:3:10: fatal error: {}: No such file or directory"),
        ("nvcc", 'cairn_runtime.hpp(3): catastrophic error: cannot open source file "{}"'),
    ],
)
def test_a_header_the_installation_lacks_claims_no_defect(tmp_path, compiler, said):
    """clang++ without libstdc++'s headers refuses every program at the runtime's first include; that is the
    installation's fault, not the generated C++'s. A runtime header missing beside program.cpp is the build's own."""
    path = tmp_path / "doomed.cairn"
    path.write_text("fn main() -> i32 { return 0; }\n", encoding="utf-8")
    project = load_project(path)

    def refused(header: str) -> dict:
        stderr = f"In file included from {tmp_path}/program.cpp:2:\n{tmp_path}/{said.format(header)}\n1 error\n"
        return refusal(project, stderr, lambda: pytest.fail("no line of program.cpp is named"), compiler, tmp_path)

    assert refused("algorithm") == {}
    assert refused("cairn_coop.hpp")["compiler_defect"]


@pytest.mark.parametrize(
    "manifest",
    sorted(p for p in (ROOT / "examples").rglob("*.toml") if "[project]" in p.read_text(encoding="utf-8")),
    ids=lambda p: str(p.relative_to(ROOT / "examples")),
)
def test_a_debug_emission_differs_only_by_its_line_directives(manifest):
    """What the line map rests on: a failed build's program.cpp is read line for line against the same program
    generated with #line directives, so that program must be this one with those lines added and nothing else."""
    project = load_project(manifest)
    plain, debug = (emitted(project, kind=project.kind, debug=d).generated.splitlines() for d in (False, True))
    assert any(DIRECTIVE.match(line) for line in debug)
    assert [line for line in debug if not DIRECTIVE.match(line)] == plain


def test_a_debug_build_names_the_line_of_each_match_arm():
    """So the binder of an arm is placed at its arm, not at the last statement of the arm before it."""
    lines = compile_source(HOST, "doomed.cairn")[0].splitlines()
    binder = next(i for i, line in enumerate(lines) if "v_v = " in line)
    assert lines[binder - 1].strip() == '#line 5 "doomed.cairn"'

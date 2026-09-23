"""What a person at a terminal reads, and that piped output stays the JSON record it always was."""

import json

import pytest

from cairn.cli import main
from cairn.compiler import modules
from cairn.editor.terminal import ended

RACY = """fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }

fn racy(n:usize, data:rw<u64>[n]) {
  let left = spawn fill(n, data, 0);
  data[0] = 7;
  wait(left);
}
"""


def test_a_refusal_shows_the_code_the_place_and_the_line(tmp_path, capsys):
    source = tmp_path / "racy.cairn"
    source.write_text(RACY)
    assert main(["check", str(source), "--format", "human"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "error[E-LEASED]: data is lent to left until wait(left)."
    assert err[1].endswith("racy.cairn:5:3")
    assert err[3] == "5 |   data[0] = 7;"
    assert err[4] == "  |   ^^^^"  # the token the column points at


def test_a_misspelled_name_is_answered_with_the_nearest_one(tmp_path, capsys):
    source = tmp_path / "typo.cairn"
    source.write_text("fn main() -> i32 { let total = 3; return i32(totl); }\n")
    assert main(["check", str(source), "--format", "human"]) == 1
    assert "= help: did you mean total?" in capsys.readouterr().err


def test_a_project_error_names_its_own_file_and_line(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/lib.cairn").write_text("fn one() -> u64 = 1;\n")
    (tmp_path / "src/main.cairn").write_text("fn main() -> i32 {\n  return i32(two());\n}\n")
    (tmp_path / "cairn.toml").write_text(
        '[project]\nname = "p"\nsources = ["src/lib.cairn", "src/main.cairn"]\n[build]\nkind = "exe"\narch = "baseline"\n'
    )
    assert main(["check", str(tmp_path), "--format", "human"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[1].endswith("src/main.cairn:2:14") and err[3] == "2 |   return i32(two());"


def test_piped_output_is_the_json_record(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CAIRN_FORMAT", raising=False)
    source = tmp_path / "racy.cairn"
    source.write_text(RACY)
    assert main(["check", str(source)]) == 1
    record = json.loads(capsys.readouterr().out)
    assert record["code"] == "E-LEASED" and record["line"] == 5 and record["protocol"] == "cairn.diagnostic/2"
    monkeypatch.setenv("CAIRN_FORMAT", "human")
    assert main(["check", str(source), "--format", "json"]) == 1  # the flag outranks the environment
    assert json.loads(capsys.readouterr().out)["code"] == "E-LEASED"


def test_an_accepted_program_is_one_line(tmp_path, capsys):
    source = tmp_path / "ok.cairn"
    source.write_text("fn main() -> i32 = 0;\n")
    assert main(["check", str(source), "--format", "human"]) == 0
    assert capsys.readouterr().out == "typed: 1 function\n"
    source.write_text('import std.text;\nfn main() -> i32 { if text.equal("a", "a") { return 0; } return 1; }\n')
    assert main(["check", str(source), "--format", "human"]) == 0
    said = capsys.readouterr().out
    assert said.startswith("typed: 1 function, and ") and said.endswith(" from the library\n"), said


@pytest.mark.parametrize(
    "code,said",
    [(0, "exited with status 0"), (3, "exited with status 3"), (-6, "was stopped by SIGABRT: a guard failed")],
)
def test_how_a_child_ended(code, said):
    assert ended(code) == said


def test_a_refusal_inside_a_library_module_names_that_module_s_file(tmp_path, capsys, monkeypatch):
    """A library body is parsed from its own file, so its line is that file's, never a line of the project."""
    std = tmp_path / "std"
    std.mkdir()
    (std / "broken.cairn").write_text("module std.broken;\npub fn f() -> u64 {\n  return true;\n}\n")
    monkeypatch.setattr(modules, "STD", std)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.cairn").write_text("fn one() -> u64 = 1;\nfn two() -> u64 = 2;\nfn three() -> u64 = 3;\n")
    (tmp_path / "src/main.cairn").write_text("import std.broken;\nfn main() -> i32 = i32(broken.f());\n")
    (tmp_path / "cairn.toml").write_text(
        '[project]\nname = "p"\nsources = ["src/a.cairn", "src/main.cairn"]\n[build]\nkind = "exe"\narch = "baseline"\n'
    )
    assert main(["check", str(tmp_path), "--format", "json"]) == 1
    record = json.loads(capsys.readouterr().out)
    assert record["module"] == "std.broken" and record["file"] == str(std / "broken.cairn") and record["line"] == 3
    assert main(["check", str(tmp_path), "--format", "human"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[1].endswith("broken.cairn:3:10") and err[3] == "3 |   return true;"

"""A check reports every independent refusal at once: the first exactly as a check that stops meets it, then each
further one in source order, and never one that may only follow from another."""

import json
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import Diagnostic, compile_program
from cairn.projects.project import load_project
from checks import refusal_differential as harness
from checks.emission_identity import programs

ROOT = Path(__file__).resolve().parents[2]
THREE = """fn first(x:u64) -> u64 {
  let y:u32 = x;
  return y;
}

fn second(a:u64) -> u64 {
  return a + missing;
}

fn third() -> bool {
  return 1;
}
"""

GEO = """module geo;

pub struct Pair { a:u64; b:u64; }

pub fn area(p:ro<Pair>) -> u64 = p.a * p.c;

pub fn half(x:u64) -> u64 = x / 2;
"""

MAIN = """module app;
import geo;

fn twice(x:u64) -> u64 {
  let y:bool = x;
  return x;
}

fn main() -> i32 {
  if geo.half(4) != 2 { return 1; }
  return 0;
}
"""

NOISY = """fn noisy(x:u64) -> u64 {
  println("noisy");
  return x + missing;
}
"""


def every(source: str) -> dict:
    """The record of a check that reports every refusal; it must not have ended early on a fault."""
    with pytest.raises(Diagnostic) as error:
        compile_program(source, every=True)
    assert error.value.abandoned is None
    return error.value.data


def alone(source: str) -> dict:
    with pytest.raises(Diagnostic) as error:
        compile_program(source)
    return error.value.data


def where(d: dict) -> tuple:
    return d["code"], d["line"], d["column"]


def project(root, **files):
    (root / "src").mkdir()
    listed = ", ".join(f'"src/{name}"' for name in files)
    (root / "cairn.toml").write_text(f'[project]\nname = "p"\nsources = [{listed}]\n[build]\nkind = "exe"\n')
    for name, text in files.items():
        (root / "src" / name).write_text(text)
    return root


def test_three_mistakes_in_three_functions_are_reported_by_one_check():
    record = every(THREE)
    first = {k: v for k, v in record.items() if k != "further"}
    assert first == alone(THREE)  # what a check that stops says, exactly
    assert where(record) == ("E-TYPE-MISMATCH", 2, 15)
    assert [where(d) for d in record["further"]] == [("E-UNBOUND", 7, 14), ("E-TYPE-MISMATCH", 11, 10)]
    assert all(d["protocol"] == "cairn.diagnostic/2" and d["status"] == "rejected" for d in record["further"])
    assert "not_judged" not in record and "further_omitted" not in record


def test_a_project_reports_each_refusal_at_its_own_file_and_line(tmp_path, capsys):
    root = project(tmp_path, **{"geo.cairn": GEO, "main.cairn": MAIN})
    assert main(["check", str(root), "--format", "json"]) == 1
    record = json.loads(capsys.readouterr().out)
    assert (record["code"], record["file"], record["line"]) == ("E-FIELD", "src/geo.cairn", 5)
    assert [(d["code"], d["file"], d["line"], d["column"]) for d in record["further"]] == [
        ("E-TYPE-MISMATCH", "src/main.cairn", 5, 16)
    ]
    assert main(["check", str(root), "--format", "human"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "error[E-FIELD]: Unknown field c." and err[1].endswith("src/geo.cairn:5:40")
    later = err.index("error[E-TYPE-MISMATCH]: Expected bool, got u64.")
    assert err[later - 1] == "" and err[later + 1].endswith("src/main.cairn:5:16")
    assert err[later + 3] == "5 |   let y:bool = x;" and err[-1] == "error: 2 refusals"


def test_one_refusal_alone_reads_as_it_always_has(tmp_path, capsys):
    source = tmp_path / "one.cairn"
    source.write_text("fn f() -> bool = 1;\n")
    assert main(["check", str(source), "--format", "json"]) == 1
    assert set(json.loads(capsys.readouterr().out)) == {"protocol", "status", "code", "message", "line", "column",
                                                        "trust", "expected_type", "actual_type", "file", "card",
                                                        "repair_hint"}  # fmt: skip
    assert main(["check", str(source), "--format", "human"]) == 1
    assert not capsys.readouterr().err.splitlines()[-1].startswith("error: ")


def test_a_caller_whose_ceiling_needs_a_refused_row_is_not_judged():
    quiet = NOISY + "fn quiet(x:u64) -> u64 pure { return noisy(x); }\n"
    record = every(quiet)
    assert where(record) == ("E-UNBOUND", 3, 14) and "further" not in record and record["not_judged"] == 1
    fixed = quiet.replace("x + missing", "x + 1")
    assert alone(fixed)["code"] == "E-EFFECT-CEILING"  # what the ceiling says hangs on the body refused above


def test_a_lane_through_a_refused_callee_is_not_judged():
    lanes = NOISY + "fn lanes(n:usize, out:rw<u64>[n]) {\n  parallel i in n { out[i] = noisy(1); }\n}\n"
    record = every(lanes)
    assert where(record) == ("E-UNBOUND", 3, 14) and "further" not in record and record["not_judged"] == 1
    assert alone(lanes.replace("x + missing", "x + 1"))["code"] == "E-PARALLEL-CALL"


def test_a_refused_type_ends_the_check_after_every_type():
    source = (
        "struct P { x:Foo; }\nstruct Q { p:P; }\nstruct R { y:Bar; }\nfn use(p:P) -> u64 = 1;\nfn f() -> u64 = true;\n"
    )
    record = every(source)
    assert record["message"] == "Unknown type Foo." and [d["message"] for d in record["further"]] == [
        "Unknown type Bar."
    ]  # Q holds P and is not refused again; f's own mistake waits, since nothing says what names a refused type
    assert record["not_judged"] == 2


def test_a_refused_signature_ends_the_check_after_every_signature():
    source = "fn a(x:Foo) -> u64 = 1;\nfn b(y:u64) -> Bar = y;\nfn c() -> u64 = a(1);\nfn d() -> u64 = true;\n"
    record = every(source)
    assert [where(d) for d in [record, *record["further"]]] == [("E-TYPE", 1, 1), ("E-TYPE", 2, 1)]
    assert record["not_judged"] == 2


def test_a_template_refused_where_two_callers_use_it_is_reported_once():
    source = "fn pick[T](a:T, b:T) -> T { return c; }\nfn one() -> u64 = pick(1, 2);\n"
    source += "fn two() -> u64 = pick(3, 4);\nfn three() -> bool = 7;\n"
    record = every(source)
    assert [where(d) for d in [record, *record["further"]]] == [("E-UNBOUND", 1, 36), ("E-TYPE-MISMATCH", 4, 22)]
    assert record["not_judged"] == 1  # two met the same refusal in pick and was judged no further


def test_a_ceiling_refused_only_through_a_refused_callee_is_not_repeated():
    source = 'fn helper() -> u64 pure { println("x"); return 1; }\nfn outer() -> u64 pure { return helper(); }\n'
    source += 'fn apart() -> u64 pure { println("y"); return 2; }\n'
    record = every(source)
    assert [(d["code"], d["line"]) for d in [record, *record["further"]]] == [
        ("E-EFFECT-CEILING", 1),
        ("E-EFFECT-CEILING", 3),
    ]
    assert record["not_judged"] == 1


def test_operand_order_is_judged_wherever_no_refused_body_is_reached():
    source = "fn fill(n:usize, out:rw<u64>[n]) -> u64 { out[0] = 1; return 1; }\n"
    source += "fn one(n:usize, a:rw<u64>[n]) -> u64 { return fill(n, a) + 1; }\n"
    source += "fn two(n:usize, b:rw<u64>[n]) -> u64 { return 2 + fill(n, b); }\nfn three() -> bool = 1;\n"
    record = every(source)
    assert [where(d) for d in [record, *record["further"]]] == [
        ("E-TYPE-MISMATCH", 4, 22),
        ("E-EFFECT-ORDER", 2, 47),
        ("E-EFFECT-ORDER", 3, 51),
    ]


def test_the_first_refusal_stays_first_even_when_it_reaches_a_later_one():
    source = 'fn outer() -> u64 pure { return helper(); }\nfn helper() -> u64 pure { println("x"); return 1; }\n'
    record = every(source)
    assert where(record) == where(alone(source)) == ("E-EFFECT-CEILING", 1, 1)
    assert [where(d) for d in record["further"]] == [("E-EFFECT-CEILING", 2, 1)]


def test_a_cycle_of_constants_is_one_refusal():
    record = every("const HEAD:u32 = TAIL + 1;\nconst TAIL:u32 = HEAD;\n")
    assert record["code"] == "E-CONST" and "further" not in record


def test_a_parse_error_is_reported_alone():
    record = every("fn a( -> u64 = 1;\nfn b() -> u64 = ;\n")
    assert record == alone("fn a( -> u64 = 1;\nfn b() -> u64 = ;\n")


def test_the_record_lists_twenty_further_refusals_and_counts_the_rest():
    record = every("".join(f"fn g{k}() -> bool = {k};\n" for k in range(25)))
    assert [d["line"] for d in record["further"]] == list(range(2, 22)) and record["further_omitted"] == 4


def test_an_accepted_program_is_checked_the_same_way_either_way():
    source = load_project(ROOT / "examples/apps/analytics").source
    assert compile_program(source, every=True)[2] == compile_program(source)[2]


@pytest.mark.parametrize("chunk", range(8))
def test_every_refused_program_the_repository_holds_keeps_its_first_refusal(chunk):
    """The differential of `tools/checks/refusal_differential.py` over one eighth of the programs it takes."""
    report = harness.census(dict(list(programs().items())[chunk::8]), workers=1)
    assert report["broken"] == {} and report["first_identical"] == report["refused"] > 100

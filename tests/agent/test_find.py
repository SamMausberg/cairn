"""`cairn find`: the functions to call, by the values an agent has or by its words, over the builtins, the packaged
library and the program's own functions.

The questions are the ones the 1.1 evaluation's subjects searched the documentation for (evidence/v1_1/friction):
reading and parsing input, a Vec and a Buf of records, tasks and groups, the limits of i64 and searching text. A hit
fits by the checker's own rules: each probe the batch accepts also checks alone, and each it refuses is refused
alone, so one check of every probe answers what a check of each would.
"""

import json

import pytest

from cairn.agent.find import BUILTINS, Probes, find, indexed_or_refused
from cairn.cli import main
from cairn.compiler.cairnc import Diagnostic, compile_program
from cairn.compiler.primitives.builtins import TABLE

RECORDS = """struct Rec { key:u64; weight:i64; }

// The sum of a view of weights.
fn total(n:usize, xs:ro<i64>[n]) -> i64 {
  let mut sum:i64 = 0;
  for x in xs { sum += x; }
  return sum;
}

fn main() -> i32 { return 0; }
"""


def named(record: dict) -> list[str]:
    """The name each hit calls, as written before its parameters."""
    return [line.split("(")[0].split("[")[0] for line in record["hits"]]


@pytest.mark.parametrize(
    ("words", "first"),
    [
        ("parse integer", "std.text.parse_i64"),
        ("read stdin", "std.io.read_stdin"),
        ("vec push", "std.vec.push"),
        ("map insert", "std.map.insert"),
        ("find substring", "std.text.find"),
        ("spawn task group", "Group"),
        ("sort", "std.sort.radix_sort"),
    ],
)
def test_words_find_what_the_subjects_searched_for_first(words, first):
    assert named(find(None, words))[0] == first


def test_words_answer_with_every_word_they_can_and_a_line_each():
    parsed = find(None, "parse integer")
    assert named(parsed) == ["std.text.parse_i64", "std.text.parse_u64"] and parsed["more"] == 0
    [line] = [h for h in parsed["hits"] if h.startswith("std.text.parse_i64")]
    assert line == ("std.text.parse_i64(n:usize, s:ro<u8>[n]) -> Result[i64, ParseError]  pure  // Signed decimal: an "
                    "optional leading '-', then digits.")  # fmt: skip
    tasks = find(None, "spawn task group")["hits"]  # the builtins a task needs, and nothing that matched one word
    assert named({"hits": tasks}) == ["Group", "wait"] and "spawn f(args) into g" in tasks[0]
    assert any("-9223372036854775808" in line for line in find(None, "i64 minimum")["hits"])
    assert named(find(None, "signed overflow")) == ["add_wrap"]


def test_an_answer_is_bounded_and_counts_what_it_left_out():
    whole = find(None, "write", limit=200)
    assert len(whole["hits"]) > 12 and whole["more"] == 0
    few = find(None, "write", limit=3)
    assert few["hits"] == whole["hits"][:3] and few["more"] == len(whole["hits"]) - 3
    for limit in (0, 201):
        with pytest.raises(Diagnostic, match=r"1\.\.200"):
            find(None, "write", limit=limit)


def test_types_find_the_calls_the_checker_accepts_best_fit_first():
    assert named(find(None, takes=["ro<u8>[n]"], returns="i64")) == ["std.text.parse_i64"]  # a Result holding it
    assert named(find(None, takes=["i64"], returns="usize"))[0] == "usize"  # the conversion, exact, first
    assert named(find(None, returns="Vec[u8]"))[:2] == ["std.vec.new", "std.vec.with_capacity"]
    searched = named(find(None, takes=["ro<u8>[n]", "ro<u8>[n]"], returns="usize"))
    assert searched[:2] == ["std.sort.search", "std.text.find"]  # pure before what does I/O
    records = find(RECORDS, takes=["Vec[Rec]"], limit=40)
    held = named(records)
    assert {"std.vec.pop", "std.vec.clear", "std.vec.push", "std.vec.get", "std.vec.set"} <= set(held)
    assert "std.vec.find" not in held and "std.sort.sort" not in held  # Rec has no Eq and no Ord
    assert held.index("std.vec.pop") < held.index("std.vec.push")  # every parameter filled before one left open
    assert named(find(RECORDS, takes=["usize"], returns="Buf[Rec]")) == ["Buf"]


def test_the_program_s_own_functions_come_first_and_a_refused_program_is_said():
    assert named(find(RECORDS, takes=["ro<i64>[n]"], returns="i64"))[0] == "total"
    assert named(find(RECORDS, "sum"))[0] == "total"
    refused = find("fn main() -> i32 { return x; }", "parse integer")
    assert named(refused)[0] == "std.text.parse_i64" and "E-UNBOUND" in refused["program"]


def test_an_effect_ceiling_keeps_what_stays_within_it():
    writing = named(find(None, "write", limit=200))
    assert {"std.text.write_u64", "std.io.write", "std.fs.write"} <= set(writing)
    kept = named(find(None, "write", effects="pure", limit=200))  # writes of what a call is passed stay within it
    assert "std.text.write_u64" in kept and not any(n.startswith(("std.io.", "std.fs.")) for n in kept)
    io = "io, ffi:read, ffi:__errno_location, mmio"  # read_stdin also traps: a ceiling holds every effect, as E-EFFECT-CEILING does
    assert named(find(None, "read stdin", effects=io)) == []
    assert named(find(None, "read stdin", effects=f"effects(pure, {io})")) == ["std.io.read_stdin"]
    assert named(find(None, "println", effects="pure, io, ffi:write")) == [
        "std.io.println"
    ]  # a builtin's row is its call's
    assert named(find(None, "println", takes=["u64"], effects="pure, io, ffi:write")) == ["println"]


def test_every_builtin_has_one_line():
    covered = [name for names in BUILTINS for name in names.split()]
    assert sorted(covered) == sorted(TABLE) and len(covered) == len(set(covered))


def test_a_query_names_a_type_it_cannot_resolve_or_asks_nothing():
    with pytest.raises(Diagnostic) as unknown:
        find(None, takes=["Vec[Nothing]"])
    assert unknown.value.data["code"] == "E-TYPE"
    with pytest.raises(Diagnostic) as pinned:  # a value no parameter can hold is the checker's refusal, not "nothing"
        find(None, takes=["Group[u64]"])
    assert pinned.value.data["code"] == "E-PINNED"
    with pytest.raises(Diagnostic) as empty:
        find(None, " ")
    assert empty.value.data["code"] == "E-REQUEST"


def test_each_probe_is_judged_as_a_check_of_it_alone_would_judge_it():
    """One check of every probe, reporting every refusal, against a check of each probe on its own: the accepted
    ones and a spread of the refused ones, a template's instances and a linear value's drop among them."""
    index, _ = indexed_or_refused(RECORDS)
    for takes in (["Vec[Rec]"], ["File", "ro<u8>[n]"]):
        probes = Probes(index, [index.given(t) for t in takes], None)
        probes.judged()
        text = {line.split("(")[0][3:]: line for line in probes.text}
        refused = [name for name in probes.probes if name not in probes.accepted][::40]
        assert probes.accepted and refused
        for name in [*sorted(probes.accepted), *refused]:
            try:
                compile_program(index.imports + text[name] + index.program)
                alone = True
            except Diagnostic as error:
                alone = error.data["code"] == "E-LINEAR-LEAK"  # the probe's own drop of what it was handed
            assert alone == (name in probes.accepted), text[name]


def test_the_command_line_prints_the_record_or_the_lines(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["find", "parse", "integer", "--format", "json"]) == 0
    assert named(json.loads(capsys.readouterr().out))[0] == "std.text.parse_i64"
    (tmp_path / "main.cairn").write_text(RECORDS)
    assert main(["find", "--takes", "ro<i64>[n]", "--returns", "i64", "--in", "main.cairn", "--format", "human"]) == 0
    assert capsys.readouterr().out.startswith("total(n:usize, xs:ro<i64>[n]) -> i64  pure  // The sum")
    assert main(["find", "--takes", "Nothing", "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "E-TYPE"

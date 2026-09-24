"""`cairn rules`: the rule card of a code, a card by name, the cards a program selects, and every card, offline."""

import json

import pytest

from cairn.agent.teaching import CARDS, CODES, CORE, OWNER, TOOL_CARDS, every_card
from cairn.cli import main

TASKS = """fn fill(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = u64(i); } }
fn halves(n:usize, data:rw<u64>[n]) { let left = spawn fill(data[0..n]); wait(left); }
"""


def rules(capsys, *asked, status=0):
    assert main(["rules", *asked, "--format", "json"]) == status
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(("code", "card"), [("E-LEASED", "tasks"), ("E-MOVED", "owners"), ("E-PARSE", "base"),
                                            ("E-COOP-UNORDERED", "cooperative"), ("E-SESSION", "hosts")])  # fmt: skip
def test_a_code_prints_the_card_that_owns_it(capsys, code, card):
    record = rules(capsys, code)
    assert record["schema"] == "cairn.rules/1" and record["code"] == code
    [shown] = record["cards"]
    assert shown["name"] == card and code in shown["codes"] and shown["text"] == every_card()[card]


def test_a_card_prints_by_its_name(capsys):
    [shown] = rules(capsys, "tasks")["cards"]
    assert shown == {"name": "tasks", "kind": "language", "codes": ["E-LEASED", "E-SPAWN"], "text": CARDS["tasks"]}
    [tool] = rules(capsys, "limits")["cards"]
    assert tool["kind"] == "tool" and tool["text"] == TOOL_CARDS["limits"] and "E-INTERNAL" in tool["codes"]


def test_a_program_prints_the_cards_it_selects_beyond_the_three_every_program_gets(tmp_path, capsys):
    (tmp_path / "halves.cairn").write_text(TASKS)
    record = rules(capsys, str(tmp_path / "halves.cairn"))
    assert record["always"] == list(CORE) and [c["name"] for c in record["cards"]] == ["views", "tasks"]
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cairn").write_text(TASKS)
    (tmp_path / "cairn.toml").write_text('[project]\nname = "p"\nsources = ["src/main.cairn"]\n')
    assert [c["name"] for c in rules(capsys, str(tmp_path))["cards"]] == ["views", "tasks"]


def test_an_unknown_code_or_word_is_refused_with_its_own_code(capsys):
    unknown = rules(capsys, "E-NO-SUCH-RULE", status=1)
    assert unknown["code"] == "E-RULE" and "E-NO-SUCH-RULE" in unknown["message"]
    assert rules(capsys, "no_such_card", status=1)["code"] == "E-RULE"


def test_the_list_names_every_card_and_every_code_once(capsys):
    listed = rules(capsys, "--list")["cards"]
    assert [c["name"] for c in listed] == list(every_card()) and all("text" not in c for c in listed)
    assert sorted(code for c in listed for code in c["codes"]) == sorted(OWNER)
    assert rules(capsys)["cards"] == listed  # nothing asked is the list
    assert {c["name"]: c["codes"] for c in listed} == {n: sorted(CODES[n].split()) for n in every_card()}
    assert "spawn" in next(c for c in listed if c["name"] == "tasks")["words"]


def test_a_person_reads_the_card_under_a_line_naming_its_code(capsys):
    assert main(["rules", "E-LEASED", "--format", "human"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "E-LEASED is a rule of the tasks card: E-LEASED, E-SPAWN"
    assert lines[3] == CARDS["tasks"].splitlines()[0]
    assert main(["rules", "--list", "--format", "human"]) == 0
    assert capsys.readouterr().out.splitlines()[0].split()[:2] == ["base", "E-BUILTIN-NAME,"]

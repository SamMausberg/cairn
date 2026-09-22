"""A program's current state for an agent: deterministic, hashed, refreshed by a delta that reproduces it."""

import json

import pytest

from cairn.agent.agent_tools import HANDLES, EditHost
from cairn.agent.state import apply, delta, state
from emitted import code_of as code

S = (
    "struct Pair { a:u64; b:u64; }\n"
    "fn step(x:u64) -> u64 { return add_wrap(x, 1); }\n"
    "fn caller(x:u64) -> u64 { return step(x); }\n"
    "fn fill(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = step(u64(i)); } }\n"
)


def test_a_state_names_every_function_by_module_with_its_row_and_is_deterministic():
    now = state(S)
    assert now["status"] == "typed" and now["diagnostics"] == [] and now["types"] == "struct Pair { a:u64; b:u64; }"
    assert now["modules"][""]["fill"] == [
        "fn fill(n:usize, out:rw<u64>[n]@host)",
        ["ffi_precondition", "trap", "write:out"],
    ]
    assert now["modules"][""]["step"] == ["fn step(x:u64) -> u64", []]
    assert state(S) == now and len(now["digest"]) == 64
    assert state(S + "\n")["digest"] != now["digest"]  # The source hash is part of the state.


def test_a_refused_program_keeps_its_parsed_signatures_with_unknown_rows_and_its_diagnostic():
    broken = state(S.replace("add_wrap(x, 1)", "add_wrap(x, true)"))
    assert broken["status"] == "rejected" and broken["diagnostics"][0]["code"] == "E-TYPE-MISMATCH"
    assert broken["modules"][""]["step"] == ["fn step(x:u64) -> u64", None]  # None is unknown, never empty.
    unparsed = state("fn f() -> u64 { return 1 }")
    assert unparsed["modules"] == {} and unparsed["diagnostics"][0]["code"] == "E-PARSE"


def test_a_delta_holds_only_what_changed_and_reproduces_the_state_it_names():
    before, after = state(S), state(S.replace("return add_wrap(x, 1);", "return x + 1;").replace("fn fill", "fn gone"))
    change = delta(before, after)
    trapping = ["fn step(x:u64) -> u64", ["trap"]], ["fn caller(x:u64) -> u64", ["trap"]]  # the caller's row follows
    assert change["modules"] == {"": {"fill": None, "gone": after["modules"][""]["gone"], "step": trapping[0],
                                      "caller": trapping[1]}}  # fmt: skip
    assert set(change) == {"protocol", "since", "digest", "modules", "source_sha256"}
    assert apply(before, change) == after
    with pytest.raises(ValueError):
        apply(after, change)  # taken from another state
    with pytest.raises(ValueError):
        apply(before, {**change, "digest": "0" * 64})  # does not reproduce what it names


def test_the_host_serves_the_state_after_each_admitted_edit_and_a_delta_since_one_it_sent():
    host = EditHost()
    host.open(S, "fill")
    ask = {"protocol": HANDLES, "handle": "e1"}
    first = host.respond({**ask, "kind": "state"})
    assert first["evidence"] == [] and first["modules"][""]["fill"][1] == ["ffi_precondition", "trap", "write:out"]
    host.respond({**ask, "kind": "body", "replacement": "{ }"})
    change = host.respond({**ask, "kind": "delta", "since": first["digest"]})
    emptied = state(S.replace("{ for i in 0..n { out[i] = step(u64(i)); } }", "{ }"))["modules"][""]["fill"]
    assert change["modules"] == {"": {"fill": emptied}} and "write:out" not in emptied[1]
    assert change["evidence"] == [
        {"symbol": "fill", "status": "typed", "effects": emptied[1], "check_sites": {"view_entry": 1}}
    ]
    assert apply(first, change) == host.respond({**ask, "kind": "state"})
    assert code(lambda: host.respond({**ask, "kind": "delta", "since": "0" * 64})) == "E-SESSION"
    assert code(lambda: host.respond({**ask, "kind": "state", "since": first["digest"]})) == "E-REQUEST"


def test_state_prints_from_the_command_line_and_refreshes_from_a_saved_state(tmp_path, capsys):
    from cairn.cli import main

    (tmp_path / "p.cairn").write_text(S)
    assert main(["state", str(tmp_path / "p.cairn")]) == 0
    saved = capsys.readouterr().out
    assert json.loads(saved) == state(S)
    (tmp_path / "old.json").write_text(saved)
    (tmp_path / "p.cairn").write_text(S.replace("add_wrap(x, 1)", "x + 1"))
    assert main(["state", str(tmp_path / "p.cairn"), "--since", str(tmp_path / "old.json")]) == 0
    trapping = {"step": ["fn step(x:u64) -> u64", ["trap"]], "caller": ["fn caller(x:u64) -> u64", ["trap"]]}
    assert json.loads(capsys.readouterr().out)["modules"] == {"": trapping}
    (tmp_path / "p.cairn").write_text("fn f() -> u64 { return true; }")
    assert main(["state", str(tmp_path / "p.cairn")]) == 1  # A refused program's state is printed, and exits 1.
    assert json.loads(capsys.readouterr().out)["diagnostics"][0]["file"].endswith("p.cairn")

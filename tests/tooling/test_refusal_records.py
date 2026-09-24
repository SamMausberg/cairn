"""A refused program's record names the card that owns its code and carries the fix the compiler can state, the same
from `cairn check`, `build`, `test` and the MCP `check` tool, as JSON and at a terminal."""

import json

import pytest

from cairn.agent.diagnostics import HINTS
from cairn.agent.mcp_tools import Tools
from cairn.cli import main

PROGRAMS = {  # a refusal from each part of the language: (program, code, card, the fix or None)
    "ownership": ("fn eat(b:Buf[u64]) {}\nfn main() -> i32 {\n  let b = Buf[u64](4);\n  eat(b);\n  eat(b);\n"
                  "  return 0;\n}\n", "E-MOVED", "owners", HINTS["E-MOVED"]),
    "leases": ("fn fill(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = u64(i); } }\n"
               "fn halves(n:usize, data:rw<u64>[n]) {\n  let left = spawn fill(data[0..n]);\n  data[0] = 7;\n"
               "  wait(left);\n}\n", "E-LEASED", "tasks", HINTS["E-LEASED"]),
    "lanes": ("fn f(n:usize, out:rw<u64>[n]) {\n  parallel i in n { out[0] = u64(i); }\n}\n", "E-PARALLEL-RACE",
              "parallel", None),
    "cooperative": ("fn reverse(g:usize, out:rw<u64>[g]) {\n  blocks b in g threads t in 256 {\n"
                    "    shared s:u64[256] = zeroed;\n    s[t] = u64(t);\n    let v = s[255 - t];\n"
                    "    if t == 0 { out[b] = v; }\n  }\n}\n", "E-COOP-UNORDERED", "cooperative",
                    HINTS["E-COOP-UNORDERED"]),
    "effects": ("fn f(n:usize) -> usize pure {\n  let b = Buf[u64](n);\n  return len(b);\n}\n", "E-EFFECT-CEILING",
                "effects", None),
    "types": ("fn f(x:u32) -> u64 {\n  return x;\n}\n", "E-TYPE-MISMATCH", "base",
              "Convert explicitly, u64(x), which traps outside u64's range, or compute in u64."),
    "parse": ("fn f() -> u64 {\n  return 1\n}\n", "E-PARSE", "base", HINTS["E-PARSE"]),
    "a close name": ("fn total(x:u64) -> u64 = x;\nfn main() -> i32 {\n  return i32(totl(3));\n}\n", "E-CALLEE",
                     "calls", "Did you mean total?"),
}  # fmt: skip


@pytest.mark.parametrize("part", PROGRAMS)
def test_a_refusal_names_its_card_and_its_fix_as_json(tmp_path, capsys, part):
    source, code, card, hint = PROGRAMS[part]
    (tmp_path / "p.cairn").write_text(source)
    for command in ("check", "build"):
        assert main([command, str(tmp_path / "p.cairn"), "--format", "json"]) == 1
        record = json.loads(capsys.readouterr().out)
        assert (record["code"], record["card"], record.get("repair_hint")) == (code, card, hint), command
        assert record["protocol"] == "cairn.diagnostic/2" and record["file"] == "p.cairn" and record["line"] > 0


@pytest.mark.parametrize("part", PROGRAMS)
def test_a_refusal_shows_its_fix_as_help_and_points_to_its_card(tmp_path, capsys, part):
    source, code, card, hint = PROGRAMS[part]
    (tmp_path / "p.cairn").write_text(source)
    assert main(["check", str(tmp_path / "p.cairn"), "--format", "human"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[0].startswith(f"error[{code}]: ")
    assert err[-1] == f"  = note: the {card} card states this rule: cairn rules {code}"
    helped = [line for line in err if line.startswith("  = help: ")]
    assert helped == ([f"  = help: {hint[0].lower()}{hint[1:]}"] if hint else [])


@pytest.mark.parametrize("part", PROGRAMS)
def test_the_mcp_check_tool_says_what_the_command_line_says(tmp_path, part):
    source, code, card, hint = PROGRAMS[part]
    (tmp_path / "p.cairn").write_text(source)
    tools = Tools(tmp_path)
    for asked in ({"source": source}, {"path": "p.cairn"}):
        record, refused = tools.call("check", asked)
        assert refused and (record["code"], record["card"], record.get("repair_hint")) == (code, card, hint)


def test_a_host_s_contract_is_spoken_of_only_inside_a_host(tmp_path, capsys):
    (tmp_path / "p.cairn").write_text("fn f(x:u64) -> u64 = x;\n")
    assert main(["state", str(tmp_path / "p.cairn"), "--symbol", "g", "--format", "json"]) == 1
    record = json.loads(capsys.readouterr().out)
    assert record["code"] == "E-SYMBOL" and record["card"] == "hosts" and "repair_hint" not in record
    missing, _ = Tools(tmp_path).call("check", {"source": "fn f() -> u64 { return crc32(1); }"})
    assert missing["code"] == "E-CALLEE" and "repair_hint" not in missing  # no expand request outside a host


def test_an_environment_refusal_names_the_commands_card(tmp_path, capsys):
    assert main(["test", str(tmp_path / "missing.cairn"), "--format", "json"]) == 2
    record = json.loads(capsys.readouterr().out)
    assert record["code"] == "E-PROJECT-OR-ENVIRONMENT" and record["card"] == "commands" and "repair_hint" not in record


def test_a_program_with_tests_is_refused_with_its_card_before_any_runs(tmp_path, capsys):
    (tmp_path / "p.cairn").write_text(
        "fn f(x:u64) -> u64 { return x + y; }\ntest t { assert(f(1) == 1); }\nfn main() -> i32 = i32(f(0));\n"
    )
    for command in ("test", "run"):
        assert main([command, str(tmp_path / "p.cairn"), "--format", "json"]) == 1
        record = json.loads(capsys.readouterr().out)
        assert (record["code"], record["card"], record["repair_hint"]) == ("E-UNBOUND", "base", HINTS["E-UNBOUND"])


def test_each_further_refusal_of_a_check_names_its_card_and_fix(tmp_path, capsys):
    (tmp_path / "p.cairn").write_text("fn f(x:u32) -> u64 {\n  return x;\n}\nfn g(total:u64) -> u64 {\n  return totl;\n}\n")  # fmt: skip
    assert main(["check", str(tmp_path / "p.cairn"), "--format", "json"]) == 1
    record = json.loads(capsys.readouterr().out)
    [further] = record["further"]
    assert (record["code"], record["card"]) == ("E-TYPE-MISMATCH", "base")
    assert (further["code"], further["card"], further["repair_hint"]) == ("E-UNBOUND", "base", "Did you mean total?")
    record, _ = Tools(tmp_path).call("check", {"path": "p.cairn"})
    assert record["further"][0]["card"] == "base" and record["further"][0]["repair_hint"] == "Did you mean total?"

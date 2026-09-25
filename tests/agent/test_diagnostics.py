"""What a model reads back for a refused reply: where in its reply, and the smallest fix the host can state."""

import json

import pytest

from cairn.agent.diagnostics import HINTS, fix, taught
from cairn.agent.hosts.edits import HANDLES, EditHost

S = (
    "fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {\n  let mut sum:u32 = 0;\n"
    "  for i in 0..n { sum = add_wrap(sum, u32(bytes[i])); }\n  return sum;\n}\n"
    "fn relay(n:usize, bytes:ro<u8>[n]) -> u32 { return checksum(bytes); }\n"
    "fn hidden(x:u32) -> u32 = x;\n"
)


def refusal(body, kind="body", site=None):
    host = EditHost()
    host.open(S, "relay", site=site)
    request = {
        "protocol": HANDLES,
        "handle": "e1",
        "kind": kind,
        "replacement": body,
        **({"site": site} if site else {}),
    }
    return host.reply(json.dumps(request))


@pytest.mark.parametrize(
    ("body", "code", "hint"),
    [
        ("{ return checksum(bytez); }", "E-UNBOUND", "Did you mean bytes?"),
        ("{ return checksun(bytes); }", "E-CALLEE", "Did you mean checksum? Expand a function before calling it."),
        ("{ return crc32(bytes); }", "E-CALLEE", "Expand a function before calling it."),
        ("{ return hidden(1); }", "E-CONTEXT-CLOSURE", "Ask first: an expand request naming hidden."),
        ("{ let wide:u64 = 1; return wide; }", "E-TYPE-MISMATCH", "Convert explicitly, u32(x), which traps outside u32's range, or compute in u32."),
        ("{ stack pad:u8[4] = zeroed; return checksum(pad); }", "E-EFFECT-EXPANSION",
         "Remove what brings local_read: reading the function's own storage; stack_storage: a stack declaration; zero_init: zeroed storage. The ceiling is the host's."),
    ],
)  # fmt: skip
def test_a_refusal_carries_the_fix_its_data_determines(body, code, hint):
    reply = refusal(body)
    assert reply["code"] == code and reply["repair_hint"] == hint


def test_a_misspelt_field_or_variant_gets_the_close_name():
    source = "struct Frame { head:u8; size:u32; }\nenum Op { Read; Write; }\nfn f(x:Frame) -> u32 { return x.size; }\n"
    host = EditHost()
    host.open(source, "f")
    ask = {"protocol": HANDLES, "handle": "e1", "kind": "body"}
    field = host.reply(json.dumps({**ask, "replacement": "{ return x.sise; }"}))
    assert field["code"] == "E-FIELD" and field["repair_hint"] == "Did you mean size?"
    variant = host.reply(json.dumps({**ask, "replacement": "{ if Op.Wrte == Op.Read { return 1; } return 0; }"}))
    assert variant["code"] == "E-ENUM-VARIANT" and variant["repair_hint"] == "Did you mean Write?"


def test_a_refusal_points_into_the_reply_not_the_spliced_source():
    parse = refusal("{\n  return checksum(bytes)\n}")
    assert parse["code"] == "E-PARSE" and (parse["line"], parse["column"]) == (3, 1) and parse["source_line"] == "}"
    typed = refusal("{\n  let flag:bool = 1;\n  return 0;\n}")
    assert typed["code"] == "E-TYPE-MISMATCH" and typed["in"] == "reply" and typed["line"] == 2
    assert typed["source_line"] == "  let flag:bool = 1;" and typed["repair_hint"] == "Compare to make a bool: x != 0."
    site = refusal("bytez", kind="expr", site="x0")
    assert site["code"] == "E-UNBOUND" and site["source_line"] == "bytez" and site["column"] == 1


def test_a_code_whose_message_says_the_repair_carries_no_second_one():
    reply = refusal("{ let s:u32 = checksum(bytes) + checksum(bytes); let s:u32 = 1; return s; }")
    assert reply["code"] == "E-SHADOW" and reply["repair_hint"] == HINTS["E-SHADOW"]
    assert fix({"code": "E-EFFECT-ORDER", "message": "Bind a writing call to its own statement."}) is None
    assert fix({"code": "E-LOOP-CONTROL", "message": "break requires an enclosing loop."}) is None
    for code in (
        "E-COOP-UNORDERED",
        "E-COOP-REUSE",
        "E-COOP-CONFLICT",
        "E-STAGE-BUSY",
        "E-LINEAR-LEAK",
        "E-IMPL-SIGNATURE",
        "E-IMPL-CALL",
        "E-IMPL-WHEN",
        "E-IMPL-PARAM",
    ):  # each message says what to change
        assert fix({"code": code, "message": "The message says the fix."}) is None, code
    assert (
        fix({"code": "E-IMPORT", "message": "Unknown module q; only project modules and std.* can be imported."})
        is None
    )
    assert fix({"code": "E-UNBOUND", "message": "Unbound name q.", "available_names": ["bytes"]}) == HINTS["E-UNBOUND"]


def test_a_host_refusal_names_the_card_that_states_its_rule():
    assert refusal("{ return checksum(bytez); }")["card"] == "base"
    assert refusal("{ return hidden(1); }")["card"] == "hosts"
    assert refusal("{ stack pad:u8[4] = zeroed; return checksum(pad); }")["card"] == "hosts"


def test_outside_a_host_a_fix_never_speaks_of_one():
    callee = {"code": "E-CALLEE", "message": "Unknown callable checksun; arbitrary C++ names are not allowed."}
    assert taught(callee, ("checksum",)) == {**callee, "card": "calls", "repair_hint": "Did you mean checksum?"}
    assert "repair_hint" not in taught({"code": "E-SESSION", "message": "Unknown handle."})
    assert taught({"code": "E-SESSION", "message": "Unknown handle."}, host=True)["repair_hint"] == HINTS["E-SESSION"]
    assert taught({"code": "E-MINE", "message": "A recipe chose this code."}) == {
        "code": "E-MINE",
        "message": "A recipe chose this code.",
    }


# The refusals of the 1.1 evaluation whose message did not say what to change (evidence/v1_1/friction), each with the
# fix the compiler knows.
EXTENT = "fn total(n:usize, xs:ro<u64>[n]) -> u64 { return xs[0]; }\n"
STATED = {
    "a Buf where [n] is expected: the part": (
        EXTENT + "fn main() -> i32 { let n:usize = 4; let mut v = Buf[u64](n); return i32(total(n, v)); }\n",
        "v is len(v) elements long, which the checker does not tie to n: pass the part v[0..n], whose bound is "
        "checked once at the call.",
    ),
    "an unannotated literal's u64: the annotation": (
        "fn main() -> i32 { let mut xs = Buf[u64](4); let mut i = 0; xs[i] = 1; return 0; }\n",
        "i is a u64 because an integer literal is one when nothing expects another type: declare it "
        "let mut i:usize = 0;",
    ),
    "an import by name that hides a builtin: its name": (
        'import std.io (println);\nfn main() -> i32 { let count:u64 = 3; println("count ", count); return 0; }\n',
        "println here is std.io.println, which the import by name put in place of the builtin println: take "
        "println out of the import's list, and call std.io.println through its module where you mean it.",
    ),
}


@pytest.mark.parametrize("case", STATED)
def test_a_refusal_states_the_fix_the_compiler_knows(case):
    from emitted import refused

    source, hint = STATED[case]
    assert taught(refused("E-TYPE-MISMATCH", source))["repair_hint"] == hint


def test_a_literal_binding_where_no_number_is_expected_gets_that_type_s_hint():
    from emitted import refused

    said = refused("E-TYPE-MISMATCH", "fn f(b:bool) -> bool = b;\nfn main() -> i32 { let x = 0; f(x); return 0; }\n")
    assert said["literal_binding"]["literal"] == "0" and fix(said) == "Compare to make a bool: x != 0."

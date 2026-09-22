"""What a model reads back for a refused reply: where in its reply, and the smallest fix the host can state."""

import json

import pytest

from cairn.agent.agent_tools import HANDLES, EditHost
from cairn.agent.diagnostics import HINTS, fix

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
    assert fix({"code": "E-UNBOUND", "message": "Unbound name q.", "available_names": ["bytes"]}) == HINTS["E-UNBOUND"]

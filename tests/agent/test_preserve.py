"""A host may ask that an edit keep what its function does: `preserve: identical` admits only the same emitted code,
`preserve: equivalent` also admits a rewrite Z3 shows behaves the same. Anything else is E-PRESERVE, never admitted."""

import json

import pytest

from cairn.agent.agent_tools import PROTOCOL, EditHost, EditSession
from emitted import code_of

S = "fn gap(a:u32, b:u32) -> u32 { if a > b { return a - b; } return b - a; }\nfn use(a:u32) -> u32 = gap(a, 3);\n"
LOOP = "fn total(n:u64) -> u64 { let mut t:u64 = 0; let mut i:u64 = 0; while i < n { t += i; i += 1; } return t; }\n"


def request(s, body):
    return {"protocol": PROTOCOL, "session": s.session, "kind": "body", "replacement": body}


@pytest.mark.parametrize(
    "preserve,body,admitted",
    [
        ("identical", "{ if a > b { return a - b; } return b - a; }", "identical-code"),  # the same code, rewritten
        ("equivalent", "{ if b > a { return b - a; } return a - b; }", "smt-equivalent"),
        ("equivalent", "{ if a > b { return a - b; } return b - a; }", "identical-code"),
    ],
)
def test_a_proven_rewrite_is_admitted_with_what_proves_it(preserve, body, admitted):
    s = EditSession(S, "gap", {"preserve": preserve})
    _, admission = s.check(request(s, body))
    assert admission["equivalence"] == admitted


@pytest.mark.parametrize(
    "source,symbol,preserve,body,why",
    [
        (S, "gap", "identical", "{ if b > a { return b - a; } return a - b; }", None),  # equivalent, not identical
        (S, "gap", "equivalent", "{ if a >= b { return a - b; } return b - a + 1; }", "witness"),
        (LOOP, "total", "equivalent", "{ let mut t:u64 = 0; let mut i:u64 = 0; while i < n { t = t + i; i = i + 1; } "
         "return t + 0; }", "reason"),  # unknown is never admitted as unchanged
    ],
)  # fmt: skip
def test_an_edit_that_is_not_proven_to_keep_the_function_is_refused(source, symbol, preserve, body, why):
    s = EditSession(source, symbol, {"preserve": preserve})
    try:
        s.check(request(s, body))
    except Exception as e:
        assert e.data["code"] == "E-PRESERVE"
        if why == "witness":
            w = e.data["witness"]
            assert w["before"] != w["after"] and set(w["inputs"]) == {"a", "b"}
        if why == "reason":
            assert "unrolling budget" in e.data["reason"]
        return
    pytest.fail("the edit was admitted")


def test_the_contract_names_a_class_and_binds_the_session():
    assert code_of(lambda: EditSession(S, "gap", {"preserve": "similar"})) == "E-CONTRACT"
    assert EditSession(S, "gap", {"preserve": "equivalent"}).session != EditSession(S, "gap").session
    plain = EditSession(S, "gap")
    _, admission = plain.check(request(plain, "{ if a >= b { return a - b; } return b - a + 1; }"))
    assert admission["equivalence"] == "not-proved"  # without the contract nothing is compared


def test_the_host_answers_a_model_s_reply_with_the_refusal_and_its_witness():
    host = EditHost()
    host.open(S, "gap", {"preserve": "identical"})
    reply = {"protocol": "cairn.edit/2", "handle": "e1", "kind": "body",
             "replacement": "{ if a >= b { return a - b; } return b - a + 1; }"}  # fmt: skip
    answer = host.reply(json.dumps(reply))
    assert answer["code"] == "E-PRESERVE" and answer["witness"]["before"] != answer["witness"]["after"]
    assert answer["repair_hint"].startswith("At a = ") and answer["repair_hint"].endswith("keep that answer.")
    assert "e1" not in host.admitted  # nothing was admitted

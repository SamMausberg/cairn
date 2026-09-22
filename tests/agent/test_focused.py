"""Focused packets that grow on request, and host handles in place of digests (cairn.edit/2)."""

import json
import re
from pathlib import Path

import pytest
import test_language as language
from test_agent10 import PROGRAMS

from cairn.agent.agent_tools import HANDLES, PROTOCOL, EditHost, EditSession
from cairn.compiler.cairnc import Diagnostic

S = (
    "// Adds one, wrapping.\nfn step(x:u64)->u64 { return add_wrap(x,1); }\n"
    "fn caller(x:u64)->u64{return step(x);}\nfn other(x:u64)->u64{return mul_wrap(x,2);}\n"
    "struct Pair { a:u64; b:u64; }\nfn pair(p:Pair)->u64{return p.a;}\n"
)


def code(call):
    with pytest.raises(Diagnostic) as e:
        call()
    return e.value.data["code"]


def body(s, text):
    return s.check({"protocol": PROTOCOL, "session": s.session, "kind": "body", "replacement": text})


def test_expansion_discloses_a_body_and_makes_it_callable():
    s = EditSession(S, "step")
    assert code(lambda: body(s, "{return other(x);}")) == "E-CONTEXT-CLOSURE"
    stale = s.session
    grown = s.expand(["other"])
    assert grown["context"] == [{"symbol": "other", "source": "fn other(x:u64)->u64{return mul_wrap(x,2);}"}]
    assert set(grown["dependencies"]) == {"other"} and s.session != stale
    assert body(s, "{return other(x);}")[1]["status"] == "typed"
    old = {"protocol": PROTOCOL, "session": stale, "kind": "body", "replacement": "{return x;}"}
    assert code(lambda: s.check(old)) == "E-SESSION"  # What may be called changed, so the old digest is stale.
    assert [c["symbol"] for c in s.packet()["context"]] == ["step", "other"]


def test_expanding_a_disclosed_callee_keeps_the_digest():
    s = EditSession(S, "caller")
    before = s.session
    assert s.expand(["step"])["context"][0]["source"].startswith("fn step") and s.session == before


@pytest.mark.parametrize(
    ("names", "error"),
    [([], "E-REQUEST"), ("other", "E-REQUEST"), ([1], "E-REQUEST"), (["x"] * 33, "E-REQUEST"),
     (["ghost"], "E-SYMBOL"), (["step"], "E-SYMBOL")],
)  # fmt: skip
def test_expansion_requests_are_bounded_and_named(names, error):
    assert code(lambda: EditSession(S, "step").expand(names)) == error


def test_expansion_shows_a_type_and_the_types_a_body_names():
    s = EditSession(S, "step")
    assert "struct Pair" not in json.dumps(s.packet())
    assert s.expand(["Pair"])["types"] == "struct Pair { a:u64; b:u64; }"
    assert "struct Pair" in s.expand(["pair"])["types"]
    assert "struct Pair" in EditSession(S, "pair").packet()["types"]


def test_host_contracts_and_comments_travel_with_an_interface():
    p = EditSession(S, "caller", {"contracts": {"step": "Returns x + 1 modulo 2^64."}}).packet()
    assert p["dependencies"]["step"] == {
        "signature": "fn step(x:u64) -> u64",
        "effects": [],
        "contract": "Returns x + 1 modulo 2^64.",
        "comment": "Adds one, wrapping.",
    }
    assert code(lambda: EditSession(S, "caller", {"contracts": {"ghost": "x"}})) == "E-SYMBOL"
    assert code(lambda: EditSession(S, "caller", {"contracts": {"step": 1}})) == "E-CONTRACT"
    assert code(lambda: EditSession(S, "caller", scope="whole")) == "E-REQUEST"


BODIES = ["{return x;}", "{return other(x);}", "{return caller(x);}", "{return step(x);}", "{return x+1;}"]


@pytest.mark.parametrize("name", ["breadth", "tasks", "extents"])
def test_the_focused_boundary_is_never_wider_than_the_component(name):
    source = PROGRAMS[name]
    for f in [f for f in EditSession(source, "main").parsed.functions if not f.static]:
        focused, component = EditSession(source, f.name), EditSession(source, f.name, scope="component")
        assert focused.visible <= component.visible
        assert set(focused.packet()["dependencies"]) <= set(component.packet()["dependencies"])


def test_the_focused_verdict_implies_the_component_verdict():
    for symbol in ["step", "caller", "other"]:
        focused, component = EditSession(S, symbol), EditSession(S, symbol, scope="component")
        for text in BODIES:
            try:
                body(focused, text)
            except Diagnostic:
                continue
            body(component, text)  # Anything admitted with less disclosed is admitted with more.


def test_host_handles_carry_no_digests():
    host = EditHost()
    p = host.open(S, "step")
    assert p["handle"] == "e1" and p["draft_protocol"]["protocol"] == HANDLES
    assert not re.search(r"[0-9a-f]{64}", json.dumps(p))
    admitted = host.respond({"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": "{return x;}"})
    assert admitted["status"] == "typed" and "session" not in admitted
    candidate, receipt = host.admitted["e1"][0]
    assert "fn step(x:u64)->u64 {return x;}" in candidate and len(receipt["candidate_sha256"]) == 64


def test_host_expressions_use_short_site_names():
    host = EditHost()
    p = host.open("fn f(a:u64,b:u64)->u64{return a*b;}", "f", site="x2")
    assert p["focus"]["site"] == "x2" and p["focus"]["source"] == "b"
    reply = {"protocol": HANDLES, "handle": "e1", "kind": "expr", "site": "x2", "replacement": "b+1"}
    assert host.respond(reply)["status"] == "typed" and "a*(b+1)" in host.admitted["e1"][0][0]
    assert code(lambda: host.respond({**reply, "site": "x99"})) == "E-SITE"
    assert code(lambda: host.respond({**reply, "site": ["x2"]})) == "E-SITE"


@pytest.mark.parametrize(
    "request_",
    [
        {"protocol": HANDLES, "handle": "e9", "kind": "body", "replacement": "{return x;}"},
        {"protocol": HANDLES, "handle": 1, "kind": "body", "replacement": "{return x;}"},
        {"protocol": PROTOCOL, "handle": "e1", "kind": "body", "replacement": "{return x;}"},
        {"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": "{return x;}", "session": "0" * 64},
        {"protocol": HANDLES, "handle": "e1", "kind": "rename", "replacement": "{return x;}"},
        {"protocol": HANDLES, "handle": "e1", "kind": "expand"},
        ["not", "an", "object"],
    ],
)
def test_host_refuses_malformed_or_unknown_requests(request_):
    host = EditHost()
    host.open(S, "step")
    assert code(lambda: host.respond(request_)) in {"E-SESSION", "E-REQUEST"}


def test_host_admission_runs_every_session_check():
    host = EditHost()
    host.open(S, "step")
    for text, error in [("{return x+1;}", "E-EFFECT-EXPANSION"), ("{return other(x);}", "E-CONTEXT-CLOSURE"),
                        ("{return true;}", "E-TYPE-MISMATCH")]:  # fmt: skip
        reply = host.reply(json.dumps({"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": text}))
        assert reply["code"] == error and "repair_hint" in reply
    grown = host.respond({"protocol": HANDLES, "handle": "e1", "kind": "expand", "symbols": ["other"]})
    assert "session" not in grown and grown["context"][0]["symbol"] == "other"
    edit = {"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": "{return other(x);}"}
    assert host.reply(json.dumps(edit))["status"] == "typed"
    assert host.reply("{not json")["code"] == "E-REQUEST"


def test_a_warm_host_sends_each_card_and_the_boundaries_once():
    host = EditHost()
    first, second = host.open(S, "step"), host.open(S, "caller")
    assert "base" in first["rule_cards"] and "boundaries" in first and "sent_before" not in first
    assert "base" not in second["rule_cards"] and "boundaries" not in second
    assert {"base", "boundaries"} <= set(second["sent_before"])
    third = host.open(language.PRELUDE + language.MAIN, "main", scope="component")
    assert "generics" in third["rule_cards"] and "base" in third["sent_before"]
    expand = {"protocol": HANDLES, "handle": "e3", "kind": "expand", "symbols": ["push"]}
    assert code(lambda: host.respond(expand)) == "E-REQUEST"  # A component packet has nothing more to disclose.


def test_inspect_prints_a_focused_packet_and_its_expansions(tmp_path, capsys):
    from cairn.cli import main

    (tmp_path / "p.cairn").write_text(S)
    assert main(["inspect", str(tmp_path / "p.cairn"), "--symbol", "step", "--expand", "other"]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["protocol"] == "cairn.packet/2" and [c["symbol"] for c in packet["context"]] == ["step", "other"]
    assert main(["inspect", str(tmp_path / "p.cairn"), "--symbol", "step", "--scope", "component"]) == 0
    assert json.loads(capsys.readouterr().out)["protocol"] == "cairn.packet/1"
    assert main(["inspect", str(tmp_path / "p.cairn"), "--symbol", "step", "--expand", "ghost"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "E-SYMBOL"


def test_every_draft_the_host_offers_has_the_shape_the_schema_states():
    schema = json.loads((Path(__file__).resolve().parents[2] / "docs/project/edit_schema.json").read_text())
    shapes = {(v["properties"]["protocol"]["const"], v["properties"]["kind"]["const"]): v for v in schema["oneOf"]}
    host, session = EditHost(), EditSession(S, "step")
    drafts = [
        host.open(S, "step")["draft_protocol"],
        host.open(S, "step", site="x0")["draft_protocol"],
        host.open(S, "step")["expand_protocol"],
        host.open(S, "step")["explain_protocol"],
        session.packet()["draft_protocol"],
        session.packet(next(iter(session.sites)))["draft_protocol"],
    ]
    for draft in drafts:
        shape = shapes[draft["protocol"], draft["kind"]]
        assert set(draft) == set(shape["required"]) == set(shape["properties"])
        for key, rule in shape["properties"].items():
            if "pattern" in rule:
                assert re.fullmatch(rule["pattern"], draft[key]), (key, draft[key])

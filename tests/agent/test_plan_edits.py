"""The plan-only edit: a reply changes one function's plan and can change nothing else.

Each refusal names its code: a reply that tries to carry anything but plan items, an item the function's regions do
not take, a value out of range, a stale or spent session, and a body edit sent to a plan host or a plan reply sent to
a body host. An admitted reply leaves every function's receipt as it was, apart from the plan.
"""

import pytest

from cairn.agent.agent_tools import HANDLES, EditHost
from cairn.agent.plans import PROTOCOL, PlanHost
from cairn.compiler.cairnc import Diagnostic, compile_source

SPREAD = """fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..64 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
fn walk(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = 1; } }
"""
DEVICE = "fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }\n"


def reply(packet, **items):
    return {"protocol": PROTOCOL, "session": packet["session"], "items": items}


def code_of(call) -> str:
    with pytest.raises(Diagnostic) as caught:
        call()
    return caught.value.data["code"]


def test_the_packet_opens_only_the_items_of_the_function_s_regions():
    host = PlanHost()
    packet = host.open(SPREAD, "spread", sizes=[{"n": 1e6}])
    assert set(packet["items"]) == {"grain", "lanes"} and packet["current"] == {}
    assert packet["items"]["lanes"] == {"region": "host", "least": 1, "most": 1024}
    assert packet["predicted"][0]["sizes"] == {"n": 1e6} and packet["signature"].startswith("fn spread(")
    device = host.open(DEVICE, "scale")
    assert set(device["items"]) == {"block", "per_lane", "unroll", "vector", "stage"}
    assert device["items"]["block"]["multiple_of"] == 32 and device["items"]["vector"]["power_of_two"]


def test_an_admitted_plan_changes_the_plan_and_nothing_else():
    host = PlanHost()
    packet = host.open(SPREAD, "spread", sizes=[{"n": 1e6}])
    admitted = host.respond(reply(packet, grain=1, lanes=8))
    assert admitted["status"] == "admitted" and admitted["plan"] == "plan spread { grain 1; lanes 8; }"
    assert admitted["rows_unchanged"] and admitted["predicted"][0]["sizes"] == {"n": 1e6}
    now = host.source("spread")
    before, after = compile_source(SPREAD)[1]["functions"], compile_source(now)[1]["functions"]
    assert after["spread"]["plan"] == {"grain": 1, "lanes": 8}
    assert {n: {k: v for k, v in r.items() if k != "plan"} for n, r in after.items()} == before
    assert now.startswith(SPREAD.rstrip("\n"))  # the source is the original with one plan line after it


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"items": {"block": 128}}, "E-PLAN"),  # a device item, and spread has only a host region
        ({"items": {"lanes": 2000}}, "E-PLAN"),  # out of range
        ({"items": {"lanes": -1}}, "E-PLAN"),
        ({"items": {"lanes": "8"}}, "E-REQUEST"),  # a value is a whole number, never text to splice
        ({"items": {"lanes": True}}, "E-REQUEST"),
        ({"items": "grain 1; } fn evil() {"}, "E-REQUEST"),
        ({"replacement": "{ return; }"}, "E-REQUEST"),  # a plan reply carries items and nothing else
        ({"session": "0" * 64}, "E-SESSION"),
    ],
)
def test_a_reply_that_is_not_only_a_plan_is_refused(change, code):
    host = PlanHost()
    packet = host.open(SPREAD, "spread")
    assert code_of(lambda: host.respond({**reply(packet, grain=1), **change})) == code
    assert host.source("spread") == SPREAD  # nothing was applied


def test_a_spent_or_stale_session_changes_nothing():
    host = PlanHost()
    packet = host.open(SPREAD, "spread")
    first = host.respond(reply(packet, lanes=4))
    assert code_of(lambda: host.respond(reply(packet, lanes=2))) == "E-SESSION"  # spent by the first reply
    assert "lanes 4" in host.source("spread")
    host.open(SPREAD, "spread")  # the host's source for spread moved on
    assert code_of(lambda: host.respond({**reply(packet, lanes=2), "session": first["next_session"]})) == "E-SESSION"


def test_a_function_without_a_region_or_outside_the_root_has_no_plan_session():
    assert code_of(lambda: PlanHost().open(SPREAD, "walk")) == "E-PLAN"
    assert code_of(lambda: PlanHost().open(SPREAD, "nothing")) == "E-SYMBOL"


def test_neither_protocol_widens_into_the_other():
    plans = PlanHost()
    packet = plans.open(SPREAD, "spread")
    body = {"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": "{ return; }"}
    assert code_of(lambda: plans.respond(body)) == "E-REQUEST"
    edits = EditHost()
    edits.open(SPREAD, "spread")
    assert code_of(lambda: edits.respond({**reply(packet, grain=1), "handle": "e1", "kind": "plan"})) == "E-REQUEST"

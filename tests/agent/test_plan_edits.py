"""The plan-only edit: a reply changes one function's plan and can change nothing else.

Each refusal names its code: a reply that tries to carry anything but plan items, an item the function's regions do
not take, a value out of range, a stale or spent session, and a body edit sent to a plan host or a plan reply sent to
a body host. An admitted reply leaves every function's receipt as it was, apart from the plan.
"""

import pytest

from cairn.agent.agent_tools import HANDLES, EditHost
from cairn.agent.plans import PROTOCOL, PlanHost
from cairn.compiler.cairnc import compile_source
from cairn.perf.plan_source import write_plan
from cairn.projects.project import load_project
from emitted import code_of

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
    declared = "out[i] = mix(u64(i)); } }"  # the source is the original with one plan line after spread
    assert now == SPREAD.replace(declared, declared + "\nplan spread { grain 1; lanes 8; }")


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


def test_a_function_without_a_region_or_a_function_of_that_name_has_no_plan_session():
    assert code_of(lambda: PlanHost().open(SPREAD, "walk")) == "E-PLAN"
    assert code_of(lambda: PlanHost().open(SPREAD, "nothing")) == "E-SYMBOL"


def project(root, main="import lib;\nimport other;\nfn main() -> i32 { return 0; }\n"):
    """Two modules that each declare a spread with a region; other's already has a plan."""
    (root / "src").mkdir(parents=True)
    (root / "cairn.toml").write_text(
        '[project]\nname = "twin"\nsources = ["src/lib.cairn", "src/other.cairn", "src/main.cairn"]\n'
    )
    (root / "src/lib.cairn").write_text("module lib;\n" + SPREAD.replace("fn spread", "pub fn spread"))
    (root / "src/other.cairn").write_text(
        "module other;\npub fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = u64(i); } }\n"
        "plan spread { lanes 2; }\n"
    )
    (root / "src/main.cairn").write_text(main)
    return load_project(root)


def receipts(source):
    return compile_source(source)[1]["functions"]


def test_a_function_of_a_named_module_takes_a_plan_in_its_own_module(tmp_path):
    loaded = project(tmp_path)
    host = PlanHost()
    packet = host.open(loaded, "lib.spread", sizes=[{"n": 1e6}])
    assert set(packet["items"]) == {"grain", "lanes"} and packet["current"] == {}
    assert packet["written_in"] == {"module": "lib", "file": "src/lib.cairn", "after_line": 7}
    admitted = host.respond(reply(packet, grain=1, lanes=8))
    assert admitted["plan"] == "plan spread { grain 1; lanes 8; }" and admitted["written_in"]["module"] == "lib"
    before, after = receipts(loaded.source), receipts(host.source("lib.spread"))
    assert after["lib.spread"]["plan"] == {"grain": 1, "lanes": 8} and after["other.spread"]["plan"] == {"lanes": 2}
    assert {n: r for n, r in after.items() if n != "lib.spread"} == {
        n: r for n, r in before.items() if n != "lib.spread"
    }
    assert host.open(host.source("lib.spread"), "lib.spread")["current"] == {"grain": 1, "lanes": 8}
    assert code_of(lambda: host.open(loaded, "spread")) == "E-SYMBOL"  # two modules declare one: name its module


def test_a_plan_written_elsewhere_under_the_qualified_name_is_replaced_not_doubled(tmp_path):
    loaded = project(tmp_path, "import lib;\nfn main() -> i32 { return 0; }\nplan lib.spread { lanes 4; }\n")
    host = PlanHost()
    packet = host.open(loaded, "lib.spread")
    assert packet["current"] == {"lanes": 4}
    host.respond(reply(packet, grain=64))
    now = host.source("lib.spread")
    assert "plan lib.spread" not in now and now.count("plan spread { grain 64; }") == 1
    assert receipts(now)["lib.spread"]["plan"] == {"grain": 64}
    assert write_plan(tmp_path, "lib.spread", {"grain": 64}) == "src/lib.cairn"  # the files take the same edits
    assert "plan" not in (tmp_path / "src/main.cairn").read_text()
    assert "plan spread { grain 64; }" in (tmp_path / "src/lib.cairn").read_text()
    assert receipts(load_project(tmp_path).source)["other.spread"]["plan"] == {"lanes": 2}


def test_neither_protocol_widens_into_the_other():
    plans = PlanHost()
    packet = plans.open(SPREAD, "spread")
    body = {"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": "{ return; }"}
    assert code_of(lambda: plans.respond(body)) == "E-REQUEST"
    edits = EditHost()
    edits.open(SPREAD, "spread")
    assert code_of(lambda: edits.respond({**reply(packet, grain=1), "handle": "e1", "kind": "plan"})) == "E-REQUEST"

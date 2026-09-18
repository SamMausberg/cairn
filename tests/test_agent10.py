"""The agent layer over the 1.0 language: projection, packets, rule cards and guarded edits."""

from pathlib import Path

import pytest
import test_concurrency as concurrency
import test_language as language

from cairn.agent_tools import PROTOCOL, EditSession, canonical_source
from cairn.cairnc import Diagnostic, compile_source
from cairn.sketches import Sketch
from cairn.teaching import CARDS, select_cards

STD = Path(__file__).resolve().parents[1] / "src/cairn/std"
PROGRAMS = {
    "breadth": language.PRELUDE + language.MAIN,
    "dynamic": language.DYNAMIC,
    "tasks": concurrency.HELPERS + concurrency.TASKS,
    "device": concurrency.DEVICE,
    **{"std." + p.stem: p.read_text() for p in sorted(STD.glob("*.cairn"))},
}
KERNEL = """
fn weight(v:u64) -> u64 pure = mul_wrap(v, 3);
fn scale(n:usize, out:rw<u64>[n], x:ro<u64>[n]) {
  parallel i in n { out[i] = weight(x[i]) + 1; }
}
fn hidden(v:u64) -> u64 pure = v;
"""


@pytest.mark.parametrize("name", PROGRAMS)
def test_projection_keeps_native_code_and_is_idempotent(name):
    source = PROGRAMS[name]
    canonical = canonical_source(source)
    assert compile_source(canonical)[0] == compile_source(source)[0]
    assert canonical_source(canonical) == canonical


def edit(session, body):
    return session.check({"protocol": PROTOCOL, "session": session.session, "kind": "body", "replacement": body})


def test_lane_edits_are_admitted_only_when_race_free_and_within_effects():
    session = EditSession(KERNEL, "scale")
    packet = session.packet()
    assert {"parallel", "effects", "views"} <= set(packet["rule_cards"]) and "weight" in packet["dependencies"]
    assert "par:host" in packet["allowed_effects"]
    candidate, receipt = edit(session, "{ parallel i in n { out[i] = weight(x[i]) + 2; } }")
    assert receipt["status"] == "typed" and "+ 2" in candidate
    for body, code in [
        ("{ parallel i in n { out[i] = out[0]; } }", "E-PARALLEL-RACE"),
        ("{ let mut t:u64 = 0; parallel i in n { t = t + x[i]; } }", "E-PARALLEL-WRITE"),
        ("{ buffer tmp:u64[n] = zeroed; parallel i in n { out[i] = tmp[i]; } }", "E-EFFECT-EXPANSION"),
        ("{ let t = spawn weight(1); out[0] = wait(t); }", "E-EFFECT-EXPANSION"),
        ("{ out[0] = hidden(x[0]); }", "E-CONTEXT-CLOSURE"),
    ]:
        with pytest.raises(Diagnostic) as e:
            edit(session, body)
        assert e.value.data["code"] == code


def test_packets_disclose_generic_origins_and_feature_cards():
    session = EditSession(language.PRELUDE + language.MAIN, "main")
    packet = session.packet()
    shown = {c.get("symbol") for c in packet["context"]}
    assert {"push", "vec_new", "largest", "main"} <= shown  # Templates, not their mangled instances.
    assert {"generics", "owners", "sums", "views"} <= set(packet["rule_cards"])
    assert "push[u64]" in packet["dependencies"]


def test_templates_are_not_edit_targets_but_their_callers_are():
    with pytest.raises(Diagnostic) as e:
        EditSession(language.PRELUDE + language.MAIN, "push")
    assert e.value.data["code"] == "E-EDIT-PROFILE"


def test_named_choice_inside_a_lane():
    sketch = Sketch(KERNEL, "scale").hole("bias", "1")
    assert "+ (7)" in sketch.fill(bias="7").source
    with pytest.raises(Diagnostic):
        sketch.fill(bias="out[0]")


def test_every_card_is_reachable_from_tokens():
    sample = (
        "module m; import std.core (Option); pub trait T { fn f(self:ro<Self>) -> f32; } extern fn g() effects(io);"
        "fn h[K:nat](d:ro<dyn T>, n:usize, x:rw<u64>[n]@device) { unsafe { g(); } let t = spawn h(); wait(t);"
        " let b = Buf[u64](n); defer k(b); buffer s:u64[n] = zeroed; let c = compact x for i in n where true yield 1;"
        " parallel i in n { } apply(|v:u64| -> u64 { return v; }); match o { Option.None => {} } }"
        "family q = h[1..2]; derive wire for P;"
    )
    assert set(select_cards(sample, has_views=True, has_records=True, has_sums=True)) == set(CARDS)

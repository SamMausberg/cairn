"""The implementation session: an agent submits a new implementation of a pinned reference as JSON, and the host
admits it only when it validates against the reference on boundary inputs.

The exchange is the raw text a model would send. Each refusal names its code: a submission that reaches for the
reference, a tolerance, the test policy or the permitted inputs, one that is not one implementation of the pinned
reference, a stale handle, a signature the compiler refuses, and a wrong implementation, refused with its shrunk
input and kept as a regression. Every submission, admitted or refused, reaches the history seam.
"""

import json

import pytest

from cairn.agent.implementations import PROTOCOL, ImplementationHost
from cairn.compiler.cairnc import compile_source
from emitted import code_of

SOURCE = """fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut s:u64 = 0;
  for i in 0..n { s += xs[i]; }
  return s;
}

fn main() -> i32 {
  let mut xs = Buf[u64](6);
  for i in 0..6 { xs[i] = u64(i); }
  if total(xs) != 15 { return 1; }
  return 0;
}
"""
POLICY = {"tolerance": {"absolute": 0.0, "relative": 0.0}, "domain": {"largest_extent": 48}, "budget": 64}
BY4 = """fn total_by4(n:usize, xs:ro<u64>[n]) -> u64 implements total when n % 4 == 0 {
  let mut a:u64 = 0;
  let mut b:u64 = 0;
  for k in 0..n / 4 { a += xs[4 * k] + xs[4 * k + 1]; b += xs[4 * k + 2] + xs[4 * k + 3]; }
  return a + b;
}"""
# Wrong at every odd length: the last element is dropped.
PAIRS = """fn total_pairs(n:usize, xs:ro<u64>[n]) -> u64 implements total {
  let mut s:u64 = 0;
  for k in 0..n / 2 { s += xs[2 * k] + xs[2 * k + 1]; }
  return s;
}"""


def submit(handle: str, source: str, **extra) -> str:
    return json.dumps({"protocol": PROTOCOL, "handle": handle, "kind": "submit", "source": source, **extra})


@pytest.fixture
def host(tmp_path):
    seen = []
    host = ImplementationHost(regressions=tmp_path / "regressions" / "total.json", history=seen.append)
    host.seen = seen
    return host


def test_the_packet_pins_the_reference_the_tolerance_the_tests_and_the_inputs(host):
    packet = host.open(SOURCE, "total", POLICY)
    assert packet["handle"] == "i1" and packet["reference"]["symbol"] == "total"
    assert packet["reference"]["source"].startswith("fn total(") and packet["reference"]["effects"] == [
        "ffi_precondition", "read:xs", "trap"]  # fmt: skip
    pinned = packet["pinned"]
    assert pinned["tolerance"] == {"absolute": 0.0, "relative": 0.0} and pinned["domain"] == {"largest_extent": 48}
    assert pinned["tests"]["budget"] == 64 and all(len(pinned[k]) == 64 for k in pinned if k.endswith("_sha256"))
    assert "implements total when CONDITION" in packet["reply"]["source"] and "implementations" in packet["rule_cards"]


def test_a_validated_implementation_advances_the_source_and_is_not_selected(host):
    host.open(SOURCE, "total", POLICY)
    answer = json.loads(json.dumps(host.reply(submit("i1", BY4))))
    assert answer["status"] == "validated" and answer["implementation"] == "total_by4", answer
    assert answer["finite"]["status"] == "passed" and answer["finite"]["implementation_ran"] > 0
    assert answer["claim"].startswith("finite-tested") and answer["selected"] is False
    assert answer["select_with"] == "plan total use total_by4;" and len(answer["identity"]) == 64
    now = host.source("i1")
    assert BY4 in now and "plan total" not in now
    receipt = compile_source(now)[1]["functions"]["total"]
    assert set(receipt["implementations"]) == {"total_by4"} and "runs" not in receipt
    assert [e["status"] for e in host.seen] == ["validated"] and host.seen[0]["identity"] == answer["identity"]


def test_a_wrong_implementation_is_refused_with_its_shrunk_input_and_kept(host, tmp_path):
    host.open(SOURCE, "total", POLICY)
    refusal = host.reply(submit("i1", PAIRS))
    assert refusal["code"] == "E-VALIDATION", refusal
    failed = refusal["finite"]["failed"]
    assert failed["inputs"] == {"n": 1, "xs": [1]}  # shrunk from the first case that told them apart
    assert failed["reference"]["return"] == 1 and failed["implementation"]["return"] == 0
    assert "At n = 1, xs = [1]" in refusal["repair_hint"]
    kept = json.loads((tmp_path / "regressions" / "total.json").read_text())
    assert kept["schema"] == "cairn.regressions/1" and kept["cases"] == [
        {"args": {"n": 1, "xs": [1]}, "why": "told total_pairs from total"}]  # fmt: skip
    assert host.source("i1") == SOURCE and host.seen[-1]["status"] == "refused"
    assert host.seen[-1]["code"] == "E-VALIDATION" and len(host.seen[-1]["identity"]) == 64


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"tolerance": {"absolute": 1e-3, "relative": 0.0}}, "E-TOLERANCE"),  # loosening the tolerance
        ({"relative": 0.5}, "E-TOLERANCE"),
        ({"budget": 1}, "E-TEST-POLICY"),  # one case would pass anything
        ({"seed": 7}, "E-TEST-POLICY"),
        ({"domain": {"largest_extent": 4}}, "E-DOMAIN"),  # only the lengths it handles
        ({"assume": "n % 2 == 0"}, "E-DOMAIN"),
        ({"reference": "fn total(n:usize, xs:ro<u64>[n]) -> u64 = 0;"}, "E-REFERENCE"),
        ({"note": "please"}, "E-REQUEST"),
    ],
)
def test_a_submission_cannot_reach_what_the_host_pinned(host, extra, code):
    host.open(SOURCE, "total", POLICY)
    assert host.reply(submit("i1", BY4, **extra))["code"] == code
    assert host.source("i1") == SOURCE and host.seen[-1]["code"] == code


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("fn total(n:usize, xs:ro<u64>[n]) -> u64 = 0;", "E-REFERENCE"),  # a new definition of the reference
        (BY4 + "\nfn other(x:u64) -> u64 implements main = x;", "E-DECLARATION"),  # two implementations
        (BY4.replace("implements total", "implements main"), "E-REFERENCE"),
        (BY4 + "\ntest by4 { assert(true); }", "E-TEST-POLICY"),  # the tests are the host's
        (BY4 + "\nplan total use total_by4;", "E-DECLARATION"),  # so is which implementation runs
        (BY4 + "\nconst WIDTH:usize = 4;", "E-DECLARATION"),
        (BY4.replace("total_by4", "main"), "E-DECLARATION"),  # replacing a function of the program
        (BY4 + "\nfn main() -> i32 { return 0; }", "E-DECLARATION"),
        (BY4.replace("xs:ro<u64>[n]", "xs:ro<u64>[n]@unified"), "E-IMPL-SIGNATURE"),  # a wider input domain
        (BY4.replace("when n % 4 == 0", "when n + 4 > 8"), "E-IMPL-WHEN"),
        (BY4.replace("return a + b;", "let c = Buf[u64](1); return a + b;"), "E-IMPL-EFFECT"),
        (BY4.replace("return a + b;", "return a + total(0, xs[0..0]) + b;"), "E-IMPL-CALL"),
        ("fn total_by4(n:usize, xs:ro<u64>[n]) -> u64 implements total { return 0 }", "E-PARSE"),
    ],
)
def test_a_submission_that_is_not_one_implementation_of_the_reference_is_refused(host, source, code):
    host.open(SOURCE, "total", POLICY)
    refusal = host.reply(submit("i1", source))
    assert refusal["code"] == code, refusal
    assert host.source("i1") == SOURCE


def test_a_refusal_points_into_the_submission():
    host = ImplementationHost()
    host.open(SOURCE, "total", POLICY)
    refusal = host.reply(submit("i1", BY4.replace("when n % 4 == 0", "when n + 4 > 8")))
    assert refusal["in"] == "reply" and refusal["line"] == 1 and "n + 4 > 8" in refusal["source_line"]


def test_a_changed_implementation_replaces_its_declaration(host):
    host.open(SOURCE, "total", POLICY)
    first = host.reply(submit("i1", BY4))
    second = host.reply(submit("i1", BY4.replace("when n % 4 == 0", "when n % 8 == 0")))
    assert first["status"] == second["status"] == "validated" and first["identity"] != second["identity"]
    now = host.source("i1")
    assert now.count("fn total_by4(") == 1 and "when n % 8 == 0" in now


def test_an_unknown_handle_or_protocol_is_refused(host):
    host.open(SOURCE, "total", POLICY)
    assert host.reply(submit("i9", BY4))["code"] == "E-SESSION"
    assert host.reply(json.dumps({"protocol": "cairn.plan/1", "session": "x", "items": {}}))["code"] == "E-REQUEST"
    assert code_of(lambda: host.respond({"protocol": PROTOCOL, "handle": "i1", "kind": "body", "source": BY4})) == (
        "E-REQUEST")  # fmt: skip


def test_every_submission_is_kept_in_the_candidate_history(tmp_path):
    from cairn.agent.history import History

    host = ImplementationHost(records=tmp_path / "history")
    host.open(SOURCE, "total", POLICY)
    host.reply(submit("i1", PAIRS))
    host.reply(submit("i1", BY4, tolerance={"absolute": 1.0}))
    host.reply(submit("i1", BY4))
    kept = History(tmp_path / "history").records("total")
    assert [(r["kind"], r["candidate"]) for r in kept] == [
        ("failure", "plan total use total_pairs;"), ("failure", "submission"),
        ("validation", "plan total use total_by4;")]  # fmt: skip
    assert kept[0]["detail"]["stage"] == "validation" and kept[0]["detail"]["inputs"] == {"n": 1, "xs": [1]}
    assert kept[1]["detail"]["why"].startswith("E-TOLERANCE") and kept[2]["detail"]["evidence"] == "finite-tested"
    assert (
        kept[0]["identity"]["contract"] == kept[2]["identity"]["contract"] and kept[2]["identity"]["target"] == "host"
    )


# K elements a step; the instance at 8 drops the first element of every step, the one at 2 is right.
BY_K = """fn total_by[K:nat](n:usize, xs:ro<u64>[n]) -> u64 implements total when n % K == 0 tune K in [2, 8] {
  let mut s:u64 = 0;
  for k in 0..n / K {
    for j in 0..K { if K < 8 || j > 0 { s += xs[K * k + j]; } }
  }
  return s;
}"""


def test_a_parameterized_submission_is_admitted_only_when_every_instance_validates(tmp_path):
    from cairn.agent.history import History

    host = ImplementationHost(records=tmp_path / "history")
    host.open(SOURCE, "total", {**POLICY, "domain": {"largest_extent": 24}, "budget": 32})
    refused = host.reply(submit("i1", BY_K))
    assert refused["code"] == "E-VALIDATION" and refused["message"].startswith("total_by[8] is not validated")
    right = BY_K.replace("if K < 8 || j > 0 { s += xs[K * k + j]; }", "s += xs[K * k + j];")
    answer = host.respond(json.loads(submit("i1", right)))
    assert answer["status"] == "validated" and answer["implementation"] == "total_by"
    assert set(answer["instances"]) == {"total_by[2]", "total_by[8]"}  # each validated on its own
    assert answer["instances"]["total_by[8]"]["when"] == "n % 8 == 0"
    assert answer["instances"]["total_by[8]"]["select_with"] == "plan total use total_by[8];"
    assert "tune K in [2, 8]" in host.source("i1")
    kept = [(r["kind"], r["candidate"]) for r in History(tmp_path / "history").records("total")]
    assert kept == [("validation", "plan total use total_by[2];"), ("failure", "plan total use total_by[8];"),
                    ("validation", "plan total use total_by[2];"), ("validation", "plan total use total_by[8];")]  # fmt: skip

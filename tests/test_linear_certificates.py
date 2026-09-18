"""Exact algebra checks, corrupted-certificate rejection, and finite sanity oracles."""

from dataclasses import replace
from itertools import product

import pytest

from cairn.cairnc import compile_source
from cairn.linear_certificates import Certificate, Rule, audit_collector, check, collector_rules


def test_all_rules_and_compiler_gate():
    a = audit_collector()
    assert a["certificate_count"] == 17 and a["lean_verified"] is True and "not this Python file" in a["lean_scope"]
    _, r = compile_source("fn id(x:u64)->u64=x;")
    assert r["arithmetic_certificate"]["sha256"] == a["sha256"]


@pytest.mark.parametrize("rule,cert", collector_rules(), ids=[r.name for r, _ in collector_rules()])
def test_perturbed_goal_rejected(rule, cert):
    goal = list(rule.conclusion)
    goal[0] += 1
    assert not check(replace(rule, conclusion=tuple(goal)), cert)


@pytest.mark.parametrize("rule,cert", collector_rules(), ids=[r.name for r, _ in collector_rules()])
def test_negated_multiplier_rejected(rule, cert):
    w = list(cert.weights)
    w[0] = -1
    assert not check(rule, Certificate(tuple(w), cert.nonnegative_constant))


@pytest.mark.parametrize("value", [True, 1.0, -1, "1", 2**4097, None])
def test_malformed_number_rejected(value):
    r, c = collector_rules()[0]
    assert not check(r, Certificate((value, *c.weights[1:]), c.nonnegative_constant))
    assert not check(r, replace(c, nonnegative_constant=value))


def test_bad_shape_rejected():
    r, c = collector_rules()[0]
    assert not check(replace(r, conclusion=(0,)), c)
    assert not check(r, Certificate(()))
    assert not check(None, c)


def test_compiler_refuses_failed_certificate(monkeypatch):
    import cairn.linear_certificates as m

    monkeypatch.setattr(m, "check", lambda *_: False)
    with pytest.raises(ValueError):
        compile_source("fn id(x:u64)->u64=x;")


def test_independent_finite_sanity():
    # This sanity check is not the universal proof. The coefficient identity is.
    for rule, cert in collector_rules():
        for k, i, n, m in product(range(5), repeat=4):
            env = (1, k, i, n, m)
            eval_form = lambda f: sum(a * b for a, b in zip(f, env))
            if all(eval_form(f) >= 0 for f in rule.assumptions):
                assert eval_form(rule.conclusion) >= 0


def test_lean_status_follows_the_exact_bundle(monkeypatch):
    """A changed rule is no longer the bundle Lean checked, however valid it still is."""
    import cairn.linear_certificates as lc

    rules = lc.collector_rules()
    monkeypatch.setattr(lc, "collector_rules", lambda: rules[1:])
    assert lc.audit_collector()["lean_verified"] is False

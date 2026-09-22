import json
from pathlib import Path

import pytest

from cairn.agent.agent_tools import PROTOCOL, EditSession, explain, load_json_strict
from cairn.agent.projection import canonical_source, semantic_ast
from cairn.compiler.cairnc import Diagnostic, Parser, compile_source
from emitted import code_of

R = Path(__file__).resolve().parents[2]

S = "fn step(x:u64)->u64 { return add_wrap(x,1); }\nfn caller(x:u64)->u64{return step(x);}\nfn other(x:u64)->u64{return mul_wrap(x,2);}\n"


def request(s, body):
    return {"protocol": PROTOCOL, "session": s.session, "kind": "body", "replacement": body}


def expression(s, old, new):
    key = next(k for k, v in s.sites.items() if v["source"] == old)
    return {"protocol": PROTOCOL, "session": s.session, "kind": "expr", "site": key, "replacement": new}


def rejected(code, s, r):
    assert code_of(lambda: s.check(r)) == code


@pytest.mark.parametrize(
    "path", ["examples/basics/native.cairn", "examples/basics/family.cairn", "examples/basics/wire.cairn"]
)
def test_projection(path):
    original = (R / path).read_text()
    view = canonical_source(original)
    assert view == canonical_source(view)
    assert compile_source(original)[0] == compile_source(view)[0]
    assert semantic_ast(Parser(original).parse()) == semantic_ast(Parser(view).parse())


def test_familiar_mutability():
    a = "fn f(x:u64)->u64 {reg z=x; z=add_wrap(z,1); return z;}"
    assert compile_source(a)[0] == compile_source(a.replace("reg", "let mut"))[0]


def test_named_parameters_remapped():
    source = "fn fill(n:usize,dest:rw<u64>[n]@host,src:ro<u64>[n]@host){each i in n {dest[i]=src[i];}} fn top(n:usize,output:rw<u64>[n]@host,input:ro<u64>[n]@host){fill(n,output,input);}"
    _, r = compile_source(source)
    assert r["functions"]["top"]["effects"] == ["ffi_precondition", "read:input", "trap", "write:output"]


@pytest.mark.parametrize("count", range(2, 13))
def test_recursive_permutations_reach_fixed_point(count):
    ns = [f"a{i}" for i in range(count)]
    params = ",".join(n + ":ro<u64>[n]@host" for n in ns)
    source = (
        f"fn rotate(n:usize,{params})->u64 {{let x=a0[0]; return add_wrap(x,rotate(n,{','.join(ns[1:] + ns[:1])}));}}"
    )
    _, r = compile_source(source)
    assert {"read:" + n for n in ns} <= set(r["functions"]["rotate"]["effects"])
    assert "diverge" in r["functions"]["rotate"]["effects"]


def test_component_packet_includes_callers_and_callees():
    s = EditSession(S, "step", scope="component")
    assert set(s.packet()["dependencies"]) == {"step", "caller"}
    assert len(s.packet()["context"]) == 2
    assert "other" not in json.dumps(s.packet())


def test_focused_packet_shows_the_target_and_interfaces():
    p = EditSession(S, "step").packet()
    assert p["protocol"] == "cairn.packet/2" and [c["symbol"] for c in p["context"]] == ["step"]
    assert set(p["dependencies"]) == {"caller"} and p["callers"] == ["caller"] and p["not_shown"] == ["other"]
    assert p["dependencies"]["caller"]["evidence"] == "interface" and "contract" not in p["dependencies"]["caller"]
    assert "mul_wrap(x,2)" not in json.dumps(p)


@pytest.mark.parametrize("scope", ["focused", "component"])
def test_extra_context(scope):
    s = EditSession(S, "step", include=("other",), scope=scope)
    assert {"caller", "other"} <= set(s.packet()["dependencies"])
    s.check(request(s, "{return other(x);}"))


def test_exact_outside_preservation():
    s = EditSession(S, "step")
    f = s.f
    new, r = s.check(request(s, "{return mul_wrap(x,3);}"))
    assert new[: f.body_start] == S[: f.body_start]
    assert new.endswith(S[f.end :])
    assert r["status"] == "typed"
    assert r["behavioral_tests"] == "not-run"


@pytest.mark.parametrize(
    "body",
    [
        "{return x;} fn injected()->u64{return 0;}",
        "{return x;} //comment\n fn injected()->u64{return 0;}",
        "return x;",
        "{return x;",
        "{return true;}",
        "{return missing;}",
        "{return x;} ;",
        "{return x;} #include <stdio.h>",
    ],
)
def test_bad_bodies(body):
    s = EditSession(S, "step")
    with pytest.raises(Diagnostic):
        s.check(request(s, body))


def test_stale_session():
    s = EditSession(S, "step")
    r = request(s, "{return x;}")
    r["session"] = "0" * 64
    rejected("E-SESSION", s, r)
    other = EditSession(S + "\n", "step")
    rejected("E-SESSION", other, request(s, "{return x;}"))


def test_policy_session_binding():
    s = EditSession(S, "step", {"task": "one"})
    q = EditSession(S, "step", {"task": "two"})
    rejected("E-SESSION", q, request(s, "{return x;}"))


def test_request_unknown_key():
    s = EditSession(S, "step")
    r = request(s, "{return x;}")
    r["allowed_effects"] = ["trap"]
    rejected("E-REQUEST", s, r)


@pytest.mark.parametrize("text", ['{"kind":"body","kind":"expr"}', '{"n":NaN}'])
def test_duplicate_and_nonfinite_json(text):
    assert code_of(lambda: load_json_strict(text)) == "E-REQUEST"


WRITES = "fn store(n:usize,b:rw<u64>[n]@host){b[0]=1;} fn top(n:usize,left:rw<u64>[n]@host,right:rw<u64>[n]@host){store(n,left);}"


@pytest.mark.parametrize(
    ("source", "symbol", "contract", "body", "error"),
    [
        (S, "step", None, "{return x+1;}", "E-EFFECT-EXPANSION"),  # The effect ceiling.
        (S, "step", None, "{return other(x);}", "E-CONTEXT-CLOSURE"),  # A callee the packet does not show.
        (S, "step", {"allowed_effects": ["trap"]}, "{return x+1;}", "E-CALLER-EFFECT"),  # A caller's row grows.
        ("fn f(n:usize,a:ro<u64>[n]@host)->u64{return a[0];}", "f", None, "{a[0]=1;return a[0];}", "E-WRITE-LEASE"),
        (WRITES, "top", None, "{store(n,right);}", "E-EFFECT-EXPANSION"),  # A write to another array is visible.
        (S, "step", None, "{x=1;return x;}", "E-IMMUTABLE"),  # Parameters stay immutable.
    ],
)
def test_an_edit_is_refused_with_its_code(source, symbol, contract, body, error):
    s = EditSession(source, symbol, contract)
    rejected(error, s, request(s, body))


def test_expression_expected_type():
    s = EditSession(S, "step")
    with pytest.raises(Diagnostic) as e:
        s.check(expression(s, "add_wrap(x,1)", "true"))
    d = explain(e.value)
    assert d["expected_type"] == "u64" and d["actual_type"] == "bool"


def test_expression_parenthesized_insertion():
    s = EditSession("fn f(a:u64,b:u64,c:u64)->u64{return a*b;}", "f")
    new, _ = s.check(expression(s, "b", "b+c"))
    assert "a*(b+c)" in new
    # Missing parenthesis insertion would mean a*b+c, a different AST.
    expr = Parser(new).parse().functions[0].body[0].exprs[0]
    assert expr.val == "*" and expr.args[1].val == "+"


@pytest.mark.parametrize("payload", ["x; return 0", "x) + 1", "x /*", "x; fn bad(){}"])
def test_expression_injection(payload):
    s = EditSession(S, "step")
    with pytest.raises(Diagnostic):
        s.check(expression(s, "x", payload))


def test_lexical_scope():
    source = "fn f(n:usize,x:ro<u64>[n]@host)->u64{let mut total:u64=0; for i in 0..n{let y=x[i];total=add_wrap(total,y);}return total;}"
    s = EditSession(source, "f")
    for site in s.sites.values():
        if site["source"] == "add_wrap(total,y)":
            assert set(site["bindings"]) == {"n", "x", "total", "i", "y"}
    final = max((v for v in s.sites.values() if v["source"] == "total"), key=lambda v: v["start"])
    assert set(final["bindings"]) == {"n", "x", "total"}


@pytest.mark.parametrize(
    "source",
    [
        "fn f(x:u64)->u64 {return ((x));}",
        "fn f(x:u64)->u64 {return add_wrap((x),1);}",
        "fn f(a:u64,b:u64)->u64 {return (a+b)*(a-b);}",
        "fn f(n:usize,x:ro<u64>[n]@host)->u64 {return x[(n-1)];}",
        "fn f(a:u64,b:u64)->bool{return a>b && a!=0;}",
    ],
)
def test_every_site_identity_substitution(source):
    s = EditSession(source, "f")
    before = compile_source(source)[0]
    for k, v in s.sites.items():
        r = {"protocol": PROTOCOL, "session": s.session, "kind": "expr", "site": k, "replacement": v["source"]}
        after, _ = s.check(r)
        assert compile_source(after)[0] == before


def test_generated_dependency_disclosed():
    source = "fn f[K:nat](x:u64)->u64{return mul_wrap(x,u64(K));} family scale=f[1..3]; fn top(x:u64)->u64{return scale_1(x);}"
    text = json.dumps(EditSession(source, "top", scope="component").packet())
    assert "family scale = f[1..3];" in text and "fn f[K:nat]" in text
    s = EditSession(source, "top")
    assert "fn f[K:nat]" not in json.dumps(s.packet()) and "scale_1" in s.packet()["dependencies"]
    body = s.expand(["scale_1"])["context"][0]["source"]
    assert "fn f[K:nat]" in body and "family scale=f[1..3];" not in body and "family scale = f[1..3];" in body


def test_unrelated_template_not_editable():
    with pytest.raises(Diagnostic):
        EditSession("fn f[K:nat]()->usize{return K;}family x=f[1..3];", "f")


def test_candidate_filter():
    s = EditSession(S, "step")
    k = next(k for k, v in s.sites.items() if v["source"] == "add_wrap(x,1)")
    results = s.candidates(k, ["x", "false", "ghost", "x+1"])
    assert [r["status"] for r in results] == ["typed", "rejected", "rejected", "rejected"]


@pytest.mark.parametrize("contract", [[], False, 0, ""])
def test_contract_must_be_an_object(contract):
    assert code_of(lambda: EditSession("fn f(x:u64)->u64{return x;}", "f", contract)) == "E-CONTRACT"

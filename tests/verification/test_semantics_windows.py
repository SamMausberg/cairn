"""An owner that the paths of a function leave in different storage, and the native run of every verdict.

A join of paths once kept the first path's storage for every path, so an owner given new storage on another path kept
its old length, and `cairn verify` called two programs that differ natively `smt-equivalent`. Each generated program
here moves owners about under branches, loops and calls, and its translation must agree with a native build on every
input. Every `smt-equivalent` answer is also run natively on validation's boundary inputs before it is given, and a
difference found there is a translator fault, never an equivalence.
"""

import ctypes
import random

import pytest
from test_semantics import check, refute

from cairn.compiler.cairnc import compile_source
from cairn.verify.scalar import symbolic
from cairn.verify.scalar.semantics import equivalent
from emitted import library

# The reproduction: natively f(4, 0, true) is 0 and n is 4, and the join once answered smt-equivalent.
REASSIGNED = """fn f(n:usize, m:usize, c:bool) -> usize {
  let mut d = Buf[u64](n);
  for i in 0..1 { if c { d = Buf[u64](m); } }
  return len(d);
}"""
LENGTH = "fn f(n:usize, m:usize, c:bool) -> usize { return n; }"
CHOSEN = "fn f(n:usize, m:usize, c:bool) -> usize { if c { return m; } return n; }"
GROW = "fn grow(d:rw<Buf[u64]>, k:usize) { d = Buf[u64](k); }\n"


def test_an_owner_one_path_gives_new_storage_keeps_each_path_s_length():
    r = refute(REASSIGNED, LENGTH)
    assert r["counterexample"]["c"] and r["counterexample"]["n"] != r["counterexample"]["m"]
    check(REASSIGNED, CHOSEN)
    branch = REASSIGNED.replace("for i in 0..1 { if c { d = Buf[u64](m); } }", "if c { d = Buf[u64](m); }")
    refute(branch, LENGTH)
    check(branch, CHOSEN)


def test_an_owner_a_callee_gives_new_storage_comes_back_new():
    """A callee that gives the owner it was lent new storage on some paths: the caller sees each path's length."""
    grown = (
        GROW
        + "fn f(n:usize, m:usize, c:bool) -> usize { let mut d = Buf[u64](n); if c { grow(d, m); } return len(d); }"
    )
    refute(grown, GROW + LENGTH)
    check(grown, GROW + CHOSEN)
    inside = GROW.replace("{ d = Buf[u64](k); }", "{ if k > 1 { d = Buf[u64](k); } }")
    both = inside + "fn f(n:usize, m:usize, c:bool) -> usize { let mut d = Buf[u64](n); grow(d, m); return len(d); }"
    check(both, inside + "fn f(n:usize, m:usize, c:bool) -> usize { if m > 1 { return m; } return n; }")


SIZES = ["n", "m", "n + 1", "2"]
CONDITIONS = ["c", "!c", "n < m", "m == 1", "len(d) > len(e)"]


def statements(rng: random.Random, depth: int, names: list[int]) -> str:
    """Statements that move the two owners d and e about and write what they hold. At most one loop, at the top: every
    loop is unrolled in full and each iteration joins its paths, so a second loop, or one inside another, multiplies
    what Z3 is given without testing anything new."""
    out = []
    for _ in range(rng.randint(2, 4) if depth == 0 else rng.randint(1, 2)):
        looped = depth == 0 and any(x < 0 for x in names)
        pick, owner = rng.randrange(8 if depth == 0 and not looped else 6 if depth < 2 else 5), rng.choice("de")
        names.append(len(names))
        k = names[-1]
        if pick == 0:
            out.append(f"{owner} = Buf[u64]({rng.choice(SIZES)});")
        elif pick == 1:
            out.append(f"grow({owner}, {rng.choice(SIZES)});")
        elif pick == 2:
            out.append("swap(d, e);")
        elif pick == 3:
            out.append(f"let t{k} = take({owner});")
        elif pick == 4:
            out.append(f"if len({owner}) > 0 {{ {owner}[0] = add_wrap({owner}[0], {k + 1}); }}")
        elif pick == 5:
            inner = statements(rng, depth + 1, names)
            out.append(f"if {rng.choice(CONDITIONS)} {{ {inner} }} else {{ {statements(rng, depth + 1, names)} }}")
        else:
            names.append(-1)  # the one loop
            body = statements(rng, 2, names)
            if pick == 6:
                out.append(f"for i{k} in 0..{rng.randint(0, 3)} {{ {body} }}")
            else:
                out.append(f"let mut w{k}:usize = 0; while w{k} < {rng.randint(0, 2)} {{ {body} w{k} += 1; }}")
    return " ".join(out)


def generated(seed: int) -> str:
    rng = random.Random(seed)
    body = statements(rng, 0, [])
    return (
        GROW + "fn f(n:usize, m:usize, c:bool) -> u64 {\n"
        "  let mut d = Buf[u64](n);\n  let mut e = Buf[u64](m);\n"
        f"  {body}\n"
        "  let mut s:u64 = 0;\n  for i in 0..len(d) { s = add_wrap(s, d[i]); }\n"
        "  return add_wrap(add_wrap(u64(len(d)) * 64, u64(len(e)) * 8), s);\n}\n"
    )


INPUTS = [(n, m, c) for n in range(4) for m in range(4) for c in (False, True)]


@pytest.mark.parametrize("seed", range(24))
def test_generated_owner_moves_translate_as_a_native_build_runs_them(tmp_path, seed):
    """The program against a table of what its native build returned on every input the precondition admits: Z3
    must find them equivalent, so the translation says what the machine did on each of them."""
    source = generated(seed)
    native = library(tmp_path, compile_source(source)[0], "clang++")
    native.cf_f.argtypes, native.cf_f.restype = [ctypes.c_size_t, ctypes.c_size_t, ctypes.c_bool], ctypes.c_uint64
    table = "".join(
        f"  if n == {n} && m == {m} && {'c' if c else '!c'} {{ return {native.cf_f(n, m, c)}; }}\n"
        for n, m, c in INPUTS
    )
    ran = f"fn f(n:usize, m:usize, c:bool) -> u64 {{\n{table}  return 0;\n}}\n"
    check(source, ran, assume="n <= 3 && m <= 3", timeout_ms=30000)


def test_a_verdict_is_run_natively_on_boundary_inputs_before_it_stands():
    r = check(REASSIGNED, CHOSEN)
    assert r["native_replay"]["status"] == "agrees" and r["native_replay"]["cases"] > 0, r["native_replay"]
    record = "struct P { a:u64; }\nfn f(p:P) -> u64 = p.a;"
    r = check(record, record)
    assert r["native_replay"]["status"] == "not-run" and "scalars and views of scalars" in r["native_replay"]["reason"]


def test_a_difference_the_translation_loses_is_a_translator_fault(monkeypatch):
    """With the old join put back, Z3 is handed a translation that has lost the difference; the native run finds it,
    and the answer says the verifier is at fault instead of calling the two equivalent."""
    monkeypatch.setattr(symbolic.Symbolic, "window", lambda self, last, chosen: chosen[0][1] if chosen else last)
    r = equivalent(REASSIGNED, LENGTH, "f")
    assert r["status"] == "translator-fault", r
    assert r["native_replay"]["status"] == "disagrees"
    case, told = r["counterexample"], r["concrete"]
    assert told["reference"]["return"] == (case["m"] if case["c"] else case["n"]) != told["candidate"]["return"]

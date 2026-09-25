"""Who reaches a statement of a cooperative region together (compiler/cooperative/cooperative.py, `Reach`).

A barrier needs every thread of its block and a warp operation every thread of its warp, so any value that decides
whether a thread gets there must be the same in all of them. Each program below makes such a value differ from thread
to thread in a way the rule once missed, and each is refused with the code of the operation it guards; the controls
beside them keep what the rule can still show.
"""

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import refused

HELPERS = "fn put(x:rw<usize>, v:usize) { x = v; }\nfn call(f:ro<fn()>) { f(); }\n"
HEAD = HELPERS + "fn f(g:usize, live:ro<Atomic[u64]>) {\n  blocks b in g threads t in 64 {\n"
TAIL = "  }\n}\n"
CHAIN = "".join(f"let mut x{k}:usize = 0;\n" for k in range(10)) + "for k in 0..20 {\n  if x0 == 1 { barrier; }\n"
CHAIN += "".join(f"  x{k} = x{k + 1};\n" for k in range(9)) + "  x9 = t;\n}\n"


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-COOP-BARRIER", "let mut k:usize = 0;\nput(k, t);\nif k == 0 { barrier; }\n"),
        ("E-COOP-BARRIER", "let mut k:usize = 0;\ncall(|| { k = t; });\nif k == 0 { barrier; }\n"),
        ("E-COOP-BARRIER", "let mut a = Array[usize, 2]();\na[0] = t;\nif a[0] == 0 { barrier; }\n"),
        ("E-COOP-BARRIER", "let mut a = Array[usize, 2]();\na[t % 2] = 1;\nif a[0] == 1 { barrier; }\n"),
        ("E-COOP-BARRIER", "let mut k:usize = t;\nlet mut z:usize = 0;\nswap(k, z);\nif z == 0 { barrier; }\n"),
        ("E-COOP-BARRIER", "if live.fetch_add(1, Order.relaxed) == 0 { barrier; }\n"),
        ("E-COOP-BARRIER", CHAIN),
        ("E-COOP-WARP", "if t < 5 && shuffle_xor(t, 1) == 0 { let q = 1; }\n"),
        ("E-COOP-WARP", "let k = t;\nif k.max(0) == 0 { let w = shuffle(t, 0); }\n"),
        (
            "E-COOP-WARP",
            "let mut k:usize = 0;\nwhile shuffle_xor(k, 1) < 4 {\n  if t == 3 { break; }\n  k = k + 1;\n}\n",
        ),
    ],
    ids=["rw borrow", "closure", "element", "index", "swap", "atomic", "nine rounds", "&&", "receiver", "while"],
)
def test_a_value_that_differs_between_threads_decides_no_collective(code, body):
    refused(code, HEAD + body + TAIL)


def test_what_still_holds_for_the_whole_block():
    """A value passed to a primitive by value, a local written the same in every thread, and an element written at
    one index are still the block's."""
    compile_source(HEAD + "let mut k:usize = 0;\nlet z = min(k, t);\nif k == 0 { barrier; }\n" + TAIL)
    compile_source(HEAD + "let mut a = Array[usize, 2]();\na[1] = g;\nif a[1] == 3 { barrier; }\n" + TAIL)
    compile_source(HEAD + "let mut k:usize = 0;\nput(k, g);\nif k == 1 { barrier; }\n" + TAIL)
    compile_source(HEAD + "if t < 32 && shuffle_xor(t, 1) == 0 { let q = 1; }\n" + TAIL)


LANE = 'asm ptx sm_75 "mov.u32 %0, %%laneid;" (out lane:u32);'


def test_what_typed_assembly_computes_differs_between_threads():
    """%laneid is each thread's own, and the checker cannot see what an instruction computes."""
    region = "fn f(g:usize, n:usize, out:rw<u32>[n]@device) {\n  blocks b in g threads t in 32 {\n    unsafe {\n"
    region += f"      {LANE}\n      BODY\n    }}\n    if b * 32 + t < n {{ out[b * 32 + t] = 1; }}\n  }}\n}}\n"
    refused("E-COOP-WARP", region.replace("BODY", "if lane == 0 { let w = shuffle(t, 0); }"))
    compile_source(region.replace("BODY", "let w = shuffle(lane, 0);"))


@pytest.mark.parametrize(
    "body",
    [
        "shared s:u64[64] = zeroed;\ncall(|| { s[0] = u64(t); });\n",
        "call(|| { out[0] = u64(t); });\n",
        "pipeline tiles:u64[64] depth 1;\ncall(|| { let v = tiles[t]; });\n",
    ],
    ids=["shared", "outside", "pipeline"],
)
def test_a_closure_names_no_array_the_rules_keep_apart(body):
    """A closure may run in any phase and any thread: neither the phase rule nor the global rule can place what it
    touches, so it names no shared array, pipeline or array from outside."""
    source = HELPERS + "fn f(g:usize, out:rw<u64>[g]) {\n  blocks b in g threads t in 64 {\n" + body + TAIL
    assert "closure" in refused("E-COOP-UNDECIDED", source)["message"]
    compile_source(source.replace(body, "let mut k:usize = 0;\ncall(|| { k = t; });\n"))

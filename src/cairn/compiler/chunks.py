"""Vector chunks: which arrays a device region's lanes may move W elements at a time, and the lowering that does.

`plan f { vector W; }` asks each lane of f's device regions to run W adjacent indices. The plan asks; this module
decides, from the checked tree, which arrays that turns into one W-wide load and store per lane:

- every use of the array in the region is an element at exactly the lane's index, `x[i]`, whose bounds guard the
  checker discharged, so a chunk read before the body runs reads only elements the body would have read, and no
  guard can fail between the chunk's load and the element it stands for;
- its element is a scalar or a storage float, and W of them fit one access of at most 16 bytes, the widest a lane
  moves at once.

A lane's writes land in its chunk and the chunk is stored after the body, so an element the body leaves alone is
written back as it was loaded; the lane rule makes element `[i]` of anything written this lane's alone, so no
other lane can see the difference. A region with none of these arrays is refused (E-PLAN): the plan would change
nothing. Arrays used any other way stay the ordinary pointer accesses they were.

At launch one check decides the whole region: when every chunked pointer sits on its chunk's width, the lanes run
chunks, and the last indices past a whole chunk run one at a time; when any does not, as a part `x[1..n]` of an
aligned buffer may not, the region runs its scalar lanes. Every index runs exactly once either way, so the result
is the one the unplanned region computes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .tree import FLOAT, INT, STORAGE, Expr, Stmt, fail, is_view, nested

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

WIDEST = 16  # bytes one lane moves in a single access (LDG.128, STG.128)
ELEMENTS = {"bool", *INT, *FLOAT, *STORAGE}


def exprs(ss: list[Stmt]):
    """Every expression under statements, each node once, outermost first."""

    def under(e: Any):
        if isinstance(e, Expr):
            yield e
            for a in e.args:
                yield from under(a)

    for s in ss:
        for e in s.exprs:
            yield from under(e)
        yield from exprs(nested(s))


def assignments(ss: list[Stmt]):
    for s in ss:
        if s.tag == "assign":
            yield s
        yield from assignments(nested(s))


def chunkable(s: Stmt) -> dict[str, tuple[Any, bool, bool, Expr]]:
    """The arrays the lanes of region s touch only as `x[i]`, each with its element type, whether its chunk is
    loaded first (the body reads it, and a compound assignment reads its place too, or writes it anywhere but a plain
    `x[i] = e` at the top of the body, which every lane runs), whether it is stored after (the body writes it), and the
    view's own name as the body wrote it."""
    names = {name for name, *_ in s.touched}
    indexed: dict[str, list[Expr]] = {}
    seen: dict[str, int] = {}
    for e in exprs(s.body):  # every appearance of each name, however it is used, beside its uses as an element
        if e.tag == "name" and e.val in names:
            seen[e.val] = seen.get(e.val, 0) + 1
        if e.tag == "index" and e.args[0].tag == "name" and e.args[0].val in names:
            indexed.setdefault(e.args[0].val, []).append(e)
    targets = {id(a.exprs[0]) for a in assignments(s.body)}
    top = {id(a.exprs[0]) for a in s.body if a.tag == "assign" and not a.op}
    chosen: dict[str, tuple[Any, bool, bool, Expr]] = {}
    for name, uses in indexed.items():
        at_i = all(u.args[1].tag == "name" and u.args[1].val == (s.binder or s.name) and u.established for u in uses)
        element, view = uses[0].ty, uses[0].args[0]
        if not at_i or seen.get(name) != len(uses) or not is_view(view.ty) or element.name not in ELEMENTS:
            continue
        loaded, stored = any(id(u) not in top for u in uses), any(id(u) in targets for u in uses)
        chosen[name] = (element, loaded, stored, view)
    return chosen


def vectored(c: Checker, s: Stmt, width: int, token: Any):
    """Choose what `vector W` chunks in the device region s, or refuse the plan."""
    if s.fuse:
        fail("E-PLAN", "vector chunks one region's lanes and fuse joins regions; a plan takes one or the other.", token)
    chosen = chunkable(s)
    for name, (element, *_) in chosen.items():
        if width * c.sizeof(element) > WIDEST:
            most = WIDEST // c.sizeof(element)
            fail("E-PLAN", f"vector {width} would move {width * c.sizeof(element)} bytes of {name} at once; a lane "
                 f"moves at most {WIDEST}, so {element.display()} takes vector {most} at most.", token)  # fmt: skip
    if not chosen:
        fail("E-PLAN", "vector chunks arrays a device region touches only at [i], each guard discharged, and this "
             "region touches none that way: the plan would change nothing.", token)  # fmt: skip
    s.vector = width
    s.chunked = tuple((name, *chosen[name]) for name in sorted(chosen))


def lower(g: Emitter, s: Stmt, extent: str, scalar: str, schedule: list[int], unroll: int):
    """The launch of a vectored region: its scalar lanes, one lane of W adjacent indices over chunks, and the one
    alignment check that picks between them for the whole region."""
    width, pointers = s.vector, {name: g.pointer(view)[0] for name, *_, view in s.chunked}

    def chunk():
        for name, element, load, _, _ in s.chunked:
            kind = f"cr::gpu::Chunk<{g.type(element)}, {width}>"
            g.put(
                f"auto cr_c_{name} = {kind}::load({pointers[name]} + cr_base);" if load else f"{kind} cr_c_{name}{{}};"
            )
        saved = dict(g.scalar)
        g.scalar |= {name: f"cr_c_{name}[cr_k]" for name, *_ in s.chunked}

        def each():
            g.put(f"[[maybe_unused]] const std::size_t v_{s.binder or s.name} = cr_base + cr_k;")
            g.block(s.body)

        g.put("#pragma unroll")
        g.nest(f"for (unsigned cr_k = 0; cr_k < {width}; ++cr_k) {{", each)
        g.scalar = saved
        for name, _, _, stored, _ in s.chunked:
            if stored:
                g.put(f"cr_c_{name}.store({pointers[name]} + cr_base);")

    chunks = g.inner(lambda: "[=] CR_DEVICE(std::size_t cr_base)", chunk)
    aligned = f"cr::gpu::aligned<{width}>({', '.join(pointers.values())})"
    entry = f"cr::gpu::launch_vector<{width}{f', {unroll}' if unroll > 1 else ''}>"
    g.put(f"{entry}({extent}, {aligned}, {scalar}, {chunks}{''.join(f', {x}' for x in schedule)});")

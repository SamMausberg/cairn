"""What the checker has established about usize values where it stands, for lowering to use.

A fact is an edge `x - y <= k` between two atoms. An atom is ZERO, an immutable usize name, a usize
field reached from an immutable local (`c.rows`), the length of an owner reached that way (`len(data)`,
or the field a record declares as its extent), or such an atom times a positive constant (`b*256`). A
loop, lane or collector binder brings its bounds, a `let` brings what bounds its initializer (an owner's
length for `Buf[T](n)`), a branch brings its condition, the left side of `&&` (true) or `||` (false) brings
itself to the right side, a collector's predicate to its projection, and an `if` with one arm that leaves gives
the other arm's condition to the rest of the block. Facts are dropped with the block that made them and name
only values that cannot change while they are in scope, so a fact holds wherever it is visible. A fact
between plain atoms also holds scaled by any constant a product atom names, so `b < k` gives
`b*256 + 256 <= k*256`.

A guard is discharged when the shortest path through those facts shows it cannot fail: an index
below its view's extent, a usize `+` that stays below the maximum, a usize `-` whose right side is
no larger than its left, a shift count below the width, a conversion whose operand fits, a part
`x[lo..hi]` with `lo <= hi <= len(x)` whose extent is `hi - lo`. The site still counts as a syntactic check
site and its function's row still says `trap`; lowering omits the guard, and the receipt counts it under
`discharged_check_sites`. Nothing here changes which programs are accepted.

Every fact remembers its origin, and a discharged site keeps the facts its decision used (`Expr.proof`).
This module only proposes: `verify/elision.py` checks each proof on its own terms before lowering acts on it.

The same facts place a lane's accesses (`window`): lane `b` owns the block `[b*S, b*S + S)` of an array
lanes write when every access it makes there is shown to stay inside it, which the race rule in
`concurrency.region` accepts as it accepts `x[b]`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..syntax.tree import BITS, SIGNED, UNSIGNED, USIZE, Expr, is_view, negated_literal

CHECKED = {"+", "-", "*", "/", "%"}  # The binary operators lowering guards on integers.
GUARDED_CALLS = {*BITS, "shr", "shl_wrap"}  # Conversions to an integer, and shifts.

if TYPE_CHECKING:
    from .checking import Checker

ZERO = ""  # The atom whose value is 0.
MAX = 2**64 - 1
WIDEST = 4  # How many bounds of one expression are kept; more would only cost time.
NEGATED = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!=", "!=": "=="}
Term = tuple[str, int]  # An atom plus a constant.
# Where a fact comes from: ("binder", s), ("let", s), ("arm", s, truth), ("exit", s, truth), ("left", e, truth),
# ("predicate", s) or ("kept", s), naming the statement or expression that makes it true.
Origin = tuple[Any, ...]


class Fact(tuple):
    """`x - y <= k`, and the origin that makes it true; a scaled copy keeps the fact it was scaled from."""

    origin: Origin | None
    base: Any

    def __new__(cls, x: str, y: str, k: int, origin: Origin | None = None, base: Any = None):
        fact = super().__new__(cls, (x, y, k))
        fact.origin, fact.base = origin, base
        return fact

    def __getnewargs__(self) -> tuple[str, str, int]:
        """What a copy of the fact is made from; its origin and base come back with its other attributes."""
        return self[0], self[1], self[2]


def fixed(c: Checker, e: Expr) -> str | None:
    """The identity of a local, or a chain of its fields, whose value cannot change while it is in scope."""
    names: list[str] = []
    while e.tag == "field":
        names, e = [e.val, *names], e.args[0]
    b = c.env.get(e.val) if e.tag == "name" else None
    return None if b is None or b.mutable or b.ty.mode == "rw" else ".".join([e.val, *names])


def atom(c: Checker, e: Expr) -> Term | None:
    b = c.env.get(e.val) if e.tag == "name" else None
    if b is not None and b.constant is not None:
        return ZERO, b.constant
    name = fixed(c, e) if e.ty == USIZE else None
    return (name, 0) if name else None


def exact(c: Checker, e: Expr) -> Term | None:
    """The value of a usize expression as one atom plus a constant, when it is that."""
    if e.ty != USIZE:
        return None
    if e.tag == "int":
        return ZERO, int(e.val)
    if e.tag in {"name", "field"}:  # A named constant is its literal.
        return exact(c, e.ref) if isinstance(e.ref, Expr) else atom(c, e)
    if e.tag == "call" and len(e.args) == 1 and e.val in {"len", "usize"}:
        return extent(c, e.args[0]) if e.val == "len" else exact(c, e.args[0])
    if e.tag == "binary" and e.val == "*":  # A name times a positive constant is an atom of its own: `b*256`.
        (x, j), (y, k) = (exact(c, a) or (None, 0) for a in e.args)
        if x == ZERO and y == ZERO:  # two constants: `2 * BINS`
            return ZERO, j * k
        name, times = (x, k) if y == ZERO and x and not j else (y, j) if x == ZERO and y and not k else ("", 0)
        return (f"{name}*{times}", 0) if name and times > 0 else None
    if e.tag == "binary" and e.val in {"+", "-"}:
        (x, j), (y, k) = (exact(c, a) or (None, 0) for a in e.args)
        if x is not None and y == ZERO:
            return x, j + k if e.val == "+" else j - k
        if x == ZERO and y is not None and e.val == "+":
            return y, j + k
    return None


def extent(c: Checker, e: Expr) -> Term | None:
    """The element count of what is indexed, when an atom or a literal says it."""
    ty = e.ty
    if ty is None:
        return None
    if ty.name == "Array" and str(ty.args[1]).isdigit():
        return ZERO, int(ty.args[1])
    if is_view(ty):
        return (ZERO, int(ty.extent)) if ty.extent.isdigit() else atom(c, Expr("name", ty.extent, ty=USIZE))
    owner = fixed(c, e) if ty.name == "Buf" else None
    return (c.declared_extent(e) or f"len({owner})", 0) if owner else None


def bounds(c: Checker, e: Expr) -> tuple[list[Term], list[Term]]:
    """Terms a usize expression is known not to exceed, and terms it is known to reach; zero is always one."""
    if e.ty != USIZE:
        return [], []
    known = [x] if (x := exact(c, e)) else []
    high, low = list(known), [*known, (ZERO, 0)]
    if e.tag == "binary" or (e.tag == "call" and e.val == "min" and len(e.args) == 2):
        (ha, la), (hb, lb) = (bounds(c, a) for a in e.args)
        op = e.val
        if op == "+":
            high += plus(c, ha, hb)
            low += [(x, j + k) for x, j in la for y, k in lb if y == ZERO]
        elif op == "-":
            high += [(x, j - k) for x, j in ha for y, k in lb if y == ZERO]
            low += [(x, j - k) for x, j in la for y, k in hb if y == ZERO]
        elif op == "/":  # A zero divisor traps, so any quotient is at most its dividend.
            high += ha
        elif op == "%":
            high += ha + [(y, k - 1) for y, k in hb]
        elif op in {"&", "min"}:
            high += ha + hb
    elif e.tag == "call" and e.val == "usize" and len(e.args) == 1 and e.args[0].ty.name in UNSIGNED:
        high.append((ZERO, largest(e.args[0])))
    return list(dict.fromkeys(high))[:WIDEST], list(dict.fromkeys(low))[:WIDEST]


def largest(e: Expr) -> int:
    """The largest value an unsigned expression can have, from its width, literals, `&`, `%`, `/` and `min`."""
    top = 2 ** BITS[e.ty.name] - 1
    if e.tag == "int":
        return int(e.val)
    if e.tag == "call" and e.val in UNSIGNED and len(e.args) == 1 and e.args[0].ty.name in UNSIGNED:
        return min(top, largest(e.args[0]))  # A widening conversion keeps its operand's bound.
    if (e.tag == "binary" and e.val in {"&", "%", "/"}) or (e.tag == "call" and e.val == "min" and len(e.args) == 2):
        a, b = (largest(x) for x in e.args)
        return {"&": min(a, b), "min": min(a, b), "%": min(a, max(b - 1, 0)), "/": a}[e.val]
    return top


def plus(c: Checker, ha: list[Term], hb: list[Term]) -> list[Term]:
    """Upper bounds of a sum: a term plus another the facts cap at a constant, as `row + v` with v below 256."""
    out = []
    for (x, j), (y, k) in ((p, q) for p in ha for q in hb):
        if (cap := 0 if y == ZERO else ceiling(c, y)) is not None:
            out.append((x, j + k + cap))
        if x != ZERO and (cap := ceiling(c, x)) is not None:
            out.append((y, j + k + cap))
    return out


def ceiling(c: Checker, atom: str) -> int | None:
    """The least constant the facts cap an atom at, when they cap it below MAX."""
    found = path(c, atom, ZERO)
    if found is None or found[0] >= MAX:
        return None
    cite(c, found[1])
    return found[0]


def learn(c: Checker, low: Term, high: Term, strict: bool, origin: Origin | None = None):
    """Record low < high (strict) or low <= high."""
    if low[0] != high[0]:
        c.facts.append(Fact(low[0], high[0], high[1] - low[1] - strict, origin))


def distance(c: Checker, source: str, target: str) -> int | None:
    """The least k with source - target <= k that the facts in scope give."""
    found = path(c, source, target)
    return None if found is None else found[0]


def path(c: Checker, source: str, target: str) -> tuple[int, list[tuple[str, str, int]]] | None:
    """That least k by Bellman-Ford over the facts in scope, and the edges of the path that gives it.

    x - y <= k also gives x*S - y*S <= k*S for every stride S an atom multiplies by. Every usize the
    program holds is at most MAX, but a product atom is only a number: it gets no such bound of its own,
    and is bounded only through a value the program computed from it."""
    facts = [*c.facts, *scaled(c.facts, {source, target})]
    atoms = {source, target, ZERO, *(a for a, _, _ in facts), *(b for _, b, _ in facts)}
    edges = [*facts, *((a, ZERO, MAX) for a in atoms if "*" not in a), *((ZERO, a, 0) for a in atoms)]
    best: dict[str, int] = {source: 0}
    via: dict[str, tuple[str, str, int]] = {}
    for _ in range(len(atoms)):
        changed = False
        for edge in edges:
            a, b, k = edge
            if a in best and best[a] + k < best.get(b, MAX + 1):
                best[b], via[b], changed = best[a] + k, edge, True
        if not changed:
            if target not in best:
                return None
            used: list[tuple[str, str, int]] = []
            at = target
            while at != source and len(used) < len(atoms):  # Each atom's last improvement: a shortest-path tree.
                used.append(via[at])
                at = via[at][0]
            return best[target], used
    return None  # A negative cycle: contradictory facts, unreachable code, and nothing is claimed for it.


def scaled(facts: list[tuple[str, str, int]], atoms: set[str]) -> list[tuple[str, str, int]]:
    """Each fact between plain atoms, multiplied by every stride a product atom names."""
    strides = {int(a.rsplit("*", 1)[1]) for a in {*atoms, *(a for f in facts for a in f[:2])} if "*" in a}
    return [Fact(f[0] and f"{f[0]}*{n}", f[1] and f"{f[1]}*{n}", f[2] * n, getattr(f, "origin", None), f)
            for n in strides for f in facts if "*" not in f[0] + f[1]]  # fmt: skip


def cite(c: Checker, used: list[Any]):
    """Keep the facts a decision rests on, each as it was learned (a scaled copy as the fact it scales)."""
    cited = getattr(c, "cited", None)
    for f in used if cited is not None else ():
        if isinstance(f, Fact) and f.origin is not None:
            cited.append(f if f.base is None else f.base)


def at_most(c: Checker, x: Term, y: Term, slack: int = 0) -> bool:
    """Whether x - y <= slack follows from the facts in scope."""
    found = path(c, x[0], y[0])
    if found is None or found[0] > slack - x[1] + y[1]:
        return False
    cite(c, found[1])
    return True


def assume(c: Checker, cond: Expr, truth: bool = True, origin: Origin | None = None):
    """Record what a condition that has just evaluated to `truth` says about usize values."""
    if cond.tag == "unary" and cond.val == "!":
        return assume(c, cond.args[0], not truth, origin)
    if cond.tag == "binary" and cond.val in {"&&", "||"}:
        for part in cond.args if truth == (cond.val == "&&") else []:
            assume(c, part, truth, origin)
        return
    if cond.tag != "binary" or cond.val not in NEGATED or cond.args[0].ty != USIZE:
        return
    op = cond.val if truth else NEGATED[cond.val]
    a, b = cond.args if op in {"<", "<=", "=="} else cond.args[::-1]
    for zero, other in [(a, b), (b, a)] if op == "!=" else []:  # A usize that is not zero is at least one.
        if exact(c, zero) == (ZERO, 0) and (x := exact(c, other)):
            learn(c, (ZERO, 1), x, False, origin)
    for left, right in [(a, b), (b, a)][: {"!=": 0, "==": 2}.get(op, 1)]:
        for x in bounds(c, left)[1]:
            for y in bounds(c, right)[0]:
                learn(c, x, y, op in {"<", ">"}, origin)


def binder(c: Checker, name: str, lo: Expr | None, hi: Expr, strict: bool = True, origin: Origin | None = None):
    """A loop, lane or collector binder: lo <= name < hi, or name <= hi when not strict."""
    for y in bounds(c, hi)[0]:
        learn(c, (name, 0), y, strict, origin)
    for x in bounds(c, lo)[1] if lo else []:
        learn(c, x, (name, 0), False, origin)


def defined(c: Checker, name: str, value: Expr, origin: Origin | None = None):
    """An immutable name bound to a usize value, or to a new owner of that many elements. A usize value is also
    kept whole, so that `linear` can see through the name to what it was bound to."""
    if value.ty == USIZE:
        c.values[name] = (c.env.get(name), value)
    if value.tag == "call" and value.val == "Buf" and value.ty.name == "Buf" and len(value.args) == 1:
        name, value = f"len({name})", value.args[0]
    binder(c, name, value, value, strict=False, origin=origin)


def linear(c: Checker, e: Expr | str, depth: int = 4) -> dict[str, int] | None:
    """A usize expression as atoms times integers plus a constant (the ZERO entry), when it is one: `+` and `-`
    of atoms and literals, and immutable names seen through to what they were bound to. Two expressions with the
    same form have the same value wherever both evaluate without a trap."""
    if isinstance(e, str):
        return ({ZERO: int(e)} if int(e) else {}) if e.isdigit() else None
    if e.ty != USIZE:
        return None
    if e.tag == "binary" and e.val in {"+", "-"}:
        a, b = (linear(c, x, depth) for x in e.args)
        if a is None or b is None:
            return None
        sign, out = (1 if e.val == "+" else -1), dict(a)
        for name, times in b.items():
            out[name] = out.get(name, 0) + sign * times
        return {name: times for name, times in out.items() if times}
    known = c.values.get(e.val) if e.tag == "name" and depth else None
    if known and known[0] is not None and known[0] is c.env.get(e.val) and not known[0].mutable:
        return linear(c, known[1], depth - 1)
    term = exact(c, e)
    if term is None or "*" in term[0]:
        return None
    out = {term[0]: 1} if term[0] else {}
    return {**out, ZERO: term[1]} if term[1] else out


def guarded(e: Expr) -> bool:
    """Whether lowering writes a guard anywhere in `e`, a bound of a part."""
    ty = e.ty.name if e.ty is not None else ""
    site = e.tag == "index" or (e.tag == "binary" and e.val in CHECKED and ty in BITS)
    site = site or (e.tag == "unary" and e.val == "-" and ty in SIGNED and not negated_literal(e))
    site = site or (e.tag == "call" and e.val in GUARDED_CALLS)
    return (site and not e.established) or any(guarded(a) for a in e.args)


def index(c: Checker, e: Expr) -> bool:
    """An index below the extent of what it indexes."""
    c.cited = []
    n = extent(c, e.args[0])
    return n is not None and any(at_most(c, x, n, -1) for x in bounds(c, e.args[1])[0])


def part(c: Checker, e: Expr) -> bool:
    """A part `x[lo..hi]` with lo <= hi <= len(x), handed where the callee expects exactly `hi - lo` elements: its
    extent is this part's own span, or has the same linear form. Lowering writes `x + lo` and does not evaluate
    `hi` again, so `hi` may hold no guard of its own."""
    c.cited = []
    (base, lo, hi), want = e.args, e.ref
    n = extent(c, base)
    return n is not None and not guarded(hi) and want is not None and spans(c, e, want) and inside(c, lo, hi, n)


def inside(c: Checker, lo: Expr, hi: Expr, n: Term) -> bool:
    """lo <= hi <= n, from the facts in scope: `partOk` in Facts.lean."""
    (h_lo, _), (h_hi, l_hi) = bounds(c, lo), bounds(c, hi)
    return any(at_most(c, y, x) for y in h_lo for x in l_hi) and any(at_most(c, y, n) for y in h_hi)


def spans(c: Checker, e: Expr, want: Expr | str) -> bool:
    """Whether a part's extent is `hi - lo`: its own span written out, or an extent of the same linear form."""
    if isinstance(want, Expr) and want.span is e and want.tag == "binary":
        return True
    high, low, given = linear(c, e.args[2]), linear(c, e.args[1]), linear(c, want)
    if high is None or low is None or given is None:
        return False
    return {a: t for a in {*high, *low} if (t := high.get(a, 0) - low.get(a, 0))} == given


def arithmetic(c: Checker, e: Expr) -> bool:
    """A usize + that cannot pass the maximum, or a usize - that cannot go below zero."""
    c.cited = []
    (ha, la), (hb, _) = (bounds(c, a) for a in e.args)
    if e.val == "+":
        return any(at_most(c, x, (ZERO, MAX)) for x in plus(c, ha, hb))
    return e.val == "-" and any(at_most(c, y, x) for y in hb for x in la)


def shift(c: Checker, e: Expr) -> bool:
    """A shift count below the width of what it shifts."""
    c.cited = []
    return any(at_most(c, x, (ZERO, BITS[e.args[0].ty.name] - 1)) for x in bounds(c, e.args[1])[0])


def conversion(c: Checker, e: Expr) -> bool:
    """A conversion to an integer type that holds every value its operand can have."""
    c.cited = []
    src, dst = e.args[0].ty.name, e.val
    if src not in BITS or dst not in BITS or (src in SIGNED and dst not in SIGNED):
        return False
    if BITS[src] + (src not in SIGNED and dst in SIGNED) <= BITS[dst]:
        return True
    top = 2 ** (BITS[dst] - (dst in SIGNED)) - 1
    return src not in SIGNED and any(at_most(c, x, (ZERO, top)) for x in bounds(c, e.args[0])[0])


def window(c: Checker, e: Expr, binder: str) -> int | None:
    """The stride S of the one block `[binder * S, binder * S + S)` that an index, or a part `x[lo..hi]`, is
    shown to stay inside: 1 for the binder itself. Two lanes' blocks of one stride never meet."""
    if e.tag == "name" and e.val == binder:
        return 1
    strides = {int(a.split("*")[1]) for fact in c.facts for a in fact[:2] if a.startswith(binder + "*")}
    strides |= {int(t[0].split("*")[1]) for x in [e, *e.args] for t in bounds(c, x)[1] if t[0].startswith(binder + "*")}
    (low, high), room = (e.args[1:3], 0) if e.tag == "slice" else ((e, e), -1)  # A part may end where its block does.
    for stride in sorted(strides):
        base = (f"{binder}*{stride}", 0)
        if any(at_most(c, base, x) for x in bounds(c, low)[1]) and below(c, high, base, stride + room):
            return stride
    return None


def below(c: Checker, e: Expr, base: Term, room: int) -> bool:
    """e is at most base + room: from its bounds, or as `base + j` with j at most room."""
    if any(at_most(c, x, (base[0], room)) for x in bounds(c, e)[0]):
        return True
    for a, b in (e.args, e.args[::-1]) if e.tag == "binary" and e.val == "+" else []:
        start = exact(c, a)
        if start and at_most(c, start, base) and any(at_most(c, x, (ZERO, room)) for x in bounds(c, b)[0]):
            return True
    return False


def discharge(c: Checker, e: Expr, kind: str, holds: bool, proof: tuple[str, Any] | None = None) -> bool:
    """Mark a guard site whose condition was established, count it, and keep its proof: ("facts", the facts the
    decision used) unless the caller gives another, as ("span", part) for a value a part's own guard covers."""
    if holds:
        e.established = True
        e.proof = proof or ("facts", tuple(dict.fromkeys(getattr(c, "cited", None) or ())))
        c.discharged[kind] = c.discharged.get(kind, 0) + 1
    return holds

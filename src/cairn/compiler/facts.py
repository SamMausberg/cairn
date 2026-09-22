"""What the checker has established about usize values where it stands, for lowering to use.

A fact is an edge `x - y <= k` between two atoms. An atom is ZERO, an immutable usize name, a usize
field reached from an immutable local (`c.rows`), the length of an owner reached that way (`len(data)`,
or the field a record declares as its extent), or such an atom times a positive constant (`b*256`). A
loop, lane or collector binder brings its bounds, a `let` brings what bounds its initializer (an owner's
length for `Buf[T](n)`), a branch brings its condition, and an `if` with one arm that leaves gives the
other arm's condition to the rest of the block. Facts are dropped with the block that made them and name
only values that cannot change while they are in scope, so a fact holds wherever it is visible. A fact
between plain atoms also holds scaled by any constant a product atom names, so `b < k` gives
`b*256 + 256 <= k*256`.

A guard is discharged when the shortest path through those facts shows it cannot fail: an index
below its view's extent, a usize `+` that stays below the maximum, a usize `-` whose right side is
no larger than its left, a shift count below the width, a conversion whose operand fits. The site still
counts as a syntactic check site and its function's row still says `trap`; lowering omits the guard, and
the receipt counts it under `discharged_check_sites`. Nothing here changes which programs are accepted.

The same facts place a lane's accesses (`window`): lane `b` owns the block `[b*S, b*S + S)` of an array
lanes write when every access it makes there is shown to stay inside it, which the race rule in
`concurrency.region` accepts as it accepts `x[b]`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .tree import BITS, SIGNED, UNSIGNED, USIZE, Expr, is_view

if TYPE_CHECKING:
    from .checking import Checker

ZERO = ""  # The atom whose value is 0.
MAX = 2**64 - 1
WIDEST = 4  # How many bounds of one expression are kept; more would only cost time.
NEGATED = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!=", "!=": "=="}
Term = tuple[str, int]  # An atom plus a constant.


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
    d = distance(c, atom, ZERO)
    return d if d is not None and d < MAX else None


def learn(c: Checker, low: Term, high: Term, strict: bool):
    """Record low < high (strict) or low <= high."""
    if low[0] != high[0]:
        c.facts.append((low[0], high[0], high[1] - low[1] - strict))


def distance(c: Checker, source: str, target: str) -> int | None:
    """The least k with source - target <= k that the facts in scope give, by Bellman-Ford over them.

    x - y <= k also gives x*S - y*S <= k*S for every stride S an atom multiplies by. Every usize the
    program holds is at most MAX, but a product atom is only a number: it gets no such bound of its own,
    and is bounded only through a value the program computed from it."""
    facts = [*c.facts, *scaled(c.facts, {source, target})]
    atoms = {source, target, ZERO, *(a for a, _, _ in facts), *(b for _, b, _ in facts)}
    edges = [*facts, *((a, ZERO, MAX) for a in atoms if "*" not in a), *((ZERO, a, 0) for a in atoms)]
    best = {source: 0}
    for _ in range(len(atoms)):
        changed = False
        for a, b, k in edges:
            if a in best and best[a] + k < best.get(b, MAX + 1):
                best[b], changed = best[a] + k, True
        if not changed:
            return best.get(target)
    return None  # A negative cycle: contradictory facts, unreachable code, and nothing is claimed for it.


def scaled(facts: list[tuple[str, str, int]], atoms: set[str]) -> list[tuple[str, str, int]]:
    """Each fact between plain atoms, multiplied by every stride a product atom names."""
    strides = {int(a.rsplit("*", 1)[1]) for a in {*atoms, *(a for f in facts for a in f[:2])} if "*" in a}
    return [(times(a, n), times(b, n), k * n) for n in strides for a, b, k in facts if "*" not in a + b]


def times(atom: str, stride: int) -> str:
    return f"{atom}*{stride}" if atom else ZERO


def at_most(c: Checker, x: Term, y: Term, slack: int = 0) -> bool:
    """Whether x - y <= slack follows from the facts in scope."""
    d = distance(c, x[0], y[0])
    return d is not None and d <= slack - x[1] + y[1]


def assume(c: Checker, cond: Expr, truth: bool = True):
    """Record what a condition that has just evaluated to `truth` says about usize values."""
    if cond.tag == "unary" and cond.val == "!":
        return assume(c, cond.args[0], not truth)
    if cond.tag == "binary" and cond.val in {"&&", "||"}:
        for part in cond.args if truth == (cond.val == "&&") else []:
            assume(c, part, truth)
        return
    if cond.tag != "binary" or cond.val not in NEGATED or cond.args[0].ty != USIZE:
        return
    op = cond.val if truth else NEGATED[cond.val]
    a, b = cond.args if op in {"<", "<=", "=="} else cond.args[::-1]
    for zero, other in [(a, b), (b, a)] if op == "!=" else []:  # A usize that is not zero is at least one.
        if exact(c, zero) == (ZERO, 0) and (x := exact(c, other)):
            learn(c, (ZERO, 1), x, False)
    for left, right in [(a, b), (b, a)][: {"!=": 0, "==": 2}.get(op, 1)]:
        for x in bounds(c, left)[1]:
            for y in bounds(c, right)[0]:
                learn(c, x, y, op in {"<", ">"})


def binder(c: Checker, name: str, lo: Expr | None, hi: Expr, strict: bool = True):
    """A loop, lane or collector binder: lo <= name < hi, or name <= hi when not strict."""
    for y in bounds(c, hi)[0]:
        learn(c, (name, 0), y, strict)
    for x in bounds(c, lo)[1] if lo else []:
        learn(c, x, (name, 0), False)


def defined(c: Checker, name: str, value: Expr):
    """An immutable name bound to a usize value, or to a new owner of that many elements."""
    if value.tag == "call" and value.val == "Buf" and value.ty.name == "Buf" and len(value.args) == 1:
        name, value = f"len({name})", value.args[0]
    high, low = bounds(c, value)
    for y in high:
        learn(c, (name, 0), y, False)
    for x in low:
        learn(c, x, (name, 0), False)


def index(c: Checker, e: Expr) -> bool:
    """An index below the extent of what it indexes."""
    n = extent(c, e.args[0])
    return n is not None and any(at_most(c, x, n, -1) for x in bounds(c, e.args[1])[0])


def arithmetic(c: Checker, e: Expr) -> bool:
    """A usize + that cannot pass the maximum, or a usize - that cannot go below zero."""
    (ha, la), (hb, _) = (bounds(c, a) for a in e.args)
    if e.val == "+":
        return any(at_most(c, x, (ZERO, MAX)) for x in plus(c, ha, hb))
    return e.val == "-" and any(at_most(c, y, x) for y in hb for x in la)


def shift(c: Checker, e: Expr) -> bool:
    """A shift count below the width of what it shifts."""
    return any(at_most(c, x, (ZERO, BITS[e.args[0].ty.name] - 1)) for x in bounds(c, e.args[1])[0])


def conversion(c: Checker, e: Expr) -> bool:
    """A conversion to an integer type that holds every value its operand can have."""
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
    for stride in sorted(strides):
        base = (f"{binder}*{stride}", 0)
        low, high = (e.args[1], e.args[2]) if e.tag == "slice" else (e, None)
        above = any(at_most(c, base, x) for x in bounds(c, low)[1])
        if high is None and above and below(c, e, base, stride - 1):
            return stride
        if high is not None and above and below(c, high, base, stride):
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


def discharge(c: Checker, e: Expr, kind: str, holds: bool):
    """Mark a guard site whose condition was established, and count it."""
    if holds:
        e.established = True
        c.discharged[kind] = c.discharged.get(kind, 0) + 1

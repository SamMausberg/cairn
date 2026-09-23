"""The independent check of every guard lowering leaves out.

`compiler/facts.py` proposes. It marks a site whose guard it showed cannot fail (`Expr.established`) and keeps the
proof its decision used (`Expr.proof`): the facts, each naming the statement or expression that made it true, or a
part whose own guard covers the site. Nothing here calls into facts.py or trusts its reasoning.

`audit` walks every function the way it runs and keeps its own account: which names are immutable, what each
immutable usize name was bound to, and which origins are in force where. A loop, lane or collector binder is in force
in its body, a `let` and a collector's result for the rest of their block, a condition in its arm, the left side of
`&&` or `||` in the right side, a collector's predicate in its projection, and an `if` with exactly one arm that
always leaves for the rest of the block. At each site it takes the cited facts and requires, of every one, that its
origin is in force there, that it follows from that origin by the derivation written here, and that every value it
names is still immutable there. It then decides the guard again from those facts alone. A span proof holds where
the site sits inside an argument written `hi - lo` over the bounds of a part among the same call's arguments.

A site whose proof fails keeps its guard: its flag is cleared before a line is emitted, and the receipt counts it
under `refused_discharges`. `keep_all` clears every flag, which is how a conservative build keeps every guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..compiler.tree import BITS, SIGNED, UNSIGNED, USIZE, Expr, Function, Program, Stmt, is_view

ZERO = ""
MAX = 2**64 - 1
LIMIT = 64  # Bound terms kept per expression; more only costs time.
Term = tuple[str, int]
Edge = tuple[str, str, int]
CHECKED = {"+", "-", "*", "/", "%"}
KINDS = {"index": "bounds", "slice": "bounds", "binary": "overflow", "shift": "shift", "conversion": "conversion"}


@dataclass
class Name:
    fixed: bool  # Its value cannot change while it is in scope.
    value: Expr | None = None  # What an immutable `let` bound it to.
    constant: int | None = None


@dataclass
class Walk:
    """What holds at the point the walk has reached in one function."""

    fields: dict[str, dict[str, str]]
    env: dict[str, Name] = field(default_factory=dict)
    active: dict[tuple, list[Edge]] = field(default_factory=dict)  # origin key -> the facts it gives
    spans: dict[int, Expr] = field(default_factory=dict)  # node -> the part among its call's arguments it repeats
    seen: set[int] = field(default_factory=set)
    accepted: dict[str, int] = field(default_factory=dict)
    refused: dict[str, int] = field(default_factory=dict)

    # Atoms and bounds -----------------------------------------------------------------------------------------

    def path(self, e: Expr) -> str | None:
        """A name or a chain of fields, as a string, when it is one."""
        names: list[str] = []
        while e.tag == "field":
            names, e = [e.val, *names], e.args[0]
        return ".".join([e.val, *names]) if e.tag == "name" else None

    def still(self, path: str) -> bool:
        """Whether the value a path names cannot change here: its root is an immutable binding."""
        name = self.env.get(path.split(".")[0])
        return name is not None and name.fixed

    def valid(self, atom: str) -> bool:
        if atom == ZERO:
            return True
        if "*" in atom:
            base, times = atom.rsplit("*", 1)
            return times.isdigit() and int(times) > 0 and "*" not in base and self.valid(base)
        if atom.startswith("len(") and atom.endswith(")"):
            return self.still(atom[4:-1])
        return self.still(atom)

    def exact(self, e: Expr) -> Term | None:
        """A usize expression's value as one atom plus a constant, when it is that."""
        if e.ty != USIZE:
            return None
        if e.tag == "int":
            return ZERO, int(e.val)
        if e.tag == "name":
            name = self.env.get(e.val)
            if name is None:  # A global constant: the checker resolved it to its literal.
                return self.exact(e.ref) if isinstance(e.ref, Expr) and e.ref.tag == "int" else None
            if name.constant is not None:
                return ZERO, name.constant
            return (e.val, 0) if name.fixed else None
        if e.tag == "field":
            path = self.path(e)
            return (path, 0) if path and self.still(path) else None
        if e.tag == "call" and len(e.args) == 1 and e.val == "len":
            return self.extent(e.args[0])
        if e.tag == "call" and len(e.args) == 1 and e.val == "usize" and e.args[0].ty == USIZE:
            return self.exact(e.args[0])
        if e.tag == "binary" and e.val == "*":
            (x, j), (y, k) = (self.exact(a) or (None, 0) for a in e.args)
            if x and y == ZERO and not j and k > 0 and "*" not in x:
                return f"{x}*{k}", 0
            if y and x == ZERO and not k and j > 0 and "*" not in y:
                return f"{y}*{j}", 0
            return None
        if e.tag == "binary" and e.val in {"+", "-"}:
            (x, j), (y, k) = (self.exact(a) or (None, 0) for a in e.args)
            if x is not None and y == ZERO:
                return x, j + k if e.val == "+" else j - k
            if x == ZERO and y is not None and e.val == "+":
                return y, j + k
        return None

    def extent(self, e: Expr) -> Term | None:
        """How many elements what `e` names holds, as a term."""
        ty = e.ty
        if ty is None:
            return None
        if ty.name == "Array" and str(ty.args[1]).isdigit():
            return ZERO, int(ty.args[1])
        if is_view(ty):
            if ty.extent.isdigit():
                return ZERO, int(ty.extent)
            name = self.env.get(ty.extent)
            if name is not None and name.constant is not None:
                return ZERO, name.constant
            return (ty.extent, 0) if name is not None and name.fixed else None
        path = self.path(e) if ty.name == "Buf" else None
        if not path or not self.still(path):
            return None
        record = e.args[0].ty if e.tag == "field" and e.args[0].ty is not None else None
        declared = (
            self.fields.get(record.name, {}).get(e.val) if record is not None and record.mode == "value" else None
        )
        return (path.rsplit(".", 1)[0] + "." + declared if declared else f"len({path})"), 0

    def largest(self, e: Expr) -> int:
        top = 2 ** BITS[e.ty.name] - 1
        if e.tag == "int":
            return int(e.val)
        if e.tag == "call" and len(e.args) == 1 and e.val in UNSIGNED and e.args[0].ty.name in UNSIGNED:
            return min(top, self.largest(e.args[0]))
        if (e.tag == "binary" and e.val in {"&", "%", "/"}) or (
            e.tag == "call" and e.val == "min" and len(e.args) == 2
        ):
            a, b = (self.largest(x) for x in e.args)
            return {"&": min(a, b), "min": min(a, b), "%": min(a, max(b - 1, 0)), "/": a}[e.val]
        return top

    def terms(self, e: Expr, facts: list[Edge]) -> tuple[set[Term], set[Term]]:
        """Terms `e` is known not to exceed, and terms it is known to reach, where it evaluates without a trap."""
        if e.ty != USIZE:
            return set(), set()
        known = self.exact(e)
        high: set[Term] = {known} if known else set()
        low: set[Term] = {known, (ZERO, 0)} if known else {(ZERO, 0)}
        pair = e.tag == "binary" or (e.tag == "call" and e.val == "min" and len(e.args) == 2)
        if pair and len(e.args) == 2:
            (ha, la), (hb, lb) = (self.terms(a, facts) for a in e.args)
            op = e.val
            if op == "+":
                for (x, j), (y, k) in ((p, q) for p in ha for q in hb):
                    for base, other, add in ((x, y, j + k), (y, x, j + k)):
                        cap = 0 if other == ZERO else self.cap(other, facts)
                        if cap is not None:
                            high.add((base, add + cap))
                low |= {(x, j + k) for x, j in la for y, k in lb if y == ZERO}
            elif op == "-":
                high |= {(x, j - k) for x, j in ha for y, k in lb if y == ZERO}
                low |= {(x, j - k) for x, j in la for y, k in hb if y == ZERO}
            elif op == "/":
                high |= ha
            elif op == "%":
                high |= ha | {(y, k - 1) for y, k in hb}
            elif op in {"&", "min"}:
                high |= ha | hb
        elif e.tag == "call" and e.val == "usize" and len(e.args) == 1 and e.args[0].ty.name in UNSIGNED:
            high.add((ZERO, self.largest(e.args[0])))
        return set(sorted(high)[:LIMIT]), set(sorted(low)[:LIMIT])

    def cap(self, atom: str, facts: list[Edge]) -> int | None:
        d = distance(facts, atom, ZERO)
        return d if d is not None and d < MAX else None

    def linear(self, e: Expr | str, depth: int = 4) -> dict[str, int] | None:
        """A usize expression as atoms times integers plus a constant (under ZERO), through immutable `let`s."""
        if isinstance(e, str):
            return ({ZERO: int(e)} if int(e) else {}) if e.isdigit() else None
        if e.ty != USIZE:
            return None
        if e.tag == "binary" and e.val in {"+", "-"}:
            a, b = (self.linear(x, depth) for x in e.args)
            if a is None or b is None:
                return None
            out = dict(a)
            for atom, times in b.items():
                out[atom] = out.get(atom, 0) + (times if e.val == "+" else -times)
            return {atom: times for atom, times in out.items() if times}
        name = self.env.get(e.val) if e.tag == "name" else None
        if name is not None and name.fixed and name.value is not None and depth:
            return self.linear(name.value, depth - 1)
        known = self.exact(e)
        if known is None or "*" in known[0]:
            return None
        out = {known[0]: 1} if known[0] else {}
        return {**out, ZERO: known[1]} if known[1] else out

    # What an origin says ----------------------------------------------------------------------------------------

    def learn(self, out: list[Edge], low: Term, high: Term, strict: bool):
        if low[0] != high[0]:
            out.append((low[0], high[0], high[1] - low[1] - strict))

    def bound(self, name: str, lo: Expr | None, hi: Expr, strict: bool) -> list[Edge]:
        """lo <= name < hi (or <= hi)."""
        facts, out = self.facts(), list[Edge]()
        for y in self.terms(hi, facts)[0]:
            self.learn(out, (name, 0), y, strict)
        for x in self.terms(lo, facts)[1] if lo is not None else ():
            self.learn(out, x, (name, 0), False)
        return out

    def condition(self, cond: Expr, truth: bool) -> list[Edge]:
        """What a condition that evaluated to `truth` says about usize values."""
        if cond.tag == "unary" and cond.val == "!":
            return self.condition(cond.args[0], not truth)
        if cond.tag == "binary" and cond.val in {"&&", "||"}:
            both = truth == (cond.val == "&&")
            return [f for part in cond.args for f in self.condition(part, truth)] if both else []
        flips = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!=", "!=": "=="}
        if cond.tag != "binary" or cond.val not in flips or cond.args[0].ty != USIZE:
            return []
        op, (a, b), facts, out = cond.val if truth else flips[cond.val], cond.args, self.facts(), list[Edge]()
        if op in {">", ">="}:
            a, b, op = b, a, {">": "<", ">=": "<="}[op]
        if op == "!=":  # a usize that is not zero is at least one
            for zero, other in ((a, b), (b, a)):
                if self.exact(zero) == (ZERO, 0) and (x := self.exact(other)):
                    self.learn(out, (ZERO, 1), x, False)
            return out
        for left, right in [(a, b), (b, a)] if op == "==" else [(a, b)]:
            for x in self.terms(left, facts)[1]:
                for y in self.terms(right, facts)[0]:
                    self.learn(out, x, y, op == "<")
        return out

    def facts(self) -> list[Edge]:
        return [f for held in self.active.values() for f in held]

    def enter(self, key: tuple, facts: list[Edge]) -> tuple:
        self.active[key] = facts
        return key

    # The walk --------------------------------------------------------------------------------------------------

    def bind(self, name: str, fixed: bool, value: Expr | None = None):
        self.env[name] = Name(fixed, value if fixed and value is not None and value.ty == USIZE else None)

    def block(self, body: list[Stmt]):
        env, active = dict(self.env), dict(self.active)
        for s in body:
            self.stmt(s)
        self.env, self.active = env, active

    def stmt(self, s: Stmt):
        tag, es = s.tag, s.exprs
        if tag in {"let", "reg"}:
            self.expr(es[0])
            self.bind(s.name, tag == "let", es[0])
            if tag == "let":
                self.enter(("let", id(s)), self.defined(s.name, es[0]))
        elif tag in {"buffer", "stack"}:
            self.expr(es[0])
            self.bind(s.name, False)
        elif tag == "unpack":
            self.expr(es[0])
            for n in s.other_names:
                self.bind(n.val, s.op == "let")
        elif tag == "for":
            self.expr(es[0])
            self.expr(es[1])
            self.inside(s, s.name, self.bound(s.name, es[0], es[1], True), lambda: self.block(s.body))
        elif tag == "parallel":
            self.expr(es[0])
            self.inside(s, s.name, self.bound(s.name, None, es[0], True), lambda: self.block(s.body))
        elif tag == "reduce":
            self.expr(es[0])
            self.inside(s, s.binder, self.bound(s.binder, None, es[0], True), lambda: self.expr(es[1]))
            self.bind(s.name, True)
        elif tag == "scan":  # The yield and the store out[i] it feeds, both below the count.
            out, hi, value, store = es
            self.expr(out)
            self.expr(hi)
            self.inside(s, s.binder, self.bound(s.binder, None, hi, True), lambda: [self.expr(value), self.expr(store)])
            if s.name:
                self.bind(s.name, True)
        elif tag == "compact":
            out, hi, pred, value = es
            self.expr(out)
            self.expr(hi)

            def projected():
                self.expr(pred)
                key = self.enter(("predicate", id(s)), self.condition(pred, True))
                self.expr(value)
                del self.active[key]

            self.inside(s, s.binder, self.bound(s.binder, None, hi, True), projected)
            self.bind(s.name, True)
            self.enter(("kept", id(s)), self.bound(s.name, None, hi, False))
        elif tag == "if":
            cond = es[0]
            self.expr(cond)
            for branch, truth in ((s.body, True), (s.other, False)):
                key = self.enter(("arm", id(s), truth), self.condition(cond, truth))
                self.block(branch)
                del self.active[key]
            then, other = leaves(s.body), leaves(s.other)
            if then != other:  # The rest of the block runs only after the arm that does not leave.
                self.enter(("exit", id(s), other), self.condition(cond, other))
        elif tag == "match":
            self.expr(es[0])
            for arm in s.arms:
                env = dict(self.env)
                if arm.binder:
                    self.bind(arm.binder, True)
                self.block(arm.body)
                self.env = env
        elif tag in {"block", "unsafe", "defer"}:
            self.block(s.body)
        else:  # while, return, assign, expr, submit, break, continue, and anything newer: no origin of its own
            for e in es:
                self.expr(e)
            for inner in (s.body, s.other, *(a.body for a in s.arms)):
                if inner:
                    self.block(inner)

    def defined(self, name: str, value: Expr) -> list[Edge]:
        if value.tag == "call" and value.val == "Buf" and value.ty is not None and value.ty.name == "Buf":
            return self.bound(f"len({name})", value.args[0], value.args[0], False) if len(value.args) == 1 else []
        return self.bound(name, value, value, False)

    def inside(self, s: Stmt, binder: str, facts: list[Edge], body):
        env = dict(self.env)
        self.bind(binder, True)
        key = self.enter(("binder", id(s)), facts)
        body()
        del self.active[key]
        self.env = env

    def expr(self, e: Expr):
        if id(e) in self.seen:
            return
        self.seen.add(id(e))
        if e.tag == "binary" and e.val in {"&&", "||"}:
            self.expr(e.args[0])
            key = self.enter(("left", id(e), e.val == "&&"), self.condition(e.args[0], e.val == "&&"))
            self.expr(e.args[1])
            del self.active[key]
        elif e.tag == "lambda" and isinstance(e.ref, Function):
            env = dict(self.env)
            for n, t in e.ref.params:
                self.bind(n, t.mode == "value")
            self.block(e.ref.body)
            self.env = env
        elif e.tag == "spawn" and isinstance(e.ref, Stmt):
            self.stmt(e.ref)
        else:
            if e.tag == "call":
                self.repeated(e.args)
            for a in e.args:
                self.expr(a)
            if e.tag == "slice" and isinstance(e.ref, Expr):
                self.expr(e.ref)
        if e.established:
            self.judge(e)

    def repeated(self, args: list[Expr]):
        """Note every node of an argument written `hi - lo` over a part among the same arguments."""
        for a in args:
            if a.tag != "binary" or a.val != "-":
                continue
            for p in args:
                if p.tag == "slice" and alike(a.args[0], p.args[2]) and alike(a.args[1], p.args[1]):
                    stack = [a]
                    while stack:
                        node = stack.pop()
                        self.spans[id(node)] = p
                        stack += node.args
                    break

    # Each site ---------------------------------------------------------------------------------------------------

    def judge(self, e: Expr):
        kind = site(e)
        ok = kind is not None and self.proved(e, kind)
        tally = self.accepted if ok else self.refused
        name = KINDS.get(kind or "", "unknown")
        tally[name] = tally.get(name, 0) + 1
        if not ok:
            e.established = False

    def proved(self, e: Expr, kind: str) -> bool:
        how, given = e.proof if isinstance(e.proof, tuple) and len(e.proof) == 2 else (None, None)
        if how == "span":
            return kind == "binary" and e.ty == USIZE and e.val in {"+", "-"} and self.spans.get(id(e)) is given
        if how != "facts" or not isinstance(given, tuple):
            return False
        facts: list[Edge] = []
        for f in given:
            if not self.cited(f):
                return False
            facts.append((f[0], f[1], f[2]))
        return decide(self, e, kind, facts)

    def cited(self, f: Any) -> bool:
        """A cited fact holds here: its origin is in force, it follows from that origin, and it names nothing
        that could have changed."""
        origin = getattr(f, "origin", None)
        if not isinstance(origin, tuple) or len(origin) < 2 or len(f) != 3:
            return False
        key = (origin[0], id(origin[1]), *origin[2:])
        held = self.active.get(key)
        x, y, k = f
        if held is None or not (self.valid(x) and self.valid(y)):
            return False
        return any(a == x and b == y and j <= k for a, b, j in held)


def alike(a: Expr, b: Expr) -> bool:
    return (a.tag, a.val, len(a.args)) == (b.tag, b.val, len(b.args)) and all(map(alike, a.args, b.args))


def leaves(body: list[Stmt]) -> bool:
    """Whether a block always leaves by return, break or continue, so that nothing after it in its block runs."""
    if not body:
        return False
    s = body[-1]
    if s.tag in {"return", "break", "continue"}:
        return True
    if s.tag == "if":
        return leaves(s.body) and leaves(s.other)
    if s.tag in {"block", "unsafe"}:
        return leaves(s.body)
    return s.tag == "match" and bool(s.arms) and all(leaves(a.body) for a in s.arms)


def site(e: Expr) -> str | None:
    """Which guard an established node would have had."""
    if e.tag in {"index", "slice"}:
        return e.tag
    if e.tag == "binary" and e.val in {"+", "-"} and e.ty == USIZE:
        return "binary"
    if e.tag == "call" and e.val in {"shr", "shl_wrap"}:
        return "shift"
    if e.tag == "call" and e.val in BITS and len(e.args) == 1:
        return "conversion"
    return None


def scaled(facts: list[Edge], atoms: set[str]) -> list[Edge]:
    strides = {int(a.rsplit("*", 1)[1]) for a in atoms | {a for f in facts for a in f[:2]} if "*" in a}
    return [(a and f"{a}*{n}", b and f"{b}*{n}", k * n) for n in strides for a, b, k in facts if "*" not in a + b]


def distance(facts: list[Edge], source: str, target: str) -> int | None:
    """The least k with source - target <= k that `facts` give, with every plain atom between 0 and MAX."""
    every = facts + scaled(facts, {source, target})
    atoms = {source, target, ZERO} | {a for f in every for a in f[:2]}
    edges = every + [(a, ZERO, MAX) for a in atoms if "*" not in a] + [(ZERO, a, 0) for a in atoms]
    best = {source: 0}
    for _ in range(len(atoms) + 1):
        changed = False
        for a, b, k in edges:
            if a in best and best[a] + k < best.get(b, MAX + 1):
                best[b], changed = best[a] + k, True
        if not changed:
            return best.get(target)
    return None


def within(facts: list[Edge], x: Term, y: Term, slack: int = 0) -> bool:
    """x - y <= slack."""
    d = distance(facts, x[0], y[0])
    return d is not None and d <= slack - x[1] + y[1]


def decide(w: Walk, e: Expr, kind: str, facts: list[Edge]) -> bool:
    """The guard's condition, from the cited facts and the bounds derived here."""
    if kind == "index":
        n = w.extent(e.args[0])
        return n is not None and any(within(facts, h, n, -1) for h in w.terms(e.args[1], facts)[0])
    if kind == "binary":
        (_, la), (hb, _) = (w.terms(a, facts) for a in e.args)
        if e.val == "+":
            return any(within(facts, h, (ZERO, MAX)) for h in w.terms(e, facts)[0])
        return any(within(facts, y, x) for y in hb for x in la)
    if kind == "shift":
        width = BITS[e.args[0].ty.name]
        return any(within(facts, h, (ZERO, width - 1)) for h in w.terms(e.args[1], facts)[0])
    if kind == "conversion":
        src, dst = e.args[0].ty.name, e.val
        if src not in BITS or (src in SIGNED and dst not in SIGNED):
            return False
        if BITS[src] + (src not in SIGNED and dst in SIGNED) <= BITS[dst]:
            return True
        top = 2 ** (BITS[dst] - (dst in SIGNED)) - 1
        return src not in SIGNED and any(within(facts, h, (ZERO, top)) for h in w.terms(e.args[0], facts)[0])
    if kind == "slice":
        return part(w, e, facts)
    return False


def part(w: Walk, e: Expr, facts: list[Edge]) -> bool:
    """lo <= hi <= len(x), an extent equal to hi - lo, and nothing in `hi` that lowering would still guard."""
    (base, lo, hi), want = e.args, e.ref
    n = w.extent(base)
    if n is None or want is None or guards(hi):
        return False
    written = isinstance(want, Expr) and want.tag == "binary" and want.val == "-"
    if not (written and alike(want.args[0], hi) and alike(want.args[1], lo)):
        high, low, given = w.linear(hi), w.linear(lo), w.linear(want)
        if high is None or low is None or given is None:
            return False
        if {a: t for a in high.keys() | low.keys() if (t := high.get(a, 0) - low.get(a, 0))} != given:
            return False
    (h_lo, _), (h_hi, l_hi) = w.terms(lo, facts), w.terms(hi, facts)
    return any(within(facts, y, x) for y in h_lo for x in l_hi) and any(within(facts, y, n) for y in h_hi)


def guards(e: Expr) -> bool:
    """Whether lowering still writes a guard anywhere in `e`."""
    ty = e.ty.name if e.ty is not None else ""
    here = e.tag == "index" or (e.tag == "binary" and e.val in CHECKED and ty in BITS)
    here = here or (e.tag == "unary" and e.val == "-" and ty in SIGNED)
    here = here or (e.tag == "call" and (e.val in BITS or e.val in {"shr", "shl_wrap"}))
    return (here and not e.established) or any(guards(a) for a in e.args)


def audit(p: Program, keep_all: bool = False) -> dict[str, dict[str, dict[str, int]]]:
    """Check every discharged guard of every function; clear the flag of each whose proof fails, or of every one
    when `keep_all`. Per function: the discharges accepted and the ones refused, by kind."""
    out = {}
    for f in p.functions:
        if f.extern or (f.generics and not f.bindings):
            continue
        w = Walk(p.field_extents)
        for n, t in f.params:
            w.bind(n, t.mode != "rw")
        for n, v in f.bindings.items():
            if isinstance(v, int):
                w.env[n] = Name(True, constant=v)
        if keep_all:
            clear(f.body)
        elif any(e.established for e in nodes(f.body)):  # A function with nothing proposed has nothing to check.
            w.block(f.body)
        out[f.name] = {"accepted": w.accepted, "refused": w.refused}
    return out


def nodes(body: list[Stmt]):
    """Every expression under `body` that lowering writes, a lambda's, a part's extent and a spawned region's too."""
    for s in body:
        for e in s.exprs:
            yield from within_expr(e)
        for inner in (s.body, s.other, *(a.body for a in s.arms)):
            yield from nodes(inner)


def within_expr(e: Expr):
    yield e
    for a in e.args:
        yield from within_expr(a)
    if isinstance(e.ref, Expr) and e.tag == "slice":
        yield from within_expr(e.ref)
    elif isinstance(e.ref, Function) and e.tag == "lambda":
        yield from nodes(e.ref.body)
    elif isinstance(e.ref, Stmt) and e.tag == "spawn":
        yield from nodes([e.ref])


def clear(body: list[Stmt]):
    """Clear every established flag under `body`, so that lowering writes every guard."""
    for e in nodes(body):
        e.established = False

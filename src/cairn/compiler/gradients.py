"""Reverse-mode differentiation: `derive grad for f;` and `derive grad[w, b] for f;` generate `f_grad`, the adjoint of
f, as ordinary CAIRN source checked like any other function (docs/numerics.md#gradients).

`f_grad` takes f's parameters, then `seed` when f returns a float, then one adjoint for each parameter it
differentiates, `d_x:rw<T>` or `d_x:rw<T>[n]`, into which it adds, and `d_out:ro<T>[n]` for each float view f
writes, which it reads. It runs f's statements, adds seed times each partial derivative into the adjoints, and
returns f's result. Nothing is taped: every return, and the end of a function that writes views, carries its own
backward sweep, which recomputes the immutable lets on its path. The fragment is one whose adjoint lies in the same
fragment: immutable lets, `if` whose paths return, sums (`reduce +`, or a `let mut` one loop adds into), loops and
regions that write each output element once, `+ - * /`, `sqrt`, `abs`, `floor ceil trunc`, float conversions,
`std.math.exp` and `log`, and calls of functions that derive their own gradient. Anything else is refused by name; nothing outside it gets a derivative that is silently wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tree import Expr, Function, Program, Stmt, Type, fail

FLOATS = {"f32", "f64"}
FLAT = {"floor", "ceil", "trunc"}  # piecewise constant: no derivative flows through them
BINARY = {"+", "-", "*", "/"}


@dataclass
class Add:
    """One contribution of a backward sweep: `target += value`."""

    target: str
    kind: str  # "outer": a parameter's or an outer let's adjoint; "inner": a region's own let; "element": a view's
    value: str


@dataclass
class Nest:
    """Contributions under a condition or inside a loop, kept together so a filter can keep part of them."""

    head: str  # `if x < 0.0` or `for j in 0..n`
    body: list[Any]
    other: list[Any] = field(default_factory=list)  # the else branch of a condition


@dataclass
class Line:
    """A statement a contribution needs first or last: a temporary, or a callee's own gradient."""

    text: str


def show(e: Expr) -> str:
    """An expression of the fragment printed back as source, every operator bracketed."""
    if e.tag in {"name", "int", "float", "bool"}:
        return e.val
    if e.tag == "unary":
        return f"({e.val}{show(e.args[0])})"
    if e.tag == "binary":
        return f"({show(e.args[0])} {e.val} {show(e.args[1])})"
    if e.tag == "index":
        return f"{show(e.args[0])}[{', '.join(map(bare, e.args[1:]))}]"
    if e.tag == "slice":
        return f"{show(e.args[0])}[{bare(e.args[1])}..{bare(e.args[2])}]"
    if e.tag == "field":
        return f"{show(e.args[0])}.{e.val}"
    if e.tag == "call" and not e.val.startswith("."):
        targs = e.ref if isinstance(e.ref, tuple) else ()
        shown = f"[{', '.join(t.display() if isinstance(t, Type) else str(t) for t in targs)}]" if targs else ""
        return f"{e.val}{shown}({', '.join(map(bare, e.args))})"
    fail("E-GRAD-FORM", f"derive grad cannot differentiate a {e.tag} expression.", e)


def loop_head(s: Stmt) -> str:
    """`for i in lo..hi` or `parallel i in n`, as the statement was written."""
    if s.tag == "for":
        return f"for {s.name} in {show(s.exprs[0])}..{show(s.exprs[1])}"
    return f"parallel {s.name} in {show(s.exprs[0])}"


def bare(e: Expr) -> str:
    """An expression where nothing binds tighter around it: an index, an argument, a whole right-hand side."""
    text = show(e)
    return text[1:-1] if e.tag in {"binary", "unary"} else text


class Adjoint:
    def __init__(self, p: Program, f: Function, wrt: list[str], grads: dict[str, list[str]], at: Any):
        self.p, self.f, self.at, self.grads = p, f, at, grads
        self.fs = {g.name: g for g in p.functions}
        self.types: dict[str, str] = {}
        self.views: dict[str, Type] = {}
        for name, ty in f.params:
            if ty.mode == "value":
                self.types[name] = ty.name
            elif ty.extent and ty.name in FLOATS:
                self.views[name] = ty
        floats = [n for n, t in f.params if t.mode == "value" and t.name in FLOATS]
        readable = [n for n, t in self.views.items() if t.mode == "ro"]
        for name in wrt:
            if name not in floats and name not in readable:
                fail("E-GRAD", f"{name} is not a float parameter or a ro float view of {f.name}.", at)
        self.inputs = wrt or floats + readable
        self.outputs = [n for n, t in self.views.items() if t.mode == "rw"]
        self.active = set(self.inputs)
        self.written: set[str] = set()
        self.lane, self.count = "", 0
        self.inner: set[str] = set()  # the lets of the region being swept
        self.sums: dict[str, str] = {}  # each `let mut` accumulator: "open" until its one loop has added into it
        self.names = {n for n, _ in f.params} | {"d_" + n for n in self.inputs + self.outputs} | {"seed", "grad_result"}

    # What values are and depend on ---------------------------------------------------------------

    def depends(self, e: Expr) -> bool:
        if e.tag == "name":
            return e.val in self.active
        if e.tag == "index":
            return e.args[0].tag == "name" and e.args[0].val in self.active
        if e.tag == "call" and e.val in {"f32", "f64"} and e.args and self.infer(e.args[0]) not in FLOATS:
            return False  # an integer converted is data
        return any(self.depends(a) for a in e.args)

    def infer(self, e: Expr) -> str | None:
        """The type a float expression has, where the source says it; literals adapt and say nothing."""
        if e.tag == "name":
            return self.types.get(e.val)
        if e.tag == "index" and e.args[0].tag == "name" and e.args[0].val in self.views:
            return self.views[e.args[0].val].name
        if e.tag == "unary":
            return self.infer(e.args[0])
        if e.tag == "binary":
            return "bool" if e.val not in BINARY else self.infer(e.args[0]) or self.infer(e.args[1])
        if e.tag == "call":
            if e.val in FLOATS:
                return e.val
            if e.val in {"sqrt", "abs"} | FLAT and e.args:
                return self.infer(e.args[0])
            callee = self.callee(e.val)
            return callee.ret.name if callee else None
        return None

    def callee(self, written: str) -> Function | None:
        from .expansion import visible

        found = visible(self.p, self.f.module, written, self.fs)
        return self.fs.get(found) if found else None

    def bind(self, s: Stmt):
        """What a let, a reduction or a region teaches: its value's type and whether it carries a derivative."""
        if s.tag in {"let", "reduce", "reg"}:
            if s.name in self.names:
                fail("E-DERIVE-COLLISION", f"derive grad uses {s.name} itself; rename the local.", s)
            self.names |= {s.name, "d_" + s.name}
            value = s.exprs[-1]
            ty = s.ty.name if s.ty else self.infer(value)
            self.types[s.name] = ty or ""
            if self.depends(value):
                if ty not in FLOATS:
                    fail("E-GRAD-FORM", f"Annotate let {s.name} with its float type; derive grad needs it.", s)
                self.active.add(s.name)

    # The forward statements, printed back -------------------------------------------------------------

    def forward(self, s: Stmt) -> list[str]:
        if s.tag == "let":
            return [f"let {s.name}{':' + s.ty.display() if s.ty else ''} = {show(s.exprs[0])};"]
        if s.tag == "reduce":
            if s.op != "+" and self.depends(s.exprs[1]):
                fail("E-GRAD-FORM", f"derive grad differentiates reduce +, not reduce {s.op}.", s)
            head = f"let {s.name}{':' + s.ty.display() if s.ty else ''} = reduce {s.op}"
            return [
                f"{head} {'parallel' if s.pooled else 'for'} {s.binder} in {show(s.exprs[0])} yield {show(s.exprs[1])};"
            ]
        if s.tag in {"parallel", "for"}:
            return self.region(s)
        if s.tag == "reg" and (s.ty.name if s.ty else self.infer(s.exprs[0])) in FLOATS:
            self.sums[s.name] = "open"
            return [f"let mut {s.name}{':' + s.ty.display() if s.ty else ''} = {show(s.exprs[0])};"]
        fail("E-GRAD-FORM", self.refusal(s), s)

    def refusal(self, s: Stmt) -> str:
        if s.tag == "reg":
            return f"derive grad differentiates a let mut only as a float sum; {s.name} is not one."
        if s.tag == "assign":
            return "derive grad assigns only an output element, out[i] = e, inside the loop or region over i."
        return f"derive grad cannot differentiate a {s.tag} statement."

    def region(self, s: Stmt) -> list[str]:
        """A loop or region whose body is lets and output elements, each written once at the binder."""
        if s.tag == "for" and s.op == "elements":
            fail("E-GRAD-FORM", "derive grad differentiates index loops: write for i in 0..len(xs) with xs[i].", s)
        if s.tag != "for" and s.other_names:
            fail("E-GRAD-FORM", "derive grad does not differentiate queued device work.", s)
        lines = [loop_head(s) + " {"]
        self.types[s.name] = "usize"
        summed = set()
        for inner in s.body:
            if inner.tag == "assign" and self.summed(inner, s):
                summed.add(inner.exprs[0].val)
                added = inner.exprs[1]
                lines.append(f"  {inner.exprs[0].val} {added.val}= {bare(added.args[1])};")
            elif inner.tag == "assign":
                target, value = inner.exprs
                out = target.args[0].val if target.tag == "index" and target.args[0].tag == "name" else ""
                if out not in self.outputs or len(target.args) != 2 or show(target.args[1]) != s.name:
                    fail("E-GRAD-FORM", self.refusal(inner), inner)
                if out in self.written:
                    fail("E-GRAD-FORM", f"derive grad writes {out} once; a second loop over it has no adjoint.", inner)
                if self.reads(value, out):
                    fail("E-GRAD-FORM", f"{out} is an output of the derivative; it cannot also be read.", inner)
                lines.append(f"  {show(target)} = {show(value)};")
            else:
                if inner.tag not in {"let", "reduce"}:
                    fail("E-GRAD-FORM", self.refusal(inner), inner)
                lines += ["  " + line for line in self.forward(inner)]
                self.bind(inner)
        self.written |= {st.exprs[0].args[0].val for st in s.body if st.tag == "assign" and st.exprs[0].tag == "index"}
        for name in summed:  # what the loop added is known now: the sum may be read from here on
            self.sums[name] = "closed"
            if any(self.depends(st.exprs[1]) for st in s.body if st.tag == "assign" and st.exprs[0].tag == "name"):
                self.active.add(name)
        return [*lines, "}"]

    def summed(self, st: Stmt, loop: Stmt) -> bool:
        """`acc += e` (or `acc = acc + e`, or `-`) in a sequential loop, the one loop that adds into acc."""
        target, value = st.exprs
        if target.tag != "name" or target.val not in self.sums:
            return False
        name = target.val
        whole = value.tag == "binary" and value.val in {"+", "-"} and value.args[0].tag == "name"
        if not whole or value.args[0].val != name or self.reads(value.args[1], name):
            fail("E-GRAD-FORM", f"derive grad adds into {name} only as {name} += e, with e not reading {name}.", st)
        if self.sums[name] != "open" or loop.tag != "for":
            fail("E-GRAD-FORM", f"{name} is a sum that one sequential loop adds into, once.", st)
        return True

    def early(self, s: Stmt):
        """A sum is read only after its loop: a partial sum carries a derivative this sweep does not follow."""
        for name, state in self.sums.items():
            body = s.body if s.tag in {"for", "parallel"} else []
            parts = [*s.exprs, *(x for st in body for x in st.exprs[(1 if st.tag == "assign" else 0) :])]
            parts = [x.args[1] if x.tag == "binary" and x.args[0].tag == "name" and x.args[0].val == name and
                     s.tag == "for" else x for x in parts]  # fmt: skip
            if state == "open" and any(self.reads(x, name) for x in parts):
                fail("E-GRAD-FORM", f"{name} is read before the loop that adds into it has finished.", s)

    def reads(self, e: Expr, name: str) -> bool:
        return (e.tag == "name" and e.val == name) or any(self.reads(a, name) for a in e.args)

    # The backward sweep --------------------------------------------------------------------------------

    def back(self, e: Expr, adj: str) -> list[Any]:
        """What `e` adds into the adjoints when `adj` flows into it."""
        if not self.depends(e):
            return []
        if e.tag == "name":
            return [Add("d_" + e.val, "inner" if e.val in self.inner else "outer", adj)]
        if e.tag == "index":
            view, at = e.args[0].val, e.args[1]
            if self.lane and show(at) != self.lane:
                fail("E-GRAD-RACE", f"The adjoint of {show(e)} adds into d_{view} at another lane's element; "
                     f"read {view} at [{self.lane}] in the region, or differentiate a sequential loop.", e)  # fmt: skip
            return [Add(f"d_{view}[{show(at)}]", "element", adj)]
        if e.tag == "unary" and e.val == "-":
            return self.back(e.args[0], f"(-{adj})")
        items: list[Any] = []
        if e.tag == "binary" and e.val in BINARY:
            a, b = e.args
            if e.val in {"+", "-"}:
                return self.back(a, adj) + self.back(b, adj if e.val == "+" else f"(-{adj})")
            av, bv = self.value(a, items), self.value(b, items)
            if e.val == "*":
                return items + self.back(a, f"({adj} * {bv})") + self.back(b, f"({av} * {adj})")
            return items + self.back(a, f"({adj} / {bv})") + self.back(b, f"(-({adj} * {av}) / ({bv} * {bv}))")
        if e.tag == "call" and e.args:
            a = e.args[0]
            if e.val in FLOATS:
                return self.back(a, f"{self.infer(a)}({adj})")
            if e.val in FLAT:
                return []
            libm = self.callee(e.val)  # std.math's exp and log have derivatives written with themselves
            if e.val == "sqrt":
                return items + self.back(a, f"({adj} / (2.0 * sqrt({self.value(a, items)})))")
            if e.val == "abs":  # the subgradient at zero is +1
                below = f"if {self.value(a, items)} < 0.0"
                return [*items, Nest(below, self.back(a, f"(-{adj})"), self.back(a, adj))]
            if libm and libm.name == "std.math.exp":
                return items + self.back(a, f"({adj} * {self.value(e, items)})")
            if libm and libm.name == "std.math.log":
                return items + self.back(a, f"({adj} / {self.value(a, items)})")
            return self.chain(e, adj)
        fail("E-GRAD-FORM", f"derive grad cannot differentiate {show(e)}.", e)

    def value(self, e: Expr, items: list[Any]) -> str:
        """An operand a derivative repeats. One that calls a function is bound first, so the call runs once and
        never sits beside another operand, as E-EFFECT-ORDER requires of the source too."""
        if not calls(e):
            return show(e)
        name = self.fresh("grad_v")
        items.append(Line(f"let {name}:{self.infer(e) or 'f64'} = {bare(e)};"))
        return name

    def chain(self, e: Expr, adj: str) -> list[Any]:
        """A call of a function that derives its own gradient: its gradient, handed the adjoints of the arguments."""
        g = self.callee(e.val)
        if g is None or g.name not in self.grads:
            fail("E-GRAD-CALL", f"{e.val} has no derived gradient; write derive grad for {e.val};.", e)
        if self.lane:
            fail("E-GRAD-FORM", f"derive grad does not call {e.val}'s gradient inside a region's lane.", e)
        if len(e.args) != len(g.params) or g.ret.name not in FLOATS:
            fail("E-GRAD-CALL", f"Write every argument of {e.val}, whose gradient needs a float result.", e)
        before: list[Any] = []
        after: list[Any] = []
        adjoints = []
        for (name, ty), arg in zip(g.params, e.args, strict=True):
            differentiated = name in self.grads[g.name]
            if not differentiated:
                if self.depends(arg):
                    fail("E-GRAD-CALL", f"{e.val}'s gradient leaves out {name}; add it to derive grad[...] for "
                         f"{e.val}.", e)  # fmt: skip
                continue
            if ty.mode != "value":
                base = arg.args[0] if arg.tag == "slice" else arg
                if base.tag != "name" or base.val not in self.active:
                    fail("E-GRAD-CALL", f"{show(arg)} carries no derivative, and {e.val}'s gradient needs one "
                         f"for {name}.", e)  # fmt: skip
                adjoints.append("d_" + show(arg))
            elif arg.tag == "name" and arg.val in self.active:
                adjoints.append("d_" + arg.val)
            else:
                temporary = self.fresh("grad_d")
                before.append(Line(f"let mut {temporary}:{ty.name} = 0.0;"))
                adjoints.append(temporary)
                after += self.back(arg, temporary)
        text = f"{e.val}_grad({', '.join(map(bare, e.args))}, {adj}, {', '.join(adjoints)});"  # it writes, so it stands alone
        return [*before, Line(text), *after]

    def fresh(self, prefix: str) -> str:
        self.count += 1
        name = f"{prefix}{self.count}"
        if name in self.names:
            fail("E-DERIVE-COLLISION", f"derive grad uses {name} itself; rename it.", self.at)
        return name

    def sweep(self, path: list[Stmt], result: Expr | None) -> list[str]:
        """Everything the statements on `path` add into the adjoints, from the last back to the first."""
        items: list[Any] = [Line(f"let mut d_{s.name}:{self.types[s.name]} = 0.0;") for s in path
                            if s.tag in {"let", "reduce", "reg"} and s.name in self.active]  # fmt: skip
        if result is not None:
            items.insert(0, Line(f"let grad_result:{self.f.ret.display()} = {show(result)};"))
            items += self.back(result, "seed")
        for s in reversed(path):
            if s.tag in {"let", "reg"} and s.name in self.active:
                items += self.back(s.exprs[0], "d_" + s.name)
            elif s.tag == "reduce" and s.name in self.active:
                items.append(self.summed_back(s))
            elif s.tag in {"parallel", "for"}:
                items += self.backward_region(s)
        lines = render(items)
        return [*lines, "return grad_result;"] if result is not None else lines

    def summed_back(self, s: Stmt) -> Nest:
        """A `reduce +`'s adjoint flows into every yield, in a sequential loop of its own."""
        return Nest(f"for {s.binder} in 0..{show(s.exprs[0])}", self.back(s.exprs[1], "d_" + s.name))

    def backward_region(self, s: Stmt) -> list[Any]:
        """A region's lanes add into their own elements in a region of their own; what they add into an outer
        scalar is gathered by a sequential loop, so no lane writes a shared value."""
        lets = [st for st in s.body if st.tag in {"let", "reduce"}]
        head = loop_head(s)
        self.inner = {st.name for st in lets}
        self.lane = s.name if s.tag == "parallel" else ""
        items: list[Any] = [Line(line) for st in lets for line in self.forward(st)]
        items += [Line(f"let mut d_{st.name}:{self.types[st.name]} = 0.0;") for st in lets if st.name in self.active]
        for st in reversed(s.body):
            if st.tag == "assign" and st.exprs[0].tag == "name":  # acc += e: e gets acc's adjoint, every step
                added = st.exprs[1]
                adjoint = "d_" + st.exprs[0].val if added.val == "+" else f"(-d_{st.exprs[0].val})"
                items += self.back(added.args[1], adjoint) if st.exprs[0].val in self.active else []
            elif st.tag == "assign":
                target = st.exprs[0]
                seed = f"d_{target.args[0].val}[{show(target.args[1])}]"
                items += self.back(st.exprs[1], seed)
            elif st.name in self.active and st.tag == "let":
                items += self.back(st.exprs[0], "d_" + st.name)
            elif st.name in self.active:  # a reduction inside the lane: its own sequential loop
                items.append(self.summed_back(st))
        self.inner, self.lane = set(), ""
        if s.tag == "for":
            return [Nest(head, items)]
        passes = []
        if contributes(items, "outer") and any(t.place != "host" for t in self.views.values()):
            fail("E-GRAD-FORM", "A device region's lanes would add into a shared scalar, which a host loop gathers; "
                 "leave that parameter out of derive grad[...] or differentiate a host region.", s)  # fmt: skip
        if contributes(items, "element"):
            passes.append(Nest(head, keep(items, {"element", "inner"})))
        if contributes(items, "outer"):
            passes.append(Nest(f"for {s.name} in 0..{show(s.exprs[0])}", keep(items, {"outer", "inner"})))
        return passes

    # The whole function ---------------------------------------------------------------------------------

    def statements(self, ss: list[Stmt], path: list[Stmt]) -> list[str]:
        lines: list[str] = []
        for s in ss:
            if s.tag == "return":
                if not s.exprs and self.f.ret.name != "void":
                    fail("E-GRAD-FORM", "A return without a value ends only a function that writes views.", s)
                return lines + self.sweep(path, s.exprs[0] if s.exprs else None) + ([] if s.exprs else ["return;"])
            if s.tag == "if":  # the derivative follows the branch taken; the condition itself carries none
                lines.append(f"if {show(s.exprs[0])} {{")
                lines += ["  " + x for x in self.statements(s.body, list(path))]
                lines += ["} else {", *("  " + x for x in self.statements(s.other, list(path))), "}"]
                continue
            self.early(s)
            lines += self.forward(s)
            self.bind(s)
            path = [*path, s]
        return lines + (self.sweep(path, None) if self.f.ret.name == "void" else [])

    def source(self) -> str:
        f = self.f
        if f.generics or f.extern or f.kernel or f.owner:
            fail("E-GRAD", f"derive grad takes a plain function; {f.name} is generic, extern, a kernel or a method.",
                 self.at)  # fmt: skip
        if f.ret.name not in FLOATS and not (f.ret.name == "void" and self.outputs):
            fail("E-GRAD", f"{f.name} returns no float and writes no float view: it has nothing to differentiate.",
                 self.at)  # fmt: skip
        params = [f"{n}:{t.display()}" for n, t in f.params]
        params += [f"seed:{f.ret.display()}"] if f.ret.name in FLOATS else []
        for name, ty in f.params:
            if name in self.inputs:
                params.append(
                    f"d_{name}:"
                    + (
                        f"rw<{ty.name}>"
                        if ty.mode == "value"
                        else Type(ty.name, "rw", ty.extent, place=ty.place).display()
                    )
                )
            elif name in self.outputs:
                params.append(f"d_{name}:" + Type(ty.name, "ro", ty.extent, place=ty.place).display())
        for n in params:
            name = n.split(":", 1)[0]
            if name.startswith("d_") and name in {p for p, _ in f.params}:
                fail(
                    "E-DERIVE-COLLISION", f"derive grad names an adjoint {name}, which {f.name} already uses.", self.at
                )
        body = self.statements(f.body, [])
        ret = "" if f.ret.name == "void" else f" -> {f.ret.display()}"
        local = f.name.rsplit(".", 1)[-1]
        return f"fn {local}_grad({', '.join(params)}){ret} {{\n" + "".join(f"  {line}\n" for line in body) + "}\n"


def calls(e: Expr) -> bool:
    """Whether an expression calls anything beyond the exact builtins, which observe and cost nothing."""
    pure = e.tag == "call" and e.val in FLOATS | FLAT | {"sqrt", "abs"}
    return (e.tag == "call" and not pure) or any(calls(a) for a in e.args)


def contributes(items: list[Any], kind: str) -> bool:
    return any(
        (isinstance(x, Add) and x.kind == kind) or (isinstance(x, Nest) and contributes(x.body + x.other, kind))
        for x in items
    )


def keep(items: list[Any], kinds: set[str]) -> list[Any]:
    kept: list[Any] = []
    for x in items:
        if isinstance(x, Add) and x.kind not in kinds:
            continue
        if isinstance(x, Nest):
            x = Nest(x.head, keep(x.body, kinds), keep(x.other, kinds))
            if not x.body and not x.other:
                continue
        kept.append(x)
    return kept


def render(items: list[Any], depth: int = 0) -> list[str]:
    pad, lines = "  " * depth, []
    for x in items:
        if isinstance(x, Line):
            lines.append(pad + x.text)
        elif isinstance(x, Add):
            lines.append(f"{pad}{x.target} = {x.target} + {x.value};")
        else:
            lines += [f"{pad}{x.head} {{", *render(x.body, depth + 1)]
            lines += [f"{pad}}} else {{", *render(x.other, depth + 1), f"{pad}}}"] if x.other else [f"{pad}}}"]
    return lines


def differentiate(p: Program, wanted: list[tuple], names: set[str]):
    """Every `derive grad` of the program: each target's differentiated parameters are known before any gradient
    is written, so a function may call the gradient of one derived after it."""
    from .expansion import visible

    fs = {f.name: f for f in p.functions}
    targets = []
    for module, _, naturals, target, at in wanted:
        full = visible(p, module, target, fs) if target else None
        if full is None or fs[full].module != module or any(isinstance(n, int) for n in naturals):
            fail("E-GRAD", "Write derive grad for f; or derive grad[x, y] for f; with f a function of this module "
                 "and x, y its float parameters.", at)  # fmt: skip
        targets.append((module, fs[full], [str(n) for n in naturals], at))
    grads = {f.name: Adjoint(p, f, wrt, {}, at).inputs for _, f, wrt, at in targets}
    for module, f, wrt, at in targets:
        made = gradient(p, f, wrt, grads, at)
        if made.name in names or made.name in p.modules:
            fail("E-DERIVE-COLLISION", f"Derived name {made.name} already exists.", at)
        names.add(made.name)
        p.modules[made.name], made.module = module, module
        p.public |= {made.name} if made.public else set()
        made.source_name = f"derive grad for {f.name}"
        p.functions.append(made)


def gradient(p: Program, f: Function, wrt: list[str], grads: dict[str, list[str]], at: Any) -> Function:
    """f's adjoint, parsed from the source this generates; every node takes the position of the `derive` itself,
    so a refusal inside generated code points at the line that asked for it."""
    from .syntax import Parser

    text = Adjoint(p, f, wrt, grads, at).source()
    made = Parser(text).parse().functions[0]
    made.name = f.name + "_grad"
    made.public = f.public
    todo: list[Any] = [*made.body]
    for node in todo:
        node.line, node.col = at.line, at.col
        if isinstance(node, Stmt):
            todo += [*node.body, *node.other, *node.exprs, *node.arms]
        elif isinstance(node, Expr):
            todo += node.args
        else:
            todo += node.body
    made.line, made.col = at.line, at.col
    return made

"""What a function does each time it runs, counted from the typed tree in the sizes it is written in.

A count is a polynomial in the function's extents: a region over `n` runs its body `n` times, a loop over `m` inside
it runs `n*m` times. An access is a stream when its index moves with a loop or lane binder, invariant when it does
not move, and irregular when it depends on data. What the tree cannot bound, such as a `while` loop's trip count, a
foreign call or a function value, is named in `unknown`, and the model's confidence falls with each entry. Nothing
here is timed.
"""

from __future__ import annotations

from typing import Any

from ..compiler.plans import chunks, fusion
from ..compiler.primitives import atomics, wide
from ..compiler.primitives.builtins import WRAPPING
from ..compiler.syntax.tree import FLOAT, INT, NUMERIC, Expr, Function, Stmt, Type, is_view, negated_literal
from .cooperative_work import region
from .counts import ONE, Cost, Frame, Poly, Region, Work, add, data_dependent, path, widen

CHECKED = {"+": "int_checked", "-": "int_checked", "*": "mul_checked", "/": "div", "%": "div"}


class Counter:
    """Counts every function of a checked program once; a call is the callee's cost in the caller's sizes."""

    def __init__(self, program: Any, checker: Any):
        self.p, self.c = program, checker
        self.costs: dict[str, Cost] = {}
        self.open: set[str] = set()
        self.values: dict[str, Poly] = {}  # immutable usize locals and Buf lengths of the function being counted
        self.minimum: dict[str, list[Expr]] = {}  # `let hi = min(a, b)`: a bound counts at whichever side is fixed
        self.bound: list[str] = []  # the loop and lane binders in scope, which sizes may mention
        self.data: set[str] = set()  # locals whose value was read from memory or computed from what was
        self.cost = Cost("", 0, [])
        self.f: Function | None = None  # the function being counted, whose blocks fusion reads
        self.kept: set[str] = set()  # scratch a fused chain holds in its lanes: never allocated, never streamed
        self.coop: Any = None  # the cooperative region being counted, which counts what its threads do apart

    def function(self, f: Function) -> Cost:
        if f.name in self.costs:
            return self.costs[f.name]
        if f.name in self.open:
            return Cost(f.name, f.line, [], unknown=[f"{f.name} is recursive: its depth is not a size"])
        self.open.add(f.name)
        saved = self.values, self.cost, self.bound, self.data, self.f
        self.bound, self.data, self.f = [], set(), f
        extents = [n for n, t in f.params if t == Type("usize")]
        self.values = {n: Poly.var(n) for n in extents}
        for n, t in f.params:
            if is_view(t):
                self.values[f"len({n})"] = self.extent(t, n)
        self.cost = Cost(f.name, f.line, extents)
        if f.extern:
            self.cost.unknown.append(f"{f.name} is foreign: its body is not visible")
        self.block(f.body, Frame(self.cost.seq, ONE, ()))
        # An extent is a usize parameter some count depends on; `interior(i, n)` does the same work for every i and n.
        self.cost.extents = [n for n in extents if n in self.cost.symbols()]
        cost, (self.values, self.cost, self.bound, self.data, self.f) = self.cost, saved
        self.open.discard(f.name)
        self.costs[f.name] = cost
        return cost

    # Sizes -------------------------------------------------------------------------------------------------------

    def extent(self, ty: Type, base: str) -> Poly:
        if ty.extent.isdigit():
            return Poly.of(int(ty.extent))
        if ty.extent:
            return self.values.get(ty.extent, Poly.var(ty.extent))
        if ty.name == "Array" and len(ty.args) > 1 and isinstance(ty.args[1], int):
            return Poly.of(ty.args[1])
        return self.values.get(f"len({base})", Poly.var(f"len({base})"))

    def size(self, e: Expr) -> Poly | None:
        """The value of a `usize` expression as a polynomial in the extents, or None when it is not one."""
        if isinstance(e.ref, Expr):
            return self.size(e.ref)
        if e.tag == "int":
            return Poly.of(int(e.val))
        if e.tag == "name":
            if isinstance(e.ref, int):
                return Poly.of(e.ref)
            return Poly.var(e.val) if e.val in self.bound else self.values.get(e.val)
        if e.tag == "call" and e.val == "len" and e.args:
            viewed = e.args[0]
            return self.extent(viewed.ty, path(viewed)) if viewed.ty else None
        if e.tag == "binary" and e.val in {"+", "-", "*", "/"}:
            left, right = self.size(e.args[0]), self.size(e.args[1])
            if left is None or right is None:
                return None
            if e.val == "/":  # only by a constant: a quotient of two sizes is not a polynomial
                divisor = right.value({})
                return left * (1 / divisor) if divisor else None
            return left + right if e.val == "+" else left - right if e.val == "-" else left * right
        return None

    def trips(self, lo: Expr, hi: Expr, line: int) -> Poly:
        a = self.size(lo)
        if a is not None and hi.tag == "name" and isinstance(self.minimum.get(hi.val), list):
            hi = min(self.minimum[hi.val], key=lambda side: len(((self.size(side) or Poly.var("?")) - a).symbols()))
            self.note(
                f"line {line}: a loop bounded by min() is counted at its fixed side, which the last pass may not reach"
            )
        b = self.size(hi)
        if a is not None and b is not None:
            return b - a
        self.cost.unknown.append(f"line {line}: a trip count that is not a function of the extents")
        return Poly.var(f"?trips@{line}")

    def note(self, why: str) -> None:
        if why not in self.cost.approximate:
            self.cost.approximate.append(why)

    # Statements --------------------------------------------------------------------------------------------------

    def block(self, ss: list[Stmt], at: Frame) -> None:
        chains = {id(chain.regions[0]): chain for chain in fusion.chains(ss, self.c.rows, self.f)}
        inside = {id(r) for chain in chains.values() for r in chain.regions[1:]}
        self.kept |= {name for chain in chains.values() for name in chain.scratch}
        for s in ss:
            if id(s) in chains:
                self.fused(chains[id(s)], at)
            elif id(s) not in inside and (self.coop is None or not self.coop.stmt(s, at)):
                getattr(self, "s_" + s.tag, self.s_plain)(s, at)

    def fused(self, chain: fusion.Chain, at: Frame) -> None:
        """A chain a plan fused is one region: one pass over the extent doing every body, sharing what it reads,
        with its lane-held scratch neither streamed nor allocated. A host reduce that ends it is one fold whose
        every step runs the bodies first: in order on this thread, or pooled in blocks."""
        head, tail, body = chain.regions[0], chain.regions[-1], Work()
        folds = tail.tag == "reduce"
        binders = tuple(r.binder or r.name for r in chain.regions)
        self.bound += binders
        count = self.size(head.exprs[0]) or Poly.var(f"?count@{head.line}")
        lane = Frame(body, ONE, (*at.binders, *binders), not folds or tail.pooled)
        for r in chain.regions[: -1 if folds else None]:
            self.block(r.body, lane)
            # What this body wrote, a later body reads back only at the lane's own index: from a register or L1.
            lane.seen |= {(k, False) for k in body.writes}
        if folds:
            self.expr(tail.exprs[1], lane)
            body.op(combining(tail), ONE)
        del self.bound[-len(binders) :]
        for name in chain.scratch:
            for table in (body.reads, body.writes, body.footprint):
                table.pop(name, None)
        if folds and not tail.pooled:  # the fold's own thread runs every step, the bodies with it
            at.work.merge(body, at.times * count)
            return
        kind = "pooled" if folds else "device" if head.ref == "device" else "host"
        self.cost.regions.append(
            Region(kind, head.line, count, at.times, body, head.block, head.plan, head.launch, fuse=head.fuse)
        )

    def s_plain(self, s: Stmt, at: Frame) -> None:
        for e in s.exprs:
            self.expr(e, at)
        self.block(s.body, at)

    def s_asm(self, s: Stmt, at: Frame) -> None:  # typed assembly: its operands, then instructions nobody counts
        self.s_plain(s, at)
        self.io(at, f"line {s.line}: asm reaches the machine")

    def s_let(self, s: Stmt, at: Frame) -> None:
        e = s.exprs[0]
        self.expr(e, at)
        if s.tag == "let" and s.ty == Type("usize") and (value := self.size(e)) is not None:
            self.values[s.name] = value
        elif s.tag == "let" and e.tag == "call" and e.val == "min" and len(e.args) == 2:
            self.minimum[s.name] = list(e.args)
        elif s.tag == "reg" or data_dependent(e, self.data, tuple(self.bound)) or e.tag == "index":
            self.data.add(s.name)
        if e.tag == "call" and e.val == "Buf" and e.args and (n := self.size(e.args[0])) is not None:
            self.values[f"len({s.name})"] = n

    s_reg = s_let

    def s_assign(self, s: Stmt, at: Frame) -> None:
        target, value = s.exprs
        if kind := fold(target, value, at.binders):
            self.expr(value.args[1], at)
            at.work.op(kind, at.times)
        else:
            self.expr(value, at)
        if target.tag == "index":
            self.access(target, at, write=True)
        else:
            self.expr(target, at, place=True)

    def s_buffer(self, s: Stmt, at: Frame) -> None:
        n = self.size(s.exprs[0]) or Poly.var(f"?capacity@{s.line}")
        self.values[f"len({s.name})"] = n
        if s.tag == "buffer" and s.name not in self.kept:
            self.cost.allocations = self.cost.allocations + at.times
            self.cost.allocated = self.cost.allocated + n * self.c.sizeof(s.ty) * at.times

    s_stack = s_buffer

    def branches(self, bodies: list[list[Stmt]], at: Frame) -> None:
        """The dearer of the branches, term by term: the model prices a branch as the one that costs more."""
        joined: Work | None = None
        for body in bodies:
            w = Work()
            self.block(body, Frame(w, at.times, at.binders, at.lane, at.seen))
            joined = w if joined is None else joined.join(w)
        if joined is not None:
            at.work.merge(joined)

    def s_if(self, s: Stmt, at: Frame) -> None:
        """An `if` or a `match`: its condition or subject, a branch, and the dearer of its bodies."""
        self.expr(s.exprs[0], at)
        at.work.op("branch", at.times)
        self.branches([s.body, s.other] if s.tag == "if" else [arm.body for arm in s.arms], at)

    s_match = s_if

    def s_for(self, s: Stmt, at: Frame) -> None:
        lo, hi = s.exprs
        self.expr(lo, at)
        self.expr(hi, at)
        n = self.trips(lo, hi, s.line)
        if any(b in n.symbols() for b in at.binders):  # A triangular loop is counted at its bound.
            n = n.subst({b: Poly.var(f"?bound({b})") for b in at.binders})
            self.note(f"line {s.line}: a loop bounded by an outer index is counted at its largest")
        self.bound.append(s.name)
        self.block(s.body, at.inner(n, s.name))
        self.bound.pop()

    def s_while(self, s: Stmt, at: Frame) -> None:
        n = Poly.var(f"?trips@{s.line}")
        self.cost.unknown.append(f"line {s.line}: a while loop's trip count is not a size")
        inner = at.inner(n)
        self.expr(s.exprs[0], inner)
        self.block(s.body, inner)

    def s_parallel(self, s: Stmt, at: Frame) -> None:
        count = self.size(s.exprs[0]) or Poly.var(f"?count@{s.line}")
        body = Work()
        self.bound.append(s.name)
        self.block(s.body, Frame(body, ONE, (*at.binders, s.name), True))
        self.bound.pop()
        kind = "device" if s.ref == "device" else "host"
        held = chunks.chunkable(s) if kind == "device" else {}  # what any vector plan would chunk, planned or not
        found = tuple((sum(1 for u in chunks.exprs(s.body) if u.tag == "index" and u.args[0].tag == "name"
                           and u.args[0].val == name), loaded, stored, self.c.sizeof(element))
                      for name, (element, loaded, stored, _) in sorted(held.items()))  # fmt: skip
        self.cost.regions.append(Region(kind, s.line, count, at.times, body, s.block, s.plan, s.launch, fuse=s.fuse,
                                        vector=s.vector, chunks=found))  # fmt: skip

    def s_blocks(self, s: Stmt, at: Frame) -> None:
        region(self, s, at)

    def s_reduce(self, s: Stmt, at: Frame) -> None:
        self.reduction(s, at, *s.exprs[:2])  # a total written into an element is one store after the fold

    def s_scan(self, s: Stmt, at: Frame) -> None:
        out, hi, value, store = s.exprs
        self.reduction(s, at, hi, value, store, out)

    def reduction(self, s: Stmt, at: Frame, hi: Expr, value: Expr, store: Expr | None = None,
                  out: Expr | None = None) -> None:  # fmt: skip
        """A reduction or a scan, priced as a written loop is: a checked or float step waits on the last, the rest
        overlap. Pooled or on the device it is a region, and a scan's second pass reads and writes each element once
        more, with its block's offset combined in."""
        count = self.size(hi) or Poly.var(f"?count@{s.line}")
        region = s.ref == "device" or s.pooled
        inner = Frame(Work(), ONE, (s.binder,), True) if region else at.inner(count, s.binder)
        self.expr(value, inner)
        if store is not None:
            self.access(store, inner, write=True)
        inner.work.op(combining(s), inner.times)
        if not region:
            return
        if out is not None:
            key, size = path(out), Poly.of(self.c.sizeof(out.ty.value) if out.ty else 8)
            add(inner.work.reads, key, size)
            add(inner.work.writes, key, size)
            inner.work.op("int", ONE)
        self.cost.regions.append(
            Region("device" if s.ref == "device" else "pooled", s.line, count, at.times, inner.work)
        )

    def s_compact(self, s: Stmt, at: Frame) -> None:
        """A compaction: in the calling thread, a loop; on the device, a region whose body is one index's work, as a
        reduction's is, which the region's count multiplies."""
        out, hi, predicate, value = s.exprs
        count = self.size(hi) or Poly.var(f"?count@{s.line}")
        device = s.ref == "device"
        inner = Frame(Work(), ONE, (s.binder,), True) if device else at.inner(count, s.binder)
        inner.seen.add((path(out), True))
        self.expr(predicate, inner)
        self.expr(value, inner)
        size = self.c.sizeof(out.ty.value) if out.ty else 8
        add(inner.work.writes, path(out), inner.times * size)
        inner.work.op("collect", inner.times)  # a store at a count that moves with the data, one element at a time
        if device:
            self.cost.regions.append(Region("device", s.line, count, at.times, inner.work))

    def s_defer(self, s: Stmt, at: Frame) -> None:
        self.block(s.body, at)

    def s_submit(self, s: Stmt, at: Frame) -> None:
        self.s_plain(s, at)
        self.io(at, f"line {s.line}: a submission's completion time is the kernel's")

    # Expressions -------------------------------------------------------------------------------------------------

    def io(self, at: Frame, why: str) -> None:
        at.work.op("io", at.times)
        if why not in self.cost.unknown:
            self.cost.unknown.append(why)

    def expr(self, e: Expr, at: Frame, place: bool = False) -> None:
        tag = e.tag
        if tag == "index":
            self.access(e, at, write=False)
        elif tag == "slice":
            for a in e.args:
                self.expr(a, at)
            at.work.op("part_guard", at.times)
        elif tag == "field":
            if not isinstance(e.ref, tuple):
                self.expr(e.args[0], at, place)
        elif tag == "binary":
            for a in e.args:
                self.expr(a, at)
            kind = self.arithmetic(e)
            if kind == "int_checked" and self.size(e) is not None:
                kind = "index_checked"  # arithmetic on sizes and binders: the vectorizer checks it once per block
            at.work.op(kind, at.times)
        elif tag == "unary" and not negated_literal(e):  # a negated literal costs what a literal does: nothing
            self.expr(e.args[0], at)
            checked = e.val == "-" and e.ty is not None and e.ty.name in INT
            at.work.op("int_checked" if checked else e.ty.name if e.ty and e.ty.name in FLOAT else "int", at.times)
        elif tag == "try":
            self.expr(e.args[0], at)
            at.work.op("branch", at.times)
        elif tag == "call":
            self.call(e, at)
        elif tag == "spawn":
            self.spawn(e, at)
        else:
            for a in e.args:
                if isinstance(a, Expr):
                    self.expr(a, at)

    def arithmetic(self, e: Expr) -> str:
        ty, op = e.ty.name if e.ty else "", e.val
        if op in {"==", "!=", "<", "<=", ">", ">=", "&&", "||"}:
            return "compare"
        if ty in FLOAT:
            return f"{ty}_div" if op == "/" else ty
        if op in {"&", "|", "^"} or e.established:
            return "mul" if op == "*" else "div" if op in {"/", "%"} else "int"
        return CHECKED.get(op, "int")

    def access(self, e: Expr, at: Frame, write: bool) -> None:
        """An element read or written: a stream if its index moves with a binder, irregular if it rests on data."""
        base, index = e.args
        self.expr(base, at, place=True)
        self.expr(index, at)
        if not e.established:
            at.work.op("bounds_guard", at.times)
        at.work.op("store" if write else "load", at.times)
        key, element = path(base), self.c.sizeof(e.ty) if e.ty and e.ty.name != "void" else 8
        if self.coop is not None and self.coop.access(e, key, element, at, write):
            return
        reach = self.extent(base.ty, key) * element if base.ty is not None else Poly()
        if data_dependent(index, self.data, at.binders):  # priced by latency at the level its own view lives in
            place = (key, spelled(index))  # reading a bin and writing it back is one trip to its line
            if place not in at.seen:
                at.seen.add(place)
                add(at.work.irregular, key, at.times)
            widen(at.work.footprint, key, reach)
            return
        if not mentioned(index) & set(at.binders):
            return  # One element, the same on every pass: a register or the first cache line, not a stream.
        if (key, write) in at.seen:
            return  # A second access on the same pass is a neighbour of the first, already in cache.
        at.seen.add((key, write))
        add(at.work.writes if write else at.work.reads, key, at.times * element)
        widen(at.work.footprint, key, reach)

    def wide(self, e: Expr, access: wide.Wide, at: Frame) -> None:
        """A wide load or store (compiler/primitives/wide.py): one access of K adjacent elements and its guard, a stream of K
        elements a pass where its index moves with a binder, as K accesses at [i] would be."""
        write = e.val == "store_wide"
        at.work.op("bounds_guard", at.times)
        at.work.op("store" if write else "load", at.times)
        key, index = path(e.args[0]), e.args[1]
        if self.coop is not None and key.split(".")[0] in self.coop.local:  # a block's shared memory: its wavefronts
            at.work.op("shared_wavefront", at.times * (-(-32 * access.bytes // 128) / 32))
            add(at.work.writes if write else at.work.reads, "shared " + key.split(".")[0], at.times * access.bytes)
            return
        reach = self.extent(e.args[0].ty, key) * access.size if e.args[0].ty is not None else Poly()
        if data_dependent(index, self.data, at.binders):
            add(at.work.irregular, key, at.times)
        elif mentioned(index) & set(at.binders) and (key, write) not in at.seen:
            at.seen.add((key, write))
            add(at.work.writes if write else at.work.reads, key, at.times * access.bytes)
        widen(at.work.footprint, key, reach)

    def call(self, e: Expr, at: Frame) -> None:
        for a in e.args:
            self.expr(a, at)
        if self.coop is not None and self.coop.call(e, at):
            return
        ref = e.ref
        if isinstance(ref, Function):
            self.inline(ref, e.args, at)
            return
        kind = ref[0] if isinstance(ref, tuple) and ref else ""
        name = e.val
        if kind == "builtin" and isinstance(ref[-1], wide.Wide):
            self.wide(e, ref[-1], at)
        elif kind == "builtin" and name in atomics.NAMES:
            at.work.op("atomic", at.times)
            self.note(f"line {e.line}: an atomic update costs what its contention costs")
        elif kind == "builtin":
            if name in NUMERIC:
                at.work.op("convert" if e.established or name in FLOAT else "convert_guard", at.times)
            elif name in WRAPPING or name in {"min", "max"}:
                at.work.op("mul" if name == "mul_wrap" else "int", at.times)
                if name in {"shr", "shl_wrap"} and not e.established:
                    at.work.op("shift_guard", at.times)
            elif name == "Buf" and e.args:
                n = self.size(e.args[0]) or Poly.var(f"?capacity@{e.line}")
                element = self.c.sizeof(e.ty.args[0]) if e.ty and e.ty.args else 8
                self.cost.allocations = self.cost.allocations + at.times
                self.cost.allocated = self.cost.allocated + n * element * at.times
            elif name == "transfer":
                self.transfer(e, at)
            elif name == "mma_unordered" and len(e.args) == 3:  # a warp's fragment step (compiler/device/fragments.py)
                why = f"line {e.line}: a tensor-core fragment step is not priced"
                if why not in self.cost.unknown:
                    self.cost.unknown.append(why)
            elif name == "mma_unordered":
                self.multiply(e, at)
            elif name in {"wait", "collect"}:
                self.cost.waits = self.cost.waits + at.times
            elif name in {"take", "swap"}:
                at.work.op("move", at.times)
            elif name in {"mmio_read", "mmio_write", "asm"}:
                self.io(at, f"line {e.line}: {name} reaches the machine")
        elif kind in {"indirect", "dispatch"}:
            at.work.op("indirect", at.times)
            why = f"line {e.line}: a call through a function value or a dyn table has no body to count"
            if why not in self.cost.unknown:
                self.cost.unknown.append(why)
        elif kind == "shared":
            at.work.op("atomic", at.times)
            self.note(f"line {e.line}: an atomic or a lock costs what its contention costs")
        elif kind == "ring":
            self.io(at, f"line {e.line}: an I/O ring operation costs what the kernel takes")

    def multiply(self, e: Expr, at: Frame) -> None:
        """mma_unordered: 2mnk operations over a and b, read once each on the device, where the tensor cores
        take them, and c read and written once; on the host, the reference loop's own passes over b and c."""
        m, n, k = (self.size(x) or Poly.var(f"?{name}@{e.line}") for x, name in zip(e.args[:3], "mnk", strict=True))
        element, device = e.ref[1], e.ref[2]
        size = self.c.sizeof(element)
        c, a, b = (path(x) for x in e.args[3:])
        if device:
            body = Work()
            body.op("tensor", m * n * k * 2.0)
            body.reads |= {a: m * k * size, b: k * n * size, c: m * n * 4.0}
            body.writes[c] = m * n * 4.0
            self.cost.regions.append(Region("tensor", e.line, ONE, at.times, body, tensor=element.name))
            return
        at.work.op("f32", m * n * k * at.times * 2.0)
        at.work.op("load", m * n * k * at.times * 2.0)
        at.work.op("store", m * n * k * at.times)
        for key, moved, reach in ((a, m * k * size, m * k * size), (b, m * n * k * size, k * n * size),
                                  (c, m * n * k * 4.0, m * n * 4.0)):  # fmt: skip
            add(at.work.reads, key, moved * at.times)
            widen(at.work.footprint, key, reach)
        add(at.work.writes, c, m * n * k * 4.0 * at.times)

    def given(self, f: Function, args: list[Expr]) -> tuple[dict[str, Poly], dict[str, str]]:
        sizes, rename = {}, {}
        for (n, t), a in zip(f.params, args, strict=False):
            if t == Type("usize"):
                sizes[n] = self.size(a) or Poly.var(f"?{n}@{a.line}")
            if t.mode != "value" or t.name == "Buf":
                rename[n] = path(a)
        return sizes, rename

    def inline(self, f: Function, args: list[Expr], at: Frame) -> None:
        callee = self.function(f)
        sizes, rename = self.given(f, args)
        seen = callee.subst(sizes, rename, at.times)
        if f.extern:
            self.io(at, f"line {args[0].line if args else f.line}: {f.name} is foreign, so its time is not counted")
        at.work.op("call", at.times)
        at.work.merge(seen.seq)
        if at.lane:  # A lane's callee runs inside the lane: its own regions cannot exist there.
            for r in seen.regions:
                at.work.merge(r.body, r.count * r.runs)
            seen.regions = []
        seen.seq = Work()
        self.cost.absorb(seen)

    def spawn(self, e: Expr, at: Frame) -> None:
        call = e.args[0] if e.args else None
        if call is None or not isinstance(call.ref, Function):
            at.work.op("spawn", at.times)
            return
        for a in call.args:
            self.expr(a, at)
        sizes, rename = self.given(call.ref, call.args)
        self.cost.tasks.append((at.times, self.function(call.ref).subst(sizes, rename, ONE)))

    def transfer(self, e: Expr, at: Frame) -> None:
        src = e.args[1] if len(e.args) > 1 else e.args[0]
        dst = e.args[0]
        places = (getattr(src.ty, "place", "host"), getattr(dst.ty, "place", "host"))
        way = {("host", "device"): "h2d", ("device", "host"): "d2h", ("device", "device"): "d2d"}.get(places, "h2h")
        element = self.c.sizeof(src.ty.value) if src.ty else 8
        count = (self.extent(src.ty, path(src)) if src.ty and src.tag != "slice" else self.span(src)) or Poly.var(
            f"?extent@{e.line}"
        )
        add(self.cost.transfers, way, count * element * at.times)

    def span(self, e: Expr) -> Poly | None:
        if e.tag != "slice":
            return None
        lo, hi = self.size(e.args[1]), self.size(e.args[2])
        return None if lo is None or hi is None else hi - lo


def combining(s: Stmt) -> str:
    """One step of a reduction or a scan: an in-order float fold and a checked sum wait on the step before; wrapping,
    min and max reassociate."""
    return f"{s.ty.name}_fold" if s.ty.name in FLOAT else "int_fold" if s.op == "+" else "int"


def fold(target: Expr, value: Expr, binders: tuple[str, ...]) -> str | None:
    """`acc = acc op x` inside a loop: each pass waits for the last one's result, so it costs the latency of `op`."""
    if not binders or target.tag != "name" or value.tag != "binary" or value.val not in {"+", "-", "*"}:
        return None
    if value.args[0].tag != "name" or value.args[0].val != target.val or value.ty is None:
        return None
    if value.ty.name in FLOAT:
        return f"{value.ty.name}_fold"
    return "int_fold" if value.ty.name in INT and not value.established else None


def spelled(e: Expr) -> str:
    """An expression as a key: two accesses at the same spelling on one pass reach the same element."""
    inner = ",".join(spelled(a) for a in e.args if isinstance(a, Expr))
    return f"{e.tag}:{e.val}({inner})"


def mentioned(e: Expr) -> set[str]:
    found = {e.val} if e.tag == "name" else set()
    for a in e.args:
        if isinstance(a, Expr):
            found |= mentioned(a)
    return found


def count(program: Any, checker: Any, names: set[str] | None = None) -> dict[str, Cost]:
    """The cost of every written function of a checked program, or of `names` alone."""
    counter = Counter(program, checker)
    chosen = [f for f in program.functions if not f.extern and (names is None or f.name in names)]
    return {f.name: counter.function(f) for f in chosen}

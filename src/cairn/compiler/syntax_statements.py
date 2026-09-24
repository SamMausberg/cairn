"""The parser's statements: declarations of locals, control flow, loops, regions, the contracted forms
`reduce`, `compact` and `scan`, and the statements only a recipe has."""

from __future__ import annotations

from typing import Any

from .lexing import COMPOUND, IDENT, Token, unescape
from .syntax_expressions import ARM_STATEMENTS, REDUCERS, ExpressionParser, copied
from .tree import USIZE, Arm, Assembly, Diagnostic, Expr, Stmt, Type, fail


class StatementParser(ExpressionParser):
    def arm_body(self) -> list[Stmt]:
        """A block, or one simple statement standing for it: `None => return 0;` is `None => { return 0; }`."""
        if self.t.s == "{":
            return self.block()
        s = self.stmt()
        if s.tag not in ARM_STATEMENTS:
            fail("E-PARSE", "An arm without braces is one return, break, continue, assignment or call; "
                 "write a block for anything else.", s)  # fmt: skip
        return [s]

    def block(self) -> list[Stmt]:
        self.need("{")
        body = []
        while not self.eat("}"):
            if self.t.s == "<eof>":
                fail("E-PARSE", "Unclosed block.", self.t)
            body.append(self.stmt())
        return body

    def generator(self, opener: str = "for") -> tuple[str, Expr]:
        """`for i in n`, shared by the contracted forms; a reduction may open with `parallel` instead."""
        self.need(opener)
        binder = self.ident()
        self.need("in")
        return binder, self.expr()

    def scanning(self) -> bool:
        """`scan op [exclusive] out for|parallel i in n yield v`: a word only here, an ordinary name elsewhere."""
        k = 3 if self.ahead(2) == "exclusive" and self.ahead(3) not in {"for", "parallel"} else 2
        return self.t.s == "scan" and self.ahead(1) in REDUCERS and self.ahead(k + 1) in {"for", "parallel"}

    def contracted(self, t: Token, tag: str, name: str, typ: Type | None) -> Stmt:
        form = self.take()
        if tag != "let" or (form == "compact" and typ not in (None, USIZE)):
            fail("E-COLLECT-BINDING", f"{'A scan' if form == 'scan' else 'Compaction'} binds an immutable result.", t)
        at: dict[str, Any] = {"line": t.line, "col": t.col}
        if form == "scan":  # The operator, then `exclusive` unless that is the output's own name.
            op = self.take()
            exclusive = self.t.s == "exclusive" and self.ahead(1) not in {"for", "parallel"}
            self.i += exclusive
            out, pooled = Expr("name", self.ident(), **at), self.t.s == "parallel"
            binder, hi = self.generator("parallel" if pooled else "for")
            self.need("yield")
            store = Expr("index", "", [Expr("name", out.val, **at), Expr("name", binder, **at)], **at)  # out[i]
            es = [out, hi, self.expr(), store]
            self.need(";")
            return Stmt(form, name, typ, es, binder=binder, op=op, pooled=pooled, exclusive=exclusive, **at)
        if form == "compact":
            out = Expr("name", self.ident(), **at)
            binder, hi = self.generator()
            self.need("where")
            pred = self.expr()
            self.need("yield")
            es, op, typ = [out, hi, pred, self.expr()], "", USIZE
        else:
            op = self.t.s
            if op not in REDUCERS:
                fail("E-REDUCE-OP", f"reduce accepts one of {sorted(REDUCERS)}.", self.t)
            self.i += 1
            if self.t.s == "warp" and self.ahead(1) == "yield":  # `reduce + warp yield v`: over one warp's threads
                self.i += 2
                value = self.expr()
                self.need(";")
                return Stmt("warp_reduce", name, typ, [value], op=op, **at)
            pooled = self.t.s == "parallel"
            binder, hi = self.generator("parallel" if pooled else "for")
            self.need("yield")
            es = [hi, self.expr()]
        self.need(";")
        return Stmt(form, name, typ, es, binder=binder, op=op, pooled=form == "reduce" and pooled, **at)

    def cooperative(self, at: dict[str, Any]) -> Stmt:
        """`blocks b in G threads t in T { }`, up to three names and extents a side; `blocks` and `threads` are words
        only here. The block names come first in `other_names`, and `op` says how many there are. A finish, `then
        threads t in T { }`, is the one statement in `other`: its thread names and extents, and its body."""
        self.need("blocks")
        names = self.binders()
        extents = self.extents(len(names))
        if self.t.s != "threads":
            fail("E-PARSE", "A cooperative region is `blocks b in G threads t in T { ... }`.", self.t)
        self.i += 1
        count = len(names)
        names += self.binders()
        extents += self.extents(len(names) - count)
        body, finish = self.block(), []
        if self.t.s == "then" and self.ahead(1) == "threads":  # `then` is a word only here
            where: dict[str, Any] = {"line": self.t.line, "col": self.t.col}
            self.i += 2
            threads = self.binders()
            finish = [Stmt("finish", exprs=self.extents(len(threads)), body=self.block(), other_names=threads, **where)]
        return Stmt("blocks", exprs=extents, body=body, other=finish, other_names=names, op=str(count), **at)

    def binders(self) -> list[Expr]:
        found: list[Expr] = []
        while not found or self.eat(","):
            found.append(Expr("name", self.t.s, [], self.t.line, self.t.col))
            self.ident()
        return found

    def extents(self, count: int) -> list[Expr]:
        self.need("in")
        found = [self.expr()]
        while self.eat(","):
            found.append(self.expr())
        if len(found) != count:
            fail("E-PARSE", f"{count} name(s) take {count} extent(s); {len(found)} are written.", self.t)
        return found

    def require(self, t: Token) -> Stmt:
        """`require unsigned(f), "message";` states a recipe's admissible inputs."""
        self.need("require")
        condition = self.expr()
        self.need(",")
        if self.t.s[0] != '"':
            fail("E-PARSE", 'require states its message: require condition, "why";', self.t)
        self.i += 1
        self.need(";")
        return Stmt("require", unescape(self.ts[self.i - 2]), exprs=[condition], line=t.line, col=t.col)

    def assembly(self, t: Token) -> Stmt:
        """`asm [volatile] target [capability] "template" (operands) [clobbers(...)] [effects(...)];`, typed assembly.
        `asm`, `volatile`, `out` and `clobbers` are words only here: `asm("wfi")` and a program's `asm` keep theirs."""
        self.i += 1
        volatile = self.eat("volatile")
        target = self.ident()
        capability = self.ident() if IDENT.fullmatch(self.t.s) else ""
        if self.t.s[0] != '"':
            fail("E-PARSE", 'Typed assembly is asm target "instructions" (operands) effects(...);', self.t)
        template = unescape(self.t)
        self.i += 1
        outputs: list[tuple[str, Type, bool, int, int]] = []
        exprs: list[Expr] = []
        if self.eat("("):
            while self.t.s != ")":
                at = self.t
                if at.s == "out" and IDENT.fullmatch(self.ahead(1)) and self.ahead(2) == ":":
                    if len(exprs) > sum(started for _, _, started, _, _ in outputs):
                        fail("E-ASM-OPERANDS", "Outputs come before inputs, as the template numbers them.", at)
                    self.i += 1
                    name = self.ident()
                    self.need(":")
                    ty = self.ty()
                    started = self.eat("=")
                    exprs += [self.expr()] if started else []
                    outputs.append((name, ty, started, at.line, at.col))
                else:
                    exprs.append(self.expr())
                if not self.eat(","):
                    break
            self.need(")")
        clobbers: list[str] = []
        if self.t.s == "clobbers" and self.ahead(1) == "(":
            self.i += 2
            clobbers = self.listed(")", self.ident)
        effects: list[str] = []
        if self.eat("effects"):
            self.need("(")
            while not self.eat(")"):
                name = self.take()
                while self.eat(":"):
                    name += ":" + self.take()
                effects.append(name)
                if self.t.s != ")":
                    self.need(",")
        self.need(";")
        s = Stmt("asm", target, exprs=exprs, line=t.line, col=t.col)
        s.assembly = Assembly(target, capability, template, outputs, tuple(effects), volatile, tuple(clobbers))
        return s

    def stmt(self) -> Stmt:
        t = self.t
        if t.s == "asm" and IDENT.fullmatch(self.ahead(1)):  # typed assembly; asm("wfi") stays a call
            return self.assembly(t)
        at: dict[str, Any] = {"line": t.line, "col": t.col}
        if self.recipe and t.s == "each":
            return Stmt("each", ref=self.each(self.block), **at)
        if self.recipe and t.s == "require":
            return self.require(t)
        if self.scanning():  # A scan whose total nobody reads.
            return self.contracted(t, "let", "", None)
        if t.s == "blocks" and IDENT.fullmatch(self.ahead(1)) and self.ahead(2) in {",", "in"}:
            return self.cooperative(at)
        if t.s == "shared" and IDENT.fullmatch(self.ahead(1)) and self.ahead(2) == ":":  # a block's shared array
            self.i += 1
            n = self.ident()
            self.need(":")
            element = Type(self.path())
            self.need("[")
            extent = self.expr()
            self.need("]", "=", "zeroed", ";")
            return Stmt("shared", n, element, [extent], **at)
        if t.s == "pipeline" and IDENT.fullmatch(self.ahead(1)) and self.ahead(2) == ":":  # stages a block fills
            self.i += 1
            n = self.ident()
            self.need(":")
            element = Type(self.path())
            self.need("[")
            size = self.expr()
            self.need("]", "depth")
            depth = self.expr()
            self.need(";")
            return Stmt("pipeline", n, element, [size, depth], **at)
        if t.s == "barrier" and self.ahead(1) == ";":  # every thread of a cooperative block meets here
            self.i += 2
            return Stmt("barrier", **at)
        if t.s in {"buffer", "stack"}:
            self.i += 1
            n = self.ident()
            self.need(":")
            mark = self.i
            try:  # In `T[n]` the brackets are the capacity; in `Pair[u8][n]` the first are arguments.
                element = self.ty()
                generic = self.t.s == "["
            except Diagnostic:
                generic = False
            if not generic:
                self.i = mark
                element = Type(self.path())
            self.need("[")
            extent = self.expr()
            self.need("]")
            place = self.place()
            self.need("=", "zeroed", ";")
            return Stmt(t.s, n, Type(element.name, args=element.args, place=place), [extent], **at)
        if t.s in {"let", "reg"}:
            self.i += 1
            tag = "reg" if t.s == "let" and self.eat("mut") else t.s
            if self.ahead(1) in {"(", "."}:  # `let Conn(sock, sent) = c;` takes a record apart, as `Conn(..)` built it.
                record = self.path()
                self.need("(")
                names = self.listed(
                    ")", lambda: Expr("name", self.ident(), [], self.ts[self.i - 1].line, self.ts[self.i - 1].col)
                )
                self.need("=")
                whole = self.expr()
                self.need(";")
                return Stmt("unpack", record, None, [whole], op=tag, other_names=names, **at)
            n = self.ident()
            typ = self.ty() if self.eat(":") else None
            self.need("=")
            if self.t.s in {"compact", "reduce"} or self.scanning():
                return self.contracted(t, tag, n, typ)
            e = self.expr()
            self.need(";")
            return Stmt(tag, n, typ, [e], **at)
        if t.s in {"break", "continue"}:
            self.i += 1
            self.need(";")
            return Stmt(t.s, **at)
        if self.eat("return"):
            es = [] if self.t.s == ";" else [self.expr()]
            self.need(";")
            return Stmt("return", exprs=es, **at)
        if self.eat("if"):
            e = self.expr()
            b = self.block()
            o: list[Stmt] = []
            if self.eat("else"):
                o = [self.stmt()] if self.t.s == "if" else self.block()
            return Stmt("if", exprs=[e], body=b, other=o, **at)
        if self.eat("match"):
            scrutinee = self.expr()
            self.need("{")
            arms = []
            while not self.eat("}"):
                a = self.t
                name = self.path()  # `Some(v)` or `Option.Some(v)`: a bare variant is the subject's.
                binder = ""
                if self.eat("("):
                    binder = self.ident()
                    self.need(")")
                self.need("=>")
                arms.append(Arm(name, binder, self.arm_body(), a.line, a.col))
            return Stmt("match", exprs=[scrutinee], arms=arms, **at)
        if self.eat("while"):
            e = self.expr()
            return Stmt("while", exprs=[e], body=self.block(), **at)
        if t.s in {"for", "parallel"}:
            self.i += 1
            n = self.ident()
            index = ""
            if t.s == "for" and self.eat(","):  # `for i, x in xs`: the position, then the element
                index, n = n, self.ident()
            self.need("in")
            lo = self.expr()
            if t.s == "parallel":
                region = Stmt("parallel", n, exprs=[lo], **at)
                region.other_names = self.after()  # Only `spawn parallel ... after t { }` may order itself.
                region.body = self.block()
                return region
            if self.t.s == "{" or index:  # `for x in xs { }` walks the elements; the checker writes the index loop
                return Stmt("for", n, exprs=[lo], body=self.block(), op="elements", binder=index, **at)
            self.need("..")
            return Stmt("for", n, exprs=[lo, self.expr()], body=self.block(), **at)
        if self.eat("each"):
            n = self.ident()
            self.need("in")
            hi = self.expr()
            return Stmt("for", n, exprs=[Expr("int", "0", **at), hi], body=self.block(), **at)
        if self.eat("defer"):
            return Stmt("defer", body=[self.stmt()], **at)
        if self.eat("unsafe"):
            return Stmt("unsafe", body=self.block(), **at)
        if t.s == "{":
            return Stmt("block", body=self.block(), **at)
        e = self.expr()
        if e.tag == "spawn" and self.t.s == "into" and IDENT.fullmatch(self.ahead(1)):  # `spawn f(args) into g;`
            self.i += 1  # A word only here, like `after`: the task joins a group instead of naming a ticket.
            e.val = "into"
            group = self.ident()
            self.need(";")
            return Stmt("submit", group, exprs=[e], **at)
        if self.eat("="):
            v = self.expr()
            self.need(";")
            return Stmt("assign", exprs=[e, v], **at)
        if self.t.s in COMPOUND:  # `p += v` is checked as `p = p + v`; the emitter evaluates `p` once.
            op = COMPOUND[self.t.s]
            self.i += 1
            v = self.expr()
            self.need(";")
            return Stmt("assign", exprs=[e, Expr("binary", op, [copied(e), v], e.line, e.col)], op=op, **at)
        self.need(";")
        return Stmt("expr", exprs=[e], **at)

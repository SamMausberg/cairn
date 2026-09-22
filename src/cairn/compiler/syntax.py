"""The parser: syntax and source ranges only.

Tokens come from lexing.py and the tree it builds is tree.py; every name the parser produces is
resolved, typed and given effects by checking.py. Nothing here evaluates source.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .lexing import COMPOUND, IDENT, NUMBER, RESERVED, Token, lex, unescape
from .tree import INTRINSIC_TYPES as INTRINSIC_TYPES  # The language server takes the vocabulary from here.
from .tree import (
    PLACES,
    USIZE,
    VOID,
    Arm,
    Diagnostic,
    Each,
    Expr,
    Function,
    Impl,
    Program,
    Recipe,
    Shape,
    Stmt,
    Type,
    fail,
)
from .tree import SCALAR as SCALAR

PREC = {"||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "<": 7, "<=": 7, ">": 7, ">=": 7}
PREC |= {"+": 8, "-": 8, "*": 9, "/": 9, "%": 9}
REDUCERS = {"+", "*", "&", "|", "^", "add_wrap", "mul_wrap", "min", "max"}
ARM_STATEMENTS = {"return", "break", "continue", "assign", "expr"}  # what an arm may be without braces


def copied(e: Expr) -> Expr:
    """The place `p += v` reads, as a node of its own: checked and annotated apart from the place it writes, and
    without a source span, so the one place written is the one site a hover or an edit sees."""
    return Expr(e.tag, e.val, [copied(a) for a in e.args], e.line, e.col, ref=e.ref)


class Parser:
    def __init__(self, source: str):
        self.ts = lex(source)
        self.i = self.depth = 0
        self.module = ""
        self.recipe = False  # Inside a recipe: `$` splices, each, where, require and fold are syntax.

    @property
    def t(self) -> Token:
        return self.ts[self.i]

    def ahead(self, k: int) -> str:
        return self.ts[min(self.i + k, len(self.ts) - 1)].s

    def eat(self, s: str) -> bool:
        if self.t.s == s:
            self.i += 1
            return True
        return False

    def need(self, *expected: str):
        for s in expected:
            if not self.eat(s):
                fail("E-PARSE", f"Expected {s!r}, found {self.t.s!r}.", self.t)

    def ident(self) -> str:
        t = self.t
        if "$" in t.s and not self.recipe:  # `$` splices exist only inside a recipe.
            fail("E-LEX", "Unexpected character '$'.", t)
        if not IDENT.fullmatch(t.s) or t.s in RESERVED:
            fail("E-NAME", f"Expected an identifier, found {t.s!r}.", t)
        self.i += 1
        return t.s

    def path(self) -> str:
        name = self.ident()
        while self.t.s == "." and IDENT.fullmatch(self.ahead(1)):
            self.i += 1
            name += "." + self.ident()
        return name

    def integer(self) -> int:
        if not self.t.s.isdigit():
            fail("E-STATIC", "Expected a nonnegative integer literal.", self.t)
        self.i += 1
        return int(self.ts[self.i - 1].s)

    def listed(self, close: str, item):
        """Comma-separated items up to `close`; the opener was already consumed."""
        out = []
        if not self.eat(close):
            out.append(item())
            while self.eat(","):
                out.append(item())
            self.need(close)
        return out

    def place(self) -> str:
        if not self.eat("@"):
            return "host"
        if self.t.s not in PLACES:
            fail("E-PLACE", f"Unknown placement {self.t.s!r}; expected one of {PLACES}.", self.t)
        self.i += 1
        return self.ts[self.i - 1].s

    def ty(self) -> Type:
        if self.t.s in {"ro", "rw"}:
            mode = self.t.s
            self.i += 1
            self.need("<")
            inner = self.ty()
            self.need(">")
            extent = ""
            if self.eat("["):
                extent = str(self.integer()) if self.t.s.isdigit() else self.ident()
                self.need("]")
            return Type(inner.name, mode, extent, inner.args, self.place() if extent else "host")
        if self.eat("fn"):
            self.need("(")
            params = self.listed(")", self.ty)
            return Type("fn", args=(*params, self.ty() if self.eat("->") else VOID))
        if self.eat("dyn"):
            return Type("dyn", args=(Type(self.path()),))
        name = self.path()
        if self.eat("["):
            return Type(name, args=tuple(self.listed("]", self.type_argument)))
        return Type(name)

    def type_argument(self):
        return self.integer() if self.t.s.isdigit() else self.ty()

    def generic_parameters(self) -> list[tuple[str, str]]:
        def parameter():
            name = self.ident()
            if not self.eat(":"):
                return name, "type"
            if self.t.s in {"nat", "type"} or (self.recipe and self.t.s == "fn"):
                self.i += 1
                return name, self.ts[self.i - 1].s
            bounds = [self.path()]
            while self.eat("+"):  # K: Hash + Eq
                bounds.append(self.path())
            return name, "+".join(bounds)

        return self.listed("]", parameter) if self.eat("[") else []

    def parameter(self) -> tuple[str, Type]:
        name = self.ident()
        self.need(":")
        return name, self.ty()

    # Expressions -----------------------------------------------------------------------------

    def expr(self, prec: int = 0) -> Expr:
        start = self.i
        self.depth += 1
        if self.depth > 100:
            fail("E-DEPTH", "Expression nesting exceeds 100.", self.t)
        t = self.t
        at = (t.line, t.col)
        if self.eat("("):
            e = self.expr()
            self.need(")")
        elif t.s in {"-", "!", "~"}:
            self.i += 1
            e = Expr("unary", t.s, [self.expr(10)], *at)
        elif t.s in {"try", "spawn"}:
            self.i += 1
            if t.s == "spawn" and self.t.s == "parallel":  # Queued device work: the region itself is the operand.
                region = self.stmt()
                e = Expr("spawn", "", region.other_names, *at, ref=region)
            else:
                e = Expr(t.s, "", [self.expr(10)], *at)
                e.args += self.after() if t.s == "spawn" else []
        elif t.s in {"|", "||"}:
            e = self.closure()
        elif self.recipe and t.s == "fold":
            self.i += 1
            combiner = self.path() if IDENT.fullmatch(self.t.s) and self.t.s not in RESERVED else self.t.s
            self.i += combiner in PREC
            if self.t.s != "each" or not (combiner in PREC or IDENT.fullmatch(combiner.split(".")[0])):
                fail("E-PARSE", "fold takes an operator or a function of two operands, then what it joins: "
                     "fold + each f in R { .. } or fold lib.chain each f in R { .. }.", t)  # fmt: skip
            e = Expr("fold", combiner, [self.expr(10)], *at)
        elif self.recipe and t.s == "each":
            e = Expr("each", "", [], *at, ref=self.each(lambda: self.need("{") or self.listed("}", self.expr)))
        elif self.eat("true") or self.eat("false"):
            e = Expr("bool", t.s, [], *at)
        elif NUMBER.match(t.s):
            self.i += 1
            e = Expr("float" if any(c in t.s for c in ".eE") else "int", t.s, [], *at)
        elif t.s.startswith("0x"):
            self.i += 1
            e = Expr("int", str(int(t.s, 16)), [], *at)
        elif t.s[0] in "\"'":
            self.i += 1
            text = unescape(t)
            if t.s[0] == "'" and len(text.encode("latin-1", "replace")) != 1:
                fail("E-LEX", "A character literal is exactly one byte.", t)
            e = Expr("str", text, [], *at) if t.s[0] == '"' else Expr("int", str(ord(text)), [], *at)
        else:
            e = Expr("name", self.ident(), [], *at)
        e.start, e.end = self.ts[start].start, self.ts[self.i - 1].end
        while True:
            if self.eat("("):
                e = self.call(e, self.listed(")", self.expr))
            elif self.eat("["):
                first = self.expr()
                if self.eat(".."):
                    e = Expr("slice", "", [e, first, self.expr()], e.line, e.col)
                else:
                    rest = []
                    while self.eat(","):
                        rest.append(self.expr())
                    e = Expr("index", "", [e, first, *rest], e.line, e.col)
                self.need("]")
            elif self.eat("."):
                e = Expr("field", self.ident(), [e], e.line, e.col)
            elif self.t.s in PREC and PREC[self.t.s] >= prec:
                op = self.t.s
                self.i += 1
                if op in {"<", ">"} and self.t.s == op and self.t.start == self.ts[self.i - 1].end:
                    fail("E-PARSE", f"There is no {op * 2} operator: shifts are shl_wrap(x, k) and shr(x, k).", self.t)
                e = Expr("binary", op, [e, self.expr(PREC[op] + 1)], e.line, e.col)
            else:
                break
            e.start, e.end = self.ts[start].start, self.ts[self.i - 1].end
        self.depth -= 1
        return e

    def call(self, callee: Expr, args: list[Expr]) -> Expr:
        """Direct, qualified, explicitly instantiated and method calls share one node."""
        targs = None
        if callee.tag == "index" and callee.args[0].tag in {"name", "field"}:
            targs = tuple(self.as_type(a) for a in callee.args[1:])
            callee = callee.args[0]
        name = self.dotted(callee)
        if name is not None:
            return Expr("call", name, args, callee.line, callee.col, ref=targs)
        if callee.tag == "field":  # A method on an arbitrary receiver: f(receiver, ...).
            return Expr("call", "." + callee.val, [callee.args[0], *args], callee.line, callee.col)
        fail("E-CALL", "Only direct calls, methods and qualified constructors are supported.", callee)

    def dotted(self, e: Expr) -> str | None:
        if e.tag == "name":
            return e.val
        base = self.dotted(e.args[0]) if e.tag == "field" else None
        return None if base is None else base + "." + e.val

    def as_type(self, e: Expr):
        if e.tag == "int":
            return int(e.val)
        if e.tag == "index":
            return Type(self.as_type(e.args[0]).name, args=tuple(self.as_type(a) for a in e.args[1:]))
        name = self.dotted(e)
        if name is None:
            fail("E-TYPE", "Expected a type argument.", e)
        return Type(name)

    def closure(self) -> Expr:
        t = self.t
        params = [] if self.eat("||") else (self.need("|"), self.listed("|", self.parameter))[1]
        ret = self.ty() if self.eat("->") else VOID
        f = Function("", params, ret, self.block(), line=t.line, col=t.col)
        return Expr("lambda", "", [], t.line, t.col, ref=f)

    # Statements ------------------------------------------------------------------------------

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

    def contracted(self, t: Token, tag: str, name: str, typ: Type | None) -> Stmt:
        form = self.t.s
        self.i += 1
        if tag != "let" or (form == "compact" and typ not in (None, USIZE)):
            fail("E-COLLECT-BINDING", "Compaction binds an immutable usize result.", t)
        at: dict[str, Any] = {"line": t.line, "col": t.col}
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
            pooled = self.t.s == "parallel"
            binder, hi = self.generator("parallel" if pooled else "for")
            self.need("yield")
            es = [hi, self.expr()]
        self.need(";")
        return Stmt(form, name, typ, es, binder=binder, op=op, pooled=form == "reduce" and pooled, **at)

    def after(self) -> list[Expr]:
        """`after a, b`: tickets whose queued work runs first. A word only here, not a reserved one."""
        names: list[Expr] = []
        if self.t.s == "after" and self.ahead(1) not in {"=", ".", "(", "["}:
            self.i += 1
            names.append(Expr("name", self.ident(), [], self.t.line, self.t.col))
            while self.eat(","):
                names.append(Expr("name", self.ident(), [], self.t.line, self.t.col))
        return names

    def each(self, items) -> Each:
        """`each f in R where at = offset(f) { ... }` or `each b in 0..bytes(f) { ... }`."""
        self.need("each")
        binder = self.ident()
        self.need("in")
        seq = [self.expr()] + ([self.expr()] if self.eat("..") else [])
        return Each(binder, seq, self.where(), items())

    def where(self) -> list[tuple[str, Expr]]:
        named: list[tuple[str, Expr]] = []
        while self.eat("where") or (named and self.eat(",")):
            name = self.ident()
            self.need("=")
            named.append((name, self.expr()))
        return named

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

    def items(self) -> list[Any]:
        """The declarations of a recipe (or of an `each` inside one)."""
        self.need("{")
        found: list[Any] = []
        while not self.eat("}"):
            t = self.t
            public = self.eat("pub")
            if self.t.s == "each":
                found.append(self.each(self.items))
            elif self.t.s == "require":
                found.append(self.require(t))
            elif self.eat("struct"):
                found.append(Shape(self.ident(), self.shape(), public, t.line, t.col))
            elif self.eat("impl"):
                trait = self.path()
                self.need("for")
                found.append(Impl(trait, self.ty(), [], self.i))
                self.need("{")
                while not self.eat("}"):
                    start = self.t
                    self.need("fn")
                    found[-1].members.append(self.function(start, public=True))
            else:
                kernel = self.eat("kernel")
                self.need("fn")
                found.append(self.function(t, public=public, kernel=kernel))
        return found

    def shape(self) -> list[Any]:
        """A recipe's record: `(name, type, extent)` per field, where `$f:Buf[$t][rows];` names an extent field."""
        self.need("{")
        fields: list[Any] = []
        while not self.eat("}"):
            if self.t.s == "each":
                fields.append(self.each(self.shape))
            else:
                name, ty = self.parameter()
                extent = ""
                if self.eat("["):
                    extent = self.ident()
                    self.need("]")
                fields.append((name, ty, extent))
                self.need(";")
        return fields

    def stmt(self) -> Stmt:
        t = self.t
        at: dict[str, Any] = {"line": t.line, "col": t.col}
        if self.recipe and t.s == "each":
            return Stmt("each", ref=self.each(self.block), **at)
        if self.recipe and t.s == "require":
            return self.require(t)
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
            if self.t.s in {"compact", "reduce"}:
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

    # Declarations ----------------------------------------------------------------------------

    def function(self, t: Token, *, bodiless: bool = False, **flags) -> Function:
        n = self.ident()
        generics = self.generic_parameters()
        self.need("(")
        ps = self.listed(")", self.parameter)
        ret = self.ty() if self.eat("->") else VOID
        effects = None
        if self.eat("pure"):
            effects = ("pure",)
        elif self.eat("effects"):
            self.need("(")
            effects = tuple(self.listed(")", self.effect))
        body_start = self.t.start
        body: list[Stmt] = []
        if bodiless:
            self.need(";")
        elif self.eat("="):
            if ret == VOID:
                fail("E-EXPRESSION-BODY", "An expression body needs an explicit nonvoid return type.", t)
            value = self.expr()
            self.need(";")
            body = [Stmt("return", exprs=[value], line=value.line, col=value.col)]
        else:
            body = self.block()
        end = self.ts[self.i - 1].end
        return Function(
            n, ps, ret, body, generics, source_name=n, line=t.line, col=t.col, start=t.start,
            body_start=body_start, end=end, module=self.module, effects=effects, **flags,
        )  # fmt: skip

    def effect(self) -> str:
        self.i += 1
        name = self.ts[self.i - 1].s
        while self.eat(":"):
            self.i += 1
            name += ":" + self.ts[self.i - 1].s
        return name

    def parse(self) -> Program:
        p = Program()

        def declare(name: str, t: Token) -> str:
            full = f"{self.module}.{name}" if self.module else name
            if full in p.modules:
                fail("E-DUPLICATE", f"Duplicate declaration {name}.", t)
            p.modules[full] = self.module
            if public:
                p.public.add(full)
            return full

        while self.t.s != "<eof>":
            t = self.t
            public = self.eat("pub")
            if self.eat("module"):
                self.module = self.path()
                self.need(";")
            elif self.eat("import"):
                path = self.path()
                alias = self.ident() if self.eat("as") else path.rsplit(".", 1)[-1]
                p.imports.append((self.module, path, alias))
                if self.eat("("):  # import m (A, b); also brings those names in unqualified.
                    for name in self.listed(")", self.ident):
                        p.uses[self.module, name] = f"{path}.{name}"
                self.need(";")
            elif self.eat("const"):
                n = self.ident()
                self.need(":")
                typ = self.ty()
                self.need("=")
                p.consts[declare(n, t)] = (typ, self.expr())
                self.need(";")
            elif self.t.s in {"struct", "linear"}:
                attributes = {"linear"} if self.eat("linear") else set()
                self.need("struct")
                n = self.ident()
                generics = self.generic_parameters()
                if self.eat("packed"):
                    attributes.add("packed")
                elif self.eat("align"):
                    self.need("(")
                    boundary = self.integer()
                    if boundary & (boundary - 1) or not 8 <= boundary <= 65536:
                        fail("E-ALIGN", "align(n) raises a record's alignment: a power of two from 8 to 65536.", t)
                    attributes.add(f"align({boundary})")
                    self.need(")")
                self.need("{")
                fs, carried = [], {}
                while not self.eat("}"):
                    fs.append(self.parameter())
                    if self.eat("["):  # `price:Buf[f64][rows]`: this field holds as many elements as `rows` says.
                        carried[fs[-1][0]] = self.ident()
                        self.need("]")
                    self.need(";")
                full = declare(n, t)
                p.records[full], p.generics[full], p.attributes[full] = fs, generics, attributes
                if carried:
                    p.field_extents[full] = carried
            elif self.eat("enum"):
                n = self.ident()
                generics = self.generic_parameters()
                self.need("{")
                vs: list[tuple[str, Type | None]] = []
                while not self.eat("}"):
                    variant = self.ident()
                    payload = None
                    if self.eat("("):
                        payload = self.ty()
                        self.need(")")
                    vs.append((variant, payload))
                    self.need(";")
                full = declare(n, t)
                if generics or any(ty is not None for _, ty in vs):
                    p.sums[full] = vs
                else:
                    p.enums[full] = [v for v, _ in vs]
                p.generics[full] = generics
            elif self.eat("trait"):
                n = self.ident()
                self.need("{")
                members = []
                while not self.eat("}"):
                    start = self.t
                    self.need("fn")
                    members.append(self.function(start, bodiless=True))
                p.traits[declare(n, t)] = members
            elif self.eat("impl"):
                block = (self.module, self.i)
                generics = self.generic_parameters()
                trait = self.path()
                self.need("for")
                target = self.ty()
                self.need("{")
                while not self.eat("}"):
                    start = self.t
                    self.need("fn")
                    f = self.function(start, public=True, owner=(trait, target))
                    f.generics, f.block = generics + f.generics, block
                    f.name = f.source_name = declare(f"{trait}.{target.display()}.{f.name}", start)
                    p.functions.append(f)
            elif self.eat("extern"):
                symbol = unescape(self.t) if self.t.s[0] == '"' else ""  # extern "close" fn close_fd(...)
                if symbol and not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9$.@]*", symbol):
                    fail("E-EXTERN", "An extern's link name is one C symbol.", self.t)
                self.i += bool(symbol)
                self.need("fn")
                f = self.function(t, bodiless=True, extern=True, public=public, symbol=symbol)
                f.name = f.source_name = declare(f.name, t)
                p.functions.append(f)
            elif self.t.s in {"fn", "kernel"}:
                kernel = self.eat("kernel")
                self.need("fn")
                f = self.function(t, public=public, kernel=kernel)
                f.name = f.source_name = declare(f.name, t)
                p.functions.append(f)
            elif self.t.s == "recipe" and IDENT.fullmatch(self.ahead(1)):
                self.i += 1
                name, first, self.recipe = declare(self.ident(), t), self.i - 2 - public, True
                kinds = self.generic_parameters()
                if any(kind not in {"nat", "fn"} for _, kind in kinds):
                    fail("E-RECIPE", "A recipe's bracket parameters are naturals (K:nat) and function names (F:fn); "
                         "the type it is derived for follows `for`.", t)  # fmt: skip
                param = self.ident() if self.eat("for") else ""
                recipe = Recipe(name, kinds, param, self.where(), self.items(), self.module, public, t.line, t.col)
                recipe.start, recipe.end, self.recipe = self.ts[first].start, self.ts[self.i - 1].end, False
                text = " ".join(x.s for x in self.ts[first : self.i])
                recipe.digest = hashlib.sha256(text.encode()).hexdigest()
                p.recipes[name] = recipe
            elif self.t.s == "test" and IDENT.fullmatch(self.ahead(1)):  # A word only here: `test sums { ... }`.
                self.i += 1
                n, head = self.ident(), self.t
                if head.s != "{" or f"{self.module}.test${n}".lstrip(".") in p.modules:
                    fail(
                        "E-TEST",
                        f"A test is `test {n} {{ ... }}`: one name per module, no parameters, no result.",
                        head,
                    )
                f = Function(f"test${n}", [], VOID, self.block(), source_name=n, line=t.line, col=t.col, start=t.start,
                             body_start=head.start, end=self.ts[self.i - 1].end, module=self.module, test=True)  # fmt: skip
                f.name = f.source_name = declare(f.name, t)
                p.functions.append(f)
            elif self.t.s == "plan" and IDENT.fullmatch(self.ahead(1)):  # A word only here: `plan f { grain 64; }`.
                self.i += 1
                name, chosen = self.path(), {}
                self.need("{")
                while not self.eat("}"):  # Items and their ranges are the checker's (concurrency.PLAN_ITEMS).
                    item = self.t
                    if not IDENT.fullmatch(item.s) or item.s in chosen:
                        fail("E-PLAN", "A plan sets each of its items once, as a name and a natural.", item)
                    self.i += 1
                    chosen[item.s] = self.integer()
                    self.need(";")
                p.plans.append((self.module, name, chosen, t))
            elif self.eat("derive"):
                written, naturals = self.path(), []
                if self.eat("["):  # A natural, or the name of a function as the deriving module sees it.
                    naturals = self.listed("]", lambda: self.integer() if self.t.s[0].isdigit() else self.path())
                onto = self.path() if self.eat("for") else ""
                p.derivations.append((self.module, written, tuple(naturals), onto, t))
                self.need(";")
            elif self.eat("family"):
                pre = self.ident()
                self.need("=")
                base = self.path()
                self.need("[")
                lo = self.integer()
                self.need("..")
                hi = self.integer()
                self.need("]", ";")
                p.families.append((declare(pre, t), base, lo, hi))  # Its instances belong to this module.
            else:
                fail(
                    "E-DECLARATION",
                    f"Unsupported declaration {self.t.s!r}; expected fn, struct, enum, trait, impl, "
                    "const, extern, module, import, family, or derive wire.",
                    self.t,
                )
        return p

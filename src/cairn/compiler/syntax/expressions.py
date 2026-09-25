"""The parser's token cursor, types and expressions.

`statements.py` adds statements and `parser.py` adds declarations and the entry point; the three are one
`Parser`. Nothing here evaluates source.
"""

from __future__ import annotations

from .lexing import IDENT, NUMBER, RESERVED, Token, lex, unescape
from .tree import PLACES, VOID, Each, Expr, Function, Stmt, Type, fail

PREC = {"||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "<": 7, "<=": 7, ">": 7, ">=": 7}
PREC |= {"+": 8, "-": 8, "*": 9, "/": 9, "%": 9}
REDUCERS = {"+", "*", "&", "|", "^", "add_wrap", "mul_wrap", "min", "max"}
ARM_STATEMENTS = {"return", "break", "continue", "assign", "expr"}  # what an arm may be without braces
HABITS = {  # what another language's spelling is in CAIRN, said where the parser meets it
    "as": " CAIRN has no `as`: convert with a call, u64(x), which checks the range.",
    "::": " CAIRN has no `::`: a module's name is followed by `.`, as in vec.push, and an i64's minimum is the "
    "literal -9223372036854775808.",
}


def copied(e: Expr) -> Expr:
    """The place `p += v` reads, as a node of its own: checked and annotated apart from the place it writes, and
    without a source span, so the one place written is the one site a hover or an edit sees."""
    return Expr(e.tag, e.val, [copied(a) for a in e.args], e.line, e.col, ref=e.ref)


def lent_part(a: Expr, lends: tuple[str, str, str]) -> Expr:
    """`a.data[a.lo..a.hi]`, the part a record that `lends data[lo..hi]` stands for, a literal bound as written."""
    carrier, lo, hi = lends
    field = [Expr("field", name, [copied(a)], a.line, a.col) for name in (carrier, lo, hi)]
    bound = [Expr("int", b, [], a.line, a.col) if b.isdigit() else field[j + 1] for j, b in enumerate((lo, hi))]
    return Expr("slice", "", [field[0], *bound], a.line, a.col, start=a.start, end=a.end)


class ExpressionParser:
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

    def take(self) -> str:
        """The current token's text, then move past it."""
        self.i += 1
        return self.ts[self.i - 1].s

    def eat(self, s: str) -> bool:
        if self.t.s == s:
            self.i += 1
            return True
        return False

    def need(self, *expected: str):
        for s in expected:
            if not self.eat(s):
                habit = HABITS.get(self.t.s + (self.ahead(1) if self.t.s == ":" else ""), "")
                fail("E-PARSE", f"Expected {s!r}, found {self.t.s!r}.{habit}", self.t)

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

    def bounds(self) -> tuple[str, str]:
        """`[lo..hi]` of a `lends` clause: each a literal or a field of the record, never an expression."""
        self.need("[")
        lo = str(self.integer()) if self.t.s.isdigit() else self.ident()
        self.need("..")
        hi = str(self.integer()) if self.t.s.isdigit() else self.ident()
        self.need("]")
        return lo, hi

    def integer(self) -> int:
        if not self.t.s.isdigit():
            fail("E-STATIC", "Expected a nonnegative integer literal.", self.t)
        return int(self.take())

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
        return self.take()

    def ty(self) -> Type:
        if self.t.s in {"ro", "rw"}:
            mode = self.take()
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
                return name, self.take()
            bounds = [self.path()]
            while self.eat("+"):  # K: Hash + Eq
                bounds.append(self.path())
            return name, "+".join(bounds)

        return self.listed("]", parameter) if self.eat("[") else []

    def parameter(self) -> tuple[str, Type]:
        name = self.ident()
        self.need(":")
        return name, self.ty()

    # The statement grammar (statements.py) supplies these; an expression holds a block or a region.

    def stmt(self) -> Stmt:
        raise NotImplementedError

    def block(self) -> list[Stmt]:
        raise NotImplementedError

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
            e = Expr("str", text, [], *at) if t.s[0] == '"' else Expr("int", str(ord(text)), [], *at, char=True)
        elif t.s in {"if", "match"}:  # Neither yields a value, as it does in Rust.
            fail("E-NAME", f"{t.s} is a statement, not an expression: declare the value first, let mut x = ...;, "
                 "and assign it in each branch.", t)  # fmt: skip
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

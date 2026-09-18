"""Tokens, syntax tree, parser and the closed scalar vocabulary.

The parser owns syntax and source ranges only. Every name it produces is
resolved, typed and given effects by checking.py; nothing here evaluates source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, NoReturn

MAX_SOURCE = 2_000_000
MAX_FAMILY = 1024
MAX_FUNCTIONS = 2048
MAX_NODES = 200_000


class Diagnostic(Exception):
    def __init__(self, code: str, message: str, line: int = 0, column: int = 0, **details):
        super().__init__(message)
        self.data = {
            "protocol": "cairn.diagnostic/2",
            "status": "rejected",
            "code": code,
            "message": message,
            "line": line,
            "column": column,
            "trust": "prototype-not-verified",
            **details,
        }


def fail(code: str, message: str, node: Any = None, **details) -> NoReturn:
    raise Diagnostic(code, message, getattr(node, "line", 0), getattr(node, "col", 0), **details)


@dataclass
class Token:
    s: str
    line: int
    col: int
    start: int = -1
    end: int = -1


TOKEN = re.compile(
    r"//[^\n]*|\s+|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)+'|0x[0-9A-Fa-f]+"
    r"|(?:[0-9]+\.[0-9]+(?:[eE][+-]?[0-9]+)?|[0-9]+(?:[eE][+-]?[0-9]+))|[0-9]+"
    r"|[A-Za-z_][A-Za-z_0-9]*|=>|->|\.\.|==|!=|<=|>=|&&|\|\||[{}()\[\],;:.@+*/%<>=!&|^~-]"
)
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
RESERVED = set(  # One readable paragraph of words beats a wall of quoted strings.
    "fn struct enum family let mut reg if else for each in while return true false ro rw host nat "
    "effects pure extern unsafe defer match kernel module import compact where yield derive wire "
    "buffer stack zeroed break continue trait impl dyn const pub linear parallel reduce spawn try "
    "as type device pinned unified".split()
)
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'"}
PLACES = ("host", "device", "pinned", "unified")


def lex(text: str) -> list[Token]:
    if len(text.encode()) > MAX_SOURCE:
        fail("E-SOURCE-LIMIT", "Source exceeds the 2 MB bootstrap limit.")
    out: list[Token] = []
    p, line, col = 0, 1, 1
    while p < len(text):
        m = TOKEN.match(text, p)
        if not m:
            fail("E-LEX", f"Unexpected character {text[p]!r}.", Token("", line, col))
        s = m.group()
        if not s.isspace() and not s.startswith("//"):
            out.append(Token(s, line, col, p, m.end()))
        if "\n" in s:
            line += s.count("\n")
            col = len(s.rsplit("\n", 1)[1]) + 1
        else:
            col += len(s)
        p = m.end()
    out.append(Token("<eof>", line, col, p, p))
    return out


def unescape(token: Token) -> str:
    def replace(m: re.Match) -> str:
        body = m.group(1)
        if body[0] == "x" and len(body) == 3:
            return chr(int(body[1:], 16))
        if body not in ESCAPES:
            fail("E-LEX", f"Unknown escape \\{body}.", token)
        return ESCAPES[body]

    return re.sub(r"\\(x[0-9A-Fa-f]{2}|.)", replace, token.s[1:-1])


@dataclass(frozen=True)
class Type:
    """A value type, or a second-class borrow of one (`mode` ro/rw, optional array extent)."""

    name: str
    mode: str = "value"
    extent: str = ""
    args: tuple = ()
    place: str = "host"

    @property
    def value(self) -> Type:
        return Type(self.name, args=self.args)

    def display(self) -> str:
        if self.name == "fn":
            inner = f"fn({', '.join(a.display() for a in self.args[:-1])})"
            inner += "" if self.args[-1].name == "void" else " -> " + self.args[-1].display()
        elif self.name == "dyn":
            inner = "dyn " + self.args[0].display()
        else:
            shown = (a.display() if isinstance(a, Type) else str(a) for a in self.args)
            inner = self.name + ("[" + ", ".join(shown) + "]" if self.args else "")
        if self.mode == "value":
            return inner
        return f"{self.mode}<{inner}>" + (f"[{self.extent}]@{self.place}" if self.extent else "")


BITS = {"u8": 8, "u16": 16, "u32": 32, "u64": 64, "usize": 64, "i8": 8, "i16": 16, "i32": 32, "i64": 64}
CPP = {n: "std::size_t" if n == "usize" else f"std::{'u' * (n[0] == 'u')}int{w}_t" for n, w in BITS.items()}
CPP |= {"bool": "bool", "f32": "float", "f64": "double", "void": "void"}
UNSIGNED = {n for n in BITS if n[0] == "u"}
SIGNED = set(BITS) - UNSIGNED
INT = UNSIGNED | SIGNED
FLOAT = {"f32", "f64"}
NUMERIC = INT | FLOAT
SCALAR = NUMERIC | {"bool"}
WIDTH = BITS
VOID, BOOL, USIZE = Type("void"), Type("bool"), Type("usize")


@dataclass
class Expr:
    tag: str
    val: str = ""
    args: list[Expr] = field(default_factory=list)
    line: int = 0
    col: int = 0
    ty: Type | None = None
    start: int = -1
    end: int = -1
    ref: Any = None  # The checker's resolution: a binding, callee, constant or lambda.


@dataclass
class Arm:
    variant: str
    binder: str
    body: list[Stmt]
    line: int = 0
    col: int = 0


@dataclass
class Stmt:
    tag: str
    name: str = ""
    ty: Type | None = None
    exprs: list[Expr] = field(default_factory=list)
    body: list[Stmt] = field(default_factory=list)
    other: list[Stmt] = field(default_factory=list)
    line: int = 0
    col: int = 0
    binder: str = ""
    arms: list[Arm] = field(default_factory=list)
    op: str = ""
    ref: Any = None


@dataclass
class Function:
    name: str
    params: list[tuple[str, Type]]
    ret: Type
    body: list[Stmt]
    generics: list[tuple[str, str]] = field(default_factory=list)  # (name, nat | type | Trait)
    bindings: dict[str, Any] = field(default_factory=dict)  # instantiated generics
    source_name: str = ""
    line: int = 0
    col: int = 0
    start: int = -1
    body_start: int = -1
    end: int = -1
    module: str = ""
    public: bool = False
    extern: bool = False
    effects: tuple[str, ...] | None = None  # A declared ceiling; None infers.
    owner: tuple[str, Type] | None = None  # (trait, Self) for an impl member.

    @property
    def static(self) -> str | None:
        return self.generics[0][0] if self.generics and not self.bindings else None

    @property
    def binding(self) -> int | None:
        return next((v for v in self.bindings.values() if isinstance(v, int)), None)


@dataclass
class Program:
    records: dict[str, list[tuple[str, Type]]] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)
    functions: list[Function] = field(default_factory=list)
    families: list[tuple[str, str, int, int]] = field(default_factory=list)
    derivations: list[str] = field(default_factory=list)
    sums: dict[str, list[tuple[str, Type | None]]] = field(default_factory=dict)
    generics: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # generic types
    attributes: dict[str, set[str]] = field(default_factory=dict)  # linear, packed, align(n)
    traits: dict[str, list[Function]] = field(default_factory=dict)
    consts: dict[str, tuple[Type, Expr]] = field(default_factory=dict)
    imports: list[tuple[str, str, str]] = field(default_factory=list)  # (importer, path, alias)
    public: set[str] = field(default_factory=set)
    uses: dict[tuple[str, str], str] = field(default_factory=dict)  # (importer, bare name) -> full name
    sources: dict[str, str] = field(default_factory=dict)  # linked library module -> its text
    modules: dict[str, str] = field(default_factory=dict)  # declared name -> owning module


PREC = {"||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "<": 7, "<=": 7, ">": 7, ">=": 7}
PREC |= {"+": 8, "-": 8, "*": 9, "/": 9, "%": 9}
REDUCERS = {"+", "*", "&", "|", "^", "add_wrap", "mul_wrap", "min", "max"}


class Parser:
    def __init__(self, source: str):
        self.ts = lex(source)
        self.i = self.depth = 0
        self.module = ""

    @property
    def t(self) -> Token:
        return self.ts[self.i]

    def eat(self, s: str) -> bool:
        if self.t.s == s:
            self.i += 1
            return True
        return False

    def need(self, s: str):
        if not self.eat(s):
            fail("E-PARSE", f"Expected {s!r}, found {self.t.s!r}.", self.t)

    def ident(self) -> str:
        t = self.t
        if not IDENT.fullmatch(t.s) or t.s in RESERVED:
            fail("E-NAME", f"Expected an identifier, found {t.s!r}.", t)
        self.i += 1
        return t.s

    def path(self) -> str:
        name = self.ident()
        while self.t.s == "." and IDENT.fullmatch(self.ts[self.i + 1].s):
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
            if self.t.s in {"nat", "type"}:
                self.i += 1
                return name, self.ts[self.i - 1].s
            return name, self.path()

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
            e = Expr(t.s, "", [self.expr(10)], *at)
        elif t.s in {"|", "||"}:
            e = self.closure()
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

    def block(self) -> list[Stmt]:
        self.need("{")
        body = []
        while not self.eat("}"):
            if self.t.s == "<eof>":
                fail("E-PARSE", "Unclosed block.", self.t)
            body.append(self.stmt())
        return body

    def generator(self) -> tuple[str, Expr]:
        """`for i in n`, shared by the contracted forms."""
        self.need("for")
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
            binder, hi = self.generator()
            self.need("yield")
            es = [hi, self.expr()]
        self.need(";")
        return Stmt(form, name, typ, es, binder=binder, op=op, **at)

    def stmt(self) -> Stmt:
        t = self.t
        at: dict[str, Any] = {"line": t.line, "col": t.col}
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
            self.need("=")
            self.need("zeroed")
            self.need(";")
            return Stmt(t.s, n, Type(element.name, args=element.args, place=place), [extent], **at)
        if t.s in {"let", "reg"}:
            self.i += 1
            tag = "reg" if t.s == "let" and self.eat("mut") else t.s
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
                name = self.path()
                if "." not in name:
                    fail("E-PARSE", "A match arm names Enum.Variant.", a)
                binder = ""
                if self.eat("("):
                    binder = self.ident()
                    self.need(")")
                self.need("=>")
                arms.append(Arm(name, binder, self.block(), a.line, a.col))
            return Stmt("match", exprs=[scrutinee], arms=arms, **at)
        if self.eat("while"):
            e = self.expr()
            return Stmt("while", exprs=[e], body=self.block(), **at)
        if t.s in {"for", "parallel"}:
            self.i += 1
            n = self.ident()
            self.need("in")
            lo = self.expr()
            if t.s == "parallel":
                return Stmt("parallel", n, exprs=[lo], body=self.block(), **at)
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
        if self.eat("="):
            v = self.expr()
            self.need(";")
            return Stmt("assign", exprs=[e, v], **at)
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
                    attributes.add(f"align({self.integer()})")
                    self.need(")")
                self.need("{")
                fs = []
                while not self.eat("}"):
                    fs.append(self.parameter())
                    self.need(";")
                full = declare(n, t)
                p.records[full], p.generics[full], p.attributes[full] = fs, generics, attributes
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
                generics = self.generic_parameters()
                trait = self.path()
                self.need("for")
                target = self.ty()
                self.need("{")
                while not self.eat("}"):
                    start = self.t
                    self.need("fn")
                    f = self.function(start, public=True, owner=(trait, target))
                    f.generics = generics + f.generics
                    f.name = f.source_name = declare(f"{trait}.{target.display()}.{f.name}", start)
                    p.functions.append(f)
            elif self.eat("extern"):
                self.need("fn")
                f = self.function(t, bodiless=True, extern=True, public=public)
                f.name = f.source_name = declare(f.name, t)
                p.functions.append(f)
            elif self.eat("fn"):
                f = self.function(t, public=public)
                f.name = f.source_name = declare(f.name, t)
                p.functions.append(f)
            elif self.eat("derive"):
                self.need("wire")
                self.need("for")
                p.derivations.append(self.path())
                self.need(";")
            elif self.eat("family"):
                pre = self.ident()
                self.need("=")
                base = self.path()
                self.need("[")
                lo = self.integer()
                self.need("..")
                hi = self.integer()
                self.need("]")
                self.need(";")
                p.families.append((pre, base, lo, hi))
            else:
                fail(
                    "E-DECLARATION",
                    f"Unsupported declaration {self.t.s!r}; expected fn, struct, enum, trait, impl, "
                    "const, extern, module, import, family, or derive wire.",
                    self.t,
                )
        return p

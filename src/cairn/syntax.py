"""Tokens, syntax tree, parser and closed native type vocabulary."""
from __future__ import annotations
from dataclasses import dataclass, field
import re
from typing import Any
from .version import VERSION
MAX_SOURCE = 2_000_000
MAX_FAMILY = 1024
MAX_FUNCTIONS = 2048
MAX_NODES = 200_000

class Diagnostic(Exception):
    def __init__(self, code: str, message: str, line: int = 0, column: int = 0, **details):
        super().__init__(message)
        self.data = {"protocol": "cairn.diagnostic/2", "status": "rejected",
                     "code": code, "message": message, "line": line,
                     "column": column, "trust": "prototype-not-verified", **details}

def fail(code: str, message: str, node: Any = None, **details):
    raise Diagnostic(code, message, getattr(node, "line", 0), getattr(node, "col", 0), **details)

@dataclass
class Token:
    s: str
    line: int
    col: int
    start: int = -1
    end: int = -1

TOKEN = re.compile(r"//[^\n]*|\s+|(?:[0-9]+\.[0-9]+(?:[eE][+-]?[0-9]+)?|[0-9]+(?:[eE][+-]?[0-9]+))|[0-9]+|[A-Za-z_][A-Za-z_0-9]*|=>|->|\.\.|==|!=|<=|>=|&&|\|\||[{}()\[\],;:.@+*/%<>=!&|^~-]")
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
RESERVED = set("fn struct enum family let mut reg if else for each in while return true false ro rw host nat effects pure extern unsafe defer match kernel module import compact where yield derive wire buffer stack zeroed break continue".split())

def lex(text: str) -> list[Token]:
    if len(text.encode()) > MAX_SOURCE:
        fail("E-SOURCE-LIMIT", "Source exceeds the 2 MB bootstrap limit.")
    out: list[Token] = []
    p = 0; line = 1; col = 1
    while p < len(text):
        m = TOKEN.match(text, p)
        if not m:
            fail("E-LEX", f"Unexpected character {text[p]!r}.", Token("", line, col))
        s = m.group()
        if not s.isspace() and not s.startswith("//"):
            out.append(Token(s, line, col, p, m.end()))
        if "\n" in s:
            line += s.count("\n"); col = len(s.rsplit("\n", 1)[1]) + 1
        else:
            col += len(s)
        p = m.end()
    out.append(Token("<eof>", line, col, p, p))
    return out

@dataclass(frozen=True)
class Type:
    name: str
    mode: str = "value"
    extent: str = ""
    def cpp(self) -> str:
        base = CPP.get(self.name, "ct_" + self.name)
        return ("const " if self.mode == "ro" else "") + base + ("*" if self.mode != "value" else "")
    def display(self) -> str:
        return self.name if self.mode == "value" else f"{self.mode}<{self.name}>[{self.extent}]@host"

CPP = {"bool":"bool", "u8":"std::uint8_t", "u16":"std::uint16_t", "u32":"std::uint32_t",
       "u64":"std::uint64_t", "usize":"std::size_t", "i32":"std::int32_t", "i64":"std::int64_t",
       "f32":"float", "f64":"double", "void":"void"}
UNSIGNED = {"u8", "u16", "u32", "u64", "usize"}
SIGNED = {"i32", "i64"}
INT = UNSIGNED | SIGNED
FLOAT = {"f32", "f64"}
NUMERIC = INT | FLOAT
WIDTH = {"u8":8, "u16":16, "u32":32, "u64":64, "usize":64, "i32":32, "i64":64}

@dataclass
class Expr:
    tag: str
    val: str = ""
    args: list[Expr] = field(default_factory=list)
    line: int = 0
    col: int = 0
    ty: Type | None = None
    cpp: str = ""
    start: int = -1
    end: int = -1

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

@dataclass
class Arm:
    variant: str
    binder: str
    body: list[Stmt]
    line: int = 0
    col: int = 0

@dataclass
class Function:
    name: str
    params: list[tuple[str, Type]]
    ret: Type
    body: list[Stmt]
    static: str | None = None
    binding: int | None = None
    source_name: str = ""
    line: int = 0
    col: int = 0
    start: int = -1
    body_start: int = -1
    end: int = -1

@dataclass
class Program:
    records: dict[str, list[tuple[str, Type]]] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)
    functions: list[Function] = field(default_factory=list)
    families: list[tuple[str, str, int, int]] = field(default_factory=list)
    derivations: list[str] = field(default_factory=list)
    sums: dict[str, list[tuple[str, Type | None]]] = field(default_factory=dict)

PREC = {"||":1,"&&":2,"|":3,"^":4,"&":5,"==":6,"!=":6,"<":7,"<=":7,">":7,">=":7,"+":8,"-":8,"*":9,"/":9,"%":9}

class Parser:
    def __init__(self, source: str):
        self.ts = lex(source); self.i = 0; self.depth = 0
    @property
    def t(self): return self.ts[self.i]
    def eat(self, s: str) -> bool:
        if self.t.s == s:
            self.i += 1; return True
        return False
    def need(self, s: str):
        if not self.eat(s): fail("E-PARSE", f"Expected {s!r}, found {self.t.s!r}.", self.t)
    def ident(self):
        t = self.t
        if not IDENT.fullmatch(t.s) or t.s in RESERVED: fail("E-NAME", f"Expected an identifier, found {t.s!r}.", t)
        self.i += 1; return t.s
    def integer(self):
        if not self.t.s.isdigit(): fail("E-STATIC", "Expected a nonnegative integer literal.", self.t)
        v = int(self.t.s); self.i += 1; return v
    def ty(self) -> Type:
        if self.t.s in {"ro", "rw"}:
            mode = self.t.s; self.i += 1; self.need("<"); n = self.ident(); self.need(">")
            self.need("[")
            ext = str(self.integer()) if self.t.s.isdigit() else self.ident()
            self.need("]")
            if self.eat("@"): self.need("host")
            return Type(n, mode, ext)
        return Type(self.ident())
    def expr(self, prec: int = 0) -> Expr:
        start = self.i
        self.depth += 1
        if self.depth > 100: fail("E-DEPTH", "Expression nesting exceeds 100.", self.t)
        t = self.t
        if self.eat("("):
            e = self.expr(); self.need(")")
        elif t.s in {"-", "!", "~"}:
            self.i += 1; e = Expr("unary", t.s, [self.expr(10)], t.line, t.col)
        elif self.eat("true") or self.eat("false"):
            e = Expr("bool", t.s, [], t.line, t.col)
        elif re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", t.s):
            self.i += 1; e = Expr("float" if any(c in t.s for c in ".eE") else "int", t.s, [], t.line, t.col)
        else:
            name = self.ident(); e = Expr("name", name, [], t.line, t.col)
        e.start = self.ts[start].start; e.end = self.ts[self.i-1].end
        while True:
            if self.eat("("):
                args = []
                if not self.eat(")"):
                    args.append(self.expr())
                    while self.eat(","): args.append(self.expr())
                    self.need(")")
                if e.tag=="field" and e.args[0].tag=="name":
                    callee=e.args[0].val+"."+e.val
                elif e.tag=="name": callee=e.val
                else: fail("E-CALL", "Only direct calls and qualified sum constructors are supported.",e)
                e = Expr("call", callee, args, e.line, e.col)
            elif self.eat("["):
                idx = self.expr(); self.need("]"); e = Expr("index", "", [e, idx], e.line, e.col)
            elif self.eat("."):
                e = Expr("field", self.ident(), [e], e.line, e.col)
            elif self.t.s in PREC and PREC[self.t.s] >= prec:
                op = self.t.s; p = PREC[op]; self.i += 1
                e = Expr("binary", op, [e, self.expr(p+1)], e.line, e.col)
            else: break
            e.start = self.ts[start].start; e.end = self.ts[self.i-1].end
        self.depth -= 1
        return e
    def block(self) -> list[Stmt]:
        self.need("{"); body = []
        while not self.eat("}"):
            if self.t.s == "<eof>": fail("E-PARSE", "Unclosed block.", self.t)
            body.append(self.stmt())
        return body
    def stmt(self) -> Stmt:
        t = self.t
        if t.s in {"buffer", "stack"}:
            self.i += 1
            n = self.ident(); self.need(":"); typ = Type(self.ident())
            self.need("["); extent = self.expr(); self.need("]")
            self.need("="); self.need("zeroed"); self.need(";")
            return Stmt(t.s, n, typ, [extent], line=t.line, col=t.col)
        if t.s in {"let", "reg"}:
            self.i += 1
            tag = "reg" if t.s == "let" and self.eat("mut") else t.s
            n = self.ident(); typ = self.ty() if self.eat(":") else None
            self.need("=")
            if self.eat("compact"):
                if tag != "let" or (typ is not None and typ != Type("usize")):
                    fail("E-COLLECT-BINDING", "Compaction binds an immutable usize result.", t)
                out = self.ident(); self.need("for"); binder = self.ident(); self.need("in")
                hi = self.expr(); self.need("where"); pred = self.expr(); self.need("yield")
                value = self.expr(); self.need(";")
                return Stmt("compact", n, Type("usize"),
                            [Expr("name",out,line=t.line,col=t.col),hi,pred,value],
                            line=t.line,col=t.col,binder=binder)
            e = self.expr(); self.need(";")
            return Stmt(tag, n, typ, [e], line=t.line, col=t.col)
        if t.s in {"break","continue"}:
            self.i+=1; self.need(";")
            return Stmt(t.s,line=t.line,col=t.col)
        if self.eat("return"):
            es = [] if self.t.s == ";" else [self.expr()]; self.need(";")
            return Stmt("return", exprs=es, line=t.line, col=t.col)
        if self.eat("if"):
            e = self.expr(); b = self.block(); o = []
            if self.eat("else"):
                o = [self.stmt()] if self.t.s == "if" else self.block()
            return Stmt("if", exprs=[e], body=b, other=o, line=t.line, col=t.col)
        if self.eat("match"):
            scrutinee=self.expr(); self.need("{"); arms=[]
            while not self.eat("}"):
                a=self.t; name=self.ident(); self.need("."); name+="."+self.ident()
                binder=""
                if self.eat("("):
                    binder=self.ident(); self.need(")")
                self.need("=>"); body=self.block()
                arms.append(Arm(name,binder,body,a.line,a.col))
            return Stmt("match",exprs=[scrutinee],arms=arms,line=t.line,col=t.col)
        if self.eat("while"):
            e = self.expr(); b = self.block(); return Stmt("while", exprs=[e], body=b, line=t.line, col=t.col)
        if self.eat("for"):
            n = self.ident(); self.need("in"); lo = self.expr(); self.need(".."); hi = self.expr(); b = self.block()
            return Stmt("for", n, exprs=[lo,hi], body=b, line=t.line, col=t.col)
        if self.eat("each"):
            n = self.ident(); self.need("in"); hi = self.expr(); b = self.block()
            return Stmt("for", n, exprs=[Expr("int","0",line=t.line,col=t.col),hi], body=b, line=t.line,col=t.col)
        e = self.expr()
        if self.eat("="):
            v = self.expr(); self.need(";"); return Stmt("assign", exprs=[e,v], line=t.line,col=t.col)
        self.need(";"); return Stmt("expr", exprs=[e], line=t.line,col=t.col)
    def parse(self) -> Program:
        p = Program(); names = set()
        while self.t.s != "<eof>":
            t = self.t
            if self.eat("struct"):
                n = self.ident(); self.need("{"); fs = []
                while not self.eat("}"):
                    f = self.ident(); self.need(":"); typ = self.ty(); self.need(";"); fs.append((f,typ))
                if n in names: fail("E-DUPLICATE", f"Duplicate declaration {n}.",t)
                names.add(n); p.records[n] = fs
            elif self.eat("enum"):
                n = self.ident(); self.need("{"); vs = []
                while not self.eat("}"):
                    variant=self.ident(); payload=None
                    if self.eat("("):
                        payload=self.ty(); self.need(")")
                    vs.append((variant,payload)); self.need(";")
                if n in names: fail("E-DUPLICATE", f"Duplicate declaration {n}.",t)
                names.add(n)
                if any(ty is not None for _,ty in vs): p.sums[n]=vs
                else: p.enums[n]=[v for v,_ in vs]
            elif self.eat("fn"):
                n = self.ident(); static = None
                if self.eat("["):
                    static = self.ident(); self.need(":"); self.need("nat"); self.need("]")
                self.need("("); ps = []
                if not self.eat(")"):
                    while True:
                        q = self.ident(); self.need(":"); typ = self.ty(); ps.append((q,typ))
                        if not self.eat(","): break
                    self.need(")")
                ret = self.ty() if self.eat("->") else Type("void")
                body_start = self.t.start
                if self.eat("="):
                    if ret == Type("void"):
                        fail("E-EXPRESSION-BODY", "An expression body needs an explicit nonvoid return type.", t)
                    value = self.expr(); self.need(";")
                    b = [Stmt("return", exprs=[value], line=value.line, col=value.col)]
                else:
                    b = self.block()
                end = self.ts[self.i-1].end
                if n in names: fail("E-DUPLICATE", f"Duplicate declaration {n}.",t)
                names.add(n); p.functions.append(Function(n,ps,ret,b,static,source_name=n,line=t.line,col=t.col,start=t.start,body_start=body_start,end=end))
            elif self.eat("derive"):
                self.need("wire"); self.need("for"); n=self.ident(); self.need(";")
                p.derivations.append(n)
            elif self.eat("family"):
                pre = self.ident(); self.need("="); base = self.ident(); self.need("[")
                lo = self.integer(); self.need(".."); hi = self.integer(); self.need("]"); self.need(";")
                p.families.append((pre,base,lo,hi))
            else:
                fail("E-DECLARATION", f"Unsupported declaration {self.t.s!r}; expected fn, struct, enum, family, or derive wire.", self.t)
        return p

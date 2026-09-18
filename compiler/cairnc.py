#!/usr/bin/env python3
"""CAIRN Native 0.4: a deliberately bounded, inspectable C++20 backend.

This is a compiler prototype, not a verified compiler. No source is eval'ed.
All emitted identifiers are prefixed, all AST constructors are closed, and
ordinary integer arithmetic and slice indexing have defined trap behavior.
"""
from __future__ import annotations
import argparse
import copy
from dataclasses import dataclass, field, asdict
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

VERSION = "cairn-native/0.4.0"
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

TOKEN = re.compile(r"//[^\n]*|\s+|(?:[0-9]+\.[0-9]+(?:[eE][+-]?[0-9]+)?|[0-9]+(?:[eE][+-]?[0-9]+))|[0-9]+|[A-Za-z_][A-Za-z_0-9]*|->|\.\.|==|!=|<=|>=|&&|\|\||[{}()\[\],;:.@+*/%<>=!&|^~-]")
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
RESERVED = set("fn struct enum family let mut reg if else for each in while return true false ro rw host nat effects pure extern unsafe defer match kernel module import compact where yield derive wire".split())

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
            self.need("]"); self.need("@"); self.need("host")
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
                if e.tag != "name": fail("E-CALL", "Only direct named calls are in the native subset.", e)
                e = Expr("call", e.val, args, e.line, e.col)
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
        if self.eat("return"):
            es = [] if self.t.s == ";" else [self.expr()]; self.need(";")
            return Stmt("return", exprs=es, line=t.line, col=t.col)
        if self.eat("if"):
            e = self.expr(); b = self.block(); o = self.block() if self.eat("else") else []
            return Stmt("if", exprs=[e], body=b, other=o, line=t.line, col=t.col)
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
                    vs.append(self.ident()); self.need(";")
                if n in names: fail("E-DUPLICATE", f"Duplicate declaration {n}.",t)
                names.add(n); p.enums[n] = vs
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

def derive_wire(p: Program) -> Program:
    """Closed AST-to-AST generator; field names cannot inject generated source."""
    names = {f.name for f in p.functions} | set(p.records) | set(p.enums)
    def var(n): return Expr("name",n)
    def lit(n): return Expr("int",str(n))
    def call(n,*args): return Expr("call",n,list(args))
    for record in p.derivations:
        if record not in p.records:
            fail("E-DERIVE-TYPE", f"Unknown record {record}.")
        fields=p.records[record]
        if any(t.mode != "value" or t.name not in {"u8","u16","u32","u64"} for _,t in fields):
            fail("E-DERIVE-FIELD", "wire/1 supports only fixed-width unsigned scalar fields.")
        size=sum(WIDTH[t.name]//8 for _,t in fields)
        enc=[]; decoded=[]; offset=0
        for field,t in fields:
            value=Expr("field",field,[var("value")])
            parts=[]
            for byte in range(WIDTH[t.name]//8):
                bits=call("shr",copy.deepcopy(value),lit(8*byte))
                low=Expr("binary","&",[bits,lit(255)])
                enc.append(Stmt("assign",exprs=[Expr("index",args=[var("out"),lit(offset)]),call("u8",low)]))
                b=call(t.name,Expr("index",args=[var("input"),lit(offset)]))
                parts.append(call("shl_wrap",b,lit(8*byte)))
                offset+=1
            full=parts[0]
            for part in parts[1:]: full=Expr("binary","|",[full,part])
            decoded.append(full)
        functions=[
            Function("encode_"+record,[("out",Type("u8","rw",str(size))),("value",Type(record))],Type("void"),enc,source_name="derive wire for "+record),
            Function("decode_"+record,[("input",Type("u8","ro",str(size)))],Type(record),[Stmt("return",exprs=[call(record,*decoded)])],source_name="derive wire for "+record),
            Function("wire_size_"+record,[],Type("usize"),[Stmt("return",exprs=[lit(size)])],source_name="derive wire for "+record)
        ]
        for f in functions:
            if f.name in names: fail("E-DERIVE-COLLISION", f"Derived name {f.name} already exists.")
            names.add(f.name); p.functions.append(f)
    return p

def specialize(p: Program) -> Program:
    base = {f.name:f for f in p.functions if f.static}
    out = [f for f in p.functions if not f.static]
    names = {f.name for f in out} | set(p.records) | set(p.enums)
    def node_count(f):
        total = 0; todo = list(f.body)
        while todo:
            node = todo.pop(); total += 1
            if isinstance(node, Stmt): todo.extend(node.body + node.other + node.exprs)
            elif isinstance(node, Expr): todo.extend(node.args)
        return total
    estimated = sum(node_count(f) for f in out)
    if estimated > MAX_NODES or len(out) > MAX_FUNCTIONS:
        fail("E-EXPANSION-LIMIT", "Program exceeds the pre-expansion budget.")
    for prefix, name, lo, hi in p.families:
        if name not in base: fail("E-FAMILY-TARGET", f"{name} is not a static function template.")
        if not 0 <= lo < hi <= 2**32 or hi-lo > MAX_FAMILY:
            fail("E-FAMILY-LIMIT", "Family must be a nonempty half-open range, at most 1024 variants, below 2^32.")
        cost = (hi-lo)*node_count(base[name])
        if estimated + cost > MAX_NODES or len(out) + hi-lo > MAX_FUNCTIONS:
            fail("E-EXPANSION-LIMIT", "Family exceeds the remaining AST/function budget.")
        estimated += cost
        for k in range(lo,hi):
            f = copy.deepcopy(base[name]); f.name = f"{prefix}_{k}"; f.binding = k
            if f.name in names: fail("E-DUPLICATE", f"Family emits duplicate name {f.name}.")
            names.add(f.name); out.append(f)
    for n in base:
        if not any(name == n for _,name,_,_ in p.families):
            fail("E-UNINSTANTIATED", f"Static function {n} has no family; unused templates are not silently ignored.")
    if len(out) > MAX_FUNCTIONS: fail("E-EXPANSION-LIMIT", "Expanded program exceeds 2048 functions.")
    p.functions = out
    return p

@dataclass
class Binding:
    ty: Type
    mutable: bool = False
    constant: int | None = None

class Checker:
    def __init__(self, program: Program, capture_sites: bool = False):
        self.p = program
        self.capture_sites = capture_sites
        self.sites: list[dict[str, Any]] = []
        self.call_edges: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.fs = {f.name:f for f in program.functions}
        builtin_names = set(CPP) | {"add_wrap", "sub_wrap", "mul_wrap", "shl_wrap", "shr", "min", "max"}
        for name in set(self.fs) | set(program.records) | set(program.enums):
            if name in builtin_names:
                fail("E-BUILTIN-NAME", f"Cannot redefine builtin {name}.")
        self.env: dict[str,Binding] = {}
        self.f: Function | None = None
        self.local_effects: dict[str,set[str]] = {}
        self.calls: dict[str,set[str]] = {}
        self.checks: dict[str,dict[str,int]] = {}
        self.effects: set[str] = set()
        self.callset: set[str] = set()
        self.counts: dict[str,int] = {}
        self.nodes = 0
    def type_ok(self, ty: Type, node=None):
        if ty.name not in CPP and ty.name not in self.p.records and ty.name not in self.p.enums:
            fail("E-TYPE", f"Unknown type {ty.name}.",node)
        if ty.mode != "value" and ty.name == "void": fail("E-TYPE", "A slice cannot contain void.",node)
    def effect(self, name: str): self.effects.add(name)
    def guard(self, kind: str):
        self.effects.add("trap")
        self.counts[kind] = self.counts.get(kind,0) + 1
    def expect(self, got: Type, want: Type, e: Expr):
        if got != want: fail("E-TYPE-MISMATCH", f"Expected {want.display()}, got {got.display()}.",e, expected_type=want.display(), actual_type=got.display())
    def check(self) -> dict[str,Any]:
        seen = set(CPP)
        for n, fields in self.p.records.items():
            if n in CPP: fail("E-TYPE-NAME", f"Cannot redefine builtin {n}.")
            if not fields: fail("E-RECORD", "Empty records are outside this ABI subset.")
            if len({x for x,_ in fields}) != len(fields): fail("E-DUPLICATE", f"Duplicate field in {n}.")
            for _,ty in fields:
                if ty.mode != "value" or ty.name not in seen or ty.name == "void":
                    fail("E-RECORD-TYPE", "Record fields must be previously declared value types, not views or void.")
            seen.add(n)
        for n, variants in self.p.enums.items():
            if n in CPP or not variants or len(set(variants)) != len(variants): fail("E-ENUM", f"Invalid enum {n}.")
        for f in self.p.functions:
            self.f = f; self.env = {}; self.effects = set(); self.callset=set(); self.counts={}
            self.call_edges[f.name] = []
            for n,ty in f.params:
                self.type_ok(ty,f)
                if n in self.env or ty.name == "void": fail("E-PARAM", f"Invalid or duplicate parameter {n}.",f)
                if ty.mode != "value":
                    if ty.extent.isdigit():
                        if int(ty.extent) > 2**63-1: fail("E-EXTENT", "Static extent exceeds bootstrap bound.",f)
                    elif ty.extent not in self.env or self.env[ty.extent].ty != Type("usize"):
                        fail("E-EXTENT", "Dynamic extent must name an earlier immutable usize parameter.",f)
                    self.effects.add("ffi_precondition")
                    self.guard("view_entry")
                self.env[n] = Binding(ty)
                if ty.name in self.p.enums and ty.mode == "value":
                    self.guard("enum_entry")
            if f.static:
                if f.static in self.env: fail("E-DUPLICATE", "Static binder shadows a parameter.",f)
                self.env[f.static] = Binding(Type("usize"),constant=f.binding)
            self.type_ok(f.ret,f)
            if f.ret.mode != "value": fail("E-ESCAPE", "Borrowed view returns are not in the native subset.",f)
            terminated = self.block(f.body)
            if f.ret.name != "void" and not terminated: fail("E-RETURN", f"Not all paths of {f.name} return.",f)
            self.local_effects[f.name] = set(self.effects)
            self.calls[f.name] = self.callset
            self.checks[f.name] = self.counts
        # Footprints are instantiated at each call site, not copied under the
        # callee's parameter names. A recursive argument permutation can require
        # more iterations than the number of functions.
        effects = {n:set(es) for n,es in self.local_effects.items()}
        for n in effects:
            todo = list(self.calls[n]); visited = set()
            while todo:
                q = todo.pop()
                if q == n: effects[n].add("diverge"); break
                if q in visited: continue
                visited.add(q); todo.extend(self.calls[q])
        global_effects = {x for es in effects.values() for x in es if ":" not in x}
        bound = 1 + sum(2*len(f.params) + len(global_effects) for f in self.p.functions)
        for _ in range(bound):
            changed = False
            for n in effects:
                before = len(effects[n])
                for callee, mapping in self.call_edges[n]:
                    for effect in tuple(effects[callee]):
                        if effect.startswith(("read:", "write:")):
                            kind, formal = effect.split(":",1)
                            if formal not in mapping:
                                fail("E-INTERNAL", "Unmapped callee memory footprint.")
                            effects[n].add(kind + ":" + mapping[formal])
                        else:
                            effects[n].add(effect)
                changed |= len(effects[n]) != before
            if not changed: break
        else:
            fail("E-EFFECT-LIMIT", "Effect fixed point exceeded its finite universe.")
        # Unspecified C++ argument/operand evaluation must not reorder observable writes.
        # Calls that can write are allowed only at a whole-expression root.
        # This deliberately rejects some well-defined programs rather than silently
        # choosing an order that differs from the documented native semantics.
        def audit_expr(e: Expr, root: bool = True):
            if e.tag == "call" and e.val in effects and not root:
                if any(x.startswith("write:") for x in effects[e.val]):
                    fail("E-EFFECT-ORDER", "Bind a writing call to its own statement before using its result.", e)
            for child in e.args: audit_expr(child, False)
        def audit_block(ss: list[Stmt]):
            for s in ss:
                for i,e in enumerate(s.exprs):
                    audit_expr(e, s.tag != "compact" and not (s.tag == "assign" and i == 0))
                audit_block(s.body); audit_block(s.other)
        for f in self.p.functions: audit_block(f.body)
        return {n:{"effects":sorted(effects[n]),"calls":sorted(self.calls[n]),
                   "syntactic_check_sites":self.checks[n],"heap_allocations":0,
                   "implicit_synchronization":0,"status":"prototype-checked-not-proved"} for n in effects}
    def block(self, ss: list[Stmt]) -> bool:
        saved = dict(self.env); returned=False
        for s in ss:
            if returned: fail("E-UNREACHABLE", "Statement after unconditional return.",s)
            returned = self.stmt(s)
        self.env = saved
        return returned
    def stmt(self, s: Stmt) -> bool:
        if s.tag in {"let","reg"}:
            if s.name in self.env: fail("E-SHADOW", f"{s.name} is already bound; shadowing is forbidden in this subset.",s)
            if s.ty: self.type_ok(s.ty,s)
            ty = self.expr(s.exprs[0],s.ty)
            if ty.mode != "value" or ty.name == "void": fail("E-VIEW-ALIAS", "Local view aliases and void values are outside this subset.",s)
            s.ty = ty; self.env[s.name] = Binding(ty,s.tag=="reg")
        elif s.tag == "compact":
            out, hi, pred, value = s.exprs
            if s.name in self.env or s.binder in self.env or s.name == s.binder:
                fail("E-SHADOW", "Collector names must be fresh and distinct.", s)
            if out.val not in self.env or self.env[out.val].ty.mode != "rw":
                fail("E-WRITE-LEASE", "Compaction target must be a direct rw parameter.", out)
            t = self.env[out.val].ty
            self.expr(out); self.expr(hi, Type("usize"))
            if hi.tag not in {"name","int"} or hi.val != t.extent:
                fail("E-COLLECT-CAPACITY", "Bootstrap compaction requires iteration extent equal to output capacity.", hi)
            self.env[s.binder] = Binding(Type("usize"))
            self.expr(pred, Type("bool")); self.expr(value, Type(t.name))
            def mentions(e, name):
                return (e.tag == "name" and e.val == name) or any(mentions(x,name) for x in e.args)
            if mentions(pred, out.val) or mentions(value, out.val):
                fail("E-COLLECT-SELF-READ", "Collector predicate/projection cannot read its output.", s)
            del self.env[s.binder]
            self.env[s.name] = Binding(Type("usize"))
            self.effect("write:"+out.val)
            self.counts["bounded_collectors"] = self.counts.get("bounded_collectors",0)+1
        elif s.tag == "assign":
            ty = self.lvalue(s.exprs[0]); self.expr(s.exprs[1],ty)
        elif s.tag == "return":
            assert self.f
            if self.f.ret.name == "void":
                if s.exprs: fail("E-RETURN", "Void function cannot return a value.",s)
            else:
                if not s.exprs: fail("E-RETURN", "Missing return value.",s)
                self.expr(s.exprs[0],self.f.ret)
            return True
        elif s.tag == "if":
            self.expr(s.exprs[0],Type("bool"))
            a=self.block(s.body); b=self.block(s.other)
            return bool(s.other) and a and b
        elif s.tag == "while":
            self.expr(s.exprs[0],Type("bool")); self.effect("diverge"); self.block(s.body)
        elif s.tag == "for":
            self.expr(s.exprs[0],Type("usize")); self.expr(s.exprs[1],Type("usize"))
            if s.name in self.env: fail("E-SHADOW", f"Loop binder {s.name} already exists.",s)
            self.env[s.name]=Binding(Type("usize")); self.block(s.body); del self.env[s.name]
        elif s.tag == "expr":
            ty = self.expr(s.exprs[0])
            if s.exprs[0].tag != "call": fail("E-DISCARD", "Only calls may be used as discarded expression statements.",s)
            if ty.name != "void": fail("E-DISCARD", "Nonvoid result must be bound or returned.",s)
        else: fail("E-INTERNAL", f"Unknown statement {s.tag}.",s)
        return False
    def lvalue(self, e: Expr) -> Type:
        if e.tag == "name":
            if e.val not in self.env or not self.env[e.val].mutable:
                fail("E-IMMUTABLE", f"{e.val} is not a mutable local.",e)
            return self.expr(e)
        if e.tag == "index":
            base=e.args[0]
            if base.tag != "name" or base.val not in self.env or self.env[base.val].ty.mode != "rw":
                fail("E-WRITE-LEASE", "Indexed assignment requires a direct rw slice parameter.",e)
            self.effect("write:"+base.val)
            return self.index(e, False)
        if e.tag == "field":
            self.lvalue(e.args[0])
            return self.expr(e)
        fail("E-LVALUE", "Assignment requires a mutable variable, field, or rw element.",e)
    def index(self, e: Expr, read: bool=True) -> Type:
        a,i=e.args
        ty=self.expr(a)
        if ty.mode == "value": fail("E-INDEX", "Only views can be indexed.",e)
        self.expr(i,Type("usize")); self.guard("bounds")
        if read: self.effect("read:" + a.val)
        length=ty.extent if ty.extent.isdigit() else "v_"+ty.extent
        e.ty=Type(ty.name); e.cpp=f"cr::at({a.cpp}, {i.cpp}, {length})"
        return e.ty
    def expr(self, e: Expr, expected: Type|None=None) -> Type:
        self.nodes += 1
        if self.nodes > MAX_NODES: fail("E-AST-LIMIT", "Expanded typechecking exceeds 200000 expression visits.",e)
        tag=e.tag
        if tag == "int":
            ty = expected if expected and expected.mode=="value" and expected.name in NUMERIC else Type("u64")
            n=int(e.val)
            if ty.name in WIDTH:
                bits=WIDTH[ty.name]-(1 if ty.name in SIGNED else 0)
                if n >= 2**bits: fail("E-LITERAL-RANGE",f"Literal is not representable in {ty.name}.",e)
            elif n > 2**53: fail("E-LITERAL-RANGE","Large integer-to-float literals require a checked explicit conversion.",e)
            e.cpp=f"static_cast<{ty.cpp()}>({n}{'ULL' if n < 2**64 else ''})"
        elif tag == "float":
            ty=expected if expected and expected.name in FLOAT else Type("f64")
            import math
            v=float(e.val)
            if not math.isfinite(v) or (ty.name=="f32" and abs(v)>3.4028234663852886e38):
                fail("E-LITERAL-RANGE", "Nonfinite/overflowing floating literal.",e)
            e.cpp=e.val + ("f" if ty.name=="f32" else "")
        elif tag == "bool": ty=Type("bool"); e.cpp=e.val
        elif tag == "name":
            if e.val not in self.env: fail("E-UNBOUND", f"Unbound name {e.val}.",e, available_names=sorted(self.env), expected_type=expected.display() if expected else None)
            b=self.env[e.val]; ty=b.ty
            e.cpp=f"static_cast<std::size_t>({b.constant}ULL)" if b.constant is not None else "v_"+e.val
        elif tag == "index":
            ty=self.index(e)
        elif tag == "field":
            a=e.args[0]
            if a.tag=="name" and a.val in self.p.enums and a.val not in self.env:
                if e.val not in self.p.enums[a.val]: fail("E-ENUM-VARIANT", f"Unknown {a.val}.{e.val}.",e)
                ty=Type(a.val); e.cpp="ct_"+a.val+"::v_"+e.val
            else:
                at=self.expr(a)
                if at.mode!="value" or at.name not in self.p.records: fail("E-FIELD", "Field access requires a record.",e)
                fs=dict(self.p.records[at.name])
                if e.val not in fs: fail("E-FIELD", f"Unknown field {e.val}.",e)
                ty=fs[e.val]; e.cpp=f"({a.cpp}).v_{e.val}"
        elif tag == "unary":
            a=e.args[0]; ty=self.expr(a, Type("bool") if e.val=="!" else expected)
            if ty.mode!="value": fail("E-OPERATOR", "Unary operator on a view.",e)
            if e.val=="!": self.expect(ty,Type("bool"),e); e.cpp=f"(!{a.cpp})"
            elif e.val=="~":
                if ty.name not in UNSIGNED: fail("E-OPERATOR", "Bitwise complement requires an unsigned integer.",e)
                e.cpp=f"static_cast<{ty.cpp()}>(~{a.cpp})"
            elif ty.name in FLOAT: e.cpp=f"(-{a.cpp})"
            elif ty.name in SIGNED:
                self.guard("overflow"); e.cpp=f"cr::sub<{ty.cpp()}>(0, {a.cpp})"
            else: fail("E-OPERATOR", "Negation requires a signed integer or floating value.",e)
        elif tag == "binary":
            a,b=e.args; op=e.val
            logical=op in {"&&","||"}; compare=op in {"==","!=","<","<=",">",">="}
            hint=Type("bool") if logical else (None if compare else expected)
            # Literals adapt to their nonliteral peer; no general implicit conversion.
            if a.tag in {"int","float"} and b.tag not in {"int","float"}:
                right=self.expr(b,hint); left=self.expr(a,right)
            else:
                left=self.expr(a,hint); right=self.expr(b,left)
            self.expect(right,left,e)
            if left.mode!="value": fail("E-OPERATOR", "View operators are not implicit loops.",e)
            if logical:
                self.expect(left,Type("bool"),e); ty=left; e.cpp=f"({a.cpp} {op} {b.cpp})"
            elif compare:
                if left.name not in NUMERIC|{"bool"} and not (op in {"==","!="} and left.name in self.p.enums):
                    fail("E-OPERATOR", "Comparison requires scalars or equality on enums.",e)
                ty=Type("bool"); e.cpp=f"({a.cpp} {op} {b.cpp})"
            elif op in {"&","|","^"}:
                if left.name not in UNSIGNED: fail("E-OPERATOR", "Bitwise operation requires unsigned scalars.",e)
                ty=left; e.cpp=f"static_cast<{ty.cpp()}>({a.cpp} {op} {b.cpp})"
            elif left.name in FLOAT and op!="%":
                ty=left; e.cpp=f"({a.cpp} {op} {b.cpp})"
            elif left.name in INT:
                ty=left; fn={"+":"add","-":"sub","*":"mul","/":"divide","%":"remainder"}[op]
                self.guard("division" if op in {"/","%"} else "overflow")
                e.cpp=f"cr::{fn}<{ty.cpp()}>({a.cpp}, {b.cpp})"
            else: fail("E-OPERATOR", f"{op} not defined on {left.name}.",e)
        elif tag == "call":
            ty=self.call(e,expected)
        else: fail("E-INTERNAL", f"Unknown expression {tag}.",e)
        e.ty=ty
        if expected: self.expect(ty,expected,e)
        if self.capture_sites and e.start >= 0:
            self.sites.append({"symbol": self.f.name if self.f else "", "start":e.start,
                "end":e.end, "tag":e.tag, "type":ty.display(),
                "expected_type":expected.display() if expected else None,
                "bindings":{n:{"type":b.ty.display(), "mutable":b.mutable,
                    "constant":b.constant} for n,b in self.env.items()}})
        return ty
    def call(self,e:Expr,expected:Type|None)->Type:
        n=e.val; args=e.args
        if n in NUMERIC:
            if len(args)!=1: fail("E-ARITY", "Scalar conversion takes one argument.",e)
            src=self.expr(args[0]); ty=Type(n)
            if src.mode!="value" or src.name not in NUMERIC: fail("E-CAST", "Conversion requires numeric scalar.",e)
            if src.name in FLOAT and n in INT:
                fail("E-CAST", "Float-to-integer conversion is not in the bootstrap subset.",e)
            if src.name in INT and n in INT:
                self.guard("conversion"); e.cpp=f"cr::convert<{ty.cpp()}>({args[0].cpp})"
            else: e.cpp=f"static_cast<{ty.cpp()}>({args[0].cpp})"
            return ty
        if n in {"add_wrap","sub_wrap","mul_wrap","shl_wrap","shr"}:
            if len(args)!=2: fail("E-ARITY",f"{n} takes two arguments.",e)
            a,b=args
            if a.tag=="int" and b.tag!="int" and n not in {"shl_wrap","shr"}:
                t=self.expr(b,expected); self.expr(a,t)
            else:
                t=self.expr(a,expected); self.expr(b,Type("usize") if n in {"shl_wrap","shr"} else t)
            if t.mode!="value" or t.name not in UNSIGNED: fail("E-WRAP-TYPE", "Wrapping/bit shift operations require unsigned integers.",e)
            if n in {"shl_wrap","shr"}: self.guard("shift")
            e.cpp=f"cr::{n}<{t.cpp()}>({a.cpp}, {b.cpp})"; return t
        if n in {"min","max"}:
            if len(args)!=2: fail("E-ARITY",f"{n} takes two arguments.",e)
            t=self.expr(args[0],expected); self.expr(args[1],t)
            if t.name not in INT or t.mode!="value": fail("E-MINMAX", "Bootstrap min/max are integer-only; floating NaN semantics must be explicit.",e)
            e.cpp=f"std::{n}({args[0].cpp}, {args[1].cpp})"; return t
        if n in self.p.records:
            fs=self.p.records[n]
            if len(fs)!=len(args): fail("E-ARITY",f"Record {n} expects {len(fs)} fields in declaration order.",e)
            for a,(_,t) in zip(args,fs): self.expr(a,t)
            e.cpp="ct_"+n+"{"+", ".join(a.cpp for a in args)+"}"; return Type(n)
        if n not in self.fs: fail("E-CALLEE",f"Unknown callable {n}; arbitrary C++ names are not allowed.",e)
        f=self.fs[n]
        if len(args)!=len(f.params): fail("E-ARITY", f"{n} expects {len(f.params)} arguments.",e)
        subst={name:a for (name,_),a in zip(f.params,args)}
        views=[]
        for a,(name,t) in zip(args,f.params):
            if t.mode=="value": self.expr(a,t)
            else:
                at=self.expr(a)
                ext=t.extent
                if not ext.isdigit():
                    v=subst[ext]
                    if v.tag not in {"name","int"}: fail("E-CALL-SHAPE", "View extent argument must be a name or literal.",v)
                    ext=v.val
                want=Type(t.name,t.mode,ext)
                if at.mode=="rw" and want.mode=="ro": want=Type(t.name,"rw",ext)
                self.expect(at,want,a)
                if a.tag!="name": fail("E-CALL-VIEW", "Only direct view parameters may be passed.",a)
                views.append((a.val,t.mode))
        for i,(a,m) in enumerate(views):
            for b,k in views[i+1:]:
                if a==b and (m=="rw" or k=="rw"):
                    fail("E-ALIAS", "A mutable view cannot be passed to overlapping call arguments.",e)
        assert self.f is not None
        self.call_edges[self.f.name].append((n,{formal:actual.val
            for (formal,t),actual in zip(f.params,args) if t.mode != "value"}))
        self.callset.add(n); e.cpp="cf_"+n+"("+", ".join(a.cpp for a in args)+")"
        return f.ret

RUNTIME = r'''// CAIRN Native runtime: guarded values and views, no allocator or scheduler.
#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <type_traits>
#include <utility>
namespace cr {
[[noreturn]] inline void trap() noexcept { std::abort(); }
template<class T> inline T add(T a,T b) noexcept { T r; if(__builtin_add_overflow(a,b,&r)) trap(); return r; }
template<class T> inline T sub(T a,T b) noexcept { T r; if(__builtin_sub_overflow(a,b,&r)) trap(); return r; }
template<class T> inline T mul(T a,T b) noexcept { T r; if(__builtin_mul_overflow(a,b,&r)) trap(); return r; }
template<class T> inline T divide(T a,T b) noexcept {
  if(b==0) trap();
  if constexpr(std::is_signed_v<T>) if(a==std::numeric_limits<T>::min() && b==T(-1)) trap();
  return a/b;
}
template<class T> inline T remainder(T a,T b) noexcept {
  if(b==0) trap();
  if constexpr(std::is_signed_v<T>) if(a==std::numeric_limits<T>::min() && b==T(-1)) trap();
  return a%b;
}
template<class T> inline T add_wrap(T a,T b) noexcept { return static_cast<T>(std::uint64_t(a)+std::uint64_t(b)); }
template<class T> inline T sub_wrap(T a,T b) noexcept { return static_cast<T>(std::uint64_t(a)-std::uint64_t(b)); }
template<class T> inline T mul_wrap(T a,T b) noexcept { return static_cast<T>(std::uint64_t(a)*std::uint64_t(b)); }
template<class T> inline T shl_wrap(T a,std::size_t n) noexcept {
  if(n>=sizeof(T)*8) trap();
  return static_cast<T>(std::uint64_t(a)<<n);
}
template<class T> inline T shr(T a,std::size_t n) noexcept {
  if(n>=sizeof(T)*8) trap();
  return static_cast<T>(std::uint64_t(a)>>n);
}
template<class T,class S> inline T convert(S x) noexcept {
  if(!std::in_range<T>(x)) trap();
  return static_cast<T>(x);
}
template<class T> inline T& at(T* p,std::size_t i,std::size_t n) noexcept {
  if(i>=n) trap();
  return p[i];
}
template<class T> inline void view(T* p,std::size_t n) noexcept {
  if(n==0) return;
  if(p==nullptr || reinterpret_cast<std::uintptr_t>(p)%alignof(T)) trap();
  if(n>std::numeric_limits<std::size_t>::max()/sizeof(T)) trap();
  const auto a=reinterpret_cast<std::uintptr_t>(p);
  if(a>std::numeric_limits<std::uintptr_t>::max()-n*sizeof(T)) trap();
}
template<class A,class B> inline void disjoint(A* a,std::size_t na,B* b,std::size_t nb) noexcept {
  if(na==0 || nb==0) return;
  const auto x=reinterpret_cast<std::uintptr_t>(a), y=reinterpret_cast<std::uintptr_t>(b);
  if(x<y+nb*sizeof(B) && y<x+na*sizeof(A)) trap();
}
} // namespace cr
'''

class Emitter:
    def __init__(self,p:Program): self.p=p; self.lines=[]; self.ind=0; self.counter=0
    def put(self,s=""): self.lines.append("  "*self.ind+s)
    def name(self,n): return "v_"+n
    def extent(self,t): return t.extent if t.extent.isdigit() else self.name(t.extent)
    def signature(self,f):
        ps=", ".join(t.cpp()+" "+self.name(n) for n,t in f.params)
        return f'extern "C" {f.ret.cpp()} cf_{f.name}({ps}) noexcept'
    def emit(self):
        self.put('// Generated by '+VERSION+'. Do not edit; edit the CAIRN source.')
        self.put('#include "cairn_runtime.hpp"')
        for n,fs in self.p.records.items():
            self.put("struct ct_"+n+" {")
            self.ind+=1
            for f,t in fs: self.put(t.cpp()+" "+self.name(f)+";")
            self.ind-=1; self.put("};")
        for n,vs in self.p.enums.items():
            self.put("enum class ct_"+n+" : std::uint32_t { "+", ".join(self.name(v) for v in vs)+" };")
        for f in self.p.functions: self.put(self.signature(f)+";")
        for f in self.p.functions:
            self.put(); self.put(self.signature(f)+" {"); self.ind+=1
            arrays=[(n,t) for n,t in f.params if t.mode!="value"]
            for n,t in arrays: self.put(f"cr::view({self.name(n)}, {self.extent(t)});")
            for i,(n,t) in enumerate(arrays):
                for m,u in arrays[i+1:]:
                    if t.mode=="rw" or u.mode=="rw":
                        self.put(f"cr::disjoint({self.name(n)}, {self.extent(t)}, {self.name(m)}, {self.extent(u)});")
            for n,t in f.params:
                if t.name in self.p.enums and t.mode=="value":
                    self.put(f"if(static_cast<std::uint32_t>({self.name(n)}) >= {len(self.p.enums[t.name])}) cr::trap();")
            self.block(f.body); self.ind-=1; self.put("}")
        return "\n".join(self.lines)+"\n"
    def block(self,ss):
        for s in ss:
            es=s.exprs
            if s.tag in {"let","reg"}:
                self.put(("const " if s.tag=="let" else "")+s.ty.cpp()+" "+self.name(s.name)+" = "+es[0].cpp+";")
            elif s.tag=="compact":
                out,hi,pred,value = es
                used=self.name(s.name); i=self.name(s.binder)
                self.put(f"std::size_t {used} = 0;")
                self.put(f"for (std::size_t {i}=0; {i}<{hi.cpp}; ++{i}) {{")
                self.ind+=1
                self.put(f"if ({pred.cpp}) {{")
                self.ind+=1
                # The only unchecked store constructor: induction gives used <= i < n.
                # The source cannot mutate used or read output during collection.
                self.put(f"{out.cpp}[{used}] = {value.cpp};")
                self.put(f"++{used};")
                self.ind-=1; self.put("}")
                self.ind-=1; self.put("}")
            elif s.tag=="assign": self.put(es[0].cpp+" = "+es[1].cpp+";")
            elif s.tag=="return": self.put("return"+(" "+es[0].cpp if es else "")+";")
            elif s.tag=="expr": self.put(es[0].cpp+";")
            elif s.tag in {"if","while"}:
                condition = es[0].cpp
                if condition.startswith("(") and condition.endswith(")"):
                    condition = condition[1:-1]
                self.put(s.tag+" ("+condition+") {"); self.ind+=1; self.block(s.body); self.ind-=1
                if s.other:
                    self.put("} else {"); self.ind+=1; self.block(s.other); self.ind-=1
                self.put("}")
            elif s.tag=="for":
                self.counter+=1; lim="cr_limit_"+str(self.counter)
                self.put("{"); self.ind+=1
                # Source order is lower bound, upper bound, then iteration.
                begin="cr_begin_"+str(self.counter)
                self.put(f"const std::size_t {begin} = {es[0].cpp};")
                self.put(f"const std::size_t {lim} = {es[1].cpp};")
                n=self.name(s.name)
                self.put(f"for (std::size_t {n} = {begin}; {n} < {lim}; ++{n}) {{")
                self.ind+=1; self.block(s.body); self.ind-=1; self.put("}")
                self.ind-=1; self.put("}")

def compile_source(source:str)->tuple[str,dict[str,Any]]:
    p=specialize(derive_wire(Parser(source).parse()))
    checker=Checker(p); receipts=checker.check()
    cpp=Emitter(p).emit()
    manifest={"compiler":VERSION,"source_sha256":hashlib.sha256(source.encode()).hexdigest(),
              "generated_sha256":hashlib.sha256(cpp.encode()).hexdigest(),
              "runtime_sha256":hashlib.sha256(RUNTIME.encode()).hexdigest(),
              "function_count":len(p.functions),"families":[list(x) for x in p.families],
              "wire_derivations":p.derivations,
              "trusted_lowering_rules":["bounded-collector/1","unsigned-little-endian-wire/1"],
              "functions":receipts,"formal_status":"not-verified",
              "ffi_requires":"Each nonempty view describes live, initialized, correctly typed storage for its stated extent throughout the call; no concurrent external mutation.",
              "target_profile":"64-bit host, C++20, GCC/Clang overflow builtins, strict floating mode"}
    return cpp,manifest

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source",type=Path)
    ap.add_argument("-o","--output",type=Path)
    ap.add_argument("--check",action="store_true",help="Check and report without writing generated files")
    ap.add_argument("--receipt",type=Path)
    args=ap.parse_args()
    try:
        source=args.source.read_text(encoding="utf-8")
        cpp,receipt=compile_source(source)
        if args.output and not args.check:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(cpp)
            (args.output.parent/"cairn_runtime.hpp").write_text(RUNTIME)
        if args.receipt: args.receipt.write_text(json.dumps(receipt,indent=2)+"\n")
        if not args.output or args.check:
            print(json.dumps({"status":"accepted","compiler":VERSION,"functions":receipt["function_count"],"formal_status":"not-verified"}))
        return 0
    except Diagnostic as e:
        print(json.dumps(e.data),file=sys.stderr); return 1
    except (OSError,UnicodeError,RecursionError,ValueError,OverflowError) as e:
        print(json.dumps({"protocol":"cairn.diagnostic/1","status":"unknown","code":"E-RESOURCE-OR-IO","message":str(e)}),file=sys.stderr); return 2

if __name__=="__main__": sys.exit(main())

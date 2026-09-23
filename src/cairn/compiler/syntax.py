"""The parser: syntax and source ranges only.

Tokens come from lexing.py and the tree it builds is tree.py; every name the parser produces is
resolved, typed and given effects by checking.py. Nothing here evaluates source. `Parser` is three layers:
the cursor, types and expressions (syntax_expressions.py), statements (syntax_statements.py), and here the
declarations of a source file and the entry point `Parser.parse`.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .lexing import IDENT as IDENT
from .lexing import RESERVED as RESERVED
from .lexing import Token, unescape
from .syntax_expressions import ARM_STATEMENTS as ARM_STATEMENTS
from .syntax_expressions import PREC as PREC
from .syntax_expressions import REDUCERS as REDUCERS
from .syntax_expressions import copied as copied
from .syntax_expressions import lent_part as lent_part
from .syntax_statements import StatementParser
from .tree import INTRINSIC_TYPES as INTRINSIC_TYPES  # The language server takes the vocabulary from here.
from .tree import SCALAR as SCALAR
from .tree import VOID, Function, Impl, Program, Recipe, Shape, Stmt, Type, fail


class Parser(StatementParser):
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
        name = self.take()
        while self.eat(":"):
            name += ":" + self.take()
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
                p.scopes.append((t.start, self.module))
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
                lent: tuple[str, str, str] | None = None
                while not self.eat("}"):
                    if self.t.s == "lends" and self.ahead(1) != ":" and self.eat("lends"):  # the view it lends
                        if lent:
                            fail("E-LENDS", f"{n} already lends {lent[0]}; a record lends one view.", self.t)
                        lent = (self.ident(), *self.bounds())
                        self.need(";")
                        continue
                    fs.append(self.parameter())
                    if self.eat("["):  # `price:Buf[f64][rows]`: this field holds as many elements as `rows` says.
                        carried[fs[-1][0]] = self.ident()
                        self.need("]")
                    self.need(";")
                full = declare(n, t)
                p.records[full], p.generics[full], p.attributes[full] = fs, generics, attributes
                if carried:
                    p.field_extents[full] = carried
                if lent:
                    p.lends[full] = lent
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
            elif self.t.s == "layout" and IDENT.fullmatch(self.ahead(1)) and self.ahead(2) == "=":  # A word only here:
                self.i += 1  # `layout T = pad(rows(32, 32), 1);`, folded by compiler/layouts.py.
                n = self.ident()
                self.need("=")
                p.layouts[declare(n, t)] = self.expr()
                self.need(";")
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

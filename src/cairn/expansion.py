"""Bounded AST generators: library recipes applied by `derive`, and static families. No external inputs."""

from __future__ import annotations

import copy
import operator
import re
from dataclasses import dataclass
from functools import reduce
from typing import Any

from .syntax import (  # isort: skip
    FLOAT, INT, MAX_FAMILY, MAX_FUNCTIONS, MAX_NODES, SCALAR, SIGNED, UNSIGNED, WIDTH, Arm, Each, Expr, Function,
    Program, Recipe, Shape, Stmt, Type, fail,
)  # fmt: skip

OPERATORS = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.floordiv, "%": operator.mod,
             "==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt,
             ">=": operator.ge, "&&": lambda a, b: a and b, "||": lambda a, b: a or b}  # fmt: skip


def declared(p: Program) -> set[str]:
    return {f.name for f in p.functions} | set(p.records) | set(p.enums) | set(p.sums)


def visible(p: Program, module: str, written: str, table: Any) -> str | None:
    """Resolve a name written in `module` before any checker exists: alias, own module, then the root."""
    head, _, rest = written.partition(".")
    aliases = {alias: target for importer, target, alias in p.imports if importer == module}
    aliased = [f"{aliases[head]}.{rest}"] if rest and head in aliases else []
    found = next((n for n in [*aliased, f"{module}.{written}", written] if n in table), None)
    if found and p.modules.get(found, "") not in ("", module) and found not in p.public:
        fail("E-PRIVATE", f"{found} is private to module {p.modules[found]}.")
    return found


@dataclass
class Field:
    name: str
    ty: Type
    index: int
    before: list[Type]  # The fields declared ahead of it: its packed offset is their size.


class Deriver:
    """One application of a recipe: static evaluation, `$` splices, unrolled `each`. It reads nothing but
    the recipe and the schema it is applied to, so the same inputs always generate the same declarations."""

    def __init__(self, p: Program, recipe: Recipe, statics: dict[str, Any]):
        self.p, self.recipe, self.nodes = p, recipe, 0
        self.root = self.scope(statics, recipe.where)

    def scope(self, env: dict[str, Any], where: list[tuple[str, Expr]]) -> dict[str, Any]:
        env = dict(env)
        for (
            name,
            e,
        ) in where:  # Evaluated when first spliced, so a `require` can refuse its input before it is measured.
            env[name] = (e, env.copy())
        return env

    def bits(self, x: Any, node: Any) -> int:
        ty = x.ty if isinstance(x, Field) else x
        if isinstance(ty, Type) and ty.mode == "value" and not ty.args:
            if ty.name in WIDTH or ty.name in {"bool", "f32", "f64"}:
                return WIDTH.get(ty.name, {"bool": 8, "f32": 32}.get(ty.name, 64))
            if ty.name in self.p.records and not self.p.generics.get(ty.name):
                return sum(self.bits(t, node) for _, t in self.p.records[ty.name])
        fail("E-RECIPE-STATIC", "A size is defined for scalars and for records of them.", node)

    def static(self, e: Expr, env: dict[str, Any]) -> Any:
        """The static language of `where`, `require` and ranges: naturals, booleans, and facts about types."""
        if e.tag in {"int", "bool"}:
            return int(e.val) if e.tag == "int" else e.val == "true"
        if e.tag == "name":
            value = env.get(e.val.lstrip("$"))
            if value is None:
                fail("E-RECIPE-STATIC", f"{e.val} is not a static name of this recipe.", e)
            if isinstance(value, tuple):
                value = env[e.val.lstrip("$")] = self.static(*value)
            return value
        if e.tag == "unary" and e.val in {"!", "-"}:
            value = self.static(e.args[0], env)
            return not value if e.val == "!" else -value
        if e.tag == "binary" and e.val in OPERATORS:
            return OPERATORS[e.val](*(self.static(a, env) for a in e.args))
        if e.tag == "call" and len(e.args) == 1:
            x = self.static(e.args[0], env)
            ty = x.ty if isinstance(x, Field) else x
            facts = {
                "bits": lambda: self.bits(x, e), "bytes": lambda: self.bits(x, e) // 8, "typeof": lambda: ty,
                "index": lambda: x.index, "offset": lambda: sum(self.bits(t, e) for t in x.before) // 8,
                "count": lambda: len(self.fields(ty, e)), "unsigned": lambda: ty.name in UNSIGNED and ty.mode == "value",
                "signed": lambda: ty.name in SIGNED, "integer": lambda: ty.name in INT, "float": lambda: ty.name in FLOAT,
                "scalar": lambda: ty.name in SCALAR, "record": lambda: ty.name in self.p.records,
            }  # fmt: skip
            if e.val in facts and isinstance(ty, Type) and (isinstance(x, Field) or e.val not in {"index", "offset"}):
                return facts[e.val]()
        fail("E-RECIPE-STATIC", "This is not a static expression: naturals, comparisons and facts such as bytes(f).", e)

    def fields(self, ty: Any, node: Any) -> list[Field]:
        if not isinstance(ty, Type) or ty.name not in self.p.records or self.p.generics.get(ty.name):
            fail("E-DERIVE-TYPE", f"{ty.display() if isinstance(ty, Type) else ty} is not a plain record.", node)
        declared = self.p.records[ty.name]
        return [Field(n, t, i, [u for _, u in declared[:i]]) for i, (n, t) in enumerate(declared)]

    def each(self, each: Each, env: dict[str, Any]):
        """The environments of one static iteration, in order."""
        bounds = [self.static(e, env) for e in each.seq]
        values = range(*bounds) if len(bounds) == 2 else self.fields(bounds[0], each.seq[0])
        if len(values) > MAX_FAMILY:
            fail("E-EXPANSION-LIMIT", f"A static iteration has at most {MAX_FAMILY} steps.", each.seq[0])
        for value in values:
            yield self.scope({**env, each.binder: value}, each.where)

    def text(self, written: str, env: dict[str, Any], node: Any) -> str:
        """Splice `$name` into an identifier: a field's name, a type's own name, a natural's digits."""

        def spliced(m: re.Match) -> str:
            name = m.group(1)  # `$R_columns` is `$R` then `_columns`: the longest static name wins.
            while name not in env and "_" in name:
                name = name.rsplit("_", 1)[0]
            value = self.static(Expr("name", name, line=getattr(node, "line", 0)), env)
            if isinstance(value, bool) or not isinstance(value, (int, Field, Type)):
                fail("E-RECIPE-STATIC", f"${name} cannot be part of a name.", node)
            shown = value.name.rsplit(".", 1)[-1] if isinstance(value, (Field, Type)) else str(value)
            return shown + m.group(1)[len(name) :]

        head, dot, rest = written.partition(".")
        whole = env.get(head)  # The bare type parameter is the type itself: R, R.Variant.
        return (
            whole.name + dot + rest
            if isinstance(whole, Type)
            else re.sub(r"\$([A-Za-z_][A-Za-z_0-9]*)", spliced, written)
        )

    def type(self, t: Any, env: dict[str, Any], node: Any) -> Any:
        if not isinstance(t, Type):
            return t
        value = self.static(Expr("name", t.name), env) if t.name.lstrip("$") in env else None
        base = value if isinstance(value, Type) else Type(self.text(t.name, env, node))
        args = base.args or tuple(self.type(a, env, node) for a in t.args)
        return Type(base.name, t.mode, self.text(t.extent, env, node), args, t.place)

    def exprs(self, e: Expr, env: dict[str, Any]) -> list[Expr]:
        """An expression expands to one expression, except `each`, which splices a list into its call."""
        self.nodes += 1
        if self.nodes > MAX_NODES:
            fail("E-EXPANSION-LIMIT", f"Recipe {self.recipe.name} exceeds the expansion budget.", e)
        if e.tag == "each":
            return [x for inner in self.each(e.ref, env) for x in self.exprs(e.ref.items[0], inner)]
        if e.tag == "fold":
            parts = self.exprs(e.args[0], env)
            if not parts:
                fail("E-RECIPE-STATIC", "fold needs at least one operand.", e)
            return [reduce(lambda a, b: Expr("binary", e.val, [a, b], e.line, e.col), parts)]
        if e.tag == "name" and e.val.startswith("$") and e.val[1:] in env:
            value = self.static(e, env)
            if isinstance(value, (bool, int)):
                tag = "bool" if isinstance(value, bool) else "int"
                return [Expr(tag, str(value).lower(), [], e.line, e.col)]
        args = [x for a in e.args for x in self.exprs(a, env)]
        if e.tag == "binary" and e.val in {"+", "-", "*"} and all(a.tag == "int" for a in args):
            folded = OPERATORS[e.val](*(int(a.val) for a in args))  # Offsets read as the literals they are.
            if folded >= 0:
                return [Expr("int", str(folded), [], e.line, e.col)]
        ref = e.ref
        if isinstance(ref, tuple):  # Explicit type arguments of a call.
            ref = tuple(self.type(t, env, e) for t in ref)
        elif isinstance(ref, Function):  # A closure written inside the recipe.
            ref = self.function(ref, env, "")
        text = e.val if e.tag in {"int", "float", "bool", "str", "binary", "unary"} else self.text(e.val, env, e)
        return [Expr(e.tag, text, args, e.line, e.col, ref=ref)]

    def expr(self, e: Expr, env: dict[str, Any]) -> Expr:
        found = self.exprs(e, env)
        if len(found) != 1:
            fail("E-RECIPE-STATIC", "`each` splices a list; it belongs among the arguments of a call.", e)
        return found[0]

    def stmts(self, ss: list[Stmt], env: dict[str, Any]) -> list[Stmt]:
        out: list[Stmt] = []
        for s in ss:
            if s.tag == "each":
                out += [x for inner in self.each(s.ref, env) for x in self.stmts(s.ref.items, inner)]
            elif s.tag == "require":
                self.require(s, env)
            else:
                arms = [
                    Arm(self.text(a.variant, env, a), a.binder, self.stmts(a.body, env), a.line, a.col) for a in s.arms
                ]
                out.append(Stmt(s.tag, self.text(s.name, env, s), self.type(s.ty, env, s), [self.expr(e, env) for e in s.exprs],
                                self.stmts(s.body, env), self.stmts(s.other, env), s.line, s.col, s.binder, arms, s.op, s.ref))  # fmt: skip
        return out

    def require(self, s: Stmt, env: dict[str, Any]):
        if self.static(s.exprs[0], env) is not True:
            code, colon, message = s.name.partition(": ")  # A message may open with the diagnostic code it keeps.
            known = colon and re.fullmatch(r"E-[A-Z-]+", code)
            fail(code if known else "E-DERIVE-DOMAIN", message if known else s.name, s)

    def function(self, f: Function, env: dict[str, Any], prefix: str) -> Function:
        made = copy.copy(f)
        made.name = prefix + self.text(f.name, env, f)
        made.params = [(self.text(n, env, f), self.type(t, env, f)) for n, t in f.params]
        made.ret, made.body = self.type(f.ret, env, f), self.stmts(f.body, env)
        return made

    def shape(self, fields: list[Any], env: dict[str, Any]) -> list[tuple[str, Type]]:
        out: list[tuple[str, Type]] = []
        for item in fields:
            if isinstance(item, Each):
                out += [x for inner in self.each(item, env) for x in self.shape(item.items, inner)]
            else:
                out.append((self.text(item[0], env, None), self.type(item[1], env, None)))
        return out

    def items(self, items: list[Any], env: dict[str, Any], prefix: str) -> list[Any]:
        """The declarations one application generates: functions, and records as (name, fields, public)."""
        out: list[Any] = []
        for item in items:
            if isinstance(item, Each):
                out += [x for inner in self.each(item, env) for x in self.items(item.items, inner, prefix)]
            elif isinstance(item, Stmt):
                self.require(item, env)
            elif isinstance(item, Shape):
                out.append((prefix + self.text(item.name, env, item), self.shape(item.fields, env), item.public))
            else:
                out.append(self.function(item, env, prefix))
        return out


def derive(p: Program) -> Program:
    """Apply every `derive recipe[naturals] for Type;`. The generated functions are ordinary code of the
    deriving module, checked like any other; a bare recipe name falls back to the packaged std.<name>."""
    names = declared(p)
    for module, written, naturals, target, at in p.derivations:
        found = visible(p, module, written, p.recipes) or f"std.{written}.{written}"
        recipe = p.recipes.get(found)
        if recipe is None or len(naturals) != len(recipe.nats) or bool(target) != bool(recipe.param):
            fail("E-DERIVE-RECIPE", f"No recipe {written} takes these arguments; write "
                 "`derive name[naturals] for Type;` as the recipe declares.", at)  # fmt: skip
        full = visible(p, module, target, {**p.records, **p.sums, **p.enums}) if target else ""
        if target and full is None:
            fail("E-DERIVE-TYPE", f"Unknown record {target}.", at)
        statics = dict(zip(recipe.nats, naturals, strict=True)) | ({recipe.param: Type(full)} if target else {})
        deriver = Deriver(p, recipe, statics)
        for made in deriver.items(recipe.items, deriver.root, module + "." if module else ""):
            name, public = (made.name, made.public) if isinstance(made, Function) else (made[0], made[2])
            if name in names or name in p.modules:
                fail("E-DERIVE-COLLISION", f"Derived name {name} already exists.", at)
            names.add(name)
            p.modules[name] = module
            p.public |= {name} if public else set()
            if isinstance(made, Function):
                made.source_name, made.module = f"derive {written}" + (f" for {full}" if target else ""), module
                p.functions.append(made)
            else:
                p.records[name], p.generics[name], p.attributes[name] = made[1], [], set()
    return p


def node_count(f: Function) -> int:
    total = 0
    todo: list[Any] = list(f.body)
    while todo:
        node = todo.pop()
        total += 1
        if isinstance(node, Stmt):
            todo.extend(node.body + node.other + node.exprs + node.arms)
        elif isinstance(node, Arm):
            todo.extend(node.body)
        elif isinstance(node, Expr):
            todo.extend(node.args)
    return total


def specialize(p: Program) -> Program:
    """Instantiate each family over its bounded natural range; templates stay for the checker."""
    templates = {f.name: f for f in p.functions if f.generics}
    concrete = [f for f in p.functions if not f.generics]
    names = declared(p) - set(templates)
    estimated = sum(node_count(f) for f in concrete)
    if estimated > MAX_NODES or len(concrete) > MAX_FUNCTIONS:
        fail("E-EXPANSION-LIMIT", "Program exceeds the pre-expansion budget.")
    for prefix, name, lo, hi in p.families:
        home = p.modules.get(prefix, "")
        base = templates.get(visible(p, home, name, templates) or "")
        if base is None or [k for _, k in base.generics] != ["nat"]:
            fail("E-FAMILY-TARGET", f"{name} is not a static function template.")
        if not 0 <= lo < hi <= 2**32 or hi - lo > MAX_FAMILY:
            fail("E-FAMILY-LIMIT", "Family must be a nonempty half-open range, at most 1024 variants, below 2^32.")
        estimated += (hi - lo) * node_count(base)
        if estimated > MAX_NODES or len(concrete) + hi - lo > MAX_FUNCTIONS:
            fail("E-EXPANSION-LIMIT", "Family exceeds the remaining AST/function budget.")
        for k in range(lo, hi):
            f = copy.deepcopy(base)
            f.name, f.bindings = f"{prefix}_{k}", {base.generics[0][0]: k}
            if f.name in names:
                fail("E-DUPLICATE", f"Family emits duplicate name {f.name}.")
            names.add(f.name)
            p.modules[f.name], f.public = home, prefix in p.public
            p.public |= {f.name} if f.public else set()
            concrete.append(f)
    p.functions = [*concrete, *templates.values()]
    return p

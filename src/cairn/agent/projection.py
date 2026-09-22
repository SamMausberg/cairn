"""The canonical read-only projection: CAIRN source printed back from the tree, declarations and signatures.

An agent reads a program through this projection. Printing, reparsing and printing again gives the same text, and
compiling the projection gives the same C++ as the source it came from; comments are not copied.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from ..compiler.cairnc import Expr, Function, Parser, Program, Stmt, Type, fail
from ..compiler.expansion import declared, derive
from ..compiler.lexing import lex
from ..compiler.modules import link
from ..compiler.syntax import ARM_STATEMENTS


def generics(params: list[tuple[str, str]]) -> str:
    shown = ", ".join(n if kind == "type" else f"{n}:{kind.replace('+', ' + ')}" for n, kind in params)
    return f"[{shown}]" if params else ""


def local(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def signature(f: Function) -> str:
    """The declared interface, exactly as an edit must preserve it."""
    ps = ", ".join(n + ":" + t.display() for n, t in f.params)
    ret = "" if f.ret.name == "void" else " -> " + f.ret.display()
    ceiling = "" if f.effects is None else " pure" if f.effects == ("pure",) else f" effects({', '.join(f.effects)})"
    written = f.name if f.source_name.startswith("derive ") else f.source_name  # A derived impl keeps its origin there.
    name = written.rsplit(".", 1)[-1] if f.owner else f.name.rsplit(".", 1)[-1] if f.module else f.name
    link = f'"{f.symbol}" ' if f.symbol else ""
    return f"{'extern ' * f.extern}{link}{'kernel ' * f.kernel}fn {name}{generics(f.generics if not f.bindings else [])}({ps}){ret}{ceiling}"


ESCAPES = {"\n": "\\n", "\t": "\\t", "\r": "\\r", "\0": "\\0", "\\": "\\\\", '"': '\\"'}


def format_expr(e: Expr) -> str:
    args = [format_expr(a) for a in e.args] if e.tag != "lambda" else []
    if e.tag in {"name", "int", "float", "bool", "function"}:
        return e.val
    if e.tag == "str":
        return '"' + "".join(ESCAPES.get(c, c if 32 <= ord(c) < 127 else f"\\x{ord(c):02x}") for c in e.val) + '"'
    if e.tag == "call":
        targs = e.ref if isinstance(e.ref, tuple) and all(isinstance(t, Type | int) for t in e.ref) else ()
        shown = "[" + ", ".join(t.display() if isinstance(t, Type) else str(t) for t in targs) + "]" if targs else ""
        if e.val.startswith("."):
            return f"{args[0]}{e.val}{shown}({', '.join(args[1:])})"
        return f"{e.val}{shown}({', '.join(args)})"
    if e.tag == "index":
        return f"{args[0]}[{', '.join(args[1:])}]"
    if e.tag == "slice":
        return f"{args[0]}[{args[1]}..{args[2]}]"
    if e.tag == "field":
        return f"{args[0]}.{e.val}"
    if e.tag == "spawn" and isinstance(e.ref, Stmt):  # Queued device work: the region is the operand.
        inner = format_block([e.ref], -1).split("\n")[1:-1]  # The region's own lines, without a block around them.
        return "spawn " + "\n".join(inner).strip()
    if e.tag in {"try", "spawn"}:
        return f"{e.tag} {args[0]}" + (" after " + ", ".join(args[1:]) if args[1:] else "")
    if e.tag == "coerce":
        return args[0]
    if e.tag == "unary":
        return f"({e.val}{args[0]})"
    if e.tag == "binary":
        return f"({args[0]} {e.val} {args[1]})"
    if e.tag == "lambda":
        f = e.ref
        ps = ", ".join(n + ":" + t.display() for n, t in f.params)
        return f"|{ps}|" + ("" if f.ret.name == "void" else " -> " + f.ret.display()) + " " + format_block(f.body)
    fail("E-PROJECTION", "Cannot project unknown expression kind.")


def format_block(ss: list[Stmt], indent: int = 0) -> str:
    lines = ["{"]
    pad = "  " * (indent + 1)

    def nested(body: list[Stmt]) -> str:
        return format_block(body, indent + 1)

    def arm(body: list[Stmt]) -> str:  # One simple statement on one line is written without its braces.
        lines = format_block(body, indent + 2).split("\n")
        short = len(body) == 1 and body[0].tag in ARM_STATEMENTS and len(lines) == 3
        return lines[1].strip() if short else "\n".join(lines)

    for s in ss:
        es = [format_expr(e) for e in s.exprs]
        typed = ":" + s.ty.display() if s.tag in {"let", "reg", "reduce"} and s.ty else ""
        if s.tag in {"buffer", "stack"}:
            place = "" if s.ty.place == "host" else "@" + s.ty.place
            line = f"{s.tag} {s.name}:{s.ty.value.display()}[{es[0]}]{place} = zeroed;"
        elif s.tag in {"let", "reg"}:
            line = f"{'let mut' if s.tag == 'reg' else 'let'} {s.name}{typed} = {es[0]};"
        elif s.tag == "unpack":
            line = f"{'let mut' if s.op == 'reg' else 'let'} {s.name}({', '.join(n.val for n in s.other_names)}) = {es[0]};"
        elif s.tag == "compact":
            line = f"let {s.name} = compact {es[0]} for {s.binder} in {es[1]} where {es[2]} yield {es[3]};"
        elif s.tag == "reduce":
            line = f"let {s.name}{typed} = reduce {s.op} {'parallel' if s.pooled else 'for'} {s.binder} in {es[0]} yield {es[1]};"
        elif s.tag == "assign":
            line = f"{es[0]} = {es[1]};"
        elif s.tag in {"break", "continue"}:
            line = s.tag + ";"
        elif s.tag == "return":
            line = "return" + (" " + es[0] if es else "") + ";"
        elif s.tag == "expr":
            line = es[0] + ";"
        elif s.tag == "submit":
            line = f"{es[0]} into {s.name};"
        elif s.tag == "match":
            arms = [f"{pad}  {a.variant}{f'({a.binder})' if a.binder else ''} => {arm(a.body)}" for a in s.arms]
            line = "\n".join([f"match {es[0]} {{", *arms, pad + "}"])
        elif s.tag in {"if", "while"}:
            line = f"{s.tag} {es[0]} {nested(s.body)}" + (f"\n{pad}else {nested(s.other)}" if s.other else "")
        elif s.tag == "for":
            line = f"for {s.name} in {es[0]}..{es[1]} {nested(s.body)}"
        elif s.tag == "parallel":
            order = " after " + ", ".join(t.val for t in s.other_names) if s.other_names else ""
            line = f"parallel {s.name} in {es[0]}{order} {nested(s.body)}"
        elif s.tag == "defer":
            line = "defer " + format_block(s.body, indent).split("\n", 2)[1].strip()
        elif s.tag in {"unsafe", "block"}:
            line = ("unsafe " if s.tag == "unsafe" else "") + nested(s.body)
        else:
            fail("E-PROJECTION", "Cannot project unknown statement kind.")
        lines.append(pad + line)
    lines.append("  " * indent + "}")
    return "\n".join(lines)


def declarations(p: Program) -> dict[str, str]:
    """Every record, enum, sum, trait and constant declaration, keyed by its full name."""
    out: dict[str, str] = {}

    def pub(name: str) -> str:
        return "pub " if name in p.public else ""

    for n, fs in p.records.items():
        marks = p.attributes.get(n, set())
        layout = "".join(" " + a for a in sorted(marks - {"linear"}))
        carried = p.field_extents.get(n, {})
        fields = " ".join(f"{k}:{t.display()}{'[' + carried[k] + ']' if k in carried else ''};" for k, t in fs)
        linear = "linear " * ("linear" in marks)
        out[n] = f"{pub(n)}{linear}struct {local(n)}{generics(p.generics.get(n, []))}{layout} {{ {fields} }}"
    for n, vs in p.enums.items():
        out[n] = f"{pub(n)}enum {local(n)} {{ {' '.join(v + ';' for v in vs)} }}"
    for n, payloads in p.sums.items():
        variants = " ".join(v + (f"({t.display()})" if t else "") + ";" for v, t in payloads)
        out[n] = f"{pub(n)}enum {local(n)}{generics(p.generics.get(n, []))} {{ {variants} }}"
    for n, members in p.traits.items():
        out[n] = f"{pub(n)}trait {local(n)} {{ {' '.join(signature(m) + ';' for m in members)} }}"
    out.update({n: f"{pub(n)}const {local(n)}:{t.display()} = {format_expr(e)};" for n, (t, e) in p.consts.items()})
    return out


def type_declarations(p: Program) -> str:
    return "\n".join(declarations(p).values())


def related(table: dict[str, str], text: str) -> dict[str, str]:
    """The declarations `text` names, closed over the names those declarations use in turn."""
    words = {t.s for t in lex(text)}
    chosen: dict[str, str] = {}
    while fresh := {n: d for n, d in table.items() if n not in chosen and local(n) in words}:
        chosen.update(fresh)
        words |= {t.s for d in fresh.values() for t in lex(d)}
    return {n: d for n, d in table.items() if n in chosen}


def function_source(f: Function) -> str:
    if f.extern:
        return signature(f) + ";"
    return signature(f) + " " + format_block(f.body, 1 if f.owner else 0)


def derivation(recipe: str, naturals: tuple, target: str) -> str:
    arguments = f"[{', '.join(map(str, naturals))}]" if naturals else ""
    return f"derive {recipe}{arguments}" + (f" for {target}" if target else "") + ";"


def canonical_source(source: str) -> str:
    """An inspectable AST projection. Comments are not copied. Not an in-place edit."""
    return projection(Parser(source).parse(), source)


def expanded_source(source: str) -> str:
    """What the program's derivations generated, in the same projection: generated code reads as ordinary code."""
    p = link(Parser(source).parse())
    written = declared(p)
    p = derive(p)
    records = {n: fields for n, fields in p.records.items() if n not in written}
    made = [f for f in p.functions if f.name not in written]
    modules = {n: p.modules[n] for n in [*records, *(f.name for f in made)]}
    return projection(
        Program(records, functions=made, generics=p.generics, public=p.public, modules=modules,
                field_extents=p.field_extents),
        source,
    )  # fmt: skip


def projection(p: Program, source: str) -> str:
    out = []
    for module in dict.fromkeys(p.modules.values()):
        tables: dict[str, Any] = {k: {n: v for n, v in getattr(p, k).items() if p.modules.get(n, "") == module}
                  for k in ("records", "enums", "sums", "traits", "consts")}  # fmt: skip
        out += [f"module {module};"] if module else []
        for importer, target, alias in p.imports:
            if importer != module:
                continue
            names = [n for (m, n), full in p.uses.items() if m == module and full == f"{target}.{n}"]
            renamed = f" as {alias}" if alias != local(target) else ""
            out.append(f"import {target}{renamed}{' (' + ', '.join(names) + ')' if names else ''};")
        shown = type_declarations(
            Program(**tables, generics=p.generics, attributes=p.attributes, public=p.public,
                    field_extents=p.field_extents)
        )  # fmt: skip
        out += [shown] if shown else []
        members: list[Function] = []
        for f in [*(f for f in p.functions if f.module == module), None]:
            if members and (f is None or f.owner != members[0].owner):  # Close the impl block in its place.
                shared = [g for g in members[0].generics if all(g in m.generics for m in members)]
                bodies = ["  " + function_source(replace(m, generics=m.generics[len(shared) :])) for m in members]
                trait, self_type = members[0].owner
                out.append(f"impl{generics(shared)} {trait} for {self_type.display()} {{\n" + "\n".join(bodies) + "\n}")
                members = []
            if f is not None and f.owner:
                members.append(f)
            elif f is not None:
                out.append(("pub " if f.public and module else "") + function_source(f))
        out += [f"{'pub ' * (pre in p.public)}family {local(pre)} = {name}[{lo}..{hi}];"
                for pre, name, lo, hi in p.families if p.modules[pre] == module]  # fmt: skip
        out += [source[r.start : r.end] for r in p.recipes.values() if r.module == module]  # A recipe is its own text.
        out += [derivation(r, naturals, target) for m, r, naturals, target, _ in p.derivations if m == module]
        out += [f"plan {name} {{{''.join(f' {k} {v};' for k, v in (('grain', g), ('lanes', most)) if v)} }}"
                for m, name, g, most, _ in p.plans if m == module]  # fmt: skip
    return "\n\n".join(out) + "\n"


def semantic_ast(p: Program) -> Any:
    """Strip annotations/source locations, preserving all authored AST decisions."""
    erased = {"line", "col", "start", "end", "body_start", "block", "cpp", "ty_inferred"}

    def erase(v):
        if isinstance(v, dict):
            return {k: erase(x) for k, x in v.items() if k not in erased}
        return [erase(x) for x in v] if isinstance(v, list | tuple) else v

    return erase(asdict(p))


def comment_above(text: str, offset: int) -> list[str]:
    """The `//` lines directly above a declaration, as one paragraph (or nothing)."""
    lines = text[:offset].rstrip().split("\n") if offset > 0 else []
    found: list[str] = []
    while lines and lines[-1].lstrip().startswith("//"):
        found.insert(0, lines.pop().lstrip()[2:].strip())
    return [" ".join(found)] if found else []

"""Inspectable guarded C++20 emission from the typed tree. The backend is not verified."""

from __future__ import annotations

import re
from pathlib import Path

from .checking import COMPARISONS, WRAPPING, Checker, is_view
from .syntax import CPP, FLOAT, INT, NUMERIC, VERSION, Expr, Function, Program, Stmt, Type

RUNTIME_FILES = {
    p.name: p.read_text(encoding="utf-8") for p in sorted((Path(__file__).parent / "runtime").glob("*.hpp"))
}
RUNTIME = RUNTIME_FILES["cairn_runtime.hpp"]
CHECKED = {"+": "add", "-": "sub", "*": "mul", "/": "divide", "%": "remainder"}


def mangle(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")


def bare(condition: str) -> str:
    return condition[1:-1] if condition.startswith("(") and condition.endswith(")") else condition


class Emitter:
    def __init__(self, p: Program, checker: Checker | None = None):
        self.p, self.lines, self.ind, self.counter = p, [], 0, 0
        self.loops: list[int] = []
        self.headers = ["cairn_runtime.hpp"]
        self.c = checker or Checker(p)
        if checker is None:
            self.c.check()

    def put(self, s: str = ""):
        self.lines.append("  " * self.ind + s)

    def nest(self, head: str, body, tail: str = "}"):
        self.put(head)
        self.ind += 1
        body()
        self.ind -= 1
        self.put(tail)

    def need(self, header: str):
        if header not in self.headers:
            self.headers.append(header)

    def fresh(self, prefix: str) -> tuple[str, int]:
        self.counter += 1
        return f"{prefix}{self.counter}", self.counter

    # Types -------------------------------------------------------------------------------------

    def type(self, t: Type) -> str:
        if t.name == "fn":
            base = f"{self.type(t.args[-1])} (*)({', '.join(self.type(a) for a in t.args[:-1])})"
        elif t.name == "Buf":
            self.need("cairn_owners.hpp")
            base = f"cr::Buf<{self.type(t.args[0])}>"
        elif t.name == "Array":
            base = f"std::array<{self.type(t.args[0])}, {t.args[1]}>"
        else:
            base = CPP.get(t.name) or "ct_" + mangle(t.value.display())
        if t.mode == "value":
            return base
        return ("const " if t.mode == "ro" else "") + base + ("*" if t.extent else "&")

    def trivial(self, t: Type) -> bool:
        return self.c.kind(t) == "copy"

    def layouts(self):
        for ty, layout in self.c.layouts.items():
            name = "ct_" + mangle(ty.display())
            attributes = self.p.attributes.get(ty.name, set())
            align = next((f"alignas({a[6:-1]}) " for a in attributes if a.startswith("align(")), "")
            if isinstance(layout, list):
                packed = " __attribute__((packed))" if "packed" in attributes else ""
                self.nest(f"struct {align}{name} {{", lambda layout=layout: [
                    self.put(f"{self.type(t)} v_{f};") for f, t in layout], "}" + packed + ";")  # fmt: skip
            elif ty.name in self.p.enums:
                self.put(f"enum class {name} : std::uint32_t {{ {', '.join('v_' + v for v in layout)} }};")
            else:
                members = [f"{self.type(t) if t else 'std::uint8_t'} v_{v};" for v, t in layout.items()]

                def body(members=members, plain=self.trivial(ty)):
                    self.put("std::uint32_t tag;")
                    if plain:  # One active scalar payload: the 0.6 C layout.
                        self.nest("union {", lambda: [self.put(m) for m in members], "} payload;")
                    else:  # Owners cannot share storage; inactive payloads stay zero.
                        self.nest("struct {", lambda: [self.put(m) for m in members], "} payload;")

                self.nest(f"struct {name} {{", body, "};")

    # Expressions -------------------------------------------------------------------------------

    def literal(self, n: int, ty: Type) -> str:
        return f"static_cast<{self.type(ty)}>({n}{'ULL' if n < 2**64 else ''})"

    def extent(self, ty: Type, base: str = "") -> str:
        if ty.extent.isdigit():
            return ty.extent
        return f"{base}.size()" if ty.extent.startswith("len(") else "v_" + ty.extent

    def pointer(self, e: Expr) -> tuple[str, str]:
        """(data pointer, element count) of anything indexable."""
        ty, text = e.ty, self.expr(e)
        if e.tag == "str":
            return text, str(len(e.val.encode("latin-1", "replace")))
        if is_view(ty):
            return text, self.extent(ty)
        return text + ".data()", (str(ty.args[1]) if ty.name == "Array" else text + ".size()")

    def expr(self, e: Expr) -> str:
        return getattr(self, "e_" + e.tag)(e)

    def e_int(self, e: Expr) -> str:
        return self.literal(int(e.val), e.ty)

    def e_float(self, e: Expr) -> str:
        return e.val + ("f" if e.ty.name == "f32" else "")

    def e_bool(self, e: Expr) -> str:
        return e.val

    def e_str(self, e: Expr) -> str:
        body = "".join(chr(b) if 32 <= b < 127 and chr(b) not in '"\\?' else f"\\{b:03o}"
                       for b in e.val.encode("latin-1", "replace"))  # fmt: skip
        return f'reinterpret_cast<const std::uint8_t*>("{body}")'

    def e_name(self, e: Expr) -> str:
        if isinstance(e.ref, Expr):
            return self.expr(e.ref)
        if isinstance(e.ref, int):
            return self.literal(e.ref, e.ty)
        name = "v_" + mangle(e.val)
        return f"std::move({name})" if e.ref == "move" else name

    def e_index(self, e: Expr) -> str:
        data, count = self.pointer(e.args[0])
        return f"cr::at({data}, {self.expr(e.args[1])}, {count})"

    def e_slice(self, e: Expr) -> str:
        data, count = self.pointer(e.args[0])
        want = self.expr(e.ref) if isinstance(e.ref, Expr) else str(e.ref)
        self.need("cairn_owners.hpp")
        return f"cr::part({data}, {self.expr(e.args[1])}, {self.expr(e.args[2])}, {count}, {want})"

    def e_field(self, e: Expr) -> str:
        if isinstance(e.ref, tuple):
            return self.variant(e, [])
        return f"({self.expr(e.args[0])}).v_{e.val}"

    def variant(self, e: Expr, args: list[Expr]) -> str:
        name, index = self.type(e.ty), e.ref[1]
        if e.ty.name in self.p.enums:
            return f"{name}::v_{e.val.rsplit('.', 1)[-1]}"
        value = self.expr(args[0]) if args else "0"
        return f"{name}{{{index}, {{.v_{e.val.rsplit('.', 1)[-1]} = {value}}}}}"

    def e_unary(self, e: Expr) -> str:
        a, ty = self.expr(e.args[0]), e.ty
        if e.val == "!":
            return f"(!{a})"
        if e.val == "~":
            return f"static_cast<{self.type(ty)}>(~{a})"
        return f"(-{a})" if ty.name in FLOAT else f"cr::sub<{self.type(ty)}>(0, {a})"

    def e_binary(self, e: Expr) -> str:
        a, b, op, ty = self.expr(e.args[0]), self.expr(e.args[1]), e.val, e.ty
        if op in {"&", "|", "^"}:
            return f"static_cast<{self.type(ty)}>({a} {op} {b})"
        if op in COMPARISONS or op in {"&&", "||"} or ty.name in FLOAT:
            return f"({a} {op} {b})"
        return f"cr::{CHECKED[op]}<{self.type(ty)}>({a}, {b})"

    def e_call(self, e: Expr) -> str:
        if isinstance(e.ref, Function):
            return self.invoke(e, e.ref)
        kind = e.ref[0]
        if kind == "variant":
            return self.variant(e, e.args)
        if kind == "record":
            return self.type(e.ty) + "{" + ", ".join(self.expr(a) for a in e.args) + "}"
        n, args, ty = e.val, e.args, self.type(e.ty)
        if n == "len":
            return self.lenof(args[0])
        if n in NUMERIC:
            integral = args[0].ty.name in INT and n in INT
            return f"{'cr::convert' if integral else 'static_cast'}<{ty}>({self.expr(args[0])})"
        texts = [self.expr(a) for a in args]
        if n in WRAPPING:
            return f"cr::{n}<{ty}>({texts[0]}, {texts[1]})"
        if n in {"min", "max"}:
            return f"std::{n}({texts[0]}, {texts[1]})"
        if n == "take":
            return f"std::exchange({texts[0]}, {{}})"
        if n == "swap":
            return f"std::swap({texts[0]}, {texts[1]})"
        return f"{ty}({texts[0]})" if n == "Buf" else f"{ty}{{}}"

    def lenof(self, a: Expr) -> str:
        count = self.pointer(a)[1]
        return f"static_cast<std::size_t>({count}ULL)" if count.isdigit() else count

    def invoke(self, e: Expr, f: Function) -> str:
        args = []
        for a, (_, declared) in zip(e.args, f.params, strict=True):
            args.append(self.pointer(a)[0] if declared.extent and a.tag != "slice" else self.expr(a))
        return f"cf_{mangle(f.name)}({', '.join(args)})"

    # Functions and statements ------------------------------------------------------------------

    def signature(self, f: Function) -> str:
        ps = ", ".join(f"{self.type(t)} v_{n}" for n, t in f.params)
        exported = all(self.trivial(t.value) and t.name != "fn" for t in [f.ret, *(t for _, t in f.params)])
        head = f"{'extern "C" ' if exported or f.extern else ''}{self.type(f.ret)} cf_{mangle(f.name)}({ps}) noexcept"
        return head + (f' __asm__("{f.name.rsplit(".", 1)[-1]}")' if f.extern else "")

    def emit(self) -> str:
        self.layouts()
        for f in self.p.functions:
            self.put(self.signature(f) + ";")
        for f in self.p.functions:
            if not f.extern:
                self.put()
                self.nest(self.signature(f) + " {", lambda f=f: self.function(f))
        head = ["// Generated by " + VERSION + ". Do not edit; edit the CAIRN source."]
        head += [f'#include "{header}"' for header in self.headers]
        return "\n".join(head + self.lines) + "\n"

    def function(self, f: Function):
        arrays = [(n, t) for n, t in f.params if is_view(t)]
        for n, t in arrays:
            self.put(f"cr::view(v_{n}, {self.extent(t)});")
        for i, (n, t) in enumerate(arrays):
            for m, u in arrays[i + 1 :]:
                if "rw" in (t.mode, u.mode):
                    self.put(f"cr::disjoint(v_{n}, {self.extent(t)}, v_{m}, {self.extent(u)});")
        for n, t in f.params:
            if t.mode == "value" and isinstance(self.c.layouts.get(t), dict):
                tag = f"static_cast<std::uint32_t>(v_{n})" if t.name in self.p.enums else f"v_{n}.tag"
                self.put(f"if({tag} >= {len(self.c.layouts[t])}) cr::trap();")
        self.block(f.body)

    def block(self, ss: list[Stmt]):
        for s in ss:
            getattr(self, "s_" + s.tag)(s, [self.expr(e) for e in s.exprs] if s.tag not in {"compact"} else [])

    def s_buffer(self, s: Stmt, es: list[str]):
        owner, _ = self.fresh("cr_owner_")
        ty = self.type(s.ty)
        if s.tag == "buffer":
            self.put(f"cr::Buffer<{ty}> {owner}({es[0]});")
        else:
            self.put(f"std::array<{ty}, {s.exprs[0].val}> {owner}{{}};")
        self.put(f"{ty}* const v_{s.name} = {owner}.data();")

    s_stack = s_buffer

    def s_let(self, s: Stmt, es: list[str]):
        if s.ty.mode != "value":  # A local name for static text: a constant pointer to constant bytes.
            self.put(f"{self.type(s.ty)} const v_{s.name} = {es[0]};")
        else:  # An owner stays non-const so that it can be moved from later.
            const = "const " if s.tag == "let" and self.trivial(s.ty) else ""
            self.put(f"{const}{self.type(s.ty)} v_{s.name} = {es[0]};")

    s_reg = s_let

    def s_compact(self, s: Stmt, _: list[str]):
        out, hi, pred, value = (self.expr(e) for e in s.exprs)
        used, i = "v_" + s.name, "v_" + s.binder

        def selected():
            # The only unchecked store: induction gives used <= i < n (Lean: store_index_lt_capacity).
            self.put(f"{out}[{used}] = {value};")
            self.put(f"++{used};")

        self.put(f"std::size_t {used} = 0;")
        self.nest(f"for (std::size_t {i}=0; {i}<{hi}; ++{i}) {{", lambda: self.nest(f"if ({pred}) {{", selected))

    def s_assign(self, s: Stmt, es: list[str]):
        self.put(f"{es[0]} = {es[1]};")

    def s_break(self, s: Stmt, _: list[str]):
        self.put(f"goto cr_{s.tag}_{self.loops[-1]};")

    s_continue = s_break

    def s_return(self, s: Stmt, es: list[str]):
        local = s.exprs and s.exprs[0].ref == "move"  # C++ already moves a returned local.
        self.put("return" + (" v_" + mangle(s.exprs[0].val) if local else " " + es[0] if es else "") + ";")

    def s_expr(self, s: Stmt, es: list[str]):
        self.put(es[0] + ";")

    def s_block(self, s: Stmt, _: list[str]):
        self.nest("{", lambda: self.block(s.body))

    s_unsafe = s_block

    def s_defer(self, s: Stmt, _: list[str]):
        guard, _ = self.fresh("cr_defer_")
        self.need("cairn_owners.hpp")
        self.nest(f"const cr::Defer {guard}{{[&]() noexcept {{", lambda: self.block(s.body), "}};")

    def s_match(self, s: Stmt, es: list[str]):
        temp, _ = self.fresh("cr_match_")
        ty = s.exprs[0].ty
        layout, tagged, plain = self.c.layouts[ty], ty.name not in self.p.enums, self.trivial(ty)

        def arms():
            for arm, variant in zip(s.arms, s.ref, strict=True):

                def body(arm=arm, variant=variant):
                    if arm.binder:
                        payload = f"{temp}.payload.v_{variant}"
                        const, value = ("const ", payload) if plain else ("", f"std::move({payload})")
                        self.put(f"{const}{self.type(layout[variant])} v_{arm.binder} = {value};")
                    self.block(arm.body)
                    self.put("break;")

                self.nest(f"case {list(layout).index(variant)}: {{", body)
            self.put("default: cr::trap();")

        def whole():
            self.put(f"{'const ' if plain else ''}auto {temp} = {es[0]};")
            selector = temp + ".tag" if tagged else f"static_cast<std::uint32_t>({temp})"
            self.nest(f"switch ({selector}) {{", arms)

        self.nest("{", whole)

    def controls(self, ss: list[Stmt]) -> set[str]:
        found = set()
        for s in ss:
            if s.tag in {"break", "continue"}:
                found.add(s.tag)
            if s.tag not in {"for", "while"}:
                found |= self.controls(s.body) | self.controls(s.other)
                for arm in s.arms:
                    found |= self.controls(arm.body)
        return found

    def loop(self, head: str, s: Stmt, index: int):
        """A switch must not intercept break/continue, so loop control uses compiler-owned labels."""
        controls = self.controls(s.body)

        def body():
            self.loops.append(index)
            if controls:
                self.nest("{", lambda: self.block(s.body))
                if "continue" in controls:
                    self.put(f"cr_continue_{index}: ;")
            else:
                self.block(s.body)
            self.loops.pop()

        self.nest(head, body)
        if "break" in controls:
            self.put(f"cr_break_{index}: ;")

    def s_while(self, s: Stmt, es: list[str]):
        self.loop(f"while ({bare(es[0])}) {{", s, self.fresh("")[1])

    def s_if(self, s: Stmt, es: list[str]):
        self.put(f"if ({bare(es[0])}) {{")
        self.ind += 1
        self.block(s.body)
        self.ind -= 1
        if s.other:
            self.nest("} else {", lambda: self.block(s.other))
        else:
            self.put("}")

    def s_for(self, s: Stmt, es: list[str]):
        _, index = self.fresh("")
        begin, limit, n = f"cr_begin_{index}", f"cr_limit_{index}", "v_" + s.name

        def whole():
            # Source order is lower bound, upper bound, then iteration.
            self.put(f"const std::size_t {begin} = {es[0]};")
            self.put(f"const std::size_t {limit} = {es[1]};")
            self.loop(f"for (std::size_t {n} = {begin}; {n} < {limit}; ++{n}) {{", s, index)

        self.nest("{", whole)

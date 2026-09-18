"""Inspectable guarded C++20 emission from the typed tree. The backend is not verified."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .builtins import SHARED, TABLE, WRAPPING
from .checking import COMPARISONS, Checker
from .syntax import CPP, FLOAT, Expr, Function, Program, Stmt, Type, fail, is_view
from .version import VERSION

RUNTIME_FILES = {
    p.name: p.read_text(encoding="utf-8") for p in sorted((Path(__file__).parent / "runtime").glob("*.hpp"))
}
RUNTIME = RUNTIME_FILES["cairn_runtime.hpp"]
CHECKED = {"+": "add", "-": "sub", "*": "mul", "/": "divide", "%": "remainder"}


def mangle(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")


def local(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def bare(condition: str) -> str:
    """Drop one redundant outer pair of parentheses, only when the first really closes at the end."""
    depth = 0
    for i, c in enumerate(condition):
        depth += (c == "(") - (c == ")")
        if depth == 0:
            return condition[1:-1] if i == len(condition) - 1 and condition.startswith("(") else condition
    return condition


class Emitter:
    def __init__(self, p: Program, checker: Checker | None = None, origin: Any = "", roots: tuple[str, ...] = ()):
        self.p, self.ind, self.counter = p, 0, 0
        self.lines: list[str] = []
        # A source name, or a function from a line to (file, line), turns on #line directives.
        self.origin = (lambda line: (origin, line)) if isinstance(origin, str) and origin else origin
        self.loops: list[int] = []
        self.headers = ["cairn_runtime.hpp"]
        self.roots = roots  # An executable emits only what its entry points reach.
        self.dynamic: dict[str, None] = {}  # Traits used behind dyn, in first-use order.
        self.vtables: dict[str, str] = {}
        self.tables: dict[str, tuple[str, Type]] = {}  # Which (trait, type) owns each vtable symbol.
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
            shape = f"{self.type(t.args[-1])}({', '.join(self.type(a) for a in t.args[:-1])})"
            self.need("cairn_owners.hpp")
            if t.mode != "value":  # A borrowed callable: two words, no allocation, cannot escape.
                return f"cr::Fn<{shape}>"
            base = f"cr::FnPtr<{shape}>"
        elif t.name == "dyn":  # { object, vtable }: two words passed by value, whatever the borrow mode.
            self.dynamic.setdefault(t.args[0].name, None)
            return "cd_" + mangle(t.args[0].name)
        elif t.name == "Dyn":  # The owned form: the same two words plus how to release the object.
            self.dynamic.setdefault(t.args[0].name, None)
            self.need("cairn_owners.hpp")
            base = f"cr::Dyn<cd_{mangle(t.args[0].name)}>"
        elif t.name == "Buf":
            self.need("cairn_owners.hpp")
            base = f"cr::Buf<{self.type(t.args[0])}>"
        elif t.name in SHARED:  # Interior mutability: a ro borrow of shared state is still a plain reference.
            self.need("cairn_parallel.hpp")
            base = f"cr::par::{SHARED[t.name]}<{self.type(t.args[0])}>"
            return base if t.mode == "value" else base + "&"
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
                zero = "" if self.trivial(ty) else "{}"  # Side-by-side payloads each start as their zero.
                members = [f"{self.type(t) if t else 'std::uint8_t'} v_{v}{zero};" for v, t in layout.items()]

                # One active scalar payload keeps the 0.6 C union; owners cannot share storage, so a sum
                # that carries one stores its payloads side by side and the inactive ones stay zero.
                storage = "union {" if self.trivial(ty) else "struct {"

                def body(members=members, storage=storage):
                    self.put("std::uint32_t tag;")
                    self.nest(storage, lambda: [self.put(m) for m in members], "} payload;")

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
        if e.tag == "slice":
            return text, self.span(e)
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

    @staticmethod
    def quoted(text: str) -> str:
        """A C++ string literal that cannot end early: everything unusual is an octal escape."""
        data = text.encode("latin-1", "replace")
        return '"' + "".join(chr(b) if 32 <= b < 127 and chr(b) not in '"\\?' else f"\\{b:03o}" for b in data) + '"'

    def e_str(self, e: Expr) -> str:
        return f"reinterpret_cast<const std::uint8_t*>({self.quoted(e.val)})"

    def e_name(self, e: Expr) -> str:
        if isinstance(e.ref, Expr):
            return self.expr(e.ref)
        if isinstance(e.ref, int):
            return self.literal(e.ref, e.ty)
        name = "v_" + e.val
        return f"std::move({name})" if e.ref == "move" else name

    def e_index(self, e: Expr) -> str:
        data, count = self.pointer(e.args[0])
        return f"cr::at({data}, {self.expr(e.args[1])}, {count})"

    def span(self, e: Expr) -> str:
        return f"({self.expr(e.args[2])} - {self.expr(e.args[1])})"

    def e_slice(self, e: Expr) -> str:
        data, count = self.pointer(e.args[0])
        want = self.expr(e.ref) if isinstance(e.ref, Expr) else str(e.ref or self.span(e))
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

    def e_lambda(self, e: Expr) -> str:
        f, start = e.ref, len(self.lines)
        self.ind += 1
        self.block(f.body)
        self.ind -= 1
        text, self.lines = self.lines[start:], self.lines[:start]
        ps = ", ".join(f"{self.type(t)} v_{n}" for n, t in f.params)
        return "\n".join([f"[&]({ps}) noexcept -> {self.type(f.ret)} {{", *text, "  " * self.ind + "}"])

    def e_function(self, e: Expr) -> str:
        return "cf_" + mangle(e.ref.name)

    def e_spawn(self, e: Expr) -> str:
        """Arguments are evaluated now and carried by value, so the task never reads the spawner's locals."""
        call, captures, passed = e.args[0], [], []
        for k, (a, (_, want)) in enumerate(zip(call.args, call.ref.params, strict=True)):
            single = want.mode != "value" and not want.extent
            value = (
                self.expr(a)
                if want.mode == "value" or a.tag == "slice"
                else self.pointer(a)[0]
                if want.extent
                else "&" + self.expr(a)
            )
            captures.append(f"a{k} = {value}")
            passed.append(f"*a{k}" if single else f"a{k}" if self.trivial(want) else f"std::move(a{k})")
        body = f"return cf_{mangle(call.ref.name)}({', '.join(passed)});"
        return f"{self.type(e.ty)}::spawn([{', '.join(captures)}]() mutable noexcept {{ {body} }})"

    def e_coerce(self, e: Expr) -> str:
        """A fat reference: the object's address and the static vtable of its implementation."""
        if e.ref is None:  # An owned dynamic value already carries both words.
            return f"{self.expr(e.args[0])}.view()"
        table = self.vtable(e.ty.args[0].name, e.args[0].ty.value, e.ref)
        return f"cd_{mangle(e.ty.args[0].name)}{{const_cast<void*>(static_cast<const void*>(&({self.expr(e.args[0])}))), &{table}}}"

    def vtable(self, trait: str, ty: Type, members: list[Function]) -> str:
        concrete, table = self.type(ty), f"cv_{mangle(trait)}_{mangle(ty.display())}"
        if table not in self.vtables:
            thunks = []
            for member, f in zip(self.p.traits[trait], members, strict=True):  # One thunk per trait member.
                at = next(i for i, (_, t) in enumerate(member.params) if t.name == "Self")
                ps = "".join(f", {self.type(t)} v_{n}" for i, (n, t) in enumerate(f.params) if i != at)
                call = ", ".join(
                    f"*static_cast<{concrete}*>(self)" if i == at else f"std::move(v_{n})"
                    for i, (n, _) in enumerate(f.params)
                )
                thunks.append(
                    f"[](void* self{ps}) noexcept -> {self.type(f.ret)} {{ return cf_{mangle(f.name)}({call}); }}"
                )
            self.vtables[table] = f"static const cdt_{mangle(trait)} {table}{{{', '.join(thunks)}}};"
        if self.tables.setdefault(table, (trait, ty)) != (trait, ty):
            fail("E-MANGLE", f"{trait} for {ty.display()} and {self.tables[table][0]} for "
                 f"{self.tables[table][1].display()} would share the C symbol {table}; rename one.")  # fmt: skip
        return table

    def e_try(self, e: Expr) -> str:
        temp, _ = self.fresh("cr_try_")
        ok, err, target, carrier = e.ref
        ret, move = self.type(target), (lambda x: x) if self.trivial(e.args[0].ty) else "std::move({})".format
        carried = move(f"{temp}.payload.v_{err}") if self.c.layouts[e.args[0].ty][err] else "0"
        failure = f"if ({temp}.tag != 0) return {ret}{{1, {{.v_{carrier} = {carried}}}}};"
        value = move(f"{temp}.payload.v_{ok}") + ";" if e.ty.name != "void" else ""
        return f"({{ auto {temp} = {self.expr(e.args[0])}; {failure} {value} }})"

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
        if kind == "dispatch":  # receiver.vt->member(receiver.self, the other arguments...)
            receiver = self.expr(e.args[e.ref[3]]) + (".view()" if e.args[e.ref[3]].ty.name == "Dyn" else "")
            rest = [self.expr(a) for i, a in enumerate(e.args) if i != e.ref[3]]
            return f"{receiver}.vt->m{e.ref[2]}({', '.join([receiver + '.self', *rest])})"
        if kind == "shared":  # receiver.op(values..., memory orders...)
            texts = [self.expr(a) for a in e.args]
            orders = {i for i, a in enumerate(e.args) if a.ty.name == "Order"}
            texts = [f"static_cast<cr::par::Order>({t})" if i in orders else t for i, t in enumerate(texts)]
            return f"{texts[0]}.{e.val}({', '.join(texts[1:])})"
        if kind == "indirect":  # A zeroed fn value is a valid value; calling it is a guard failure.
            callee = f"v_{e.val}" if e.ref[1].mode != "value" else f"cr::callable(v_{e.val})"
            return f"{callee}({', '.join(self.expr(a) for a in e.args)})"
        if kind == "variant":
            return self.variant(e, e.args)
        if kind == "record":
            return self.type(e.ty) + "{" + ", ".join(self.expr(a) for a in e.args) + "}"
        return TABLE[e.val][1](self, e)

    def invoke(self, e: Expr, f: Function) -> str:
        args = []
        for a, (_, declared) in zip(e.args, f.params, strict=True):
            args.append(self.pointer(a)[0] if declared.extent and a.tag != "slice" else self.expr(a))
        return f"cf_{mangle(f.name)}({', '.join(args)})"

    # Functions and statements ------------------------------------------------------------------

    def signature(self, f: Function) -> str:
        ps = ", ".join(f"{self.type(t)} v_{n}" for n, t in f.params)
        exported = all(self.trivial(t.value) and t.name != "fn" for t in [f.ret, *(t for _, t in f.params)])
        linkage = "inline " if f.kernel else 'extern "C" ' if exported or f.extern else ""
        device = "CR_HD " if f.name in self.c.device_functions else ""
        symbol = f' __asm__("{f.symbol or local(f.name)}")' if f.extern else ""  # Whatever header declares it.
        return f"{linkage}{device}{self.type(f.ret)} cf_{mangle(f.name)}({ps}) noexcept{symbol}"

    def interfaces(self) -> tuple[list[str], list[str]]:
        """For each trait used behind dyn: the two-word reference (declared before any layout that
        owns one) and its table of member thunks (defined after the layouts its members mention)."""
        references, tables = [], []
        for trait in self.dynamic:
            members = []
            for i, m in enumerate(self.p.traits[trait]):
                with self.c.within(self.p.modules.get(trait, ""), {"Self": Type("dyn", args=(Type(trait),))}):
                    rest = [self.type(self.c.resolve(t)) for _, t in m.params if t.name != "Self"]
                    members.append(f"{self.type(self.c.resolve(m.ret))} (*m{i})({', '.join(['void*', *rest])});")
            name, table = "cd_" + mangle(trait), "cdt_" + mangle(trait)
            references += [f"struct {table};", f"struct {name} {{ void* self; const {table}* vt; }};"]
            tables.append(f"struct {table} {{ {' '.join(members)} }};")
        return references, tables

    def emit(self) -> str:
        symbols: dict[str, str] = {}
        for name in [*(f.name for f in self.p.functions), *(t.display() for t in self.c.layouts), *self.p.traits]:
            if symbols.setdefault(mangle(name), name) != name:
                fail(
                    "E-MANGLE",
                    f"{name} and {symbols[mangle(name)]} would share the C symbol {mangle(name)}; rename one.",
                )
        self.layouts()
        types, self.lines = self.lines, []
        reached, todo = set(), list(self.roots)
        while todo:
            name = todo.pop()
            if name not in reached:
                reached.add(name)
                todo += self.c.calls.get(name, ())
        functions = [f for f in self.p.functions if not self.roots or f.name in reached]
        for f in functions:
            if not f.extern:
                self.put()
                self.nest(self.signature(f) + " {", lambda f=f: self.function(f))
        head = ["// Generated by " + VERSION + ". Do not edit; edit the CAIRN source."]
        head += [f'#include "{header}"' for header in self.headers]
        prototypes = [self.signature(f) + ";" for f in functions]
        references, tables = self.interfaces()
        parts = [*head, *references, *types, *tables, *prototypes, *self.vtables.values(), *self.lines]
        return "\n".join(parts) + "\n"

    def function(self, f: Function):
        self.f = f
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
            if self.origin and s.line:
                self.put('#line {1} "{0}"'.format(*self.origin(s.line)))
            getattr(self, "s_" + s.tag)(s, [self.expr(e) for e in s.exprs] if s.tag != "compact" else [])

    def s_buffer(self, s: Stmt, es: list[str]):
        owner, _ = self.fresh("cr_owner_")
        ty = self.type(s.ty)
        if s.tag == "buffer" and s.ref != "host":
            self.need("cairn_gpu.hpp")
            self.put(
                f"cr::gpu::{ {'device': 'Buffer', 'pinned': 'Pinned', 'unified': 'Unified'}[s.ref] }<{ty}> {owner}({es[0]});"
            )
        elif s.tag == "buffer":  # Scalars keep the 0.6 owner; any other element type needs a movable zero.
            scalar = s.ty.name in CPP
            self.need("cairn_runtime.hpp" if scalar else "cairn_owners.hpp")
            self.put(f"cr::{'Buffer' if scalar else 'Buf'}<{ty}> {owner}({es[0]});")
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
        if s.ref == "device":
            keep = self.lane(s, lambda: self.put(f"return {pred};"))
            project = self.lane(s, lambda: self.put(f"return {value};"))
            return self.put(f"const std::size_t {used} = cr::gpu::compact({out}, {hi}, {keep}, {project});")

        def selected():
            # The only unchecked store: induction gives used <= i < n (Lean: store_index_lt_capacity).
            self.put(f"{out}[{used}] = {value};")
            self.put(f"++{used};")

        self.put(f"std::size_t {used} = 0;")
        self.nest(f"for (std::size_t {i}=0; {i}<{hi}; ++{i}) {{", lambda: self.nest(f"if ({pred}) {{", selected))

    def lane(self, s: Stmt, body) -> str:
        """The lambda every lane runs; its body is identical for host threads and device lanes."""
        device = s.ref == "device"  # CUDA wants by-value capture and the annotation before the parameters.
        self.need("cairn_gpu.hpp" if device else "cairn_parallel.hpp")
        start = len(self.lines)
        self.ind += 1
        body()
        self.ind -= 1
        text, self.lines = self.lines[start:], self.lines[:start]
        head = (
            f"{'[=] CR_DEVICE' if device else '[&]'}(std::size_t v_{s.binder or s.name}){'' if device else ' noexcept'}"
        )
        return "\n".join([head + " {", *text, "  " * self.ind + "}"])

    def s_parallel(self, s: Stmt, es: list[str]):
        entry = "cr::gpu::launch" if s.ref == "device" else "cr::par::run"
        self.put(f"{entry}({es[0]}, {self.lane(s, lambda: self.block(s.body))});")

    def s_reduce(self, s: Stmt, es: list[str]):
        ty, op = self.type(s.ty), s.op
        combine = (
            f"cr::{op}<{ty}>(a, b)"
            if op in WRAPPING
            else f"(a {'<' if op == 'min' else '>'} b ? a : b)"
            if op in {"min", "max"}
            else f"static_cast<{ty}>(a {op} b)"
        )
        limits = f"std::numeric_limits<{ty}>"
        identity = {
            "*": "1",
            "mul_wrap": "1",
            "&": f"{limits}::max()",
            "min": f"{limits}::max()",
            "max": f"{limits}::lowest()",
        }.get(op, "0")
        if s.ref == "device":
            value = self.lane(s, lambda: self.put(f"return {self.expr(s.exprs[1])};"))
            fold = f"[] CR_DEVICE({ty} a, {ty} b) {{ return {combine}; }}"
            self.put(
                f"const {ty} v_{s.name} = cr::gpu::reduce<{ty}>({es[0]}, static_cast<{ty}>({identity}), {fold}, {value});"
            )
        else:  # On the host a reduction is an ordinary in-order fold: no threads, no hidden cost.
            self.put(f"{ty} v_{s.name} = static_cast<{ty}>({identity});")
            self.put(f"for (std::size_t v_{s.binder} = 0; v_{s.binder} < {es[0]}; ++v_{s.binder}) {{")
            self.put(f"  const {ty} a = v_{s.name}, b = {es[1]};")
            self.put(f"  v_{s.name} = {combine};")
            self.put("}")

    def s_assign(self, s: Stmt, es: list[str]):
        self.put(f"{es[0]} = {es[1]};")

    def s_break(self, s: Stmt, _: list[str]):
        self.put(f"goto cr_{s.tag}_{self.loops[-1]};")

    s_continue = s_break

    def s_return(self, s: Stmt, es: list[str]):
        local = s.exprs and s.exprs[0].ref == "move"  # C++ already moves a returned local.
        self.put("return" + (" v_" + s.exprs[0].val if local else " " + es[0] if es else "") + ";")

    def s_expr(self, s: Stmt, es: list[str]):
        self.put(es[0] + ";")

    def s_block(self, s: Stmt, _: list[str]):
        self.nest("{", lambda: self.block(s.body))

    s_unsafe = s_block

    def s_defer(self, s: Stmt, _: list[str]):
        guard = self.fresh("cr_defer_")[0]
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

"""The C header of a library build: every checked entry a C or C++ program may call, the layouts it passes, and
what each entry does and may do.

A declaration here is written from the same rule that gives the entry C linkage (`Emitter.exported`), and
every layout is computed here and checked twice: by `_Static_assert` in the header, against the C compiler
that includes it, and by `static_assert` appended to the library's own C++, against the emitter's structs.
A library built with its header cannot link if the two ever disagree. What cannot cross the boundary is
listed at the end with the reason, never declared.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from typing import Any

from ..version import VERSION
from .cairnc import compile_program
from .codegen import Emitter, mangle
from .lexing import comment_above
from .tree import BITS, STORAGE, Function, Type, is_view

SCALARS = {"bool": ("bool", 1), "f32": ("float", 4), "f64": ("double", 8), "void": ("void", 0)}
SCALARS |= {n: ("size_t" if n == "usize" else f"{'u' * (n[0] == 'u')}int{w}_t", w // 8) for n, w in BITS.items()}
SCALARS |= {n: (f"cairn_{n}", 1 + (sum(STORAGE[n][:2]) + 1 > 8)) for n in STORAGE}  # a bit pattern, one field
RESERVED = set(  # C and C++ keywords a CAIRN name may spell; such a name gets a trailing underscore
    "alignas alignof and asm auto bool break case catch char class const constexpr continue default delete do "
    "double else enum explicit export extern false float for friend goto if inline int long mutable namespace new "
    "noexcept not operator or private protected public register restrict return short signed sizeof static struct "
    "switch template this throw true try typedef typeid typename union unsigned using virtual void volatile while "
    "xor".split()
)
PREAMBLE = """\
Every function is the checked entry of a CAIRN function. Before the body runs, each view argument (a pointer
with a length) must be null only when empty, aligned for its element, and inside the address space, and a
view the function writes must overlap no other view argument. A failed check, and every guard of the body
(overflow, bounds, a narrowing that does not fit, an assert), calls abort(): nothing unwinds and nothing is
rolled back. A single borrow (T *) must point to live, initialized storage, which no entry can check. The
effect row after each declaration is the one the checker inferred; `trap` means the call may abort."""


@dataclass
class Layout:
    size: int
    align: int
    offsets: list[tuple[str, int]] = field(default_factory=list)  # (C++ member, offset), checked in the library


class Header:
    def __init__(self, source: str, name: str, mine: Any = None):
        self.p, self.c, self.receipt = compile_program(source)
        self.emitter = Emitter(self.p, self.c)
        self.source, self.name = source, name
        self.mine = mine or (lambda f: not f.name.startswith("std."))
        self.shapes: dict[Type, Layout] = {}
        self.types: list[str] = []  # C definitions, each after the ones it uses
        self.checks: list[str] = []  # static_assert lines for the library's own C++

    def reason(self, t: Type, parameter: bool = True) -> str:
        """Why a type cannot appear in a C declaration, or "" when it can; a member may be an Array."""
        v = t.value
        if v.name == "fn":
            return "a function value, whose calling convention C does not share"
        if v.name in {"dyn", "Dyn"}:
            return "a dyn reference, a pair of pointers into CAIRN's own dispatch tables"
        if not self.emitter.trivial(v):
            return f"{v.display()} is an owner or a linear value, and ownership does not cross the C boundary"
        if v.name == "Array" and t.mode == "value" and parameter:
            return "an Array passed by value, which C cannot pass; pass a view instead"
        return next(filter(None, (self.reason(part, False) for part in self.parts(v))), "")

    def parts(self, v: Type) -> list[Type]:
        layout = self.c.layouts.get(v)
        if v.name == "Array":
            return [v.args[0]]
        if isinstance(layout, list) and v.name not in self.p.enums:
            return [ft for _, ft in layout]
        if isinstance(layout, dict):
            return [pt for pt in layout.values() if pt]
        return []

    def ctype(self, t: Type) -> str:
        v = t.value
        if v.name in SCALARS:
            return SCALARS[v.name][0]
        self.shape(v)
        return "ct_" + mangle(v.display())

    def declarator(self, t: Type, name: str) -> str:
        """`T name`, or `T name[N]` for an inline Array member."""
        suffix = ""
        while t.name == "Array":
            suffix, t = f"[{t.args[1]}]" + suffix, t.args[0]
        return f"{self.ctype(t)} {name}{suffix}"

    def shape(self, v: Type) -> Layout:
        if v in self.shapes:
            return self.shapes[v]
        if v.name in SCALARS:
            size = SCALARS[v.name][1]
            return Layout(size, size)
        if v.name == "Array":
            inner = self.shape(v.args[0])
            return Layout(inner.size * int(v.args[1]), inner.align)
        name, layout = "ct_" + mangle(v.display()), self.c.layouts[v]
        attributes = self.p.attributes.get(v.name, set())
        packed = "packed" in attributes
        wanted = max([int(a[6:-1]) for a in attributes if a.startswith("align(")] or [1])
        if v.name in self.p.enums:
            shape = Layout(4, 4)
            constants = ", ".join(f"{name}_{variant} = {i}" for i, variant in enumerate(layout))
            self.types += [
                *self.told(v, "enum", "A tag no variant has makes an entry abort."),
                f"typedef uint32_t {name};",
                f"enum {{ {constants} }};",
            ]
        elif isinstance(layout, list):
            offset, widest, members, offsets = 0, 1, [], []
            for member, ft in layout:
                inner = self.shape(ft)
                step = 1 if packed else inner.align
                offset = -(-offset // step) * step
                offsets.append((f"v_{member}", offset))
                members.append(f"  {self.declarator(ft, cname(member))};")
                offset, widest = offset + inner.size, max(widest, step)
            align = max(widest, wanted)
            shape = Layout(-(-offset // align) * align or align, align, offsets)
            attribute = " __attribute__((packed))" * packed + f" __attribute__((aligned({wanted})))" * (wanted > 1)
            self.types += [
                *self.told(v, "struct"),
                f"typedef struct {name} {name};",
                f"struct{attribute} {name} {{",
                *members,
                "};",
            ]
        else:  # A sum with payloads: its tag, then one payload at a time in a union, as the emitter lays it out.
            variants = [(variant, self.shape(pt) if pt else Layout(1, 1), pt) for variant, pt in layout.items()]
            inside = max(m[1].align for m in variants)
            start = -(-4 // inside) * inside
            union = max(-(-m[1].size // inside) * inside for m in variants)
            align = max(4, inside)
            shape = Layout(-(-(start + union) // align) * align, align, [("tag", 0), ("payload", start)])
            fields = [
                f"    {self.declarator(pt, cname(v_)) if pt else 'uint8_t ' + cname(v_)};" for v_, _, pt in variants
            ]
            constants = ", ".join(f"{name}_{variant} = {i}" for i, variant in enumerate(layout))
            self.types += [*self.told(v, "enum", "A tag no variant has makes an entry abort."),
                           f"typedef struct {name} {name};", f"enum {{ {constants} }};", f"struct {name} {{",
                           "  uint32_t tag;", "  union {", *fields, "  } payload;", "};"]  # fmt: skip
        self.shapes[v] = shape
        self.types.append(f"CAIRN_LAYOUT(sizeof({name}) == {shape.size} && CAIRN_ALIGNOF({name}) == {shape.align});")
        self.checks.append(f"static_assert(sizeof({name}) == {shape.size} && alignof({name}) == {shape.align}, "
                           f'"{v.display()} is laid out as its C header says");')  # fmt: skip
        for member, offset in shape.offsets:
            c_member = cname(member[2:]) if member.startswith("v_") else member
            self.types.append(f"CAIRN_LAYOUT(offsetof({name}, {c_member}) == {offset});")
            self.checks.append(f'static_assert(offsetof({name}, {member}) == {offset}, "{v.display()}.{member}");')
        self.types.append("")
        return shape

    def told(self, v: Type, kind: str, *more: str) -> list[str]:
        """The comment written above a type's declaration, and what else a C reader must know of it."""
        word = re.escape(v.name.rsplit(".", 1)[-1])
        found = re.search(rf"^(?:pub )?(?:linear )?{kind} {word}\b", self.source, re.M)
        said = [*(comment_above(self.source, found.start()) if found else []), *more]
        return ["/*", *(" * " + line for text in said for line in wrap(text)), " */"] if said else []

    def refusal(self, f: Function) -> str:
        """Why a function of this library has no C declaration, or "" when it has one."""
        if f.kernel:
            return "a device kernel, launched only from CAIRN code"
        if f.owner:
            return "a trait member, reached through its trait"
        if not self.emitter.exported(f):
            return next(filter(None, (self.reason(t) for t in [f.ret, *(t for _, t in f.params)])), "no C linkage")
        return next(filter(None, (self.reason(t) for t in [f.ret, *(t for _, t in f.params)])), "")

    def declaration(self, f: Function) -> list[str]:
        params = []
        for n, t in f.params:
            pointer = "const " * (t.mode == "ro") + self.ctype(t) + " *" if t.mode != "value" else self.ctype(t) + " "
            params.append(pointer + cname(n))
        said = [*comment_above(self.source, f.start)] if f.start >= 0 else []
        said.append(f"CAIRN: {f.name}({', '.join(f'{n}:{shown(t)}' for n, t in f.params)})"
                    + (f" -> {shown(f.ret)}" if f.ret.name != "void" else ""))  # fmt: skip
        for n, t in f.params:
            if is_view(t):
                said.append(f"{cname(n)}: {t.extent} elements, {'read' if t.mode == 'ro' else 'read and written'}.")
            elif t.mode != "value":
                said.append(f"{cname(n)}: one {t.value.display()}, {'read' if t.mode == 'ro' else 'read and written'}.")
        said.append("Effects: " + (", ".join(self.receipt[f.name]["effects"]) or "none") + ".")
        lines = ["/*", *(" * " + line for text in said for line in wrap(text)), " */"]
        return [*lines, f"{self.ctype(f.ret)} cf_{mangle(f.name)}({', '.join(params) or 'void'});", ""]

    def render(self) -> tuple[str, str]:
        """The header, and the static_assert lines that hold the library's C++ to the layouts it states."""
        own = [f for f in self.p.functions if self.mine(f) and not f.extern and not f.test and not f.static]
        own = [f for f in own if f.name.rsplit(".", 1)[-1] != "main" and (f.public or not f.module)]
        declared, withheld = [], []
        for f in own:
            why = self.refusal(f)
            if why:
                withheld.append(f"{f.name}: {why}.")
            else:
                declared += self.declaration(f)
        guard = f"CAIRN_{mangle(self.name).upper()}_H"
        head = [f"/* Generated by {VERSION} from the CAIRN library {self.name}. Do not edit; edit the CAIRN source.",
                *(" * " + line for line in PREAMBLE.split("\n")), " */", f"#ifndef {guard}", f"#define {guard}",
                "#include <stdbool.h>", "#include <stddef.h>", "#include <stdint.h>", "#ifdef __cplusplus",
                "#define CAIRN_LAYOUT(e) static_assert(e, #e)", "#define CAIRN_ALIGNOF(t) alignof(t)",
                'extern "C" {', "#else", "#define CAIRN_LAYOUT(e) _Static_assert(e, #e)",
                "#define CAIRN_ALIGNOF(t) _Alignof(t)", "#endif", ""]  # fmt: skip
        storage = [f"typedef struct {{ {'uint16_t' if SCALARS[n][1] == 2 else 'uint8_t'} bits; }} cairn_{n};"
                   for n in STORAGE if f"cairn_{n}" in "\n".join([*self.types, *declared])]  # fmt: skip
        tail = ["/* Not declared, because it cannot cross the C boundary:", *(" * " + w for w in withheld), " */", ""]
        tail = tail if withheld else []
        text = [*head, *storage, *([""] if storage else []), *self.types, *declared, *tail]
        text += ["#ifdef __cplusplus", "}", "#endif", f"#endif /* {guard} */"]
        return "\n".join(text) + "\n", "\n".join(["// The layouts the C header states.", *self.checks]) + "\n"


def shown(t: Type) -> str:
    """A type as CAIRN writes it, leaving out the host placement every C pointer already has."""
    return t.display().replace("@host", "")


def cname(name: str) -> str:
    return name + "_" if name in RESERVED else name


def wrap(text: str) -> list[str]:
    return textwrap.wrap(text, 112) or [""]


def header(source: str, name: str, mine: Any = None) -> tuple[str, str]:
    """The C header of the library `name` built from `source`, and the checks its own C++ carries."""
    return Header(source, name, mine).render()

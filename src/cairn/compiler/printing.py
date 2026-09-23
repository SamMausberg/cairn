"""print, println, eprint, eprintln and format: typed and variadic, with what they cost in the row.

Every argument is one piece of text: an integer in decimal, a bool as `true` or `false`, a character literal as
its byte, an f32 or f64 in the shortest form that reads back to the same value, or bytes from a string, a u8
view, a part, a Buf or Array of u8, or a record that lends a u8 view. Every piece is computed, left to right,
before a byte is written (runtime/cairn_print.hpp), so a piece whose guard fails aborts with nothing written.
The four print forms write to standard output or standard error through one 4096-byte buffer on the stack and
allocate nothing; `format(out, ...)` appends to a growable byte record, a record whose `lends c[0..n]` names a
Buf[u8] and its length, and allocates at most once a call. A program's own function of the name wins (SOFT).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .syntax import copied
from .tree import BOOL, FLOAT, INT, SIGNED, STORAGE, VOID, Expr, Type, fail, is_view

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

STREAMS = {"print": (1, False), "println": (1, True), "eprint": (2, False), "eprintln": (2, True)}  # fd, newline
NAMES = {*STREAMS, "format"}
U8 = Type("u8")


def check_print(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    c.host_only(e, f"{e.val} writes to the process's streams")
    if targs:
        fail("E-ARITY", f"{e.val} takes no type arguments; each argument says what it prints.", e)
    borrows: list[tuple[str, str]] = []
    first = 0
    if e.val == "format":
        if not args:
            fail("E-ARITY", "format takes the byte record it appends to, then what to append.", e)
        target(c, args[0], borrows)
        first = 1
    else:
        c.effects |= {"io", "ffi:write"}
    kinds = [piece(c, args, k, borrows) for k in range(first, len(args))]
    c.disjoint(borrows, e)
    e.ref = ("builtin", kinds)
    return VOID


def target(c: Checker, a: Expr, borrows: list[tuple[str, str]]):
    """What format appends to: a named place whose record lends `c[0..n]`, c a Buf[u8] with no declared extent of
    its own and n a usize field, as std.vec.Vec[u8] does. Growing it replaces c and moves n, so both are written."""
    ty = c.place(a, write=True)
    shape = None if is_view(ty) else c.p.lends.get(ty.name)
    carrier = shape and Expr("field", shape[0], [copied(a)], a.line, a.col)
    if (
        not shape
        or shape[1] != "0"
        or shape[2].isdigit()
        or shape[0] in c.p.field_extents.get(ty.name, {})
        or c.expr(carrier, consume=False) != Type("Buf", args=(U8,))
    ):
        fail("E-FORMAT-TARGET", f"format appends to a growable byte record, a Vec[u8] or a record that lends "
             f"buf[0..len] of a Buf[u8]; {ty.display()} is not one.", a)  # fmt: skip
    written = c.lend(a, "rw", borrows)
    c.effects |= {"write:" + written} if written else set()
    c.guard("bounds")  # the record's own part, data[0..len], guarded as lending it is
    c.guard("allocation", "alloc", "free")


def piece(c: Checker, args: list[Expr], k: int, borrows: list[tuple[str, str]]) -> str:
    """The kind of one argument, which lowering renders: text, char, bool, signed, unsigned, f32 or f64."""
    a = args[k]
    if a.tag == "int" and a.char:  # 'c' written here prints its byte; a u8 held anywhere prints its number
        c.expr(a, U8)
        return "char"
    if a.tag == "unary" and a.val == "-" and a.args[0].tag in {"int", "float"}:  # nothing else says what -1 is
        c.expr(a, Type("i64") if a.args[0].tag == "int" else None)
        return "signed" if a.args[0].tag == "int" else "f64"
    ty = viewed = c.view_argument(a) if a.tag in {"str", "slice"} else c.peek(a)
    if not is_view(ty) and ty.name in c.p.lends:  # `print(line)`: the part the record lends, written out
        carrier, lo, hi = c.p.lends[ty.name]
        field = [Expr("field", name, [copied(a)], a.line, a.col) for name in (carrier, lo, hi)]
        bound = [Expr("int", b, [], a.line, a.col) if b.isdigit() else field[j + 1] for j, b in enumerate((lo, hi))]
        a = args[k] = Expr("slice", "", [field[0], *bound], a.line, a.col, start=a.start, end=a.end)
        viewed = c.view_argument(a)
    elif not is_view(ty) and ty.name in {"Buf", "Array"}:
        viewed = c.view_argument(a)
    if is_view(viewed):
        if viewed.name != "u8" or viewed.args:
            fail("E-PRINT-ARG", f"print writes the bytes of a u8 view; format the elements of {viewed.display()} "
                 "with std.fmt first, or print them one by one.", a)  # fmt: skip
        lent = c.lend(a, "ro", borrows, elements=True)
        c.effects |= {"read:" + lent} if lent else set()
        return "text"
    if ty.mode == "value" and ty.name in STORAGE:
        fail("E-PRINT-ARG", f"{ty.name} is a storage float: widen it with f32(x) to print it.", a)
    if ty.mode != "value" or ty.name not in {*INT, *FLOAT, BOOL.name}:
        fail("E-PRINT-ARG", f"print writes integers, bools, character literals, floats and bytes; format a "
             f"{ty.display()} with std.fmt first, or print its fields.", a)  # fmt: skip
    c.expr(a)
    return "bool" if ty == BOOL else ty.name if ty.name in FLOAT else "signed" if ty.name in SIGNED else "unsigned"


RENDER = {"char": "byte({})", "bool": "boolean({})", "f32": "real({})", "f64": "real({})",
          "signed": "integer(static_cast<std::int64_t>({}))", "unsigned": "integer(static_cast<std::uint64_t>({}))"}  # fmt: skip


def lower_print(g: Emitter, e: Expr) -> str:
    """One braced list of pieces: C++ evaluates a braced list left to right, so every argument is computed, in the
    order it was written, before the runtime writes the first byte."""
    g.need("cairn_print.hpp")
    shown = e.args[1:] if e.val == "format" else e.args
    pieces = []
    for a, kind in zip(shown, e.ref[1], strict=True):
        if kind == "text":
            data, count = g.pointer(a)
            pieces.append(f"cr::out::text({data}, {count})")
        else:
            pieces.append("cr::out::" + RENDER[kind].format(g.expr(a)))
    listed = "{" + ", ".join(pieces) + "}"
    if e.val == "format":
        record, (carrier, _, length) = g.expr(e.args[0]), g.p.lends[e.args[0].ty.name]
        return f"cr::out::append(({record}).v_{carrier}, ({record}).v_{length}, {listed})"
    fd, line = STREAMS[e.val]
    return f"cr::out::write({fd}, {str(line).lower()}, {listed})"

"""Every builtin in one place: how it types, what it costs, and how it lowers.

Each entry of TABLE pairs a rule `(checker, call, args, type_args, expected) -> Type` with a lowering
`(emitter, call) -> C++`. Names in SOFT arrived in 1.0 and yield to a program's own function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .syntax import FLOAT, HOST_VISIBLE, INT, NUMERIC, UNSIGNED, USIZE, VOID, Expr, Type, fail, is_view, root

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

WRAPPING = {"add_wrap", "sub_wrap", "mul_wrap", "shl_wrap", "shr"}
SOFT = {"take", "swap", "transfer", "mmio_read", "mmio_write", "asm", "wait"}
SHARED = {"Ticket": "Task", "Atomic": "Atomic", "Mutex": "Mutex"}  # CAIRN name -> cr::par class


def arity(e: Expr, args: list[Expr], count: int, message: str):
    if len(args) != count:
        fail("E-ARITY", message, e)


def explicit(c: Checker, e: Expr, name: str, targs: tuple, expected: Type | None, hint: str) -> Type:
    """The type a constructor builds: written as Name[...] or taken from the expected type."""
    ty = c.resolve(Type(name, args=targs), e) if targs else expected
    if ty is None or ty.name != name:
        fail("E-INFER", hint, e)
    return ty


def check_len(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    if len(args) != 1 or root(args[0]).tag != "name" or args[0].tag == "slice":
        fail("E-LEN", "len takes one direct borrowed view or local buffer.", e)
    ty = c.expr(args[0], consume=False)
    if not is_view(ty) and ty.name not in {"Buf", "Array"}:
        fail("E-LEN", "len requires an array view.", e)
    c.capture(c.where(args[0]), "ro")  # A length never changes under a lease, but a closure still reads the owner.
    return USIZE


def lower_len(g: Emitter, e: Expr) -> str:
    count = g.pointer(e.args[0])[1]
    return f"static_cast<std::size_t>({count}ULL)" if count.isdigit() else count


def check_convert(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    arity(e, args, 1, "Scalar conversion takes one argument.")
    src = c.expr(args[0])
    if src.mode != "value" or src.name not in NUMERIC:
        fail("E-CAST", "Conversion requires numeric scalar.", e)
    if e.val in INT:  # Narrowing, and float to integer (truncation toward zero), are range checked.
        c.guard("conversion")
    return Type(e.val)


def lower_convert(g: Emitter, e: Expr) -> str:
    guard = "static_cast" if e.val in FLOAT else "cr::convert" if e.args[0].ty.name in INT else "cr::truncate"
    return f"{guard}<{g.type(e.ty)}>({g.expr(e.args[0])})"


def check_binary(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """add_wrap sub_wrap mul_wrap shl_wrap shr min max: a literal adapts to its peer."""
    arity(e, args, 2, f"{e.val} takes two arguments.")
    a, b = args
    shift = e.val in {"shl_wrap", "shr"}
    if a.tag == "int" and b.tag != "int" and not shift:
        t = c.expr(b, expected)
        c.expr(a, t)
    else:
        t = c.expr(a, expected)
        c.expr(b, USIZE if shift else t)
    if e.val in WRAPPING:
        if t.mode != "value" or t.name not in UNSIGNED:
            fail("E-WRAP-TYPE", "Wrapping/bit shift operations require unsigned integers.", e)
        if shift:
            c.guard("shift")
    elif t.name not in INT or t.mode != "value":
        fail("E-MINMAX", "Bootstrap min/max are integer-only; floating NaN semantics must be explicit.", e)
    return t


def lower_binary(g: Emitter, e: Expr) -> str:
    a, b = (g.expr(x) for x in e.args)
    return f"cr::{e.val}<{g.type(e.ty)}>({a}, {b})" if e.val in WRAPPING else f"std::{e.val}({a}, {b})"


def check_machine(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """mmio_read mmio_write asm: target access is audited, never silently safe."""
    n = e.val
    if not c.unsafe_depth:
        fail("E-UNSAFE", f"{n} touches the machine directly; use it inside an unsafe block.", e)
    c.effect("asm" if n == "asm" else "mmio")
    if n == "asm":
        if len(args) != 1 or args[0].tag != "str":
            fail("E-ARITY", "asm takes one string literal of target instructions.", e)
        return VOID
    ty = c.resolve(targs[0], e) if len(targs) == 1 else expected
    if ty is None or ty.name not in UNSIGNED:
        fail("E-INFER", f"Write {n}[u8|u16|u32|u64] with the register width.", e)
    arity(e, args, 1 + (n == "mmio_write"), f"{n} takes an address" + (" and a value." if n == "mmio_write" else "."))
    c.expr(args[0], USIZE)
    if n == "mmio_write":
        c.expr(args[1], ty)
    e.ref = ("builtin", ty)
    return ty if n == "mmio_read" else VOID


def lower_machine(g: Emitter, e: Expr) -> str:
    if e.val == "asm":
        return f"__asm__({g.quoted(e.args[0].val)})"
    register = f"*reinterpret_cast<volatile {g.type(e.ref[1])}*>({g.expr(e.args[0])})"  # One access, exact width.
    return f"static_cast<{g.type(e.ty)}>({register})" if e.val == "mmio_read" else f"({register} = {g.expr(e.args[1])})"


def check_transfer(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """The only way elements cross a placement boundary; extents agree by identity, a part's by its guard."""
    arity(e, args, 2, "transfer takes a destination and a source view.")
    if c.lanes:
        fail("E-PARALLEL-NEST", "transfer moves a whole array; it cannot run inside a lane.", e)
    dst, src = (c.view_argument(a) for a in args)
    if not is_view(dst) or not is_view(src) or dst.mode != "rw" or root(args[0]).tag != "name":
        fail("E-WRITE-LEASE", "transfer needs an rw destination view and a source view.", e)
    extent = dst.extent if "part" in (dst.extent, src.extent) else src.extent
    c.expect(Type(src.name, "rw", extent, src.args, dst.place), dst, e)
    borrows: list[tuple[str, str]] = []
    written, read = c.lend(args[0], "rw", borrows), c.lend(args[1], "ro", borrows)
    c.disjoint(borrows, e)
    ends = ["h" if t.place in HOST_VISIBLE else "d" for t in (src, dst)]
    c.effects |= {f"transfer:{ends[0]}2{ends[1]}", "write:" + written} | ({"read:" + read} if read else set())
    return VOID


def lower_transfer(g: Emitter, e: Expr) -> str:
    sizes = [g.span(a) if a.tag == "slice" else g.pointer(a)[1] for a in e.args]
    for a, peer in zip(e.args, reversed(sizes), strict=True):  # Each part is guarded against its peer's length.
        a.ref = peer if a.tag == "slice" else a.ref
    count, dst, src = sizes[0], g.pointer(e.args[0])[0], g.pointer(e.args[1])[0]
    ends = ["h" if a.ty.place in HOST_VISIBLE else "d" for a in (e.args[1], e.args[0])]
    if ends == ["h", "h"]:
        return f"std::copy_n({src}, {count}, {dst})"
    g.need("cairn_gpu.hpp")
    return f"cr::gpu::copy({dst}, {src}, {count}, cr::gpu::Dir::{ends[0]}2{ends[1]})"


def check_wait(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """Completion is the only thing that returns a task's borrows."""
    arity(e, args, 1, "wait takes one ticket.")
    c.spawning = "<wait>"
    ticket = c.expr(args[0])
    c.spawning = ""
    if ticket.name != "Ticket" or args[0].tag != "name":
        fail("E-TYPE-MISMATCH", "wait takes the name of a ticket.", e)
    c.leases.pop(args[0].val, None)
    c.effect("join")
    return ticket.args[0]


def check_shared(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """Atomic[T](v) and Mutex[T](v) are declared in place and reached through ro borrows."""
    ty = explicit(c, e, e.val, targs, expected, f"Write {e.val}[T](initial).")
    if e.val == "Atomic" and ty.args[0].name not in INT | {"bool"}:
        fail("E-INFER", "An atomic holds an integer or bool.", e)
    arity(e, args, 1, f"{e.val} takes its initial value.")
    c.expr(args[0], ty.args[0])
    return ty


def check_exchange(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """take and swap are the only ways to move an owner out of a place."""
    take = e.val == "take"
    arity(e, args, 1 if take else 2, f"{e.val} takes {'one place' if take else 'two places'}.")
    types = [c.place(a, write=True) for a in args]
    if take and c.kind(types[0]) == "linear":
        fail("E-LINEAR-STORAGE", "take would leave a forged linear value behind; swap two places instead.", e)
    c.effects |= {"read:" + root(a).val for a in args}
    c.expect(types[-1], types[0], e)
    return types[0] if take else VOID


def lower_exchange(g: Emitter, e: Expr) -> str:
    places = [g.expr(a) for a in e.args]
    return f"std::exchange({places[0]}, {{}})" if e.val == "take" else f"std::swap({places[0]}, {places[1]})"


def check_dyn(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """Dyn[Trait](value) moves a value of any implementing type to the heap."""
    ty = explicit(c, e, "Dyn", targs, expected, "Write Dyn[Trait](value).")
    arity(e, args, 1, "Dyn takes the value it will own.")
    members = c.vtable(ty.args[0].name, c.expr(args[0]).value, e)
    c.effects |= {"alloc", "free"}
    c.guard("allocation")
    e.ref = ("builtin", members)
    return ty


def lower_dyn(g: Emitter, e: Expr) -> str:
    table = g.vtable(e.ty.args[0].name, e.args[0].ty.value, e.ref[1])
    return f"{g.type(e.ty)}::make({g.expr(e.args[0])}, &{table})"


def check_owner(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """Zero-initialized owners: Buf[T](n) on the heap, Array[T, N]() inline."""
    ty = explicit(c, e, e.val, targs, expected, f"Write {e.val}[...] with its type arguments.")
    heap = e.val == "Buf"
    arity(e, args, int(heap), f"{e.val} takes {'one capacity' if heap else 'no arguments'}.")
    c.effect("zero_init")
    if heap:
        c.expr(args[0], USIZE)
        c.effects |= {"alloc", "free"}
        c.guard("allocation")
    return ty


def lower_construct(g: Emitter, e: Expr) -> str:
    return f"{g.type(e.ty)}({g.expr(e.args[0])})" if e.args else f"{g.type(e.ty)}{{}}"


TABLE: dict[str, tuple[Any, Any]] = {
    "len": (check_len, lower_len),
    **dict.fromkeys(NUMERIC, (check_convert, lower_convert)),
    **dict.fromkeys(WRAPPING | {"min", "max"}, (check_binary, lower_binary)),
    **dict.fromkeys(("mmio_read", "mmio_write", "asm"), (check_machine, lower_machine)),
    "transfer": (check_transfer, lower_transfer),
    "wait": (check_wait, lambda g, e: f"{g.expr(e.args[0])}.wait()"),
    **dict.fromkeys(("Atomic", "Mutex"), (check_shared, lower_construct)),
    **dict.fromkeys(("take", "swap"), (check_exchange, lower_exchange)),
    "Dyn": (check_dyn, lower_dyn),
    **dict.fromkeys(("Buf", "Array"), (check_owner, lower_construct)),
}

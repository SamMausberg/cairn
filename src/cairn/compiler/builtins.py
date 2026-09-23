"""Every builtin in one place: how it types, what it costs, and how it lowers.

Each entry of TABLE pairs a rule `(checker, call, args, type_args, expected) -> Type` with a lowering
`(emitter, call) -> C++`. Names in SOFT arrived in 1.0 and yield to a program's own function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import facts, printing, rings
from .traits import vtable
from .tree import (
    BOOL,
    FLOAT,
    HOST_VISIBLE,
    INT,
    NUMERIC,
    SIGNED,
    STORAGE,
    UNSIGNED,
    USIZE,
    VOID,
    Expr,
    Type,
    fail,
    is_view,
    root,
)

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

WRAPPING = {"add_wrap", "sub_wrap", "mul_wrap", "shl_wrap", "shr"}
SOFT = {"take", "swap", "transfer", "mmio_read", "mmio_write", "asm", "wait", "collect"}
MATH = {"sqrt", "floor", "ceil", "trunc", "abs", "to_bits"}  # 1.4: a program's own function of the name wins
SOFT |= MATH | printing.NAMES | {"quantize", "quantize_stochastic", "from_bits", "assert", "assert_eq"}
QUANTIZED = [*STORAGE, "i8", "u8", "i16", "u16"]  # where one rounding of x / scale is exact (cairn_float.hpp)
PATTERN = {"f32": "u32", "f64": "u64", **{n: "u16" if STORAGE[n][0] + STORAGE[n][1] > 7 else "u8" for n in STORAGE}}
F32 = Type("f32")
SHARED = {"Ticket": "Task", "Atomic": "Atomic", "Mutex": "Mutex", "Group": "Group"}  # CAIRN name -> cr::par class


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
    if len(args) != 1 or root(args[0]).tag not in {"name", "str"} or args[0].tag == "slice":
        fail("E-LEN", "len takes one direct borrowed view, local buffer or string literal.", e)
    ty = c.expr(args[0], consume=False)
    if not is_view(ty) and ty.name not in {"Buf", "Array"}:
        fail("E-LEN", "len requires an array view.", e)
    if args[0].tag != "str":
        c.leased(c.where(args[0]), "ro", e, elements=False)  # Lent elements keep their count; a lent owner may not.
    return USIZE


def lower_len(g: Emitter, e: Expr) -> str:
    count = g.pointer(e.args[0])[1]
    return f"static_cast<std::size_t>({count}ULL)" if count.isdigit() else count


def check_convert(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    arity(e, args, 1, "Scalar conversion takes one argument.")
    src = c.expr(args[0])
    if src.mode != "value" or src.name not in NUMERIC | STORAGE.keys():
        fail("E-CAST", "Conversion requires numeric scalar.", e)
    if e.val in STORAGE and src.name not in FLOAT:  # One rounding, from a value f32 or f64 holds exactly.
        fail("E-CAST", f"{e.val} rounds from f32 or f64; convert {src.name} to f32 or f64 first.", e)
    if src.name in STORAGE and e.val not in FLOAT:
        fail("E-CAST", f"{src.name} widens exactly to f32 or f64 only; convert from there.", e)
    if e.val in STORAGE:  # IEEE's conversion: infinity past the range, which f8e4m3 cannot hold, so it traps.
        if not STORAGE[e.val][2]:
            c.guard("conversion")
        overflow = "infinity" if STORAGE[e.val][2] else "trap"
        contract(c, e, "convert", src.name, e.val, overflow=overflow, nan="nan")
    if e.val in INT:  # Narrowing, and float to integer (truncation toward zero), are range checked.
        c.guard("conversion")
        facts.discharge(c, e, "conversion", facts.conversion(c, e))
    return Type(e.val)


def contract(c: Checker, e: Expr, op: str, source: str, target: str, **terms: str):
    """The numerical contract of a rounding the source wrote, as the receipt lists it beside the row."""
    record = {"line": e.line, "op": op, "from": source, "to": target, "rounding": "nearest-even", **terms}
    c.numerics.setdefault(c.f.name, []).append(record)


def lower_convert(g: Emitter, e: Expr) -> str:
    if e.val in STORAGE:
        return f"cr::fp::narrow<{g.type(e.ty)}>({g.expr(e.args[0])})"
    exact = e.val in FLOAT or e.established
    guard = "static_cast" if exact else "cr::convert" if e.args[0].ty.name in INT else "cr::truncate"
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
            facts.discharge(c, e, "shift", facts.shift(c, e))
    elif t.name not in INT or t.mode != "value":
        fail("E-MINMAX", "Bootstrap min/max are integer-only; floating NaN semantics must be explicit.", e)
    return t


def lower_binary(g: Emitter, e: Expr) -> str:
    a, b = (g.expr(x) for x in e.args)
    if e.established:  # A count the checker showed is below the width: the runtime's shift, unguarded.
        return f"static_cast<{g.type(e.ty)}>(std::uint64_t({a}) {'<<' if e.val == 'shl_wrap' else '>>'} {b})"
    return f"cr::{e.val}<{g.type(e.ty)}>({a}, {b})" if e.val in WRAPPING else f"std::{e.val}({a}, {b})"


def check_math(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """sqrt floor ceil trunc abs to_bits: IEEE 754 makes each correctly rounded or exact, so every compiler, the
    host and the device agree on them; the libm functions whose results vary are not builtins."""
    arity(e, args, 1, f"{e.val} takes one argument.")
    t = c.expr(args[0], None if e.val == "to_bits" else expected)
    if t.mode == "value" and (t.name in FLOAT or (e.val == "to_bits" and t.name in STORAGE)):
        return Type(PATTERN[t.name]) if e.val == "to_bits" else t
    if e.val == "abs" and t.mode == "value" and t.name in SIGNED:
        c.guard("overflow")  # The minimum has no magnitude of its own type.
        return t
    kind = "a signed integer or a float" if e.val == "abs" else "f32 or f64"
    fail("E-MATH-TYPE", f"{e.val} takes {kind}, not {t.display()}.", e)


def check_from_bits(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """from_bits[T](u): the float whose pattern is u, the inverse of to_bits; every pattern is some value."""
    ty = c.resolve(targs[0], e) if len(targs) == 1 else expected
    if ty is None or ty.mode != "value" or ty.name not in PATTERN:
        fail("E-MATH-TYPE", "Write from_bits[T](u) with T one of f32 f64 f16 bf16 f8e4m3 f8e5m2.", e)
    arity(e, args, 1, "from_bits takes the unsigned pattern.")
    c.expect(c.expr(args[0], Type(PATTERN[ty.name])), Type(PATTERN[ty.name]), args[0])
    e.ref = ("builtin", ty)
    return ty


def check_quantize(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """quantize[T](x, scale): x / scale rounded once, to nearest with ties to even, and clamped to T's finite
    range. quantize_stochastic[T](x, scale, noise) rounds away from zero with the probability the dropped
    fraction is, by the u32 noise the caller draws. The scale must be positive and finite, and an integer T has
    no NaN to give, so both are guards."""
    ty = c.resolve(targs[0], e) if len(targs) == 1 else expected
    if ty is None or ty.mode != "value" or ty.name not in QUANTIZED:
        fail("E-QUANTIZE", f"Write {e.val}[T](x, scale) with T one of {' '.join(QUANTIZED)}.", e)
    stochastic = e.val == "quantize_stochastic"
    arity(
        e, args, 2 + stochastic, f"{e.val} takes a value, its scale" + (" and 32 bits of noise." if stochastic else ".")
    )
    for a, want in zip(args, [F32, F32, Type("u32")], strict=False):
        c.expect(c.expr(a, want), want, a)
    c.guard("quantize")
    contract(c, e, "quantize", "f32", ty.name, scale="positive-finite", overflow="saturate",
             nan="nan" if ty.name in STORAGE else "trap")  # fmt: skip
    if stochastic:
        c.numerics[c.f.name][-1]["rounding"] = "stochastic-u32"
    e.ref = ("builtin", ty)
    return ty


def lower_math(g: Emitter, e: Expr) -> str:
    a, ty = g.expr(e.args[0]), g.type(e.ty)
    return f"cr::{'math::' if e.val in {'sqrt', 'floor', 'ceil', 'trunc'} else ''}{e.val}<{ty}>({a})"


def check_assert(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """assert(cond) and assert(cond, "why"): a guard the program writes. A false condition prints where it was
    written, and the text when there is one, on standard error, then aborts as every failed guard does."""
    if not 1 <= len(args) <= 2 or (len(args) == 2 and args[1].tag != "str"):
        fail("E-ARITY", "assert takes a condition and, optionally, one string literal saying what failed.", e)
    c.expect(c.expr(args[0], BOOL), BOOL, args[0])
    c.guard("assert")
    return VOID


def lower_assert(g: Emitter, e: Expr) -> str:
    g.need("cairn_assert.hpp")
    said = f": {e.args[1].val}" if len(e.args) == 2 else ""
    return f"cr::check({g.expr(e.args[0])}, {g.quoted(f'assertion failed {g.site(e.line)}{said}')})"


def check_assert_eq(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """assert_eq(a, b) and assert_eq(a, b, "why"): assert(a == b) that prints both values when they differ. The
    operands are checked as `a == b` is, so a literal takes the other side's type; only a scalar prints."""
    if not 2 <= len(args) <= 3 or (len(args) == 3 and args[2].tag != "str"):
        fail("E-ARITY", "assert_eq takes two values and, optionally, one string literal saying what failed.", e)
    c.expr(Expr("binary", "==", args[:2], e.line, e.col), BOOL)
    shown = args[0].ty
    if shown is None or shown.mode != "value" or shown.name not in NUMERIC | {"bool"}:
        fail("E-ASSERT-EQ", f"assert_eq prints what it compares, and {shown.display() if shown else 'that'} is not "
             "an integer, bool or float: write assert(a == b).", e)  # fmt: skip
    c.guard("assert")
    return VOID


def lower_assert_eq(g: Emitter, e: Expr) -> str:
    g.need("cairn_assert.hpp")
    said = f": {e.args[2].val}" if len(e.args) == 3 else ""
    where = g.quoted(f"assertion failed {g.site(e.line)}{said}")
    return f"cr::check_eq({g.expr(e.args[0])}, {g.expr(e.args[1])}, {where})"


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


def crossing(src: Type, dst: Type) -> str:
    """Which way a transfer goes, `h2d` and the like, by where each end's memory can be reached."""
    return "2".join("h" if t.place in HOST_VISIBLE else "d" for t in (src, dst))


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
    c.borrowed = borrows  # What a queued transfer holds until its wait.
    c.effects |= {"transfer:" + crossing(src, dst), "write:" + written} | ({"read:" + read} if read else set())
    return VOID


def lower_transfer(g: Emitter, e: Expr, queued: str | None = None) -> str:
    sizes = [g.span(a) if a.tag == "slice" else g.pointer(a)[1] for a in e.args]
    for a, peer in zip(e.args, reversed(sizes), strict=True):  # Each part is guarded against its peer's length.
        a.ref = peer if a.tag == "slice" else a.ref
    count, dst, src = sizes[0], g.pointer(e.args[0])[0], g.pointer(e.args[1])[0]
    if (way := crossing(e.args[1].ty, e.args[0].ty)) == "h2h":
        return f"std::copy_n({src}, {count}, {dst})"
    g.need("cairn_gpu.hpp")
    entry, order = ("copy", "") if queued is None else ("copy_async", queued)  # Queued on a stream of its own.
    return f"cr::gpu::{entry}({dst}, {src}, {count}, cr::gpu::Dir::{way}{order})"


def check_wait(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """Completion is the only thing that returns a task's borrows. wait(g) on a group joins every task still
    running, drops every result nobody collected, and returns every lease the group held."""
    arity(e, args, 1, "wait takes one ticket or one group.")
    c.spawning = "<wait>"
    ticket = c.expr(args[0])
    c.spawning = ""
    if ticket.name not in {"Ticket", "Group", "IoRing"} or args[0].tag != "name":
        fail("E-TYPE-MISMATCH", "wait takes the name of a ticket, a group or an I/O ring.", e)
    if ticket.name == "IoRing":
        rings.waited(c, args[0])
        return VOID
    c.leases.pop(args[0].val, None)
    c.before.pop(args[0].val, None)
    c.effect("join")
    return VOID if ticket.name == "Group" else ticket.args[0]


def check_group(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """Group[T](n): up to n tasks in flight, collected in the order they finish. Declared in place like an
    atomic, linear like a ticket, and its whole storage is taken here, so a submission never allocates."""
    c.host_only(e, "A task group is a host object")
    ty = explicit(c, e, "Group", targs, expected, "Write Group[T](capacity).")
    arity(e, args, 1, "Group takes the most tasks it holds at once.")
    c.expr(args[0], USIZE)
    c.guard("allocation", "alloc", "free")
    return ty


def check_collect(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """collect(g) blocks until some task of the group has finished and yields its result. Which task that was
    is not known here, so no lease is returned before wait(g); a group with nothing outstanding traps."""
    arity(e, args, 1, "collect takes the name of a group.")
    binding = c.env.get(args[0].val) if args[0].tag == "name" else None
    if binding is None or binding.ty.name != "Group":
        fail("E-TYPE-MISMATCH", "collect takes the name of a group.", e)
    c.expr(args[0], consume=False)
    c.effect("join")
    c.guard("collect")
    return binding.ty.args[0]


def check_shared(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """Atomic[T](v) and Mutex[T](v) are declared in place and reached through ro borrows."""
    c.host_only(e, "Atomics and mutexes are host objects")
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
    held = c.expr(args[0]).value
    if c.kind(held) == "linear":  # The erased value is dropped with its box; a linear one may only be consumed.
        fail("E-LINEAR-STORAGE", f"Dyn[...] erases what it holds and drops it: {held.display()} is linear.", e)
    members = vtable(c, ty.args[0].name, held, e)
    c.guard("allocation", "alloc", "free")
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
        c.guard("allocation", "alloc", "free")
    return ty


def lower_construct(g: Emitter, e: Expr) -> str:
    return f"{g.type(e.ty)}({g.expr(e.args[0])})" if e.args else f"{g.type(e.ty)}{{}}"


def lower_float(g: Emitter, e: Expr) -> str:
    """quantize and from_bits, which may name no storage type at all (quantize[i8], from_bits[f32])."""
    g.need("cairn_float.hpp")
    return f"cr::fp::{e.val}<{g.type(e.ty)}>({', '.join(map(g.expr, e.args))})"


TABLE: dict[str, tuple[Any, Any]] = {
    "len": (check_len, lower_len),
    "assert": (check_assert, lower_assert),
    "assert_eq": (check_assert_eq, lower_assert_eq),
    **dict.fromkeys(printing.NAMES, (printing.check_print, printing.lower_print)),
    **dict.fromkeys(NUMERIC, (check_convert, lower_convert)),
    **dict.fromkeys(WRAPPING | {"min", "max"}, (check_binary, lower_binary)),
    **dict.fromkeys(MATH, (check_math, lower_math)),
    **dict.fromkeys(STORAGE, (check_convert, lower_convert)),
    **dict.fromkeys(("quantize", "quantize_stochastic"), (check_quantize, lower_float)),
    "from_bits": (check_from_bits, lower_float),
    **dict.fromkeys(("mmio_read", "mmio_write", "asm"), (check_machine, lower_machine)),
    "transfer": (check_transfer, lower_transfer),
    "wait": (check_wait, lambda g, e: f"{g.expr(e.args[0])}.wait()"),
    "collect": (check_collect, lambda g, e: f"{g.expr(e.args[0])}.collect()"),
    "Group": (check_group, lower_construct),
    "IoRing": (rings.check_ring, lower_construct),
    **dict.fromkeys(("Atomic", "Mutex"), (check_shared, lower_construct)),
    **dict.fromkeys(("take", "swap"), (check_exchange, lower_exchange)),
    "Dyn": (check_dyn, lower_dyn),
    **dict.fromkeys(("Buf", "Array"), (check_owner, lower_construct)),
}

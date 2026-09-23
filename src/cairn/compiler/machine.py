"""The machine: `mmio_read`, `mmio_write` and `asm`, their rules beside their lowering.

Each reaches past the checker, so each is legal only inside `unsafe { }` and names what it does in the row: `mmio`
for a register access, `asm` for a string of instructions whose effects the checker cannot see.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .tree import UNSIGNED, USIZE, VOID, Expr, Type, fail

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

NAMES = {"mmio_read", "mmio_write", "asm"}


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
    count = 1 + (n == "mmio_write")
    if len(args) != count:
        fail("E-ARITY", f"{n} takes an address" + (" and a value." if n == "mmio_write" else "."), e)
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

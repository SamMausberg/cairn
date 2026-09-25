"""The tensor-core multiply: `mma_unordered(m, n, k, c, a, b)`, its rule, its numerical contract and its lowering.

It adds the product of the row-major `m x k` matrix `a` and the row-major `k x n` matrix `b`, both views of one
storage float, into the row-major `m x n` f32 view `c`. The name is the contract, as `add_wrap` names wrapping:

- every product `a[i][p] * b[p][j]` is exact wherever f32 holds it (always, for f16, f8e4m3 and f8e5m2 inputs;
  for bf16 unless the product overflows or underflows f32);
- every output is its old value plus its k products, each sum rounded to f32, in an order and grouping the
  hardware picks, which is why the written loop and the tensor cores need not agree to the last bit;
- every finite output lies within (k + 1) * 2^-22 * (|c[i][j]| + sum over p of |a[i][p] * b[p][j]|) of the exact
  result, its old value included, and an output any of whose products or partial sums is not finite is not finite
  either, with no promise which.

On the host the multiply is its own reference loop, every product then every sum in increasing p. On the device,
where all three views live, it runs on the tensor cores (runtime/cairn_tensor.hpp). The extents are checked once at
the call, `len(c) == m * n`, `len(a) == m * k` and `len(b) == k * n`, a guard that traps otherwise. The receipt lists
the contract under `numerics`, and `cairn verify` answers unknown for a function that holds one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..lower import execution
from ..syntax.tree import HOST_VISIBLE, STORAGE, USIZE, VOID, Expr, Type, fail, is_view
from . import fragments

if TYPE_CHECKING:
    from ..check.checking import Checker
    from ..lower.codegen import Emitter

BOUND = "(k + 1) * 2^-22 * (|c| + sum |a * b|)"  # how far a finite output may lie from the exact result


def check_mma(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    from ..primitives.builtins import arity, contract  # builtins registers this rule, so it is imported here, not above

    if len(args) == 3:  # one warp's step on tensor-core fragments: compiler/device/fragments.py
        return fragments.check_mma(c, e, args)
    arity(e, args, 6, "mma_unordered takes m, n and k, then the f32 matrix c[m * n] it adds into, and the row-major "
          "a[m * k] and b[k * n]; or an accumulator fragment, an A and a B.")  # fmt: skip
    if c.lanes:
        fail("E-PARALLEL-NEST", "mma_unordered multiplies whole matrices; it cannot run inside a lane.", e)
    for extent in args[:3]:
        c.expect(c.expr(extent, USIZE), USIZE, extent)
    into, left, right = (c.view_argument(a) for a in args[3:])
    if not is_view(into) or into.mode != "rw" or into.name != "f32":
        fail("E-MMA", "mma_unordered adds into an rw view of f32: c[m * n], the tensor cores' accumulator.", args[3])
    if not (is_view(left) and is_view(right)) or left.name != right.name or left.name not in STORAGE:
        fail("E-MMA", f"a and b are views of one storage float, {' '.join(STORAGE)}; the multiply widens neither.", e)
    places = {into.place, left.place, right.place}
    device = "device" in places
    if (device and places != {"device"}) or (not device and not places <= HOST_VISIBLE):
        fail("E-PLACEMENT", "mma_unordered runs where its matrices live: all three @device for the tensor cores, or "
             "all three where the host reaches them.", e)  # fmt: skip
    borrows: list[tuple[str, str]] = []
    written = c.lend(args[3], "rw", borrows)
    read = {c.lend(a, "ro", borrows) for a in args[4:]} - {""}
    c.disjoint(borrows, e)
    c.effects |= {"read:" + r for r in read} | ({"write:" + written} if written else set())
    c.guard("mma")  # the extents' check: len(c) == m * n, len(a) == m * k, len(b) == k * n
    if device:
        c.effect("par:device")
    contract(c, e, "mma", left.name, "f32", rounding="unordered-f32", products="exact-in-f32", bound=BOUND)
    e.ref = ("builtin", Type(left.name), device)
    return VOID


def lower_mma(g: Emitter, e: Expr) -> str:
    if e.ref[2] == "fragment":
        return fragments.lower_mma(g, e)
    g.need("cairn_tensor.hpp")
    _, element, device = e.ref
    if device:
        g.need("cairn_gpu.hpp")
        if element.name == "bf16":  # bf16 fragments start at sm_80; f16 and the widened 8-bit floats run on sm_75
            g.feature("bf16")
    extents = ", ".join(g.expr(x) for x in e.args[:3])
    views = ", ".join(f"{data}, {count}" for data, count in (g.pointer(a) for a in e.args[3:]))
    if device:  # on the calling thread's execution context, waiting for its stream alone (runtime/cairn_exec.hpp)
        return f"cr::tensor::launch<{g.type(element)}>({execution.CONTEXT}, {extents}, {views})"
    return f"cr::tensor::multiply<{g.type(element)}>({extents}, {views})"

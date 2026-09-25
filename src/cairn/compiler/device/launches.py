"""Foreign kernels: an `extern` with `launch(threads, block)`, its rule beside its lowering.

    extern "stencil_1d_tiled" fn stencil_gpu(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) launch(n, 256)
      effects();

declares a `__global__` function that a vendored CUDA source defines (projects/foreign.py builds it). A call from host
code, inside `unsafe` as every foreign call is, launches it over `threads` indices with `block` threads to a block and
as many blocks as cover them, on the calling thread's stream (runtime/cairn_exec.hpp), and returns once that stream
has run it, as a device `parallel` region does. The kernel is trusted to guard its own indices and to touch only
what its views lend it, as its declared effects say: the checker cannot see its body. Its row adds `par:device` and
`trap`, since a launch that fails aborts.

The lowering declares the kernel with the C++ types CAIRN passes, so a definition with other parameter types leaves
the launch unresolved and the build fails to link; the unit that compiles the vendored source asserts the same types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..syntax.tree import SCALAR, USIZE, VOID, Function, fail, is_view

if TYPE_CHECKING:
    from ..check.checking import Checker
    from ..lower.codegen import Emitter

GRID = 2**31 - 1  # the most blocks one launch has along x


def check_launch(c: Checker, f: Function):
    """What a launched kernel may be: a void function of scalars and device views, a thread count and a block size."""
    assert f.launch is not None
    threads, block = f.launch
    if f.ret != VOID:
        fail("E-LAUNCH", "A launched kernel returns nothing; it writes its results through a view.", f)
    if not 32 <= block <= 1024 or block % 32:
        fail("E-LAUNCH", f"A block is whole warps, 32 to 1024 threads; {block} is not.", f)
    if not (threads.isdigit() or dict(f.params).get(threads) == USIZE):
        fail("E-LAUNCH", f"launch counts its threads by a usize parameter or a literal; {threads} is neither.", f)
    for name, t in f.params:
        if (t.mode == "value" and t.name not in SCALAR) or (t.mode != "value" and not is_view(t)):
            fail("E-LAUNCH", f"A launched kernel takes scalars and views; {name} is {t.display()}.", f)
        if is_view(t) and t.place not in {"device", "unified"}:
            fail("E-PLACEMENT", f"{name} is {t.place} memory, which a kernel cannot reach: pass an @device view.", f)
    if not (f.symbol or "x").replace("_", "a").isalnum():
        fail("E-LAUNCH", f"A launched kernel is named by one C++ identifier; {f.symbol} is not one.", f)
    c.effects |= {"par:device", "trap"}


def lower_launch(g: Emitter, f: Function) -> str:
    """The kernel's declaration, and the host function a call from CAIRN reaches: launch on the stream, check."""
    assert f.launch is not None
    g.need("cairn_gpu.hpp")
    threads, block = f.launch
    kernel = f.symbol or f.name.rsplit(".", 1)[-1]
    types = [g.type(t) for _, t in f.params]
    count = f"std::size_t{{{threads}}}" if threads.isdigit() else f"v_{threads}"
    parameters = ", ".join(f"{t} v_{n}" for t, (n, _) in zip(types, f.params, strict=True))
    arguments = ", ".join(f"v_{n}" for n, _ in f.params)
    return "\n".join([
        f"__global__ void {kernel}({', '.join(types)});",
        f"inline void {g.callee(f)}({parameters}) noexcept {{",
        f"  const std::size_t cr_blocks = {count} / {block} + ({count} % {block} != 0);",
        f"  if (cr_blocks > {GRID}ULL) cr::trap();",
        "  if (cr_blocks) cr::reuse::synchronous(cr::gpu::here(), [&](cudaStream_t cr_stream) {",
        f"    {kernel}<<<static_cast<unsigned>(cr_blocks), {block}, 0, cr_stream>>>({arguments});",
        "    cr::gpu::check(cudaGetLastError());",
        "  });",
        "}",
    ])  # fmt: skip

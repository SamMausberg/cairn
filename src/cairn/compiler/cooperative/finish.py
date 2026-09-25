"""A cooperative region's finish: `then threads t in T { ... }`, a body that runs once, in one block, after every
block of the region has finished and its writes are visible. Its rule and its lowering.

    blocks b in g threads t in 256 {
      ...                                    // each block leaves its partial sum in partial[b]
    } then threads t in 256 {
      ...                                    // one block adds every partial[k] together
    }

The finish is a block of its own: the region's thread count, split into its own names and extents (E-COOP-SHAPE
otherwise), and no block name. It is checked as a region of one block with every rule a region has, and it runs where
the region runs (E-PLACEMENT for a view of the other side). Everything the region's blocks wrote is visible to it and
nothing of theirs is in scope: the region's locals and shared arrays have ended, and the finish declares its own. So it
may read any element of an array the blocks wrote, plainly or atomically, which no block of the region may.

It runs exactly once, also when the grid has no blocks: then it is the only block, and a reduction's finish writes the
identity. On the host it is one more team of threads, started after every block's threads have been joined. On the
device the region is still one launch (runtime/cairn_coop.hpp): each block, once done, has one thread make the block's
writes visible device-wide (__threadfence) and add one to its launch's counter; the block that brings the count to the
grid's runs the finish after one more fence, then puts the counter back to zero for the next launch. The counters are a
table in the module's global memory, one for each stream that runs such launches, so a finish allocates nothing and
adds nothing to the row: a function with one may be enqueued on a caller's stream and captured in a CUDA graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..syntax.tree import USIZE, Expr, Stmt, fail

if TYPE_CHECKING:
    from ..check.checking import Checker
    from ..check.scope import Block
    from ..lower.codegen import Emitter

HIDDEN = "then#"  # the finish's one block name: no program can write it, so no program can name it


def check(c: Checker, s: Stmt, block: Block):
    """Check the finish of region `s`, whose blocks `block` describes, as a region of one block of the same threads."""
    from .cooperative import s_blocks  # cooperative calls this, so it is imported here, not above

    done: Stmt = s.other[0]
    one = Expr("int", "1", line=done.line, col=done.col, ty=USIZE)
    region = Stmt("blocks", exprs=[one, *done.exprs], body=done.body, other_names=[
        Expr("name", HIDDEN, line=done.line, col=done.col), *done.other_names], op="1", line=done.line, col=done.col)  # fmt: skip
    s_blocks(c, region, main=block)
    if region.ref.count != block.count:
        fail("E-COOP-SHAPE", f"A region's finish runs in the block that finishes last, so it has the region's "
             f"{block.count} threads; {' x '.join(map(str, region.ref.extents))} is {region.ref.count}.", done)  # fmt: skip
    done.ref = region  # the finish as a region of one block: what lowering and pricing read


def placed(c: Checker, s: Stmt, main: Block) -> bool:
    """A finish runs where its region runs: it reaches no view of the other side."""
    from .cooperative import placements

    if not main.device and "device" in placements(c, s.body):
        fail("E-PLACEMENT", "The region's blocks run on the host, so its finish does too, and it cannot reach "
             "@device memory.", s)  # fmt: skip
    return main.device


def lower(g: Emitter, s: Stmt, entry: str, head: str, body: str):
    """The region and its finish as one call: `run_then` on host threads, `launch_then` as one kernel."""
    from .cooperative import lambda_head, thread_names

    block, done = s.ref, s.other[0].ref.ref

    def inside():
        thread_names(g, done)
        g.block(s.other[0].body)

    last = g.inner(lambda: lambda_head(done), inside)
    shared = max(block.bytes, done.bytes)  # the finish reuses the last block's shared memory
    zeroed = f", {block.zeroed}, {done.zeroed}" if (block.zeroed, done.zeroed) != (shared, shared) else ""
    g.put(f"cr::coop::{entry}<{block.count}, {shared}{zeroed}>({head}, {body}, {last});")

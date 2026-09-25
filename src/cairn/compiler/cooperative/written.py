"""The rule that lets a shared array go unzeroed: `shared partial:f32[256];` with no initializer is accepted when every
element any thread reads was written first, by that thread earlier or by any thread before a barrier between them.

    shared warps:f32[8];                     // no zero fill where each block starts
    if t % 32 == 0 { warps[t / 32] = w; }    // thread 32 k writes warps[k]
    barrier;                                 // every write before it is every thread's
    if t < 8 { let s = warps[t]; }           // so these reads see a value, never what an earlier block left

`Written` rides on the phase rule's run of one block (block_run.BlockRun, compiler/cooperative/phases.py). It keeps the elements
written for sure before the phase now open: at each barrier, whatever a write stores in every thread that surely makes
it, in every way the phase may have gone, is written from then on. A read, an atomic update, and a part lent to a
callee to write (which may read it first) must reach elements written that way, or written for sure by the same
thread earlier in the phase. A write that may not happen (under an unknown condition, in a loop that may not run, or
by a callee) makes nothing written. A branch the whole block takes one way it cannot tell keeps what both ways wrote;
a loop whose trip count it cannot tell, and whose body holds a barrier, keeps what came before it. An index it cannot
follow is refused, since it cannot show that element was written, unless every element of the array was.

Anything else is E-COOP-UNWRITTEN, naming the first element read before any write. Without a zero fill a block starts
with what the last block on that SM left, which nothing may read; on the host the unzeroed arrays start each block
filled with a pattern (runtime/cairn_coop.hpp), so a read the rule let through would show.
"""

from __future__ import annotations

from typing import Any

from ..syntax.tree import fail
from .block_run import RANGE, TRAP, BlockRun, Event
from .footprints import Poly


def keyed(idx: Any) -> tuple | None:
    """An element as the rule compares them: a number, or a polynomial's terms and offset; None for an unknown one."""
    if idx is None or idx is TRAP or isinstance(idx, bool):
        return None
    if isinstance(idx, int):
        return (frozenset(), idx)
    return (idx.key, idx.offset) if isinstance(idx, Poly) else None


class Written(BlockRun):
    """The phase rule's run of one block (phases.Phases is one), keeping the elements written for sure and refusing a
    read of an unzeroed array that may reach any other."""

    unset: set[str]  # the shared arrays declared without `= zeroed`
    done: set[tuple]  # (array, element) written for sure before the open phase began
    own: dict[int, tuple[int, dict[int, set[tuple]]]]  # id(alternative) -> (events seen, thread -> its sure writes)

    def written(self, unset: set[str]):
        self.unset, self.done, self.own = set(unset), set(), {}

    def who(self, t: int) -> str:
        names, extents, parts = self.block.threads, self.block.extents, []
        for n, k in zip(names, extents, strict=True):
            parts.append(f"{n} = {t % k}")
            t //= k
        return "thread " + ", ".join(parts)

    def elements(self, index: Any, t: int) -> list[Any]:
        """The elements an index reaches in thread t: one, or each of a part's; None for one it cannot follow."""
        if isinstance(index, tuple):
            lo, hi = self.at(index[1], t), self.at(index[2], t)
            if lo is TRAP or hi is TRAP:
                return []
            if isinstance(lo, int) and isinstance(hi, int) and not isinstance(lo, bool) and hi - lo <= RANGE:
                return list(range(lo, hi))
            return [None]
        return [self.at(index, t)]

    def record(self, array, index, write, node, time, mask, warp=False, atomic=False, sure=True):
        if array in self.unset and (not write or atomic or not sure):
            self.reached(array, index, node, mask)
        super().record(array, index, write, node, time, mask, warp, atomic, sure)

    def barrier(self, node: Any):
        found = [self.surely(alternative) for alternative in self.open]
        super().barrier(node)
        if found:
            self.done |= set.intersection(*found)
        self.own = {}

    def snapshot(self) -> Any:
        return set(self.done)

    def restore(self, state: Any):
        self.done = set(state)

    def meet(self, a: Any, b: Any) -> Any:
        return a & b

    # ------------------------------------------------------------------------------------------------------------

    def sure(self, ev: Event, t: int) -> bool:
        return ev.write and ev.sure and not ev.atomic and not ev.warp and (ev.mask is None or ev.mask[t] == 1)

    def surely(self, alternative: list[Event]) -> set[tuple]:
        """What one way through the phase writes for sure, in any thread."""
        out: set[tuple] = set()
        for ev in alternative:
            if ev.array not in self.unset or not (ev.write and ev.sure and not ev.atomic):
                continue
            for t in range(self.T):
                if ev.mask is not None and ev.mask[t] != 1:
                    continue
                for idx in self.elements(ev.index, t):
                    if (key := keyed(idx)) is not None:
                        out.add((ev.array, key))
        return out

    def mine(self, alternative: list[Event], t: int) -> set[tuple]:
        """What thread t has written for sure in this way through the open phase so far, kept as it grows."""
        seen, held = self.own.get(id(alternative), (0, {}))
        if seen > len(alternative):  # another list now has this id: start again
            seen, held = 0, {}
        for ev in alternative[seen:]:
            if ev.array not in self.unset:
                continue
            for u in range(self.T):
                if self.sure(ev, u):
                    held.setdefault(u, set()).update(
                        (ev.array, k) for i in self.elements(ev.index, u) if (k := keyed(i)) is not None
                    )
        self.own[id(alternative)] = (len(alternative), held)
        return held.get(t, set())

    def reached(self, array: str, index: Any, node: Any, mask: list[int] | None):
        """Refuse a read of an unzeroed array that may reach an element nobody surely wrote first."""
        size = self.counts[array]
        whole = all((array, (frozenset(), e)) in self.done for e in range(size))  # the array written throughout
        for alternative in self.open:
            for t in range(self.T):
                if mask is not None and not mask[t]:
                    continue
                for idx in self.elements(index, t):
                    if idx is TRAP or (isinstance(idx, int) and not isinstance(idx, bool) and idx >= size):
                        continue  # the thread's guard aborts it first
                    key = keyed(idx)
                    if key is not None and ((array, key) in self.done or (array, key) in self.mine(alternative, t)):
                        continue
                    if key is None and whole:
                        continue  # every element was written first, so whichever one the index names was
                    shown = "..." if key is None else repr(idx)
                    why = "an index the checker cannot follow, so it cannot show the element was written first" \
                        if key is None else "and no thread surely wrote that element first"  # fmt: skip
                    fail("E-COOP-UNWRITTEN", f"{array} is declared without `= zeroed`, and {self.who(t)} reads "
                         f"{array}[{shown}] at line {getattr(node, 'line', 0)}, {why}. Write it first, in this "
                         "thread or in another before a barrier, or declare the array `= zeroed`.", node,
                         array=array)  # fmt: skip

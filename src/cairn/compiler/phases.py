"""The phase rule of a cooperative region: between two barriers, no two threads of one block touch one element of a
shared array where either of them writes it.

`block_run.BlockRun` runs the region's body for every thread of one block together; `Phases` is that run, judging
each phase as a barrier ends it.

Every access to a shared array is recorded with the phase it falls in: the stretch of the run between two barriers.
When a phase ends, every pair of accesses to one element by two different threads is looked at, and a pair where
either writes is refused:

- both write: E-COOP-CONFLICT;
- the write runs first: the read wants the new value, and a barrier between them would give it (E-COOP-UNORDERED);
- the read runs first: the write replaces what the reader may not have read yet (E-COOP-REUSE).

Two indexes are one element when they are the same number, or the same polynomial; they are apart when they differ by
a nonzero constant. Anything else, an unknown index or two indexes whose difference depends on a symbol, beside an
access by another thread that writes, is refused with E-COOP-UNDECIDED: the rule never accepts what it cannot decide.
An index outside the array is no access, since its bounds guard aborts the thread first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .block_run import LANES, RANGE, TRAP, BlockRun, Event
from .footprints import Poly
from .tree import Stmt, fail

if TYPE_CHECKING:
    from .checking import Checker
    from .scope import Block


class Phases(BlockRun):
    """One block's run that refuses each phase in which two threads touch one shared element and either writes."""

    def whom(self, x: tuple[int, Event]) -> str:
        return f"a lane of warp {x[0] // LANES} of the block" if x[1].warp else self.who(x[0])

    def who(self, t: int) -> str:
        names, extents, parts = self.block.threads, self.block.extents, []
        for n, k in zip(names, extents, strict=True):
            parts.append(f"{n} = {t % k}")
            t //= k
        return "thread " + ", ".join(parts)

    def check(self, events: list[Event]):
        """Refuse a phase in which two threads touch one element of a shared array and either writes."""
        arrays: dict[str, list[Event]] = {}
        for ev in events:
            arrays.setdefault(ev.array, []).append(ev)
        for array, found in arrays.items():
            if any(ev.write for ev in found):
                self.check_array(array, found)

    def check_array(self, array: str, found: list[Event]):
        size = self.counts[array]
        cells: dict[tuple, list[tuple[int, Event]]] = {}
        vague: list[tuple[int, Event]] = []
        for ev in found:
            for t in range(self.T):
                m = 1 if ev.mask is None else ev.mask[t]
                if not m:
                    continue
                for idx in self.elements(ev.index, t):
                    if idx is TRAP:
                        continue
                    if idx is None or isinstance(idx, bool):
                        vague.append((t, ev))
                        continue
                    if isinstance(idx, int):
                        if idx < size:
                            cells.setdefault((frozenset(), idx), []).append((t, ev))
                    else:
                        cells.setdefault((idx.key, idx.offset), []).append((t, ev))
        for (key, offset), touches in cells.items():
            for writer in (x for x in touches if x[1].write):
                others = [x for x in touches if clash(x, writer)]
                if others:
                    element = repr(Poly({**dict(key), (): offset})) if key else str(offset)
                    self.refuse(array, element, writer, next((x for x in others if x[1].write), others[0]))
        keyed: dict[frozenset, list[tuple[int, Event]]] = {}
        for (key, _), touches in cells.items():
            keyed.setdefault(key, []).extend(touches)
        groups = list(keyed.values())
        for i, a in enumerate(groups):
            for b in groups[i + 1 :]:
                self.apart(array, a, b)
        everything = [*vague, *(y for g in groups for y in g)]
        for t, ev in vague:
            self.apart(array, [(t, ev)], everything)

    def elements(self, index: Any, t: int) -> list[Any]:
        if isinstance(index, tuple):
            lo, hi = self.at(index[1], t), self.at(index[2], t)
            if lo is TRAP or hi is TRAP:
                return []
            if isinstance(lo, int) and isinstance(hi, int) and not isinstance(lo, bool) and hi - lo <= RANGE:
                return list(range(lo, hi))
            return [None]
        return [self.at(index, t)]

    def apart(self, array: str, a: list[tuple[int, Event]], b: list[tuple[int, Event]]):
        """Accesses whose indexes differ by a symbol, or are unknown: they may meet, so a write among them by one
        thread beside any access by another is undecided."""
        for writes, others in ((a, b), (b, a)):
            for x in (x for x in writes if x[1].write):
                y = next((y for y in others if clash(y, x)), None)
                if y is not None:
                    self.undecided(array, x, y)

    def undecided(self, array: str, x: tuple[int, Event], y: tuple[int, Event]):
        first, second = sorted((x, y), key=lambda z: z[1].time)
        self.atomic(array, first, second, "reads or writes plainly an element the checker cannot tell from it")
        fail("E-COOP-UNDECIDED", f"The checker cannot tell whether {array}[...] at line {first[1].node.line} "
             f"({self.whom(first)}) and at line {second[1].node.line} ({self.whom(second)}) are one element, "
             "and one of them writes it in the same phase. Index shared arrays by the thread and loop names and "
             "constants, or put a barrier between the two.", second[1].node, array=array)  # fmt: skip

    def atomic(self, array: str, a: tuple[int, Event], b: tuple[int, Event], how: str):
        """Refuse an atomic update and a plain access by another thread in one phase: E-ATOMIC-MIXED."""
        if a[1].atomic or b[1].atomic:
            update, plain = (a, b) if a[1].atomic else (b, a)
            fail("E-ATOMIC-MIXED", f"{array}[...] is updated atomically by {self.whom(update)} at line "
                 f"{update[1].node.line}, and {self.whom(plain)} {how} at line {plain[1].node.line} in the same phase: "
                 "a plain access of an element another thread updates atomically sees half an update or undoes one. "
                 f"Put a barrier {between(a[1].node.line, b[1].node.line)}.", b[1].node, array=array)  # fmt: skip

    def refuse(self, array: str, element: str, x: tuple[int, Event], y: tuple[int, Event]):
        a, b = (x, y) if x[1].time <= y[1].time else (y, x)
        self.atomic(array, a, b, "reads or writes it plainly")
        where = f"line {a[1].node.line}" if a[1].node.line == b[1].node.line else \
            f"lines {a[1].node.line} and {b[1].node.line}"  # fmt: skip
        if x[1].write and y[1].write:
            fail("E-COOP-CONFLICT", f"{array}[{element}] is written by {self.whom(a)} and by {self.whom(b)} in "
                 f"the same phase, at {where}: two threads write one element with no barrier between them, and "
                 "the last to write wins. Give each thread its own element.", b[1].node, array=array,
                 threads=[a[0], b[0]])  # fmt: skip
        if a[1].write:
            fail("E-COOP-UNORDERED", f"{self.whom(b)} reads {array}[{element}] at line {b[1].node.line}, which "
                 f"{self.whom(a)} writes at line {a[1].node.line} in the same phase: nothing makes the write "
                 f"happen first. Put a barrier {between(a[1].node.line, b[1].node.line)}.", b[1].node, array=array,
                 write=a[1].node.line, read=b[1].node.line)  # fmt: skip
        fail("E-COOP-REUSE", f"{self.whom(b)} rewrites {array}[{element}] at line {b[1].node.line} while "
             f"{self.whom(a)} may still be reading what it held, at line {a[1].node.line}, in the same phase. "
             f"Put a barrier {between(a[1].node.line, b[1].node.line)}, so every thread has read the old value "
             "first.", b[1].node, array=array, read=a[1].node.line, write=b[1].node.line)  # fmt: skip


def one(x: tuple[int, Event], y: tuple[int, Event]) -> bool:
    """Whether two accesses are one thread's, which never conflict: the same thread, or for a write in a lane
    nobody names, the same write by the same warp."""
    if x[1].warp or y[1].warp:
        return x[1] is y[1] and x[0] // LANES == y[0] // LANES
    return x[0] == y[0]


def clash(x: tuple[int, Event], y: tuple[int, Event]) -> bool:
    """Whether two accesses to one element may conflict: made by two threads, and not both atomic updates."""
    return not one(x, y) and not (x[1].atomic and y[1].atomic)


def between(first: int, second: int) -> str:
    """Where a barrier orders an access at line `first` before one at line `second` that runs after it."""
    if first == second:
        return f"after line {first}, before it runs again"
    return f"after line {first} and before line {second} runs"


def check(c: Checker, s: Stmt, block: Block, grid: list[Any]):
    """Run the region's body for every thread of one block and refuse a phase with two threads at one element."""
    if not block.shared:
        return
    run = Phases(c, block, {name: n for name, (_, n, _) in block.shared.items()}, node=s)
    env: dict[str, Any] = {}
    t = list(range(block.count))
    for name, extent in zip(block.threads, block.extents, strict=True):
        env[name] = [x % extent for x in t]
        t = [x // extent for x in t]
    for name in block.grid:
        env[name] = Poly.of(name)
    run.stmts(s.body, env, None)
    run.barrier(s)  # the block's end: every thread is done before the next block uses its arrays

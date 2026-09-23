"""What a cooperative region does, counted from the checked tree: how many blocks it runs, the shape and shared
memory of each, and what every thread does between its barriers.

`blocks b in G threads t in T { body }` runs G blocks of T threads (compiler/cooperative.py). The region's `count` is
its number of blocks, a polynomial in the function's extents, and its body is what one thread does, counted as
`work.py` counts a lane. Four things a thread does are counted apart from a lane's:

- an access to a shared array or a pipeline stage costs shared-memory wavefronts: one for each 128 bytes a warp
  moves, and one more for every further word one of the 32 banks has to serve (a bank conflict);
- an access to an array from outside costs its share of the 32-byte sectors the block touches between two
  barriers, each sector once: 32 adjacent f32 are four sectors, and one f32 of every eight is a sector each. What a
  warp's scattered lanes cost in L1 and L2 transactions beyond the sectors is not priced;
- a barrier, a pipeline's fill and wait, a warp shuffle or reduction and a tensor-core fragment step are counted by
  kind, a fragment step with its 2 M N K multiply-add operations;
- a branch costs what the warps that enter it issue.

Which lanes of a warp take part in each access and each branch, and which bank or sector each reaches, comes from the
census: the body run for every thread of one block by the phase rule's own evaluator (compiler/phases.py), with the
block names and everything from outside as symbols. Where the census cannot place the lanes, as for an index read
from data or lanes whose offsets differ by a symbol, an access is priced a sector a lane, or conflict free in shared
memory, and the region names its line. Nothing here is timed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..compiler import fragments, phases
from ..compiler.cooperative import SHUFFLES, WARP_SIZE
from ..compiler.footprints import Poly as Symbolic
from ..compiler.tree import FLOAT, USIZE, Expr, Stmt, Type, nested, root
from .regions import walked
from .work import ONE, Frame, Poly, Region, Work, add, data_dependent, path, widen

PHASE_BYTES = 128  # what shared memory serves a warp in one wavefront: 32 banks of 4-byte words
SECTOR = 32  # the bytes device memory moves for any access that touches them
WORD = 4
PLACED = {"shared_wavefront", "fill_bytes"}  # counts the census made from the lanes that take part
REDUCE_STEPS = 5  # a warp reduction's butterfly: halves, quarters, ... down to neighbours, log2(32) shuffles


@dataclass
class Stage:
    """One pipeline of a region: its stages and each wait's count of transfers left in flight."""

    name: str
    line: int
    elements: int
    element_bytes: int
    depth: int
    stage_bytes: int  # one stage as the checker lays it out, on 16 bytes
    waits: dict[int, int] = field(default_factory=dict)  # line -> transfers the wait leaves in flight (wait_group N)

    @property
    def in_flight(self) -> int:
        """The fewest transfers any of its waits leaves in flight: the checker's N of `cp.async.wait_group N`."""
        return min(self.waits.values(), default=0)


@dataclass
class Shape:
    """The constants of one cooperative region; everything that grows with the sizes is in the region's body."""

    function: str  # the function whose kernel the region is
    ordinal: int  # which of `function`'s cooperative regions of this placement, in source order
    device: bool
    threads: int
    extents: tuple[int, ...]
    shared_bytes: int  # every array and stage of a block, each from a 128-byte boundary: its static shared memory
    arrays: list[tuple[str, int, int]] = field(default_factory=list)  # (name, line, bytes)
    stages: list[Stage] = field(default_factory=list)
    barriers: list[int] = field(default_factory=list)  # lines
    collectives: dict[int, str] = field(default_factory=dict)  # line -> the warp operation there
    fragments: dict[int, str] = field(default_factory=dict)  # line -> the fragment operation there
    census: str = ""  # empty when the census ran; else why it did not
    vague: list[int] = field(default_factory=list)  # lines of accesses the census could not place


@dataclass
class Site:
    """What the census saw of one access, summed over the block-wide executions it saw."""

    runs: int = 0
    lanes: int = 0  # active lanes
    cost: float = 0.0  # wavefronts for shared memory, sectors for an array from outside
    vague: bool = False


def wavefronts(offsets: list[tuple[int, int]], size: int) -> int:
    """The wavefronts one warp's access takes: its lanes in groups that move 128 bytes, each group as many as the
    busiest bank has distinct words to serve (compiler/layouts.py `conflicts` counts the same for a spread)."""
    per = max(1, min(WARP_SIZE, PHASE_BYTES // size))
    groups: dict[int, dict[int, set[int]]] = {}
    for lane, o in offsets:
        banks = groups.setdefault(lane // per, {})
        for word in range(o // WORD, (o + size - 1) // WORD + 1):
            banks.setdefault(word % 32, set()).add(word)
    return sum(max(len(words) for words in banks.values()) for banks in groups.values())


@dataclass
class Census(phases.Phases):
    """The phase rule's run of one block, keeping what each access and each branch did instead of judging it."""

    local: set[str] = field(default_factory=set)  # shared arrays and pipelines
    sizes: dict[str, int] = field(default_factory=dict)
    sites: dict[int, Site] = field(default_factory=dict)  # id(index expression) -> what it did
    branches: dict[int, list[Any]] = field(default_factory=dict)  # id(if) -> one split per execution
    seen: dict[int, phases.Event] = field(default_factory=dict)  # held, so no later event reuses an id

    def check(self, events: list[phases.Event]):
        """One phase's accesses. An array from outside moves each sector the block touches in the phase once: the
        warps' later touches of it find it in the SM's L1, so every access pays its share of the phase's sectors."""
        fresh = [ev for ev in events if id(ev) not in self.seen]  # an event sits in every alternative open then
        self.seen |= {id(ev): ev for ev in fresh}
        touched: dict[str, list[tuple[Site, set[Any]]]] = {}
        for ev in fresh:
            found = self.place(ev)
            if found is not None:
                touched.setdefault(ev.array, []).append(found)
        for pairs in touched.values():
            union = len(set().union(*(sectors for _, sectors in pairs)))
            total = sum(len(sectors) for _, sectors in pairs)
            for site, sectors in pairs:
                site.cost += len(sectors) * union / total

    def place(self, ev: phases.Event) -> tuple[Site, set[Any]] | None:
        """Count one block-wide access: a shared one's wavefronts, warp by warp; for an outside array, the sectors
        its lanes touch, which `check` shares out, or a sector a lane where the census cannot place them."""
        node = ev.node
        if not isinstance(node, Expr) or node.tag != "index" or isinstance(ev.index, tuple):
            return None
        size = self.sizes.get(ev.array) or (self.c.sizeof(node.ty) if node.ty else 8)
        live = [t for t in range(self.T) if ev.mask is None or ev.mask[t]]
        if not live:
            return None
        site = self.sites.setdefault(id(node), Site())
        site.runs += 1
        site.lanes += len(live)
        shared = ev.array in self.local
        sectors: set[Any] = set()
        for w in range(0, self.T, WARP_SIZE):
            lanes = [t for t in live if w <= t < w + WARP_SIZE]
            values = [self.at(ev.index, t) for t in lanes]
            if not lanes:
                continue
            if any(v is None or v is phases.TRAP or isinstance(v, bool) for v in values):
                site.vague = True  # read from data, or past what the evaluator follows
                site.cost += -(-len(lanes) * size // PHASE_BYTES) if shared else len(lanes)
                continue
            polys = [phases.as_poly(v) for v in values]
            if not shared:  # lanes whose offsets differ by a symbol, a row apart, are in different sectors
                sectors |= {(p.key, p.offset * size // SECTOR) for p in polys}
            elif len({p.key for p in polys}) > 1:
                site.vague = True
                site.cost += -(-len(lanes) * size // PHASE_BYTES)
            else:
                site.cost += wavefronts([(t - w, p.offset * size) for t, p in zip(lanes, polys, strict=True)], size)
        return (site, sectors) if sectors else None

    def branch(self, s: Stmt, cond: Any, env: dict[str, Any], mask: list[int] | None):
        self.branches.setdefault(id(s), []).append(self.split(cond, mask))
        return super().branch(s, cond, env, mask)

    def split(self, cond: Any, mask: list[int] | None) -> tuple[float, float, float, float] | None:
        """(the share of the live warps that enter the body, the share that enter the else, whether any enters the
        body, whether any enters the else), or None when the block goes one way the census cannot tell."""
        if cond is phases.TRAP:
            return None
        if not isinstance(cond, list) and mask is None:
            return (1.0, 0.0, 1.0, 0.0) if cond is True else (0.0, 1.0, 0.0, 1.0) if cond is False else None
        live = self.warps(mask)
        if not live:
            return None
        yes, no = self.warps(self.restrict(mask, cond, True)), self.warps(self.restrict(mask, cond, False))
        return yes / live, no / live, float(yes > 0), float(no > 0)

    def warps(self, mask: list[int] | None) -> int:
        return self.T // WARP_SIZE if mask is None else len({t // WARP_SIZE for t, m in enumerate(mask) if m})


def names(ss: list[Stmt]) -> tuple[dict[str, Any], set[str]]:
    """The usize names a body reads, each a symbol or a static natural's value, and the arrays it indexes."""
    read: dict[str, Any] = {}
    arrays: set[str] = set()

    def expr(e: Expr):
        if e.tag == "name" and e.ty == USIZE and not isinstance(e.ref, Expr):
            natural = isinstance(e.ref, int) and not isinstance(e.ref, bool)
            read[e.val] = e.ref if natural else Symbolic.of(e.val)
        if e.tag == "index" and root(e).tag == "name":
            arrays.add(root(e).val)
        for a in e.args:
            if isinstance(a, Expr):
                expr(a)

    def stmts(body: list[Stmt]):
        for s in body:
            for e in s.exprs:
                expr(e)
            stmts(nested(s))

    stmts(ss)
    return read, arrays


def census(c: Any, s: Stmt) -> Census | str:
    """The census of one region, or why it could not run."""
    block = s.ref
    read, arrays = names(s.body)
    counts = dict.fromkeys(arrays, 0)
    counts |= {n: size for n, (_, size, _) in block.shared.items()}
    counts |= {n: p.size for n, p in block.pipelines.items()}
    sizes = {n: c.sizeof(element) for n, (element, _, _) in block.shared.items()}
    sizes |= {n: c.sizeof(p.element) for n, p in block.pipelines.items()}
    run = Census(c, block, counts, node=s, local=set(block.shared) | set(block.pipelines), sizes=sizes)
    env: dict[str, Any] = dict(read)
    t = list(range(block.count))
    for name, extent in zip(block.threads, block.extents, strict=True):
        env[name] = [x % extent for x in t]
        t = [x // extent for x in t]
    for name in block.grid:
        env[name] = Symbolic.of(name)
    try:
        run.stmts(s.body, env, None)
        run.barrier(s)
    except Exception as error:  # the region is priced without it, and says why
        return f"{type(error).__name__}: {str(error)[:160]}"
    return run


def pure(c: Any, e: Expr) -> bool:
    """A value computed from sizes, names and layout coordinates alone, with no element read and no call that could
    read one: what a thread's index may be made of without resting on data."""
    if e.tag == "index":
        return False
    laid = isinstance(e.ref, tuple) and e.ref[:1] == ("layout",)
    if e.tag == "call" and not laid and e.val not in {"len", "min", "max", "shr", "shl_wrap", "usize"}:
        return False
    return all(pure(c, a) for a in e.args if isinstance(a, Expr))


class Tally:
    """The counter's view of one cooperative region while its body is walked: work.py's Counter hands this each
    statement, access and call, and it counts what a thread does that a lane does not."""

    def __init__(self, k: Any, s: Stmt, found: Census | str | None):
        self.k, self.s, self.block = k, s, s.ref
        self.census = found if isinstance(found, Census) else None
        self.local = set(self.block.shared) | set(self.block.pipelines)
        f = k.f
        regions = [x for x in walked(f.body) if x.tag == "blocks" and x.ref.device == self.block.device] if f else [s]
        ordinal = regions.index(s)
        self.shape = Shape(f.name if f is not None else "", ordinal, self.block.device, self.block.count,
                           tuple(self.block.extents), self.block.bytes,
                           census=found if isinstance(found, str) else "")  # fmt: skip
        for x in s.body:
            if x.tag == "shared":
                self.shape.arrays.append((x.name, x.line, k.c.sizeof(x.ref[0]) * x.ref[1]))
            elif x.tag == "pipeline":
                p = x.ref
                stage = -(-p.size * k.c.sizeof(p.element) // 16) * 16
                self.shape.stages.append(Stage(x.name, x.line, p.size, k.c.sizeof(p.element), p.depth, stage))

    # Statements --------------------------------------------------------------------------------------------------

    def stmt(self, s: Stmt, at: Frame) -> bool:
        """Count `s` when a thread's cost of it differs from a lane's; False leaves it to the counter."""
        tag, times, threads = s.tag, at.times, self.block.count
        if tag == "barrier":
            at.work.op("barrier", times)
            self.shape.barriers.append(s.line)
        elif tag in {"shared", "pipeline"}:  # zeroed where the block starts, a share by every thread
            held = self.block.shared[s.name] if tag == "shared" else None
            size = self.k.c.sizeof(held[0]) * held[1] if held else next(
                st.stage_bytes * st.depth for st in self.shape.stages if st.name == s.name)  # fmt: skip
            at.work.op("shared_wavefront", times * (size / PHASE_BYTES / threads))
            add(at.work.writes, f"shared {s.name}", times * (size / threads))
        elif tag == "warp_reduce":
            self.k.expr(s.exprs[0], at)
            at.work.op("shuffle", times * REDUCE_STEPS)
            at.work.op(s.ty.name if s.ty is not None and s.ty.name in FLOAT else "int", times * REDUCE_STEPS)
            self.k.data.add(s.name)
            self.shape.collectives[s.line] = f"reduce {s.op} warp"
        elif tag == "if" and self.census is not None and id(s) in self.census.branches:
            return self.branch(s, at)
        elif tag == "let" and s.exprs and pure(self.k.c, s.exprs[0]):
            self.k.s_let(s, at)
            self.k.data.discard(s.name)  # a layout's coordinate of the thread's own index is not data
        else:
            return False
        return True

    def branch(self, s: Stmt, at: Frame) -> bool:
        """An `if` as its warps run it: each side's operations issued by the share of the live warps that enter it,
        both sides where a warp's lanes split; its accesses by what the census saw them move when they ran."""
        splits = self.census.branches[id(s)] if self.census else []
        if not splits or any(x is None for x in splits):
            return False
        n = len(splits)
        shares = [sum(x[i] for x in splits) / n for i in range(4)]
        self.k.expr(s.exprs[0], at)
        at.work.op("branch", at.times)
        for body, issued, entered in ((s.body, shares[0], shares[2]), (s.other, shares[1], shares[3])):
            if not body or not entered:
                continue
            w = Work()
            self.k.block(body, Frame(w, at.times, at.binders, at.lane, at.seen))
            for kind, count in w.ops.items():  # what the census already counted by lane is counted as entered
                add(at.work.ops, kind, count * (entered if kind in PLACED else issued))
            for mine, theirs in ((at.work.reads, w.reads), (at.work.writes, w.writes),
                                 (at.work.irregular, w.irregular)):  # fmt: skip
                for key, count in theirs.items():
                    add(mine, key, count * entered)
            for key, reach in w.footprint.items():
                widen(at.work.footprint, key, reach)
        return True

    # Accesses and calls --------------------------------------------------------------------------------------------

    def access(self, e: Expr, key: str, size: int, at: Frame, write: bool) -> bool:
        """An element read or written by a thread: shared-memory wavefronts, or the sectors of an outside array."""
        threads, site = self.block.count, self.census.sites.get(id(e)) if self.census else None
        base = key.split(".")[0]
        if site is not None and site.vague and e.line not in self.shape.vague:
            self.shape.vague.append(e.line)
        if base in self.local:
            each = site.cost / site.runs / threads if site else -(-WARP_SIZE * size // PHASE_BYTES) / WARP_SIZE
            share = site.lanes / site.runs / threads if site else 1.0
            at.work.op("shared_wavefront", at.times * each)
            add(at.work.writes if write else at.work.reads, f"shared {base}", at.times * size * share)
            return True
        index = e.args[1]
        reach = self.k.extent(e.args[0].ty, key) * size if e.args[0].ty is not None else Poly()
        widen(at.work.footprint, key, reach)
        if data_dependent(index, self.k.data, at.binders) and not pure(self.k.c, index):
            add(at.work.irregular, key, at.times * (site.lanes / site.runs / threads if site else 1.0))
            return True
        moved = site.cost * SECTOR / site.runs / threads if site else float(size)
        add(at.work.writes if write else at.work.reads, key, at.times * moved)
        return True

    def call(self, e: Expr, at: Frame) -> bool:
        """A pipeline's operation, a warp shuffle or a fragment operation; False for anything else."""
        ref, times, threads = e.ref, at.times, self.block.count
        if isinstance(ref, tuple) and ref[:1] == ("stage",):
            stage = next(st for st in self.shape.stages if st.name == ref[1])
            if ref[2] == "fill":  # one cp.async a thread for each element it owns, of a whole stage at most
                moved = stage.elements * stage.element_bytes / threads
                add(at.work.reads, path(e.args[1]) if len(e.args) > 3 else path(e.args[0]), times * moved)
                add(at.work.writes, f"shared {stage.name}", times * moved)
                at.work.op("fill_bytes", times * moved)
                at.work.op("cp_async", times * (stage.elements / threads))
                at.work.op("shared_wavefront", times * (stage.stage_bytes / PHASE_BYTES / threads))
            elif ref[2] == "wait":  # cp.async.wait_group N, then the block's barrier
                at.work.op("stage_wait", times)
                at.work.op("barrier", times)
                stage.waits[e.line] = ref[3] if len(ref) > 3 else 0
            return True
        if not (isinstance(ref, tuple) and ref[:1] == ("builtin",)):
            return False
        if e.val in SHUFFLES:
            at.work.op("shuffle", times)
            self.shape.collectives[e.line] = e.val
            return True
        if e.val == "mma_unordered" and len(e.args) == 3:  # one warp's tensor-core step
            found = fragments.fragment(e.args[1].ty)
            if found is None:
                return False
            family, _, element, (m, n, k) = found
            at.work.op("tensor", times * (2.0 * m * n * k / WARP_SIZE))
            at.work.op("mma", times)
            self.shape.fragments[e.line] = f"{family} {m} x {n} x {k} {element}"
            return True
        if e.val in fragments.OPERATIONS and isinstance(ref, tuple) and len(ref) == 3:
            family, role, element, shape, _, shared = ref[2]
            rows, cols = fragments.extent(role, shape)
            moved = rows * cols * (4 if role == "acc" else self.k.c.sizeof(Type(element)))
            array = root(e.args[0]).val if root(e.args[0]).tag == "name" else path(e.args[0])
            write = e.val == "mma_store"
            if shared:
                at.work.op("shared_wavefront", times * (moved / PHASE_BYTES / WARP_SIZE))
                add(at.work.writes if write else at.work.reads, f"shared {array}", times * (moved / WARP_SIZE))
            else:
                add(at.work.writes if write else at.work.reads, array, times * (moved / WARP_SIZE))
            at.work.op("fragment_move", times)
            self.shape.fragments.setdefault(e.line, f"{e.val} {family} {rows} x {cols}")
            return True
        return False


def region(k: Any, s: Stmt, at: Frame) -> None:
    """Count one cooperative region: its blocks, and one thread's work in its body."""
    block = s.ref
    count = int(s.op)
    binders = [n.val for n in s.other_names]
    blocks = ONE
    for e in s.exprs[:count]:
        blocks = blocks * (k.size(e) or Poly.var(f"?blocks@{s.line}"))
    found = census(k.c, s) if block.device else None
    tally = Tally(k, s, found)
    body = Work()
    saved = k.coop
    k.coop = tally
    k.bound += binders
    k.block(s.body, Frame(body, ONE, (*at.binders, *binders), True))
    del k.bound[-len(binders) :]
    k.coop = saved
    shape = tally.shape
    if not block.device:
        k.cost.unknown.append(f"line {s.line}: a cooperative region on host threads starts two teams of "
                              f"{block.count} threads and meets at std::barrier, whose waits nothing measured")  # fmt: skip
    elif shape.census:
        k.note(f"line {s.line}: the census of the cooperative region did not run ({shape.census}); its accesses are "
               "priced a sector a lane and without bank conflicts")  # fmt: skip
    for line in sorted(shape.vague):
        k.note(f"line {line}: the lanes of this access differ by a symbol or by data, so it is priced a sector a "
               "lane, or without bank conflicts in shared memory")  # fmt: skip
    k.cost.regions.append(Region("cooperative", s.line, blocks, at.times, body, coop=shape))

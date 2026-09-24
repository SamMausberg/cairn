"""What the checker knows about the function it is inside, and about one parallel region in it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tree import Expr, Function, Type


@dataclass
class Scope:
    """Everything the checker knows about the function it is inside."""

    f: Function
    tenv: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Binding] = field(default_factory=dict)
    effects: set[str] = field(default_factory=set)
    callset: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=dict)
    moved: set[str] = field(default_factory=set)
    deferred: set[str] = field(default_factory=set)
    loop_depth: int = 0
    unsafe_depth: int = 0
    device_depth: int = 0
    module: str = ""
    lanes: Lanes | None = None
    coop: Block | None = None  # The cooperative region being checked (compiler/cooperative.py); its lanes are threads.
    closure: tuple[Function, set[str]] | None = None  # (the closure, names bound outside it)
    leases: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # ticket -> [(place, mode)]
    before: dict[str, set[str]] = field(default_factory=dict)  # device ticket -> tickets its work is queued after
    spawning: str = ""  # The name a `let t = spawn ...` is about to bind.
    touched: list[tuple[str, str, bool, Any]] | None = None  # Every access of the loop body being checked.
    facts: list[tuple[str, str, int]] = field(default_factory=list)  # (x, y, k): x - y <= k, in scope (facts.py)
    values: dict[str, tuple[Any, Any]] = field(default_factory=dict)  # name -> (binding, the usize it was bound to)
    cited: list[tuple[str, str, int]] = field(default_factory=list)  # The facts the last decision rested on.
    discharged: dict[str, int] = field(default_factory=dict)  # Guard sites whose condition was established.


@dataclass
class Lanes:
    """One data-parallel region: lanes may only touch element `binder` of what any lane writes."""

    binder: str
    outer: set[str]
    home: Any = None  # The closure the region began in: `return` may leave a newer closure, never the lane.
    accesses: list[tuple[str, int | None, bool, Expr]] = field(default_factory=list)  # root, block stride, write
    atomics: list[tuple[str, Expr]] = field(default_factory=list)  # root, element: atomic updates (atomics.py)


@dataclass
class Block:
    """One cooperative region, `blocks b in G threads t in T { }`: every block runs T threads that share its arrays
    and meet at its barriers (compiler/cooperative.py)."""

    grid: list[str]  # the block names, fastest first
    threads: list[str]  # the thread names, fastest first
    extents: list[int]  # each thread name's extent: constants, whose product is whole warps
    device: bool
    top: set[int] = field(default_factory=set)  # ids of the statements directly in the body, where arrays are declared
    shared: dict[str, tuple[Type, int, int]] = field(default_factory=dict)  # array -> (element, count, byte offset)
    pipelines: dict[str, Any] = field(default_factory=dict)  # name -> its stages (compiler/pipelines.py)
    reach: dict[int, tuple[int, Any]] = field(default_factory=dict)  # id(node) -> (who reaches it together, why)
    levels: dict[int, tuple[int, Any]] = field(default_factory=dict)  # id(call argument) -> (how widely shared, why)
    bytes: int = 0  # shared memory each block holds: its arrays and stages, each on 16 bytes

    @property
    def count(self) -> int:
        """Threads in one block."""
        total = 1
        for n in self.extents:
            total *= n
        return total


@dataclass
class Binding:
    ty: Type
    mutable: bool = False
    constant: int | None = None
    depth: int = 0  # Loop depth at declaration: an outer owner cannot be moved inside a loop.


SCOPED = frozenset(Scope.__dataclass_fields__)

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
    closure: tuple[Function, set[str]] | None = None  # (the closure, names bound outside it)
    leases: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # ticket -> [(place, mode)]
    before: dict[str, set[str]] = field(default_factory=dict)  # device ticket -> tickets its work is queued after
    spawning: str = ""  # The name a `let t = spawn ...` is about to bind.


@dataclass
class Lanes:
    """One data-parallel region: lanes may only touch element `binder` of what any lane writes."""

    binder: str
    outer: set[str]
    home: Any = None  # The closure the region began in: `return` may leave a newer closure, never the lane.
    accesses: list[tuple[str, bool, bool, Expr]] = field(default_factory=list)  # root, at binder, write


@dataclass
class Binding:
    ty: Type
    mutable: bool = False
    constant: int | None = None
    depth: int = 0  # Loop depth at declaration: an outer owner cannot be moved inside a loop.


SCOPED = frozenset(Scope.__dataclass_fields__)

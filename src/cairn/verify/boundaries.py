"""Valid boundary inputs for one function, generated from its contract.

The contract is what the compiler already knows: the signature, which `usize` parameters are extents, an
implementation's `when`, the literal tiles its body indexes by, and the plan items that shape its regions (grain,
vector width, stage, block, fuse) with the lane pool's own cutoff. From those come the sizes at a tile minus one,
the tile and the tile plus one, zero and one where the domain admits them, a partial final tile, views that start one
element past an aligned allocation, and values at the edges of each parameter's type or of the host's domain.

These are inputs only. What a function should do with them comes from its reference when the cases run
(verify/validation.py); nothing here states an expected result.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field
from typing import Any

from ..compiler.concurrency import walk as statements
from ..compiler.tree import BITS, FLOAT, SIGNED, Expr, Function, Program, is_view

CUTOFF = 16384  # runtime/cairn_parallel.hpp lanes::CUTOFF: below it a host region is the plain loop
GRAIN = 8192  # lanes::GRAIN: indices a lane is engaged for
SCALARS = {"bool", *BITS, *FLOAT}
PATTERNS = ("zeros", "ones", "ascending", "largest", "alternating", "random")


class Unsupported(Exception):
    """A signature the validator cannot feed: it is `unknown`, never passed."""


@dataclass
class Param:
    name: str
    kind: str  # "extent", "scalar" or "view"
    ty: str  # the scalar type, or a view's element type
    extent: str = ""  # a view's extent: a parameter's name or a literal
    mode: str = "value"  # a view's ro or rw


@dataclass
class Case:
    args: dict[str, Any]  # each scalar's value and each view's elements
    offsets: dict[str, int] = field(default_factory=dict)  # elements a view starts past an aligned allocation
    why: str = ""  # the boundary it exercises

    def key(self) -> str:
        import json

        return json.dumps([self.args, self.offsets], sort_keys=True)


def signature(f: Function, device: bool = False) -> list[Param]:
    """The parameters as the validator feeds them, or Unsupported with the reason. A device view is fed only to the
    device tests `make gpu` runs (`device`)."""
    views = {t.extent for _, t in f.params if is_view(t)}
    out = []
    for n, t in f.params:
        if is_view(t):
            if t.name not in SCALARS or t.args:
                raise Unsupported(f"{n} is a view of {t.value.display()}; the validator feeds views of scalars.")
            if t.place == "device" and not device:
                raise Unsupported(f"{n} lives on the device, and nothing runs on a device outside make gpu.")
            out.append(Param(n, "view", t.name, t.extent, t.mode))
        elif t.mode != "value" or t.name not in SCALARS:
            raise Unsupported(f"{n} is {t.display()}; the validator feeds scalars and views of scalars.")
        else:
            out.append(Param(n, "extent" if t.name == "usize" and n in views else "scalar", t.name))
    return out


def limits(ty: str) -> tuple[Any, Any]:
    if ty == "bool":
        return False, True
    if ty in FLOAT:
        return -3.0e38 if ty == "f32" else -1.0e300, 3.0e38 if ty == "f32" else 1.0e300
    bits = BITS[ty]
    return (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1) if ty in SIGNED else (0, 2**bits - 1)


def edges(ty: str, lo: Any = None, hi: Any = None) -> list[Any]:
    """A type's edge values inside [lo, hi], most telling first."""
    if ty == "bool":
        return [False, True]
    low, high = limits(ty)
    lo, hi = low if lo is None else max(lo, low), high if hi is None else min(hi, high)
    if ty in FLOAT:
        found = [0.0, 1.0, -1.0, 0.5, -0.0, lo, hi, 1.0e-30, 3.0, 1.0e7]
    else:
        found = [0, 1, lo, hi, hi - 1, lo + 1, 2, -1, (lo + hi) // 2]
    return list({repr(v): v for v in found if lo <= v <= hi}.values())  # by repr: -0.0 is its own edge, not 0.0


def literals(e: Any) -> list[tuple[str, int]]:
    """(operator, literal) of each binary node with an integer literal operand, in an expression or a body. An
    instance's natural (`K` of `total_by[8]`) is the literal it stands for there."""
    found: list[tuple[str, int]] = []
    todo = [e] if isinstance(e, Expr) else []
    for s in [] if isinstance(e, Expr) else statements(e):
        todo += s.exprs
    while todo:
        x = todo.pop()
        if x.tag == "binary":
            for a in x.args:
                if a.tag == "int":
                    found.append((x.val, int(a.val, 0)))
                elif a.tag == "name" and isinstance(a.ref, int) and not isinstance(a.ref, bool):
                    found.append((x.val, a.ref))
        todo += x.args
    return found


def tiles(p: Program, f: Function, plan: dict[str, int] | None = None) -> dict[int, str]:
    """Each size a boundary sits at, and what put it there: the `when`, the body's index arithmetic, the plan, the
    blocks a lane owns and the lane pool's cutoff."""
    out: dict[int, str] = {}

    def add(size: int, why: str) -> None:
        if 1 < size <= 1 << 20:
            out.setdefault(size, why)

    clause = f.implements
    if clause is not None and clause.when is not None:
        for op, k in literals(clause.when):
            add(k, f"the condition's {op} {k}")
    for op, k in literals(f.body):
        if op in {"*", "/", "%"}:
            add(k, f"the body's {op} {k}")
    for item, value in (plan or {}).items():
        if item in {"vector", "block", "grain", "stage", "fuse", "per_lane"}:
            add(value, f"plan {item} {value}")
    regions = [s for s in statements(f.body) if s.tag == "parallel" or (s.tag in {"reduce", "scan"} and s.pooled)]
    for s in regions:
        add(s.block, f"a lane's block of {s.block}")
    if any(s.ref == "host" for s in regions):
        add(CUTOFF, "the lane pool's cutoff")
    return out


def sizes(found: dict[int, str], least: int, most: int) -> list[tuple[int, str]]:
    """Extents at each tile minus one, the tile, plus one and a partial second tile, then zero, one and the
    greatest the domain admits."""
    out: dict[int, str] = {}
    for n, why in [(0, "empty"), (1, "one element"), (2, "two elements"), (3, "three elements")]:
        out[n] = why
    for t, why in sorted(found.items()):
        for n, how in [(t - 1, "a tile minus one"), (t, "one tile"), (t + 1, "a tile plus one"),
                       (2 * t - 1, "a partial second tile"), (2 * t, "two tiles"), (3 * t + 1, "a tile plus one after three")]:  # fmt: skip
            out.setdefault(n, f"{how}, {t} from {why}")
    out.setdefault(most, "the largest extent the domain admits")
    return [(n, why) for n, why in sorted(out.items()) if least <= n <= most]


def pattern(ty: str, n: int, kind: str, rng: random.Random, lo: Any = None, hi: Any = None) -> list[Any]:
    low, high = limits(ty)
    lo, hi = low if lo is None else max(lo, low), high if hi is None else min(hi, high)
    if ty == "bool":
        return [bool(i % 2) if kind == "alternating" else kind in {"ones", "largest"} for i in range(n)]
    if kind == "zeros":
        return [clamp(0, lo, hi, ty)] * n
    if kind == "ones":
        return [clamp(1, lo, hi, ty)] * n
    if kind == "ascending":
        return [clamp(i, lo, hi, ty) for i in range(n)]
    if kind == "largest":
        return [hi] * n
    if kind == "alternating":
        return [hi if i % 2 else lo for i in range(n)]
    if ty in FLOAT:
        return [rng.uniform(-1000.0, 1000.0) if lo < -1000 < 1000 < hi else rng.uniform(lo, hi) for _ in range(n)]
    return [rng.randint(lo, hi) for _ in range(n)]


def clamp(v: Any, lo: Any, hi: Any, ty: str) -> Any:
    v = min(max(v, lo), hi)
    return float(v) if ty in FLOAT else v


def generate(f: Function, found: dict[int, str], domain: dict[str, Any], budget: int = 256, seed: int = 0,
             device: bool = False) -> list[Case]:  # fmt: skip
    """Cases for `f`: every extent size with each view pattern, the scalar parameters' edges crossed with one another
    (so two of them meet at the same value), views placed one element off an aligned allocation, then random ones,
    within `budget`. The same inputs come out for the same seed."""
    params = signature(f, device)
    rng = random.Random(seed)
    extents = [p for p in params if p.kind == "extent"]
    bounds = domain.get("extents", {})
    ranges = {p.name: bounds.get(p.name, [0, domain.get("largest_extent", 4096)]) for p in extents}
    per = {p.name: sizes(found, int(ranges[p.name][0]), int(ranges[p.name][1])) for p in extents}
    values = domain.get("values", {})
    scalars = {p.name: edges(p.ty, *values.get(p.name, [None, None])) for p in params if p.kind == "scalar"}
    width = math.prod(len(v) for v in scalars.values())
    if width <= 4 * budget:
        combos = list(itertools.product(*scalars.values()))
    else:  # too many to cross: the diagonal of each value against each other's, then random picks
        combos = [tuple(v[(i + k) % len(v)] for k, v in enumerate(scalars.values())) for i in range(budget)]
        combos += [tuple(rng.choice(v) for v in scalars.values()) for _ in range(budget)]
    rounds = max([len(v) for v in per.values()] + [1])
    cases: list[Case] = []
    seen: set[str] = set()
    turn = iter(itertools.cycle(range(len(combos))))

    def one(step: int, kind: str, offset: int, pick: int | None = None) -> None:
        chosen = {p.name: per[p.name][(step + i) % len(per[p.name])] for i, p in enumerate(extents) if per[p.name]}
        if len(chosen) != len(extents):
            return
        args: dict[str, Any] = {n: size for n, (size, _) in chosen.items()}
        args |= dict(zip(scalars, combos[next(turn) if pick is None else pick], strict=True))
        for p in params:
            if p.kind == "view":
                count = int(p.extent) if p.extent.isdigit() else args[p.extent]
                args[p.name] = pattern(p.ty, count, kind, rng, *values.get(p.name, [None, None]))
        why = "; ".join(f"{n} = {size}: {how}" for n, (size, how) in chosen.items()) or "fixed extents"
        offsets = {p.name: offset for p in params if p.kind == "view"} if offset else {}
        case = Case(args, offsets, f"{why}; {kind} elements" + (f"; views {offset} element off" if offset else ""))
        if case.key() not in seen and len(cases) < budget:
            seen.add(case.key())
            cases.append(case)

    for step in range(rounds):
        for kind in PATTERNS[:2]:
            one(step, kind, 0)
    for pick in range(min(len(combos), budget // 2)) if scalars else ():
        one(pick % rounds, "ascending", 0, pick)
    for step in range(rounds):
        for kind in PATTERNS[2:]:
            one(step, kind, 0)
    for step in range(rounds):
        one(step, "ascending", 1)
    for _ in range(budget):
        one(rng.randrange(rounds), "random", rng.choice([0, 1]))
    return cases

#!/usr/bin/env python3
"""Differential check: `checking.py` and the Lean ownership calculus on the same generated programs.

`proofs/Cairn/Ownership.lean` is a hand-written model of the rules `src/cairn/compiler/checking.py`
enforces.  Nothing extracts one from the other, so the two can drift.  This harness
generates programs in the fragment both of them understand, renders each one TWICE from a
single intermediate -- once as CAIRN source, once as a Lean `Program` literal -- and requires
that the Python checker accepts exactly the programs the Lean `accepts` accepts.

What that establishes: the two checkers classify the same generated programs identically,
modulo the hand-written rendering below.  It is stronger than review, and it catches drift at
a scale review does not reach.  It is NOT extraction: the rendering is the thing being trusted,
and a shape absent from the fragment is not compared at all.

The fragment is `FRAGMENT` and `EXCLUDED` below, and it is deliberately narrow.  Widen it only
when the current one is green.

The programs are checked and never built or run, which is why a backwards part and a view
extent that does not match its part are emitted freely: both are legal to the checker and trap
at run time, and only the checker's answer is compared.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cairn.compiler.cairnc import Diagnostic, compile_program

PROOFS = ROOT / "proofs"
ELAN_BIN = Path.home() / ".elan" / "bin"

# One row per shape the generator may emit.  The CAIRN column is what `render_cairn` writes and
# the Lean column is what `render_lean` writes, from the same intermediate statement.
FRAGMENT = (
    ("a heap owner", "let mut dK = Buf[u64](n);", "Stmt.alloc x"),
    ("a copyable scalar", "let mut sK:u64 = 0;", "Stmt.mkScalar x"),
    ("a scalar copy", "let mut cK = sJ;", "Stmt.copy y x"),
    ("an owner move", "let mut mK = dJ;", "Stmt.move y x"),
    ("a length read", "let kK = len(dJ);", "Stmt.call [(Place.hdr r, Mode.ro)]"),
    ("a call", "hSIG(args);", "Stmt.call args"),
    ("a task", "let tK = spawn hSIG(args);", "Stmt.spawn t args"),
    ("a join", "wait(tK);", "Stmt.wait t"),
    ("two paths", "if flag { ... } else { ... }", "Stmt.ite thn els"),
    ("a region", "parallel i in n { ... }", "Stmt.parallel n body"),
    ("a lane's own element", "dJ[i] = 1;", "Touch.elem r Mode.rw"),
    ("any other element", "dJ[0] = 1;", "Touch.other r Mode.rw"),
    ("a local of the enclosing scope", "sJ = 1;", "Touch.whole r Mode.rw"),
    ("a length a lane reads", "let lvK = len(dJ);", "Touch.len r"),
    ("the whole owner", "dJ passed to rw<Buf[u64]>", "Place.whole r"),
    ("a shared scalar", "sJ passed to rw<u64>", "Place.whole r"),
    ("every element", "len(dJ), dJ passed to rw<u64>[n]", "Place.elems r"),
    ("a part", "hi - lo, dJ[lo..hi] passed to rw<u64>[n]", "Place.part r lo hi"),
    ("a backwards part", "dJ[hi..lo], which cr::part traps on", "Place.part r hi lo"),
    ("a visible bound", "0, 2, 4, 8 and the immutable a, b, n", "Bound.lit / Bound.nm"),
)

# What the generator never emits, because the two sides would not be comparable.
EXCLUDED = (
    "record fields and field paths (`box.a`): the Lean model needs the header read that "
    "`checking.py:e_field` charges written out as its own statement, which is a second rendering rule",
    "a lane that calls anything, `lane:f` callbacks, a nested region, and a region inside a branch",
    "`drop x`: CAIRN has no statement for it; it is the implicit release at an inner scope's exit",
    "device work ordered by `after`, which `e_spawn` exempts from the lease rule and the model has no streams for",
    "closures and their captures, placement, `reduce`, `compact`",
    "`take`, `swap`, loops, `return` inside a branch, `break`, `continue`, traits, generics",
    "re-binding a local: every name is declared exactly once, by one alloc, scalar, copy or move",
    "`spawn` and `wait` inside a branch, and a branch-local name used after the branch: "
    "CAIRN blocks are scopes and the Lean model has one flat scope",
)

# The diagnostics the Lean calculus models.  Anything else means the generator left the fragment,
# which is a failure of this harness, not a disagreement between the two checkers.
MODELLED_CODES = frozenset(
    {"E-LEASED", "E-ALIAS", "E-MOVED", "E-LINEAR-LEAK", "E-LINEAR-BRANCH", "E-PARALLEL-RACE", "E-PARALLEL-WRITE"}
)

# The bounds a part may carry, in increasing order of the value each one has in the rendered source.
BOUNDS = ("0", "2", "a", "4", "b", "8", "n")
BOUND_LEAN = {"0": "Bound.lit 0", "2": "Bound.lit 2", "4": "Bound.lit 4", "8": "Bound.lit 8",
              "a": "Bound.nm 0", "b": "Bound.nm 1", "n": "Bound.nm 2"}  # fmt: skip

PRELUDE = "fn opaque() -> bool = true;\n"
HEADER = "  let a:usize = 2;\n  let b:usize = 5;\n  let n:usize = 8;\n  let flag = opaque();\n"


@dataclass(frozen=True)
class Place:
    """What one borrow names: `whole` for an owner or a scalar, `elems`, `part` or `hdr`."""

    kind: str
    var: str
    lo: str = ""
    hi: str = ""


@dataclass(frozen=True)
class Alloc:
    var: str


@dataclass(frozen=True)
class MkScalar:
    var: str


@dataclass(frozen=True)
class Copy:
    dst: str
    src: str


@dataclass(frozen=True)
class Move:
    dst: str
    src: str


@dataclass(frozen=True)
class Len:
    dst: str
    var: str


@dataclass(frozen=True)
class Call:
    borrows: tuple[tuple[Place, str], ...]


@dataclass(frozen=True)
class Spawn:
    ticket: str
    borrows: tuple[tuple[Place, str], ...]


@dataclass(frozen=True)
class Wait:
    ticket: str


@dataclass(frozen=True)
class Ite:
    thn: tuple
    els: tuple


@dataclass(frozen=True)
class Region:
    """`parallel i in n { ... }`, as the list of accesses `checking.py:region` keeps.

    One access per statement of the rendered body, so the CAIRN source and the Lean `Touch`
    list carry the same accesses in the same order.
    """

    touches: tuple[tuple[str, str, str], ...]  # (elem | other | whole | len, local, ro | rw)


@dataclass(frozen=True)
class Prog:
    """One generated program: the locals it declares, the tickets it spawns, and its body."""

    name: str
    variables: tuple[str, ...]
    tickets: tuple[str, ...]
    body: tuple


def family(place: Place, scalars: frozenset[str]) -> str:
    """Which parameter shape a borrow needs: a whole owner, a shared scalar, or an array view."""
    if place.kind != "whole":
        return "view"
    return "scalar" if place.var in scalars else "owner"


def helper(shapes: tuple[tuple[str, str], ...]) -> tuple[str, str]:
    """The name and the definition of the callee that takes exactly this footprint."""
    params = []
    for index, (shape, mode) in enumerate(shapes):
        if shape == "view":
            params.append(f"n{index}:usize")
            params.append(f"a{index}:{mode}<u64>[n{index}]")
        elif shape == "owner":
            params.append(f"b{index}:{mode}<Buf[u64]>")
        else:
            params.append(f"v{index}:{mode}<u64>")
    name = "h_" + "_".join(shape[0] + mode for shape, mode in shapes)
    return name, "fn " + name + "(" + ", ".join(params) + ") { }\n"


def arguments(borrows: tuple[tuple[Place, str], ...]) -> str:
    """The argument list, with the extent each view parameter needs in front of it."""
    written: list[str] = []
    for place, _ in borrows:
        if place.kind == "whole":
            written.append(place.var)
        elif place.kind == "elems":
            written.append("len(" + place.var + ")")  # The extent of a whole owner is its length.
            written.append(place.var)
        else:
            written.append(place.hi if place.lo == "0" else place.hi + " - " + place.lo)
            written.append(place.var + "[" + place.lo + ".." + place.hi + "]")
    return ", ".join(written)


def header_reads(borrows: tuple[tuple[Place, str], ...]) -> tuple[str, ...]:
    """The owners whose length the rendered CAIRN reads to state a view's extent.

    `len(d)` is charged by `check_len` as `leased(d, "ro", elements=False)`, which is `Place.hdr`.
    The Lean rendering writes it out as its own statement in front of the call; nothing changes
    between the arguments of one call, so hoisting it cannot move a verdict.
    """
    return tuple(place.var for place, _ in borrows if place.kind == "elems")


def lane_statement(kind: str, var: str, mode: str, position: int) -> str:
    """One lane access, as one CAIRN statement, so the body and the `Touch` list line up.

    `e_index` records the root, whether the index is the binder, and whether it writes; `len` is
    not recorded at all; and assigning a local of the enclosing scope is `E-PARALLEL-WRITE`.
    """
    private = "lv" + str(position)
    if kind == "len":
        return "let " + private + " = len(" + var + ");"
    if kind == "whole":
        return var + " = 1;" if mode == "rw" else "let " + private + " = " + var + ";"
    index = "i" if kind == "elem" else "0"
    read = var + "[" + index + "]"
    return read + " = 1;" if mode == "rw" else "let " + private + " = " + read + ";"


def render_cairn(prog: Prog, scalars: frozenset[str]) -> str:
    """The CAIRN source for one generated program."""
    helpers: dict[str, str] = {}

    def statements(body: tuple, indent: str) -> list[str]:
        lines = []
        for statement in body:
            if isinstance(statement, Alloc):
                lines.append(indent + "let mut " + statement.var + " = Buf[u64](n);")
            elif isinstance(statement, MkScalar):
                lines.append(indent + "let mut " + statement.var + ":u64 = 0;")
            elif isinstance(statement, Copy | Move):  # One form: which it is depends on what `src` holds.
                lines.append(indent + "let mut " + statement.dst + " = " + statement.src + ";")
            elif isinstance(statement, Len):
                lines.append(indent + "let " + statement.dst + " = len(" + statement.var + ");")
            elif isinstance(statement, Wait):
                lines.append(indent + "wait(" + statement.ticket + ");")
            elif isinstance(statement, Ite):
                lines.append(indent + "if flag {")
                lines.extend(statements(statement.thn, indent + "  "))
                lines.append(indent + "} else {")
                lines.extend(statements(statement.els, indent + "  "))
                lines.append(indent + "}")
            elif isinstance(statement, Region):
                lines.append(indent + "parallel i in n {")
                for position, (kind, var, mode) in enumerate(statement.touches):
                    lines.append(indent + "  " + lane_statement(kind, var, mode, position))
                lines.append(indent + "}")
            else:
                borrows = statement.borrows
                shapes = tuple((family(place, scalars), mode) for place, mode in borrows)
                name, definition = helper(shapes)
                helpers[name] = definition
                call = name + "(" + arguments(borrows) + ");"
                if isinstance(statement, Spawn):
                    call = "let " + statement.ticket + " = spawn " + call
                lines.append(indent + call)
        return lines

    body = statements(prog.body, "  ")
    text = "".join(sorted(helpers.values())) + PRELUDE
    return text + "fn main() -> i32 {\n" + HEADER + "\n".join(body) + "\n  return 0;\n}\n"


def lean_place(place: Place, numbering: dict[str, int]) -> str:
    root = "⟨" + str(numbering[place.var]) + ", []⟩"
    if place.kind == "part":
        return "Place.part " + root + " (" + BOUND_LEAN[place.lo] + ") (" + BOUND_LEAN[place.hi] + ")"
    return "Place." + place.kind + " " + root


def lean_borrows(borrows: tuple[tuple[Place, str], ...], numbering: dict[str, int]) -> str:
    return "[" + ", ".join("(" + lean_place(p, numbering) + ", Mode." + m + ")" for p, m in borrows) + "]"


def render_lean(prog: Prog, numbering: dict[str, int], tickets: dict[str, int]) -> str:
    """The Lean `Program` literal for one generated program."""

    def statements(body: tuple) -> list[str]:
        written: list[str] = []
        for statement in body:
            if isinstance(statement, Alloc):
                written.append("Stmt.alloc " + str(numbering[statement.var]))
            elif isinstance(statement, MkScalar):
                written.append("Stmt.mkScalar " + str(numbering[statement.var]))
            elif isinstance(statement, Copy):
                written.append("Stmt.copy " + str(numbering[statement.dst]) + " " + str(numbering[statement.src]))
            elif isinstance(statement, Move):
                written.append("Stmt.move " + str(numbering[statement.dst]) + " " + str(numbering[statement.src]))
            elif isinstance(statement, Len):
                written.append("Stmt.call [(Place.hdr ⟨" + str(numbering[statement.var]) + ", []⟩, Mode.ro)]")
            elif isinstance(statement, Wait):
                written.append("Stmt.wait " + str(tickets[statement.ticket]))
            elif isinstance(statement, Ite):
                written.append("Stmt.ite [" + ", ".join(statements(statement.thn)) + "] ["
                               + ", ".join(statements(statement.els)) + "]")  # fmt: skip
            elif isinstance(statement, Region):
                touches = ", ".join(
                    "Touch." + kind + " ⟨" + str(numbering[var]) + ", []⟩" + ("" if kind == "len" else " Mode." + mode)
                    for kind, var, mode in statement.touches
                )
                written.append("Stmt.parallel (Bound.nm 2) [" + touches + "]")
            else:
                for owner in header_reads(statement.borrows):
                    written.append("Stmt.call [(Place.hdr ⟨" + str(numbering[owner]) + ", []⟩, Mode.ro)]")
                borrows = lean_borrows(statement.borrows, numbering)
                if isinstance(statement, Spawn):
                    written.append("Stmt.spawn " + str(tickets[statement.ticket]) + " " + borrows)
                else:
                    written.append("Stmt.call " + borrows)
        return written

    scope = "[" + ", ".join(str(numbering[v]) for v in prog.variables) + "]"
    return "def " + prog.name + " : Program := ⟨" + scope + ", [" + ", ".join(statements(prog.body)) + "]⟩"


class Generator:
    """One seeded walk through the fragment.  Nothing here tries to produce accepted programs."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.owners: list[str] = []
        self.scalars: list[str] = []
        self.variables: list[str] = []
        self.tickets: list[str] = []
        self.live: list[str] = []
        self.fresh = 0

    def name(self, prefix: str) -> str:
        self.fresh += 1
        return prefix + str(self.fresh)

    def place(self) -> Place:
        """One place of a declared local: a whole owner or scalar, its elements, or a part of it."""
        if self.scalars and self.rng.random() < 0.25:
            return Place("whole", self.rng.choice(self.scalars))
        var = self.rng.choice(self.owners)
        kind = self.rng.choices(("whole", "elems", "part"), weights=(2, 3, 5))[0]
        if kind != "part":
            return Place(kind, var)
        first, second = self.rng.sample(range(len(BOUNDS)), 2)
        if self.rng.random() < 0.85:  # The rest are backwards, which `cr::part` traps on and the checker allows.
            first, second = min(first, second), max(first, second)
        return Place("part", var, BOUNDS[first], BOUNDS[second])

    def borrows(self, count: int) -> tuple[tuple[Place, str], ...]:
        return tuple((self.place(), self.rng.choice(("ro", "rw"))) for _ in range(count))

    def branch_body(self) -> tuple:
        body: list = []
        for _ in range(self.rng.randint(1, 2)):
            choice = self.rng.random()
            if choice < 0.5:
                body.append(Call(self.borrows(self.rng.randint(1, 2))))
            elif choice < 0.7:
                body.append(Len(self.name("k"), self.rng.choice(self.owners)))
            elif choice < 0.9:
                target = self.name("m")  # Declared inside the branch, so never named after it.
                self.variables.append(target)
                body.append(Move(target, self.rng.choice(self.owners)))
            elif self.scalars:
                source = self.rng.choice(self.scalars)
                target = self.name("c")  # Likewise: a branch is a scope, so it stays out of the pools.
                self.variables.append(target)
                body.append(Copy(target, source))
        return tuple(body)

    def build(self, name: str) -> Prog:
        body: list = []
        for _ in range(self.rng.randint(1, 3)):
            var = self.name("d")
            self.owners.append(var)
            self.variables.append(var)
            body.append(Alloc(var))
        for _ in range(self.rng.randint(0, 2)):
            var = self.name("s")
            self.scalars.append(var)
            self.variables.append(var)
            body.append(MkScalar(var))
        for _ in range(self.rng.randint(2, 7)):
            body.extend(self.step())
        for ticket in list(self.live):
            if self.rng.random() < 0.85:  # The rest leak, which both checkers must refuse.
                self.live.remove(ticket)
                body.append(Wait(ticket))
        return Prog(name, tuple(self.variables), tuple(self.tickets), tuple(body))

    def split(self) -> list:
        """A K-way split of one owner, which is the shape the chaining rule exists for.

        Cutting at `0`, `a`, `b`, `n` and handing the pieces out one at a time is the case
        `checking.py:overlaps` licenses by chaining the `lo <= hi` fact of the piece in between.
        Uniform random parts reach it too rarely to be evidence about `reaches`.
        """
        var = self.rng.choice(self.owners)
        cuts = sorted(self.rng.sample(range(len(BOUNDS)), self.rng.randint(3, 4)))
        pieces = [Place("part", var, BOUNDS[lo], BOUNDS[hi]) for lo, hi in itertools.pairwise(cuts)]
        self.rng.shuffle(pieces)
        if self.rng.random() < 0.5:
            return [Call(tuple((piece, "rw") for piece in pieces))]
        statements: list = []
        for piece in pieces:
            ticket = self.name("t")
            self.tickets.append(ticket)
            self.live.append(ticket)
            statements.append(Spawn(ticket, ((piece, "rw"),)))
        return statements

    def region(self) -> list:
        """One `parallel i in n { ... }`, as the accesses its body makes."""
        touches = []
        for _ in range(self.rng.randint(1, 3)):
            choice = self.rng.random()
            if choice < 0.15 and self.scalars:
                touches.append(("whole", self.rng.choice(self.scalars), self.rng.choice(("ro", "rw"))))
            elif choice < 0.25:
                touches.append(("len", self.rng.choice(self.owners), "ro"))
            else:
                kind = "elem" if self.rng.random() < 0.6 else "other"
                touches.append((kind, self.rng.choice(self.owners), self.rng.choice(("ro", "rw"))))
        return [Region(tuple(touches))]

    def step(self) -> list:
        choice = self.rng.random()
        if choice < 0.26:
            return [Call(self.borrows(self.rng.randint(1, 3)))]
        if choice < 0.45:
            ticket = self.name("t")
            self.tickets.append(ticket)
            self.live.append(ticket)
            return [Spawn(ticket, self.borrows(self.rng.randint(1, 2)))]
        if choice < 0.60:
            return self.split()
        if choice < 0.72:
            return self.region()
        if choice < 0.80 and self.live:
            ticket = self.rng.choice(self.live)
            self.live.remove(ticket)
            return [Wait(ticket)]
        if choice < 0.86:
            return [Len(self.name("k"), self.rng.choice(self.owners))]
        if choice < 0.92:
            target = self.name("m")
            self.variables.append(target)
            source = self.rng.choice(self.owners)
            self.owners.append(target)
            return [Move(target, source)]
        if choice < 0.96 and self.scalars:
            target = self.name("c")
            self.variables.append(target)
            source = self.rng.choice(self.scalars)
            self.scalars.append(target)
            return [Copy(target, source)]
        return [Ite(self.branch_body(), self.branch_body())]


def generate(count: int, seed: int) -> list[tuple[Prog, frozenset[str], dict[str, int], dict[str, int]]]:
    """`count` programs, each with the scalar set and the Lean numbering its renderings need."""
    built = []
    for index in range(count):
        generator = Generator(random.Random(seed * 1_000_003 + index))
        prog = generator.build("p" + str(index))
        numbering = {name: position for position, name in enumerate(prog.variables)}
        tickets = {name: position for position, name in enumerate(prog.tickets)}
        built.append((prog, frozenset(generator.scalars), numbering, tickets))
    return built


def python_verdict(source: str) -> tuple[bool, str]:
    """What `checking.py` says: accepted, or the diagnostic code that refused it."""
    try:
        compile_program(source)
    except Diagnostic as refusal:
        return False, refusal.data["code"]
    return True, ""


def find_lake() -> str | None:
    found = shutil.which("lake")
    if found:
        return found
    candidate = ELAN_BIN / "lake"
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def lean_environment() -> dict[str, str]:
    environment = dict(os.environ)
    if ELAN_BIN.is_dir():
        environment["PATH"] = str(ELAN_BIN) + os.pathsep + environment.get("PATH", "")
    return environment


def lean_source(rendered: list[str], names: list[str], chunk: int = 100) -> str:
    """One file: a `Program` literal per generated program, and one `#eval` per chunk of them.

    The chunks exist because a single list literal of a few thousand entries exhausts the
    elaborator's recursion depth; the verdicts are printed in source order either way.
    """
    lines = ["import Cairn.Ownership", "", "namespace CairnDifferential", "", "open Cairn.Ownership", ""]
    lines.extend(rendered)
    for start in range(0, len(names), chunk):
        group = names[start : start + chunk]
        lines.append("")
        lines.append("def verdicts" + str(start) + " : List (String × Bool) :=")
        lines.append("  [" + ", ".join('("' + name + '", accepts ' + name + ")" for name in group) + "]")
        lines.append("")
        lines.append("#eval show IO Unit from do")
        lines.append("  for (name, ok) in verdicts" + str(start) + " do")
        lines.append('    IO.println s!"{name} {if ok then "accept" else "reject"}"')
    lines.append("")
    lines.append("end CairnDifferential")
    return "\n".join(lines) + "\n"


def lean_verdicts(text: str, lake: str, target: Path, timeout: int) -> dict[str, bool]:
    """Run the generated file once and read one line per program.

    `lake env lean` needs `Cairn.Ownership` already compiled, so a first failure is answered by
    building `proofs/` once and trying again: that covers a checkout whose `.lake` is cold, and
    a build another process was part way through.  A second failure is reported, never ignored.
    """
    target.write_text(text, encoding="utf-8")
    command = [lake, "env", "lean", str(target)]
    done = subprocess.run(command, cwd=PROOFS, capture_output=True, text=True, env=lean_environment(), timeout=timeout)
    if done.returncode != 0:
        subprocess.run(
            [lake, "build"], cwd=PROOFS, capture_output=True, text=True, env=lean_environment(), timeout=timeout
        )
        done = subprocess.run(
            command, cwd=PROOFS, capture_output=True, text=True, env=lean_environment(), timeout=timeout
        )
    if done.returncode != 0:
        raise RuntimeError("lake env lean failed:\n" + done.stdout[-4000:] + done.stderr[-4000:])
    verdicts = {}
    for line in done.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1] in {"accept", "reject"}:
            verdicts[parts[0]] = parts[1] == "accept"
    return verdicts


def compare(count: int, seed: int, lake: str, target: Path, timeout: int) -> dict:
    built = generate(count, seed)
    sources = {}
    rendered = []
    for prog, scalars, numbering, tickets in built:
        sources[prog.name] = render_cairn(prog, scalars)
        rendered.append(render_lean(prog, numbering, tickets))
    names = [prog.name for prog, *_ in built]
    verdicts = lean_verdicts(lean_source(rendered, names), lake, target, timeout)
    missing = [name for name in names if name not in verdicts]
    if missing:
        raise RuntimeError("the Lean run printed no verdict for " + ", ".join(missing[:10]))

    disagreements, outside, codes = [], [], {}
    accepted = 0
    for (prog, *_rest), lean_line in zip(built, rendered, strict=True):
        source = sources[prog.name]
        ok, code = python_verdict(source)
        accepted += int(ok)
        codes[code or "accepted"] = codes.get(code or "accepted", 0) + 1
        if code and code not in MODELLED_CODES:
            outside.append({"program": prog.name, "code": code, "cairn": source, "lean": lean_line})
        elif ok != verdicts[prog.name]:
            disagreements.append(
                {
                    "program": prog.name,
                    "python": "accept" if ok else "reject:" + code,
                    "lean": "accept" if verdicts[prog.name] else "reject",
                    "cairn": source,
                    "lean_program": lean_line,
                }
            )
    status = "agreed"
    if outside:
        status = "outside-the-fragment"
    elif disagreements:
        status = "disagreed"
    return {
        "status": status,
        "programs": count,
        "seed": seed,
        "python_accepted": accepted,
        "python_rejected": count - accepted,
        "codes": dict(sorted(codes.items())),
        "disagreements": disagreements,
        "outside_the_fragment": outside,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=int(os.environ.get("CAIRN_DIFFERENTIAL_N", "60")))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--keep", type=Path, help="write the generated Lean file here instead of a scratch path")
    parser.add_argument("--fragment", action="store_true", help="print the fragment table and exit")
    options = parser.parse_args()

    if options.fragment:
        print(json.dumps({"fragment": [list(row) for row in FRAGMENT], "excluded": list(EXCLUDED)}, indent=2))
        return 0

    lake = find_lake()
    if lake is None:
        print(json.dumps({"status": "unavailable", "reason": "no lake on PATH and no ~/.elan/bin/lake"}, indent=2))
        return 3

    if options.keep:
        options.keep.parent.mkdir(parents=True, exist_ok=True)
        report = compare(options.count, options.seed, lake, options.keep, options.timeout)
    else:
        with tempfile.TemporaryDirectory(prefix="cairn-differential-") as scratch:
            target = Path(scratch) / "Differential.lean"
            report = compare(options.count, options.seed, lake, target, options.timeout)
    for entry in report["disagreements"] + report["outside_the_fragment"]:
        print("== " + entry["program"] + " ==", file=sys.stderr)
        print(entry["cairn"], file=sys.stderr)
        print(entry.get("lean_program", entry.get("lean", "")), file=sys.stderr)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "agreed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""One compile per distinct source in a process, shared by every tool that reads one: the edit, plan and
implementation hosts, `cairn state`, `cairn mcp`, `cairn lsp`, and `cairn predict` with the device inspection it
reads.

A compile is keyed by everything it reads: the source text, the compiler this process loaded (`compiler`), the
packaged `std` modules as they are on disk now (`library`, since each link reads them again), whether the checker
captures expression sites, and whether it reports every refusal. The device target and emulation are not inputs of a
compile: `projects/target.py` and `projects/emulation.py` judge its receipt afterwards.

What a compile made is kept as bytes, never as objects a caller holds: the checked program with its checker and
receipts as the check left them, pickled, and a refusal's record; and once a caller asks for one, the parse and the
emitted C++ with its manifest. Every caller unpickles its own copy, so a change one caller makes to what it got is never read by
another. A caller whose request ran a stage takes the objects that stage made, which nobody else has seen.

A check that captures sites answers one that does not, with the sites left out. An accepted program checks the same
with and without `every`, since compiler/check/refusals.py changes nothing until it keeps a refusal, so an accepted check
serves both; a refusal answers only the setting it was made under. The emission of an accepted program depends on
neither. A check a fault ended, and anything too deep to pickle, is answered and not kept.

An accepted check also keeps its walk over the bodies: the checker pickled as that walk left it, before anything after
it changed the checker, and where each function's body is. A source that differs from a kept one in one function's
body is checked from that walk, which checks that body alone and runs every rule after the walk again
(compiler/check/incremental.py), and answers what a whole check answers; an edit it does not take is checked whole.
A walk that recorded sites answers a check with or without them, and one without answers only a check without.

At most ENTRIES sources and BYTES bytes are kept, and the source used least recently goes first. The bytes counted are
the bytes held: every pickle, the C++ text and every refusal record. Objects a caller holds are not counted.
"""

from __future__ import annotations

import functools
import gc
import hashlib
import pickle
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cairnc import Checker, Diagnostic, Parser, Program, compile_program, generate, joined
from .check import incremental
from .check.incremental import Walk
from .lower.codegen import RUNTIME
from .syntax.modules import STD

ENTRIES = 16  # sources
BYTES = 256 << 20  # what their stages hold, in bytes
READ = ("verify/linear_certificates.py", "projects/target.py")  # read by a compile beside implementation_hash()'s files
Checked = tuple[Program, Checker, dict[str, Any]]


@functools.cache
def compiler() -> str:
    """The compiler this process loaded, as one digest: `implementation_hash()` (verify/scalar/semantics.py) of every
    parser, checker and emitter file, the other files a compile reads, and the runtime headers. It is read once, since
    a file changed on disk after the process imported it is not the code that runs."""
    from ..verify.scalar.semantics import implementation_hash

    root = Path(__file__).parents[1]
    parts = [implementation_hash().encode(), RUNTIME.encode(), *((root / name).read_bytes() for name in READ)]
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def library() -> str:
    """The packaged `std` modules as they are on disk now, as one digest."""
    h = hashlib.sha256()
    for path in sorted(STD.rglob("*.cairn")):
        h.update(path.relative_to(STD).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def dumps(value: Any) -> bytes | None:
    """`value` pickled, or None when it is too deep to pickle, which leaves it unkept."""
    try:
        return pickle.dumps(value, pickle.HIGHEST_PROTOCOL)
    except RecursionError:
        return None


def loads(data: bytes) -> Any:
    """A copy from its pickle, with the collector paused: unpickling a checked program makes objects far faster than a
    collection pass can use them, and paused it takes about a quarter of the time on a large project."""
    running = gc.isenabled()
    gc.disable()
    try:
        return pickle.loads(data)
    finally:
        if running:
            gc.enable()


class Refused:
    """A stage's refusal, kept as its record and raised again as a new Diagnostic."""

    def __init__(self, error: Diagnostic):
        self.data = pickle.dumps(error.data, pickle.HIGHEST_PROTOCOL)
        self.size = len(self.data)

    def raised(self) -> Diagnostic:
        return Diagnostic.of(loads(self.data))


class Parsed:
    """A parse, pickled, and where its bodies are (incremental.bodies), which says whether another source differs from
    this one in one body alone."""

    def __init__(self, tree: Program, data: bytes):
        self.data, self.spans, self.size = data, incremental.bodies(tree), len(data)


class Pickled:
    """A stage's objects, pickled; a check keeps its sites apart, so a caller that wants none unpickles none."""

    def __init__(self, data: bytes, sites: bytes = b""):
        self.data, self.sites, self.size = data, sites, len(data) + len(sites)


class Emitted:
    """An emission: the C++ and the manifest, pickled."""

    def __init__(self, cpp: str, manifest: bytes):
        self.cpp, self.manifest, self.size = cpp, manifest, len(cpp.encode("utf-8")) + len(manifest)


class Walked:
    """An accepted check's walk over the bodies: the checker as the walk left it and the walk's record, pickled, and
    where each body is, which says whether another source differs from this one in one body alone."""

    def __init__(self, data: bytes, walk: Walk, sites: bool):
        self.data, self.spans, self.sites, self.size = data, walk.spans, sites, len(data)

    @classmethod
    def of(cls, c: Checker, walk: Walk) -> Walked | None:
        """What `compile_program` gives its `walked` argument, kept; nothing for a walk that met a refusal."""
        data = None if c.refusals else dumps((c.p, c, walk))
        return None if data is None else cls(data, walk, c.capture_sites)


class Entry:
    """What the compiles of one source made."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.parse: Parsed | Refused | None = None  # the parse tree, before linking
        self.checks: dict[tuple[bool, bool], Pickled | Refused] = {}  # by (sites, every)
        self.walked: Walked | None = None
        self.emission: Emitted | Refused | None = None

    def size(self) -> int:
        held = (self.parse, *self.checks.values(), self.walked, self.emission)
        return sum(kept.size for kept in held if kept is not None)


class Cache:
    """The kept compiles of this process, by key, the one used least recently first."""

    def __init__(self, entries: int = ENTRIES, size: int = BYTES):
        self.entries, self.size = entries, size
        self.held: OrderedDict[str, Entry] = OrderedDict()
        self.sizes: dict[str, int] = {}
        self.lock = threading.Lock()

    def entry(self, key: str, source: str) -> Entry:
        """The kept entry of `key`, now the one used most recently, or a new one that `settle` keeps once it holds
        something."""
        with self.lock:
            if (found := self.held.get(key)) is None:
                return Entry(source)
            self.held.move_to_end(key)
            return found

    def recent(self) -> list[Entry]:
        """The kept entries, the one used most recently first."""
        with self.lock:
            return list(reversed(self.held.values()))

    def settle(self, key: str, entry: Entry) -> None:
        """Keep `entry` as the one used most recently and count what it holds, then drop the sources used least
        recently until both limits hold. An entry larger than the whole allowance goes too, after every other."""
        with self.lock:
            self.held[key] = entry
            self.held.move_to_end(key)
            self.sizes[key] = entry.size()
            while self.held and (len(self.held) > self.entries or sum(self.sizes.values()) > self.size):
                gone, _ = self.held.popitem(last=False)
                del self.sizes[gone]

    def stats(self) -> dict[str, int]:
        with self.lock:
            return {"sources": len(self.held), "bytes": sum(self.sizes.values()), "most_sources": self.entries,
                    "most_bytes": self.size}  # fmt: skip

    def clear(self) -> None:
        with self.lock:
            self.held.clear()
            self.sizes.clear()


CACHE = Cache()


class Compilation:
    """One source's compile as one caller reads it. Each stage runs at most once per source in this process, and every
    call answers with the caller's own copy. `cached` stays true while no stage has run for this caller."""

    def __init__(self, source: str, sites: bool = False, every: bool = False, cache: Cache | None = None):
        self.source, self.sites, self.every = source, sites, every
        self.cache = cache or CACHE
        text = hashlib.sha256(source.encode("utf-8")).hexdigest()
        self.key = hashlib.sha256(f"{compiler()}\0{library()}\0{text}".encode()).hexdigest()
        self.held = self.cache.entry(self.key, source)  # this caller's, even once the cache drops it
        self.cached = True
        self.edit = ""  # the function whose body alone was checked or parsed, from a kept walk or parse

    def keep(self) -> None:
        self.cache.settle(self.key, self.held)

    def parsed(self) -> Program:
        """What `Parser(source).parse()` answers. A source that differs in one body from one whose parse is kept is
        parsed from that parse, the edited body alone (compiler/check/incremental.py)."""
        kept = self.held.parse
        if isinstance(kept, Refused):
            raise kept.raised()
        if kept is not None:
            return loads(kept.data)
        self.cached = False
        try:
            tree = self.spliced() or Parser(self.source).parse()
        except Diagnostic as error:
            self.held.parse = Refused(error)
            self.keep()
            raise
        if (data := dumps(tree)) is not None:
            self.held.parse = Parsed(tree, data)
            self.keep()
        return tree

    def spliced(self) -> Program | None:
        """The parse from a kept parse of a source this one differs from in one body, or None."""
        for base in self.cache.recent():
            found = base.parse
            if isinstance(found, Parsed) and (edit := incremental.edited(base.source, self.source, found.spans)):
                try:
                    tree = incremental.spliced(loads(found.data), edit, self.source)
                except Exception:  # a body that does not parse alone, which a whole parse reports where it is
                    return None
                self.edit = edit.name
                return tree
        return None

    def found(self) -> Pickled | Refused | None:
        """The kept check that answers this caller: one made as it asks, one that also captured sites, or, for an
        accepted program, one made with the other `every`."""
        checks, widths = self.held.checks, [True] if self.sites else [False, True]
        for sites in widths:
            if (sites, self.every) in checks:
                return checks[sites, self.every]
        accepted = [checks.get((sites, not self.every)) for sites in widths]
        return next((kept for kept in accepted if isinstance(kept, Pickled)), None)

    def check(self) -> Checked:
        """Run the check, keep it and its walk, and give its objects to this caller. An edit of one body of a source
        whose walk is kept is checked from that walk; otherwise a kept parse is used, or the check parses and keeps no
        parse, since only a caller that asks for one reads it."""
        kept = self.held.parse
        if isinstance(kept, Refused):
            raise kept.raised()
        self.cached, walked = False, list[Walked | None]()
        try:
            answer = self.edited(walked.append)
            if answer is None:
                tree = loads(kept.data) if kept is not None else None
                answer = compile_program(self.source, self.sites, parsed=tree, every=self.every,
                                         walked=lambda c, walk: walked.append(Walked.of(c, walk)))  # fmt: skip
        except Diagnostic as error:
            if error.abandoned is None:  # a check a fault ended is not an answer
                self.held.checks[self.sites, self.every] = Refused(error)
                self.keep()
            raise
        p, checker, receipts = answer
        listed, checker.sites = checker.sites, []
        try:
            data, sites = dumps((p, checker, receipts)), dumps(listed)
        finally:
            checker.sites = listed
        if data is not None and sites is not None:
            self.held.checks[self.sites, self.every] = Pickled(data, sites)
            if walked and walked[-1] is not None and not (self.held.walked and self.held.walked.sites):
                self.held.walked = walked[-1]  # one that recorded sites answers more
            self.keep()
        return p, checker, receipts

    def edited(self, walked: Callable[[Walked | None], None]) -> Checked | None:
        """The check from the kept walk of a source this one differs from in one body, if there is one and the edit is
        one it takes; None, and the whole check runs, otherwise."""
        for base in self.cache.recent():
            found = base.walked
            if found is None or (self.sites and not found.sites):
                continue
            if (edit := incremental.edited(base.source, self.source, found.spans)) is None:
                continue
            p, checker, walk = loads(found.data)
            self.edit = edit.name  # a refusal raised below is this edit's too
            try:
                receipts = incremental.recheck(checker, walk, edit, self.source, self.sites, self.every,
                                               lambda c, walk: walked(Walked.of(c, walk)))  # fmt: skip
            except Diagnostic:
                raise
            except Exception:  # an edit the walk does not take (Fallback), or a fault of the walk's own: check whole
                self.edit = ""
                return None
            return p, checker, receipts
        return None

    def copied(self, kept: Pickled | Refused, sites: bool) -> Checked:
        """A copy of a kept check, as a check with these settings would have left it."""
        if isinstance(kept, Refused):
            raise kept.raised()
        p, checker, receipts = loads(kept.data)
        checker.capture_sites, checker.sites = sites, loads(kept.sites) if sites else []
        checker.refusals = [] if self.every else None
        return p, checker, receipts

    def program(self) -> Checked:
        """What `compile_program(source, sites, every=every)` answers."""
        kept = self.found()
        return self.check() if kept is None else self.copied(kept, self.sites)

    def emitted(self) -> tuple[str, dict[str, Any]]:
        """What `compile_source(source, every=every)` answers: the C++ and its manifest."""
        kept = self.held.emission
        if isinstance(kept, Refused):
            raise kept.raised()
        if kept is not None:
            return kept.cpp, loads(kept.manifest)
        found = self.found()
        checked = self.check() if found is None else self.copied(found, False)
        self.cached = False
        try:
            interface, bodies, manifest = generate(self.source, "", (), every=self.every, checked=checked)
        except Diagnostic as error:
            self.held.emission = Refused(error)
            self.keep()
            raise
        cpp = joined(interface, bodies)
        if (data := dumps(manifest)) is not None:
            self.held.emission = Emitted(cpp, data)
            self.keep()
        return cpp, manifest


def program(source: str, sites: bool = False, every: bool = False) -> Checked:
    """`compile_program(source, sites, every=every)`, once per source in this process."""
    return Compilation(source, sites, every).program()


def emitted(source: str, every: bool = False) -> tuple[str, dict[str, Any]]:
    """`compile_source(source, every=every)`, once per source in this process."""
    return Compilation(source, every=every).emitted()


def parsed(source: str) -> Program:
    """`Parser(source).parse()`, once per source in this process."""
    return Compilation(source).parsed()

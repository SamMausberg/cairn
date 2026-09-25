"""A body edit checked without checking every other body again, with the whole check's answer or none.

The walk over the bodies (`Checker.bodies`) checks each concrete function in program order, and each check adds to the
program-wide tables refusals.py names (TABLES, LISTS), appends the generic instances it makes to the program, takes
the addresses of functions and counts nodes and unique places. What a check reads of another function's check is only
what it makes on first use through `Checker.made` (MEMOS: generic instances, layouts of generic types,
implementations, folded layouts), and every rule that reads rows runs after the walk. `recorded` walks as `bodies`
does and notes how far each table had grown after each function and around each first making, so what one check
added can be read back from the checker as the walk left it, which the caller pickles right then.

`edited` compares a new source with the one a walk was recorded for. It takes an edit that changes the text of one
function's body and nothing else, so every declaration and every other body reads byte for byte as before, and it
never reads a caller's claim about what changed.

`replayed` checks the new source from that pickle: every position after the edited body moves by what the edit
added, the new body is parsed in place, and the walk runs again in program order, where every other function's
additions are put back as its check made them and only the edited body is checked. Where the edited body's check asks
for something the walk made first in that body, it is put back as it was made, so a later body that reads it reads the
same object. That is what a whole check of the new source answers when these hold, and where one does not `replayed`
raises Fallback and the caller checks the whole program:

- everything the edited body made first in the walk is put back while it is checked again, and every address it took
  first is taken again, so no later check reads anything the walk had and this one has not;
- its new check makes nothing that the check of a later function made, so none of those finds it made;
- it names as many unknown places as before (`Checker.unique`, which extents carry), or no later check names one;
- the program stays inside the node and function limits a whole check holds it to.

The memos of what a type is (`Checker.kinds`, `Checker.frees`) are held to less: they answer the same for every type
whichever check asks first, so one check may hold an entry the other has not needed. Everything after the walk
(implementations, plans, the rows and every rule that reads them) runs as in a whole check, on what the walk left.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..derive.expansion import visible
from ..device import layouts
from ..syntax.lexing import lex
from ..syntax.parser import Parser
from ..syntax.tree import MAX_FUNCTIONS, MAX_NODES, Diagnostic, Expr, Function, Program, Stmt, Type, local
from .refusals import LISTS, TABLES

if TYPE_CHECKING:
    from .checking import Checker

MEMOS = ("fs", "layouts", "impls", "folded", "bounds")  # what a body's check reads, and makes on first use
FUNCTIONS = len(TABLES) + len(LISTS)  # where a mark holds the program's functions; its nodes and unique places follow
Mark = tuple[int, ...]  # the sizes of TABLES and LISTS, the functions, the nodes and the unique places
Made = tuple[Mark, Mark, set[str]]  # a first making: the marks before and after it, and the addresses it took first


class Fallback(Exception):
    """This edit is checked whole: the reason says which condition of the module docstring failed."""


@dataclass
class Walk:
    """How far each table had grown after the declarations were prepared and after each function's body, and what
    each body's check made first."""

    prepared: Mark
    names: list[str] = field(default_factory=list)  # the concrete functions, in the order the walk checks them
    marks: list[Mark] = field(default_factory=list)  # after each of them
    taken: list[set[str]] = field(default_factory=list)  # the functions whose address each one's check took first
    made: list[dict[tuple[str, Any], Made]] = field(default_factory=list)  # what each one's check made first
    spans: dict[str, tuple[int, int, int, bool]] = field(default_factory=dict)  # (line, body_start, end, editable)

    def before(self, k: int) -> Mark:
        return self.marks[k - 1] if k else self.prepared


def mark(c: Checker) -> Mark:
    return (*(len(getattr(c, n)) for n in TABLES), *(len(getattr(c, n)) for n in LISTS), len(c.p.functions), c.nodes,
            c.unique)  # fmt: skip


def shifted(made: Made, by: Mark) -> Made:
    before, after, taken = made
    return (
        tuple(a + d for a, d in zip(before, by, strict=True)),
        tuple(a + d for a, d in zip(after, by, strict=True)),
        taken,
    )


def grown(c: Checker, walk: Walk, taken: set[str], made: dict[tuple[str, Any], Made]) -> None:
    """Note what the function just checked added: the tables' sizes, the addresses it took first, what it made."""
    walk.marks.append(mark(c))
    walk.taken.append(c.address_taken - taken if len(c.address_taken) != len(taken) else set())
    walk.made.append(made)
    taken |= walk.taken[-1]


class Making:
    """What `Checker.made` does while a walk is recorded: make what is asked, and note what making it added. While the
    edited body is checked again, a key the recorded walk made in that body is put back as it was made instead, when
    the check would make it the same: everything that body made before it is back, and nothing it made exists yet."""

    def __init__(self, kept: Kept | None = None, start: Mark = (), base: dict[tuple[str, Any], Made] | None = None):
        self.kept, self.start, self.base = kept, start, base or {}
        self.made: dict[tuple[str, Any], Made] = {}  # in this walk's marks
        self.back: set[tuple[str, Any]] = set()  # what was put back

    def __call__(self, c: Checker, key: tuple[str, Any], make: Callable[[], Any]) -> None:
        before, taken = mark(c), set(c.address_taken)
        found = self.base.get(key)
        if self.kept is not None and found is not None and self.fits(c, found):
            self.kept.put(c, found[0], found[1], found[2])
            self.back |= self.kept.keys(found[0], found[1])
            by = tuple(a - b for a, b in zip(before, found[0], strict=True))
            self.made |= {k: shifted(m, by) for k, m in self.base.items() if found[0] <= m[0] and m[1] <= found[1]}
        else:
            make()
        self.made[key] = (before, mark(c), c.address_taken - taken)

    def fits(self, c: Checker, made: Made) -> bool:
        before, after, _ = made
        assert self.kept is not None
        if not self.kept.keys(self.start, before) <= self.back or c.unique != before[-1] != after[-1]:
            return False
        if len(c.p.functions) + after[FUNCTIONS] - before[FUNCTIONS] > MAX_FUNCTIONS:
            return False  # making it again reports the limit where a whole check does
        return not any(key in getattr(c, n) for n, key in self.kept.keys(before, after))


@dataclass
class Kept:
    """What a recorded walk left in the program-wide tables, read back while its bodies are put back."""

    tables: dict[str, list[tuple[Any, Any]]]
    lists: dict[str, list[Any]]
    functions: list[Function]

    @classmethod
    def of(cls, c: Checker) -> Kept:
        return cls({n: list(getattr(c, n).items()) for n in TABLES}, {n: list(getattr(c, n)) for n in LISTS},
                   list(c.p.functions))  # fmt: skip

    def keys(self, a: Mark, b: Mark) -> set[tuple[str, Any]]:
        """What the walk made first between two marks, in the memos a body's check reads."""
        return {(n, key) for n in MEMOS for key, _ in self.tables[n][a[TABLES.index(n)] : b[TABLES.index(n)]]}

    def put(self, c: Checker, a: Mark, b: Mark, taken: set[str]) -> None:
        """Put back what the walk added between two marks: every table's and list's additions, the instances, their
        signatures, the addresses taken first and the counts."""
        for i, n in enumerate(TABLES):
            getattr(c, n).update(self.tables[n][a[i] : b[i]])
        for j, n in enumerate(LISTS, len(TABLES)):
            getattr(c, n).extend(self.lists[n][a[j] : b[j]])
        c.p.functions.extend(self.functions[a[FUNCTIONS] : b[FUNCTIONS]])
        fs = TABLES.index("fs")
        c.signed |= {name for name, _ in self.tables["fs"][a[fs] : b[fs]]}  # a body's check signs only its instances
        c.address_taken |= taken
        c.nodes, c.unique = c.nodes + b[-2] - a[-2], c.unique + b[-1] - a[-1]


def recorded(c: Checker) -> Walk:
    """`Checker.bodies`, noting how far every table grew after each function and what each check made first. The
    caller pickles the checker as the walk leaves it, with this record, when the check is accepted."""
    concrete = c.prepare()
    walk, taken = Walk(mark(c)), set(c.address_taken)
    c.making = making = Making()
    try:
        for f in concrete:
            making.made = {}
            c.body(f)
            walk.names.append(f.name)
            grown(c, walk, taken, making.made)
    finally:
        c.making = None
    described(c, walk)
    return walk


def described(c: Checker, walk: Walk) -> None:
    """Where each function's body is in the combined source, and whether an edit of it alone is checked from the
    walk: a function the program's own source declares, with a body, that no implementation, family or derivation
    reads beyond its name."""
    p = c.p
    named = {n.rsplit(".", 1)[-1] for d in p.derivations for n in (*d[2], d[3]) if isinstance(n, str) and n}
    references = {r for f in p.functions if f.implements for r in [reference(c, f)] if r}
    walk.spans = {}
    if any(f.source_name.startswith("derive grad") for f in p.functions):
        return  # a gradient's nodes take the line of the derive that asked for it, and places in text it generated
    for name in walk.names:
        f = c.fs[name]
        if not home(p, c.fs, f):
            continue  # its positions are a library module's text
        plain = not (f.bindings or f.extern or f.implements or f.source_name.startswith("derive "))
        editable = plain and name not in references and local(name) not in named
        walk.spans[name] = (f.line, f.body_start, f.end, editable)


def reference(c: Checker, f: Function) -> str | None:
    assert f.implements is not None
    with c.within(f.module):
        try:
            return c.qualify(f.implements.reference, c.fs)
        except Diagnostic:
            return None


def home(p: Program, fs: dict[str, Function], f: Function) -> bool:
    """Whether `f` has its positions in the combined source: not a library module's, nor copied from one, nor made
    from a packaged recipe."""
    while f.bindings and f.source_name in fs and fs[f.source_name] is not f:  # an instance or a family's copy
        f = fs[f.source_name]
    if f.module in p.sources:
        return False
    if f.source_name.startswith("derive "):  # the recipe's positions, found as expansion.derive found it
        written = f.source_name.removeprefix("derive ").split(" for ")[0]
        try:
            found = visible(p, f.module, written, p.recipes)
        except Diagnostic:
            found = None
        recipe = p.recipes.get(found or f"std.{written}.{written}") or p.recipes.get(f"std.derived.{written}")
        return recipe is not None and recipe.module not in p.sources
    return True


# Which function an edit changed ------------------------------------------------------------------------------------


@dataclass
class Edit:
    """One function's body `before[body_start:end]` became `after[body_start:end + moved]`."""

    name: str
    line: int  # the line the old body ends on: everything below it moves
    body_start: int
    end: int
    moved: int  # bytes
    lines: int
    tokens: int


def common(a: str, b: str, limit: int, backward: bool = False) -> int:
    """How many characters `a` and `b` share at their start (or end), at most `limit`: a binary search over slices,
    each compared in C."""
    lo, hi = 0, limit
    while lo < hi:
        mid = (lo + hi + 1) // 2
        same = a[len(a) - mid :] == b[len(b) - mid :] if backward else a[:mid] == b[:mid]
        lo, hi = (mid, hi) if same else (lo, mid - 1)
    return lo


def edited(before: str, after: str, spans: dict[str, tuple[int, int, int, bool]]) -> Edit | None:
    """The one body whose text changed between `before`, whose walk recorded `spans` (Walk.spans), and `after`; None
    when anything else changed, or the edit is one `replayed` does not take."""
    if before == after:
        return None
    first = common(before, after, min(len(before), len(after)))
    last = common(before, after, min(len(before), len(after)) - first, backward=True)
    changed = len(before) - last
    found = next((name for name, (_, start, end, _) in spans.items() if start <= first < end and changed <= end), None)
    if found is None:
        return None
    _, body_start, end, editable = spans[found]
    stop = before.find("\n", end)
    rest = before[end : stop if stop >= 0 else None].strip()
    glued = first == body_start and not before[body_start - 1].isspace()
    if not editable or (rest and not rest.startswith("//")) or glued:
        return None  # a token after the body on its last line would move sideways; a new first token may join one
    moved = len(after) - len(before)
    lines = after.count("\n", body_start, end + moved) - before.count("\n", body_start, end)
    tokens = len(lex(after, body_start, end + moved)) - len(lex(before, body_start, end))
    return Edit(found, before.count("\n", 0, end) + 1, body_start, end, moved, lines, tokens)


def reparsed(after: str, f: Function, edit: Edit) -> tuple[int, list[Stmt]]:
    """Where the edited body starts and what it holds, parsed where it stands in `after` as a whole parse of `after`
    reads it: its last token ends where the old body's did, moved, and is one character that joins nothing after
    it."""
    parser = Parser(after, (edit.body_start, edit.end + edit.moved))
    try:
        body = parser.block() if f.test else parser.body(f.ret, parser.t)
    except Diagnostic as error:
        raise Fallback(f"the edited body does not parse alone ({error.data['code']})") from None
    last = parser.ts[parser.i - 1]
    if parser.t.s != "<eof>" or last.end != edit.end + edit.moved or last.s not in {"}", ";"}:
        raise Fallback("the edited body does not end where the old one did")
    return parser.ts[0].start, body


# Where everything below the edit moves --------------------------------------------------------------------------


class Moved:
    """Moves the positions of what lies below an edited body: `lines` down, `moved` bytes on, `tokens` token indices
    on (an impl's block is named by one). Each node moves once, however many parents reach it."""

    def __init__(self, edit: Edit):
        self.lines, self.bytes, self.tokens = edit.lines, edit.moved, edit.tokens
        self.seen: set[int] = set()

    def token(self, t: Any) -> None:
        t.line, t.start, t.end = t.line + self.lines, t.start + self.bytes, t.end + self.bytes

    def function(self, f: Function) -> None:
        if id(f) in self.seen:
            return
        self.seen.add(id(f))
        f.line += self.lines if f.line else 0
        f.start, f.body_start, f.end = (x + self.bytes if x >= 0 else x for x in (f.start, f.body_start, f.end))
        if isinstance(f.block, tuple):  # (module, token index) as parsed; (recipe, token index, type) as derived
            f.block = (f.block[0], f.block[1] + self.tokens, *f.block[2:])
        self.nodes([*f.body, *([f.implements.when] if f.implements and f.implements.when else [])])

    def kept(self, roots: list[Expr]) -> None:
        """Mark nodes that stay where they are, so no parent that moves moves them."""
        todo = list(roots)
        while todo:
            n = todo.pop()
            if id(n) not in self.seen:
                self.seen.add(id(n))
                todo += n.args

    def nodes(self, roots: list[Any]) -> None:
        todo = list(roots)
        while todo:
            n = todo.pop()
            if id(n) in self.seen:
                continue
            self.seen.add(id(n))
            n.line += self.lines if n.line else 0
            if isinstance(n, Expr):
                n.start, n.end = (x + self.bytes if x >= 0 else x for x in (n.start, n.end))
                todo += [*n.args, *held(n.ref)]
                if n.tag == "lambda" and isinstance(n.ref, Function):
                    self.function(n.ref)
            elif isinstance(n, Stmt):
                todo += [*n.exprs, *n.other_names, *n.body, *n.other, *n.arms, *held(n.ref)]
                if n.assembly:
                    n.assembly.outputs = [(a, t, s, line + self.lines if line else line, col)
                                          for a, t, s, line, col in n.assembly.outputs]  # fmt: skip
            else:  # an arm
                todo += n.body


def held(ref: Any) -> list[Expr | Stmt]:
    """The nodes an annotation holds beside the tree, which move with it: a spawned region or a cooperative finish,
    and the nodes a record keeps, as a wide access keeps its part and a recipe's `each` its sequence, conditions and
    items. A name's reference to a constant's literal is not one: the literal stays where the constant is declared."""
    found: list[Expr | Stmt] = []
    for x in ref if isinstance(ref, tuple | list) else [ref]:
        if isinstance(x, Stmt):
            found.append(x)
        elif hasattr(x, "__dict__") and not isinstance(x, Expr | Function | Type):
            for value in vars(x).values():
                for y in value if isinstance(value, list | tuple) else [value]:
                    found += [z for z in (y if isinstance(y, tuple) else [y]) if isinstance(z, Expr | Stmt)]
    return found


def moved(c: Checker, edit: Edit) -> set[str]:
    """Move every position of the combined source below the edited body to where a whole parse of the edited source
    puts it; the functions that moved."""
    p, move = c.p, Moved(edit)
    mine = {name for name, module in p.modules.items() if module not in p.sources}
    declared = [e for name, (_, e) in p.consts.items() if name in mine] + [e for n, e in p.layouts.items() if n in mine]
    move.nodes([e for e in declared if e.line > edit.line])
    move.kept([e for e in declared if e.line <= edit.line])  # a body below may hold a constant's literal above
    after = {f.name for f in p.functions if f.line > edit.line and home(p, c.fs, f)}
    for f in p.functions:
        if f.name in after:
            move.function(f)
    for module, *_, token in [*p.plans, *p.selections, *p.derivations]:
        if module not in p.sources and token.line > edit.line:
            move.token(token)
    for name, members in p.traits.items():
        for m in members if name in mine else []:
            if m.line > edit.line:
                move.function(m)
    for recipe in p.recipes.values():
        if recipe.module not in p.sources and recipe.line > edit.line:
            recipe.line, recipe.start, recipe.end = (
                recipe.line + edit.lines,
                recipe.start + edit.moved,
                recipe.end + edit.moved,
            )
            items(move, recipe.items, recipe.where)
    p.scopes[:] = [(at + edit.moved if module not in p.sources and at >= edit.end else at, module)
                   for at, module in p.scopes]  # fmt: skip
    for site in c.sites:
        if site["symbol"] in after:
            site["start"], site["end"] = site["start"] + edit.moved, site["end"] + edit.moved
    for name in after:
        for record in [*c.resources.get(name, ()), *c.numerics.get(name, ())]:
            record["line"] += edit.lines if record.get("line") else 0
    return after


def items(move: Moved, found: list[Any], where: list[tuple[str, Expr]]) -> None:
    """A recipe's declarations, and those of each `each` in it."""
    move.nodes([e for _, e in where])
    for item in found:
        if isinstance(item, Function):
            move.function(item)
        elif isinstance(item, Stmt):
            move.nodes([item])
        elif hasattr(item, "members"):  # an impl, named by its token index
            item.block += move.tokens
            for m in item.members:
                move.function(m)
        elif hasattr(item, "seq"):  # each
            move.nodes(item.seq)
            items(move, item.items, item.where)
        elif hasattr(item, "fields"):  # a record, whose fields hold no place but in an `each`
            item.line += move.lines
            items(move, item.fields, [])


# The walk again ----------------------------------------------------------------------------------------------------


def replayed(c: Checker, walk: Walk, edit: Edit, body: tuple[int, list[Stmt]]) -> Walk:
    """The walk over the bodies of the edited program, from the checker a recorded walk left: every function's
    additions but the edited one's put back where the walk made them, and the edited one checked with `body`. The
    record of this walk, for the next edit."""
    k = walk.names.index(edit.name)
    lo, hi, last = walk.before(k), walk.marks[k], walk.marks[-1]
    kept, borrowed = Kept.of(c), c.borrowed
    for i, n in enumerate(TABLES):
        table = getattr(c, n)
        table.clear()
        table.update(kept.tables[n][: lo[i]])
    for j, n in enumerate(LISTS, len(TABLES)):
        del getattr(c, n)[lo[j] :]
    del c.p.functions[lo[FUNCTIONS] :]
    c.address_taken.difference_update(*walk.taken[k:])
    c.signed -= {name for name, _ in kept.tables["fs"][lo[TABLES.index("fs")] :]}
    c.nodes, c.unique = lo[-2], lo[-1]
    taken = set(c.address_taken)
    again = Walk(walk.prepared, list(walk.names), walk.marks[:k], [set(s) for s in walk.taken[:k]], walk.made[:k])
    f = c.fs[edit.name]
    (f.body_start, f.body), f.end = body, edit.end + edit.moved
    c.making = making = Making(kept, lo, walk.made[k])
    try:
        c.body(f)
    finally:
        c.making = None
    grown(c, again, taken, making.made)
    now = again.marks[-1]
    present = {key for key in making.back if key[1] in getattr(c, key[0])}  # a refusal takes back what it put back
    if lost := kept.keys(lo, hi) - present:
        raise Fallback(f"{edit.name} no longer makes {min(map(str, lost))} first as the walk did")
    later = kept.keys(hi, last)
    if clash := {(n, key) for n in MEMOS for key in list(getattr(c, n))[lo[TABLES.index(n)] :]} & later:
        raise Fallback(f"{edit.name} now makes {min(map(str, clash))} first, which a later check made")
    if not walk.taken[k] <= again.taken[-1]:
        raise Fallback(f"{edit.name} no longer takes first the address it took")
    if now[-1] - lo[-1] != hi[-1] - lo[-1] and last[-1] != hi[-1]:
        raise Fallback("the edited body names another number of unknown places, which later extents count")
    for i in range(k + 1, len(walk.names)):
        start, stop = walk.marks[i - 1], walk.marks[i]
        by = tuple(a - b for a, b in zip(mark(c), start, strict=True))
        kept.put(c, start, stop, walk.taken[i])
        grown(c, again, taken, {key: shifted(made, by) for key, made in walk.made[i].items()})
    if c.nodes > MAX_NODES or len(c.p.functions) > MAX_FUNCTIONS:
        raise Fallback("the edited program passes a limit a whole check reports")
    if k + 1 < len(walk.names):
        c.borrowed = borrowed  # what the last function's last call lent, as the walk leaves it
    described(c, again)
    return again


def recheck(c: Checker, walk: Walk, edit: Edit, after: str, sites: bool, every: bool,
            walked: Callable[[Checker, Walk], None]) -> dict[str, Any]:  # fmt: skip
    """What compile_program(after, sites, every=every) answers for the checker `c` a recorded walk of the source
    before the edit left: its receipts, with `c` and `c.p` the checked program. `walked` is given the checker and
    the record of the walk right after it, as compile_program gives it."""
    body = reparsed(after, c.fs[edit.name], edit)
    moved(c, edit)
    if not sites:
        c.sites.clear()
    c.capture_sites, c.refusals = sites, [] if every else None
    receipts = c.check(lambda c: walked(c, replayed(c, walk, edit, body)))
    layouts.settle(c)  # as compile_program does
    return receipts

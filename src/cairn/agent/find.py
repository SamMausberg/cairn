"""`cairn find`: the functions to call, found by the values an agent has or by its words, in a bounded answer.

A query runs over the builtins, the packaged `std` modules and the program's own functions. Given the types of the
values an agent has, and perhaps the type it wants back, a function fits when a call of it with those values checks.
Each candidate call is written out as a probe function in the program's root module: the values in any order, and
each parameter they leave open as a further value of its declared type, a template's parameters at the types the
values hold. One check judges every probe, reporting each refusal on its own (compiler/check/refusals.py), so what
fits is what the compiler accepts and nothing here repeats a type rule. A probe refused only because it drops a
linear value it was handed still fits: the call checked, and the drop is the probe's. A call that fills every
parameter, besides the extents a call may leave out, fits before one that leaves some open, and a result that holds
the wanted type, as an `Option` or a `Result` does, after one that is it.

Given words, a function matches when its name, its module or the first sentence of the comment above it holds
them, or a word `ALIASES` takes for one of them. A builtin's rule is code (compiler/primitives/builtins.py), so
`BUILTINS` says in one line what each does; a probe still judges every builtin a type query reaches.

A hit is one line: the qualified name and signature, the effects of its row beyond what `pure` allows, and the first
sentence of its comment. An effect ceiling keeps the functions whose rows, less their reads and writes of what they
are passed, stay within it as `E-EFFECT-CEILING` judges a row; the modes of the values given decide those reads and
writes. A builtin's row belongs to a call, so a type query gives it and a word query does not.
"""

from __future__ import annotations

import contextlib
import functools
import itertools
import re
from dataclasses import dataclass, field
from typing import Any

from ..compiler import compilations
from ..compiler.cairnc import Checker, Diagnostic, Function, Parser, Type, derive, fail, link, specialize
from ..compiler.check.calls import extents
from ..compiler.check.effects import allowed
from ..compiler.check.refusals import reaches
from ..compiler.check.traits import described
from ..compiler.primitives.builtins import TABLE
from ..compiler.syntax.lexing import comment_above
from ..compiler.syntax.tree import CPP, INTRINSIC_TYPES, is_view
from ..editor.docs import library_names
from .projection import generics

LIMIT = 10  # hits an answer lists; it counts the rest
BUILTINS = {  # the names each line covers: how a call is written, and what it does
    "len": "len(xs) -> usize: the extent of an array view, a part, a Buf, an Array or a string; a Vec's is v.len",
    "print println eprint eprintln": "println(a, ...): integers, floats, bools, 'c', strings and u8 views, then a "
    "newline; print leaves it out, eprint and eprintln write to standard error",
    "format": "format(out, a, ...): what println would write, appended to a Vec[u8]",
    "u8 u16 u32 u64 usize i8 i16 i32 i64 f32 f64": "u64(x): x as that type, aborting when an integer does not fit; the "
    "minimum of a signed type is a literal, -9223372036854775808 for i64",
    "f16 bf16 f8e4m3 f8e5m2": "f16(x): a storage float, rounded once; widen it with f32(x) to compute",
    "add_wrap sub_wrap mul_wrap": "add_wrap(a, b): modular arithmetic of two unsigned integers; checked + - * "
    "abort on overflow, signed or unsigned",
    "shl_wrap shr": "shl_wrap(x, k), shr(x, k): an unsigned integer shifted left or right by a count below its width",
    "min max": "min(a, b), max(a, b): the lesser or the greater of two integers",
    "sqrt floor ceil trunc": "sqrt(x), floor(x), ceil(x), trunc(x): of an f32 or f64, exact as IEEE 754 defines them",
    "abs": "abs(x): the magnitude of a float or a signed integer, aborting on the signed minimum",
    "to_bits from_bits": "to_bits(x): a float's IEEE bit pattern; from_bits[T](u) is the float back",
    "quantize quantize_stochastic": "quantize[T](x, scale): x / scale rounded once to T, clamped to its finite range",
    "assert assert_eq": 'assert(cond, "why"), assert_eq(a, b): abort, saying where and why, unless it holds',
    "Buf Array": "Buf[T](n): an owner of n zeroed elements on the heap; Array[T, N]() is a fixed array held inline",
    "take swap": "take(place): the owner moved out, leaving zero behind; swap(a, b) exchanges two places",
    "wait": "wait(t): waits for the task let t = spawn f(args); started and gives its result; wait(g) joins a group",
    "Group collect": "Group[T](k): up to k tasks, each started by spawn f(args) into g; collect(g) is the next result "
    "to finish",
    "Atomic Mutex": "Atomic[T](v), Mutex[T](v): one value that tasks share and change through ro borrows",
    "Dyn": "Dyn[Trait](value): a value of any type that implements Trait, moved to the heap",
    "transfer": "transfer(dst, src): elements copied between host and device memory",
    "IoRing": "IoRing(n): a Linux io_uring with n operations in flight, declared in place",
    "shuffle shuffle_xor shuffle_down shuffle_up": "shuffle(v, lane): v as another thread of the warp holds it",
    "warp_ballot warp_any warp_all warp_match": "warp_ballot(c): the lanes of the warp where c holds",
    "mma_unordered": "mma_unordered(m, n, k, c, a, b): c += a * b on the tensor cores, summed in an order the "
    "hardware picks",
    "mma_load mma_store mma_get mma_set": "mma_load[F](tile, L, i, j): fragment (i, j) of a tile; mma_store(tile, L, "
    "i, j, acc) writes one back",
    "WmmaA WmmaB WmmaAcc MmaA MmaB MmaAcc TmemAcc": "WmmaAcc[f32, 16, 16, 16](0.0): a tensor-core fragment, every "
    "element one value",
    "load_wide store_wide": "load_wide[K](x, i): x[i .. i + K] as one Array[T, K]; store_wide(x, i, v) writes one",
    "atomic_add_wrap atomic_add_unordered atomic_min atomic_max atomic_and atomic_or atomic_xor "
    "atomic_cas": "atomic_add_wrap(x[i], v): one element updated atomically, from any number of lanes or threads",
    "mmio_read mmio_write asm": 'mmio_read[T](address), mmio_write[T](address, value), asm("..."): the machine, '
    "inside unsafe",
}
# Words an agent brings from other languages, and the words of CAIRN's names and comments each stands for.
ALIASES = {
    "integer": "i64 u64 usize decimal", "int": "i64 u64 usize", "number": "i64 u64 f64 decimal integer float",
    "string": "text", "str": "text", "substring": "find needle", "search": "find", "index": "find", "lookup": "get find",
    "array": "buf vec", "list": "vec", "vector": "vec", "append": "push extend", "add": "push insert add",
    "remove": "remove pop", "delete": "remove", "dictionary": "map", "dict": "map", "hashmap": "map", "table": "map",
    "stdin": "stdin input", "input": "stdin read", "stdout": "print println", "output": "print write",
    "thread": "spawn task group", "concurrent": "spawn group", "parallel": "spawn group", "join": "wait",
    "size": "len count", "length": "len", "minimum": "min", "maximum": "max", "cast": "u64 i64 usize",
    "convert": "u64 i64 usize", "overflow": "wrap", "square": "sqrt", "root": "sqrt",
}  # fmt: skip
STOP = set("a an and are as at be by for from how i in into is it its of on one or the this to what with".split())


def stem(word: str) -> str:
    """`parsing`, `parses` and `parse` alike."""
    for suffix in ("ing", "ed", "es", "s", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def split(text: str) -> list[str]:
    return [stem(w) for w in re.findall(r"[a-z][a-z0-9]*", text.lower()) if w not in STOP]


def weighed(*parts: tuple[str, int]) -> dict[str, int]:
    """Each stem of each text, weighed as the last text that holds it is: a comment, then a module, then a name."""
    words: dict[str, int] = {}
    for text, weight in parts:
        words |= dict.fromkeys(split(text), weight)
    return words


def short(ty: Type) -> str:
    """A type as a hit shows it: names without their modules, and host placement left out."""
    return re.sub(r"\b(?:[A-Za-z_]\w*\.)+(?=[A-Za-z_])", "", ty.display()).replace("@host", "")


@dataclass
class Entry:
    """A function an answer may name: a builtin has no `f`, and its `row` belongs to a call."""

    name: str  # as a call names it in full: std.text.parse_i64, len, or the program's own
    head: str  # the signature a hit shows
    doc: str  # the first sentence of its comment
    row: set[str] | None
    rank: int  # 0 the program's own, 1 the packaged library, 2 a builtin
    f: Function | None = None
    words: dict[str, int] = field(default_factory=dict)  # each stem, and how much a match of it weighs
    names: list[str] = field(default_factory=list)  # the builtins a builtin's entry covers

    def line(self, head: str, row: set[str] | None) -> str:
        extra = beyond(row)
        effects = "" if row is None else "  pure" + (f" + {', '.join(extra)}" if extra else "")
        return f"{head or self.head}{effects}" + (f"  // {self.doc}" if self.doc else "")


def beyond(row: set[str] | None) -> list[str]:
    """What a row holds that `pure` does not allow."""
    return sorted(e for e in row or () if e not in allowed(("pure",)) and not e.startswith("read:"))


def first_sentence(lines: list[str]) -> str:
    return re.split(r"(?<=[.!?])\s", lines[0], maxsplit=1)[0] if lines else ""


class Index:
    """Every candidate of one program, beside the checker that resolved them and the source probes go into."""

    def __init__(self, source: str | None):
        self.imports = "".join(f"import {m};\n" for m in library_names())
        self.program = source if source is not None else "fn main() -> i32 { return 0; }\n"
        text = self.imports + self.program
        self.c, verdicts, rows = described(lambda: Checker(specialize(derive(link(Parser(text).parse())))))
        p, self.entries = self.c.p, []
        for f in list(self.c.fs.values()):
            library = f.module in p.sources
            if f.bindings or f.owner or f.extern or f.test or f.implements is not None or "?" in f.name:
                continue
            if (library and f.name not in p.public) or f.name == "main":
                continue
            template = f.generics and verdicts.get(f.name, "ok") != "ok"  # checked per instance: no row ahead
            doc = first_sentence(comment_above(p.sources.get(f.module, text), f.start))
            params = ", ".join(f"{n}:{short(t)}" for n, t in f.params)
            head = f"{f.name}{generics(f.generics)}({params})" + ("" if f.ret.name == "void" else f" -> {short(f.ret)}")
            entry = Entry(f.name, head, doc, None if template else rows.get(f.name), int(library), f)
            local = f.name.rsplit(".", 1)[-1]
            entry.words = weighed((doc, 1), (f.module, 2), (local, 3))
            self.entries.append(entry)
        for names, said in BUILTINS.items():
            usage, _, doc = said.partition(": ")
            entry = Entry(names.split()[0], usage, doc, None, 2, names=names.split())
            entry.words = weighed((doc, 1), (names.replace("_", " "), 3))
            self.entries.append(entry)

    def given(self, text: str) -> Type:
        """A type as the agent wrote it, resolved in the root module: a name one module declares may leave out
        its module (`Vec[i64]` is `std.vec.Vec[i64]`)."""
        parser = Parser(text)
        ty = parser.ty()
        if parser.t.s != "<eof>":
            fail("E-PARSE", f"{text!r} is not one type.")
        with self.c.within("", {}):
            return self.c.resolve(self.qualified(ty))

    def qualified(self, ty: Type) -> Type:
        args = tuple(self.qualified(a) if isinstance(a, Type) else a for a in ty.args)
        name = ty.name
        with self.c.within("", {}):
            known = name in CPP or name in INTRINSIC_TYPES or self.c.qualify(name, self.c.types, self.c.p.traits)
        if not known and len(found := [n for n in self.c.types if n.rsplit(".", 1)[-1] == name]) == 1:
            name = found[0]
        return Type(name, ty.mode, ty.extent, args, ty.place)


@functools.lru_cache(maxsize=4)
def indexed(source: str | None) -> Index:
    return Index(source)


def indexed_or_refused(source: str | None) -> tuple[Index, str | None]:
    """The index of `source`, or of the library alone and the code that refused `source` when it does not check."""
    try:
        if source is not None:
            compilations.program(source)
        return indexed(source), None
    except Diagnostic as error:
        return indexed(None), error.data["code"]


# Words ----------------------------------------------------------------------------------------------------------


def worded(entries: list[Entry], words: str) -> dict[str, tuple[int, int]]:
    """By entry name, (how many of the words it matches, how much those matches weigh), for the entries that match
    the most words. A word matches a stem that it, or a word it stands for, is or begins, and each word takes the
    heaviest stem no earlier word took, so `find substring` finds `needle` in `text.find` and not `find` twice."""
    asked = [w for w in re.findall(r"[a-z][a-z0-9]*", words.lower()) if w not in STOP]
    wanted = [{stem(w), *map(stem, ALIASES.get(w, "").split())} for w in asked]
    scores = {}
    for entry in entries:
        taken: set[str] = set()
        for forms in wanted:
            found = [
                (weight, term) for term, weight in entry.words.items() if term not in taken and matches(term, forms)
            ]
            taken |= {max(found)[1]} if found else set()
        if taken:
            scores[entry.name] = (len(taken), sum(entry.words[t] for t in taken))
    most = max((matched for matched, _ in scores.values()), default=0)
    return {name: score for name, score in scores.items() if score[0] == most}


def matches(term: str, forms: set[str]) -> bool:
    return any(term == w or (len(w) >= 3 and term.startswith(w)) for w in forms)


# Types ----------------------------------------------------------------------------------------------------------


@dataclass
class Probe:
    entry: Entry
    fit: tuple[int, int]  # (0 the type wanted, or none asked, 1 a type that holds it; the parameters left open)
    head: str = ""  # a builtin's call, as the probe writes it


def mentioned(ty: Any, names: set[str]) -> set[str]:
    """The generic parameters a declared type names."""
    if not isinstance(ty, Type):
        return set()
    return ({ty.name, ty.extent} & names) | {g for a in ty.args for g in mentioned(a, names)}


def declared(ty: Type, extent: str) -> str:
    """A parameter's type as a probe declares it, its extent renamed to the probe's own."""
    return Type(ty.name, ty.mode, extent, ty.args, ty.place).display() if extent else ty.display()


class Probes:
    """The probe functions of one type query, the source that holds them before the program, and their verdicts."""

    def __init__(self, index: Index, takes: list[Type], returns: Type | None):
        self.index, self.takes, self.returns = index, takes, returns
        self.probes: dict[str, Probe] = {}
        self.accepted: set[str] = set()  # the probes whose calls fit, once judged
        self.params: list[str] = []
        for i, t in enumerate(takes):
            extent = f"e{i}" if is_view(t) and not t.extent.isdigit() else ""
            self.params += [f"{extent}:usize"] * bool(extent) + [f"a{i}:{declared(t, extent)}"]
        # A view or a borrow is passed as it is given; a value is moved into a local the probe owns and lends.
        self.moves = "".join(f"let mut x{i} = a{i}; " for i, t in enumerate(takes) if t.mode == "value")
        self.values = [f"x{i}" if t.mode == "value" else f"a{i}" for i, t in enumerate(takes)]
        self.text = [f"fn cairn_find({', '.join(self.params)}) {{ {self.moves}}}\n"]  # what a builtin's row adds to
        pool = list(dict.fromkeys(x for t in takes for x in (t.value, *(a for a in t.args if isinstance(a, Type)))))
        for entry in index.entries:
            if entry.f is None:
                for name in (n for n in entry.names if n in TABLE):
                    head = f"{name}({', '.join(short(t) for t in takes)})"
                    self.add(entry, [], f"{name}({', '.join(self.values)})", 0, None, head)
            else:
                self.declared(entry, pool)
        self.source = index.imports + "".join(self.text) + index.program

    def add(self, entry: Entry, params: list[str], call: str, left: int, void: bool | None, head: str = "") -> None:
        """A probe of each form the query asks for: the call returned as the type wanted, when one is, and the call
        bound to `_`, whose type may hold the one wanted, or else as a statement. A builtin (`void` None) gets each
        form, since its result is known only once its rule has typed it."""
        statement = [f"{call};"] * (void is not False and not self.returns)
        forms = [f"return {call};"] * bool(self.returns) + statement + [f"let _ = {call};"] * (void is not True)
        for body in forms:
            name = f"cairn_find_{len(self.probes) + 1}"
            self.probes[name] = Probe(entry, (0 if body[0] == "r" or not self.returns else 1, left), head)
            result = f" -> {self.returns.display()}" if body[0] == "r" and self.returns else ""
            self.text.append(f"fn {name}({', '.join([*self.params, *params])}){result} {{ {self.moves}{body} }}\n")

    def declared(self, entry: Entry, pool: list[Type]) -> None:
        """Every way the values fill a function's parameters, with the extents a call may leave out and without
        them, each parameter left open a further value, and a template's generics there at a type the values hold."""
        f = entry.f
        assert f is not None
        types, implied = dict(f.params), extents(f)
        names = {g for g, kind in f.generics if kind != "nat"}
        for listed in {tuple(n for n in types if n not in implied), tuple(types)}:
            for placed in itertools.permutations(range(len(listed)), len(self.takes)):
                at = dict(zip(placed, range(len(self.takes)), strict=True))
                left = [n for j, n in enumerate(listed) if j not in at]
                free = sorted(set().union(*(mentioned(types[n], names) for n in left)))
                for chosen in itertools.product(pool, repeat=len(free)):
                    params, args = [], []
                    try:
                        for j, n in enumerate(listed):
                            if j in at:
                                args.append(self.values[at[j]])
                                continue
                            with self.index.c.within(f.module, dict(zip(free, chosen, strict=True))):
                                ty = self.index.c.resolve(types[n], f)
                            extent = f"m{j}" if is_view(ty) and not ty.extent.isdigit() else ""
                            params += [f"{extent}:usize"] * bool(extent) + [f"p{j}:{declared(ty, extent)}"]
                            args.append(f"p{j}")
                    except Diagnostic:  # a generic chosen at a type it cannot take
                        continue
                    self.add(entry, params, f"{f.name}({', '.join(args)})", len(left), f.ret.name == "void")

    def judged(self) -> list[tuple[Entry, tuple[int, int], str, set[str] | None]]:
        """Each probe the checker accepts, with its fit, a builtin's call and the row that call adds."""
        c = Checker(specialize(derive(link(Parser(self.source).parse()))), False, True)
        with contextlib.suppress(Diagnostic):  # each refusal is kept in c.refusals
            c.check()
        if c.stopped:
            fail("E-INTERNAL", f"The check of the candidate calls stopped at {c.stopped}, so no answer is complete.")
        refused = {about: error.data["code"] for about, _, error, _ in c.refusals}
        base = c.local_effects.get("cairn_find", set())
        out = []
        for name, probe in self.probes.items():
            if refused.get(name, "E-LINEAR-LEAK") != "E-LINEAR-LEAK" or reaches(c, name, set(refused) - {name}):
                continue
            self.accepted.add(name)
            fit = probe.fit
            if fit[0]:  # the call as a statement: its type must hold the one wanted
                ty = c.fs[name].body[-1].ty
                if self.returns not in (ty.value, *(a for a in ty.args if isinstance(a, Type))):
                    continue
                fit = (int(ty.value != self.returns), fit[1])
            own = c.local_effects.get(name) if probe.head and name not in refused else None
            row = None if own is None else {e for e in own - base if not e.startswith(("read:", "write:"))}
            out.append((probe.entry, fit, probe.head, row))
        return out


# The answer -----------------------------------------------------------------------------------------------------


def ceiling(text: str) -> tuple[str, ...]:
    """`pure`, `effects(alloc, io)` or `alloc, io`, as the effects a declared ceiling names."""
    return tuple(w for w in re.findall(r"[\w:.]+", text) if w != "effects")


def within(row: set[str] | None, limit: tuple[str, ...]) -> bool:
    """Whether a row stays within a ceiling, less the reads and writes of what the call is passed and what a bound's
    own member does; an unknown row never does."""
    footprint = ("read:", "write:", "lane:", "bound:")
    return row is not None and all(e in allowed(limit) or e.startswith(footprint) for e in row)


def candidates(index: Index, given: list[Type] | None, wanted: Type | None):
    """(entry, fit, head, row) of everything a query may name: every entry for words alone (`given` None), and
    otherwise each call the checker accepts, a builtin's with the call it wrote and the row that call adds."""
    if given is None:
        yield from ((entry, (0, 0), "", entry.row) for entry in index.entries)
        return
    for entry, fit, head, row in Probes(index, given, wanted).judged():
        yield entry, fit, head, row if entry.f is None else entry.row


def find(source: str | None, words: str = "", takes: list[str] | tuple[str, ...] = (), returns: str | None = None,
         effects: str | None = None, limit: int = LIMIT) -> dict[str, Any]:  # fmt: skip
    """The answer to one query over the builtins, the library and `source`'s own functions (None: none). Best
    first: the fit to the types, then the words matched, the program's own before the library and the library before
    a builtin, a function of the module of a type given or wanted first, and fewer known effects before more."""
    typed = bool(takes) or returns is not None
    if not words.strip() and not typed:
        fail("E-REQUEST", "Ask with words, or with the types of the values you have and the type you want back.")
    if not 1 <= limit <= 200:
        fail("E-REQUEST", "A limit is 1..200 hits.")
    index, refusal = indexed_or_refused(source)
    given = [index.given(t) for t in takes] if typed else None
    wanted = index.given(returns) if returns is not None else None
    home = {index.c.p.modules.get(t.value.name) for t in [*(given or []), *([wanted] if wanted else [])]}
    scores = worded(index.entries, words) if words.strip() else None
    bound = ceiling(effects) if effects else None
    best: dict[str, tuple[tuple, str]] = {}
    for entry, fit, head, row in candidates(index, given, wanted):
        if (scores is not None and entry.name not in scores) or (bound is not None and not within(row, bound)):
            continue
        matched, weight = (scores or {}).get(entry.name, (0, 0))
        near = entry.f is not None and entry.f.module in home
        cost = (row is None, len(beyond(row)))  # an unknown row after every known one
        called = head.partition("(")[0]  # a builtin the words name before the others its entry covers
        order = (called not in words.split(), entry.names.index(called)) if head else (False, 0)
        key = (*fit, -matched, -weight, entry.rank, not near, cost, entry.name, order)
        if entry.name not in best or key < best[entry.name][0]:
            best[entry.name] = (key, entry.line(head, row))
    hits = sorted(best.values())
    record: dict[str, Any] = {"hits": [line for _, line in hits[:limit]], "more": max(0, len(hits) - limit)}
    if refusal:
        record["program"] = f"refused with {refusal}: only the builtins and the library were searched"
    return record


def lines(record: dict[str, Any]) -> str:
    """The answer as a person at a terminal reads it."""
    more = [f"{record['more']} more: --limit shows them."] if record["more"] else []
    empty = [] if record["hits"] or more else ["Nothing fits."]
    return "\n".join([*record["hits"], *more, *empty, *([record["program"]] if "program" in record else [])])

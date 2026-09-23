"""The emitted C++ of each function, and when two emissions are the same code.

`literals` and `zero` are identities of C++ semantics: a string bound to a local and read once is the string written
where it is read, and `n - 0` is `n`, the extent a call leaves out for the part `v[0..n]`. `unguarded` is not one: it
writes every guard as the operation it guards, so two texts equal under it differ at most in which guards they emit.
`tools/checks/emission_identity.py` applies them to whole programs and `cairn diff` to one function at a time.

`canonical` is one function's own emitted text with its locals, parameters and compiler temporaries renamed in the
order they first appear, the function's own symbol written as `cf_@`, the callees a rename maps written under their
old names, and the definition of every type and table it names appended. Two functions with equal canonical texts
are the same C++ up to a consistent renaming, which is what `identical-code` in a diff means.
"""

from __future__ import annotations

import re
from typing import Any

from ..compiler.cairnc import compile_program
from ..compiler.codegen import Emitter, mangle
from ..compiler.tree import Program

LITERAL = re.compile(r'^\s*const std::uint8_t\* const (v_\w+) = (reinterpret_cast<const std::uint8_t\*>\("(?:[^"\\]|\\.)*"\));\n', re.M)  # fmt: skip
ZERO = re.compile(r"\((v_\w+|static_cast<std::size_t>\(\d+ULL\)) - static_cast<std::size_t>\(0ULL\)\)")


def literals(cpp: str) -> str:
    out = []
    for chunk in re.split(r"(?m)^(?=\S)", cpp):  # one top-level declaration at a time
        at = 0
        while found := LITERAL.search(chunk, at):
            name, rest = found.group(1), chunk[found.end() :]
            again = re.search(rf"const std::uint8_t\* const {name} =", rest)  # the same name bound again
            scope, tail = (rest[: again.start()], rest[again.start() :]) if again else (rest, "")
            if len(re.findall(rf"\b{name}\b", scope)) != 1:
                at = found.end()
                continue
            chunk = chunk[: found.start()] + re.sub(rf"\b{name}\b", lambda _, f=found: f.group(2), scope) + tail
            at = found.start()
        out.append(chunk)
    return "".join(out)


GUARD = re.compile(
    r"\bcr::(at|part|view|disjoint|add|sub|mul|divide|remainder|convert|truncate|shr|shl_wrap)\b(<[^<>()]*>)?\("
)
UNGUARDED = {
    "at": lambda t, a: f"{a[0]}[{a[1]}]",
    "part": lambda t, a: f"({a[0]} + {a[1]})",
    "view": lambda t, a: "",
    "disjoint": lambda t, a: "",
    "add": lambda t, a: f"({a[0]} + {a[1]})",
    "sub": lambda t, a: f"({a[0]} - {a[1]})",
    "mul": lambda t, a: f"({a[0]} * {a[1]})",
    "divide": lambda t, a: f"({a[0]} / {a[1]})",
    "remainder": lambda t, a: f"({a[0]} % {a[1]})",
    "convert": lambda t, a: f"static_cast{t}({a[0]})",
    "truncate": lambda t, a: f"static_cast{t}({a[0]})",
    "shr": lambda t, a: f"static_cast{t}(std::uint64_t({a[0]}) >> {a[1]})",
    "shl_wrap": lambda t, a: f"static_cast{t}(std::uint64_t({a[0]}) << {a[1]})",
}


def arguments(text: str, at: int) -> tuple[list[str], int]:
    """The top-level arguments of the call whose `(` ends just before `at`, and the index past its `)`."""
    depth, start, out = 0, at, []
    for i in range(at, len(text)):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}" and depth:
            depth -= 1
        elif ch == ")" or (ch == "," and not depth):
            out.append(text[start:i].strip())
            start = i + 1
            if ch == ")":
                return out, i + 1
    raise ValueError("an unbalanced call in the emitted C++")


def unguarded(cpp: str) -> str:
    """Every guard written as the operation it guards. The last one is rewritten first: a guard nested in another's
    arguments starts after it, and a rewrite leaves all the text before it where it was."""
    for m in reversed(list(GUARD.finditer(cpp))):
        args, end = arguments(cpp, m.end())
        written = UNGUARDED[m.group(1)](m.group(2) or "", args)
        if not written and cpp.startswith(";\n", end):  # An entry check is a statement of its own.
            line = cpp.rfind("\n", 0, m.start()) + 1
            cpp = cpp[:line] + cpp[end + 2 :]
            continue
        cpp = cpp[: m.start()] + written + cpp[end:]
    return cpp


def guard_count(cpp: str) -> int:
    return len(GUARD.findall(cpp))


NORMALIZE = {"literals": literals, "zero": lambda cpp: ZERO.sub(lambda m: m.group(1), cpp), "guards": unguarded}

LOCAL = re.compile(r"(?<![\w.])(?<!->)v_\w+")  # a local or a parameter; after `.` or `->` it is a field
COUNTED = re.compile(r"\b(cr_[a-z]+_|[un])(\d+)\b")  # the emitter's numbered temporaries (Emitter.fresh)
NAMED = re.compile(r"\bc(?:t|dt|vt)_\w+")  # a type, a dyn table or a vtable the text refers to
CALLED = re.compile(r"\bcf_(\w+)")


def emitted(source: str) -> tuple[Program, dict[str, Any], dict[str, str], dict[str, str]]:
    """The checked program, its per-function receipts, each function's own C++ (tests included) and the definition
    of every type and table the program emits, by C name. Lowering is the one `cairn build` runs, with the guards
    the elision audit accepts left out."""
    p, checker, receipts = compile_program(source)
    emitter = Emitter(p, checker, roots=tuple(f.name for f in p.functions))  # every function, tests included
    for name, verdict in emitter.elision.items():
        if name in receipts:
            receipts[name]["discharged_check_sites"] = verdict["accepted"]
    interface, bodies = emitter.units()
    written = [f for f in p.functions if not f.extern]
    code = {f.name: "\n".join(lines) for f, (_, lines) in zip(written, bodies, strict=True)}
    receipts["$device"] = "cairn_gpu.hpp" in emitter.headers  # a device program is compiled here and never run
    return p, receipts, code, definitions(interface)


def definitions(interface: list[str]) -> dict[str, str]:
    """Each top-level declaration of the shared interface that defines a type or a table, under the name it defines."""
    out: dict[str, str] = {}
    chunk: list[str] = []
    for line in [*interface, ""]:
        if (line and line[0].isspace()) or line.startswith(("}", "};")):
            chunk.append(line)
            continue
        # A tag-only enum is emitted as `enum class ct_E`, so the name may follow `class`.
        if chunk and (m := re.search(r"\b(?:struct|union|enum(?:\s+class)?)\s+(c(?:t|dt|vt)_\w+)|\b(c(?:t|dt|vt)_\w+)\s*(?:\[|=)",
                                     chunk[0])):  # fmt: skip
            out.setdefault(m.group(1) or m.group(2), "\n".join(chunk))
        chunk = [line] if line else []
    return out


def canonical(name: str, code: str, types: dict[str, str], renamed: dict[str, str] | None = None) -> str:
    """One function's C++ in the form two consistent renamings of it share (see the module docstring)."""
    text = literals(NORMALIZE["zero"](code))
    own = "cf_" + mangle(name)
    text = re.sub(rf"\b{re.escape(own)}\b", "cf_@", text)
    moved = {mangle(new): mangle(old) for new, old in (renamed or {}).items()}
    text = CALLED.sub(lambda m: "cf_" + moved.get(m.group(1), m.group(1)), text)
    locals_: dict[str, str] = {}
    text = LOCAL.sub(lambda m: locals_.setdefault(m.group(), f"v_{len(locals_)}"), text)
    numbers: dict[str, str] = {}
    text = COUNTED.sub(lambda m: m.group(1) + numbers.setdefault(m.group(2), str(len(numbers))), text)
    seen: list[str] = []
    pending = sorted(set(NAMED.findall(text)))
    while pending:  # the types it names, and the types those name, each once
        t = pending.pop()
        if t in seen or t not in types:
            continue
        seen.append(t)
        pending += sorted(set(NAMED.findall(types[t])) - set(seen))
    return text + "".join(f"\n// {t}\n{types[t]}" for t in sorted(seen))

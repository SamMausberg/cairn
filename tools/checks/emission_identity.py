#!/usr/bin/env python3
"""Whether a source change left every program's emitted C++ as it was.

`snapshot OUT` records, for every example project and single-file example, one program importing
every std module, every CAIRN program written into a test and every `cairn` block of the docs, a
digest of the C++ it emits with its effect rows, or the code that refuses it. `compare OUT` fails
and names each program whose record changed. A `--normalize` rewrite is applied to the C++ on both
sides first. The first two are identities of C++ semantics:

  literals  a string bound to a local and read once is the string written where it is read
  zero      `n - 0` is `n`, which is the extent a call leaves out for the part `v[0..n]`

The third is not: `guards` writes every guard as the operation it guards (`cr::at(p, i, n)` as `p[i]`,
`cr::sub<T>(a, b)` as `(a - b)`, a part as `p + lo`, an entry check as nothing), so two records equal under
it differ at most in which guards they emit. The record also counts each program's guards, and `compare`
names every program that now emits fewer or more of them: a change that should only discharge guards is
equal under `guards` and has no program in `more_guards`.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.projects.project import ProjectError, load_project

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


def programs() -> dict[str, str]:
    out = {}
    for manifest in sorted((ROOT / "examples").rglob("*.toml")):
        try:
            out["project:" + str(manifest.relative_to(ROOT))] = load_project(manifest).source
        except ProjectError as e:
            out["project:" + str(manifest.relative_to(ROOT))] = "unloadable: " + str(e)
    projects = {m.parent for m in (ROOT / "examples").rglob("cairn.toml")}
    for f in sorted((ROOT / "examples").rglob("*.cairn")):
        if not projects & set(f.parents):
            out["file:" + str(f.relative_to(ROOT))] = f.read_text(encoding="utf-8")
    std = sorted("std." + p.stem for p in (ROOT / "src/cairn/std").glob("*.cairn"))
    out["std"] = "".join(f"import {m};\n" for m in std) + "fn main() -> i32 { return 0; }\n"
    for test in sorted((ROOT / "tests").rglob("*.py")):
        for node in ast.walk(ast.parse(test.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "fn " in node.value:
                out[f"test:{test.relative_to(ROOT)}:{node.lineno}"] = node.value
    for doc in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for i, block in enumerate(re.findall(r"```cairn[^\n]*\n(.*?)```", doc.read_text(encoding="utf-8"), re.S)):
            out[f"doc:{doc.relative_to(ROOT)}:{i}"] = block
    return out


def record(item: tuple[str, str, tuple[str, ...]]) -> tuple[str, str, int | None]:
    name, source, rewrites = item
    if source.startswith("unloadable: "):
        return name, source, None
    try:
        cpp, receipt = compile_source(source)
    except Diagnostic as e:
        return name, "refused " + e.data["code"], None
    guards = guard_count(cpp)
    for rewrite in rewrites:
        cpp = NORMALIZE[rewrite](cpp)
    rows = json.dumps({n: r["effects"] for n, r in receipt["functions"].items()}, sort_keys=True)
    return name, "emits " + hashlib.sha256((cpp + rows).encode()).hexdigest(), guards


def snapshot(rewrites: tuple[str, ...]) -> tuple[dict[str, str], dict[str, int]]:
    with ProcessPoolExecutor(4) as pool:
        taken = list(pool.map(record, [(n, s, rewrites) for n, s in programs().items()], chunksize=8))
    return {n: d for n, d, _ in taken}, {n: g for n, _, g in taken if g is not None}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["snapshot", "compare"])
    p.add_argument("record", type=Path)
    p.add_argument("--normalize", action="append", default=[], choices=sorted(NORMALIZE))
    a = p.parse_args()
    now, guards = snapshot(tuple(a.normalize))
    if a.command == "snapshot":
        taken = {"normalize": a.normalize, "programs": now, "guards": guards}
        a.record.write_text(json.dumps(taken, indent=1, sort_keys=True) + "\n")
        print(json.dumps({"programs": len(now), "record": str(a.record)}))
        return 0
    before = json.loads(a.record.read_text())
    if before["normalize"] != a.normalize:
        p.error(f"the record was taken with --normalize {before['normalize']}; compare the same way")
    changed = sorted(n for n, v in before["programs"].items() if now.get(n) != v)
    added = sorted(set(now) - set(before["programs"]))
    counted = {n: (k, guards[n]) for n, k in before.get("guards", {}).items() if guards.get(n, k) != k}
    fewer, more = (sorted(n for n, (k, g) in counted.items() if (g < k) == less) for less in (True, False))
    report = {"programs": len(now), "changed": changed, "new": added, "fewer_guards": fewer, "more_guards": more}
    print(json.dumps(report, indent=1))
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())

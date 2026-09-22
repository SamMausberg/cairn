"""`cairn explain`: where a program pays at run time, per function and per `.cairn` line, as observed statically.

Everything here is read, not measured. Guards, allocations and synchronization points are found in the C++ the
emitter wrote, at the line of CAIRN each statement came from. Vectorization verdicts are what clang's optimization
record says about that C++ compiled with the build's own flags. Nothing is run and nothing is timed.

A guard kind is named as the checker names it in `syntactic_check_sites`, so the two counts sit side by side: `sites`
is what the checker found that needs a guard, `emitted` is what the lowering wrote. `emitted` is lower where the
lowering proved a guard unnecessary and left it out, and higher where it writes one source expression twice (the base
of a part is written for its data and again for its size, so a guard inside it is checked twice).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..compiler.cairnc import RUNTIME_FILES, compile_program
from ..compiler.codegen import Emitter, mangle
from ..compiler.modules import library_path
from ..compiler.tree import Expr, Function, Stmt
from ..projects.toolchain import REMARKS, find, flags

GUARDS = {  # The checker's name for each guard kind, and the runtime calls that are that guard in emitted C++.
    "bounds": ("cr::at(", "cr::part("),
    "overflow": ("cr::add<", "cr::sub<", "cr::mul<"),
    "division": ("cr::divide<", "cr::remainder<"),
    "conversion": ("cr::convert<", "cr::truncate<"),
    "shift": ("cr::shl_wrap<", "cr::shr<"),
    "view_entry": ("cr::view(",),
    "disjointness": ("cr::disjoint(",),  # One per pair of views where one writes; the checker does not count them.
    "tag": ("cr::trap()",),
    "callable": ("cr::callable(",),
}
SYNCHRONIZATION = {  # What blocks, or starts something to block on later, and how the emitter spells it.
    "wait": ".wait()",
    "collect": ".collect()",
    "spawn": "::spawn(",
    "lock": ".with(",
    "host region completes": "cr::par::run(",
    "pooled reduce completes": "cr::par::reduce<",
    "device region completes": "cr::gpu::launch(",
    "queued device work": "cr::gpu::launch_async(",
    "transfer": "cr::gpu::copy(",
    "device reduce reads back": "cr::gpu::reduce",
    "device compact reads back": "cr::gpu::compact",
}
ALLOCATION = re.compile(r"(cr::(?:gpu::)?(?:Buf|Buffer|Pinned|Unified)<[^()=;]*?>)\s*\w*\(")
CALL = re.compile(r"\bcf_(\w+)\(")
LINE = re.compile(r'^\s*#line (\d+) "(.*)"$')
MAX_SOURCE = 2_000_000
PACKAGE = Path(__file__).resolve().parents[1]


class Located(Emitter):
    """The emitter with every emitted line placed where its statement was written: a linked library function at its
    own file, a function a library recipe generated at that recipe's file, anything else through `origin`.

    A statement that lowers to several C++ lines repeats its `#line` before each one, so a loop header three lines
    into a lowered `for` is still reported at the `for`."""

    def __init__(self, program, checker, origin):
        self.written, self.home, self.here = origin, None, None
        super().__init__(program, checker, self.at)

    def at(self, line: int) -> tuple[str, int]:
        self.here = (str(self.home), line) if self.home else self.written(line)
        return self.here

    def put(self, s: str = ""):
        if self.here and not s.lstrip().startswith("#line"):
            super().put('#line {1} "{0}"'.format(*self.here))
        super().put(s)

    def function(self, f: Function):
        self.home, self.here = library(self.p, f), None
        super().function(f)
        self.here = None  # The closing brace and the next signature belong to no statement.


def library(p, f: Function) -> Path | None:
    """The packaged file whose text `f`'s statements carry line numbers of, when it is not the program's own."""
    module = p.modules.get(f.name, f.module)
    if f.source_name.startswith("derive "):  # One library recipe of that name is where its body was copied from.
        name = f.source_name.split()[1].rsplit(".", 1)[-1]
        found = {r.module for full, r in p.recipes.items() if full.rsplit(".", 1)[-1] == name}
        module = found.pop() if len(found) == 1 else ""
    return library_path(module) if module in p.sources else None


def discharged(v: Any, found: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """(line, kind) of each guard site under `v` that the checker showed cannot fail, so lowering left it out."""
    if isinstance(v, list):
        for x in v:
            discharged(x, found)
    elif isinstance(v, Expr):
        if v.established:
            kind = {"index": "bounds", "binary": "overflow"}.get(v.tag)
            found.append((v.line, kind or ("shift" if v.val in {"shl_wrap", "shr"} else "conversion")))
        discharged(v.args, found)
        discharged(v.ref.body if v.tag == "lambda" else [v.ref] if isinstance(v.ref, Stmt) else [], found)
    elif isinstance(v, Stmt):
        discharged([v.exprs, v.body, v.other, *(a.body for a in v.arms)], found)
    return found


def remarks(record: str, names: dict[str, str]) -> list[dict[str, Any]]:
    """clang's loop-vectorize record, one entry per remark: which CAIRN function, where, the verdict and why."""
    out = []
    for doc in re.split(r"^--- !", record, flags=re.M)[1:]:
        kind = doc.split("\n", 1)[0].strip()
        where = re.search(r"File: (?:'((?:[^']|'')*)'|([^,']*)),\s*Line: (\d+),\s*Column: (\d+)", doc)
        symbol = re.search(r"^Function:\s+(\S+)", doc, re.M)
        text = "".join(v.strip().strip("'").replace("''", "'") for v in re.findall(r"^\s+- \w+:\s+(.*)$", doc, re.M))
        if where and symbol:
            owner = function_of(symbol.group(1), names)
            file = (where.group(1) or "").replace("''", "'") or where.group(2).strip()
            out.append({"function": owner, "file": file, "line": int(where.group(3)), "column": int(where.group(4)),
                        "kind": kind, "message": text})  # fmt: skip
    return out


def function_of(symbol: str, names: dict[str, str]) -> str:
    """The CAIRN function a C++ symbol belongs to: `cf_name`, `_Z<len>cf_name...`, or a lambda nested in one."""
    for size, rest in re.findall(r"(\d+)(cf_\w+)", symbol) or [("", symbol)]:
        found = rest[: int(size)] if size else rest
        if found.removeprefix("cf_") in names:
            return names[found.removeprefix("cf_")]
    return "(runtime) " + symbol


def verdicts(entries: list[dict[str, Any]], show) -> list[dict[str, Any]]:
    """One verdict per loop: vectorized where clang says so, with its width; else the reasons clang gave."""
    loops: dict[tuple, dict[str, Any]] = {}
    for r in entries:
        loop = loops.setdefault((r["file"], r["line"], r["column"]), {"at": show(r["file"], r["line"], r["column"]),
                                                                      "vectorized": [], "reasons": []})  # fmt: skip
        if r["kind"] == "Passed":
            loop["vectorized"].append(r["message"].removeprefix("vectorized loop "))
        elif r["kind"] == "Analysis":
            loop["reasons"].append(r["message"].removeprefix("loop not vectorized: "))
    for loop in loops.values():
        loop["vectorized"] = sorted(set(loop["vectorized"]))
        loop["reasons"] = sorted(set(loop["reasons"]))
        loop["verdict"] = "vectorized" if loop["vectorized"] else "not vectorized"
        if not loop["vectorized"]:
            del loop["vectorized"]
    return sorted(loops.values(), key=lambda loop: loop["at"])


def explain(source: str, origin: Any = "program.cairn", symbols: set[str] | None = None, cxx: str = "clang++",
            arch: str | None = None, root: Path | None = None, timeout: int = 120) -> dict[str, Any]:  # fmt: skip
    """The static cost picture of every function of `source`, or of `symbols` alone.

    `origin` names the file, or maps a line of `source` to (file, line), as a `--debug` build does. Remarks need
    clang; under another compiler the rest is still reported and `vectorization` says why it is absent.
    """
    if len(source.encode()) > MAX_SOURCE:
        raise ValueError("Source exceeds the 2 MB limit.")
    p, checker, receipts = compile_program(source)
    at = (lambda line: (origin, line)) if isinstance(origin, str) else origin
    emitter = Located(p, checker, at)
    interface, bodies = emitter.units()
    written = [f for f in p.functions if not f.extern]
    names = {mangle(f.name): f.name for f in p.functions}

    def show(file: str, line: int, column: int = 0) -> str:
        """A location as the reader knows it: under the project root, or in the package (`cairn/std/...`)."""
        path = Path(file).resolve()
        if root is not None and path.is_relative_to(root.resolve()):
            file = str(path.relative_to(root.resolve()))
        elif path.is_relative_to(PACKAGE):
            file = "cairn/" + str(path.relative_to(PACKAGE))
        return f"{file}:{line}" + (f":{column}" if column else "")

    functions: dict[str, dict[str, Any]] = {}
    for f, (_, lines) in zip(written, bodies, strict=True):
        if symbols is not None and f.name not in symbols:
            continue
        home = library(p, f)
        head = (show(str(home), f.line) if home else show(*at(f.line))) if f.line else f.name
        guards: dict[str, dict[str, int]] = {}
        allocations, synchronization, costly = [], [], []
        here = head  # Entry guards sit before the first statement: they belong to the declaration.
        for text in lines:
            if found := LINE.match(text):
                here = show(found.group(2), int(found.group(1)))
                continue
            for kind, spellings in GUARDS.items():
                if n := sum(text.count(s) for s in spellings):
                    guards.setdefault(here, {})[kind] = guards.get(here, {}).get(kind, 0) + n
            allocations += [{"at": here, "owner": m.group(1)} for m in ALLOCATION.finditer(text)]
            synchronization += [{"at": here, "kind": kind} for kind, s in SYNCHRONIZATION.items() if s in text]
            for callee in CALL.findall(text):
                row = set(receipts.get(names.get(callee, ""), {}).get("effects", ()))
                paid = sorted(row & {"alloc", "gpu_alloc", "join", "spawn", "lock", "io"} | {
                    e for e in row if e.startswith(("par:", "transfer:"))})  # fmt: skip
                if paid and names[callee] != f.name:
                    costly.append({"at": here, "calls": names[callee], "effects": paid})
        left_out: dict[str, dict[str, int]] = {}
        for line, kind in discharged(f.body, []):
            where = show(str(home), line) if home else show(*at(line))
            left_out.setdefault(where, {})[kind] = left_out.get(where, {}).get(kind, 0) + 1
        emitted: dict[str, int] = {}
        for kinds in guards.values():
            for kind, n in kinds.items():
                emitted[kind] = emitted.get(kind, 0) + n
        sites = receipts[f.name]["syntactic_check_sites"]
        functions[f.name] = {
            "at": head,
            "effects": receipts[f.name]["effects"],
            "guards": {
                "sites": {k: v for k, v in sorted(sites.items()) if k in GUARDS},
                "emitted": dict(sorted(emitted.items())),
                "discharged": receipts[f.name].get("discharged_check_sites", {}),
                "by_line": guards,
                "discharged_by_line": left_out,
            },
            "allocations": allocations,
            "costly_calls": costly,
            "synchronization": synchronization,
        }
    report = {
        "schema": "cairn.explain/1",
        "observed": "Read from the emitted C++ and the compiler's optimization record; nothing was run or timed.",
        "functions": functions,
    }
    report["vectorization"] = vectorize(p, interface, bodies, names, cxx, arch, show, symbols, functions, timeout)
    return report


def vectorize(p, interface, bodies, names, cxx, arch, show, symbols, functions, timeout) -> dict[str, Any]:
    """Compile once with the build's flags plus `REMARKS`, and hand each function the loop verdicts inside it."""
    if "cairn_gpu.hpp" in "\n".join(interface):
        return {"status": "not-run", "reason": "A device program compiles under nvcc; remarks come from host clang."}
    if Path(cxx).name.split("-")[0] != "clang++":
        return {"status": "not-run", "reason": f"Optimization remarks are read from clang++; {cxx} was named."}
    cpp = "\n".join([*interface, *(line for _, lines in bodies for line in lines)]) + "\n"
    with tempfile.TemporaryDirectory(prefix="cairn-explain-") as scratch:
        directory = Path(scratch)
        (directory / "program.cpp").write_text(cpp, encoding="utf-8")
        for name, text in RUNTIME_FILES.items():
            (directory / name).write_text(text, encoding="utf-8")
        record = directory / "program.yaml"
        native = [f for f in flags(arch, "library") if f != "-shared"] + REMARKS
        command = [find(cxx), *native, "-c", str(directory / "program.cpp"), "-o", str(directory / "program.o"),
                   f"-foptimization-record-file={record}"]  # fmt: skip
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if done.returncode:
            return {"status": "compile-failed", "stderr": done.stderr[:4000]}
        entries = remarks(record.read_text(encoding="utf-8"), names)
    for r in entries:  # A runtime header was copied beside the program: name the packaged one instead.
        if Path(r["file"]).parent.resolve() == directory.resolve():
            r["file"] = str(PACKAGE / "runtime" / Path(r["file"]).name)
    version = subprocess.run([find(cxx), "--version"], capture_output=True, text=True, timeout=10).stdout
    runtime = [r for r in entries if r["function"].startswith("(runtime)")]
    for name, entry in functions.items():
        entry["loops"] = verdicts([r for r in entries if r["function"] == name], show)
    return {
        "status": "observed",
        "compiler": version.split("\n", 1)[0],
        "flags": native,
        "outside_functions": len(runtime),
        "note": "A loop is reported where clang placed it; one inlined from the runtime or std shows that file.",
    }

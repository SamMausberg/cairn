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
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..compiler import compilations
from ..compiler.cairnc import write_program
from ..compiler.cooperative.cooperative import SHUFFLES, VOTES
from ..compiler.device import layouts
from ..compiler.lower.codegen import Emitter, demangled, mangle
from ..compiler.primitives import wide
from ..compiler.syntax.modules import library_path
from ..compiler.syntax.tree import Expr, Function, Stmt
from ..perf.regions import walked
from ..projects.toolchain import REMARKS, find, flags
from ..projects.toolchain import version as compiler_version

GUARDS = {  # The checker's name for each guard kind, and the runtime calls that are that guard in emitted C++.
    "bounds": ("cr::at(", "cr::part(", "cr::atomic::"),
    "overflow": ("cr::add<", "cr::sub<", "cr::mul<"),
    "division": ("cr::divide<", "cr::remainder<"),
    "conversion": ("cr::convert<", "cr::truncate<"),
    "shift": ("cr::shl_wrap<", "cr::shr<"),
    "view_entry": ("cr::view(",),
    "disjointness": ("cr::disjoint(",),  # One per pair of views where one writes; the checker does not count them.
    "tag": ("cr::trap()",),
    "callable": ("cr::callable(",),
    "assert": ("cr::check(", "cr::check_eq("),
    "layout": ("cr::layout::within(",),  # A coordinate or participant checked against its layout (layouts.py).
    "fragment": ("cr::frag::loaded<", "cr::frag::stored(", "cr::frag::get(", "cr::frag::set("),  # fragments.py
    "wide": ("cr::wide::load<", "cr::wide::store<"),  # K elements inside the array, the first on the width (wide.py)
}
SYNCHRONIZATION = {  # What blocks, or starts something to block on later, and how the emitter spells it.
    "wait": ".wait()",
    "collect": ".collect()",
    "spawn": "::spawn(",
    "lock": ".with(",
    "host region completes": "cr::par::run(",
    "pooled reduce completes": "cr::par::reduce<",
    "device region completes": "cr::gpu::run",  # its own stream, on the thread's execution context
    "queued device work": "cr::gpu::queue",
    "transfer": "cr::gpu::copy_on(",
    "device reduce reads back": "cr::gpu::reduce_on",
    "device reduce stays on the device": "cr::gpu::reduce_into<",
    "device scan reads back": "cr::gpu::scan_on",
    "device scan stays on the device": "cr::gpu::scan_into<",
    "device compact reads back": "cr::gpu::compact_on",
    "cooperative region completes": "cr::coop::launch<",  # compiler/cooperative/cooperative.py, on the execution context
    "host cooperative region completes": "cr::coop::run<",
    "barrier": "cr_blk.sync()",
    "pipeline copies start": ".fill(cr_blk,",  # compiler/cooperative/pipelines.py: cp.async, committed as one group
    "pipeline wait": ".template wait<",  # cp.async.wait_group N, then the block's barrier
    "warp shuffle": "cr_blk.shuffle",
    "warp reduction": "cr_blk.reduce(",
}
ALLOCATION = re.compile(r"(cr::(?:gpu::)?(?:Buf|Buffer|Pinned|Unified)<[^()=;]*?>)\s*\w*\(")
CALL = re.compile(r"\bc[fi]_(\w+)\(")  # A checked entry `cf_` or the lean body `ci_`.
PAID = {"alloc", "gpu_alloc", "join", "spawn", "lock", "io"}  # With par: and transfer:, what makes a call costly.
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
    """The CAIRN function a C++ symbol belongs to, or the runtime's."""
    found = demangled(symbol, names)
    return names[found] if found else "(runtime) " + symbol


def verdicts(entries: list[dict[str, Any]], show) -> list[dict[str, Any]]:
    """One verdict per loop: vectorized where clang says so, with its width; else the reasons clang gave."""
    loops: dict[tuple, tuple[set, set]] = defaultdict(lambda: (set(), set()))
    for r in entries:
        passed, reasons = loops[r["file"], r["line"], r["column"]]
        if r["kind"] == "Passed":
            passed.add(r["message"].removeprefix("vectorized loop "))
        elif r["kind"] == "Analysis":
            reasons.add(r["message"].removeprefix("loop not vectorized: "))
    out = [{"at": show(*where), **({"vectorized": sorted(passed)} if passed else {}), "reasons": sorted(reasons),
            "verdict": "vectorized" if passed else "not vectorized"}
           for where, (passed, reasons) in loops.items()]  # fmt: skip
    return sorted(out, key=lambda loop: loop["at"])


def cooperative(f: Function, place, sizeof) -> list[dict[str, Any]]:
    """Each cooperative region of `f` as the checker laid it out, at its lines: its block, its shared arrays and
    pipeline stages with their bytes, every barrier, every pipeline wait with the copies it leaves in flight
    (`cp.async.wait_group N`), and every warp collective and fragment operation."""
    out = []
    for s in walked(f.body):
        if s.tag != "blocks" or s.ref is None:
            continue
        block = s.ref
        found: dict[str, Any] = {"at": place(s.line), "placement": "device" if block.device else "host",
                                 "threads": block.count, "thread_extents": list(block.extents),
                                 "shared_bytes": block.bytes, "shared": [], "pipelines": [], "barriers": [],
                                 "waits": [], "copies": [], "warp_collectives": [], "fragments": []}  # fmt: skip
        for x in walked(s.body):
            if x.tag == "shared":
                element, count, _ = block.shared[x.name]
                found["shared"].append({"at": place(x.line), "name": x.name, "bytes": sizeof(element) * count})
            elif x.tag == "pipeline":
                p = block.pipelines[x.name]
                stage = -(-p.size * sizeof(p.element) // 16) * 16
                found["pipelines"].append({"at": place(x.line), "name": x.name, "depth": p.depth,
                                           "stage_bytes": stage, "bytes": stage * p.depth})  # fmt: skip
            elif x.tag == "barrier":
                found["barriers"].append(place(x.line))
            elif x.tag == "warp_reduce":
                found["warp_collectives"].append({"at": place(x.line), "operation": f"reduce {x.op} warp"})
            for e in (e for top in x.exprs for e in calls(top)):
                ref = e.ref if isinstance(e.ref, tuple) else ()
                if ref[:1] == ("stage",) and ref[2] == "wait":
                    found["waits"].append({"at": place(e.line), "pipeline": ref[1],
                                           "wait_group": ref[3] if len(ref) > 3 else 0})  # fmt: skip
                elif ref[:1] == ("stage",) and ref[2] == "fill":
                    found["copies"].append({"at": place(e.line), "pipeline": ref[1]})
                elif e.val in SHUFFLES | VOTES:
                    found["warp_collectives"].append({"at": place(e.line), "operation": e.val})
                elif e.val in {"mma_unordered", "mma_load", "mma_store"} and ref[:1] == ("builtin",):
                    found["fragments"].append({"at": place(e.line), "operation": e.val})
        out.append({k: v for k, v in found.items() if v != []})
    return out


def widened(f: Function, place) -> list[dict[str, Any]]:
    """Each wide load and store of `f`, at its line: the elements and bytes its one access moves on the device and the
    cache operator it asks for. On the host each is that many ordinary loads or stores and the hint says nothing."""
    found = []
    for s in walked(f.body):
        for e in (e for top in s.exprs for e in calls(top)):
            if e.val in wide.NAMES and isinstance(e.ref, tuple) and isinstance(e.ref[-1], wide.Wide):
                found.append({"at": place(e.line), **wide.records(e)})
    return found


def calls(e: Expr) -> list[Expr]:
    found = [e] if e.tag == "call" else []
    for a in e.args:
        if isinstance(a, Expr):
            found += calls(a)
    return found


def plain(tally: dict[str, Counter]) -> dict[str, dict[str, int]]:
    return {k: dict(v) for k, v in tally.items()}


def explain(source: str, origin: Any = "program.cairn", symbols: set[str] | None = None, cxx: str = "clang++",
            arch: str | None = None, root: Path | None = None, timeout: int = 120) -> dict[str, Any]:  # fmt: skip
    """The static cost picture of every function of `source`, or of `symbols` alone.

    `origin` names the file, or maps a line of `source` to (file, line), as a `--debug` build does. Remarks need
    clang; under another compiler the rest is still reported and `vectorization` says why it is absent.
    """
    if len(source.encode()) > MAX_SOURCE:
        raise ValueError("Source exceeds the 2 MB limit.")
    p, checker, receipts = compilations.program(source)
    at = (lambda line: (origin, line)) if isinstance(origin, str) else origin
    interface, bodies = Located(p, checker, at).units()
    names = {mangle(f.name): f.name for f in p.functions}

    def show(file: str, line: int, column: int = 0) -> str:
        """A location as the reader knows it: under the project root, or in the package (`cairn/std/...`)."""
        path = Path(file).resolve()
        if root is not None and path.is_relative_to(root.resolve()):
            file = str(path.relative_to(root.resolve()))
        elif path.is_relative_to(PACKAGE):
            file = "cairn/" + str(path.relative_to(PACKAGE))
        return f"{file}:{line}" + (f":{column}" if column else "")

    def costs(f: Function, lines: list[str]) -> dict[str, Any]:
        home = library(p, f)
        place = (lambda line: show(str(home), line)) if home else (lambda line: show(*at(line)))
        head = place(f.line) if f.line else f.name
        guards: dict[str, Counter] = defaultdict(Counter)
        allocations, synchronization, costly = [], [], []
        here = head  # Entry guards sit before the first statement: they belong to the declaration.
        for text in (part for chunk in lines for part in chunk.split("\n")):  # a region's lambda is one chunk
            if found := LINE.match(text):
                here = show(found.group(2), int(found.group(1)))
                continue
            for kind, spellings in GUARDS.items():
                if n := sum(text.count(s) for s in spellings):
                    guards[here][kind] += n
            allocations += [{"at": here, "owner": m.group(1)} for m in ALLOCATION.finditer(text)]
            synchronization += [{"at": here, "kind": kind} for kind, s in SYNCHRONIZATION.items() if s in text]
            for callee in CALL.findall(text):
                row = set(receipts.get(names.get(callee, ""), {}).get("effects", ()))
                paid = sorted(row & PAID | {e for e in row if e.startswith(("par:", "transfer:"))})
                if paid and names[callee] != f.name:
                    costly.append({"at": here, "calls": names[callee], "effects": paid})
        left_out: dict[str, Counter] = defaultdict(Counter)
        for line, kind in discharged(f.body, []):
            left_out[place(line)][kind] += 1
        receipt = receipts[f.name]
        sites = receipt["syntactic_check_sites"]
        return {
            "at": head,
            "effects": receipt["effects"],
            "guards": {
                "sites": {k: v for k, v in sorted(sites.items()) if k in GUARDS},
                "emitted": dict(sorted(sum(guards.values(), Counter()).items())),
                "discharged": receipt.get("discharged_check_sites", {}),
                "by_line": plain(guards),
                "discharged_by_line": plain(left_out),
            },
            "allocations": allocations,
            "costly_calls": costly,
            "synchronization": synchronization,
            **({"cooperative": regions} if (regions := cooperative(f, place, checker.sizeof)) else {}),
            **({"wide": accesses} if (accesses := widened(f, place)) else {}),
        }

    written = [f for f in p.functions if not f.extern and not f.test]  # a test is emitted only where it runs
    functions = {f.name: costs(f, lines) for f, (_, lines) in zip(written, bodies, strict=True)
                 if symbols is None or f.name in symbols}  # fmt: skip
    cpp = "\n".join([*interface, *(line for _, lines in bodies for line in lines)]) + "\n"
    return {
        "schema": "cairn.explain/1",
        "observed": "Read from the emitted C++ and the compiler's optimization record; nothing was run or timed.",
        "functions": functions,
        **({"layouts": layouts.explained(checker)} if p.layouts else {}),
        "vectorization": vectorize(cpp, names, cxx, arch, timeout, functions, show),
    }


def vectorize(cpp: str, names, cxx: str, arch, timeout: int, functions: dict, show) -> dict[str, Any]:
    """Compile once with the build's flags plus `REMARKS`, and hand each function the loop verdicts inside it."""
    if '#include "cairn_gpu.hpp"' in cpp:
        return {"status": "not-run", "reason": "A device program compiles under nvcc; remarks come from host clang."}
    if Path(cxx).name.split("-")[0] != "clang++":
        return {"status": "not-run", "reason": f"Optimization remarks are read from clang++; {cxx} was named."}
    native = [f for f in flags(arch, "library") if f != "-shared"] + REMARKS
    with tempfile.TemporaryDirectory(prefix="cairn-explain-") as scratch:
        directory = Path(scratch).resolve()
        write_program(directory, "program.cpp", cpp)
        record = directory / "program.yaml"
        command = [find(cxx), *native, "-c", str(directory / "program.cpp"), "-o", str(directory / "program.o"),
                   f"-foptimization-record-file={record}"]  # fmt: skip
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if done.returncode:
            return {"status": "compile-failed", "stderr": done.stderr[:4000]}
        entries = remarks(record.read_text(encoding="utf-8"), names)
    for r in entries:  # A runtime header was copied beside the program: name the packaged one instead.
        if Path(r["file"]).parent.resolve() == directory:
            r["file"] = str(PACKAGE / "runtime" / Path(r["file"]).name)
    for name, entry in functions.items():
        entry["loops"] = verdicts([r for r in entries if r["function"] == name], show)
    version = compiler_version(find(cxx))
    return {
        "status": "observed",
        "compiler": version.split("\n", 1)[0],
        "flags": native,
        "outside_functions": sum(r["function"].startswith("(runtime)") for r in entries),
        "note": "A loop is reported where clang placed it; one inlined from the runtime or std shows that file.",
    }

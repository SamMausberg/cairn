"""An `smt-equivalent` verdict run natively before it is given.

Z3's answer is only as good as the translation it was handed: a translator that loses a difference between two
versions says `smt-equivalent` of two programs that differ. So both versions are built as host libraries and called
on the boundary inputs validation generates for the signature (verify/validation/boundaries.py), each call in a
process of its own (verify/validation/isolated_calls.py). Only inputs the precondition admits run, as the concrete
evaluator (concrete.py) judges it. Two runs that tell the versions apart make the verdict a translator fault.

A case decides something when both calls return, and then the results and every rw view must match bit for bit, or
when both trap. When one traps and the other returns it decides only if the one that trapped cannot allocate: the
model takes every allocation to succeed, so a trap in a function that allocates may be an allocation that failed
natively. A call that runs out of time or crashes decides nothing. The replay is finite testing of the cases it ran,
and its silence proves nothing.

A signature validation cannot feed (a record, a sum or an owner crossing the call), device code, no native compiler
or a build that fails leave the verdict as Z3 gave it, and the record says the replay did not run.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...compiler.cairnc import compile_program, compile_source, write_program
from ...compiler.lower.codegen import mangle
from ...projects.toolchain import ProjectError, link_flags, linked
from ...projects.toolchain import command as native_command
from ..validation import boundaries
from ..validation.isolated_calls import Calls

BUDGET = 48  # boundary cases per verdict
COMPILERS = ("clang++", "g++")  # the first one found builds both versions


class NotRun(Exception):
    """Why a verdict could not be replayed; the verdict stands as Z3 gave it."""


@functools.cache
def home() -> tempfile.TemporaryDirectory:
    """Where this process keeps the libraries it built, removed when it exits."""
    return tempfile.TemporaryDirectory(prefix="cairn-replay-")


@functools.cache
def library(source: str, cxx: str) -> tuple[str, dict[str, Any]]:
    """`source` built as a host library, once a process: its path, and each function's receipt."""
    cpp, receipt = compile_source(source)
    if "cuda" in receipt["requires"]:
        raise NotRun("the program holds device code, which the replay does not run")
    where = Path(tempfile.mkdtemp(dir=home().name))
    write_program(where, "program.cpp", cpp)
    line = native_command(cxx, str(where / "program.cpp"), str(where / "program.so"))
    done = subprocess.run([*line, *link_flags(linked((), receipt["modules"]))], capture_output=True, text=True,
                          timeout=300)  # fmt: skip
    if done.returncode:
        raise NotRun(f"a version did not build natively: {done.stderr[-400:]}")
    return str(where / "program.so"), receipt["functions"]


def decides(outcomes: list[dict[str, Any]], allocates: list[bool]) -> bool | None:
    """Whether two native outcomes tell the versions apart, or None when they decide nothing."""
    kinds = [o["outcome"] for o in outcomes]
    if {"timeout", "crash"} & set(kinds):
        return None
    if kinds == ["return", "return"]:
        r, c = outcomes
        return r.get("return") != c.get("return") or r["after"] != c["after"]
    if kinds[0] == kinds[1]:
        return False
    return None if allocates[kinds.index("trap")] else True


def replay(reference: str, candidate: str, symbol: str, admits: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """Both versions of `symbol` run natively on the boundary inputs `admits` lets through: `agrees`, `disagrees`
    with the case and each outcome, or `not-run` with why."""
    try:
        cxx = next((c for c in COMPILERS if shutil.which(c)), None)
        if cxx is None:
            raise NotRun("no native compiler is installed")
        p = compile_program(reference)[0]
        f = next(g for g in p.functions if g.name == symbol)
        params = boundaries.signature(f)
        cases = [c for c in boundaries.generate(f, boundaries.tiles(p, f), {}, budget=BUDGET) if admits(c.args)]
        if not cases:
            raise NotRun("no boundary input satisfies the precondition")
        built = [library(source, cxx) for source in (reference, candidate)]
    except (NotRun, boundaries.Unsupported, ProjectError, subprocess.TimeoutExpired) as e:
        return {"status": "not-run", "reason": str(e)}
    allocates = ["alloc" in rows.get(symbol, {}).get("effects", ()) for _, rows in built]
    calls, undecided = Calls(), 0
    try:
        for case in cases:
            request = {"symbol": "cf_" + mangle(symbol), "params": [x.__dict__ for x in params], "returns": f.ret.name,
                       "case": {"args": case.args, "offsets": case.offsets}, "seconds": 5}  # fmt: skip
            outcomes = [calls({**request, "lib": lib}) for lib, _ in built]
            verdict = decides(outcomes, allocates)
            if verdict:
                return {"status": "disagrees", "compiler": cxx, "case": case.args, "why": case.why,
                        "reference": outcomes[0], "candidate": outcomes[1]}  # fmt: skip
            undecided += verdict is None
    finally:
        calls.close()
    return {"status": "agrees", "compiler": cxx, "cases": len(cases), "undecided": undecided}

"""What a foreign implementation has: vendored C++ or CUDA standing for a CAIRN reference, with exactly its evidence.

A foreign implementation is an implementation (compiler/plans/implementations.py) whose body calls an extern that a
`[foreign]` source of the manifest defines (projects/foreign.py):

    fn histogram_interleaved(n:usize, out:rw<u64>[256], x:ro<u32>[n]) implements histogram_u32 {
      unsafe { histogram_cpp(n, out, x); }
    }

`report` puts together what it has, each claim apart and none standing in for another: the contract its externs
declare, trusted and not checked; whether the program builds with the vendored sources under each compiler; what
ptxas reports of each kernel of a CUDA source, compiled for the device target; and the finite tests validation runs
against the reference (verify/validation.py), with the vendored objects linked. An implementation that runs device
code has its device tests generated and built (verify/device_validation.py) and not run, since device code runs only
under `make gpu`.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..compiler.cairnc import Diagnostic, compile_program, write_program
from ..projects import foreign
from ..projects.build import build
from ..projects.project import Project
from ..projects.target import resolve
from . import boundaries
from .device_validation import cases_as_tests
from .validation import validate

SCHEMA = "cairn.foreign/1"
TRUST = "declared-not-checked"
ON_DEVICE = "it runs device code, and device code runs only under make gpu"


def refuse(message: str) -> Diagnostic:
    return Diagnostic("E-FOREIGN", message)


def identify(project: Project, implementation: str) -> tuple[str, str, list[str], list[tuple[str, tuple[str, ...]]]]:
    """The implementation's full name, its reference, the externs it reaches, and the vendored sources defining them."""
    p, _, receipts = compile_program(project.source)
    found = sorted(n for n, r in receipts.items() if "implements" in r and implementation in (n, n.rsplit(".", 1)[-1]))
    if len(found) != 1:
        raise refuse(f"{implementation} names {'no' if not found else 'more than one'} implementation; a foreign one "
                     "is fn g(...) implements f { unsafe { vendored(...); } }.")  # fmt: skip
    name = found[0]
    fs, reached, todo = {f.name: f for f in p.functions}, set(), [name]
    while todo:  # everything it calls, down to the externs
        for callee in receipts.get(todo.pop(), {}).get("calls", []):
            if callee not in reached:
                reached.add(callee)
                todo += [] if fs[callee].extern else [callee]
    externs = sorted(n for n in reached if fs[n].extern)
    symbols = {fs[n].symbol or n.rsplit(".", 1)[-1] for n in externs}
    sources = [(path, defined) for path, defined in project.foreign if set(defined) & symbols]
    if not sources:
        raise refuse(f"{name} reaches no symbol a [foreign] source of this project defines, so it is not foreign.")
    return name, receipts[name]["implements"], externs, sources


def report(project: Project, implementation: str, *, compilers: tuple[str, ...] = ("clang++", "g++"),
           device_target: str | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """The record of one foreign implementation, `cairn.foreign/1`."""
    name, reference, externs, sources = identify(project, implementation)
    p, _, receipts = compile_program(project.source)
    fs = {f.name: f for f in p.functions}
    kernels = any(foreign.cuda(path) for path, _ in sources)
    contract = {n: {"declared": foreign.shown(fs[n]), "effects": receipts[n]["effects"]} for n in externs}
    pinned = [{"path": path, "symbols": list(symbols), "sha256": foreign.digest(project.root / path)}
              for path, symbols in sources]  # fmt: skip
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "implementation": name,
        "implements": reference,
        "identity": receipts[reference]["implementations"][name]["identity"],
        "sources": pinned,
        "contract": {"externs": contract, "trust": TRUST},
    }
    with tempfile.TemporaryDirectory(prefix="cairn-foreign-") as scratch:
        where = Path(scratch)
        built = {cxx: build(project, cxx=cxx, output=where / cxx, device_target=device_target, timeout=300)
                 for cxx in compilers}  # fmt: skip
        everywhere = all(b["status"] == "native-built" for b in built.values())
        record["native_built"] = {"status": "native-built" if everywhere else "native-build-failed",
                                  "compilers": {c: b["status"] for c, b in built.items()}}  # fmt: skip
        if not everywhere:
            record["native_built"]["stderr"] = {c: b.get("stderr", "")[-4000:] for c, b in built.items()}
        record["device_inspected"] = {"status": "not-applicable", "reason": "a C++ source has no device code"}
        if kernels:
            target = resolve(device_target, project.device_target)
            assert target is not None
            write_program(where, "runtime.cpp", "")  # the runtime headers a vendored source may include
            inspected = foreign.inspect(project, target, where)
            mine = {path: inspected.get("sources", {}).get(path) for path, _ in sources}
            record["device_inspected"] = {**inspected, "sources": mine}
        if kernels or "par:device" in receipts[name]["effects"]:
            record["finite_tested"] = device_tests(project, reference, name, compilers[0], device_target, where)
            return record
        runs = {}
        for cxx in compilers:
            (where / f"vendored-{cxx}").mkdir()
            objects = tuple(foreign.host_objects(project, where / f"vendored-{cxx}", cxx))
            runs[cxx] = validate(project.source, reference, name, policy, cxx, None, project.libraries, objects=objects)
        record["finite_tested"] = runs
    return record


def device_tests(project: Project, reference: str, name: str, cxx: str, device_target: str | None,
                 where: Path) -> dict[str, Any]:  # fmt: skip
    """The device tests validation generates for `name`, built with the vendored sources and not run."""
    p, _, _ = compile_program(project.source)
    ref = next(f for f in p.functions if f.name == reference)
    cases = boundaries.generate(ref, {}, {"largest_extent": 256}, budget=12, device=True)
    generated = cases_as_tests(project.source, reference, name, cases)
    tested = replace(project, source=project.source + "\n" + generated.source)
    built = build(tested, cxx=cxx, output=where / "device-tests", device_target=device_target, timeout=300,
                  tests=tuple(f"test${n}" for n in generated.names))  # fmt: skip
    record = {"status": "not-run", "reason": ON_DEVICE, "cases": len(generated.names), "coverage": generated.coverage,
              "built": built["status"]}  # fmt: skip
    return record | ({"stderr": built.get("stderr", "")[-4000:]} if built["status"] != "native-built" else {})


def summary(record: dict[str, Any]) -> str:
    """What a person at a terminal reads: one line per claim, none standing in for another."""
    built, inspected, tested = record["native_built"], record["device_inspected"], record["finite_tested"]
    paths = ", ".join(s["path"] for s in record["sources"])
    effects = sorted({e for x in record["contract"]["externs"].values() for e in x["effects"]})
    lines = [f"{record['implementation']} implements {record['implements']} ({paths})",
             f"  contract        declared, not checked: {', '.join(effects)}",
             f"  native build    {built['status']}: {', '.join(built['compilers'])}"]  # fmt: skip
    kernels = {k: v for s in (inspected.get("sources") or {}).values() if s for k, v in s.get("kernels", {}).items()}
    for kernel, k in kernels.items():
        lines.append(f"  device          {kernel}: {k['registers']} registers, {k['shared_bytes']} B shared, "
                     f"{k['spill_bytes']} B spilled, {k['stack_bytes']} B stack")  # fmt: skip
    if not kernels:
        lines.append(f"  device          {inspected.get('status')}: {inspected.get('reason', '')}")
    if tested.get("status") == "not-run":
        lines.append(f"  finite-tested   not run: {tested['reason']}; its {tested['cases']} device tests are "
                     f"{tested['built']}")  # fmt: skip
    else:
        for cxx, run in tested.items():
            finite = run.get("finite", {})
            said = f"{finite.get('cases', 0)} cases" if finite else run.get("reason", "")
            lines.append(f"  finite-tested   {cxx}: {run['status']}, {said}")
    return "\n".join(lines) + "\n"


def passed(record: dict[str, Any]) -> bool:
    """Whether nothing in the record failed: a device implementation's tests not running is not a failure."""
    tested = record["finite_tested"]
    runs = [tested] if tested.get("status") == "not-run" else list(tested.values())
    return record["native_built"]["status"] == "native-built" and all(
        r["status"] in {"passed", "not-run"} for r in runs
    )

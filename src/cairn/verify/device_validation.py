"""The device side of validation: an implementation against its reference on the device, under Compute Sanitizer.

`cases_as_tests` writes each boundary case as a test block that fills host buffers, moves them to the device, calls
the reference and the implementation on the same inputs (each on its own copy of every rw view), brings the results
back and asserts that they agree. `sanitized` builds those tests into one executable and runs every test under each
Compute Sanitizer tool, `memcheck`, `racecheck`, `initcheck` and `synccheck`, as a separate result per tool.

Nothing here runs outside the owner's `make gpu` (perf/on_device.py `allowed`): without `CAIRN_GPU_TESTS=1` it says
why and runs nothing, and every run holds the machine-wide device lock. Generating the tests and compiling them for
the device are host work, which the default suite does.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..compiler.cairnc import Parser
from ..compiler.tree import FLOAT, is_view
from .boundaries import Case

TOOLS = ("memcheck", "racecheck", "initcheck", "synccheck")
LARGEST = 256  # elements of one view a generated test writes out, one assignment each


def literal(ty: str, value: Any) -> str:
    if ty == "bool":
        return "true" if value else "false"
    if ty in FLOAT:
        return f"{ty}({float(value)!r})" if value == value and abs(value) != float("inf") else f"{ty}(0.0)"
    return str(int(value))


def cases_as_tests(source: str, reference: str, implementation: str, cases: list[Case],
                   tolerance: dict[str, float] | None = None) -> tuple[str, list[str]]:  # fmt: skip
    """The test blocks, as source to append after the program's last declaration of the reference's module, and
    their names. A case whose views are longer than LARGEST is left out."""
    fs = {f.name: f for f in Parser(source).parse().functions}
    ref = fs[reference]
    tolerance = tolerance or {}
    out, names = [], []
    local, impl = reference.rsplit(".", 1)[-1], implementation.rsplit(".", 1)[-1]
    for k, case in enumerate(cases):
        if any(is_view(t) and len(case.args[n]) > LARGEST for n, t in ref.params):
            continue
        name = f"device_{impl}_{k}"
        lines, args_ref, args_impl, checks = [f"test {name} {{"], [], [], []
        for n, t in ref.params:
            if not is_view(t):
                args_ref.append(literal(t.name, case.args[n]))
                args_impl.append(args_ref[-1])
                continue
            values, count, ty = case.args[n], len(case.args[n]), t.name
            where = "@device" if t.place == "device" else ""
            lines.append(f"  buffer h_{n}:{ty}[{count}] = zeroed;")
            lines += [f"  h_{n}[{i}] = {literal(ty, v)};" for i, v in enumerate(values) if v]
            copies = ["r", "i"] if t.mode == "rw" else ["r"]
            for side in copies:
                lines.append(f"  buffer {side}_{n}:{ty}[{count}]{where} = zeroed;")
                if count:  # an empty loop is a pointless comparison, which nvcc refuses
                    lines.append(
                        f"  transfer({side}_{n}, h_{n});"
                        if where
                        else f"  for j in 0..{count} {{ {side}_{n}[j] = h_{n}[j]; }}"
                    )
            args_ref.append(f"r_{n}")
            args_impl.append(f"{copies[-1]}_{n}")
            if t.mode == "rw":
                for side in copies:
                    lines.append(f"  buffer b{side}_{n}:{ty}[{count}] = zeroed;")
                checks.append((n, ty, count, bool(where)))
        call_r, call_i = f"{local}({', '.join(args_ref)})", f"{impl}({', '.join(args_impl)})"
        if ref.ret.name == "void":
            lines += [f"  {call_r};", f"  {call_i};"]
        else:
            lines += [
                f"  let want = {call_r};",
                f"  let got = {call_i};",
                f"  {agree('want', 'got', ref.ret.name, tolerance)}",
            ]
        for n, ty, count, device in (check for check in checks if check[2]):
            for side in ("r", "i"):
                lines.append(
                    f"  transfer(b{side}_{n}, {side}_{n});"
                    if device
                    else f"  for j in 0..{count} {{ b{side}_{n}[j] = {side}_{n}[j]; }}"
                )
            lines.append(f"  for j in 0..{count} {{ {agree(f'br_{n}[j]', f'bi_{n}[j]', ty, tolerance)} }}")
        out.append("\n".join([*lines, "}"]))
        names.append(name)
    return "\n\n".join(out) + "\n", names


def agree(a: str, b: str, ty: str, tolerance: dict[str, float]) -> str:
    if ty in FLOAT and (tolerance.get("absolute") or tolerance.get("relative")):
        bound = f"{ty}({tolerance.get('absolute', 0.0)!r}) + {ty}({tolerance.get('relative', 0.0)!r}) * abs({a})"
        return f"assert(abs({a} - {b}) <= {bound});"
    return f"assert({a} == {b});"


def sanitized(program: str, tests: list[str], timeout: int = 300) -> dict[str, Any]:
    """Every generated test under each Compute Sanitizer tool, one result per tool; nothing runs unless the owner's
    `make gpu` allows device code here."""
    from ..perf.on_device import allowed, locked
    from ..projects.build import build
    from ..projects.project import load_project

    if reason := allowed():
        return {"status": "not-run", "reason": reason, "tools": dict.fromkeys(TOOLS, "not-run")}
    tool = shutil.which("compute-sanitizer")
    if tool is None:
        return {"status": "unknown", "reason": "compute-sanitizer is not installed", "tools": {}}
    with tempfile.TemporaryDirectory(prefix="cairn-device-validate-") as tmp:
        path = Path(tmp) / "program.cairn"
        path.write_text(program, encoding="utf-8")
        roots = tuple(f"test${name}" for name in tests)
        built = build(load_project(path), output=Path(tmp) / "build", cxx="g++", timeout=300, tests=roots)
        if built["status"] != "native-built":
            return {"status": built["status"], "reason": built.get("stderr", "")[-2000:], "tools": {}}
        results: dict[str, Any] = {}
        with locked():
            for name in TOOLS:
                runs = []
                for index, test in enumerate(tests):
                    done = subprocess.run([tool, "--tool", name, "--error-exitcode", "97", built["artifact"], str(index)],
                                          capture_output=True, text=True, timeout=timeout)  # fmt: skip
                    runs.append({"test": test, "exit_code": done.returncode, "report": done.stdout[-4000:]})
                clean = all(r["exit_code"] == 0 for r in runs)
                results[name] = {"status": "clean" if clean else "reported", "runs": runs}
    return {"status": "run", "tools": results}

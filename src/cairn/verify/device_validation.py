"""The device side of validation: an implementation against its reference on the device, under Compute Sanitizer.

`cases_as_tests` writes each boundary case as a test block that fills host buffers, moves them to the device, calls
the reference and the implementation on the same inputs (each on its own copy of every rw view), brings the results
back and asserts that they agree under the numerical policy of verify/agreement.py, which it writes into the tests as
a function. Every input is written exactly: a float by its bits where decimal digits cannot say it (a NaN, an
infinity, -0.0), a view of more than `SHORT` elements decoded from a table of its bit patterns, and a view one
element off an aligned allocation as a part one element into its storage. A case it cannot write is counted with its
reason in the coverage it returns, never dropped unseen. `sanitized` builds those tests into one executable and runs
every test under each Compute Sanitizer tool, `memcheck`, `racecheck`, `initcheck` and `synccheck`, as a separate
result per tool; a tool that ran no test is `unknown`, never clean.

Nothing here runs outside the owner's `make gpu` (perf/on_device.py `allowed`): without `CAIRN_GPU_TESTS=1` it says
why and runs nothing, and every run holds the machine-wide device lock. Generating the tests and compiling them for
the device are host work, which the default suite does.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

from ..compiler.cairnc import Parser
from ..compiler.lower.codegen import mangle
from ..compiler.syntax.tree import BITS, FLOAT, SIGNED, is_view
from . import agreement
from .boundaries import Case, limits

TOOLS = ("memcheck", "racecheck", "initcheck", "synccheck")
SHORT = 16  # elements of a view a generated test writes one assignment each; a longer one is decoded from a table
DIGITS = {"bool": 1, "f32": 8, "f64": 16, **{t: BITS[t] // 4 for t in BITS}}  # hexadecimal digits of one pattern


class Generated(NamedTuple):
    source: str  # the tests and the functions they call, to append after the reference's module
    names: list[str]  # the tests, in the order of the cases
    coverage: dict[str, Any]  # how many cases were given and written, and each reason one was left out


def literal(ty: str, value: Any) -> str:
    """`value` as a CAIRN expression of type `ty`, exactly: ValueError when it is no value of that type."""
    if ty == "bool":
        if value not in (True, False):
            raise ValueError(f"{value!r} is not a bool")
        return "true" if value else "false"
    if ty in FLOAT:
        return agreement.exact(ty, float(value))
    low, high = limits(ty)
    if isinstance(value, bool | float) or not low <= value <= high:
        raise ValueError(f"{value!r} is not a {ty}")
    return f"({value + 1} - 1)" if value == low and ty in SIGNED else str(value)  # the least literal is out of range


def pattern(ty: str, value: Any) -> int:
    """The bits of `value` as type `ty` holds them, which a table writes in hexadecimal."""
    literal(ty, value)  # the same values, and the same refusals
    if ty == "f64":
        return agreement.bits(float(value))
    if ty == "f32":
        return agreement.single(float(value))
    return int(value) & ((1 << BITS.get(ty, 1)) - 1)


def fresh(taken: set[str], stem: str) -> str:
    while stem in taken:
        stem += "_"
    taken.add(stem)
    return stem


def cases_as_tests(source: str, reference: str, implementation: str, cases: list[Case],
                   tolerance: dict[str, float] | None = None) -> Generated:  # fmt: skip
    """The test blocks, as source to append after the program's last declaration of the reference's module, their
    names, and the coverage: every case given is written, or counted under the reason it could not be."""
    fs = {f.name: f for f in Parser(source).parse().functions}
    taken = {name.rsplit(".", 1)[-1] for name in fs}
    ref = fs[reference]
    tolerance = tolerance or {}
    local, impl = reference.rsplit(".", 1)[-1], implementation.rsplit(".", 1)[-1]
    agree, digits = fresh(taken, f"agree_{mangle(impl)}"), fresh(taken, f"digits_{mangle(impl)}")
    out, names = [], []
    left_out: dict[str, int] = {}
    for k, case in enumerate(cases):
        name = f"device_{mangle(impl)}_{k}"  # `prefix_by[8]` names no test
        try:
            out.append(written(name, ref, local, impl, case, agree, digits))
        except (ValueError, TypeError, KeyError) as e:
            why = f"an input the generator cannot write: {e}"
            left_out[why] = left_out.get(why, 0) + 1
            continue
        names.append(name)
    text = "\n\n".join(out)
    helpers = [agreement.helper(agree, tolerance)] if f"{agree}(" in text else []
    helpers += [decoder(digits)] if f"{digits}(" in text else []
    coverage = {"generated": len(cases), "written": len(names), "left_out": left_out}
    return Generated("\n".join([*helpers, text]) + "\n", names, coverage)


def written(name: str, ref: Any, local: str, impl: str, case: Case, agree: str, digits: str) -> str:
    """One case as a test block."""
    lines, args_ref, args_impl, checks = [f"test {name} {{"], [], [], []
    for n, t in ref.params:
        if not is_view(t):
            args_ref.append(literal(t.name, case.args[n]))
            args_impl.append(args_ref[-1])
            continue
        values, ty, skip = case.args[n], t.name, int(case.offsets.get(n, 0))
        size = skip + len(values)  # a view one element off sits one element into its storage
        where = "@device" if t.place == "device" else ""
        lines.append(f"  buffer h_{n}:{ty}[{size}] = zeroed;")
        lines += filled(f"h_{n}", ty, values, skip, digits)
        copies = ["r", "i"] if t.mode == "rw" else ["r"]
        for side in copies:
            lines.append(f"  buffer {side}_{n}:{ty}[{size}]{where} = zeroed;")
            if size:  # an empty loop is a pointless comparison, which nvcc refuses
                lines.append(
                    f"  transfer({side}_{n}, h_{n});"
                    if where
                    else f"  for j in 0..{size} {{ {side}_{n}[j] = h_{n}[j]; }}"
                )
        part = f"[{skip}..{size}]" if skip else ""
        args_ref.append(f"r_{n}{part}")
        args_impl.append(f"{copies[-1]}_{n}{part}")
        if t.mode == "rw":
            for side in copies:
                lines.append(f"  buffer b{side}_{n}:{ty}[{size}] = zeroed;")
            checks.append((n, ty, skip, size, bool(where)))
    call_r, call_i = f"{local}({', '.join(args_ref)})", f"{impl}({', '.join(args_impl)})"
    if ref.ret.name == "void":
        lines += [f"  {call_r};", f"  {call_i};"]
    else:
        lines += [
            f"  let want = {call_r};",
            f"  let got = {call_i};",
            f"  {agreed('want', 'got', ref.ret.name, agree)}",
        ]
    for n, ty, skip, size, device in (check for check in checks if check[3] > check[2]):
        for side in ("r", "i"):
            lines.append(
                f"  transfer(b{side}_{n}, {side}_{n});"
                if device
                else f"  for j in 0..{size} {{ b{side}_{n}[j] = {side}_{n}[j]; }}"
            )
        lines.append(f"  for j in {skip}..{size} {{ {agreed(f'br_{n}[j]', f'bi_{n}[j]', ty, agree)} }}")
    return "\n".join([*lines, "}"])


def filled(buffer: str, ty: str, values: list[Any], skip: int, digits: str) -> list[str]:
    """Statements that write `values` into `buffer` from element `skip`: one assignment each for a short view, a loop
    over a table of bit patterns for a longer one."""
    if len(values) <= SHORT:
        return [f"  {buffer}[{skip + i}] = {literal(ty, v)};" for i, v in enumerate(values)]
    width = DIGITS[ty]
    table = "".join(f"{pattern(ty, v):0{width}x}" for v in values)
    at = f"{skip} + j" if skip else "j"
    bits = f"{digits}(t_{buffer}, j * {width}, {width})"
    if ty == "bool":
        body = [f"{buffer}[{at}] = {bits} != 0;"]
    elif ty in FLOAT:
        body = [f"{buffer}[{at}] = from_bits[{ty}]({bits if ty == 'f64' else f'u32({bits})'});"]
    elif ty in SIGNED:  # two's complement: a pattern at or past half the range is negative
        half, full = 1 << (BITS[ty] - 1), (1 << BITS[ty]) - 1
        body = [f"let p:u64 = {bits};",
                f"if p >= {half} {{ {buffer}[{at}] = {ty}(0 - i64({full} - p) - 1); }} else {{ {buffer}[{at}] = {ty}(p); }}"]  # fmt: skip
    else:
        body = [f"{buffer}[{at}] = {ty}({bits});"]
    return [f'  let t_{buffer} = "{table}";', f"  for j in 0..{len(values)} {{ {' '.join(body)} }}"]


def decoder(name: str) -> str:
    """The function a table is read with: `width` hexadecimal digits from `at`, as one pattern."""
    return (
        f"fn {name}(m:usize, table:ro<u8>[m], at:usize, width:usize) -> u64 {{\n"
        "  let mut bits:u64 = 0;\n"
        "  for d in 0..width {\n"
        "    let c:u8 = table[at + d];\n"
        "    let mut v:u64 = u64(c) - 48;\n"
        "    if c >= 97 { v = u64(c) - 87; }\n"
        "    bits = bits * 16 + v;\n"
        "  }\n"
        "  return bits;\n"
        "}\n"
    )


def agreed(a: str, b: str, ty: str, agree: str) -> str:
    """The assertion that the reference's `a` and the implementation's `b` agree: under the policy for a float,
    exactly for anything else."""
    if ty in agreement.COMPARED:
        return f"assert({agree}({agreement.widened(a, ty)}, {agreement.widened(b, ty)}));"
    return f"assert({a} == {b});"


def verdict(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """One tool's result over its runs: clean only when some test ran and every run exited cleanly."""
    if not runs:
        return {"status": "unknown", "reason": "no test ran under it", "runs": runs}
    return {"status": "clean" if all(r["exit_code"] == 0 for r in runs) else "reported", "runs": runs}


def sanitized(program: str, tests: list[str], timeout: int = 300) -> dict[str, Any]:
    """Every generated test under each Compute Sanitizer tool, one result per tool; nothing runs unless the owner's
    `make gpu` allows device code here, and no test to run is `unknown`."""
    from ..perf.on_device import allowed, locked
    from ..projects.build import build
    from ..projects.project import load_project

    if reason := allowed():
        return {"status": "not-run", "reason": reason, "tools": dict.fromkeys(TOOLS, "not-run")}
    if not tests:
        return {"status": "unknown", "reason": "no device test was generated, so nothing ran", "tools": {}}
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
                results[name] = verdict(runs)
    return {"status": "run", "tools": results}

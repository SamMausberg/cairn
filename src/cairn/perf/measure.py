"""Time a CAIRN function on the host: the one place in `perf` where anything runs.

The function is compiled with the build's own flags, beside a driver that fills each view deterministically, calls
the function until a block lasts long enough to read the clock, and reports the median block of several. It is what
calibration measures a machine with and what validation checks a prediction against. It never runs device code: a
program that includes the device runtime is refused before anything is built.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..compiler.cairnc import compile_program, compile_source, write_program
from ..compiler.tree import CPP, FLOAT, Type, is_view
from ..projects.toolchain import find, flags

DRIVER = """
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <vector>
{declaration}
template<class T> struct Held {{
  std::vector<T> held; T* p;
  explicit Held(std::size_t n) : held(n + 64 / sizeof(T) + 1, T{{}}) {{
    const auto at = reinterpret_cast<std::uintptr_t>(held.data());
    p = reinterpret_cast<T*>((at + 63) & ~std::uintptr_t(63));
  }}
}};
static volatile std::uint64_t sink;
int main() {{
{setup}
  auto once = [&]() noexcept {{ {call} }};
  once();
  using clock = std::chrono::steady_clock;
  std::size_t inner = 1;
  for(;;) {{
    const auto a = clock::now();
    for(std::size_t r = 0; r < inner; ++r) once();
    const double ns = std::chrono::duration<double, std::nano>(clock::now() - a).count();
    if(ns >= {block_ns} || inner >= (std::size_t(1) << 30)) break;
    inner *= 2;
  }}
  std::vector<double> blocks;
  for(int b = 0; b < {blocks}; ++b) {{
    const auto a = clock::now();
    for(std::size_t r = 0; r < inner; ++r) once();
    blocks.push_back(std::chrono::duration<double, std::nano>(clock::now() - a).count() / double(inner));
  }}
  std::sort(blocks.begin(), blocks.end());
  std::printf("{{\\"median_ns\\": %.6f, \\"min_ns\\": %.6f, \\"max_ns\\": %.6f, \\"inner\\": %zu}}\\n",
              blocks[blocks.size() / 2], blocks.front(), blocks.back(), inner);
  return 0;
}}
"""


def fill(ty: Type, rule: str | None) -> str:
    """C++ that writes element `k` of a view: small values that keep checked arithmetic away from its guards."""
    if rule and rule.startswith("index:"):
        return f"static_cast<{CPP[ty.name]}>((k * 2654435761ull) % {rule.split(':', 1)[1]})"
    if ty.name in FLOAT:
        return f"static_cast<{CPP[ty.name]}>(0.25 * double(k % 13) + 1.0)"
    if ty.name == "bool":
        return "(k % 3) == 0"
    return f"static_cast<{CPP[ty.name]}>(k % 97 + 1)"


def driver(f: Any, sizes: Mapping[str, float], fills: dict[str, str], block_ns: float, blocks: int,
           memory: Mapping[str, str] | None = None) -> str:  # fmt: skip
    """The timing program's `main`. Each view is filled on the host; `memory` names the allocator of each placement
    the device timer copies a view to once, before timing."""
    setup, args, params = [], [], []
    for name, ty in f.params:
        if is_view(ty):
            extent = int(ty.extent) if ty.extent.isdigit() else int(sizes[ty.extent])
            element = CPP[ty.name]
            setup.append(f"  Held<{element}> h_{name}({extent});")
            setup.append(f"  for(std::size_t k = 0; k < {extent}; ++k) h_{name}.p[k] = {fill(ty, fills.get(name))};")
            args.append(f"h_{name}.p")
            if memory and ty.place in memory:
                size = f"{extent} * sizeof({element})"
                setup += [f"  {element}* b_{name} = nullptr;",
                          f"  if({memory[ty.place]}(reinterpret_cast<void**>(&b_{name}), {size}) != cudaSuccess) return 3;",
                          f"  if(cudaMemcpy(b_{name}, h_{name}.p, {size}, cudaMemcpyDefault) != cudaSuccess) return 3;"]  # fmt: skip
                args[-1] = f"b_{name}"
            params.append(f"{'const ' if ty.mode == 'ro' else ''}{element}*")
        elif ty.name in CPP and ty.name != "void":
            value = int(sizes.get(name, 3)) if ty.name not in FLOAT else sizes.get(name, 3)
            args.append(f"static_cast<{CPP[ty.name]}>({value})")
            params.append(CPP[ty.name])
        else:
            timer = "device" if memory is not None else "host"
            raise ValueError(f"{f.name} takes {ty.display()}, which the {timer} timer cannot supply.")
    ret = CPP.get(f.ret.name, "void")
    call = f"cf_{f.name}({', '.join(args)});"
    if ret != "void":
        call = f"sink = sink + static_cast<std::uint64_t>({call[:-1]});"
    declaration = f'extern "C" {ret} cf_{f.name}({", ".join(params)}) noexcept;'
    return DRIVER.format(declaration=declaration, setup="\n".join(setup), call=call, block_ns=block_ns, blocks=blocks)


def build(source: str, cxx: str, arch: str | None, extra: tuple[str, ...], directory: Path) -> Path:
    """The program's C++ and the runtime headers in `directory`, compiled to one object with the build's flags."""
    cpp, receipt = compile_source(source)
    if "cuda" in receipt["requires"]:
        raise ValueError("A device program is never timed here: device runs belong to the owner's make target.")
    program, obj = write_program(directory, "program.cpp", cpp), directory / "program.o"
    subprocess.run([find(cxx), *flags(arch, "exe"), *extra, "-c", str(program), "-o", str(obj)], check=True,
                   capture_output=True, text=True, timeout=300)  # fmt: skip
    return obj


def time(source: str, symbol: str, sizes: Mapping[str, float], *, fills: dict[str, str] | None = None,
         cxx: str = "clang++", arch: str | None = None, extra: tuple[str, ...] = (), block_ns: float = 2e6,
         blocks: int = 9, lanes: int | None = None, timeout: int = 600) -> dict[str, Any]:  # fmt: skip
    """The median time of one call of `symbol` at `sizes`, in nanoseconds, measured on this host."""
    p, _, _ = compile_program(source)
    f = next((f for f in p.functions if f.name == symbol), None)
    if f is None:
        raise ValueError(f"No function {symbol} to time.")
    with tempfile.TemporaryDirectory(prefix="cairn-time-") as scratch:
        directory = Path(scratch)
        obj = build(source, cxx, arch, extra, directory)
        (directory / "driver.cpp").write_text(driver(f, sizes, fills or {}, block_ns, blocks), encoding="utf-8")
        exe = directory / "timed"
        subprocess.run([find(cxx), *flags(arch, "exe"), *extra, str(directory / "driver.cpp"), str(obj), "-o", str(exe), "-pthread"],
                       check=True, capture_output=True, text=True, timeout=300)  # fmt: skip
        env = dict(os.environ, **({"CAIRN_LANES": str(lanes)} if lanes else {}))
        done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=timeout, env=env)
        if done.returncode:
            return {"status": "trapped" if done.returncode < 0 else "failed", "exit": done.returncode,
                    "stderr": done.stderr[:2000]}  # fmt: skip
        return {"status": "measured", **json.loads(done.stdout)}

#!/usr/bin/env python3
"""Time calls between CAIRN functions with and without the callee's entry checks.

Each case is a small CAIRN program whose hot loop calls a function that takes views. It is emitted twice from one
checked tree: as the compiler emits it, where a call from CAIRN code reaches the callee's lean body `ci_`, and with
every such call sent through the checked entry `cf_` instead, which is what the emitter wrote before checked entries
existed. Every other guard is the same in both. Both are built with the build's flags under g++ and clang++, and a
driver takes the best of `--repeat` runs of each, alternating the two, and requires the two to return the same value.

    python3 bench/cpu/entry_checks.py [--repeat 15] [--out results/timing/entry_checks.json]

It measures one machine at one moment. The machine it ran on is recorded, and nothing is claimed beyond it.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.compiler.cairnc import RUNTIME_FILES, Emitter, compile_program
from cairn.projects.toolchain import flags

CASES = {
    "windows": (
        "fn total(c:usize, v:ro<u64>[c]) -> u64 { let mut t:u64 = 0; for i in 0..c { t = add_wrap(t, v[i]); } "
        "return t; }\n"
        "fn run(n:usize, x:ro<u64>[n]) -> u64 {\n  if n < 4 { return 0; }\n  let mut t:u64 = 0;\n"
        "  for i in 0..n - 4 { t = add_wrap(t, total(x[i..i + 4])); }\n  return t;\n}\n"
    ),
    "find": (
        "import std.core (Option);\nimport std.text;\n"
        'fn run(n:usize, x:ro<u8>[n]) -> u64 {\n  match text.find(x, "needle!") {\n'
        "    Option.Some(at) => { return u64(at); }\n    Option.None => { return 0; }\n  }\n}\n"
    ),
    "pairs": (
        "fn add_into(n:usize, out:rw<u64>[n], a:ro<u64>[n]) { for i in 0..n { out[i] = add_wrap(out[i], a[i]); } }\n"
        "fn run(n:usize, x:ro<u64>[n]) -> u64 {\n  stack acc:u64[8] = zeroed;\n  let rows = n / 8;\n"
        "  for r in 0..rows { add_into(acc, x[r * 8..r * 8 + 8]); }\n  let mut t:u64 = 0;\n"
        "  for i in 0..8 { t = add_wrap(t, acc[i]); }\n  return t;\n}\n"
    ),
}
ELEMENT = {"windows": "std::uint64_t", "find": "std::uint8_t", "pairs": "std::uint64_t"}
DRIVER = """
#include <chrono>
#include <cstdlib>
#include <cstdio>
#include <vector>
int main(int argc, char** argv) {
  const std::size_t n = std::size_t(1) << 24;
  std::vector<ELEMENT> x(n);
  for (std::size_t j = 0; j < n; ++j) x[j] = static_cast<ELEMENT>(j * 7 + 1);
  std::uint64_t best = ~0ull, value = 0;
  const int repeat = argc > 1 ? std::atoi(argv[1]) : 15;
  for (int r = 0; r < repeat; ++r) {
    auto t0 = std::chrono::steady_clock::now();
    value = cf_run(n, x.data());
    auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - t0).count();
    if (static_cast<std::uint64_t>(ns) < best) best = ns;
  }
  std::printf("%llu %llu\\n", static_cast<unsigned long long>(best), static_cast<unsigned long long>(value));
}
"""


def emitted(source: str, lean: bool) -> str:
    p, checker, _ = compile_program(source)
    emitter = Emitter(p, checker)
    emitter.lean = lean  # False sends every call from CAIRN through the callee's checked entry.
    return emitter.emit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeat", type=int, default=15)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", type=Path, default=ROOT / "results/timing/entry_checks.json")
    a = ap.parse_args()
    compilers = [c for c in ("g++", "clang++") if shutil.which(c)]
    rows = []
    with tempfile.TemporaryDirectory(prefix="cairn_entries_") as scratch:
        work = Path(scratch)
        for name, text in RUNTIME_FILES.items():
            (work / name).write_text(text)
        native = flags(None, "exe")
        for case, source in CASES.items():
            for cxx in compilers:
                built = {}
                for lean in (True, False):
                    cpp = emitted(source, lean) + DRIVER.replace("ELEMENT", ELEMENT[case])
                    unit, exe = work / f"{case}_{lean}.cpp", work / f"{case}_{cxx}_{lean}"
                    unit.write_text(cpp)
                    subprocess.run([cxx, *native, str(unit), "-o", str(exe)], check=True, timeout=300)
                    built[lean] = exe
                best: dict[bool, int] = {}
                values: dict[bool, str] = {}
                for _ in range(a.rounds):  # alternate the two builds, keep each one's best
                    for lean in (True, False):
                        ns, value = subprocess.run([str(built[lean]), str(a.repeat)], capture_output=True, text=True,
                                                   check=True, timeout=600).stdout.split()  # fmt: skip
                        best[lean] = min(best.get(lean, 1 << 62), int(ns))
                        values[lean] = value
                assert values[True] == values[False], (case, cxx, values)
                rows.append({"case": case, "compiler": cxx, "lean_ns": best[True], "checked_ns": best[False],
                             "checked_over_lean": round(best[False] / best[True], 3)})  # fmt: skip
                print(json.dumps(rows[-1]))
    result = {
        "measures": "best wall time of one call of cf_run over 2^24 elements; lean: calls from CAIRN reach ci_, "
        "checked: they reach cf_, which runs the callee's view and disjointness checks first",
        "repeat": a.repeat,
        "rounds": a.rounds,
        "flags": native,
        "machine": {"platform": platform.platform(), "processor": platform.processor()},
        "rows": rows,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Differential check: each generated program behaves the same with the guards the checker discharged and without.

The generator writes functions of one shape, `gN(n, m, k, x:ro<u64>[n], y:ro<u64>[m]) -> u64`, out of statements
that stress what guard elision reasons about: early returns, branches, `&&` and `||` conditions, immutable `let`s,
owners replaced by assignment, narrowing conversions, parts with their extents left out or written as `hi - lo`, and
arithmetic next to the largest usize. Each batch is emitted twice, once as the compiler emits it and once with
`keep_guards`, which writes every guard, and both are built with clang++ under AddressSanitizer and
UndefinedBehaviorSanitizer (and, with `--gcc`, with g++). A driver runs every function on every input of a grid that
includes empty views and `k` next to the largest usize, each in a child process, and prints what it returned,
`trap` for an abort, or the status of anything else, such as a sanitizer report. The two builds must print the same
line for every case: a dropped guard that could fail shows up as a missing trap, a different value or a report.

A mismatch is minimized, by deleting statements while the mismatch persists, and printed as a CAIRN program to keep
as a regression test. `--output` records the run; `evidence/v1_4/guards/` holds the one this checkout recorded.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.compiler.cairnc import RUNTIME_FILES, Diagnostic, compile_source

MAX = 2**64 - 1
SIZES = (0, 1, 2, 5)
KS = (0, 1, 3, 7, MAX - 1, MAX)
SANITIZE = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
HELPER = (
    "fn total(c:usize, v:ro<u64>[c]) -> u64 { let mut t:u64 = 0; for i in 0..c { t = add_wrap(t, v[i]); } return t; }\n"
)
BOUNDS = ["n", "m", "n - 1", "min(k, 6) + 1", "n - k", "len(x)", "min(n, m)", "m / 2", "k % 4", "0", "1", "2"]
INDEXES = ["i", "i + 1", "i - 1", "k", "n - 1 - i", "n - i", "i / 2", "k + i", "a", "a - 1", "a + 1"]
CONDITIONS = ["i < m", "i + 1 < n", "k < n", "i > 0 && i < m", "!(i >= m)", "i < n || k < m", "i != 0", "k + 1 < n",
              "a < n", "a > 0 && a - 1 < n", "k < n && k + 1 < n", "n > 0"]  # fmt: skip
LETS = ["n - k", "k + 1", "min(n, k)", "m / 2", "n - m", "k % n", "n", "m - 1", "k * 2"]
NARROW = ["u32(k)", "u8(n)", "u16(k % 65536)", "u32(a)", "u8(i & 255)", "u8(i)", "u64(u32(n))"]


def statement(rng: random.Random, depth: int = 0) -> str:
    """One statement of a generated body; `t` is the running sum and `a` an immutable usize bound first."""
    view = rng.choice("xy")
    choice = rng.random()
    if choice < 0.3:
        lo, hi = rng.choice(["0", "1", "k", "a"]), rng.choice(BOUNDS)  # `a` may be near the largest usize
        read = f"t = add_wrap(t, {view}[{rng.choice(INDEXES)}]);"
        cond = rng.choice(CONDITIONS)
        step = rng.choice([read, f"if {cond} {{ {read} }}", f"if !({cond}) {{ return 7; }} {read}",
                           f"if {cond} {{ {read} }} else {{ t = add_wrap(t, 2); }}"])  # fmt: skip
        return f"for i in {lo}..{hi} {{ {step} }}"
    if choice < 0.5:
        lo, hi = rng.choice([("0", "a"), ("a", "n"), ("n - a", "n"), ("k", "k + 2"), ("a", "a + 1"), ("0", "m")])
        base = "x" if hi in {"n", "a"} or view == "x" else "y"
        call = rng.choice([f"total({base}[{lo}..{hi}])", f"total({hi} - {lo}, {base}[{lo}..{hi}])"])
        guard = rng.choice(["", f"if {hi} > len({base}) {{ return 5; }} ", f"if {lo} > {hi} {{ return 6; }} "])
        return f"{guard}t = add_wrap(t, {call});"
    if choice < 0.65:
        return f"t = add_wrap(t, u64({rng.choice(NARROW)}));"
    if choice < 0.75:
        return f"if {rng.choice(CONDITIONS).replace('i', 'a')} {{ t = add_wrap(t, {view}[a]); }}"
    if choice < 0.85:
        return f"if a < {rng.choice(['n', 'm'])} && {view}[a] > 3 {{ t = add_wrap(t, 1); }}"
    if choice < 0.93:
        return (f"let mut d = Buf[u64]({rng.choice(['n', 'm', '1'])}); d = Buf[u64]({rng.choice(['n', 'm', '0'])}); "
                f"t = add_wrap(t, d[{rng.choice(['a', 'k', '0'])}]);")  # fmt: skip
    if depth < 1:
        return f"if {rng.choice(CONDITIONS).replace('i', 'k')} {{ {statement(rng, depth + 1)} }} else {{ return 9; }}"
    return "t = add_wrap(t, 1);"


def function(rng: random.Random, number: int, body: list[str] | None = None) -> tuple[str, list[str]]:
    lead = f"let a = {rng.choice(LETS)};"
    body = body if body is not None else [lead, *(statement(rng) for _ in range(rng.randint(1, 3)))]
    inner = "\n  ".join(body)
    source = (f"fn g{number}(n:usize, m:usize, k:usize, x:ro<u64>[n], y:ro<u64>[m]) -> u64 {{\n"
              f"  let mut t:u64 = 0;\n  {inner}\n  return t;\n}}\n")  # fmt: skip
    return source, body


DRIVER = """
#include <cstdio>
#include <sys/wait.h>
#include <unistd.h>
int main() {
  const std::size_t sizes[] = {SIZES};
  const std::size_t ks[] = {KS};
  for(auto f : FUNCTIONS) for(std::size_t n : sizes) for(std::size_t m : sizes) for(std::size_t k : ks) {
    std::fflush(stdout);
    const pid_t child = fork();
    if(child == 0) {
      alarm(20);  // a loop that runs this long is reported as a status, not waited for
      auto* x = new std::uint64_t[n ? n : 1]; auto* y = new std::uint64_t[m ? m : 1];
      for(std::size_t j = 0; j < n; ++j) x[j] = 3 * j + 1;
      for(std::size_t j = 0; j < m; ++j) y[j] = 5 * j + 2;
      std::printf("%llu\\n", static_cast<unsigned long long>(f(n, m, k, x, y)));
      std::fflush(stdout);
      _exit(0);
    }
    int status = 0;
    waitpid(child, &status, 0);
    if(WIFSIGNALED(status) && WTERMSIG(status) == 6) std::printf("trap\\n");
    else if(!WIFEXITED(status) || WEXITSTATUS(status) != 0) std::printf("status %d\\n", status);
  }
}
"""


def driver(names: list[str]) -> str:
    functions = "{" + ", ".join(f"&cf_{n}" for n in names) + "}"
    ks = ", ".join(f"{k}ULL" for k in KS)
    return DRIVER.replace("SIZES", ", ".join(map(str, SIZES))).replace("KS", ks).replace("FUNCTIONS", functions)


def run(sources: list[str], names: list[str], keep: bool, cxx: str, work: Path) -> list[str] | None:
    """What each function prints on each input, built as emitted (or with every guard), or None if refused."""
    try:
        cpp = compile_source(HELPER + "".join(sources), keep_guards=keep)[0]
    except Diagnostic:
        return None
    work.mkdir(parents=True, exist_ok=True)
    for name, text in RUNTIME_FILES.items():
        (work / name).write_text(text)
    (work / "p.cpp").write_text(cpp + driver(names))
    flags = SANITIZE if cxx == "clang++" else SANITIZE[:4]
    subprocess.run([cxx, *flags, str(work / "p.cpp"), "-o", str(work / "p")], check=True, timeout=600)
    done = subprocess.run([str(work / "p")], capture_output=True, text=True, timeout=1200)
    return done.stdout.split("\n")[:-1]


def compare(sources, names, cxx, work) -> list[tuple[int, int, str, str]] | None:
    """(function, case, as emitted, with every guard) for every case where the two builds differ."""
    fast, safe = run(sources, names, False, cxx, work / "fast"), run(sources, names, True, cxx, work / "safe")
    if fast is None or safe is None:
        return None
    cases = len(SIZES) ** 2 * len(KS)
    assert len(fast) == len(safe) == cases * len(names), (len(fast), len(safe))
    return [(i // cases, i % cases, a, b) for i, (a, b) in enumerate(zip(fast, safe, strict=True)) if a != b]


def minimize(rng_body: list[str], number: int, cxx: str, work: Path) -> str:
    """Delete statements while the two builds still differ; the smallest program that does."""
    body = list(rng_body)
    changed = True
    while changed:
        changed = False
        for i in range(len(body)):
            trial = body[:i] + body[i + 1 :]
            source = function(random.Random(0), number, trial)[0]
            if compare([source], [f"g{number}"], cxx, work):
                body, changed = trial, True
                break
    return function(random.Random(0), number, body)[0]


def generated(seed: int, index: int, size: int) -> list[tuple[str, list[str]]]:
    """The functions of one batch the checker accepts, with their bodies; a refused one is dropped."""
    rng = random.Random(seed * 100003 + index)
    kept = []
    for source, body in (function(rng, index * size + j) for j in range(size)):
        try:
            compile_source(HELPER + source)
            kept.append((source, body))
        except Diagnostic:
            pass
    return kept


def batch(args: tuple[int, int, int, str, str]) -> dict:
    seed, index, size, cxx, root = args
    work = Path(root) / f"batch{index}"
    kept = generated(seed, index, size)
    names = [s.split("(")[0][3:] for s, _ in kept]
    found = compare([s for s, _ in kept], names, cxx, work) or []
    counterexamples = [
        minimize(kept[fn][1], int(names[fn][1:]), cxx, work / "min") for fn in sorted({f for f, *_ in found})
    ]
    receipt = compile_source(HELPER + "".join(s for s, _ in kept))[1]
    discharged = sum(sum(r["discharged_check_sites"].values()) for r in receipt["functions"].values())
    shutil.rmtree(work, ignore_errors=True)
    return {"programs": len(kept), "refused_by_checker": size - len(kept), "discharged": discharged,
            "cases": len(kept) * len(SIZES) ** 2 * len(KS), "mismatches": len(found),
            "counterexamples": counterexamples}  # fmt: skip


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--programs", type=int, default=2000)
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--seed", type=int, default=20260922)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--gcc", action="store_true", help="also build every batch with g++, without sanitizers")
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    compilers = ["clang++", *(["g++"] if a.gcc else [])]
    if not all(shutil.which(c) for c in compilers):
        print(json.dumps({"status": "unknown", "reason": f"needs {' and '.join(compilers)}"}))
        return 2
    report: dict = {"seed": a.seed, "sizes": list(SIZES), "k": [str(k) for k in KS], "compilers": {}}
    with tempfile.TemporaryDirectory(prefix="cairn_guards_") as root:
        for cxx in compilers:
            jobs = [
                (a.seed, i, a.batch, cxx, str(Path(root) / cxx)) for i in range((a.programs + a.batch - 1) // a.batch)
            ]
            with ThreadPoolExecutor(a.workers) as pool:
                rows = list(pool.map(batch, jobs))
            report["compilers"][cxx] = {k: sum(r[k] for r in rows) for k in rows[0] if k != "counterexamples"}
            report["compilers"][cxx]["counterexamples"] = [c for r in rows for c in r["counterexamples"]]
    agreed = all(r["mismatches"] == 0 for r in report["compilers"].values())
    report["status"] = "agreed" if agreed else "differed"
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0 if agreed else 1


if __name__ == "__main__":
    raise SystemExit(main())

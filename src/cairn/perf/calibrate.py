"""Measure this host into a profile: bandwidth by level and thread count, what each kind of operation costs with and
without vectors, and what the lane pool, a task and an allocation cost.

Every number comes from a CAIRN kernel compiled with the build's own flags and timed by `measure`. Operation costs
are fitted, not asserted: each kernel's operations are counted by `work`, the same counter predictions use, and a
non-negative least-squares fit finds the cost per operation that explains the times. A kind whose kernels run no
faster with the vectorizer on is recorded as one that keeps a loop scalar. Nothing here touches a device.

    python -m cairn.perf.calibrate --out evidence/v1_0/perf_model/zen4-7800x3d.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..compiler.cairnc import compile_program
from ..projects.toolchain import find, resolve_arch
from ..projects.toolchain import version as compiler_version
from . import measure
from .work import count

BANDWIDTH = """
fn rd_seq(n:usize, x:ro<u64>[n]) -> u64 { let t = reduce add_wrap for i in n yield x[i]; return t; }
fn rd_par(n:usize, x:ro<u64>[n]) -> u64 { let t = reduce add_wrap parallel i in n yield x[i]; return t; }
fn wr_seq(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = u64(i); } }
fn wr_par(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = u64(i); } }
fn gather(n:usize, out:rw<u64>[n], m:usize, x:ro<u64>[m], k:ro<u64>[n]) { for i in 0..n { out[i] = x[usize(k[i])]; } }
fn chain(n:usize, seed:u64) -> u64 {
  let mut w = seed;
  for i in 0..n { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn nothing(n:usize, out:rw<u64>[n]) { out[0] = 1; }
fn spawn_one(n:usize, out:rw<u64>[n]) { let t = spawn nothing(n, out); wait(t); }
fn alloc(n:usize) -> u64 { let b = Buf[u64](n); let t = reduce add_wrap for i in n yield b[i]; return t; }
fn at_par(n:usize) -> u64 {
  let total = Atomic[u64](0);
  parallel i in n { let before = total.fetch_add(1, Order.relaxed); }
  return total.load(Order.relaxed);
}
"""

# One kernel per kind of operation, each over views small enough to stay in the first-level cache.
OPERATIONS = """
fn cp(n:usize, out:rw<u64>[n], x:ro<u64>[n]) { for i in 0..n { out[i] = x[i]; } }
fn fill(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = u64(i); } }
fn sum(n:usize, x:ro<u64>[n]) -> u64 { let t = reduce add_wrap for i in n yield x[i]; return t; }
fn xor(n:usize, out:rw<u64>[n], x:ro<u64>[n], y:ro<u64>[n]) { for i in 0..n { out[i] = x[i] ^ y[i]; } }
fn mulw(n:usize, out:rw<u64>[n], x:ro<u64>[n], y:ro<u64>[n]) { for i in 0..n { out[i] = mul_wrap(x[i], y[i]); } }
fn addc(n:usize, out:rw<u64>[n], x:ro<u64>[n], y:ro<u64>[n]) { for i in 0..n { out[i] = x[i] + y[i]; } }
fn mulc(n:usize, out:rw<u64>[n], x:ro<u64>[n], y:ro<u64>[n]) { for i in 0..n { out[i] = x[i] * y[i]; } }
fn div(n:usize, out:rw<u64>[n], x:ro<u64>[n], y:ro<u64>[n]) { for i in 0..n { out[i] = x[i] / y[i]; } }
fn pick(n:usize, out:rw<u64>[n], x:ro<u64>[n], y:ro<u64>[n]) {
  for i in 0..n { if x[i] < y[i] { out[i] = x[i]; } else { out[i] = y[i]; } }
}
fn narrow(n:usize, out:rw<u64>[n], x:ro<u64>[n]) { for i in 0..n { out[i] = u64(u32(x[i])); } }
fn fma64(n:usize, out:rw<f64>[n], x:ro<f64>[n], y:ro<f64>[n]) { for i in 0..n { out[i] = x[i] * y[i] + x[i]; } }
fn div64(n:usize, out:rw<f64>[n], x:ro<f64>[n], y:ro<f64>[n]) { for i in 0..n { out[i] = x[i] / y[i]; } }
fn fma32(n:usize, out:rw<f32>[n], x:ro<f32>[n], y:ro<f32>[n]) { for i in 0..n { out[i] = x[i] * y[i] + x[i]; } }
fn shifted(n:usize, out:rw<u64>[n], m:usize, x:ro<u64>[m], s:usize) { for i in 0..n { out[i] = x[i + s]; } }
fn negate(n:usize, out:rw<i64>[n], x:ro<i64>[n]) { for i in 0..n { out[i] = 0 - x[i]; } }
fn sub64(n:usize, out:rw<u64>[n], x:ro<u64>[n], y:ro<u64>[n]) { for i in 0..n { out[i] = add_wrap(x[i], y[i]) - y[i]; } }
fn add32(n:usize, out:rw<f32>[n], x:ro<f32>[n], y:ro<f32>[n]) { for i in 0..n { out[i] = x[i] + y[i]; } }
fn div32(n:usize, out:rw<f32>[n], x:ro<f32>[n], y:ro<f32>[n]) { for i in 0..n { out[i] = x[i] / y[i]; } }
fn shifts(n:usize, out:rw<u64>[n], x:ro<u64>[n], y:ro<u64>[n]) {
  for i in 0..n { out[i] = shr(x[i], usize(y[i] & 31)); }
}
fn fsum64(n:usize, x:ro<f64>[n]) -> f64 { let t = reduce + for i in n yield x[i]; return t; }
fn fsum32(n:usize, x:ro<f32>[n]) -> f32 { let t = reduce + for i in n yield x[i]; return t; }
fn csum(n:usize, x:ro<u64>[n]) -> u64 { let t = reduce + for i in n yield x[i]; return t; }
fn keep(n:usize, out:rw<u64>[n], x:ro<u64>[n]) -> usize {
  let used = compact out for i in n where (x[i] & 1) == 0 yield x[i];
  return used;
}
fn at_seq(n:usize) -> u64 {
  let total = Atomic[u64](0);
  for i in 0..n { let before = total.fetch_add(1, Order.relaxed); }
  return total.load(Order.relaxed);
}
"""
OP_SIZE = 1024  # three views of 8 KiB: the first-level cache holds them
LEVEL_ELEMENTS = {"l1": 2048, "l2": 65536, "l3": 1 << 22, "dram": 1 << 26}  # u64 elements: 16 KiB .. 512 MiB
SCALAR_FLAGS = {"clang++": ("-fno-vectorize", "-fno-slp-vectorize"), "g++": ("-fno-tree-vectorize",)}


def nnls(rows: list[list[float]], targets: list[float], sweeps: int = 20000) -> list[float]:
    """Non-negative least squares by cyclic coordinate descent, each row weighted by its own target."""
    width = len(rows[0])
    a = [[v / t for v in row] for row, t in zip(rows, targets, strict=True)]
    b = [1.0] * len(targets)
    x = [0.0] * width
    residual = [bi - sum(ai[j] * x[j] for j in range(width)) for ai, bi in zip(a, b, strict=True)]
    norms = [sum(ai[j] ** 2 for ai in a) for j in range(width)]
    for _ in range(sweeps):
        moved = 0.0
        for j in range(width):
            if not norms[j]:
                continue
            step = sum(ai[j] * r for ai, r in zip(a, residual, strict=True)) / norms[j]
            new = max(0.0, x[j] + step)
            delta = new - x[j]
            if delta:
                x[j] = new
                residual = [r - ai[j] * delta for ai, r in zip(a, residual, strict=True)]
                moved = max(moved, abs(delta))
        if moved < 1e-12:
            break
    return x


def lanes() -> int:
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1


def cores() -> int:
    """Physical cores: siblings of one core share its first two cache levels."""
    try:
        seen = set()
        for cpu in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/topology/core_cpus_list"):
            seen.add(cpu.read_text().strip())
        return len(seen) or lanes()
    except OSError:
        return lanes()


def caches() -> dict[str, int]:
    found = {"l1": 32768, "l2": 1 << 20, "l3": 32 << 20}
    base = Path("/sys/devices/system/cpu/cpu0/cache")
    for index in base.glob("index*"):
        try:
            level, kind = (index / "level").read_text().strip(), (index / "type").read_text().strip()
            size = (index / "size").read_text().strip()
        except OSError:
            continue
        if kind == "Instruction":
            continue
        scale = {"K": 1024, "M": 1 << 20}.get(size[-1], 1)
        found[f"l{level}"] = int(size.rstrip("KM")) * scale
    return found


def best(result: dict[str, Any]) -> float:
    if result.get("status") != "measured":
        raise RuntimeError(f"A calibration kernel did not run: {result}")
    return float(result["min_ns"])  # the least-disturbed block: a shared machine only ever adds time


def timed(name: str, sizes: Mapping[str, float], cxx: str, arch: str, **options: Any) -> float:
    """The least-disturbed time of one call of the bandwidth kernel `name`."""
    return best(measure.time(BANDWIDTH, name, sizes, cxx=cxx, arch=arch, **options))


def operations(cxx: str, arch: str) -> tuple[dict[str, dict[str, float]], list[str], dict[str, Any]]:
    p, c, _ = compile_program(OPERATIONS)
    costs = count(p, c)
    names = [f.name for f in p.functions]
    kinds = sorted({k for name in names for k in costs[name].seq.ops})
    table: dict[str, dict[str, float]] = {}
    times: dict[str, dict[str, float]] = {}
    sizes: dict[str, float] = {"n": OP_SIZE, "m": OP_SIZE + 8, "s": 3}
    for mode, extra in (("vector", ()), ("scalar", SCALAR_FLAGS.get(Path(cxx).name.split("-")[0], ()))):
        times[mode] = {name: best(measure.time(OPERATIONS, name, sizes, cxx=cxx, arch=arch, extra=extra)) / OP_SIZE
                       for name in names}  # fmt: skip
        rows = [[(costs[name].seq.ops.get(k).value(sizes) or 0.0) / OP_SIZE if k in costs[name].seq.ops else 0.0
                 for k in kinds] for name in names]  # fmt: skip
        fitted = nnls(rows, [times[mode][name] for name in names])
        table[mode] = {k: round(v, 5) for k, v in zip(kinds, fitted, strict=True)}
    scalarizing = sorted(k for k in kinds if k not in {"load", "store", "convert", "int"}
                         and all(times["vector"][n] >= 0.6 * times["scalar"][n] for n in names
                                 if k in costs[n].seq.ops))  # fmt: skip
    return table, scalarizing, {"per_element_ns": times, "kinds": kinds}


def bandwidth(cxx: str, arch: str, threads: int, physical: int) -> tuple[dict, dict, dict[str, float]]:
    read: dict[str, dict[str, float]] = {}
    write: dict[str, dict[str, float]] = {}
    irregular: dict[str, float] = {}
    for level, n in LEVEL_ELEMENTS.items():
        size = {"n": n}
        one_read = n * 8 / timed("rd_seq", size, cxx, arch)
        one_write = n * 8 / timed("wr_seq", size, cxx, arch)
        if level in {"l1", "l2"}:  # Private levels: every core has its own, and a sibling thread shares it.
            all_read, all_write = one_read * physical, one_write * physical
        else:
            all_read = n * 8 / timed("rd_par", size, cxx, arch, lanes=threads)
            all_write = n * 8 / timed("wr_par", size, cxx, arch, lanes=threads)
        read[level] = {"1": round(one_read, 2), "all": round(max(all_read, one_read), 2)}
        write[level] = {"1": round(one_write, 2), "all": round(max(all_write, one_write), 2)}
        irregular[level] = round(timed("gather", {"n": 4096, "m": n}, cxx, arch, fills={"k": f"index:{n}"}) / 4096, 3)
    return read, write, irregular


def pool(cxx: str, arch: str, threads: int, write_l2: float) -> dict[str, Any]:
    """The cost of starting a region on `used` lanes: its time less the work its lanes share."""
    points = []
    for n in (16384, 32768, 65536, 131072, 262144):
        used = min(threads, max(1, n // 8192))
        t = timed("wr_par", {"n": n}, cxx, arch, lanes=threads)
        points.append((used, t - n * 8 / write_l2 / used))
    mean_l = sum(u for u, _ in points) / len(points)
    mean_t = sum(t for _, t in points) / len(points)
    spread = sum((u - mean_l) ** 2 for u, _ in points) or 1.0
    slope = max(0.0, sum((u - mean_l) * (t - mean_t) for u, t in points) / spread)
    fork = max(0.0, mean_t - slope * (mean_l - 1))
    return {"fork_ns": round(fork, 1), "per_lane_ns": round(slope, 1), "cutoff": 16384, "grain": 8192,
            "points": [[u, round(t, 1)] for u, t in points]}  # fmt: skip


MAPPED = 32 << 20  # glibc's largest dynamic mmap threshold: an allocation this large is mapped afresh every time
PAGE = 4096


def allocation(cxx: str, arch: str, read: dict, write: dict) -> tuple[float, float]:
    """What one small allocation and its release cost, and what each 4 KiB page of an allocation the allocator maps
    afresh adds on its first touch. The kernel reads back what it allocated, so the compiler cannot elide it."""
    small = timed("alloc", {"n": 1}, cxx, arch)
    n = 2 * MAPPED // 8
    whole = timed("alloc", {"n": n}, cxx, arch)
    moved = n * 8 / read["dram"]["1"] + n * 8 / write["dram"]["1"]  # the zeroing, and the read back
    return small, max(0.0, whole - small - moved) / (n * 8 / PAGE)


def mca_cycles(cxx: str, arch: str) -> float | None:
    """Cycles per round of the dependent chain, as llvm-mca reads the compiled loop; None without llvm-mca."""
    from .native import loop_cycles

    return loop_cycles(BANDWIDTH, "chain", "imulq", cxx, arch)


def calibrate(cxx: str = "clang++", arch: str | None = None) -> dict[str, Any]:
    arch = resolve_arch(arch)
    threads, physical = lanes(), cores()
    ops, scalarizing, fit = operations(cxx, arch)
    read, write, irregular = bandwidth(cxx, arch, threads, physical)
    chain_ns = timed("chain", {"n": 100000}, cxx, arch) / 100000
    cycles = mca_cycles(cxx, arch)
    spawn = timed("spawn_one", {"n": 8}, cxx, arch)
    shared = timed("at_par", {"n": 1 << 20}, cxx, arch, lanes=threads) / (1 << 20)
    small, page = allocation(cxx, arch, read, write)
    model = cpu_model()
    return {
        "schema": "cairn.machine/1",
        "name": model,
        "origin": "measured",
        "notes": "Measured on a shared machine: other processes ran, so each number is the least-disturbed of nine "
        "blocks, and a quiet machine may be a little faster.",
        "measured": {
            "date": datetime.date.today().isoformat(),
            "platform": platform.platform(),
            "compiler": compiler_version(find(cxx)).split("\n")[0],
            "arch": arch,
            "statistic": "minimum of nine timed blocks after one warm-up, each block at least 2 ms",
            "operation_fit": fit,
            "chain_ns_per_round": round(chain_ns, 4),
            "chain_cycles_per_round": cycles,
        },
        "host": {
            "lanes": threads,
            "cache": caches(),
            "read": read,
            "write": write,
            "ops": {arch: {**ops, "keeps_scalar": scalarizing}},
            "pool": pool(cxx, arch, threads, write["l2"]["1"]),
            "spawn_ns": round(spawn, 1),
            "alloc_ns": round(small, 2),
            "page_ns": round(page, 1),
            "mapped_bytes": MAPPED,
            "irregular_ns": irregular,
            "atomic_ns": {"shared": round(shared, 3)},
            "ghz": round(cycles / chain_ns, 3) if cycles else 0.0,
            "cores": physical,
            "measured_bytes": {level: n * 8 for level, n in LEVEL_ELEMENTS.items()},
        },
        "device": None,
    }


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip() + f", {lanes()} lanes"
    except OSError:
        pass
    return platform.processor() or platform.machine()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, help="Where the new profile is written.")
    ap.add_argument("--cxx", default="clang++")
    ap.add_argument("--arch", help="The -march profile to measure; default: this host family's baseline.")
    ap.add_argument("--into", type=Path, metavar="PROFILE.json", help="Add this profile's operation costs for "
                    "--arch to an existing profile, and measure nothing else.")  # fmt: skip
    ap.add_argument("--allocation", action="store_true", help="With --into, measure again only what an allocation "
                    "costs, small and mapped afresh, beside the bandwidths the profile already holds.")  # fmt: skip
    a = ap.parse_args(argv)
    if a.into and a.allocation:
        existing = json.loads(a.into.read_text(encoding="utf-8"))
        host = existing["host"]
        small, page = allocation(a.cxx, resolve_arch(a.arch), host["read"], host["write"])
        host |= {"alloc_ns": round(small, 2), "page_ns": round(page, 1), "mapped_bytes": MAPPED}
        existing["measured"]["allocation"] = {"date": datetime.date.today().isoformat(), "kernel": "alloc",
                                              "mapped_at": 2 * MAPPED}  # fmt: skip
        a.into.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "calibrated", "profile": str(a.into), "alloc_ns": small, "page_ns": page}))
        return 0
    if a.into:  # One more -march profile's operation costs, beside the bandwidths already measured.
        existing = json.loads(a.into.read_text(encoding="utf-8"))
        arch = resolve_arch(a.arch)
        ops, scalarizing, fit = operations(a.cxx, arch)
        existing["host"]["ops"][arch] = {**ops, "keeps_scalar": scalarizing}
        existing["measured"].setdefault("operation_fits", {})[arch] = fit
        a.into.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "calibrated", "profile": str(a.into), "arch": arch, "scalarizing": scalarizing}))
        return 0
    if not a.out:
        ap.error("give --out for a new profile, or --into to add to one")
    result = calibrate(a.cxx, a.arch)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "calibrated", "profile": str(a.out), "ghz": result["host"]["ghz"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

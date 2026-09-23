#!/usr/bin/env python3
"""Time fused chains against the regions they were written as, on this host, interleaved, beside what the model
predicted for each.

Four kernels, each built twice from one source: once as written and once with `plan f { fuse K; }`. `blend` keeps
its intermediate in a local buffer, which the fused chain holds in its lanes instead; `layers` writes three arrays
the caller keeps, so fusing saves passes over memory but no array. `energy` squares on the lane pool and sums in
order on one thread, and fused it squares inside the in-order fold, on that one thread; `digest` hashes on the pool
and folds with a pooled reduce, and fused it hashes inside the pool's blocks. Every round times each variant once
at each size under each compiler, in an order that alternates, so a change of load on a shared machine falls on
both.
Only the host runs anything: `cairn.perf.measure` refuses device code.

    python3 bench/cpu/fusion.py [--rounds 3] [--out results/fusion/fusion.json]
    python3 bench/cpu/fusion.py --reprice results/fusion/fusion.json   # the model's columns again, nothing timed
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cairn.perf import measure
from cairn.perf.report import report

KERNELS = {
    "blend": (
        """fn blend(n:usize, out:rw<f64>[n], x:ro<f64>[n], a:f64, b:f64) {
  buffer scaled:f64[n] = zeroed;
  parallel i in n { scaled[i] = a * x[i] + 0.5; }
  parallel j in n { out[j] = scaled[j] * b - x[j]; }
}
""",
        2,
    ),
    "layers": (
        """fn layers(n:usize, x:ro<f64>[n], y:rw<f64>[n], z:rw<f64>[n], out:rw<f64>[n], a:f64) {
  parallel i in n { y[i] = x[i] * a; }
  parallel i in n { z[i] = y[i] + x[i]; }
  parallel i in n { out[i] = z[i] * z[i] - y[i]; }
}
""",
        3,
    ),
    "energy": (
        """fn energy(n:usize, x:ro<f64>[n]) -> f64 {
  buffer squared:f64[n] = zeroed;
  parallel i in n { squared[i] = x[i] * x[i] + 1.0; }
  let e = reduce + for i in n yield squared[i];
  return e;
}
""",
        2,
    ),
    "digest": (
        """fn mix(v:u64) -> u64 = mul_wrap(v ^ shr(v, 29), 0xbf58476d1ce4e5b9);
fn digest(n:usize, keys:ro<u64>[n]) -> u64 {
  buffer h:u64[n] = zeroed;
  parallel i in n { h[i] = mix(keys[i]); }
  let d = reduce add_wrap parallel j in n yield h[j];
  return d;
}
""",
        2,
    ),
}
SIZES = (1e5, 1e6, 1e7, 3e7)


def variants(name: str) -> dict[str, str]:
    source, regions = KERNELS[name]
    return {"written": source, "fused": source + f"plan {name} {{ fuse {regions}; }}\n"}


def predicted(name: str, n: float) -> dict[str, float]:
    return {
        v: report(src, [{"n": n}], {name})["functions"][name]["predictions"][0]["ns"]
        for v, src in variants(name).items()
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--out", type=Path, default=ROOT / "results/fusion/fusion.json")
    p.add_argument("--reprice", type=Path, help="Recompute the predicted columns of a record; time nothing.")
    a = p.parse_args()
    if a.reprice:
        record = json.loads(a.reprice.read_text())
        for row in record["rows"]:
            now = predicted(row["kernel"], row["n"])
            row |= {"predicted_written_ns": now["written"], "predicted_fused_ns": now["fused"],
                    "predicted_speedup": round(now["written"] / now["fused"], 3)}  # fmt: skip
        a.reprice.write_text(json.dumps(record, indent=2) + "\n")
        return 0
    compilers = [c for c in ("clang++", "g++") if shutil.which(c) or Path("/opt/llvm-21.1.8/bin", c).exists()]
    times: dict[str, list[float]] = {}
    started = time.time()
    for r in range(a.rounds):
        for cxx in compilers:
            for name in KERNELS:
                for n in SIZES:
                    order = ["written", "fused"] if (r + SIZES.index(n)) % 2 == 0 else ["fused", "written"]
                    for variant in order:
                        got = measure.time(variants(name)[variant], name, {"n": n}, cxx=cxx, blocks=5)
                        if got["status"] != "measured":
                            raise SystemExit(f"{name} {variant} {cxx} n={n:g}: {got}")
                        times.setdefault(f"{cxx}|{name}|{n:g}|{variant}", []).append(got["median_ns"])
                        print(f"round {r} {cxx} {name} n={n:g} {variant}: {got['median_ns'] / 1e6:.3f} ms", flush=True)
    rows = []
    for cxx in compilers:
        for name in KERNELS:
            for n in SIZES:
                key = f"{cxx}|{name}|{n:g}|"
                written, fused = statistics.median(times[key + "written"]), statistics.median(times[key + "fused"])
                now = predicted(name, n)
                rows.append({
                    "compiler": cxx, "kernel": name, "n": n,
                    "written_ns": written, "fused_ns": fused, "measured_speedup": round(written / fused, 3),
                    "predicted_written_ns": now["written"], "predicted_fused_ns": now["fused"],
                    "predicted_speedup": round(now["written"] / now["fused"], 3),
                    "rounds_written_ns": times[key + "written"], "rounds_fused_ns": times[key + "fused"],
                })  # fmt: skip
    record = {
        "schema": "cairn.bench.fusion/1",
        "host": platform.processor() or platform.machine(),
        "platform": platform.platform(),
        "compilers": compilers,
        "rounds": a.rounds,
        "sizes": list(SIZES),
        "seconds": round(time.time() - started, 1),
        "method": "cairn.perf.measure: the median of 5 blocks per timing, each block at least 2 ms; the median of "
        "the rounds per cell; written and fused alternate in order from one size and round to the next",
        "rows": rows,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps([{k: r[k] for k in ("compiler", "kernel", "n", "measured_speedup", "predicted_speedup")}
                      for r in rows], indent=1))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

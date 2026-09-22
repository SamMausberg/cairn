#!/usr/bin/env python3
"""Build and run every arm of every kernel in the preregistered baseline suite, and record what ran.

The protocol, the kernels, the arms, the sizes and the acceptance rule are fixed in
bench/suite/PREREGISTRATION.md before any number exists. This script only carries them out: it
emits each kernel, probes whether OpenMP and oneTBB actually run in parallel here, builds every arm
under both compilers with the project's own flags, counts each arm's safety boundaries against the
CAIRN build receipt, runs what built, and writes the raw timings under results/. It never writes
under evidence/ and it refuses to write anything at all when a case disagreed with its sequential
result, which is bench/host_regions/host_regions.py's rule kept deliberately.

  python bench/suite/harness.py --smoke          # tiny sizes, seconds, proves the harness works
  python bench/suite/harness.py --build-only     # every arm built and priced, nothing timed
  python bench/suite/harness.py                  # the full preregistered sweep

A ratio this suite reports is one machine's, one lane count's and one pair of compilers'. It is not
a claim about a tuned kernel, and bench/suite/report.py prints losses in the same tables as wins.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.projects.toolchain import find
from support import best_profile, environment, generate, profile_flags

SUITE = ROOT / "bench/suite"
KERNEL_ROOT = SUITE / "kernels"
COMPILERS = ("g++", "clang++")

# A win is a median ratio of at least this much that still holds at every larger size, under both
# compilers. Anything else is level or a loss, and both appear in the same table.
WIN_RATIO = 1.25

# The preregistered sweep. Decade steps from a thousand to a hundred million, plus a thousand
# back-to-back regions at three widths, two of which are below the runtime's own serial cutoff.
FULL_SIZES = (1000, 10000, 100000, 1000000, 10000000, 100000000)
FULL_WIDTHS = (64, 1024, 16384)
FULL_REGIONS = 1000
FULL_COVER = 16000000
SMOKE_SIZES = (1000, 20000)
SMOKE_WIDTHS = (64,)
SMOKE_REGIONS = 10
SMOKE_COVER = 100000
DUMP_SIZE = 64  # the size at which each arm is checked against the kernel's independent Python oracle

# The three grain rows bench.hpp compiles, by the number its build line carries.
GRAIN_ROWS = {"library_default": 0, "cairn_claim_assigned": 1, "cairn_claim_on_demand": 2}
# Which rows each library arm is built for. TBB claims and steals in both of its rows, so it has no
# separate on-demand row; a one-thread arm has no grain to choose at all.
ARM_ROWS = {
    "omp": ("library_default", "cairn_claim_assigned", "cairn_claim_on_demand"),
    "tbb": ("library_default", "cairn_claim_assigned"),
}

# The four boundaries a source can be counted for. The fifth and sixth, the strict floating contract
# and the build line, are the same for every arm by construction and are recorded as the flag list.
SITE_PATTERNS = {
    "entry": r"\bBG_(?:VIEW|DISJOINT)\(",
    "element": r"\bBG_(?:AT|PART)\(",
    "arithmetic": r"\bBG_(?:ADD|SUB|MUL|DIVIDE|SHR)\(",
    "conversion": r"\bBG_(?:CONVERT|TRUNCATE)\(",
}
# How the build receipt's own vocabulary maps onto those four. `shift` is cr::shr's trap on an
# over-wide shift count, which is a checked arithmetic site like the others.
RECEIPT_BOUNDARY = {
    "view_entry": "entry",
    "bounds": "element",
    "overflow": "arithmetic",
    "division": "arithmetic",
    "shift": "arithmetic",
    "conversion": "conversion",
}
# A guard that fails ends in cr::trap, which g++ inlines to a call of abort and clang++ leaves as a
# call of cr::trap itself. Either relocation in a kernel's own section proves the boundary is there.
GUARD_FAILURE = ("abort", "cr4trapEv")
# BENCH_GUARDED as a word: 2 is the matched build the 1.4 addendum to the preregistration adds.
BOUNDARIES = {1: "guarded", 0: "unguarded", 2: "matched"}

BOUNDARY = "One machine, one lane count, two compilers. Not a tuned-kernel claim, and not another host's numbers."

# The preregistered kernels. `claim` says what a row of this kernel's table is allowed to assert.
KERNELS = {
    "saxpy_f32": {
        "cairn_arms": {"cairn": None},
        "arms": ("plain", "omp", "tbb"),
        "claim": "ratio",
        "why": "two flops over twelve bytes, so a large region is bound by memory bandwidth",
    },
    "mixed_u64": {
        "cairn_arms": {"cairn": None},
        "arms": ("plain", "omp", "tbb"),
        "claim": "ratio",
        "why": "a dependent integer mix in registers, so a large region is bound by the cores",
    },
    "sum_u64_wrap": {
        "cairn_arms": {"cairn": None, "cairn_atomic": "sum_u64_atomic", "cairn_pool": "sum_u64_pool"},
        "arms": ("plain", "omp", "tbb"),
        "claim": "ratio",
        "why": "wrapping addition is order independent, so a reassociating baseline is the same function",
    },
    "dot_f64": {
        "cairn_arms": {"cairn": None},
        "arms": ("plain", "omp", "tbb"),
        "claim": "semantic_difference",
        "why": "a strict in-order fold and a reassociating reduction are different functions, not two speeds",
    },
    "compact_even": {
        "cairn_arms": {"cairn": None},
        "arms": ("plain", "omp", "tbb"),
        "claim": "ratio",
        "why": "the certified collector against std::copy_if and a two-pass parallel compaction",
    },
    "histogram_u32": {
        "cairn_arms": {"cairn": None, "cairn_blocks": "histogram_u32_blocks"},
        "arms": ("plain", "omp", "tbb"),
        "claim": "expressiveness",
        "why": "the lane rule forbids the shared-bin parallel shape, so the CAIRN arm is sequential",
    },
    "stencil_1d": {
        "cairn_arms": {"cairn": None, "cairn_wrap": "stencil_1d_wrap"},
        "arms": ("plain", "omp", "tbb"),
        "claim": "ratio",
        "rejects": ("in_place_rejected.cairn", "E-PARALLEL-RACE"),
        "why": "the out-of-place shape is accepted and the in-place shape is refused, which no C++ toolchain refuses",
    },
    "tasks_split": {
        "cairn_arms": {"cairn": None},
        "arms": ("plain", "threads", "omp", "tbb"),
        "rows": ("library_default",),
        "claim": "ratio",
        "why": "four visibly disjoint parts under leases, against std::thread, OpenMP sections and parallel_invoke",
    },
}

OPENMP_PROBE = """// Does OpenMP run in parallel here, or only compile? A runtime that links and then runs on one
// thread would otherwise become a silent baseline. Prints the widest thread id it actually saw.
#include <cstdio>
#include <cstdlib>
#include <vector>
extern "C" int omp_get_thread_num(void);
extern "C" int omp_get_max_threads(void);
extern "C" void omp_set_num_threads(int);
int main(int argc, char** argv) {
  const int want = argc > 1 ? std::atoi(argv[1]) : 2;
  omp_set_num_threads(want);
  const int n = 1 << 20;
  std::vector<int> seen(std::size_t(n), 0);
  int* p = seen.data();
#pragma omp parallel for schedule(static)
  for (int i = 0; i < n; ++i) p[i] = omp_get_thread_num();
  int widest = 0;
  for (int i = 0; i < n; ++i)
    if (p[i] > widest) widest = p[i];
  std::printf("{\\"workers\\": %d, \\"max_threads\\": %d}\\n", widest + 1, omp_get_max_threads());
  return 0;
}
"""

TBB_PROBE = """// The same question for oneTBB, asked of the arena the arms will use. Each worker thread counts
// itself once through a thread_local flag, and the body is dear enough that a partitioner has a
// reason to spread it, so the answer is how many threads really ran and not how many were allowed.
#include <tbb/blocked_range.h>
#include <tbb/global_control.h>
#include <tbb/parallel_for.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
namespace {
std::atomic<int> distinct{0};
thread_local bool counted = false;
}  // namespace
int main(int argc, char** argv) {
  const std::size_t want = argc > 1 ? std::size_t(std::atol(argv[1])) : 2;
  tbb::global_control held(tbb::global_control::max_allowed_parallelism, want);
  std::atomic<std::uint64_t> total{0};
  tbb::parallel_for(tbb::blocked_range<std::size_t>(0, std::size_t(1) << 16, 64),
                    [&](const tbb::blocked_range<std::size_t>& r) {
                      if (!counted) {
                        counted = true;
                        distinct.fetch_add(1);
                      }
                      std::uint64_t mix = r.begin();
                      for (std::size_t i = r.begin(); i < r.end(); ++i)
                        for (int k = 0; k < 200; ++k) mix = mix * 6364136223846793005ull + i;
                      total.fetch_add(mix);
                    });
  std::printf("{\\"workers\\": %d, \\"checksum\\": %llu}\\n", distinct.load(),
              static_cast<unsigned long long>(total.load()));
  return 0;
}
"""


def library_directories() -> list[Path]:
    """Directories that hold an OpenMP runtime, found rather than assumed. Nothing is downloaded."""
    found = []
    for base in (Path("/usr/lib"), Path("/usr/local/lib"), Path("/usr/lib64")):
        if not base.is_dir():
            continue
        for candidate in [base, *sorted(base.glob("llvm-*/lib"))]:
            if any(candidate.glob("libomp.so*")) or any(candidate.glob("libgomp.so*")):
                found.append(candidate)
    return found


def openmp_candidates() -> list[list[str]]:
    """Every link line worth trying for OpenMP, most ordinary first. The environment wins if set."""
    override = os.environ.get("CAIRN_BENCH_OMP_FLAGS")
    if override is not None:
        return [override.split()]
    lines = [["-fopenmp"]]
    for directory in library_directories():
        lines.append(["-fopenmp", f"-L{directory}", f"-Wl,-rpath,{directory}"])
    return lines


def tbb_candidates() -> list[list[str]]:
    override = os.environ.get("CAIRN_BENCH_TBB_FLAGS")
    return [override.split()] if override is not None else [["-ltbb"]]


class Harness:
    def __init__(self, args) -> None:
        self.args = args
        self.arch = best_profile(*COMPILERS)
        # -ffunction-sections is what makes one function one inspectable section, which is how the
        # guard presence check below finds a kernel's own code and nothing else.
        self.flags = profile_flags("exe", self.arch, add=["-ffunction-sections"])
        self.work = args.out.resolve()
        self.lanes = len(os.sched_getaffinity(0))
        self.commands: list[list[str]] = []
        self.kernels = args.kernels or list(KERNELS)

    # Building ----------------------------------------------------------------------------------

    def run(self, argv: list[str], **kw) -> subprocess.CompletedProcess:
        self.commands.append([str(a) for a in argv])
        return subprocess.run([str(a) for a in argv], capture_output=True, text=True, **kw)

    def compile(self, cxx: str, sources: list[Path], out: Path, include: Path, extra: list[str], defines: list[str]):
        """One build, with the project's own flags and nothing of this script's own invention."""
        argv = [find(cxx), *self.flags, *defines, f"-I{include}", *[str(s) for s in sources], *extra, "-o", str(out)]
        done = self.run(argv)
        warned = bool(done.stderr.strip())
        return {
            "argv": argv,
            "exit_code": done.returncode,
            "built": done.returncode == 0 and not warned,
            "stderr": done.stderr[-2000:],
        }

    # Probing -----------------------------------------------------------------------------------

    def probe(self, cxx: str, name: str, text: str, candidates: list[list[str]]) -> dict:
        """Build and RUN a library probe. A library that links and then runs on one thread is not
        available for this suite: it would become a silent sequential baseline."""
        source = self.work / f"probe_{name}.cpp"
        source.write_text(text)
        attempts = []
        for extra in candidates:
            exe = self.work / f"probe_{name}_{cxx.replace('+', 'p')}"
            made = self.compile(cxx, [source], exe, self.work, extra, [])
            if not made["built"]:
                attempts.append({"extra_flags": extra, "status": "did-not-build", "stderr": made["stderr"]})
                continue
            done = self.run([exe, str(self.lanes)])
            if done.returncode != 0:
                attempts.append({"extra_flags": extra, "status": "did-not-run", "stderr": done.stderr[-2000:]})
                continue
            seen = json.loads(done.stdout)
            if self.lanes > 1 and seen["workers"] < 2:
                attempts.append({"extra_flags": extra, "status": "ran-on-one-thread", "observed": seen})
                continue
            return {"status": "available", "extra_flags": extra, "observed": seen, "attempts": attempts}
        return {"status": "unavailable", "reason": f"no {name} link line both built and ran in parallel", "attempts": attempts}  # fmt: skip

    # Boundaries --------------------------------------------------------------------------------

    @staticmethod
    def source_sites(path: Path) -> dict[str, int]:
        text = path.read_text(encoding="utf-8")
        return {name: len(re.findall(pattern, text)) for name, pattern in SITE_PATTERNS.items()}

    @staticmethod
    def receipt_sites(receipt: dict, emitted: str, entry: str) -> dict[str, int]:
        """CAIRN's own count for one entry point and everything it calls, which is the program a
        baseline arm of that entry actually mirrors. A kernel source may hold a second entry, and
        summing the file would price a program no arm runs. cr::disjoint has no receipt key of its
        own, because it follows from the parameter list, so it is counted in that entry's own
        emitted body."""
        reached, todo = set(), [entry]
        while todo:
            name = todo.pop()
            if name in reached or name not in receipt["functions"]:
                continue
            reached.add(name)
            todo.extend(receipt["functions"][name]["calls"])
        counts = dict.fromkeys(SITE_PATTERNS, 0)
        for name in reached:  # A site the checker discharged is not in the emitted code (compiler/facts.py).
            row = receipt["functions"][name]
            for key, many in row["syntactic_check_sites"].items():
                if key in RECEIPT_BOUNDARY:
                    counts[RECEIPT_BOUNDARY[key]] += many - row.get("discharged_check_sites", {}).get(key, 0)
        for name in reached:
            found = re.search(rf'^extern "C" [^;]*?\bcf_{re.escape(name)}\([^;]*?\{{\n(.*?)^\}}$', emitted, re.S | re.M)
            counts["entry"] += found.group(1).count("cr::disjoint(") if found else 0
        return counts

    def guard_presence(self, job: dict, work: Path) -> dict:
        """Does the compiled kernel really contain a guard failure path? Derived from the object,
        not asserted: every section whose name carries the kernel entry, and every relocation in it
        that names cr::trap or abort. Unknown is reported as unknown, which is never a pass."""
        cairn = job["arm"].startswith("cairn")
        source, symbol = (job["sources"][-1], "cf_") if cairn else (job["sources"][0], "arm_run")
        cxx, defines = job["compiler"], job["defines"]
        tag = "".join(c for c in "".join(defines) if c.isalnum())
        obj = work / f"presence_{job['arm']}_{cxx.replace('+', 'p')}_{tag}.o"
        # The same line the executable was built with, minus the link: a flag that changes code, such
        # as -fopenmp, has to stay, or the object would price a program the suite never ran, while a
        # library or a search path is an unused argument here and clang++ treats that as an error.
        compiling = [f for f in job["extra_flags"] if not f.startswith(("-l", "-L", "-Wl,"))]
        made = self.compile(cxx, [source], obj, work, ["-c", *compiling], defines)
        if not made["built"]:
            return {"status": "unknown", "reason": "the object did not build", "stderr": made["stderr"]}
        heads = self.run(["objdump", "-h", obj])
        sections = sorted({w for line in heads.stdout.splitlines() for w in line.split() if symbol in w})
        if not sections:
            return {"status": "unknown", "reason": f"no section carrying {symbol}"}
        failures = 0
        for section in sections:
            dumped = self.run(["objdump", "-r", "-j", section, obj]).stdout
            failures += sum(any(mark in line for mark in GUARD_FAILURE) for line in dumped.splitlines())
        return {"status": "read", "sections": sections, "guard_failure_relocations": failures}

    # Driving -----------------------------------------------------------------------------------

    def emit(self, kernel: str) -> tuple[dict, Path]:
        out = self.work / kernel
        receipt = generate(KERNEL_ROOT / kernel / "kernel.cairn", out)
        return receipt, out

    def rejection(self, kernel: str) -> dict | None:
        """A kernel may preregister a shape the compiler must refuse. Establish it by running the
        compiler, and record the code it actually printed."""
        spec = KERNELS[kernel].get("rejects")
        if spec is None:
            return None
        path, want = spec
        done = self.run([sys.executable, ROOT / "bin/cairn", "check", KERNEL_ROOT / kernel / path])
        report = json.loads(done.stdout) if done.stdout.strip().startswith("{") else {}
        return {
            "source": str(KERNEL_ROOT / kernel / path),
            "expected_code": want,
            "actual_code": report.get("code"),
            "message": report.get("message"),
            "refused_as_preregistered": report.get("status") == "rejected" and report.get("code") == want,
        }

    def jobs(self, kernel: str, work: Path, libraries: dict, kept: dict[str, int]) -> list[dict]:
        """Every arm under every compiler: a baseline guarded, unguarded, and matched to the categories of
        boundary the CAIRN arm still carries (`kept`), which is the 1.4 addendum's third build."""
        spec = KERNELS[kernel]
        matched = [f"-DBENCH_KEEP_{category.upper()}={int(kept[category] > 0)}" for category in SITE_PATTERNS]
        out = []
        for cxx in self.args.compilers:
            for arm in (*spec["cairn_arms"], *spec["arms"]):
                cairn = arm.startswith("cairn")
                sources = [KERNEL_ROOT / kernel / f"{arm}.cpp"] + ([work / "kernel.cpp"] if cairn else [])
                rows = spec.get("rows", ARM_ROWS.get(arm, ("library_default",)))
                extra, skip = [], None
                if arm in ("omp", "tbb"):
                    found = libraries[cxx][arm]
                    extra = found.get("extra_flags", [])
                    skip = None if found["status"] == "available" else found["reason"]
                for row in rows:
                    for guarded in (1, 0, 2) if not cairn else (1,):
                        out.append(
                            {
                                "kernel": kernel,
                                "arm": arm,
                                "compiler": cxx,
                                "guarded": guarded == 1,
                                "boundary": BOUNDARIES[guarded],
                                "grain_row": row if arm in ARM_ROWS else "not_applicable",
                                "sources": sources,
                                "extra_flags": extra,
                                "defines": [
                                    f"-DBENCH_GUARDED={guarded}",
                                    f"-DBENCH_GRAIN_ROW={GRAIN_ROWS[row]}",
                                    *(matched if guarded == 2 else []),
                                ],
                                "exe": work / f"{arm}_{cxx.replace('+', 'p')}_g{guarded}_{row}",
                                "unavailable": skip,
                            }
                        )
        return out

    def build(self, job: dict, work: Path) -> dict:
        record = {k: v for k, v in job.items() if k not in ("sources", "exe")}
        record["sources"] = [str(s) for s in job["sources"]]
        record["exe"] = str(job["exe"])
        if job["unavailable"]:
            record["status"] = "unavailable"
            return record
        made = self.compile(job["compiler"], job["sources"], job["exe"], work, job["extra_flags"], job["defines"])
        record.update(made)
        record["status"] = "built" if made["built"] else "did-not-build"
        return record

    def measure(self, job: dict, sizes, widths, regions, cover) -> dict:
        env = {
            **os.environ,
            "CAIRN_LANES": str(self.lanes),
            "OMP_NUM_THREADS": str(self.lanes),  # arm_setup sets it too; the two agree by construction
        }
        argv = [
            str(job["exe"]),
            "--sizes", ",".join(str(n) for n in sizes),
            "--region-widths", ",".join(str(n) for n in widths),
            "--regions", str(regions),
            "--cover", str(cover),
            "--reps", str(self.args.reps),
        ]  # fmt: skip
        self.commands.append(argv)
        done = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=self.args.timeout)
        if done.returncode != 0:
            return {"status": "failed", "exit_code": done.returncode, "argv": argv, "stderr": done.stderr[-2000:]}
        return {"status": "measured", "argv": argv, **json.loads(done.stdout)}

    def oracle(self, kernel: str, job: dict) -> dict:
        """The arm against an independent Python result. Neither the compiler nor the flags nor the
        in-process sequential loop are shared with it, so a mistake common to both C++ paths fails."""
        spec = importlib.util.spec_from_file_location(f"oracle_{kernel}", KERNEL_ROOT / kernel / "oracle.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        env = {**os.environ, "CAIRN_LANES": str(self.lanes), "OMP_NUM_THREADS": str(self.lanes)}
        argv = [str(job["exe"]), "--dump", str(DUMP_SIZE)]
        self.commands.append(argv)
        done = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=self.args.timeout)
        if done.returncode != 0:
            return {"status": "failed", "exit_code": done.returncode, "stderr": done.stderr[-2000:]}
        printed = json.loads(done.stdout)
        row = {"status": "checked", "n": DUMP_SIZE, "agrees": bool(module.agrees(DUMP_SIZE, printed["result"]))}
        # A kernel whose claim is a semantic difference offers a second question its oracle answers
        # apart from the pass: whether this arm computed the function the CAIRN source names, bit for
        # bit. It is reported, never a failure, because being a different function is the finding.
        if hasattr(module, "folds_in_order"):
            row["folds_in_order"] = bool(module.folds_in_order(DUMP_SIZE, printed["result"]))
        row["result"] = printed["result"] if not isinstance(printed["result"], list) else "an array"
        return row


def main() -> int:
    ask = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ask.add_argument("--out", type=Path, default=ROOT / "results/bench_suite", help="Where builds and timings go.")
    ask.add_argument("--kernels", type=lambda s: s.split(","), help="A subset of the preregistered kernels.")
    ask.add_argument("--compilers", type=lambda s: s.split(","), default=list(COMPILERS))
    ask.add_argument("--smoke", action="store_true", help="Tiny sizes: proves the harness works, measures nothing.")
    ask.add_argument("--build-only", action="store_true", help="Build and price every arm; time nothing.")
    ask.add_argument("--reps", type=int, default=9, help="Timed blocks per case; the median is reported.")
    ask.add_argument("--timeout", type=int, default=3600, help="Seconds one arm's sweep may take.")
    args = ask.parse_args()

    if "evidence" in args.out.resolve().parts:
        raise SystemExit("This harness never writes under evidence/. A release record is a separate, later step.")
    unknown = [k for k in (args.kernels or []) if k not in KERNELS]
    if unknown:
        raise SystemExit(f"Not preregistered: {', '.join(unknown)}. The kernel list is fixed before any run.")

    harness = Harness(args)
    harness.work.mkdir(parents=True, exist_ok=True)
    sizes = SMOKE_SIZES if args.smoke else FULL_SIZES
    widths = SMOKE_WIDTHS if args.smoke else FULL_WIDTHS
    regions = SMOKE_REGIONS if args.smoke else FULL_REGIONS
    cover = SMOKE_COVER if args.smoke else FULL_COVER
    started = time.time()

    libraries = {cxx: {} for cxx in args.compilers}
    for cxx in args.compilers:
        libraries[cxx]["omp"] = harness.probe(cxx, "openmp", OPENMP_PROBE, openmp_candidates())
        libraries[cxx]["tbb"] = harness.probe(cxx, "tbb", TBB_PROBE, tbb_candidates())
        for name, found in libraries[cxx].items():
            why = "" if found["status"] == "available" else f" ({found.get('reason', '')})"
            print(f"{cxx:8} {name:4} {found['status']}{why}", flush=True)

    builds, safety, runs, disagreed = [], {}, {}, []
    for kernel in harness.kernels:
        receipt, work = harness.emit(kernel)
        emitted = (work / "kernel.cpp").read_text(encoding="utf-8")
        # One count per CAIRN arm, over its own entry point and everything that entry calls. The
        # baselines mirror the primary entry, so that one is what every other arm is measured against.
        by_arm = {
            arm: harness.receipt_sites(receipt, emitted, entry or kernel)
            for arm, entry in KERNELS[kernel]["cairn_arms"].items()
        }
        cairn_sites = by_arm["cairn"]
        jobs = harness.jobs(kernel, work, libraries, cairn_sites)

        def one_build(job, work=work):
            return harness.build(job, work)

        def one_presence(job, work=work):
            return harness.guard_presence(job, work)

        with concurrent.futures.ThreadPoolExecutor(max_workers=harness.lanes) as pool:
            made = list(pool.map(one_build, jobs))
        builds.extend(made)
        priced = {}
        for job, record in zip(jobs, made, strict=True):
            name = f"{job['arm']}|{job['boundary']}|{job['compiler']}"
            if name not in priced and record["status"] == "built":
                priced[name] = job
        with concurrent.futures.ThreadPoolExecutor(max_workers=harness.lanes) as pool:
            seen = list(pool.map(one_presence, priced.values()))
        arms = {}
        for (name, job), presence in zip(priced.items(), seen, strict=True):
            cairn = job["arm"].startswith("cairn")
            sites = by_arm[job["arm"]] if cairn else harness.source_sites(job["sources"][0])
            if not cairn and job["boundary"] != "guarded":  # A matched build keeps only the categories it names.
                sites = {k: v if job["boundary"] == "matched" and cairn_sites[k] else 0 for k, v in sites.items()}
            equal = sites == cairn_sites
            arms[name] = {
                "boundaries": sites,
                "equal_to_cairn": equal,
                "status": "boundaries:equal" if equal else "boundaries:unequal",
                "guard_failure_path": presence,
            }
        # The object check is read as a difference, not as a count. An arm may call abort for a
        # reason of its own, and that relocation is there in both builds; only the ones the -D adds
        # are guards. A CAIRN arm has no unguarded twin, so its own count is what there is.
        for name, arm in arms.items():
            which, guard, cxx = name.split("|")
            here = arm["guard_failure_path"].get("guard_failure_relocations")
            twin = arms.get(f"{which}|{'guarded' if guard == 'unguarded' else 'unguarded'}|{cxx}", {})
            there = twin.get("guard_failure_path", {}).get("guard_failure_relocations")
            if here is None or (there is None and not which.startswith("cairn")):
                arm["boundary_in_the_object"] = "unknown"
            elif which.startswith("cairn"):
                arm["boundary_in_the_object"] = "present" if here > 0 else "absent"
            elif guard != "unguarded":  # guarded, or matched to what the CAIRN arm keeps
                arm["boundary_in_the_object"] = "present" if here > there else "absent"
            else:  # the unguarded twin: fewer failure paths than the guarded build, or say nothing
                arm["boundary_in_the_object"] = "absent" if here < there else "unknown"
        safety[kernel] = {
            "kernel": kernel,
            "claim": KERNELS[kernel]["claim"],
            "why_this_kernel_is_in": KERNELS[kernel]["why"],
            "cairn_receipt_check_sites": {k: v["syntactic_check_sites"] for k, v in receipt["functions"].items()},
            "cairn_entry_points": {arm: entry or kernel for arm, entry in KERNELS[kernel]["cairn_arms"].items()},
            "cairn_boundaries": cairn_sites,
            "cairn_boundaries_by_arm": by_arm,
            "float_contract_flags": [f for f in harness.flags if f.startswith(("-ffp-contract", "-fno-fast-math"))],
            "build_line": harness.flags,
            "arms": arms,
            "preregistered_rejection": harness.rejection(kernel),
        }
        if args.build_only:
            continue
        rows = []
        for job, record in zip(jobs, made, strict=True):
            if record["status"] != "built":
                rows.append({**{k: v for k, v in record.items() if k != "argv"}, "status": record["status"]})
                continue
            checked = harness.oracle(kernel, job)
            timed = harness.measure(job, sizes, widths, regions, cover)
            print(f"  {kernel} {job['arm']} {job['compiler']} {job['grain_row']} {timed['status']}", flush=True)
            row = {
                "kernel": kernel,
                "arm": job["arm"],
                "compiler": job["compiler"],
                "guarded": job["guarded"],
                "boundary": job["boundary"],
                "grain_row": job["grain_row"],
                "independent_oracle": checked,
                "timings": timed,
            }
            if checked["status"] != "checked" or not checked["agrees"]:
                disagreed.append(f"{kernel}/{job['arm']}/{job['compiler']}: the independent oracle disagreed")
            if timed["status"] != "measured" or not timed.get("every_case_agreed_with_the_sequential_result"):
                disagreed.append(
                    f"{kernel}/{job['arm']}/{job['compiler']}: a case disagreed with its sequential result"
                )
            rows.append(row)
        runs[kernel] = rows
        print(f"{kernel}: {sum(r['status'] == 'built' for r in made)} of {len(made)} arms built")

    if disagreed:
        for line in disagreed:
            print(line, file=sys.stderr)
        raise SystemExit("A case disagreed with its reference. Nothing was written.")

    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    record = {
        "preregistration": str(SUITE / "PREREGISTRATION.md"),
        "mode": "smoke" if args.smoke else "build-only" if args.build_only else "full",
        "acceptance": {
            "win_ratio": WIN_RATIO,
            "rule": "a win is a median ratio of at least win_ratio that holds at every larger size under both compilers",
        },
        "sizes": list(sizes),
        "region_widths": list(widths),
        "regions": regions,
        "elements_one_timed_block_covers": cover,
        "environment": {
            **environment(*args.compilers, arch=harness.arch),
            "lanes": harness.lanes,
            "lane_variable": "CAIRN_LANES, OMP_NUM_THREADS and the TBB arena all take this one number",
            "cpus_were_not_pinned": True,
            "platform": platform.platform(),
            "flags": harness.flags,
            "commit": head.stdout.strip(),
            "seconds": round(time.time() - started, 1),
        },
        "libraries": libraries,
        "safety": safety,
        "builds": builds,
        "runs": runs,
        "commands": harness.commands,
        "boundary": BOUNDARY,
    }
    (harness.work / "suite.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"wrote {harness.work / 'suite.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

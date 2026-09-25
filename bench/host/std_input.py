#!/usr/bin/env python3
"""Time reading all of standard input into a Vec[u8], and pushing into a Vec, before and after a change to std.

Each program below is built as `cairn build --kind exe` builds it, once by this tree and, with `--before DIR`, once by
the checkout in DIR (its `bin/cairn`, so its own `std`). Every program then reads the same input, `--megabytes` of
text, once from a regular file and once through a pipe, and the builds run in alternation, `--runs` times each, so
load that comes and goes on a shared machine falls on both. A run records its wall time and the most memory the
process held (its maximum resident set). Nothing is written unless every run of every build printed what the input
holds. A program one tree cannot build is recorded as refused there. It needs GNU time at /usr/bin/time.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from support import version

TIME = "/usr/bin/time"  # GNU time, for the peak resident set

PROGRAMS = {
    # The loop the skill's example and every probe task wrote: 4096 bytes a read, appended with extend_from.
    "chunk_loop": """
import std.core (Result);
import std.io as io;
import std.vec (Vec);
fn main() -> i32 {
  let mut input = vec.new[u8]();
  stack chunk:u8[4096] = zeroed;
  let mut more = true;
  while more {
    match io.read_stdin(4096, chunk) {
      Ok(got) => { if got == 0 { more = false; } else { vec.extend_from(input, got, chunk[0..got]); } }
      Err(_) => return 1;
    }
  }
  println(input.len);
  return 0;
}
""",
    # read_to_end on descriptor 0.
    "read_to_end": """
import std.core (Result);
import std.io as io;
import std.vec (Vec);
fn main() -> i32 {
  let mut input = vec.new[u8]();
  let stdin = io.File(0);
  let read = io.read_to_end(stdin, input);
  io.close(stdin);
  match read { Ok(_) => {} Err(_) => return 1; }
  println(input.len);
  return 0;
}
""",
    # One push per input byte: growth through reserve alone.
    "push_each": """
import std.core (Result);
import std.io as io;
import std.vec (Vec);
fn main() -> i32 {
  let mut input = vec.new[u8]();
  let stdin = io.File(0);
  let read = io.read_to_end(stdin, input);
  io.close(stdin);
  match read { Ok(_) => {} Err(_) => return 1; }
  let mut copy = vec.new[u8]();
  for b in input { vec.push(copy, b); }
  println(copy.len);
  return 0;
}
""",
}


def text(megabytes: int) -> bytes:
    """Lines of words and numbers, the shape the probes read, from a fixed seed."""
    pick = random.Random(7)
    words = ["open", "move", "close", "alice", "bob", "carol", "dave", "erin", "frank"]
    lines, size = [], 0
    while size < megabytes << 20:
        line = f"{pick.choice(words)} {pick.choice(words)} {pick.choice(words)} {pick.randint(0, 10**6)}\n"
        lines.append(line)
        size += len(line)
    return "".join(lines).encode()


def built(cairn: list[str], name: str, work: Path, cxx: str) -> str | None:
    """The executable `cairn` builds from PROGRAMS[name], or None when that tree refuses it."""
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{name}.cairn").write_text(PROGRAMS[name])
    done = subprocess.run([*cairn, "build", str(work / f"{name}.cairn"), "--kind", "exe", "--cxx", cxx, "--out",
                           str(work / "out"), "--format", "json"], capture_output=True, text=True, timeout=600)  # fmt: skip
    record = json.loads(done.stdout)
    return record["artifact"] if record.get("status") == "native-built" else None


def timed(exe: str, path: Path, piped: bool) -> dict:
    """One run over the input, from the file itself or through `cat`; its wall time, peak memory and output. The
    peak comes from GNU time, whose child starts from its small image: a child of this process would count ours."""
    peak = path.with_name("peak")
    with open(path, "rb") as source:
        feeder = subprocess.Popen(["cat", str(path)], stdout=subprocess.PIPE) if piped else None
        start = time.perf_counter()
        done = subprocess.run([TIME, "-f", "%M", "-o", str(peak), exe], stdin=feeder.stdout if feeder else source,
                              capture_output=True, text=True)  # fmt: skip
        seconds = time.perf_counter() - start
        if feeder:
            feeder.stdout.close()
            feeder.wait()
    return {"seconds": round(seconds, 4), "max_rss_kb": int(peak.read_text().split()[-1]), "status": done.returncode,
            "printed": done.stdout}  # fmt: skip


def summary(runs: list[dict]) -> dict:
    seconds = [r["seconds"] for r in runs]
    peaks = [r["max_rss_kb"] for r in runs]
    return {"median_s": statistics.median(seconds), "min_s": min(seconds), "max_s": max(seconds),
            "max_rss_kb": max(peaks), "runs": seconds}  # fmt: skip


def main() -> int:
    ask = argparse.ArgumentParser(description=__doc__)
    ask.add_argument("--before", type=Path, help="A checkout whose bin/cairn builds the before arm.")
    ask.add_argument("--cxx", default="clang++")
    ask.add_argument("--megabytes", type=int, default=77)
    ask.add_argument("--runs", type=int, default=5)
    ask.add_argument("--out", type=Path, required=True)
    ask.add_argument("--note", default="", help="One sentence recorded beside the numbers.")
    args = ask.parse_args()
    trees = {"after": ROOT, **({"before": args.before.resolve()} if args.before else {})}
    load_before = os.getloadavg()
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        data = text(args.megabytes)
        (work / "input.txt").write_bytes(data)
        wanted = f"{len(data)}\n"
        exes = {
            (arm, name): built([sys.executable, str(tree / "bin/cairn")], name, work / arm / name, args.cxx)
            for arm, tree in trees.items()
            for name in PROGRAMS
        }
        runs: dict[tuple[str, str, str], list[dict]] = {}
        for _ in range(args.runs):
            for name in PROGRAMS:
                for how in ("file", "pipe"):
                    for arm in sorted(trees):  # after, then before: alternation, not two blocks
                        if exes[arm, name]:
                            run = timed(exes[arm, name], work / "input.txt", how == "pipe")
                            if run["status"] != 0 or run["printed"] != wanted:
                                raise SystemExit(f"{arm} {name} over a {how} printed {run['printed']!r}; nothing kept")
                            runs.setdefault((arm, name, how), []).append(run)
    record = {
        "schema": "cairn.bench.std_input/1",
        "compiler": version(args.cxx).splitlines()[0],
        "machine": {"platform": platform.platform(), "cpus": os.cpu_count()},
        "load_average": {"before": [round(x, 2) for x in load_before], "after": [round(x, 2) for x in os.getloadavg()]},
        "input_bytes": len(data),
        "note": args.note,
        "arms": {
            arm: {
                "tree": str(tree),
                "programs": {
                    name: {how: summary(runs[arm, name, how]) for how in ("file", "pipe")} if exes[arm, name]
                    else "refused"
                    for name in PROGRAMS
                },
            }
            for arm, tree in trees.items()
        },
    }  # fmt: skip
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n")
    for arm in record["arms"]:
        for name, cell in record["arms"][arm]["programs"].items():
            for how, s in cell.items() if isinstance(cell, dict) else [("", {})]:
                print(arm, name, how, s.get("median_s", "refused"), s.get("max_rss_kb", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

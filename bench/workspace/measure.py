#!/usr/bin/env python3
"""Time what an agent waits for over `cairn mcp`: repeated checks of an unchanged project, opening an edit session,
and edit-then-check cycles, on a copy of examples/apps/analytics and on a generated project (bench/scale/generate.py).

    python3 bench/workspace/measure.py --modules 185 --out results/workspace/after.json
    python3 bench/workspace/measure.py --root OLD_CHECKOUT --modules 185 --out results/workspace/before.json

`--root` names the CAIRN checkout whose `bin/cairn mcp` is timed, so one script times two versions alike. Each
project is copied under results/workspace/ first and one server is started per project; every number is the wall
time of one tool call as the client sees it, and the server's peak resident memory is read from /proc before it
exits. Nothing here runs on a device.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = ROOT / "results" / "workspace"
sys.path.insert(0, str(HERE.parent / "scale"))
import generate

TIMEOUT = 900


class Server:
    """`ROOT/bin/cairn mcp` started in a project directory, called one tool at a time."""

    def __init__(self, root: Path, where: Path):
        self.proc = subprocess.Popen([sys.executable, str(root / "bin/cairn"), "mcp"], cwd=where,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)  # fmt: skip
        self.last = 0
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})

    def request(self, method: str, params: dict) -> dict:
        self.last += 1
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.last, "method": method, "params": params})
                              .encode() + b"\n")  # fmt: skip
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def tool(self, name: str, **arguments) -> tuple[float, dict, bool]:
        """The call's wall time in seconds, its record and whether it is an error."""
        started = time.perf_counter()
        result = self.request("tools/call", {"name": name, "arguments": arguments})["result"]
        took = time.perf_counter() - started
        return took, json.loads(result["content"][0]["text"]), result["isError"]

    def peak(self) -> int:
        """The server's peak resident memory, in KiB."""
        status = Path(f"/proc/{self.proc.pid}/status").read_text()
        return int(next(line.split()[1] for line in status.splitlines() if line.startswith("VmHWM:")))

    def close(self) -> int:
        assert self.proc.stdin
        self.proc.stdin.close()
        return self.proc.wait(timeout=TIMEOUT)


def measure(root: Path, where: Path, symbol: str, body: str, checks: int, cycles: int) -> dict:
    """One server on `where`: `checks` checks of the unchanged project, two edit sessions opened on it, then
    `cycles` rounds of open, edit and check, each edit a body no earlier round wrote (`body` with the round's
    number)."""
    server = Server(root, where)
    try:
        row: dict = {"checks": [], "opens": [], "cycles": []}
        for _ in range(checks):
            took, record, failed = server.tool("check", path=".")
            assert not failed and record["status"] == "typed", record
            row["checks"].append({"seconds": took, "cached": record.get("cached")})
        for _ in range(2):
            took, packet, failed = server.tool("edit_open", path=".", symbol=symbol)
            assert not failed, packet
            row["opens"].append({"seconds": took})
        for k in range(cycles):
            opened, packet, failed = server.tool("edit_open", path=".", symbol=symbol)
            assert not failed, packet
            request = {**packet["draft_protocol"], "replacement": body.format(k + 1)}
            edited, answer, failed = server.tool("edit_request", request=request)
            assert not failed and answer.get("written"), answer
            checked, record, failed = server.tool("check", path=".")
            assert not failed and record["status"] == "typed", record
            row["cycles"].append({"open": opened, "edit": edited, "check": checked, "cached": record.get("cached")})
        row["peak_resident_kib"] = server.peak()
        return row
    finally:
        server.close()


def summary(row: dict) -> dict:
    """The numbers a reader compares: the first check, the median of the rest, and each cycle's parts."""
    rest = sorted(c["seconds"] for c in row["checks"][1:])
    return {
        "first_check": row["checks"][0]["seconds"],
        "later_check_median": rest[len(rest) // 2] if rest else None,
        "first_open": row["opens"][0]["seconds"],
        "second_open": row["opens"][1]["seconds"],
        "cycle_median": sorted(c["open"] + c["edit"] + c["check"] for c in row["cycles"])[len(row["cycles"]) // 2],
        "check_after_edit_median": sorted(c["check"] for c in row["cycles"])[len(row["cycles"]) // 2],
        "peak_resident_kib": row["peak_resident_kib"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=ROOT, help="the CAIRN checkout whose cairn mcp is timed")
    ap.add_argument("--modules", type=int, default=185, help="the generated project's size")
    ap.add_argument("--checks", type=int, default=10)
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--only", choices=["analytics", "generated"], help="time one project")
    ap.add_argument("--out", type=Path, default=RESULTS / "measure.json")
    ap.add_argument("--commit", help="the commit --root holds, when it is not a git checkout")
    a = ap.parse_args()
    root = a.root.resolve()
    work = RESULTS / root.name
    shutil.rmtree(work, ignore_errors=True)
    projects: dict[str, tuple[Path, str, str]] = {}  # where, the function edited, its body in round {}
    if a.only != "generated":
        shutil.copytree(ROOT / "examples/apps/analytics", work / "analytics")
        projects["analytics"] = (work / "analytics", "analytics.cols.chain", "= mul_wrap(seed ^ value, {});")
    if a.only != "analytics":
        k = a.modules
        assert generate.write(work / f"p{k}", k)["modules"] == k
        fold = "{{ let mut acc:u64 = 0; for i in 0..n {{ acc = add_wrap(acc, xs[i] ^ {}); }} return acc; }}"
        projects[f"generated_{k}"] = (work / f"p{k}", f"big.m{k}.fold_{k}", fold)
    record: dict = {"root": str(root), "machine": platform.platform(), "python": platform.python_version(),
                    "projects": {}}  # fmt: skip
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    record["commit"] = a.commit or commit.stdout.strip() or None
    for name, (where, symbol, body) in projects.items():
        load = os.getloadavg()[0]  # other work on the machine slows every call alike; the record says how much ran
        row = measure(root, where, symbol, body, a.checks, a.cycles)
        record["projects"][name] = {"source_bytes": sum(p.stat().st_size for p in (where / "src").glob("*.cairn")),
                                    "symbol": symbol, "load_average": [load, os.getloadavg()[0]],
                                    "summary": summary(row), "calls": row}  # fmt: skip
        print(name, json.dumps(record["projects"][name]["summary"]))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(record, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

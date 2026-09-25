#!/usr/bin/env python3
"""An agent writes faster implementations of a sum of squares through `cairn mcp`, and `cairn tune` chooses one.

The agent is scripted: its submissions are the files in candidates/ and the tolerance written below, so every run
tells the same story. `cairn mcp` serves the implementation host over stdio as it does for any MCP client. The host
pins the reference, the tolerance, the test policy and the permitted inputs, validates each submission against the
reference, writes a validated one beside it in the project's files and keeps every result in the candidate history.
`cairn tune` then searches the implementations and times on this host only those the history holds a validation of.
Everything the host, the compiler, the validator and the timer say is computed on every run, on a copy of the project.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "src"), str(HERE.parent)]
from transcript import cairn, cpu, rel  # noqa: E402

from cairn.perf.feedback import lines_for_people  # noqa: E402
from cairn.perf.tune import lines as tune_lines  # noqa: E402

PROTOCOL = "cairn.implementation/1"
AT = "n=65536"  # 512 KiB of f64
SUBMISSIONS = [  # what the scripted agent sends, in order: a candidate file, what else the request carries, and why
    ("sumsq_by4_tail.cairn", {"tolerance": {"absolute": 0.0, "relative": 1e-6}},
     "sumsq_by4, four running sums, when n >= 4, carrying a relative tolerance of 1e-6"),
    ("sumsq_by4_tail.cairn", {}, "the same, without the tolerance"),
    ("sumsq_by4.cairn", {}, "sumsq_by4 when n % 4 == 0"),
    ("sumsq_blocks.cairn", {}, "sumsq_blocks[K], blocks of K terms each summed from zero, tune K in [4, 8, 16, 32]"),
]  # fmt: skip


class Server:
    """`cairn mcp` over its standard streams, one JSON-RPC request a line; what its builds print goes to `log`."""

    def __init__(self, root: Path, log: Path):
        self.log = log.open("w")
        self.process = subprocess.Popen([sys.executable, str(ROOT / "bin/cairn"), "mcp"], cwd=root, text=True,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)  # fmt: skip
        self.ident = 0
        hello = {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "demo", "version": "1"}}
        self.rpc("initialize", hello)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, message: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def rpc(self, method: str, params: dict) -> dict:
        self.ident += 1
        self.send({"jsonrpc": "2.0", "id": self.ident, "method": method, "params": params})
        assert self.process.stdout is not None
        answer = json.loads(self.process.stdout.readline())
        if "error" in answer:
            raise SystemExit(f"cairn mcp answered {method} with {answer['error']}")
        return answer["result"]

    def tool(self, name: str, arguments: dict) -> tuple[dict, bool, int]:
        """The tool's record, whether it is an error, and the bytes of text the agent reads."""
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        text = result["content"][0]["text"]
        return json.loads(text), result["isError"], len(text.encode())

    def close(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()
        self.process.wait(timeout=60)
        self.log.close()


def number(hex_float: str) -> float:
    return float.fromhex(hex_float)


def answered(name: str, answer: dict, project: Path) -> str:
    """The host's answer as the transcript shows it."""
    if answer.get("status") != "validated":
        head = f"refused {answer['code']}: {answer['message']}"
        failed = answer.get("failed")  # found by a finite case, or by Z3's counterexample replayed natively
        if not failed:
            return head
        found = answer["finite"] if answer["finite"].get("failed") else answer["smt"]["replay"]
        inputs = ", ".join(f"{k} = {v}" for k, v in failed["inputs"].items())
        kept = found.get("kept", "nowhere").replace(f"{project}/", "")
        return (f"{head}\n    case {answer['finite']['cases']} failed ({failed['found_as']}), and "
                f"{failed['shrunk_in']} runs shrank it to\n    {inputs}: {name} returns "
                f"{number(failed['reference']['return'])}, {answer['implementation']} returns "
                f"{number(failed['implementation']['return'])}\n    kept in {kept}")  # fmt: skip
    written = ", ".join(answer["written"])
    if "instances" not in answer:
        f = answer["finite"]
        return (f"validated: {f['cases']} cases, {f['kept_cases']} of them kept, {f['implementation_ran']} ran "
                f"{answer['implementation']}; written to {written}")  # fmt: skip
    rows = [f"    {n}: {r['finite']['cases']} cases, {r['finite']['implementation_ran']} ran it"
            for n, r in answer["instances"].items()]  # fmt: skip
    return "\n".join([f"validated every instance; written to {written}", *rows])


def search(project: Path, *extra: str) -> tuple[list[str], dict]:
    args = ["tune", rel(project), "--symbol", "sumsq", "--at", AT, *extra]
    done = cairn(*args, "--format", "json")
    if done.returncode:
        raise SystemExit(f"cairn {' '.join(args)} failed:\n{done.stdout[-3000:]}{done.stderr[-3000:]}")
    return args, json.loads(done.stdout)


def show(args: list[str], text: str) -> None:
    quoted = " ".join(f'"{a}"' if " " in a else a for a in args)
    print(f"\n$ cairn {quoted}\n{textwrap.indent(text, '  ')}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=ROOT / "results/demos/implement", help="Where the copy and record go.")
    a = p.parse_args()
    out = a.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    project = out / "sumsq"
    shutil.copytree(HERE, project, ignore=shutil.ignore_patterns("build", "candidates", "*.py", "*.md", "*.json"))
    policy = json.loads((HERE / "policy.json").read_text())
    print("An agent writes faster implementations of a sum of squares through cairn mcp; cairn tune chooses one.\n")
    record: dict = {"agent": "scripted: the submissions are demos/implement/candidates and the tolerance in run.py; "
                    "no model wrote them", "at": AT, "exchanges": []}  # fmt: skip
    server = Server(out, out / "mcp.log")
    try:
        packet, failed, size = server.tool("implementation_open", {"path": project.name, "reference": "sumsq",
                                                                   "policy": policy})  # fmt: skip
        if failed:
            raise SystemExit(f"implementation_open refused: {packet}")
        pinned = packet["pinned"]
        record["packet"] = {"bytes": size, "rule_cards": sorted(packet["rule_cards"]), "pinned": pinned}
        print(f"[sumsq] host -> agent: a packet of {size:,} bytes, from implementation_open")
        print(f"    the reference's declaration and row, and the rule cards {', '.join(packet['rule_cards'])}")
        print(f"    pinned by digest: a relative tolerance of {pinned['tolerance']['relative']!r} on the result, "
              f"{pinned['tests']['budget']} cases from seed {pinned['tests']['seed']}, extents up to "
              f"{pinned['domain']['largest_extent']}")  # fmt: skip
        for file, extra, what in SUBMISSIONS:
            request = {"protocol": PROTOCOL, "handle": packet["handle"], "kind": "submit",
                       "source": (HERE / "candidates" / file).read_text(), **extra}  # fmt: skip
            answer, failed, _ = server.tool("implementation_submit", {"request": request})
            record["exchanges"].append({"candidate": file, "extra": extra, "error": failed, "answer": answer})
            print(f"[sumsq] agent -> host: {what}")
            print("[sumsq] host: " + answered("sumsq", answer, project))
    finally:
        server.close()

    args, bare = search(project, "--measure", "6", "--no-history")
    show(args, tune_lines(bare))
    record["tune_without_history"] = bare
    before = os.getloadavg()
    args, found = search(project, "--measure", "6", "--budget-seconds", "120", "--budget-runs", "16", "--write")
    after = os.getloadavg()
    show(args, tune_lines(found))
    runs = found["budget"]["runs"]
    print(f"  {runs['started']} runs started of the {runs['allowed']} allowed; timed on this host ({cpu()}, "
          f"{os.cpu_count()} threads),\n  which other work shared: load average {before[0]:.1f} before, "
          f"{after[0]:.1f} after")  # fmt: skip
    written = [line for line in (project / "src/sumsq.cairn").read_text().splitlines() if line.startswith("plan ")]
    print(f"  --write put {' '.join(written)} into src/sumsq.cairn")
    record |= {"tune": found, "load_average": [before, after], "cpu": cpu(), "written": written}
    chosen = f"use {found['chosen']['use']}" if found["chosen"].get("use") else "none"
    args = ["tune", rel(project), "--symbol", "sumsq", "--at", AT, "--compare", "none", "--compare", chosen]
    done = cairn(*args, "--format", "json")
    record["compare"] = report = json.loads(done.stdout)
    show(args, lines_for_people(report))
    done = cairn("run", rel(project), "--format", "human", "--out", str(out / "build"))
    record["run"] = {"exit_code": done.returncode, "stdout": done.stdout}
    show(["run", rel(project)], done.stdout.rstrip("\n") + done.stderr)

    (out / "record.json").write_text(json.dumps(record, indent=2).replace(str(out) + "/", "") + "\n")
    print(f"\nThe whole exchange, every answer and both searches are in {rel(out / 'record.json')}.")
    codes = [e["answer"].get("code", e["answer"].get("status")) for e in record["exchanges"]]
    validated = {r["plan"] for r in found["candidates"] if isinstance(r.get("validated"), dict)}
    ok = codes == ["E-TOLERANCE", "E-VALIDATION", "validated", "validated"] and done.returncode == 0
    ok &= bare["chosen"].get("use") is None and found["chosen"]["plan"] in validated
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

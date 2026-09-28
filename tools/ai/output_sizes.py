#!/usr/bin/env python3
"""What an agent reads back from `cairn` and `cairn mcp`: a fixed corpus of outputs, each counted and held to a budget.

An agent's tokens are almost all context reads, and every tool result is read again at each later request
(evidence/v1_1/friction), so an output's size is paid many times over. This runs the commands an agent runs the way
its shell runs them: piped, in the project's directory, stdout and stderr together. Each case is one command on the
1.1 evaluation's `histogram` task, its reference laid out as a subject's project was: with a test block, with a second
test block that fails, and with one and with three of the mistakes the subjects made. A case whose name ends `at a
terminal` sets `CAIRN_FORMAT=human`, as a person's terminal would. The replay case checks the 69 programs the 1.1
subjects checked (`friction.py`) and counts every record. The MCP cases drive `cairn mcp` over stdio: each tool's name,
description and input schema as `tools/list` hands them to a client, and the text of a typical result of each tool,
with the implementation session on examples/implementations. That session submits the example's repaired attempt
(`candidates/prefix_blocks.cairn`) as an agent would, and Z3 does not decide it within the validation's 14 s limit,
so the session runs beside the commands.

Each case must still say what its name promises (`SAYS`), or the corpus stops and names it: a case that turned into a
short error would pass its budget and read as a cut.

The project's directory is written as HOME in every output, so a count does not depend on the directory a project was
made in. Sizes are UTF-8 bytes, and also tokens by tiktoken's `o200k_base` when it and its cached vocabulary are
present (`/usr/bin/python3` here). Neither is Claude's tokenizer, and a smaller output is not evidence that a model
does better. `output_budgets.json` holds a byte budget for each case: its size when last measured, with clang 21 on
the path, plus 5% or 16 bytes (`budget`). `--check` exits 1 when an output is larger than its budget. clang 18 reports
fewer loops to `cairn explain` than clang 21 does.

`cairn explain` at 0af5987 also depends on where the compiler's tree is. clang names a file relative to the deepest
directory it shares with the directory it compiled in, and that `cairn explain` resolves the name against its own
directory, the project's. A tree that shares a directory with the temporary one, where the projects are, prints longer
paths, so `--compiler` refuses one.

    python3 tools/ai/output_sizes.py [--output FILE]    # the record
    python3 tools/ai/output_sizes.py --check            # and exit 1 unless every case is within its budget
    python3 tools/ai/output_sizes.py --budget           # rewrite output_budgets.json from this measurement
    python3 tools/ai/output_sizes.py --compiler DIR     # the same corpus through another tree's bin/cairn
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import reduce
from operator import itemgetter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "bench" / "ai"), str(Path(__file__).parent)]
import friction
from checking import CAIRN_TOML

from support import tokenizer

HOME = "/home/agent/task"  # where every output says the project is
BUDGETS = Path(__file__).with_name("output_budgets.json")
TASK = ROOT / "bench" / "ai" / "tasks" / "histogram"
EXAMPLE = "6 4\n16 17 32 4096 255 31\n"  # the input of the task's SPEC.md
ANSWER = "0 1\n1 3\n2 1\n15 1\n"  # and the histogram it asks for
TESTS = """
test four_values_in_three_bins {
  let mut values = Buf[u32](4);
  values[0] = 1; values[1] = 17; values[2] = 18; values[3] = 256;
  let mut partial = Buf[u64](2 * 256);
  count(values, 4, 2, partial);
  assert_eq(partial[0] + partial[256], 1);
  assert_eq(partial[1] + partial[257], 2);
}
"""
FAILING = '\ntest one_bin_too_many {\n  assert_eq(usize(shr(u32(256), 4) & 255), 17, "256 >> 4 is bin 16");\n}\n'
# Mistakes the 1.1 subjects made, each in a function of its own: an unannotated literal later used as a usize, a
# misspelled field and a misspelled name.
MISTAKES = [("let k:usize = 8;", "let k = 8;"), ("inp.bytes.len;", "inp.byts.len;"),
            ("(b + 1) * n / k;", "(b + 1) * n / kk;")]  # fmt: skip
# (name, surface, program, argv after `cairn`, standard input)
COMMANDS = [
    ("check, accepted", "check", "accepted", ["check", "."], ""),
    ("check, accepted, at a terminal", "check", "accepted", ["check", "."], ""),
    ("check, one refusal", "check", "one", ["check", "."], ""),
    ("check, one refusal, at a terminal", "check", "one", ["check", "."], ""),
    ("check, three refusals", "check", "three", ["check", "."], ""),
    ("check, three refusals, at a terminal", "check", "three", ["check", "."], ""),
    ("build", "build", "accepted", ["build", "."], ""),
    ("run", "build", "accepted", ["run", "."], EXAMPLE),
    ("run --sanitize address", "build", "accepted", ["run", ".", "--sanitize", "address"], EXAMPLE),
    ("test, passing", "build", "accepted", ["test", "."], ""),
    ("test, failing", "build", "failing", ["test", "."], ""),
    ("test, failing, at a terminal", "build", "failing", ["test", "."], ""),
    ("new", "other", "accepted", ["new", "demo"], ""),
    ("rules E-EFFECT-ORDER", "other", "accepted", ["rules", "E-EFFECT-ORDER"], ""),
    ("doc --std --module std.io", "other", "accepted", ["doc", "--std", "--module", "std.io"], ""),
    ("explain", "other", "accepted", ["explain", "."], ""),
    ("explain --symbol count", "other", "accepted", ["explain", ".", "--symbol", "count"], ""),
    ("state", "other", "accepted", ["state", "."], ""),
    ("inspect --symbol count", "other", "accepted", ["inspect", ".", "--symbol", "count"], ""),
]
REPLAY = "check, the 69 programs of the 1.1 replay"


def read(text: str) -> dict:
    """What an output holds, to read a case's outcome from: its record, the records of one that prints several, or its
    text as the one field `text`, at a terminal and for a page."""
    if not text.startswith("{"):
        return {"text": text}
    each = [json.loads(r) for r in re.split(r"\n(?=\{)", text.strip())]  # a record's own lines are indented
    return each[0] if len(each) == 1 else {"records": each}


def verdict(r: dict) -> str:
    """`typed`, or the codes a check refused with in order; at a terminal, read from each refusal's heading."""
    if "text" in r:
        return " ".join(re.findall(r"^error\[([\w-]+)\]", r["text"], re.M)) or r["text"].split(":")[0]
    return " ".join(d["code"] for d in (r, *r.get("further", ()))) if "code" in r else r["status"]


def explained(r: dict) -> tuple[str, list[str]]:
    """Whether clang's remarks were read, and which of the program's own functions an explanation covers."""
    return r["vectorization"]["status"], sorted(f for f in r["functions"] if not f.startswith("std."))


def blocks(r: dict) -> tuple[int, int]:
    """How many test blocks passed, and how many failed."""
    return r["blocks"]["passed"], r["blocks"]["failed"]


def verdicts(r: dict) -> str:
    """How many checks a replay printed, and how many of them are a verdict: typed, or refused with its codes."""
    return f"{len(r['records'])} checks, {sum(c['status'] in ('typed', 'rejected') for c in r['records'])} verdicts"


status, symbol = itemgetter("status"), itemgetter("symbol")
THREE = "E-FIELD E-UNBOUND E-TYPE-MISMATCH"  # the three mistakes, in the order a check reports them
OWN = ["count", "main", "next_token", "next_u64", "read_input"]  # the functions the reference declares
# What each case says besides its size, and what it must say: a case that turned into a short error, a refusal of the
# request itself or an empty run would pass its budget and read as a cut. A tool's definition is what the server lists.
SAYS: dict[str, tuple[Callable[[dict], Any], Any]] = {
    "check, accepted": (verdict, "typed"),
    "check, accepted, at a terminal": (verdict, "typed"),
    "check, one refusal": (verdict, "E-TYPE-MISMATCH"),
    "check, one refusal, at a terminal": (verdict, "E-TYPE-MISMATCH"),
    "check, three refusals": (verdict, THREE),
    "check, three refusals, at a terminal": (verdict, THREE),
    "build": (status, "native-built"),
    "run": (lambda r: (r["exit_code"], r["stdout"]), (0, ANSWER)),
    "run --sanitize address": (lambda r: (r["exit_code"], r["stdout"], r["sanitizer"]), (0, ANSWER, "address")),
    "test, passing": (blocks, (1, 0)),
    "test, failing": (blocks, (1, 1)),
    "test, failing, at a terminal": (lambda r: r["text"].split("\n")[0], "tests-not-passed: 1 of 2 tests failed"),
    "new": (status, "created"),
    "rules E-EFFECT-ORDER": (lambda r: [card["name"] for card in r["cards"]], ["calls"]),
    "doc --std --module std.io": (lambda r: re.findall(r"^# (\S+)", r["text"], re.M), ["std.io"]),
    "explain": (explained, ("observed", OWN)),
    "explain --symbol count": (explained, ("observed", ["count"])),
    "state": (status, "typed"),
    "inspect --symbol count": (symbol, "count"),
    REPLAY: (verdicts, "69 checks, 69 verdicts"),
    "mcp check, accepted": (verdict, "typed"),
    "mcp check, three refusals": (verdict, THREE),
    "mcp edit_open": (symbol, "next_token"),
    "mcp edit_request, refused": (verdict, "E-FIELD"),
    "mcp edit_request, admitted": (verdict, "typed"),
    "mcp plan_open": (symbol, "count"),
    "mcp plan_reply": (status, "admitted"),
    "mcp implementation_open": (lambda r: r["reference"]["symbol"], "prefix"),
    "mcp implementation_submit": (status, "validated"),
    "mcp state": (status, "typed"),
    "mcp state, again": (lambda r: r["protocol"], "cairn.state-delta/1"),
}


def programs() -> dict[str, str]:
    """The histogram task's reference with test blocks and then with a failing one, and with one and three mistakes."""
    reference = (TASK / "reference.cairn").read_text()
    assert all(reference.count(old) == 1 for old, _ in MISTAKES), "the reference no longer holds each mistake's text"
    one, three = (reduce(lambda text, mistake: text.replace(*mistake), MISTAKES[:k], reference) for k in (1, 3))
    return {"accepted": reference + TESTS, "failing": reference + TESTS + FAILING, "one": one, "three": three}


def project(where: Path, source: str) -> Path:
    """A project as the evaluation's sandbox held one: `cairn.toml` and `src/main.cairn`."""
    (where / "src").mkdir(parents=True, exist_ok=True)
    (where / "cairn.toml").write_text(CAIRN_TOML)
    (where / "src" / "main.cairn").write_text(source)
    return where


def environment(human: bool = False) -> dict[str, str]:
    """This process's environment with no device reachable, and at a terminal CAIRN_FORMAT=human."""
    kept = {k: v for k, v in os.environ.items() if k not in {"CAIRN_FORMAT", "NO_COLOR"}}
    return {**kept, "CUDA_VISIBLE_DEVICES": "", **({"CAIRN_FORMAT": "human"} if human else {})}


def shell(cairn: Path, argv: list[str], where: Path, stdin: str = "", human: bool = False) -> str:
    """What an agent's shell shows of `cairn ARGV` run in `where`: stdout, then stderr, with `where` said as HOME."""
    done = subprocess.run([sys.executable, str(cairn), *argv], cwd=where, input=stdin, text=True,
                          capture_output=True, timeout=600, env=environment(human))  # fmt: skip
    return (done.stdout + done.stderr).replace(str(where), HOME)


def command(cairn: Path, case: tuple, sources: dict[str, str]) -> str:
    name, _, program, argv, stdin = case
    with tempfile.TemporaryDirectory(prefix="cairn-outputs-") as tmp:
        return shell(cairn, argv, project(Path(tmp), sources[program]), stdin, name.endswith("at a terminal"))


def replay(cairn: Path) -> str:
    """Every record `cairn check` gives the 69 programs the 1.1 subjects checked, one after another."""
    sources = [src for s in friction.subjects() if s.language == "cairn"
               for _, src in friction.checked(s, friction.requests(s))]  # fmt: skip

    def checked(source: str) -> str:
        with tempfile.TemporaryDirectory(prefix="cairn-outputs-") as tmp:
            return shell(cairn, ["check", "."], project(Path(tmp), source))

    with ThreadPoolExecutor(4) as pool:
        return "".join(pool.map(checked, sources))


class Client:
    """`cairn mcp` started in one directory, answering one request at a time."""

    def __init__(self, cairn: Path, where: Path):
        self.where = where
        self.proc = subprocess.Popen([sys.executable, str(cairn), "mcp"], cwd=where, text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env=environment())  # fmt: skip
        self.sent = 0
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})

    def request(self, method: str, params: dict) -> dict:
        self.sent += 1
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self.sent, "method": method, "params": params}) + "\n"
        )
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())["result"]

    def tool(self, name: str, **arguments) -> tuple[str, dict]:
        """The text a model reads of one call, with the served directory said as HOME, and the record it holds."""
        text = self.request("tools/call", {"name": name, "arguments": arguments})["content"][0]["text"]
        return text.replace(str(self.where), HOME), json.loads(text)

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=60)


def mcp(cairn: Path, sources: dict[str, str]) -> dict[str, str]:
    """Each tool's definition, and a typical result of each tool: a check accepted and refused, an edit session with a
    refused and an admitted body, a plan session, an implementation session, and the state asked for twice. Each
    request is the one its packet says to send."""
    out: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="cairn-outputs-") as tmp:
        project(Path(tmp) / "histogram", sources["accepted"])
        shutil.copytree(ROOT / "examples" / "implementations", Path(tmp) / "implementations")
        client = Client(cairn, Path(tmp))
        try:
            for tool in client.request("tools/list", {})["tools"]:
                shown = {k: tool[k] for k in ("name", "description", "inputSchema")}
                out[f"mcp tools/list {tool['name']}"] = json.dumps(shown, separators=(",", ":"))
            out["mcp check, accepted"], _ = client.tool("check", path="histogram")
            out["mcp check, three refusals"], _ = client.tool("check", source=sources["three"])
            out["mcp edit_open"], packet = client.tool("edit_open", path="histogram", symbol="next_token")
            body = sources["accepted"].split("-> Option[Span] ")[1].split("\n}\n")[0] + "\n}"
            wrong = {**packet["draft_protocol"], "replacement": body.replace("inp.bytes.len", "inp.byts.len")}
            out["mcp edit_request, refused"], _ = client.tool("edit_request", request=wrong)
            admitted = {**packet["draft_protocol"], "replacement": body}
            out["mcp edit_request, admitted"], _ = client.tool("edit_request", request=admitted)
            out["mcp plan_open"], plan = client.tool("plan_open", path="histogram", symbol="count")
            out["mcp plan_reply"], _ = client.tool("plan_reply", reply={**plan["reply"], "items": {"grain": 1}})
            opened = client.tool("implementation_open", path="implementations", reference="prefix")
            out["mcp implementation_open"], packet = opened
            candidate = (ROOT / "examples" / "implementations" / "candidates" / "prefix_blocks.cairn").read_text()
            submitted = {**packet["reply"], "source": candidate}
            out["mcp implementation_submit"], _ = client.tool("implementation_submit", request=submitted)
            out["mcp state"], _ = client.tool("state", path="histogram")
            out["mcp state, again"], _ = client.tool("state", path="histogram")
        finally:
            client.close()
    return out


def outputs(cairn: Path) -> dict[str, tuple[str, str]]:
    """Every case of the corpus: its surface and the text an agent reads. It stops, naming each case, when one does not
    say what `SAYS` holds it to."""
    sources = programs()
    with ThreadPoolExecutor(5) as pool:  # the MCP session, which waits on Z3, beside four commands at a time
        served = pool.submit(mcp, cairn, sources)
        printed = list(pool.map(lambda case: command(cairn, case, sources), COMMANDS))
        cases = {case[0]: (case[1], text) for case, text in zip(COMMANDS, printed, strict=True)}
        cases[REPLAY] = ("check", replay(cairn))
        cases |= {name: ("mcp", text) for name, text in served.result().items()}
    if wrong := unexpected(cases):
        raise SystemExit("A case does not say what it is measured for:\n" + "\n".join(wrong))
    return cases


def unexpected(cases: dict[str, tuple[str, str]]) -> list[str]:
    """A line for each case that does not say what `SAYS` holds it to, and for each held to nothing."""
    said = [f"{name}: measured, but held to nothing in SAYS" for name in cases
            if name not in SAYS and not name.startswith("mcp tools/list ")]  # fmt: skip
    said += [f"{name}: in SAYS, but not measured" for name in SAYS if name not in cases]
    for name, (reader, wanted) in SAYS.items():
        if name not in cases:
            continue
        try:
            got = reader(read(cases[name][1]))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):  # not the record it was
            got = cases[name][1][:300]
        if got != wanted:
            said.append(f"{name}: says {got!r}, not {wanted!r}")
    return said


def measure(cairn: Path) -> dict:
    """Each case's size, and the sums by surface and in all."""
    counted = tokenizer()
    units = ["bytes", *(["tokens"] if counted else [])]
    cases = {}
    for name, (surface, text) in outputs(cairn).items():
        cases[name] = {
            "surface": surface,
            "bytes": len(text.encode()),
            **({"tokens": counted[1](text)} if counted else {}),
        }
    surfaces = {s: {u: sum(c[u] for c in cases.values() if c["surface"] == s) for u in units}
                for s in dict.fromkeys(c["surface"] for c in cases.values())}  # fmt: skip
    total = {u: sum(s[u] for s in surfaces.values()) for u in units}
    return {"tokenizer": counted[0] if counted else None, "total": total, "surfaces": surfaces, "cases": cases}


def budget(size: int) -> int:
    """A case's budget: the size last measured and five percent, or 16 bytes when that is more."""
    return size + max(size // 20, 16)


def over(record: dict, budgets: dict[str, int]) -> list[str]:
    """A line for each case larger than its budget, or with none."""
    said = []
    for name, case in record["cases"].items():
        if name not in budgets:
            said.append(f"{name}: no budget; measure with --budget and commit {BUDGETS.name}")
        elif case["bytes"] > budgets[name]:
            said.append(f"{name}: {case['bytes']} bytes, over its budget of {budgets[name]}")
    return said


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="also write the record here")
    parser.add_argument("--check", action="store_true", help="exit 1 unless every output is within its budget")
    parser.add_argument("--budget", action="store_true", help=f"rewrite {BUDGETS.name} from this measurement")
    parser.add_argument("--compiler", type=Path, help="a tree holding bin/cairn to measure, such as `git archive`'s")
    a = parser.parse_args()
    tree, temporary = (a.compiler or ROOT).resolve(), Path(tempfile.gettempdir()).resolve()
    shared = Path(os.path.commonpath([tree, temporary]))
    if a.compiler and shared != Path(shared.anchor):
        parser.error(f"{tree} shares {shared} with the temporary directory, where the projects are, so `cairn explain` "
                     "at 0af5987 prints longer paths through it; unpack the tree elsewhere")  # fmt: skip
    record = measure(tree / "bin" / "cairn")
    text = json.dumps(record, indent=1) + "\n"
    if a.output:
        a.output.write_text(text, encoding="utf-8")
    if a.budget:
        budgets = {name: budget(case["bytes"]) for name, case in record["cases"].items()}
        BUDGETS.write_text(json.dumps(budgets, indent=1) + "\n", encoding="utf-8")
    print(text, end="")
    if a.check and (said := over(record, json.loads(BUDGETS.read_text()))):
        print("\n".join(said), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

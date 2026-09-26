#!/usr/bin/env python3
"""What the 1.1 evaluation's CAIRN subjects read to learn each library fact, against what `cairn find` answers.

For every question the friction record names (evidence/v1_1/friction/README.md), this finds the documentation
lookups of the counted CAIRN subjects that were about it, by the pattern searched or the file read (`QUESTIONS`),
and counts the tokens of what each lookup returned, as the subject's transcript kept it. It then asks `cairn find`
the same question through the `find` tool of `cairn mcp`, and counts its answer as a client receives it, and as the
lines a terminal prints. A lookup can serve two questions (a whole `std/text.md` holds parsing and searching both),
and a lookup whose pattern names none of them is counted under none.

Tokens are tiktoken's `o200k_base` (`/usr/bin/python3` here), else UTF-8 bytes. This measures the size of an answer,
not what an agent does with it: no model ran, and nothing here says an agent does better.

    python3 tools/ai/find_sizes.py --output evidence/v1_2/discovery/sizes.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools"), str(ROOT / "tools" / "ai")]
import friction

from cairn.agent.find import lines
from cairn.agent.mcp.tools import Tools
from support import tokenizer

RECORDS = "struct Rec { key:u64; weight:i64; }\nfn main() -> i32 { return 0; }\n"
# (question, what a lookup about it searched or read, the queries `cairn find` answers it with)
QUESTIONS = [
    ("read standard input", r"stdin|read_all|read_line|std/io\.md|\bio\.read", [{"words": "read stdin"}]),
    ("parse an integer from text", r"parse|ParseError|trim|whitespace|std/text\.md",
     [{"words": "parse integer"}, {"takes": ["ro<u8>[n]"], "returns": "i64"}]),
    ("a Vec of records", r"Vec\[|vec\.|std/vec\.md|\bpush\b",
     [{"words": "vec push"}, {"takes": ["Vec[Rec]"], "source": RECORDS}]),
    ("a Buf of records", r"\bBuf\b|buffer|lends", [{"takes": ["usize"], "returns": "Buf[Rec]", "source": RECORDS}]),
    ("tasks and groups", r"spawn|Group|collect\(|concurrency\.md", [{"words": "spawn task group"}]),
    ("the limits of i64", r"MAX|MIN\b|min_value|max_value|9223372036854775|i64::|minimum of a signed|roadmap\.md",
     [{"words": "i64 minimum"}]),
    ("signed overflow", r"wrap|overflow", [{"words": "signed overflow"}]),
    ("search text", r"find_byte|text\.find|\bsearch\b|substring|starts_with|std/text\.md",
     [{"words": "find substring"}, {"takes": ["ro<u8>[n]", "ro<u8>[n]"], "returns": "usize"}]),
    ("the library, whole", r"library\.md|std_api\.md|cairn doc --std", []),  # read up front; no one query replaces it
]  # fmt: skip


def looked_up(call: friction.Call) -> str | None:
    """What a documentation lookup searched or read, or None when the call is no lookup: a search's pattern and
    where it looked, a command, or a file read. The skill and its cards are the plugin's own and are left out."""
    kind, what = friction.kind(call)
    if call.name == "Grep":
        return f"{call.input.get('pattern', '')} {call.input.get('path', '')}"
    if kind != "docs" or what == "skill" or what.startswith("card "):
        return None
    return call.target()


def lookups(count) -> list[dict]:
    """Every documentation lookup of the counted CAIRN subjects, with the tokens it returned."""
    out = []
    for subject in friction.subjects():
        if subject.language != "cairn" or subject.set_aside:
            continue
        for request in friction.requests(subject):
            for call in request.calls:
                if (what := looked_up(call)) is not None:
                    out.append({"subject": subject.name, "arm": subject.arm, "request": request.index,
                                "looked_up": what, "tokens": count(call.result)})  # fmt: skip
    return out


def answered(tools: Tools, query: dict, count) -> dict:
    """The answer `cairn mcp` gives, as the text a client receives, and the lines a terminal prints."""
    record, failed = tools.call("find", dict(query))
    assert not failed, record
    text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    return {"query": query, "hits": len(record["hits"]), "more": record["more"], "first": record["hits"][:1],
            "mcp_tokens": count(text), "terminal_tokens": count(lines(record))}  # fmt: skip


def measure() -> dict:
    unit, count = tokenizer() or ("utf8_bytes", lambda text: len(text.encode("utf-8")))
    every, tools, rows = lookups(count), Tools(ROOT), []
    for question, pattern, queries in QUESTIONS:
        about = [x for x in every if re.search(pattern, x["looked_up"])]
        per_subject: dict[str, int] = {}
        for x in about:
            per_subject[x["subject"]] = per_subject.get(x["subject"], 0) + x["tokens"]
        rows.append({
            "question": question, "pattern": pattern, "lookups": len(about), "subjects": len(per_subject),
            "read_tokens": sum(x["tokens"] for x in about),
            "median_per_subject": statistics.median(per_subject.values()) if per_subject else 0,
            "largest": max(about, key=lambda x: x["tokens"], default=None),
            "answers": [answered(tools, q, count) for q in queries],
        })  # fmt: skip
    return {"unit": unit, "questions": rows, "lookups": every}


def table(measured: dict) -> str:
    out = ["| question | lookups (subjects) | read, all subjects | read, median subject | largest single read | "
           "cairn find | answer (mcp / terminal) |", "|---|---|---|---|---|---|---|"]  # fmt: skip
    for row in measured["questions"]:
        big = row["largest"]
        largest = f"{big['tokens']:,} ({short(big['looked_up'])})" if big else "none"
        if not row["answers"]:
            out.append(f"| {row['question']} | {row['lookups']} ({row['subjects']}) | {row['read_tokens']:,} | "
                       f"{row['median_per_subject']:,.0f} | {largest} | none | |")  # fmt: skip
        for i, a in enumerate(row["answers"]):
            asked = " ".join(
                [
                    a["query"].get("words", ""),
                    *(f"--takes '{t}'" for t in a["query"].get("takes", [])),
                    *([f"--returns '{a['query']['returns']}'"] if "returns" in a["query"] else []),
                ]
            ).strip()
            head = (f"| {row['question']} | {row['lookups']} ({row['subjects']}) | {row['read_tokens']:,} | "
                    f"{row['median_per_subject']:,.0f} | {largest} |") if i == 0 else "| | | | | |"  # fmt: skip
            out.append(f"{head} `cairn find {asked}` | {a['mcp_tokens']} / {a['terminal_tokens']} |")
    return "\n".join(out)


def short(looked: str) -> str:
    """A lookup as a table cell: the file it read or the command it ran, without the subject's own paths."""
    looked = re.sub(r"/home/\S+?/(docs|skills)/", r"\1/", looked).replace("|", "/")
    return looked if len(looked) <= 48 else looked[:45] + "..."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, help="Write the measurement as JSON here.")
    a = parser.parse_args()
    measured = measure()
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(measured, indent=1) + "\n", encoding="utf-8")
    print(f"tokens: {measured['unit']}\n\n{table(measured)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

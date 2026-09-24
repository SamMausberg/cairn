#!/usr/bin/env python3
"""The capability matrix: `docs/project/capability_matrix.json` checked, and written as the table in
`docs/verification.md` between its two markers.

    python3 tools/release/capability_matrix.py            # rewrite the table (`make docs` runs this)
    python3 tools/release/capability_matrix.py --check    # exit 1 when the table differs from the data

Each row is one feature and each column one claim: implemented, the targets it compiles for, whether it ran on a CPU
and on a GPU, which sanitizers watched it, and whether it was measured. A cell is a status, alone or followed by a
colon and what it rests on. The check holds the claims rule: a row names records that exist, a host feature has no
GPU claim, and a GPU run or a measurement stated as yes or partial names a record under `evidence/`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "docs/project/capability_matrix.json"
DOC = ROOT / "docs/verification.md"
SCHEMA = "cairn.capability-matrix/1"
CLAIMS = ("implemented", "compiles", "ran_on_cpu", "ran_on_gpu", "sanitizers", "measured")
FIELDS = {"feature", "side", "docs", "records", *CLAIMS}
CELL = re.compile(r"(yes|partial|no|unknown|n/a)(?:: ([^|\n]+))?")
EVIDENCED = ("ran_on_gpu", "measured")  # a yes here is an executed record, never a test that skips without a device
BEGIN = "<!-- generated from project/capability_matrix.json by tools/release/capability_matrix.py; edit the data -->"
END = "<!-- end of the generated capability matrix -->"
SIDES = {"host": "On the host", "device": "On a device"}


def status(cell: str) -> str:
    return cell.split(":", 1)[0]


def rows(data: dict | None = None) -> list[dict]:
    """The rows of the matrix, each held to the schema and the claims rule; ValueError naming the first that is not."""
    data = json.loads(DATA.read_text(encoding="utf-8")) if data is None else data
    if data.get("schema") != SCHEMA or set(data.get("columns", {})) != set(CLAIMS):
        raise ValueError(f"the matrix is {SCHEMA} with the columns {', '.join(CLAIMS)}")
    seen: set[str] = set()
    for row in data["rows"]:
        name = row.get("feature", "(a row without a feature)")
        if set(row) != FIELDS or name in seen or row["side"] not in SIDES:
            raise ValueError(f"{name}: a row has exactly {', '.join(sorted(FIELDS))}, a side of host or device, and a "
                             "feature no other row names")  # fmt: skip
        seen.add(name)
        for claim in CLAIMS:
            if not isinstance(row[claim], str) or not CELL.fullmatch(row[claim]):
                raise ValueError(f"{name}: {claim} is yes, partial, no, unknown or n/a, then optionally `: why`")
        if status(row["implemented"]) not in {"yes", "partial", "no"}:
            raise ValueError(f"{name}: implemented is yes, partial or no")
        if row["side"] == "host" and status(row["ran_on_gpu"]) != "n/a":
            raise ValueError(f"{name}: a host feature makes no GPU claim; its device side is a row of its own")
        records = row["records"]
        if not records or len(set(records)) != len(records):
            raise ValueError(f"{name}: a row names the tests and records it rests on, each once")
        if missing := [r for r in records if not (ROOT / r).exists()]:
            raise ValueError(f"{name}: no such record: {', '.join(missing)}")
        for claim in EVIDENCED:
            if status(row[claim]) in {"yes", "partial"} and not any(r.startswith("evidence/") for r in records):
                raise ValueError(f"{name}: {claim} is {status(row[claim])} only with an executed record under "
                                 "evidence/")  # fmt: skip
        page = row["docs"].split("#", 1)[0]
        if not (ROOT / "docs" / page).is_file():
            raise ValueError(f"{name}: docs names {page}, which is not a file under docs/")
    return data["rows"]


def link(record: str) -> str:
    shown = record.removeprefix("evidence/").removesuffix("/README.md")
    return f"[{shown if record.startswith('evidence/') else Path(record).name}](../{record})"


def table(data: dict | None = None) -> str:
    """The generated part of docs/verification.md, markers included."""
    data = json.loads(DATA.read_text(encoding="utf-8")) if data is None else data
    checked = rows(data)
    head = ["Feature", *data["columns"].values(), "Records"]
    out = [BEGIN]
    for side, heading in SIDES.items():
        out += ["", f"### {heading}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        for row in (r for r in checked if r["side"] == side):
            cells = [
                f"[{row['feature']}]({row['docs']})",
                *(row[c] for c in CLAIMS),
                ", ".join(map(link, row["records"])),
            ]
            out.append("| " + " | ".join(cells) + " |")
    return "\n".join([*out, "", END])


def written(doc: str, generated: str) -> str:
    """`doc` with the text between its markers replaced by `generated`."""
    start, end = doc.find(BEGIN), doc.find(END)
    if start < 0 or end < start:
        raise ValueError(f"{DOC.name} has no pair of capability matrix markers")
    return doc[:start] + generated + doc[end + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when docs/verification.md is stale")
    a = parser.parse_args(argv)
    try:
        doc = DOC.read_text(encoding="utf-8")
        fresh = written(doc, table())
    except ValueError as error:
        print(f"capability matrix: {error}", file=sys.stderr)
        return 1
    if a.check:
        if fresh != doc:
            print("docs/verification.md is stale: run python3 tools/release/capability_matrix.py", file=sys.stderr)
            return 1
        return 0
    if fresh != doc:
        DOC.write_text(fresh, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

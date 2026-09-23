#!/usr/bin/env python3
"""Turn one results/bench_suite/suite.json into readable tables. It reads; it never measures.

Every rule it applies is fixed in bench/suite/PREREGISTRATION.md: which arms may be divided into
each other, what counts as a win, and what a table is allowed to say. Losses print in the same
tables as wins, an unavailable library prints as unavailable, and a pair whose safety boundaries are
unequal prints its own column and is never turned into a ratio.

  python bench/suite/report.py                       # the default results directory
  python bench/suite/report.py --input results/bench_suite/suite.json --kernel stencil_1d
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "results/bench_suite/suite.json"

# The preregistered threshold. A ratio above one favours CAIRN, as it does in bench/host/paired.py.
WIN_RATIO = 1.25
# The arms the 1.4 addendum adds, which may also be divided into a baseline they check at least as much as.
ADDENDUM_ARMS = {"cairn_pool", "cairn_blocks"}


def column(row: dict) -> str:
    guard = row.get("boundary") or ("guarded" if row["guarded"] else "unguarded")  # 1.3 records have no boundary
    return f"{row['arm']}/{guard}/{row['grain_row']}"


def table(rows: list[list[str]], head: list[str]) -> str:
    widths = [max(len(str(r[i])) for r in [head, *rows]) for i in range(len(head))]
    line = "  ".join(h.ljust(w) for h, w in zip(head, widths, strict=True))
    rule = "  ".join("-" * w for w in widths)
    body = ["  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)) for r in rows]
    return "\n".join([line, rule, *body])


def verdict(ratios: dict[str, dict[int, float]], sizes: list[int], compilers: list[str]) -> str:
    """The preregistered rule. A win is a ratio of at least WIN_RATIO that holds from some size
    upward under every compiler that ran; a loss is at most its reciprocal at the largest size under
    every one of them; everything else is level. A ratio that reaches the threshold once and falls
    back is not a win, and a column missing under one compiler decides nothing at all."""
    if set(ratios) != set(compilers) or any(len(seen) != len(sizes) for seen in ratios.values()):
        return "incomplete"
    crossover = None
    for at in reversed(sizes):
        if all(seen[at] >= WIN_RATIO for seen in ratios.values()):
            crossover = at
        else:
            break
    if crossover is not None:
        return f"win from n={crossover}"
    if all(seen[sizes[-1]] <= 1 / WIN_RATIO for seen in ratios.values()):
        return "loss"
    return "level"


def kernel_report(name: str, record: dict, out: list[str]) -> None:
    safety = record["safety"][name]
    rows = record["runs"].get(name, [])
    out.append("")
    out.append(f"== {name} ==")
    out.append(f"claim: {safety['claim']}; in because {safety['why_this_kernel_is_in']}")
    refusal = safety.get("preregistered_rejection")
    if refusal:
        held = "as preregistered" if refusal["refused_as_preregistered"] else "NOT as preregistered"
        out.append(f"refusal: {refusal['expected_code']} {held}; the compiler printed {refusal['actual_code']}")

    out.append("")
    out.append("safety boundaries")
    head = ["arm", "entry", "element", "arithmetic", "conversion", "equal to cairn", "boundary in the object"]
    four = ("entry", "element", "arithmetic", "conversion")
    lines = [["cairn (receipt)", *[safety["cairn_boundaries"][k] for k in four], "-", "-"]]
    for arm, seen in sorted(safety["arms"].items()):
        lines.append(
            [
                arm,
                *[seen["boundaries"][k] for k in four],
                "yes" if seen["equal_to_cairn"] else "no",
                seen.get("boundary_in_the_object", "unknown"),
            ]
        )
    out.append(table(lines, head))

    timed = [r for r in rows if r.get("timings", {}).get("status") == "measured"]
    missing = [r for r in rows if r.get("timings", {}).get("status") != "measured"]
    if missing:
        out.append("")
        out.append("not measured")
        out.append(table([[r.get("arm", "?"), r.get("compiler", "?"), r.get("status") or r["timings"]["status"]] for r in missing], ["arm", "compiler", "why"]))  # fmt: skip
    if not timed:
        return

    # The kernels whose claim is a semantic difference report it here, apart from any time. Answering
    # no is the finding, not a failure, so it is never counted as one.
    folds = [r for r in timed if "folds_in_order" in r["independent_oracle"]]
    if folds:
        out.append("")
        out.append("did this arm compute the function the CAIRN source names, bit for bit")
        out.append(table([[column(r), r["compiler"], "yes" if r["independent_oracle"]["folds_in_order"] else "no"] for r in folds], ["column", "compiler", f"at n={folds[0]['independent_oracle']['n']}"]))  # fmt: skip

    sizes = [row["n"] for row in timed[0]["timings"]["sweep"]]
    compilers = sorted({r["compiler"] for r in timed})

    for cxx in compilers:
        out.append("")
        out.append(f"median milliseconds, {cxx}")
        columns = sorted({column(r) for r in timed if r["compiler"] == cxx})
        lines = []
        for k, n in enumerate(sizes):
            line = [n]
            for name_of in columns:
                found = next((r for r in timed if r["compiler"] == cxx and column(r) == name_of), None)
                line.append(f"{found['timings']['sweep'][k]['median_ms']:.6f}" if found else "-")
            lines.append(line)
        out.append(table(lines, ["n", *columns]))

    # One table per CAIRN arm, each under the equality rule. An arm the 1.4 addendum adds may
    # also be divided into a baseline it checks at least as much as in every category; such a
    # row says `cairn checks more` beside its verdict, and a win there holds with the baseline's checks added.
    by_arm = safety.get("cairn_boundaries_by_arm") or {"cairn": safety["cairn_boundaries"]}
    for mine_arm in sorted(by_arm, key=lambda a: (a != "cairn", a)):
        reference = {
            c: next((r for r in timed if r["compiler"] == c and r["arm"] == mine_arm), None) for c in compilers
        }
        if not any(reference.values()):
            continue
        out.append("")
        rule = "boundaries equal, or more on the cairn side" if mine_arm in ADDENDUM_ARMS else "equal boundaries only"
        out.append(f"ratio of baseline time to {mine_arm} time, above one favours {mine_arm}; {rule}")
        more = False
        lines = []
        for name_of in sorted({column(r) for r in timed if not r["arm"].startswith("cairn")}):
            seen: dict[str, dict[int, float]] = {}
            for cxx in compilers:
                base = next((r for r in timed if r["compiler"] == cxx and column(r) == name_of), None)
                mine = reference.get(cxx)
                arm = safety["arms"].get(f"{name_of.split('/')[0]}|{name_of.split('/')[1]}|{cxx}", {})
                equal = (
                    arm.get("boundaries") == by_arm[mine_arm]
                    if "boundaries" in arm and mine_arm != "cairn"
                    else arm.get("equal_to_cairn", False)
                )
                dominates = mine_arm in ADDENDUM_ARMS and all(
                    by_arm[mine_arm][k] >= v for k, v in arm.get("boundaries", {"entry": 1 << 62}).items()
                )
                more = more or (dominates and not equal)
                if base is None or mine is None or not (equal or dominates):
                    continue
                seen[cxx] = {
                    row["n"]: row["median_ms"] / mine["timings"]["sweep"][k]["median_ms"]
                    for k, row in enumerate(base["timings"]["sweep"])
                }
            if not seen:
                lines.append([name_of, *["boundaries:unequal" for _ in compilers], "excluded"])
                continue
            line = [name_of]
            for cxx in compilers:
                line.append(f"{seen[cxx][sizes[-1]]:.2f}" if cxx in seen else "-")
            line.append(verdict(seen, sizes, compilers) + ("; cairn checks more" if more else ""))
            lines.append(line)
            more = False
        out.append(table(lines, ["column", *compilers, "verdict"]))

    out.append("")
    out.append("what the safety boundary costs: unguarded time divided by guarded time")
    lines = []
    for cxx in compilers:
        for arm in sorted({r["arm"] for r in timed if not r["arm"].startswith("cairn")}):
            for row_name in sorted({r["grain_row"] for r in timed if r["arm"] == arm}):
                pair = {
                    r["guarded"]: r
                    for r in timed
                    if r["compiler"] == cxx and r["arm"] == arm and r["grain_row"] == row_name
                    and r.get("boundary") != "matched"
                }  # fmt: skip
                if set(pair) != {True, False}:
                    continue
                at = len(sizes) - 1
                cost = (
                    pair[False]["timings"]["sweep"][at]["median_ms"] / pair[True]["timings"]["sweep"][at]["median_ms"]
                )
                lines.append([cxx, arm, row_name, f"{cost:.3f}"])
    out.append(table(lines, ["compiler", "arm", "grain row", f"at n={sizes[-1]}"]) if lines else "nothing to pair")

    out.append("")
    out.append("back to back regions, microseconds per region")
    widths = [row["n"] for row in timed[0]["timings"]["back_to_back"]]
    lines = []
    for cxx in compilers:
        for name_of in sorted({column(r) for r in timed if r["compiler"] == cxx}):
            found = next(r for r in timed if r["compiler"] == cxx and column(r) == name_of)
            lines.append([cxx, name_of, *[f"{row['per_region_us']:.4f}" for row in found["timings"]["back_to_back"]]])
    out.append(table(lines, ["compiler", "column", *[f"n={w}" for w in widths]]))


def main() -> int:
    ask = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ask.add_argument("--input", type=Path, default=DEFAULT)
    ask.add_argument("--kernel", action="append", help="Report only these kernels.")
    args = ask.parse_args()
    record = json.loads(args.input.read_text())

    out = [f"{record['mode']} run of the preregistered suite, {record['preregistration']}"]
    where = record["environment"]
    out.append(f"host {where['host']}, profile {where['arch_profile']}, {where['lanes']} lanes, not pinned")
    out.append(f"compilers {', '.join(f'{k}: {v}' for k, v in where['compilers'].items())}")
    out.append(f"commit {where['commit']}")
    out.append(f"acceptance: {record['acceptance']['rule']}, threshold {record['acceptance']['win_ratio']}")
    out.append("")
    out.append("libraries")
    lines = [[cxx, name, found["status"], " ".join(found.get("extra_flags", [])) or found.get("reason", "")] for cxx, seen in record["libraries"].items() for name, found in seen.items()]  # fmt: skip
    out.append(table(lines, ["compiler", "library", "status", "link line or reason"]))

    for name in record["safety"]:
        if args.kernel and name not in args.kernel:
            continue
        kernel_report(name, record, out)
    out.append("")
    out.append(record["boundary"])
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

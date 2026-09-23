"""How a `cairn diff` reads: a few lines at a terminal, and a Markdown section for a pull request description.

Both render the `cairn.diff/1` record and add nothing to it. A function whose code is identical is counted and not
listed, because the count is the claim; every other function gets one line with its class, its evidence and the
compiler-established changes beside it.
"""

from __future__ import annotations

import json
from typing import Any

SHOWN = ["behavior-changed", "unknown", "signature-changed", "smt-equivalent", "renamed", "added", "removed"]
# identical-code and identical-source are counted, not listed


def value(x: Any) -> str:
    return json.dumps(x, separators=(", ", ": "))


def inputs(given: dict[str, Any]) -> str:
    return ", ".join(f"{k} = {value(v)}" for k, v in given.items()) or "no input"


def outcome(seen: dict[str, Any]) -> str:
    if not seen.get("defined"):
        return "aborts"
    written = "".join(f", leaves {k} = {value(v)}" for k, v in seen.get("written", {}).items())
    return f"returns {value(seen.get('return'))}" + written if "return" in seen else "returns" + written


def native(witness: dict[str, Any]) -> str:
    seen = witness.get("native")
    if not isinstance(seen, dict):
        return str(seen or "not replayed")
    agreed = [cxx for cxx, sides in seen.items() if all(r == "agrees" for r in sides.values())]
    other = {cxx: sides for cxx, sides in seen.items() if cxx not in agreed}
    said = f"{' and '.join(agreed)} agree" if agreed else ""
    return "; ".join([said, *(f"{cxx}: {s['before']}, {s['after']}" for cxx, s in other.items())]).strip("; ")


def evidence(entry: dict[str, Any]) -> str:
    c = entry["class"]
    through = " (its own code is identical; something it calls changed)" if entry.get("own_code") == "identical" else ""
    if c == "behavior-changed":
        w = entry["witness"]
        said = f"at {inputs(w['inputs'])}: before {outcome(w['before'])}, after {outcome(w['after'])}"
        return said + f" (native: {native(w)})" + through
    if c == "unknown" and "bounded" in entry:
        return f"equivalent wherever {entry['bounded']['where']}; beyond that: {entry.get('reason', '')}"
    if c == "renamed":
        return f"was {entry['from']}"
    if c in {"added", "removed"}:
        s = entry["signature"]
        return f"({', '.join(t for _, t in s['params'])}) -> {s['returns']}"
    if c == "smt-equivalent":
        return "Z3 found no input on which they differ" + through
    return entry.get("reason", "")


def changes(entry: dict[str, Any]) -> str:
    """The compiler-established differences, in a few words each."""
    d, out = entry.get("deltas", {}), []
    if "signature" in d:
        before, after = d["signature"]["before"], d["signature"]["after"]
        out.append(f"signature ({', '.join(t for _, t in before['params'])}) -> {before['returns']} became "
                   f"({', '.join(t for _, t in after['params'])}) -> {after['returns']}")  # fmt: skip
    if "effects" in d:
        out.append("effects " + " ".join([*("+" + e for e in d["effects"]["added"]),
                                          *("-" + e for e in d["effects"]["removed"])]))  # fmt: skip
    for key, said in (("syntactic_check_sites", "guards written"), ("discharged_check_sites", "guards discharged")):
        if key in d:
            out.append(f"{said} {sum((d[key]['before'] or {}).values())} -> {sum((d[key]['after'] or {}).values())}")
    for key, said in (("emitted_guards", "guards emitted"), ("heap_allocations", "heap allocations"),
                      ("implicit_synchronization", "synchronization")):  # fmt: skip
        if key in d:
            out.append(f"{said} {d[key]['before']} -> {d[key]['after']}")
    if "local_storage" in d:
        out.append("local storage changed")
    if "census" in d:
        out += [f"{k} {d['census']['before'][k]} -> {d['census']['after'][k]}" for k in ("spawn", "unsafe")
                if d["census"]["before"][k] != d["census"]["after"][k]]  # fmt: skip
    if entry.get("own_code", "").startswith("changed only"):
        out.append("own code differs only in its guards")
    return "; ".join(out)


def ordered(record: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rank = {c: i for i, c in enumerate(SHOWN)}
    shown = [(n, e) for n, e in record["functions"].items() if e["class"] in rank]
    return sorted(shown, key=lambda x: (rank[x[1]["class"]], x[0]))


def headline(record: dict[str, Any]) -> str:
    return ", ".join(f"{k} {c}" for c, k in record["summary"].items()) or "no functions"


def lines(record: dict[str, Any], old: str, new: str) -> str:
    out = [f"{old} -> {new}: {headline(record)}"]
    width = max((len(n) for n, _ in ordered(record)), default=0)
    for name, entry in ordered(record):
        tail = "; ".join(x for x in (evidence(entry), changes(entry)) if x)
        out.append(f"  {entry['class']:<17} {name:<{width}}  {tail}".rstrip())
    for kind in ("types", "tests"):
        if record[kind]:
            out.append(f"{kind}: " + ", ".join(f"{n} {how}" for n, how in record[kind].items()))
    if said := forecast(record):
        out.append(said)
    verdict = record["semver"]
    out.append(f"semver: {level(verdict)}" + (f": {'; '.join(verdict['reasons'])}" if verdict["reasons"] else ""))
    if verdict.get("unproven"):
        out.append(f"  {unproven(verdict)}: {', '.join(verdict['unproven'])}")
    return "\n".join(out)


def level(verdict: dict[str, Any]) -> str:
    return verdict["level"] + (f" (at least {verdict['at_least']})" if "at_least" in verdict else "")


def unproven(verdict: dict[str, Any]) -> str:
    if verdict["level"] == "unknown":
        return "unknown, because these public functions are unproven"
    return "these public functions are unproven, and cannot raise it further"


def forecast(record: dict[str, Any]) -> str:
    """The predicted cost change at the largest size priced, per function; empty when nothing was priced."""
    priced = record.get("predicted", {}).get("functions", {})
    ratios = [f"{name} x{rows[-1]['ratio']}" for name, rows in sorted(priced.items()) if rows[-1].get("ratio")]
    return f"predicted, not measured ({record['predicted']['profile']}): {', '.join(ratios)}" if ratios else ""


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def markdown(record: dict[str, Any], old: str, new: str) -> str:
    """A section to paste into a pull request description. It is written to a file and never posted anywhere."""
    out = [f"## What changed from `{old}` to `{new}`", "",
           f"Compared by `cairn diff` ({record['compiler']}): {headline(record)}. A function whose code is identical, "
           "with everything it calls, is counted and not listed.", ""]  # fmt: skip
    if rows := ordered(record):
        out += ["| function | class | evidence | compiler-established changes |", "|---|---|---|---|"]
        out += [f"| `{n}` | {e['class']} | {cell(evidence(e))} | {cell(changes(e))} |" for n, e in rows]
        out.append("")
    for kind in ("types", "tests"):
        if record[kind]:
            out += [f"{kind.capitalize()}: " + ", ".join(f"`{n}` {how}" for n, how in record[kind].items()) + ".", ""]
    if said := forecast(record):
        out += [said[0].upper() + said[1:] + ".", ""]
    verdict = record["semver"]
    reasons = "; ".join(verdict["reasons"])
    out.append(f"Semantic version: **{level(verdict)}**" + (f", because {reasons}." if reasons else "."))
    if verdict.get("unproven"):
        out += ["", f"It is {unproven(verdict)}: {', '.join(f'`{n}`' for n in verdict['unproven'])}."]
    return "\n".join(out) + "\n"

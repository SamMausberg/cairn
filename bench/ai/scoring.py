"""The audit of every transcript, and the tables PREREGISTRATION.md says to report.

The audit lists every path a subject named outside its sandbox and every command that could reach the network. It
flags; a person decides. A flag is cleared in `audit_decisions.json` with the reason, and a subject found to have read
outside its sandbox or used the network is contaminated: listed with its numbers and left out of every count.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from pathlib import Path

# Paths a subject may name outside its sandbox: the compilers, their headers and libraries, and scratch devices.
TOOLCHAIN = ("/usr/", "/opt/llvm", "/bin/", "/lib/", "/etc/alternatives", "/dev/null", "/dev/stdin", "/dev/stdout",
             "/dev/stderr", "/proc/cpuinfo", "/proc/self", "/sys/devices/system/cpu")  # fmt: skip
NETWORK = re.compile(
    r"\b(curl|wget|ssh|scp|rsync|nc|ncat|telnet|pip3?\s+install|cargo\s+(add|install|fetch|update|search)|git\s+(clone|fetch|pull)|npm|apt)\b"
)
PATH = re.compile(r"(?<![\w.$/-])(~[\w/.-]*|/[\w.+-][\w/.+-]*)")
# A run of slashes is a floor division or a comment unless what follows names a directory at the root: `//etc/x` is
# the path /etc/x, and `(n+1)//2` and `//note` are not paths.
DOUBLED = re.compile(r"(?<![\w.$/-])/{2,}([\w.+-][\w/.+-]*)")


def paths(text: str) -> list[str]:
    """Every path a shell command names, a doubled leading slash read as one."""
    doubled = [f"/{p}" for p in DOUBLED.findall(text) if Path("/", p.split("/")[0]).is_dir()]
    return PATH.findall(text) + doubled


COMPILE = re.compile(
    r"(?<![\w-])(cairn\s+(check|build|run|test)|clang\+\+|g\+\+|cargo\s+(build|run|check|test)|rustc)(?![\w+])"
)
FAILED = re.compile(r"error(\[E-?[\w-]+\])?:|error\[|panicked at|Sanitizer|runtime error|Aborted|core dumped")
REJECTED = re.compile(r'"status":\s*"(rejected|unknown)"')


def messages(transcript: Path):
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def tool_calls(transcript: Path) -> list[dict]:
    """Each tool call with its input and the text of its result, in order."""
    calls: dict[str, dict] = {}
    order = []
    for m in messages(transcript):
        message = m.get("message") if isinstance(m, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls[block["id"]] = {
                    "name": block["name"],
                    "input": block.get("input", {}),
                    "result": "",
                    "error": False,
                }
                order.append(block["id"])
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                body = block.get("content")
                text = (
                    body
                    if isinstance(body, str)
                    else " ".join(b.get("text", "") for b in body or [] if isinstance(b, dict))
                )
                calls[block["tool_use_id"]].update(result=text, error=bool(block.get("is_error")))
    return [calls[i] for i in order]


def audit(transcript: Path, sandbox: str, root: str, plugin: str | None = None) -> dict:
    """Every path named outside the sandbox and every network-looking command, with counts of what the subject ran.

    `root` holds every sandbox, every plugin arm's copy of the plugin and the installed CAIRN toolchain: a path under
    it is flagged unless it is the subject's own sandbox, its own copy of the plugin or the toolchain, so one subject
    reaching another's work is caught. A write into the plugin is flagged too. Scratch files a subject makes elsewhere
    under /tmp are its own and are not flagged. Paths are read with `..` resolved, so the skill's own
    `${CLAUDE_SKILL_DIR}/../../docs/` names the plugin's documentation."""
    tools = str(Path(root) / "toolchain")
    # The platform keeps a long tool output of the session in a file named after the sandbox and hands the subject
    # its path, so reading it back is the subject reading its own output.
    spilled = str(Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", sandbox)) + "/"
    home = (str(Path.home() / ".cargo"), str(Path.home() / ".rustup"), spilled)
    own = (sandbox, tools, *([plugin] if plugin else []), *home, *TOOLCHAIN)

    def allowed(path: str) -> bool:
        path = os.path.normpath(path) + ("/" if path.endswith("/") else "")
        if path.startswith(own):
            return True
        return path.startswith("/tmp/") and not path.startswith(root)

    flags, counts = [], {}
    compiles = failures = 0
    for call in tool_calls(transcript):
        name, given = call["name"], call["input"]
        counts[name] = counts.get(name, 0) + 1
        if name == "Bash":
            text = given.get("command", "")
            if NETWORK.search(text):
                flags.append({"tool": name, "why": "network", "what": text[:300]})
            for path in paths(text):
                if not allowed(path) and path not in ("/", "/tmp"):
                    flags.append({"tool": name, "why": "path", "what": path, "command": text[:300]})
            relative = PATH.sub(" ", text)  # a `..` inside an absolute path is judged with the path
            if ".." in re.findall(r"(?:^|[\s/'\"])(\.\.)(?:/|\s|$)", relative):
                flags.append({"tool": name, "why": "parent", "what": text[:300]})
            if COMPILE.search(text):
                compiles += 1
                if call["error"] or FAILED.search(call["result"]):
                    failures += 1
        else:
            for key in ("file_path", "path", "notebook_path"):
                target = given.get(key)
                if target and str(target).startswith("/") and not allowed(str(target)):
                    flags.append({"tool": name, "why": "path", "what": target})
                if target and plugin and name in ("Write", "Edit") and os.path.normpath(str(target)).startswith(plugin):
                    flags.append({"tool": name, "why": "plugin-write", "what": target})
            if name.endswith("__check"):  # the plugin's MCP check is a compile run as `cairn check` is
                compiles += 1
                if call["error"] or REJECTED.search(call["result"]):
                    failures += 1
    return {"tool_calls": counts, "compile_runs": compiles, "compile_runs_failed": failures, "flags": flags}


def tokens(result: dict | None) -> dict:
    """Input (fresh, cache writes, cache reads) and output tokens as the platform counted them, over every model."""
    if not result or not result.get("modelUsage"):
        return {}
    out = {"input": 0, "cache_creation": 0, "cache_read": 0, "output": 0, "cost_usd": 0.0}
    for usage in result["modelUsage"].values():
        out["input"] += usage.get("inputTokens", 0)
        out["cache_creation"] += usage.get("cacheCreationInputTokens", 0)
        out["cache_read"] += usage.get("cacheReadInputTokens", 0)
        out["output"] += usage.get("outputTokens", 0)
        out["cost_usd"] += usage.get("costUSD", 0.0)
    out["total"] = out["input"] + out["cache_creation"] + out["cache_read"] + out["output"]
    out["cost_usd"] = round(out["cost_usd"], 4)
    return out


def row(record: dict) -> dict:
    result = record.get("result") or {}
    solved = bool(record.get("verdict", {}).get("passed")) and not record.get("contaminated")
    failure = "contaminated" if record.get("contaminated") else record.get("verdict", {}).get("reason")
    return {
        "task": record["task"],
        "arm": record.get("arm", record["language"]),
        "language": record["language"],
        "replicate": record.get("replicate", 1),
        "model": (record.get("limits") or {}).get("model"),
        "solved": solved,
        "verdict": record.get("verdict", {}).get("reason"),
        "failure": None if solved else failure,
        "contaminated": bool(record.get("contaminated")),
        "stop": result.get("subtype"),
        "turns": result.get("num_turns"),
        "wall_seconds": record.get("wall_seconds"),
        **{f"tokens_{k}": v for k, v in tokens(result).items()},
        "compile_runs": record.get("audit", {}).get("compile_runs"),
        "compile_runs_failed": record.get("audit", {}).get("compile_runs_failed"),
        "source_bytes": record.get("source_bytes"),
    }


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value from the discordant pairs: b cells only the first solved, c only the second."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def pairs(rows: list[dict], first: str, second: str) -> dict:
    """The paired comparison of two languages over the cells (task, replicate) where neither subject is contaminated."""
    by = {(r["task"], r.get("replicate", 1), r["language"]): r for r in rows if not r["contaminated"]}
    cells = sorted({(t, rep) for t, rep, lang in by if lang == first and (t, rep, second) in by})
    a = [by[(t, rep, first)] for t, rep in cells]
    b = [by[(t, rep, second)] for t, rep in cells]
    only_first = sum(x["solved"] and not y["solved"] for x, y in zip(a, b, strict=True))
    only_second = sum(y["solved"] and not x["solved"] for x, y in zip(a, b, strict=True))
    ratios = [
        x["tokens_total"] / y["tokens_total"]
        for x, y in zip(a, b, strict=True)
        if x.get("tokens_total") and y.get("tokens_total")
    ]
    both = [(x, y) for x, y in zip(a, b, strict=True) if x["solved"] and y["solved"]]

    def ratio(pairs_: list[tuple[dict, dict]]) -> float | None:
        top = sum(x.get("tokens_total") or 0 for x, _ in pairs_)
        bottom = sum(y.get("tokens_total") or 0 for _, y in pairs_)
        return round(top / bottom, 3) if bottom else None

    return {
        "cells": len(cells),
        f"solved_{first}": sum(x["solved"] for x in a),
        f"solved_{second}": sum(y["solved"] for y in b),
        f"only_{first}": only_first,
        f"only_{second}": only_second,
        "mcnemar_p": round(mcnemar(only_first, only_second), 4),
        "token_ratio_all": ratio(list(zip(a, b, strict=True))),
        "token_ratio_both_solved": ratio(both),
        "token_ratio_median_per_cell": round(statistics.median(ratios), 3) if ratios else None,
        "token_ratio_range_per_cell": [round(min(ratios), 3), round(max(ratios), 3)] if ratios else None,
    }


def summary(rows: list[dict], languages: tuple[str, ...]) -> dict:
    """Per language: tasks solved, and the median and sum of every measure over the subjects not contaminated."""
    out = {}
    for language in languages:
        mine = [r for r in rows if r["language"] == language and not r["contaminated"]]
        entry = {"subjects": len(mine), "solved": sum(r["solved"] for r in mine)}
        for key in ("tokens_total", "tokens_output", "tokens_cost_usd", "turns", "wall_seconds", "compile_runs_failed"):
            values = [r[key] for r in mine if r.get(key) is not None]
            if values:
                entry[f"median_{key}"] = round(statistics.median(values), 4)
                entry[f"sum_{key}"] = round(sum(values), 4)
        out[language] = entry
    return out


def markdown(table: dict) -> str:
    """The summary, the paired comparisons and every subject's row, as Markdown tables."""
    lines = [
        "| language | subjects | solved | median tokens | sum tokens | median turns | median seconds | sum cost (USD) |"
    ]
    lines.append("|---|---|---|---|---|---|---|---|")
    for language, s in table["summary"].items():
        lines.append(
            f"| {language} | {s['subjects']} | {s['solved']} | {s.get('median_tokens_total', '')} | "
            f"{s.get('sum_tokens_total', '')} | {s.get('median_turns', '')} | {s.get('median_wall_seconds', '')} | "
            f"{s.get('sum_tokens_cost_usd', '')} |"
        )
    lines += [
        "",
        "| pair | cells | solved | only first | only second | McNemar p | tokens, all | tokens, both solved | per-cell median (range) |",
    ]
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for name, p in table["pairs"].items():
        first, second = name.split("_vs_")
        lines.append(
            f"| {first} / {second} | {p['cells']} | {p[f'solved_{first}']} / {p[f'solved_{second}']} | "
            f"{p[f'only_{first}']} | {p[f'only_{second}']} | {p['mcnemar_p']} | {p['token_ratio_all']} | "
            f"{p['token_ratio_both_solved']} | {p['token_ratio_median_per_cell']} ({p['token_ratio_range_per_cell']}) |"
        )
    lines += [
        "",
        "| replicate | task | language | solved | failure | stop | turns | tokens | cost (USD) | seconds | compiles (failed) |",
    ]
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(table["rows"], key=lambda r: (r["replicate"], r["task"], r["language"])):
        failure = "" if r["solved"] else ("contaminated" if r["contaminated"] else r["verdict"])
        lines.append(
            f"| {r['replicate']} | {r['task']} | {r['language']} | {'yes' if r['solved'] else 'no'} | {failure} | "
            f"{r['stop']} | {r['turns']} | {r.get('tokens_total', '')} | {r.get('tokens_cost_usd', '')} | "
            f"{r['wall_seconds']} | {r['compile_runs']} ({r['compile_runs_failed']}) |"
        )
    lines += [
        "",
        "Not preregistered, a description of where the calls went: calls that read the documentation and the characters "
        "they returned, the other calls and theirs, and the compiler diagnostics a subject saw outside the documentation.",
        "",
        "| replicate | task | language | docs calls | docs characters | other calls | other characters | diagnostics |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(table["rows"], key=lambda r: (r["replicate"], r["task"], r["language"])):
        if e := r.get("exploratory"):
            seen = ", ".join(f"{code} {count}" for code, count in e["diagnostics"].items())
            lines.append(
                f"| {r['replicate']} | {r['task']} | {r['language']} | {e['docs_calls']} | {e['docs_chars']} | "
                f"{e['other_calls']} | {e['other_chars']} | {seen} |"
            )
    return "\n".join(lines) + "\n"


DIAGNOSTIC = {
    "cairn": re.compile(r"\berror\[(E-[A-Z0-9-]+)\]|\"code\":\s*\"(E-[A-Z0-9-]+)\""),
    "rust": re.compile(r"\berror\[(E\d{4})\]"),
    "cpp": re.compile(r"\berror: ()"),
}


def breakdown(transcript: Path, language: str, plugin: str | None = None) -> dict:
    """Where a subject's calls went, a description and not a preregistered measure: the calls that read the
    documentation (the `docs/` of the sandbox or the plugin, the skill and its cards) and the characters they
    returned, the other calls and theirs, and the compiler diagnostics seen."""
    docs_calls = docs_chars = other_calls = other_chars = 0
    codes: dict[str, int] = {}
    for call in tool_calls(transcript):
        given = call["input"]
        target = str(given.get("file_path") or given.get("path") or given.get("command") or given.get("pattern") or "")
        documentation = "docs/" in target or (plugin is not None and plugin in target) or call["name"] == "Skill"
        if documentation and not COMPILE.search(target):
            docs_calls += 1
            docs_chars += len(call["result"])
            continue  # the documentation quotes diagnostics of its own
        other_calls += 1
        other_chars += len(call["result"])
        for match in DIAGNOSTIC[language].finditer(call["result"]):
            code = next((g for g in match.groups() if g), "error")
            codes[code] = codes.get(code, 0) + 1
    return {
        "docs_calls": docs_calls,
        "docs_chars": docs_chars,
        "other_calls": other_calls,
        "other_chars": other_chars,
        "diagnostics": dict(sorted(codes.items(), key=lambda kv: -kv[1])),
    }

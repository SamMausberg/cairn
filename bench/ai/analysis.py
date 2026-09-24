"""The analysis PREREGISTRATION_V1_1.md fixes: cost per solved task with bootstrap intervals, solve rates, turns, wall
time, and the safety failures the judged build caught.

A cell is one task in one replicate, holding one subject of each arm. Every interval comes from the same 10,000
resamples of the cells with replacement under a fixed seed, so the four arms are compared on the same resampled
tasks each time: a resample that draws a hard task draws it for every arm. Contaminated subjects are left out of
every count; their cell still counts for the other arms.
"""

from __future__ import annotations

import math
import random
import statistics

from scoring import mcnemar

SEED = 20260924
RESAMPLES = 10_000
# What the judged build caught, by the verdict's reason: a sanitizer report, an abort (a failed CAIRN guard, or a C++
# abort), a Rust panic, and `unsafe` in Rust or CAIRN source. The other reasons, wrong output, another nonzero exit,
# a build failure, a timeout and a missing construct, are reported with every subject's row.
SAFETY = ("sanitizer", "abort", "panic", "unsafe")


def cells(rows: list[dict], arms: tuple[str, ...]) -> list[dict[str, dict]]:
    """Every (task, replicate) as a map from arm to its subject's row, in a fixed order."""
    by: dict[tuple, dict[str, dict]] = {}
    for r in rows:
        by.setdefault((r["task"], r["replicate"]), {})[r["arm"]] = r
    return [{a: c[a] for a in arms if a in c} for _, c in sorted(by.items())]


def counted(cell: dict[str, dict], arm: str) -> dict | None:
    r = cell.get(arm)
    return r if r is not None and not r["contaminated"] else None


def per_solved(chosen: list[dict[str, dict]], arm: str, key: str) -> float:
    """The sum of `key` over the arm's subjects divided by how many solved: what one solved task cost, failures
    included. Infinite when none solved."""
    mine = [r for c in chosen if (r := counted(c, arm)) is not None]
    solved = sum(r["solved"] for r in mine)
    total = sum(r.get(key) or 0 for r in mine)
    return total / solved if solved else math.inf


def solve_rate(chosen: list[dict[str, dict]], arm: str) -> float:
    mine = [r for c in chosen if (r := counted(c, arm)) is not None]
    return sum(r["solved"] for r in mine) / len(mine) if mine else math.nan


def interval(values: list[float]) -> list[float]:
    """The 2.5th and 97.5th percentiles, by the nearest-rank rule."""
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]  # noqa: E731
    return [pick(0.025), pick(0.975)]


def rounded(x: float, places: int = 4) -> float | str:
    return "inf" if math.isinf(x) else ("nan" if math.isnan(x) else round(x, places))


def bootstrap(table: list[dict[str, dict]], arms: tuple[str, ...]) -> dict:
    """Point estimates and intervals for every arm and every pair of arms, from one set of resamples."""
    rng = random.Random(SEED)
    measures = {"usd": "tokens_cost_usd", "tokens": "tokens_total"}
    draws: dict[str, list[float]] = {}

    def record(name: str, value: float) -> None:
        draws.setdefault(name, []).append(value)

    def estimates(chosen: list[dict[str, dict]]) -> dict[str, float]:
        out = {}
        for arm in arms:
            out[f"{arm}.solve_rate"] = solve_rate(chosen, arm)
            for short, key in measures.items():
                out[f"{arm}.{short}_per_solved"] = per_solved(chosen, arm, key)
        for i, a in enumerate(arms):
            for b in arms[i + 1 :]:
                for short in measures:
                    top, bottom = out[f"{a}.{short}_per_solved"], out[f"{b}.{short}_per_solved"]
                    ratio = top / bottom if bottom not in (0, math.inf) and top != math.inf else math.inf
                    out[f"{a}/{b}.{short}_per_solved"] = ratio
        return out

    point = estimates(table)
    for _ in range(RESAMPLES):
        chosen = [table[rng.randrange(len(table))] for _ in table]
        for name, value in estimates(chosen).items():
            record(name, value)
    return {name: {"estimate": rounded(point[name]), "interval_95": [rounded(x) for x in interval(draws[name])]}
            for name in point}  # fmt: skip


def safety(rows: list[dict], arms: tuple[str, ...]) -> dict:
    """For each arm, how many final programs the judged build failed for each safety reason."""
    return {arm: {k: sum(r["arm"] == arm and not r["contaminated"] and r["failure"] == k for r in rows)
                  for k in SAFETY} for arm in arms}  # fmt: skip


def spread(rows: list[dict], arms: tuple[str, ...]) -> dict:
    """Median and quartiles of turns and wall time, and totals, for each arm."""
    out = {}
    for arm in arms:
        mine = [r for r in rows if r["arm"] == arm and not r["contaminated"]]
        entry: dict = {"subjects": len(mine), "solved": sum(r["solved"] for r in mine)}
        for key in ("turns", "wall_seconds", "tokens_total", "tokens_output", "tokens_cost_usd", "compile_runs"):
            values = sorted(r[key] for r in mine if r.get(key) is not None)
            if len(values) >= 2:
                q = statistics.quantiles(values, n=4, method="inclusive")
                entry[key] = {"median": rounded(q[1], 2), "quartiles": [rounded(q[0], 2), rounded(q[2], 2)],
                              "sum": rounded(sum(values), 4)}  # fmt: skip
            elif values:
                entry[key] = {"median": values[0], "quartiles": [values[0], values[0]], "sum": values[0]}
        out[arm] = entry
    return out


def solved_pairs(table: list[dict[str, dict]], arms: tuple[str, ...]) -> dict:
    """The exact McNemar test on solving, over the cells where both arms' subjects count."""
    out = {}
    for i, a in enumerate(arms):
        for b in arms[i + 1 :]:
            both = [(c[a], c[b]) for c in table if counted(c, a) and counted(c, b)]
            only_a = sum(x["solved"] and not y["solved"] for x, y in both)
            only_b = sum(y["solved"] and not x["solved"] for x, y in both)
            out[f"{a}/{b}"] = {"cells": len(both), f"only_{a}": only_a, f"only_{b}": only_b,
                               "mcnemar_p": round(mcnemar(only_a, only_b), 4)}  # fmt: skip
    return out


def analyse(rows: list[dict], arms: tuple[str, ...]) -> dict:
    table = cells(rows, arms)
    return {
        "cells": len(table),
        "resamples": RESAMPLES,
        "seed": SEED,
        "bootstrap": bootstrap(table, arms) if table else {},
        "spread": spread(rows, arms),
        "safety_failures": safety(rows, arms),
        "solved_pairs": solved_pairs(table, arms),
    }


def markdown(result: dict, rows: list[dict], arms: tuple[str, ...]) -> str:
    """The preregistered tables: each arm's cost per solved task and solve rate with intervals, the ratios, the
    spread of turns and time, the safety failures, and every subject's row."""
    b = result["bootstrap"]
    show = lambda name: f"{b[name]['estimate']} [{b[name]['interval_95'][0]}, {b[name]['interval_95'][1]}]"  # noqa: E731
    lines = [f"{result['cells']} cells, {result['resamples']} resamples of the cells, seed {result['seed']}.", "",
             "| arm | subjects | solved | solve rate [95%] | USD per solved task [95%] | tokens per solved task [95%] |",
             "|---|---|---|---|---|---|"]  # fmt: skip
    for arm in arms:
        s = result["spread"][arm]
        lines.append(f"| {arm} | {s['subjects']} | {s['solved']} | {show(f'{arm}.solve_rate')} | "
                     f"{show(f'{arm}.usd_per_solved')} | {show(f'{arm}.tokens_per_solved')} |")  # fmt: skip
    lines += ["", "| ratio | USD per solved task [95%] | tokens per solved task [95%] | McNemar p (only first, only second) |",
              "|---|---|---|---|"]  # fmt: skip
    for pair, test in result["solved_pairs"].items():
        a, c = pair.split("/")
        lines.append(f"| {pair} | {show(f'{pair}.usd_per_solved')} | {show(f'{pair}.tokens_per_solved')} | "
                     f"{test['mcnemar_p']} ({test[f'only_{a}']}, {test[f'only_{c}']}) |")  # fmt: skip
    lines += ["", "| arm | median turns [quartiles] | median seconds [quartiles] | sum USD | sum tokens | "
              + " | ".join(SAFETY) + " |", "|---|---|---|---|---|" + "---|" * len(SAFETY)]  # fmt: skip
    for arm in arms:
        s, f = result["spread"][arm], result["safety_failures"][arm]
        t, w = s.get("turns", {}), s.get("wall_seconds", {})
        lines.append(f"| {arm} | {t.get('median')} {t.get('quartiles')} | {w.get('median')} {w.get('quartiles')} | "
                     f"{s.get('tokens_cost_usd', {}).get('sum')} | {s.get('tokens_total', {}).get('sum')} | "
                     + " | ".join(str(f[k]) for k in SAFETY) + " |")  # fmt: skip
    lines += ["", "| replicate | task | arm | solved | failure | stop | turns | tokens | cost (USD) | seconds | compiles (failed) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in sorted(rows, key=lambda r: (r["replicate"], r["task"], arms.index(r["arm"]))):
        failure = "" if r["solved"] else ("contaminated" if r["contaminated"] else r["failure"])
        lines.append(f"| {r['replicate']} | {r['task']} | {r['arm']} | {'yes' if r['solved'] else 'no'} | {failure} | "
                     f"{r['stop']} | {r['turns']} | {r.get('tokens_total', '')} | {r.get('tokens_cost_usd', '')} | "
                     f"{r['wall_seconds']} | {r['compile_runs']} ({r['compile_runs_failed']}) |")  # fmt: skip
    return "\n".join(lines) + "\n"

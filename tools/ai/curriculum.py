#!/usr/bin/env python3
"""Build deterministic, compiler-checked teaching data and separate family splits.

This does not train or call a model. Native expected results come from explicit
Python oracles, not CAIRN-generated C++. All answers are shipped for audit; a
future evaluator must isolate held-out answers from the model's workspace.
"""

import argparse
import json
import random
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(R / "src"))
from cairn.agent.agent_tools import explain, stable_json
from cairn.agent.projection import canonical_source
from cairn.agent.teaching import CARDS, select_cards
from cairn.compiler.cairnc import Diagnostic, compile_source

MASK = 2**64 - 1
VALUES = [[], [0], [1], [MASK], [0, 1, 2, 3, MASK], [9, 9, 9], [5, 1, 8, 0, 2, 7]]


def build():
    tasks = []
    rng = random.Random(17092603)
    xs = VALUES + [[rng.randrange(40) for _ in range(n)] for n in [2, 5, 8, 17]]
    for family in ["affine", "count_gt", "prefix", "compact_gt", "clamp", "interval", "rotate_xor", "delta"]:
        split = "train" if family in {"affine", "count_gt", "prefix", "compact_gt", "clamp"} else "heldout"
        for k in range(8):
            symbol = f"{family}_{k}"
            cases = []
            A = k + 2
            B = k * 3 + 1
            if family == "affine":
                text = f"fn {symbol}(x:u64)->u64{{return add_wrap(mul_wrap(x,{A}),{B});}}"
                desc = f"Return ({A}*x+{B}) modulo 2^64. No trap or writes."
                for x in [0, 1, 2, 17, MASK, MASK - 1, 2**63, *[rng.getrandbits(64) for _ in range(7)]]:
                    cases.append({"args": {"x": x}, "return": (A * x + B) & MASK})
            elif family == "count_gt":
                text = f"fn {symbol}(n:usize,x:ro<u64>[n]@host,threshold:u64)->usize{{let mut total:usize=0;for i in 0..n{{if x[i]>threshold{{total=add_wrap(total,1);}}}}return total;}}"
                desc = "Count values strictly greater than threshold. Empty input returns zero."
                for x in xs:
                    cases.append({"args": {"n": len(x), "x": x, "threshold": B}, "return": sum(v > B for v in x)})
            elif family == "prefix":
                text = f"fn {symbol}(n:usize,out:rw<u64>[n]@host,x:ro<u64>[n]@host){{let mut total:u64=0;for i in 0..n{{total=add_wrap(total,x[i]);out[i]=total;}}}}"
                desc = "Write inclusive prefix sums modulo 2^64; inputs and outputs are disjoint."
                for x in xs:
                    acc = 0
                    out = []
                    for v in x:
                        acc = (acc + v) & MASK
                        out.append(acc)
                    cases.append({"args": {"n": len(x), "out": [123] * len(x), "x": x}, "after": {"out": out}})
            elif family == "compact_gt":
                text = f"fn {symbol}(n:usize,out:rw<u64>[n]@host,x:ro<u64>[n]@host,threshold:u64)->usize{{let used=compact out for i in n where x[i]>threshold yield x[i];return used;}}"
                desc = "Stably select values strictly greater than threshold, return selected length, preserve the unwritten output tail."
                for x in xs:
                    selected = [v for v in x if v > B]
                    out = selected + [123] * (len(x) - len(selected))
                    cases.append(
                        {
                            "args": {"n": len(x), "out": [123] * len(x), "x": x, "threshold": B},
                            "return": len(selected),
                            "after": {"out": out},
                        }
                    )
            elif family == "clamp":
                text = f"fn {symbol}(x:u64)->u64{{return min(max(x,{A}),{A + B});}}"
                desc = f"Clamp x to the inclusive interval [{A},{A + B}]."
                for x in [0, 1, A - 1, A, A + 1, A + B - 1, A + B, A + B + 1, MASK]:
                    cases.append({"args": {"x": x}, "return": min(max(x, A), A + B)})
            elif family == "interval":
                text = f"fn {symbol}(n:usize,x:ro<u64>[n]@host,lo:u64,hi:u64)->usize{{let mut total:usize=0;for i in 0..n{{if x[i]>=lo && x[i]<hi{{total=add_wrap(total,1);}}}}return total;}}"
                desc = "Count values in the half-open interval [lo,hi). Return zero for empty or reversed intervals."
                for x in xs:
                    for lo, hi in [(0, A), (A, A), (A + B, A), (A, A + B)]:
                        cases.append(
                            {"args": {"n": len(x), "x": x, "lo": lo, "hi": hi}, "return": sum(lo <= v < hi for v in x)}
                        )
            elif family == "rotate_xor":
                shift = k + 1
                text = f"fn {symbol}(x:u64,key:u64)->u64{{return (shl_wrap(x,{shift}) | shr(x,{64 - shift})) ^ key;}}"
                desc = f"Rotate x left by {shift} bits in 64-bit arithmetic, then XOR with key."
                for x in [0, 1, MASK, 2**63, *[rng.getrandbits(64) for _ in range(12)]]:
                    key = rng.getrandbits(64)
                    cases.append(
                        {"args": {"x": x, "key": key}, "return": (((x << shift) & MASK) | (x >> (64 - shift))) ^ key}
                    )
            else:
                text = f"fn {symbol}(n:usize,out:rw<u64>[n]@host,x:ro<u64>[n]@host){{for i in 0..n{{if i==0{{out[i]=x[i];}}else{{out[i]=sub_wrap(x[i],x[i-1]);}}}}}}"
                desc = "For nonempty input, copy the first value and then write each adjacent difference modulo 2^64. Empty input writes nothing."
                for x in xs:
                    out = ([x[0]] + [(x[i] - x[i - 1]) & MASK for i in range(1, len(x))]) if x else []
                    cases.append({"args": {"n": len(x), "out": [123] * len(x), "x": x}, "after": {"out": out}})
            text = canonical_source(text)
            _, receipt = compile_source(text)
            tasks.append(
                {
                    "id": symbol,
                    "family": family,
                    "split": split,
                    "source": text,
                    "contract": {
                        "schema": "cairn.task/1",
                        "symbol": symbol,
                        "task": desc,
                        "allowed_effects": receipt["functions"][symbol]["effects"],
                        "cases": cases,
                    },
                }
            )
    return tasks


CONTRASTS = [
    (
        "mutability",
        "fn f(x:u64)->u64{let y=x;y=add_wrap(y,1);return y;}",
        "fn f(x:u64)->u64{let mut y=x;y=add_wrap(y,1);return y;}",
        "E-IMMUTABLE",
    ),
    ("explicit_return", "fn f(x:u64)->u64{x;}", "fn f(x:u64)->u64{return x;}", "E-DISCARD"),
    ("literal_width", "fn f()->u8{return 256;}", "fn f()->u16{return 256;}", "E-LITERAL-RANGE"),
    ("numeric_cast", "fn f(x:u32)->u64{return x;}", "fn f(x:u32)->u64{return u64(x);}", "E-TYPE-MISMATCH"),
    ("unbound_name", "fn f(x:u64)->u64{return input;}", "fn f(x:u64)->u64{return x;}", "E-UNBOUND"),
    (
        "view_extent_order",
        "fn f(x:ro<u64>[n]@host,n:usize)->u64{return x[0];}",
        "fn f(n:usize,x:ro<u64>[n]@host)->u64{return x[0];}",
        "E-EXTENT",
    ),
    (
        "no_readonly_store",
        "fn f(n:usize,x:ro<u64>[n]@host)->u64{x[0]=1;return x[0];}",
        "fn f(n:usize,x:ro<u64>[n]@host)->u64{return x[0];}",
        "E-WRITE-LEASE",
    ),
    # bool is not a number, so u64(x) is refused; a numeric conversion is checked, not refused.
    ("no_bool_cast", "fn f(x:bool)->u64{return u64(x);}", "fn f(x:bool)->u64{if x {return 1;} return 0;}", "E-CAST"),
    (
        "no_alias",
        "fn use(n:usize,o:rw<u64>[n]@host,x:ro<u64>[n]@host){}fn f(n:usize,o:rw<u64>[n]@host){use(n,o,o);}",
        "fn use(n:usize,o:rw<u64>[n]@host,x:ro<u64>[n]@host){}fn f(n:usize,o:rw<u64>[n]@host,x:ro<u64>[n]@host){use(n,o,x);}",
        "E-ALIAS",
    ),
    ("unsigned_wrap", "fn f(x:i64)->i64{return add_wrap(x,1);}", "fn f(x:i64)->i64{return x+1;}", "E-WRAP-TYPE"),
    ("integer_min", "fn f(x:f64)->f64{return min(x,x);}", "fn f(x:f64)->f64{return x;}", "E-MINMAX"),
    (
        "collector_capacity",
        "fn f(n:usize,m:usize,o:rw<u64>[n]@host)->usize{let used=compact o for i in m where true yield u64(i);return used;}",
        "fn f(n:usize,m:usize,o:rw<u64>[n]@host)->usize{let used=compact o for i in n where true yield u64(i);return used;}",
        "E-COLLECT-CAPACITY",
    ),
]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=R / "training/source", help="Where the teaching data is written.")
    root = p.parse_args().out
    root.mkdir(parents=True, exist_ok=True)
    tasks = build()
    contrasts = []
    for family, bad, good, code in CONTRASTS:
        compile_source(good)
        try:
            compile_source(bad)
        except Diagnostic as e:
            # A changed code means the language moved and this pair teaches the wrong lesson.
            assert e.data["code"] == code, f"{family} is now {e.data['code']}, not {code}"
            contrasts.append(
                {
                    "family": family,
                    "rejected_source": bad,
                    "diagnostic": explain(e, bad),
                    "accepted_source": canonical_source(good),
                    "scope": "Language-teaching pair; not an authorized semantic repair. Some pairs change the API or behavior and cannot be applied in a body-only session.",
                }
            )
        else:
            raise AssertionError(f"The {family} negative example compiles now; the pair is stale: {bad}")
    for split in ["train", "heldout"]:
        rows = []
        for t in tasks:
            if t["split"] != split:
                continue
            row = {
                "id": t["id"],
                "family": t["family"],
                "task": t["contract"]["task"],
                "messages": [
                    {
                        "role": "system",
                        "content": "\n\n".join(select_cards(t["source"], has_views="@host" in t["source"]).values()),
                    },
                    {
                        "role": "user",
                        "content": t["contract"]["task"] + "\nRequired signature: " + t["source"].split("{")[0].strip(),
                    },
                ],
            }
            if split == "train":
                row["messages"].append({"role": "assistant", "content": t["source"]})
            rows.append(row)
        (root / (split + ".jsonl")).write_text("".join(stable_json(row) + "\n" for row in rows))
    (root / "contrastive.jsonl").write_text("".join(stable_json(row) + "\n" for row in contrasts))
    (root / "all_tasks_with_oracles.json").write_text(json.dumps(tasks, indent=2) + "\n")
    (root / "corpus.cairn").write_text("\n".join(t["source"] for t in tasks))
    (root / "cards.json").write_text(json.dumps(CARDS, indent=2) + "\n")
    summary = {
        "seed": 17092603,
        "tasks": len(tasks),
        "train": sum(t["split"] == "train" for t in tasks),
        "heldout": sum(t["split"] == "heldout" for t in tasks),
        "contrastive_pairs": len(contrasts),
        "test_cases": sum(len(t["contract"]["cases"]) for t in tasks),
        "train_families": sorted({t["family"] for t in tasks if t["split"] == "train"}),
        "heldout_families": sorted({t["family"] for t in tasks if t["split"] == "heldout"}),
        "independent_families": 8,
        "model_training_performed": False,
        "model_evaluations_performed": 0,
        "native_validation": "run tools/checks/curriculum_verify.py; generation checks frontend only",
        "note": "Several variants are alpha-renamings or parameter variants, not independent tasks; templates define the true diversity.",
    }
    (root / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compare complete definitions, not generated expansions or model success."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cairn import compile_source

PAIRS = [
    (
        "expression_body",
        "fn average(x:u64, y:u64) -> u64 { return (x & y) + shr(x ^ y, 1); }\n",
        "fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);\n",
    ),
    (
        "host_views",
        "fn copy(n:usize, out:rw<u64>[n]@host, input:ro<u64>[n]@host) {\n  for i in 0..n { out[i] = input[i]; }\n}\n",
        "fn copy(n:usize, out:rw<u64>[n], input:ro<u64>[n]) {\n  for i in 0..n { out[i] = input[i]; }\n}\n",
    ),
    (
        "else_if",
        "fn sign(x:i64) -> i32 {\n  if x < 0 { return -1; } else { if x > 0 { return 1; } else { return 0; } }\n}\n",
        "fn sign(x:i64) -> i32 {\n  if x < 0 { return -1; } else if x > 0 { return 1; } else { return 0; }\n}\n",
    ),
]


def main():
    rows = []
    for name, explicit, compact in PAIRS:
        a, _ = compile_source(explicit)
        b, _ = compile_source(compact)
        if a != b:
            raise SystemExit("Different lowering for " + name)
        rows.append(
            {
                "name": name,
                "explicit_source": explicit,
                "compact_source": compact,
                "explicit_utf8_bytes": len(explicit.encode()),
                "compact_utf8_bytes": len(compact.encode()),
                "generated_cpp_identical": True,
                "generated_cpp_sha256": hashlib.sha256(a.encode()).hexdigest(),
            }
        )
    print(
        json.dumps(
            {
                "method": "Exact UTF-8 bytes with whitespace retained. Complete paired definitions, no model tokenizer claim.",
                "pairs": rows,
                "interpretation": "Small authored-syntax savings only; no total-context or speed claim.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

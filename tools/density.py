#!/usr/bin/env python3
"""Reproducible lexical density accounting; no model-success claim.

Default: ByT5 plain UTF-8 byte IDs (byte+3), without special tokens.
Optional: --tiktoken o200k_base, requiring independently available package/vocab.
All text whitespace/comments retained; raw file hashes identify exact inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R / "src"))
from cairn.cairnc import Emitter, Parser


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiktoken")
    ap.add_argument("--output", type=Path, default=R / "results/density.json")
    args = ap.parse_args()
    if args.tiktoken:
        try:
            import tiktoken

            enc = tiktoken.get_encoding(args.tiktoken)
        except Exception as exc:
            raise SystemExit(f"No measurement: tiktoken package/vocabulary unavailable: {exc}")
        encode = enc.encode
        label = "tiktoken/" + args.tiktoken
    else:

        def encode(text):
            return [b + 3 for b in text.encode("utf-8")]

        label = "ByT5 plain UTF-8 bytes, no special tokens"
        sample = "source: alpha \u03b1, newline\n"
        assert bytes(i - 3 for i in encode(sample)).decode("utf-8") == sample

    def count(t):
        return len(encode(t))

    names = ["saxpy", "dot", "sum_wrap", "prefix", "count_gt", "histogram", "compact_even", "lower_bound", "gcd"]

    def extract(src, pattern):
        m = re.search(pattern, src)
        if not m:
            raise RuntimeError("Function not found: " + pattern)
        start = m.start()
        brace = src.index("{", m.end() - 1)
        depth = 1
        end = brace + 1
        while depth:
            depth += (src[end] == "{") - (src[end] == "}")
            end += 1
        return src[start:end] + "\n"

    native = (R / "examples/native.cairn").read_text()
    ref = (R / "bench/reference.cpp").read_text()
    pairs = []
    for n in names:
        a = extract(native, r"fn " + n + r"\(")
        b = extract(ref, r'extern "C" [^\n]*?cc_' + n + r"\(")
        pairs.append(
            {
                "function": n,
                "cairn_tokens": count(a),
                "cpp_tokens": count(b),
                "cpp_over_cairn": count(b) / count(a),
                "cairn_sha256": hashlib.sha256(a.encode()).hexdigest(),
                "cpp_sha256": hashlib.sha256(b.encode()).hexdigest(),
            }
        )
    paths = [
        "examples/native.cairn",
        "examples/family.cairn",
        "examples/wire.cairn",
        "bench/reference.cpp",
        "bench/family_template.cpp",
        "results/native.cpp",
        "results/family.cpp",
        "results/wire.cpp",
        "results/cairn_runtime.hpp",
        "docs/cards/AGENT_CARD.md",
        "docs/cards/GENERATOR_CONTRACTS.md",
    ]
    compiler_paths = sorted(str(p.relative_to(R)) for p in (R / "src/cairn").rglob("*") if p.suffix in {".py", ".hpp"})
    paths += compiler_paths
    files = {
        p: {"tokens": count((R / p).read_text()), "sha256": hashlib.sha256((R / p).read_bytes()).hexdigest()}
        for p in paths
    }

    def t(p):
        return files[p]["tokens"]

    family = {
        "source_tokens": t("examples/family.cairn"),
        "expanded_cpp_tokens": t("results/family.cpp"),
        "cpp_template_tokens": t("bench/family_template.cpp"),
        "expansion_ratio": t("results/family.cpp") / t("examples/family.cairn"),
        "compact_cpp_template_over_cairn": t("bench/family_template.cpp") / t("examples/family.cairn"),
        "cold_cairn_source_plus_card_plus_recipe": t("examples/family.cairn")
        + t("docs/cards/AGENT_CARD.md")
        + t("docs/cards/GENERATOR_CONTRACTS.md"),
        "source_audit_cairn_plus_card_plus_recipe_plus_compiler": t("examples/family.cairn")
        + t("docs/cards/AGENT_CARD.md")
        + t("docs/cards/GENERATOR_CONTRACTS.md")
        + sum(t(p) for p in compiler_paths),
        "cpp_template_plus_shared_runtime": t("bench/family_template.cpp") + t("results/cairn_runtime.hpp"),
        "limitation": "CAIRN exports 256 named entries; compact C++ uses one indexed entry and a function-pointer table. Different API; both tested over the same numerical family. Runtime header shared by both. No C++ language documentation charged; no universal cold-context advantage inferred.",
    }
    wire = {
        "source_tokens": t("examples/wire.cairn"),
        "expanded_cpp_tokens": t("results/wire.cpp"),
        "expansion_ratio": t("results/wire.cpp") / t("examples/wire.cairn"),
        "cold_source_plus_card_plus_recipe": t("examples/wire.cairn")
        + t("docs/cards/AGENT_CARD.md")
        + t("docs/cards/GENERATOR_CONTRACTS.md"),
        "limitation": "No independent compact C++ codec library baseline. Expansion ratio is not a language superiority comparison.",
    }
    result = {
        "tokenizer": label,
        "whitespace_and_comments": "retained",
        "special_tokens": "excluded",
        "files": files,
        "compiler_audit_files": compiler_paths,
        "compiler_audit_tokens": sum(t(p) for p in compiler_paths),
        "algorithm_pairs": pairs,
        "aggregate_algorithms": {
            "cairn_tokens": sum(x["cairn_tokens"] for x in pairs),
            "cpp_tokens": sum(x["cpp_tokens"] for x in pairs),
            "cpp_over_cairn": sum(x["cpp_tokens"] for x in pairs) / sum(x["cairn_tokens"] for x in pairs),
        },
        "family": family,
        "wire": wire,
        "claims_excluded": [
            "100x against well-factored C++",
            "frontier BPE reduction",
            "measured safe-edit context",
            "fresh-model success",
            "minimum instruction length",
        ],
        "method": "Function slices include full signatures and bodies, one terminal newline; file counts include all exact bytes. Common runtime counted separately. Constructed cold packets, not actual interaction transcripts.",
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "tokenizer": label,
                "card_tokens": t("docs/cards/AGENT_CARD.md"),
                "family": family,
                "wire": wire,
                "algorithms": result["aggregate_algorithms"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

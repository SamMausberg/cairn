"""The native compilation API: parse, expand, check, certify, emit."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .checking import Binding, Checker
from .codegen import RUNTIME, RUNTIME_FILES, Emitter
from .expansion import derive, specialize
from .linear_certificates import audit_collector
from .modules import link
from .syntax import (
    IDENT,
    INT,
    RESERVED,
    SIGNED,
    WIDTH,
    Diagnostic,
    Expr,
    Function,
    Parser,
    Program,
    Stmt,
    Type,
    fail,
)  # fmt: skip
from .version import VERSION

__all__ = [
    "IDENT", "INT", "RESERVED", "RUNTIME", "RUNTIME_FILES", "SIGNED", "VERSION", "WIDTH", "Binding", "Checker",
    "Diagnostic", "Emitter", "Expr", "Function", "Parser", "Program", "Stmt", "Type", "compile_program",
    "compile_source", "derive", "fail", "specialize",
]  # fmt: skip


def compile_program(source: str, capture_sites: bool = False) -> tuple[Program, Checker, dict[str, Any]]:
    p = specialize(derive(link(Parser(source).parse())))
    checker = Checker(p, capture_sites)
    return p, checker, checker.check()


def interfaces(p: Program, receipts: dict[str, Any]) -> dict[str, Any]:
    """Per module: what it exports and a digest of those signatures and effect rows.

    A dependent's assumptions can only break when this digest changes; bodies may change freely.
    """
    out: dict[str, Any] = {}
    for f in p.functions:
        home = p.modules.get(f.name, f.module)  # A family's instances are exported by the module that declared it.
        if home and (f.public or f.owner) and f.name in receipts:
            params = [[n, t.display()] for n, t in f.params]
            entry = {"params": params, "returns": f.ret.display(), "effects": receipts[f.name]["effects"]}
            out.setdefault(home, {})[f.name] = entry
    return {m: {"exports": sorted(fs), "interface_sha256": hashlib.sha256(json.dumps(fs, sort_keys=True).encode()).hexdigest()}
            for m, fs in sorted(out.items())}  # fmt: skip


def compile_source(source: str, origin: Any = "", roots: tuple[str, ...] = ()) -> tuple[str, dict[str, Any]]:
    """Generated C++ and its receipt; `origin` names the source in #line directives for debug builds."""
    p, checker, receipts = compile_program(source)
    certificate = audit_collector()  # The collector's unchecked store is emitted only under this gate.
    emitter = Emitter(p, checker, origin, roots)
    cpp = emitter.emit()
    manifest = {
        "compiler": VERSION,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "generated_sha256": hashlib.sha256(cpp.encode()).hexdigest(),
        "runtime_sha256": hashlib.sha256(RUNTIME.encode()).hexdigest(),
        "function_count": len(p.functions),
        "families": [list(x) for x in p.families],
        "wire_derivations": [f"{m}.{t}" if m and "." not in t else t for m, r, _, t, _ in p.derivations if r == "wire"],
        "derivations": [{"module": m, "recipe": r, "naturals": list(n), "for": t} for m, r, n, t, _ in p.derivations],
        "recipes": {name: recipe.digest for name, recipe in sorted(p.recipes.items())},
        "uninstantiated_templates": checker.unchecked,
        "modules": interfaces(p, receipts),
        "requires": ["cuda"] if "cairn_gpu.hpp" in emitter.headers else [],
        "trusted_lowering_rules": [
            "bounded-collector/2",
            "unsigned-little-endian-wire/1",
            "scoped-scalar-storage/1",
            "tagged-scalar-sums/1",
        ],
        "arithmetic_certificate": {
            k: certificate[k] for k in ("status", "sha256", "certificate_count", "checker_sha256", "lean_verified")
        },
        "functions": receipts,
        "formal_status": "not-verified",
        "ffi_requires": "Each nonempty view describes live, initialized, correctly typed storage for its "
        "stated extent throughout the call; no concurrent external mutation.",
        "target_profile": "64-bit host, C++20, GCC/Clang overflow builtins, strict floating mode",
    }
    return cpp, manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--check", action="store_true", help="Check and report without writing generated files")
    ap.add_argument("--receipt", type=Path)
    args = ap.parse_args()
    try:
        cpp, receipt = compile_source(args.source.read_text(encoding="utf-8"))
        if args.output and not args.check:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(cpp)
            for name, text in RUNTIME_FILES.items():
                (args.output.parent / name).write_text(text)
        if args.receipt:
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
        if not args.output or args.check:
            summary = {"status": "accepted", "compiler": VERSION, "functions": receipt["function_count"]}
            print(json.dumps({**summary, "formal_status": "not-verified"}))
        return 0
    except Diagnostic as e:
        print(json.dumps(e.data), file=sys.stderr)
        return 1
    except (OSError, UnicodeError, RecursionError, ValueError, OverflowError) as e:
        unknown = {"protocol": "cairn.diagnostic/1", "status": "unknown", "code": "E-RESOURCE-OR-IO"}
        print(json.dumps({**unknown, "message": str(e)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

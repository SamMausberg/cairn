"""The native compilation API: parse, expand, check, certify, emit."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ..verify.linear_certificates import audit_collector
from ..version import VERSION
from . import layouts
from .checking import Binding, Checker
from .codegen import RUNTIME, RUNTIME_FILES, Emitter
from .expansion import derive, specialize
from .lexing import IDENT, RESERVED
from .machine import records
from .modules import link
from .syntax import Parser
from .traits import certify
from .tree import INT, SIGNED, WIDTH, Diagnostic, Expr, Function, Program, Stmt, Type, fail

__all__ = ["IDENT", "INT", "RESERVED", "RUNTIME", "RUNTIME_FILES", "SIGNED", "VERSION", "WIDTH", "Binding", "Checker",
           "Diagnostic", "Emitter", "Expr", "Function", "Parser", "Program", "Stmt", "Type", "certify_templates",
           "compile_program", "compile_source", "compile_units", "derive", "fail", "generate", "joined", "specialize",
           "units", "write_program"]  # fmt: skip


def write_program(directory: Path, name: str, cpp: str) -> Path:
    """`cpp` written as `directory/name`, beside the runtime headers it includes."""
    for file, text in {name: cpp, **RUNTIME_FILES}.items():
        (directory / file).write_text(text, encoding="utf-8")
    return directory / name


def compile_program(source: str, capture_sites: bool = False, parsed: Program | None = None,
                    every: bool = False) -> tuple[Program, Checker, dict[str, Any]]:  # fmt: skip
    """`parsed`, when given, is `Parser(source).parse()` already made by the caller, and is linked in place.

    With `every`, the checker goes on after a refusal wherever the rest can still be judged: the Diagnostic raised is
    the first refusal as it is without `every`, and its record adds `further`, every other refusal no earlier one
    explains, in source order, at most twenty of them (`further_omitted` counts the rest), and `not_judged`, how many
    of the program's functions got no verdict because of a refusal. A parse error is reported alone."""
    p = specialize(derive(link(parsed or Parser(source).parse())))
    checker = Checker(p, capture_sites, every)
    receipts = checker.check()
    layouts.settle(checker)  # a layout no function uses is still held to its rules
    return p, checker, receipts


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


def certify_templates(source: str) -> dict[str, str]:
    """For each generic function of the program's own modules: "ok" if its body needs only its bounds."""
    return certify(lambda: Checker(specialize(derive(link(Parser(source).parse())))))[1]


def compile_units(source: str, origin: Any = "", roots: tuple[str, ...] = (), keep_guards: bool = False,
                  sites: Any = None) -> tuple[dict[str, str], dict[str, Any]]:  # fmt: skip
    """The same program as one object per module: `program.hpp` (what every unit shares) and `<module>.cpp`
    files holding only bodies. A body-only change alters one file; a signature change alters the header."""
    interface, bodies, manifest = generate(source, origin, roots, keep_guards, sites)
    return units(interface, bodies), manifest


def units(interface: list[str], bodies: list[tuple[str, list[str]]]) -> dict[str, str]:
    """What `generate` made, cut into the files of an incremental build."""
    shared = "\n".join(
        ["#pragma once", *(line.replace("static const cdt_", "inline const cdt_") for line in interface)]
    )
    files = {"program.hpp": shared + "\n"}
    for module, lines in bodies:
        name = (module or "root").replace(".", "_") + ".cpp"
        files[name] = files.get(name, '#include "program.hpp"\n') + "\n".join(lines) + "\n"
    return files


def joined(interface: list[str], bodies: list[tuple[str, list[str]]]) -> str:
    """What `generate` made, as the one C++ file of a whole-program build."""
    return "\n".join([*interface, *(line for _, lines in bodies for line in lines)]) + "\n"


def compile_source(source: str, origin: Any = "", roots: tuple[str, ...] = (), keep_guards: bool = False,
                   sites: Any = None, every: bool = False) -> tuple[str, dict[str, Any]]:  # fmt: skip
    """Generated C++ and its receipt; `origin` names the source in #line directives for debug builds,
    `keep_guards` writes every guard, including the ones the checker showed cannot fail, `sites` maps a line to
    the (file, line) a failed assert names, and `every` reports every refusal (`compile_program`)."""
    interface, bodies, manifest = generate(source, origin, roots, keep_guards, sites, every=every)
    return joined(interface, bodies), manifest


def generate(source: str, origin: Any, roots: tuple[str, ...], keep_guards: bool = False, sites: Any = None,
             parsed: Program | None = None,
             every: bool = False) -> tuple[list[str], list[tuple[str, list[str]]], dict]:  # fmt: skip
    p, checker, receipts = compile_program(source, parsed=parsed, every=every)
    certificate = audit_collector()  # The collector's unchecked store is emitted only under this gate.
    emitter = Emitter(p, checker, origin, roots, keep=keep_guards, sites=sites)
    for name, verdict in emitter.elision.items():  # What lowering leaves out is what the audit accepted.
        if name in receipts:
            receipts[name]["discharged_check_sites"] = verdict["accepted"]
            if verdict["refused"]:
                receipts[name]["refused_discharges"] = verdict["refused"]
    interface, bodies = emitter.units()
    for name, chains in emitter.fused.items():  # What a plan's fuse joined, as it was emitted.
        if name in receipts:
            receipts[name]["fused"] = chains
    for f in p.functions:  # What each typed asm declares, trusted as written, so an audit starts from the receipt.
        if f.name in receipts and (declared := records(f)):
            receipts[f.name]["assembly"] = declared
    declared = [x for f in receipts.values() for x in f.get("assembly", ())]  # what builds it, and where it runs
    needs = sorted({"ptx:" + x["needs"] if "needs" in x else "asm:" + x["target"] for x in declared})
    cpp = joined(interface, bodies)
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
        "requires": (["cuda"] if "cairn_gpu.hpp" in emitter.headers else []) + needs,
        "device_features": list(emitter.features),  # what the device target must provide (projects/target.py)
        "trusted_lowering_rules": [
            "bounded-collector/2",
            "unsigned-little-endian-wire/1",
            "scoped-scalar-storage/1",
            "tagged-scalar-sums/1",
            "checked-entries/1",
        ],
        "arithmetic_certificate": {
            k: certificate[k] for k in ("status", "sha256", "certificate_count", "checker_sha256", "lean_verified")
        },
        "functions": receipts,
        **({"layouts": layouts.receipt(checker)} if p.layouts else {}),
        "formal_status": "not-verified",
        "ffi_requires": "Each nonempty view describes live, initialized, correctly typed storage for its "
        "stated extent throughout the call; no concurrent external mutation.",
        "target_profile": "64-bit host, C++20, GCC/Clang overflow builtins, strict floating mode",
    }
    return interface, bodies, manifest


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
            write_program(args.output.parent, args.output.name, cpp)
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

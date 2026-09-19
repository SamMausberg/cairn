#!/usr/bin/env python3
"""Export the bounded-collector certificates as a Lean 4 source file.

The Lean development in `proofs/` must talk about exactly the rules the compiler
checks, so the rule data is generated from `cairn.linear_certificates` rather
than transcribed by hand.  `--check` re-generates in memory and compares against
the file on disk, which lets CI fail when the Python rules and the Lean file
drift apart.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cairn.verify.linear_certificates import Rule, audit_collector, check, collector_rules

TARGET = ROOT / "proofs" / "Cairn" / "CollectorCertificates.lean"


def identifier(name: str) -> str:
    """Turn a dotted obligation name into a Lean identifier component."""
    cleaned = "".join(character if character.isalnum() else "_" for character in name)
    if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
        raise ValueError("Rule name does not yield a Lean identifier: " + name)
    return cleaned


def form(values: tuple[int, ...]) -> str:
    return "⟨" + ", ".join(str(value) for value in values) + "⟩"


def lean_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def render(pairs: tuple[tuple[Rule, object], ...], digest: str) -> str:
    lines: list[str] = []
    out = lines.append
    out("/-")
    out("GENERATED FILE - DO NOT EDIT BY HAND.")
    out("")
    out("Regenerate with:")
    out("    .venv/bin/python tools/checks/export_lean_certificates.py")
    out("Check for drift with:")
    out("    .venv/bin/python tools/checks/export_lean_certificates.py --check")
    out("")
    out("Source of truth: `collector_rules()` in `src/cairn/verify/linear_certificates.py`.")
    out("SHA-256 of the exported obligation table (as `cairn certificates` reports it):")
    out("    " + digest)
    out("-/")
    out("import Cairn.Affine")
    out("")
    out("namespace Cairn")
    out("namespace Collector")
    out("")
    for rule, certificate in pairs:
        key = identifier(rule.name)
        out("/-- Obligation `" + rule.name + "`. -/")
        out("def rule_" + key + " : Rule where")
        out("  name := " + lean_string(rule.name))
        assumptions = ", ".join(form(assumption) for assumption in rule.assumptions)
        out("  assumptions := [" + assumptions + "]")
        out("  conclusion := " + form(rule.conclusion))
        out("")
        out("/-- Nonnegative-combination witness for `" + rule.name + "`. -/")
        out("def cert_" + key + " : Certificate where")
        out("  weights := [" + ", ".join(str(w) for w in certificate.weights) + "]")
        out("  nonnegativeConstant := " + str(certificate.nonnegative_constant))
        out("")
    out("/-- Every exported (rule, certificate) pair, in `collector_rules()` order. -/")
    out("def certificates : List (Rule × Certificate) :=")
    entries = ["    (rule_" + identifier(rule.name) + ", cert_" + identifier(rule.name) + ")" for rule, _ in pairs]
    out("  [\n" + ",\n".join(entries) + "\n  ]")
    out("")
    out("/-- The exported bundle has the expected size; a dropped rule fails here. -/")
    out("theorem certificates_length : certificates.length = " + str(len(pairs)) + " := by decide")
    out("")
    out("/-- **Every exported certificate is accepted by the checker**, by kernel")
    out("computation over exact integers (no `native_decide`). -/")
    out("theorem all_checked : certificates.all (fun rc => check rc.1 rc.2) = true := by decide")
    out("")
    for rule, _ in pairs:
        key = identifier(rule.name)
        out("/-- `" + rule.name + "`: the certified affine implication, as a statement")
        out("about arbitrary integers `K I N M`. -/")
        out("theorem obligation_" + key + " (K I N M : Int)")
        for index, assumption in enumerate(rule.assumptions):
            out("    (h" + str(index) + " : 0 ≤ Form.eval " + form(assumption) + " K I N M)")
        out("    : 0 ≤ Form.eval " + form(rule.conclusion) + " K I N M :=")
        out("  check_sound' (r := rule_" + key + ") (c := cert_" + key + ")")
        witness = ", ".join("h" + str(index) for index in range(len(rule.assumptions)))
        witness = ("⟨" + witness + ", trivial⟩") if witness else "trivial"
        out("    (by decide) K I N M " + witness)
        out("")
    out("end Collector")
    out("end Cairn")
    return "\n".join(lines) + "\n"


def generate() -> str:
    pairs = collector_rules()
    for rule, certificate in pairs:
        if not check(rule, certificate):
            raise SystemExit("Refusing to export an unchecked certificate: " + rule.name)
    return render(pairs, audit_collector()["sha256"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="exit nonzero when the file on disk differs from the generated text"
    )
    parser.add_argument("--output", type=Path, default=TARGET, help="destination Lean file")
    arguments = parser.parse_args(argv)

    text = generate()
    destination: Path = arguments.output
    if arguments.check:
        if not destination.exists():
            print("missing generated file: " + str(destination), file=sys.stderr)
            return 1
        current = destination.read_text(encoding="utf-8")
        if current != text:
            print("stale generated file: " + str(destination), file=sys.stderr)
            print("regenerate with: python tools/checks/export_lean_certificates.py", file=sys.stderr)
            return 1
        print("up to date: " + str(destination))
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    print("wrote " + str(destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

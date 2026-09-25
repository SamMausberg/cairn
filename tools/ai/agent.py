#!/usr/bin/env python3
"""Inspect, project and propose checked CAIRN edits. Nothing runs a model implicitly."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(R / "src"))
from cairn.agent.hosts.edits import EditSession, explain, load_json_strict
from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import Diagnostic, compile_source


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["check", "project", "packet", "sites", "apply", "candidates"])
    p.add_argument("source", type=Path)
    p.add_argument("--symbol")
    p.add_argument("--contract", type=Path)
    p.add_argument("--include", action="append", default=[])
    p.add_argument("--scope", choices=["focused", "component"], default="focused")
    p.add_argument("--expand", action="append", default=[], help="Disclose a function or type first (repeatable).")
    p.add_argument("--site")
    p.add_argument("--request", type=Path)
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    source = ""
    try:
        source = a.source.read_text(encoding="utf-8")
        if a.command == "project":
            print(canonical_source(source), end="")
            return 0
        if a.command == "check":
            _, r = compile_source(source)
            print(json.dumps({"status": "typed", "receipt": r}, indent=2))
            return 0
        if not a.symbol:
            p.error("--symbol is required for edit commands")
        contract = load_json_strict(a.contract.read_text()) if a.contract else None
        session = EditSession(source, a.symbol, contract, tuple(a.include), a.scope)
        if a.expand:
            session.expand(a.expand)  # A stateless command replays the disclosures its session had.
        if a.command == "packet":
            result = session.packet(a.site)
        elif a.command == "sites":
            result = list(session.sites.values())
        elif a.command in {"apply", "candidates"}:
            if not a.request:
                p.error("--request is required")
            request = load_json_strict(a.request.read_text())
            if a.command == "candidates":
                if not a.site:
                    p.error("--site is required")
                if not isinstance(request, list):
                    p.error("candidate file must contain an array")
                result = session.candidates(a.site, request)
            else:
                text, result = session.check(request)
                if a.out:
                    if a.out.resolve() == a.source.resolve():
                        p.error("Write a new candidate file; original is read-only.")
                    if a.out.exists():
                        p.error("Output already exists; choose a new candidate path.")
                    a.out.parent.mkdir(parents=True, exist_ok=True)
                    a.out.write_text(text, encoding="utf-8")
                    result["candidate_file"] = str(a.out)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Diagnostic as e:
        print(json.dumps(explain(e, source), indent=2), file=sys.stderr)
        return 1
    except (OSError, UnicodeError, RecursionError, ValueError) as e:
        print(json.dumps({"status": "unknown", "code": "E-RESOURCE-OR-IO", "message": str(e)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

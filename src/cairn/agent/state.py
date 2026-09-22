"""A program's current state in one deterministic object, for an agent to load in place of a growing transcript.

The state names, by module, every function the program's own modules have (the checked instances of a generic
one among them) as `[signature, effect row]`, the types those modules declare, the open diagnostics and the
evidence the caller attaches, such as the edits a host admitted. Its `digest` is the sha256 of the rest of it, so
two states with one digest are the same state. `delta` gives only what changed since an earlier state, which is
how an agent refreshes after an edit without rereading the whole.

A program the checker refuses still has a state: its parsed signatures with effect rows of `None`, which means
unknown and never empty, and the diagnostic that stopped it.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Callable
from typing import Any

from ..compiler.cairnc import VERSION, Diagnostic, Parser, compile_program
from .diagnostics import explain
from .projection import declarations, local, signature

PROTOCOL = "cairn.state/1"
DELTA = "cairn.state-delta/1"


@functools.cache
def builtin() -> frozenset[str]:
    """What every program declares before it says anything, such as the memory orders of an atomic."""
    return frozenset(declarations(compile_program("")[0]))


def own(name: str) -> bool:
    return not name.startswith("std.") and name not in builtin()


def written(f: Any) -> tuple[str, str]:
    """(module, the name as that module writes it): an impl member is Trait.Type.method."""
    if f.owner:
        return f.module, f"{local(f.owner[0])}.{local(f.owner[1].name)}.{local(f.name)}"
    return f.module, f.name.removeprefix(f.module + ".") if f.module else f.name


def sealed(value: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {**value, "digest": hashlib.sha256(body.encode()).hexdigest()}


def state(source: str, evidence: list[Any] | None = None, locate: Callable[[Diagnostic], dict] | None = None) -> dict:
    """The state of `source` now. `evidence` is attached as given; `locate` maps a diagnostic to its file."""
    modules: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    types = ""
    try:
        program, _, receipts = compile_program(source)
        functions = [(f, receipts[f.name]["effects"]) for f in program.functions if own(f.name) and f.name in receipts]
        types = "\n".join(text for name, text in declarations(program).items() if own(name))
    except Diagnostic as error:
        diagnostics.append({**explain(error, source), **(locate(error) if locate else {})})
        try:
            parsed = Parser(source).parse()
        except Diagnostic:
            parsed = None
        functions = [(f, None) for f in (parsed.functions if parsed else [])]
    for f, effects in functions:
        module, name = written(f)
        modules.setdefault(module, {})[name] = [signature(f), effects]
    return sealed({
        "protocol": PROTOCOL,
        "profile": VERSION,
        "status": "rejected" if diagnostics else "typed",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "modules": {m: dict(sorted(fs.items())) for m, fs in sorted(modules.items())},
        "types": types,
        "diagnostics": diagnostics,
        "evidence": list(evidence or []),
    })  # fmt: skip


def delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What changed from `before` to `after`: each changed function (None where one is gone), and the types,
    diagnostics and evidence whenever they differ. Applying it to `before` gives `after`."""
    if before.get("protocol") != PROTOCOL or after.get("protocol") != PROTOCOL:
        raise ValueError("A delta runs between two cairn.state/1 objects.")
    changed: dict[str, dict[str, Any]] = {}
    for module in sorted(set(before["modules"]) | set(after["modules"])):
        old, new = before["modules"].get(module, {}), after["modules"].get(module, {})
        moved = {n: new.get(n) for n in sorted(set(old) | set(new)) if old.get(n) != new.get(n)}
        if moved:
            changed[module] = moved
    same = ("profile", "status", "source_sha256", "types", "diagnostics", "evidence")
    return {
        "protocol": DELTA,
        "since": before["digest"],
        "digest": after["digest"],
        "modules": changed,
        **{k: after[k] for k in same if before[k] != after[k]},
    }


def apply(before: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """`before` with `change` applied, sealed again: the digest must come out as the one the delta names."""
    if change.get("protocol") != DELTA or change.get("since") != before.get("digest"):
        raise ValueError("This delta was taken from another state.")
    modules = {m: dict(fs) for m, fs in before["modules"].items()}
    for module, moved in change["modules"].items():
        for name, entry in moved.items():
            if entry is None:
                modules.get(module, {}).pop(name, None)
            else:
                modules.setdefault(module, {})[name] = entry
    body = {k: v for k, v in before.items() if k != "digest"}
    body.update({k: v for k, v in change.items() if k not in {"protocol", "since", "digest", "modules"}})
    body["modules"] = {m: dict(sorted(fs.items())) for m, fs in sorted(modules.items()) if fs}
    after = sealed(body)
    if after["digest"] != change["digest"]:
        raise ValueError("The delta does not reproduce the state it names.")
    return after

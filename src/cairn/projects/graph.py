"""A project's module graph, as data a build system or a CI job plans with: which file declares into which module,
what each module imports and exports, who imports it, and hashes that change when its text or its interface does."""

from __future__ import annotations

import hashlib
from typing import Any

from ..compiler.cairnc import compile_program, interfaces
from ..compiler.syntax.parser import Parser
from ..version import VERSION
from .project import Project, opened


def graph(project: Project, with_interfaces: bool = False) -> dict[str, Any]:
    """`cairn.graph/1`. Parsing alone gives the files, the modules, their imports and exports and a topological
    order; `with_interfaces` checks the whole program too, and adds each module's `interface_sha256`, the digest of
    its public signatures and effect rows that a dependent's assumptions rest on."""
    p = Parser(project.source).parse()
    files: list[dict[str, Any]] = []
    owners: dict[str, list[Any]] = {}
    current = ""
    for unit, _, text in project.files():
        names = opened(text, current)
        current = names[-1] if names else current
        files.append({"path": unit.path, "sha256": unit.sha256, "modules": names})
        for name in names:
            owners.setdefault(name, []).append(unit)
    imports: dict[str, set[str]] = {m: set() for m in owners}
    for importer, target, _ in p.imports:
        imports.setdefault(importer, set()).add(target)
    exports: dict[str, list[str]] = {}
    for name, module in p.modules.items():
        if name in p.public:
            exports.setdefault(module, []).append(name)
    dependents: dict[str, set[str]] = {m: set() for m in imports}
    for importer, targets in imports.items():
        for target in targets & dependents.keys():
            dependents[target].add(importer)
    modules = {
        m: {
            "files": [u.path for u in owners.get(m, [])],
            "imports": sorted(imports[m]),
            "dependents": sorted(dependents[m]),
            "exports": sorted(exports.get(m, [])),
            "source_sha256": hashlib.sha256("".join(u.sha256 for u in owners.get(m, [])).encode()).hexdigest(),
        }
        for m in imports
    }
    if with_interfaces:
        checked, _, receipts = compile_program(project.source)
        for m, row in interfaces(checked, receipts).items():
            if m in modules:
                modules[m]["interface_sha256"] = row["interface_sha256"]
    order, cyclic = topological(imports)
    return {"schema": "cairn.graph/1", "compiler": VERSION, "project": project.name, "files": files,
            "modules": modules, "order": order, "cyclic": cyclic,
            "libraries": sorted({t for ts in imports.values() for t in ts if t.split(".")[0] == "std"}),
            "dependencies": list(project.dependencies)}  # fmt: skip


def topological(imports: dict[str, set[str]]) -> tuple[list[str], bool]:
    """The project's modules with every module after what it imports, and whether an import cycle kept some out of
    that order (they follow, in name order)."""
    inside = set(imports)
    waiting = {m: len(imports[m] & inside) for m in inside}
    ready = sorted(m for m, n in waiting.items() if n == 0)
    order: list[str] = []
    users: dict[str, list[str]] = {m: [] for m in inside}
    for m in inside:
        for target in imports[m] & inside:
            users[target].append(m)
    while ready:
        m = ready.pop(0)
        order.append(m)
        for user in sorted(users[m]):
            waiting[user] -= 1
            if waiting[user] == 0:
                ready.append(user)
    rest = sorted(inside - set(order))
    return order + rest, bool(rest)


def summary(record: dict[str, Any]) -> str:
    """The graph for a person: one line per module in dependency order."""
    lines = [f"{record['project']}: {len(record['modules'])} modules in {len(record['files'])} files"
             + (", with an import cycle" if record["cyclic"] else "")]  # fmt: skip
    for m in record["order"]:
        row = record["modules"][m]
        where = ", ".join(row["files"]) or "no file"
        uses = ", ".join(row["imports"]) or "nothing"
        lines.append(f"  {m or '(root)'}  [{where}]  imports {uses}")
    return "\n".join(lines) + "\n"

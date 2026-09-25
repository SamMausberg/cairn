"""Code lenses: "Run test" above each `test` block and "Run" above `main`, each naming the command the editor runs
and what it runs on, the document's project when it has one."""

from __future__ import annotations

from .document import Document, declarations
from .workspace import context, path_of


def target_of(uri: str, buffers: dict[str, str]) -> str:
    """What `cairn run` and `cairn test` are given for a document: its project's manifest, else the file itself."""
    held = context(uri, buffers)
    if held is not None:
        return str(held[0].root / "cairn.toml")
    path = path_of(uri)
    return str(path) if path and path.is_file() else ""


def code_lenses(doc: Document, uri: str, buffers: dict[str, str]) -> list[dict]:
    """One lens per test block, naming it as `cairn test --test` does (`sums`, `store.sums` in module store), and
    one for `main`; none for a document that is not a file on disk."""
    where = target_of(uri, buffers)
    if not where:
        return []
    out = []
    for d in declarations(doc.code, 0, len(doc.code)):
        mark = doc.span(*d["mark"])
        if d["detail"] == "test":
            module = doc.module_at(d["head"])
            named = f"{module}.{d['name']}" if module else d["name"]
            run = {"title": "Run test", "command": "cairn.runTest", "arguments": [where, named]}
            out.append({"range": mark, "command": run})
        elif d["detail"] == "fn" and d["name"] == "main":
            out.append({"range": mark, "command": {"title": "Run", "command": "cairn.run", "arguments": [where]}})
    return out

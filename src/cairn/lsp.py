"""A Language Server Protocol server for CAIRN: stdio JSON-RPC, standard library only.

One open buffer is analysed at a time; `std.*` imports are linked by the compiler
itself. Positions are UTF-16 code units, as the protocol requires. A buffer that
does not compile yields diagnostics, never an exception: the server answers every
request it accepted and stays up.
"""

from __future__ import annotations

import bisect
import json
import sys
from typing import Any, BinaryIO

from .agent_tools import explain
from .cairnc import Diagnostic, compile_program
from .formatting import Item, format_source, roles, scan
from .syntax import IDENT, RESERVED
from .version import VERSION

DECLARATIONS = {"fn": 12, "struct": 23, "enum": 10, "trait": 11, "const": 14, "impl": 5}
MODIFIERS = {"pub", "linear", "extern"}
CAPABILITIES = {
    "positionEncoding": "utf-16",
    "textDocumentSync": {"openClose": True, "change": 1},
    "hoverProvider": True,
    "documentSymbolProvider": True,
    "documentFormattingProvider": True,
    "definitionProvider": True,
}
IGNORED = {"initialized", "$/cancelRequest", "$/setTrace", "workspace/didChangeConfiguration"}
UNSUPPORTED: Any = object()


# Framing and positions ---------------------------------------------------------------------------


def read_message(stream: BinaryIO) -> dict | None:
    """One `Content-Length` framed JSON-RPC message, or None at end of input."""
    length = 0
    while True:
        line = stream.readline()
        if not line:
            return None
        if not line.strip():
            break
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip() or b"0")
    body = stream.read(length) if length > 0 else b""
    return json.loads(body) if body else None


def write_message(stream: BinaryIO, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    stream.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    stream.flush()


def line_starts(text: str) -> list[int]:
    out, i = [0], text.find("\n")
    while i >= 0:
        out.append(i + 1)
        i = text.find("\n", i + 1)
    return out


def _units(s: str) -> int:
    """UTF-16 code units in `s`; astral characters take two."""
    return len(s) + sum(ord(c) > 0xFFFF for c in s)


class Document:
    """One open buffer with its scan, its checker sites and its diagnostics."""

    def __init__(self, text: str):
        self.text = text
        self.starts = line_starts(text)
        self.code = roles([t for t in scan(text) if not t.comment])
        self.sites, self.diagnostics = self._analyse()

    def position(self, offset: int) -> dict:
        offset = max(0, min(offset, len(self.text)))
        line = bisect.bisect_right(self.starts, offset) - 1
        return {"line": line, "character": _units(self.text[self.starts[line] : offset])}

    def offset(self, position: dict) -> int:
        line = max(0, min(int(position.get("line", 0)), len(self.starts) - 1))
        stop = self.starts[line + 1] - 1 if line + 1 < len(self.starts) else len(self.text)
        want, i = int(position.get("character", 0)), self.starts[line]
        while i < stop and want > 0:
            want -= 2 if ord(self.text[i]) > 0xFFFF else 1
            i += 1
        return i

    def span(self, start: int, end: int) -> dict:
        return {"start": self.position(start), "end": self.position(end)}

    def _analyse(self) -> tuple[list[dict], list[dict]]:
        try:
            program, checker, _ = compile_program(self.text, capture_sites=True)
        except Diagnostic as error:
            return [], [self._report(error)]
        except Exception as error:  # A compiler failure is reported, never raised at the client.
            zero = {"line": 0, "character": 0}
            return [], [
                {
                    "range": {"start": zero, "end": zero},
                    "severity": 1,
                    "source": "cairn",
                    "code": "E-INTERNAL",
                    "message": f"{type(error).__name__}: {error}",
                }
            ]
        linked = tuple(module + "." for module in program.sources)
        sites = [
            s
            for s in checker.sites
            if s["end"] > s["start"] >= 0 and s["end"] <= len(self.text) and not s["symbol"].startswith(linked)
        ]
        return sites, []

    def _report(self, error: Diagnostic) -> dict:
        d = explain(error, self.text)
        line, column = int(d.get("line") or 0), int(d.get("column") or 0)
        start = end = 0
        if line > 0:
            start = min(self.starts[min(line, len(self.starts)) - 1] + max(column - 1, 0), len(self.text))
            end = next((t.end for t in self.code if t.start == start), start)
        hint = d.get("repair_hint")
        return {
            "range": self.span(start, end),
            "severity": 1,
            "source": "cairn",
            "code": d["code"],
            "message": d["message"] + ("\n" + hint if hint else ""),
            "data": {k: d[k] for k in ("code", "repair_hint", "source_line") if k in d},
        }


# Language features -------------------------------------------------------------------------------


def declarations(cs: list[Item], lo: int, hi: int) -> list[dict]:
    """Top-level declarations in `cs[lo:hi]`, with trait and impl members as children."""
    out: list[dict] = []
    i = head = lo
    while i < hi:
        if cs[i].s in MODIFIERS:
            i += 1
            continue
        if cs[i].s not in DECLARATIONS:
            i += 1
            head = i
            continue
        j = i
        while j < hi and cs[j].s not in {";", "{"}:
            j += 1
        body = j if j < hi and cs[j].s == "{" and cs[j].pair > j else -1
        end = cs[j].pair if body >= 0 else min(j, hi - 1)
        named = cs[i + 1] if i + 1 < hi else cs[i]
        out.append(
            {
                "name": " ".join(t.s for t in cs[i + 1 : body]) if cs[i].s == "impl" else named.s,
                "kind": DECLARATIONS[cs[i].s],
                "detail": cs[i].s,
                "head": cs[head].start,
                "tail": cs[end].end,
                "mark": (named.start, named.end) if cs[i].s != "impl" else (cs[i].start, cs[i].end),
                "children": declarations(cs, body + 1, end) if body >= 0 and cs[i].s in {"trait", "impl"} else [],
            }
        )
        i = head = end + 1
    return out


def symbols(doc: Document) -> list[dict]:
    def shape(d: dict) -> dict:
        return {
            "name": d["name"],
            "kind": d["kind"],
            "detail": d["detail"],
            "range": doc.span(d["head"], d["tail"]),
            "selectionRange": doc.span(*d["mark"]),
            "children": [shape(c) for c in d["children"]],
        }

    return [shape(d) for d in declarations(doc.code, 0, len(doc.code))]


def flatten(ds: list[dict]) -> list[dict]:
    return [x for d in ds for x in [d, *flatten(d["children"])]]


def hover(doc: Document, offset: int) -> dict | None:
    """The smallest checked expression covering `offset`, with its type and binding."""
    best: dict | None = None
    for s in doc.sites:
        if s["start"] <= offset < s["end"] and (best is None or s["end"] - s["start"] < best["end"] - best["start"]):
            best = s
    if best is None:
        return None
    source = doc.text[best["start"] : best["end"]]
    body = ["```cairn", source, "```", "", "type `" + best["type"] + "`"]
    if best.get("expected_type"):
        body[-1] += ", expected `" + best["expected_type"] + "`"
    binding = best["bindings"].get(source) if best["tag"] == "name" else None
    if binding:
        body += ["", ("mutable" if binding["mutable"] else "immutable") + " binding of `" + binding["type"] + "`"]
    return {"contents": {"kind": "markdown", "value": "\n".join(body)}, "range": doc.span(best["start"], best["end"])}


def definition(doc: Document, uri: str, offset: int) -> dict | None:
    """A declaration of the identifier under the cursor, in this document only."""
    word = next((t for t in doc.code if t.start <= offset < t.end and IDENT.fullmatch(t.s)), None)
    if word is None or word.s in RESERVED:
        return None
    for d in flatten(declarations(doc.code, 0, len(doc.code))):
        if d["name"] == word.s and d["mark"][0] != word.start:
            return {"uri": uri, "range": doc.span(*d["mark"])}
    return None


# Server ------------------------------------------------------------------------------------------


class Server:
    def __init__(self, source: BinaryIO, sink: BinaryIO):
        self.source, self.sink = source, sink
        self.docs: dict[str, Document] = {}
        self.stopping = False

    def send(self, payload: dict) -> None:
        write_message(self.sink, {"jsonrpc": "2.0", **payload})

    def refresh(self, uri: str, text: str) -> None:
        self.docs[uri] = doc = Document(text)
        self.send({"method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": doc.diagnostics}})

    def handle(self, method: str, p: dict) -> Any:
        if method == "initialize":
            return {"capabilities": CAPABILITIES, "serverInfo": {"name": "cairn-lsp", "version": VERSION}}
        if method == "shutdown":
            self.stopping = True
            return None
        if method in IGNORED:
            return None
        uri = (p.get("textDocument") or {}).get("uri", "")
        if method == "textDocument/didOpen":
            self.refresh(uri, (p.get("textDocument") or {}).get("text", ""))
            return None
        if method == "textDocument/didChange":
            changes = p.get("contentChanges") or [{}]
            self.refresh(uri, changes[-1].get("text", self.docs[uri].text if uri in self.docs else ""))
            return None
        if method == "textDocument/didClose":
            self.docs.pop(uri, None)
            self.send({"method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": []}})
            return None
        doc = self.docs.get(uri)
        if doc is None:
            return None if method.startswith("textDocument/") else UNSUPPORTED
        if method == "textDocument/hover":
            return hover(doc, doc.offset(p.get("position") or {}))
        if method == "textDocument/definition":
            return definition(doc, uri, doc.offset(p.get("position") or {}))
        if method == "textDocument/documentSymbol":
            return symbols(doc)
        if method == "textDocument/formatting":
            out = format_source(doc.text)
            return [] if out == doc.text else [{"range": doc.span(0, len(doc.text)), "newText": out}]
        return UNSUPPORTED

    def dispatch(self, message: dict) -> bool:
        """Answer one message; True when the server must exit."""
        method, request = str(message.get("method") or ""), message.get("id")
        if method == "exit":
            return True
        try:
            result = self.handle(method, message.get("params") or {})
        except Exception as error:  # Robustness over strictness: a bad request never kills the server.
            if request is not None:
                self.send({"id": request, "error": {"code": -32603, "message": f"{type(error).__name__}: {error}"}})
            return False
        if request is None:
            return False
        if result is UNSUPPORTED:
            self.send({"id": request, "error": {"code": -32601, "message": "Unsupported method " + method}})
        else:
            self.send({"id": request, "result": result})
        return False

    def run(self) -> int:
        while True:
            try:
                message = read_message(self.source)
            except ValueError:  # A malformed frame is skipped, not fatal.
                continue
            if message is None:
                return 0 if self.stopping else 1
            if isinstance(message, dict) and self.dispatch(message):
                return 0


def serve() -> int:
    """`cairn lsp`: speak LSP over stdin/stdout until the client sends `exit`."""
    return Server(sys.stdin.buffer, sys.stdout.buffer).run()

"""A Language Server Protocol server for CAIRN: stdio JSON-RPC, standard library only.

One open buffer is analysed at a time; `std.*` imports are linked by the compiler itself. A buffer that
does not compile yields diagnostics, never an exception: the server answers every request it accepted and
stays up. This file is the transport and the dispatch; `document.py` is the buffer, and each feature group
has a module of its own.
"""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO

from ..version import VERSION
from .completion import completion, signature_help
from .document import Document, symbols
from .edits import formatted, prepare_rename, references, rename
from .navigation import definition, hover

CAPABILITIES = {
    "positionEncoding": "utf-16",
    "textDocumentSync": {"openClose": True, "change": 1},
    "hoverProvider": True,
    "documentSymbolProvider": True,
    "documentFormattingProvider": True,
    "definitionProvider": True,
    "completionProvider": {"triggerCharacters": ["."]},
    "signatureHelpProvider": {"triggerCharacters": ["(", ","]},
    "referencesProvider": True,
    "renameProvider": {"prepareProvider": True},
}
IGNORED = {"initialized", "$/cancelRequest", "$/setTrace", "workspace/didChangeConfiguration"}
UNSUPPORTED: Any = object()

# Framing -----------------------------------------------------------------------------------------


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


# Dispatch ----------------------------------------------------------------------------------------


# What each request asks of an open document: (document, its uri, the cursor's offset, the request's params).
ANSWERS: dict[str, Any] = {
    "textDocument/hover": lambda d, u, at, p: hover(d, at),
    "textDocument/definition": lambda d, u, at, p: definition(d, u, at),
    "textDocument/completion": lambda d, u, at, p: completion(d, at),
    "textDocument/signatureHelp": lambda d, u, at, p: signature_help(d, at),
    "textDocument/references": lambda d, u, at, p: references(d, u, at),
    "textDocument/prepareRename": lambda d, u, at, p: prepare_rename(d, at),
    "textDocument/rename": lambda d, u, at, p: rename(d, u, at, str(p.get("newName") or "")),
    "textDocument/documentSymbol": lambda d, u, at, p: symbols(d),
    "textDocument/formatting": lambda d, u, at, p: formatted(d),
}


class Server:
    def __init__(self, source: BinaryIO, sink: BinaryIO):
        self.source, self.sink = source, sink
        self.docs: dict[str, Document] = {}
        self.stopping = False

    def send(self, payload: dict) -> None:
        write_message(self.sink, {"jsonrpc": "2.0", **payload})

    def refresh(self, uri: str, text: str) -> None:
        self.docs[uri] = doc = Document(text, self.docs.get(uri))
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
        answer = ANSWERS.get(method)
        return answer(doc, uri, doc.offset(p.get("position") or {}), p) if answer else UNSUPPORTED

    def dispatch(self, message: dict) -> bool:
        """Answer one message; True when the server must exit."""
        method, request = str(message.get("method") or ""), message.get("id")
        if method == "exit":
            return True
        try:
            result = self.handle(method, message.get("params") or {})
        except Exception as error:  # Robustness over strictness: a bad request never kills the server.
            refused = isinstance(error, ValueError)  # A refused rename is a bad request, not a failure.
            if request is not None:
                code, said = (-32602, str(error)) if refused else (-32603, f"{type(error).__name__}: {error}")
                self.send({"id": request, "error": {"code": code, "message": said}})
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

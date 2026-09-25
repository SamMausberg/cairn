"""A Language Server Protocol server for CAIRN: stdio JSON-RPC, standard library only.

One open buffer is analysed at a time; `std.*` imports are linked by the compiler itself. A buffer that
does not compile yields diagnostics, never an exception: the server answers every request it accepted and
stays up. This file is the transport and the dispatch; `document.py` is the buffer, and each feature group
has a module of its own.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from typing import Any, BinaryIO, NoReturn

from ...version import VERSION
from .completion import completion, signature_help
from .document import Document, symbols
from .edits import document_highlights, formatted, highlights, prepare_rename, references, rename
from .fixes import code_actions
from .highlighting import LEGEND, semantic_tokens
from .hints import inlay_hints
from .lenses import code_lenses
from .navigation import definition, hover
from .workspace import context, within, workspace, workspace_symbols
from .workspace import definition as project_definition
from .workspace import prepare_rename as project_prepare_rename
from .workspace import references as project_references
from .workspace import rename as project_rename

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
    "semanticTokensProvider": {"legend": LEGEND, "full": True},
    "inlayHintProvider": True,
    "codeActionProvider": {"codeActionKinds": ["quickfix"]},
    "codeLensProvider": {"resolveProvider": False},
    "documentHighlightProvider": True,
    "workspaceSymbolProvider": True,
}
# The same questions when the document belongs to a project: answered across every file of it.
ACROSS: dict[str, Any] = {
    "textDocument/definition": lambda w, u, at, p: project_definition(w, u, at),
    "textDocument/references": lambda w, u, at, p: project_references(w, u, at),
    "textDocument/prepareRename": lambda w, u, at, p: project_prepare_rename(w, u, at),
    "textDocument/rename": lambda w, u, at, p: project_rename(w, u, at, str(p.get("newName") or "")),
    "textDocument/documentHighlight": lambda w, u, at, p: [r for r in project_references(w, u, at) if r["uri"] == u],
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
    "textDocument/semanticTokens/full": lambda d, u, at, p: semantic_tokens(d),
    "textDocument/inlayHint": lambda d, u, at, p: inlay_hints(d, p.get("range")),
    "textDocument/codeAction": lambda d, u, at, p: code_actions(d, u, p.get("range") or {}),
    "textDocument/documentHighlight": lambda d, u, at, p: document_highlights(d, at),
}


class Server:
    def __init__(self, source: BinaryIO, sink: BinaryIO):
        self.source, self.sink = source, sink
        self.docs: dict[str, Document] = {}
        self.roots: list[str] = []  # the folders `initialize` named, for workspace symbols
        self.stopping = False

    def send(self, payload: dict) -> None:
        write_message(self.sink, {"jsonrpc": "2.0", **payload})

    def buffers(self) -> dict[str, str]:
        """Every open document's text, by uri: what a project is read with in place of its files on disk."""
        return {u: d.text for u, d in self.docs.items()}

    def refresh(self, uri: str, text: str) -> None:
        """Analyse the buffer, within its project when it has one; every open file of that project is analysed
        again too, since an edit to one file can refuse or admit another."""
        buffers = self.buffers() | {uri: text}
        held = context(uri, buffers)
        again = [u for u in self.docs if u != uri and held and within(*held, u)] if held else []
        for u in [uri, *again]:
            inside = within(*held, u) if held else None
            self.docs[u] = doc = Document(buffers[u], self.docs.get(u), within=inside)
            self.send(
                {"method": "textDocument/publishDiagnostics", "params": {"uri": u, "diagnostics": doc.diagnostics}}
            )

    def handle(self, method: str, p: dict) -> Any:
        if method == "initialize":
            folders = [f.get("uri", "") for f in p.get("workspaceFolders") or [] if isinstance(f, dict)]
            self.roots = [u for u in [*folders, str(p.get("rootUri") or "")] if u]
            return {"capabilities": CAPABILITIES, "serverInfo": {"name": "cairn-lsp", "version": VERSION}}
        if method == "shutdown":
            self.stopping = True
            return None
        if method in IGNORED:
            return None
        if method == "workspace/symbol":
            return workspace_symbols(str(p.get("query") or ""), self.buffers(), self.roots)
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
        at, buffers = doc.offset(p.get("position") or {}), self.buffers()
        if method == "textDocument/codeLens":
            return code_lenses(doc, uri, buffers)
        if method in ACROSS and (ws := workspace(uri, buffers)) is not None:
            answer = ACROSS[method](ws, uri, at, p)
            if method == "textDocument/documentHighlight":  # the project's references in this file, read or write
                return highlights(doc, {doc.offset(r["range"]["start"]) for r in answer})
            if answer is not None or method != "textDocument/definition":  # a local or a library name: this file's
                return answer
        answer = ANSWERS.get(method)
        return answer(doc, uri, at, p) if answer else UNSUPPORTED

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
        """Read on one thread and answer on this one. What arrived while an analysis ran is answered in order, but
        a run of whole-text changes to one file is analysed once, at its last text: a large project's analysis takes
        seconds, and a burst of keystrokes would otherwise queue one for each."""
        inbox: queue.Queue = queue.Queue()
        threading.Thread(target=self.pump, args=(inbox,), daemon=True).start()
        while True:
            batch = [inbox.get()]
            while not inbox.empty():
                batch.append(inbox.get_nowait())
            for message in latest(batch):
                if message is None:
                    return 0 if self.stopping else 1
                if isinstance(message, dict) and self.dispatch(message):
                    return 0

    def pump(self, inbox: queue.Queue) -> None:
        while True:
            try:
                message = read_message(self.source)
            except ValueError:  # A malformed frame is skipped, not fatal.
                continue
            except Exception:  # A broken input ends the session as its end would, never a silent wait.
                message = None
            inbox.put(message)
            if message is None:
                return


def latest(batch: list[Any]) -> list[Any]:
    """`batch` in order, less each didChange that the very next message replaces: a later didChange of the same
    document. The server syncs whole texts, so the later change carries everything the earlier one did."""
    uri = lambda m: (m.get("params") or {}).get("textDocument", {}).get("uri")  # noqa: E731
    change = lambda m: isinstance(m, dict) and m.get("method") == "textDocument/didChange"  # noqa: E731
    return [m for m, after in zip(batch, [*batch[1:], None], strict=True)
            if not (change(m) and change(after) and uri(m) == uri(after))]  # fmt: skip


def serve() -> NoReturn:
    """`cairn lsp`: speak LSP over stdin/stdout until the client sends `exit`. The process ends here, since the
    reading thread may still wait on stdin, and an interpreter shutdown would abort on that buffer's lock."""
    code = Server(sys.stdin.buffer, sys.stdout.buffer).run()
    sys.stdout.flush()
    os._exit(code)

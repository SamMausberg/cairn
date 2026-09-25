"""`cairn mcp`: the compiler's hosts as a Model Context Protocol server over stdio, standard library only.

Each message is one line of JSON-RPC 2.0 in UTF-8, in both directions. The server answers `initialize` with the
client's protocol version when it speaks it and otherwise with its newest, `ping`, `tools/list` and `tools/call`, and
takes `notifications/initialized` and every other notification without an answer. A request it cannot read is a
JSON-RPC error (-32700 unreadable JSON, -32600 not a request, -32601 an unknown method, -32602 an unknown tool or
arguments that are not an object). A tool that refuses answers with a result whose `isError` is true and whose text is
the CAIRN diagnostic record (`tools.py`), so the agent reads the code, the line and the fix. Requests are answered
one at a time, in order.

Standard output carries only the protocol. The server keeps its own copy of the descriptors it was started with and
points 1 at standard error and 0 at /dev/null, so a compiler, a native build or a test program it starts can neither
write into the protocol nor read from it.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from typing import IO, Any

from ...version import __version__
from .tools import TOOLS, Tools

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # newest first
PARSE, INVALID, METHOD, PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603


def error(ident: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


class Server:
    """One MCP session: the negotiated protocol version and the tools' hosts."""

    def __init__(self, tools: Tools | None = None):
        self.tools = tools or Tools()
        self.version: str | None = None

    def handle(self, message: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
        """The answer to one message or batch, or None when nothing is owed: a notification, or a response."""
        if isinstance(message, list):
            if not message:
                return error(None, INVALID, "An empty batch is not a request.")
            answers = [a for m in message if isinstance(a := self.handle(m), dict)]
            return answers or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return error(
                message.get("id") if isinstance(message, dict) else None, INVALID, "Not a JSON-RPC 2.0 message."
            )
        if "method" not in message:
            return None  # a response to a request this server never sends
        ident, method, params = message.get("id"), message["method"], message.get("params", {})
        if "id" not in message:
            return None  # a notification: initialized, cancelled, or one this server does not use
        if not isinstance(method, str) or not isinstance(params, dict):
            return error(ident, INVALID, "method is a string and params an object.")
        try:
            if method == "initialize":
                return {"jsonrpc": "2.0", "id": ident, "result": self.initialize(params)}
            if method == "ping":
                return {"jsonrpc": "2.0", "id": ident, "result": {}}
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": ident, "result": {"tools": TOOLS}}
            if method == "tools/call":
                return self.call(ident, params)
        except Exception as e:  # a fault of this server, never of the request: answered, and the session goes on
            return error(ident, INTERNAL, f"{type(e).__name__}: {e}")
        return error(ident, METHOD, f"Unknown method {method}.")

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        asked = params.get("protocolVersion")
        self.version = asked if asked in PROTOCOLS else PROTOCOLS[0]
        return {
            "protocolVersion": self.version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "cairn", "version": __version__},
        }

    def call(self, ident: Any, params: dict[str, Any]) -> dict[str, Any]:
        name, arguments = params.get("name"), params.get("arguments", {})
        if name not in {t["name"] for t in TOOLS}:
            return error(ident, PARAMS, f"Unknown tool {name}; the tools are {', '.join(t['name'] for t in TOOLS)}.")
        if not isinstance(arguments, dict):
            return error(ident, PARAMS, "arguments is an object.")
        record, failed = self.tools.call(name, arguments)
        text = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return {
            "jsonrpc": "2.0",
            "id": ident,
            "result": {"content": [{"type": "text", "text": text}], "isError": failed},
        }

    def run(self, lines: Iterable[bytes], out: IO[bytes]) -> int:
        """Answer each line of `lines` on `out` until the input ends."""
        for line in lines:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError) as e:
                answer: Any = error(None, PARSE, f"Unreadable JSON: {e}")
            else:
                answer = self.handle(message)
            if answer is not None:
                out.write(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
                out.flush()
        return 0


def serve() -> int:
    """`cairn mcp`: speak MCP on the standard streams until the client closes standard input."""
    wire_in = os.fdopen(os.dup(0), "rb")
    wire_out = os.fdopen(os.dup(1), "wb")
    quiet = os.open(os.devnull, os.O_RDONLY)
    os.dup2(quiet, 0)
    os.close(quiet)
    sys.stdout.flush()
    os.dup2(2, 1)  # what anything else writes to standard output reaches standard error, never the protocol
    return Server().run(wire_in, wire_out)

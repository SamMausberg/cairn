"""`cairn lsp`: a real subprocess driven through the protocol, plus the editor assets.

Every read has a deadline, so a server that hangs fails the suite instead of it.
"""

import json
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cairn.lsp import Document, definition, hover, symbols
from cairn.syntax import RESERVED

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "editors" / "vscode"
URI = "file:///workspace/module.cairn"
TIMEOUT = 30

BROKEN = "fn f() -> u64 {\n  let x = missing_name;\n  return x;\n}\n"
FIXED = """// A checked average that cannot overflow its intermediate sum.
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);

struct Pair { a:u64; b:u64; }

enum Op { Read; Write; }

const LIMIT:usize = 8;

trait Shape { fn area(self:ro<Self>) -> u64; }

fn main() -> i32 {
  let mut total:u64 = 0;
  total = average(10, 20);
  if total == 15 { return 0; }
  return 1;
}
"""


class Client:
    """A minimal LSP client: frames out, frames in on a reader thread with a deadline."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "bin" / "cairn"), "lsp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.inbox: queue.Queue = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                length = 0
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        return
                    if not line.strip():
                        break
                    name, _, value = line.partition(b":")
                    if name.strip().lower() == b"content-length":
                        length = int(value.strip())
                self.inbox.put(json.loads(self.proc.stdout.read(length)))
        finally:
            self.inbox.put(None)

    def send(self, method, params=None, request=None):
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if request is not None:
            message["id"] = request
        body = json.dumps(message).encode()
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
        self.proc.stdin.flush()

    def take(self, match):
        """The next message satisfying `match`, or a failure before the deadline."""
        while True:
            try:
                message = self.inbox.get(timeout=TIMEOUT)
            except queue.Empty:
                self.proc.kill()
                pytest.fail(f"the server did not answer within {TIMEOUT}s")
            if message is None:
                self.proc.kill()
                pytest.fail("the server closed its output: " + self.proc.stderr.read().decode()[:2000])
            if match(message):
                return message

    def response(self, request):
        return self.take(lambda m: m.get("id") == request)

    def diagnostics(self, uri=URI):
        method = "textDocument/publishDiagnostics"
        got = self.take(lambda m: m.get("method") == method and m["params"]["uri"] == uri)
        return got["params"]["diagnostics"]

    def request(self, method, params, request):
        self.send(method, params, request)
        return self.response(request)

    def open(self, text, uri=URI):
        self.send("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "cairn", "text": text}})
        return self.diagnostics(uri)

    def change(self, text, uri=URI):
        self.send("textDocument/didChange", {"textDocument": {"uri": uri}, "contentChanges": [{"text": text}]})
        return self.diagnostics(uri)

    def close(self):
        self.proc.kill()
        self.proc.wait(timeout=TIMEOUT)


@pytest.fixture
def client():
    c = Client()
    try:
        yield c
    finally:
        if c.proc.poll() is None:
            c.close()


def place(text, needle, offset=0):
    at = text.index(needle) + offset
    return {"line": text[:at].count("\n"), "character": at - text.rfind("\n", 0, at) - 1}


def test_a_whole_session(client):
    initialized = client.request("initialize", {"processId": None, "capabilities": {}}, 1)
    capabilities = initialized["result"]["capabilities"]
    assert capabilities["positionEncoding"] == "utf-16"
    assert capabilities["textDocumentSync"]["change"] == 1
    assert all(capabilities[k] for k in ("hoverProvider", "documentSymbolProvider", "documentFormattingProvider"))
    client.send("initialized")

    reported = client.open(BROKEN)
    assert len(reported) == 1
    assert reported[0]["code"] == "E-UNBOUND"
    assert reported[0]["range"] == {"start": {"line": 1, "character": 10}, "end": {"line": 1, "character": 22}}
    assert "missing_name" in reported[0]["message"]
    assert reported[0]["data"]["repair_hint"]

    assert client.change(FIXED) == []

    hovered = client.request(
        "textDocument/hover", {"textDocument": {"uri": URI}, "position": place(FIXED, "average(10, 20)", 2)}, 2
    )
    assert "u64" in hovered["result"]["contents"]["value"]

    outline = client.request("textDocument/documentSymbol", {"textDocument": {"uri": URI}}, 3)
    names = {entry["name"] for entry in outline["result"]}
    assert {"average", "Pair", "Op", "LIMIT", "Shape", "main"} <= names
    assert [e["kind"] for e in outline["result"] if e["name"] == "Pair"] == [23]

    gone_to = client.request(
        "textDocument/definition", {"textDocument": {"uri": URI}, "position": place(FIXED, "average(10, 20)", 2)}, 4
    )
    assert gone_to["result"]["range"]["start"] == place(FIXED, "average(x:u64")

    edits = client.request("textDocument/formatting", {"textDocument": {"uri": URI}, "options": {}}, 5)
    assert edits["result"] == []  # FIXED is already formatted

    assert client.request("shutdown", {}, 6)["result"] is None
    client.send("exit")
    assert client.proc.wait(timeout=TIMEOUT) == 0


def test_formatting_returns_one_whole_document_edit(client):
    client.request("initialize", {"capabilities": {}}, 1)
    ugly = "fn f(a:u64,b:u64)->u64{return a+b;}\n"
    client.open(ugly)
    edits = client.request("textDocument/formatting", {"textDocument": {"uri": URI}, "options": {}}, 2)
    assert len(edits["result"]) == 1
    assert edits["result"][0]["newText"] == "fn f(a:u64, b:u64) -> u64 { return a + b; }\n"
    assert edits["result"][0]["range"] == {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 0}}


def test_positions_use_utf16_code_units(client):
    client.request("initialize", {"capabilities": {}}, 1)
    text = "// \U0001f600 \u00e9\u00e9\u00e9\nfn f() -> u64 {\n  let x = nope;\n  return x;\n}\n"
    reported = client.open(text)
    assert reported[0]["code"] == "E-UNBOUND"
    assert reported[0]["range"] == {"start": {"line": 2, "character": 10}, "end": {"line": 2, "character": 14}}
    # A hover on the astral comment line must round-trip through UTF-16 without an exception.
    hovered = client.request(
        "textDocument/hover", {"textDocument": {"uri": URI}, "position": {"line": 0, "character": 5}}, 2
    )
    assert hovered["result"] is None


def test_the_server_survives_nonsense(client):
    client.request("initialize", {"capabilities": {}}, 1)
    for text in ("", "}{;;;", "fn fn fn", "\u00e9\u00e9\u00e9", "fn f() { let x = $; }"):
        reported = client.open(text, URI)
        assert isinstance(reported, list)
        for method in ("hover", "definition", "documentSymbol"):
            answer = client.request(
                f"textDocument/{method}",
                {"textDocument": {"uri": URI}, "position": {"line": 0, "character": 1}},
                hash(text + method) % 10000 + 100,
            )
            assert "error" not in answer
    unknown = client.request("textDocument/willSaveWaitUntil", {"textDocument": {"uri": URI}}, 2)
    assert unknown["error"]["code"] == -32601
    assert client.request("shutdown", {}, 3)["result"] is None
    client.send("exit")
    assert client.proc.wait(timeout=TIMEOUT) == 0


def test_closing_a_document_clears_its_diagnostics(client):
    client.request("initialize", {"capabilities": {}}, 1)
    assert client.open(BROKEN)
    client.send("textDocument/didClose", {"textDocument": {"uri": URI}})
    assert client.diagnostics() == []


def test_exit_without_shutdown_is_a_failure(client):
    client.request("initialize", {"capabilities": {}}, 1)
    client.proc.stdin.close()
    assert client.proc.wait(timeout=TIMEOUT) == 1


# The language features, in process ----------------------------------------------------------------


def test_hover_reports_the_smallest_covering_expression():
    doc = Document(FIXED)
    at = FIXED.index("x & y") + 4
    answer = hover(doc, at)
    assert answer["contents"]["value"].splitlines()[1] == "y"
    assert "immutable binding of `u64`" in answer["contents"]["value"]


def test_hover_reports_mutability():
    text = "fn f() -> u64 {\n  let mut total:u64 = 0;\n  total = total + 1;\n  return total;\n}\n"
    answer = hover(Document(text), text.index("return total") + 7)
    assert "mutable binding of `u64`" in answer["contents"]["value"]


def test_hover_reports_an_expected_type():
    text = "fn f(n:usize) -> usize { return n; }\nfn g() -> usize { return f(3); }\n"
    answer = hover(Document(text), text.index("f(3)") + 2)
    assert "expected `usize`" in answer["contents"]["value"]


def test_hover_outside_any_expression_is_empty():
    assert hover(Document(FIXED), 0) is None


def test_symbols_cover_every_declaration_kind():
    doc = Document(FIXED)
    found = {s["name"]: s for s in symbols(doc)}
    assert set(found) == {"average", "Pair", "Op", "LIMIT", "Shape", "main"}
    for entry in found.values():
        assert entry["range"]["start"]["line"] <= entry["selectionRange"]["start"]["line"]
    assert found["main"]["range"]["end"]["line"] == FIXED.count("\n") - 1


def test_symbols_nest_trait_and_impl_members():
    text = "trait Shape { fn area(self:ro<Self>) -> u64; }\nimpl Shape for Square { fn area(self:ro<Square>) -> u64 = 1; }\n"
    found = symbols(Document(text))
    assert [s["name"] for s in found] == ["Shape", "Shape for Square"]
    assert [[c["name"] for c in s["children"]] for s in found] == [["area"], ["area"]]


def test_definition_finds_a_declaration_in_the_same_document():
    doc = Document(FIXED)
    at = definition(doc, URI, FIXED.index("average(10, 20)") + 2)
    assert at["range"]["start"] == doc.position(FIXED.index("average(x:u64"))
    assert definition(doc, URI, FIXED.index("fn main")) is None  # a keyword is not a name


def test_a_document_that_does_not_compile_still_has_symbols():
    doc = Document("fn a() -> u64 { return nope; }\nstruct B { x:u64; }\n")
    assert [s["name"] for s in symbols(doc)] == ["a", "B"]
    assert [d["code"] for d in doc.diagnostics] == ["E-UNBOUND"]


def test_std_imports_do_not_leak_sites_from_other_files():
    doc = Document("import std.core (Option);\nfn f() -> Option[u64] { return Option.Some(1); }\n")
    assert doc.diagnostics == []
    assert all(site["end"] <= len(doc.text) for site in doc.sites)
    assert all(not site["symbol"].startswith("std.") for site in doc.sites)


# Editor assets -------------------------------------------------------------------------------------


def grammar():
    return json.loads((EDITOR / "syntaxes" / "cairn.tmLanguage.json").read_text(encoding="utf-8"))


def test_the_grammar_keywords_are_exactly_the_reserved_words():
    words = set()
    for pattern in grammar()["repository"]["keywords"]["patterns"]:
        matched = re.fullmatch(r"\\b\(\?:([a-z|]+)\)\\b", pattern["match"])
        assert matched, f"unexpected keyword pattern shape: {pattern['match']}"
        group = set(matched.group(1).split("|"))
        assert not group & words, "a keyword is listed twice"
        words |= group
    assert words == RESERVED


def test_the_grammar_covers_the_other_lexical_classes():
    repository = grammar()["repository"]
    assert set(repository) >= {"comments", "strings", "numbers", "types", "builtins", "placements", "effects"}
    included = [p["include"].lstrip("#") for p in grammar()["patterns"]]
    assert set(included) <= set(repository)
    assert included.index("comments") == 0 and included.index("placements") < included.index("keywords")


def test_the_extension_manifest_points_at_files_that_exist():
    manifest = json.loads((EDITOR / "package.json").read_text(encoding="utf-8"))
    language = manifest["contributes"]["languages"][0]
    assert language["extensions"] == [".cairn"]
    for relative in (manifest["main"], language["configuration"], manifest["contributes"]["grammars"][0]["path"]):
        assert (EDITOR / relative).is_file(), relative
    assert manifest["activationEvents"] == ["onLanguage:cairn"]
    assert set(manifest["dependencies"]) == {"vscode-languageclient"}
    assert not (EDITOR / "node_modules").exists(), "node_modules must not be vendored"


def test_the_language_configuration_is_valid():
    configuration = json.loads((EDITOR / "language-configuration.json").read_text(encoding="utf-8"))
    assert configuration["comments"]["lineComment"] == "//"
    assert [pair[0] for pair in configuration["brackets"]] == ["{", "[", "("]
    assert {pair["open"] for pair in configuration["autoClosingPairs"]} == {"{", "[", "(", '"', "'"}


def test_the_client_starts_the_server_over_stdio():
    client = (EDITOR / "client.js").read_text(encoding="utf-8")
    assert "vscode-languageclient/node" in client
    assert "TransportKind.stdio" in client
    assert 'settings.get("server.arguments", ["lsp"])' in client

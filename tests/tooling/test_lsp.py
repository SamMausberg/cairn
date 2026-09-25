"""`cairn lsp`: a real subprocess driven through the protocol, plus the editor assets.

Every read has a deadline, so a server that hangs fails the suite instead of it.
"""

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cairn.editor.lsp.completion import completion, signature_help
from cairn.editor.lsp.document import Document, symbols
from cairn.editor.lsp.edits import prepare_rename, references, rename
from cairn.editor.lsp.navigation import definition, hover

ROOT = Path(__file__).resolve().parents[2]
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

# One buffer that exercises every name a feature has to resolve: a record, an enum, a trait and its
# impl, a borrowed parameter, two aliased library modules and a generic container.
PROGRAM = """import std.vec as vec;
import std.map as m;

struct Pair { a:u64; b:u64; }

enum Op { Read; Write; }

trait Shape { fn area(self:ro<Self>) -> u64; }

impl Shape for Pair { fn area(self:ro<Pair>) -> u64 = self.a * self.b; }

fn scale(p:rw<Pair>, by:u64) { p.a = p.a * by; }

fn main() -> i32 {
  let mut pair = Pair(2, 3);
  scale(pair, 2);
  let mut v = vec.new[u64]();
  vec.push(v, pair.a);
  let op = Op.Read;
  let t = m.new[u64, u64]();
  let total = v.capacity() + usize(pair.area());
  return 0;
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
        self.asked = 1000  # ids of `ask`, clear of the ones a test writes itself
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

    def start(self):
        return self.request("initialize", {"capabilities": {}}, 1)

    def ask(self, method, position=None, **params):
        """A request about the open document under a fresh id, at `position` when the request has one."""
        self.asked += 1
        where = {"position": position} if position is not None else {}
        return self.request(f"textDocument/{method}", {"textDocument": {"uri": URI}, **where, **params}, self.asked)

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
    line = text[text.rfind("\n", 0, at) + 1 : at]
    return {"line": text[:at].count("\n"), "character": len(line) + sum(ord(c) > 0xFFFF for c in line)}


def labels(doc, text, needle, offset=0):
    return {item["label"] for item in completion(doc, text.index(needle) + offset)}


def lines(found):
    """Each reference or edit location as (file name, line), sorted."""
    return sorted((r["uri"].rsplit("/", 1)[1], r["range"]["start"]["line"]) for r in found)


def applied(doc, edits):
    """The document with a WorkspaceEdit's changes in it, latest position first."""
    text = doc.text
    for edit in sorted(edits, key=lambda e: -doc.offset(e["range"]["start"])):
        start, end = doc.offset(edit["range"]["start"]), doc.offset(edit["range"]["end"])
        text = text[:start] + edit["newText"] + text[end:]
    return text


def test_a_whole_session(client):
    initialized = client.request("initialize", {"processId": None, "capabilities": {}}, 1)
    capabilities = initialized["result"]["capabilities"]
    assert capabilities["positionEncoding"] == "utf-16"
    assert capabilities["textDocumentSync"]["change"] == 1
    assert all(capabilities[k] for k in ("hoverProvider", "documentSymbolProvider", "documentFormattingProvider"))
    assert capabilities["completionProvider"]["triggerCharacters"] == ["."]
    assert capabilities["signatureHelpProvider"]["triggerCharacters"] == ["(", ","]
    assert capabilities["referencesProvider"] and capabilities["renameProvider"]["prepareProvider"]
    client.send("initialized")

    reported = client.open(BROKEN)
    assert len(reported) == 1
    assert reported[0]["code"] == "E-UNBOUND"
    assert reported[0]["range"] == {"start": {"line": 1, "character": 10}, "end": {"line": 1, "character": 22}}
    assert "missing_name" in reported[0]["message"]
    assert reported[0]["data"]["repair_hint"] and reported[0]["data"]["card"] == "base"
    assert reported[0]["codeDescription"]["href"].endswith("skills/cairn/SKILL.md#core-rules")  # the card, no recompile

    assert client.change(FIXED) == []

    hovered = client.ask("hover", place(FIXED, "average(10, 20)", 2))
    assert "u64" in hovered["result"]["contents"]["value"]

    outline = client.ask("documentSymbol")
    names = {entry["name"] for entry in outline["result"]}
    assert {"average", "Pair", "Op", "LIMIT", "Shape", "main"} <= names
    assert [e["kind"] for e in outline["result"] if e["name"] == "Pair"] == [23]

    gone_to = client.ask("definition", place(FIXED, "average(10, 20)", 2))
    assert gone_to["result"]["range"]["start"] == place(FIXED, "average(x:u64")

    assert client.ask("formatting", options={})["result"] == []  # FIXED is already formatted

    assert client.request("shutdown", {}, 6)["result"] is None
    client.send("exit")
    assert client.proc.wait(timeout=TIMEOUT) == 0


def test_formatting_returns_one_whole_document_edit(client):
    client.start()
    ugly = "fn f(a:u64,b:u64)->u64{return a+b;}\n"
    client.open(ugly)
    edits = client.ask("formatting", options={})
    assert len(edits["result"]) == 1
    assert edits["result"][0]["newText"] == "fn f(a:u64, b:u64) -> u64 { return a + b; }\n"
    assert edits["result"][0]["range"] == {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 0}}


def test_positions_use_utf16_code_units(client):
    client.start()
    text = "// \U0001f600 \u00e9\u00e9\u00e9\nfn f() -> u64 {\n  let x = nope;\n  return x;\n}\n"
    reported = client.open(text)
    assert reported[0]["code"] == "E-UNBOUND"
    assert reported[0]["range"] == {"start": {"line": 2, "character": 10}, "end": {"line": 2, "character": 14}}
    # A hover on the astral comment line must round-trip through UTF-16 without an exception.
    assert client.ask("hover", {"line": 0, "character": 5})["result"] is None


def test_a_run_of_changes_to_one_file_is_analysed_once_at_its_last_text():
    from cairn.editor.lsp.server import latest

    def change(uri, text):
        return {"method": "textDocument/didChange",
                "params": {"textDocument": {"uri": uri}, "contentChanges": [{"text": text}]}}  # fmt: skip

    hover = {"id": 3, "method": "textDocument/hover", "params": {"textDocument": {"uri": "a"}}}
    burst = [change("a", "1"), change("a", "2"), change("b", "x"), change("a", "3"), hover, change("a", "4"),
             change("a", "5"), None]  # fmt: skip
    kept = [m and (m.get("params") or {}).get("contentChanges", [{}])[0].get("text", "hover") for m in latest(burst)]
    assert kept == ["2", "x", "3", "hover", "5", None]  # a request between two changes keeps the first


def test_a_text_typed_again_is_not_checked_again(monkeypatch):
    from cairn.compiler import compilations
    from cairn.editor.lsp import document

    ran, real = [], compilations.compile_program
    monkeypatch.setattr(compilations, "compile_program", lambda *a, **k: ran.append(a[0]) or real(*a, **k))
    monkeypatch.setattr(compilations, "CACHE", compilations.Cache())
    monkeypatch.setattr(document, "_LAST", [])
    before, after = "fn f(x:u64) -> u64 { return x + 1; }\n", "fn f(x:u64) -> u64 { return x + 2; }\n"
    first = Document(before)
    Document(after, first)
    undone = Document(before)  # an undo: the text of the first analysis again
    assert ran == [before, after] and undone.sites == first.sites and undone.rows == first.rows
    assert undone.program is not first.program  # its own copy: what one document does to it no other reads


def test_a_burst_of_changes_leaves_the_diagnostics_and_answers_of_the_last_text(client):
    client.start()
    client.send("initialized")
    assert client.open(BROKEN)
    for k in range(20):  # sent without waiting, as a typist's editor does
        text = FIXED if k == 19 else BROKEN.replace("missing_name", f"missing_{k}")
        client.send("textDocument/didChange", {"textDocument": {"uri": URI}, "contentChanges": [{"text": text}]})
    hovered = client.ask("hover", place(FIXED, "average(10, 20)", 2))
    assert "u64" in hovered["result"]["contents"]["value"]
    published = []
    while not client.inbox.empty():
        message = client.inbox.get_nowait()
        if message and message.get("method") == "textDocument/publishDiagnostics":
            published.append(message["params"]["diagnostics"])
    assert published == [] or published[-1] == []  # whatever came after the answer is the last text's: none


def test_the_server_survives_nonsense(client):
    client.start()
    methods = ("hover", "definition", "documentSymbol", "completion", "signatureHelp", "references", "prepareRename")
    edges = ({"line": 0, "character": 0}, {"line": 0, "character": 1}, {"line": 99, "character": 99})
    for text in ("", "}{;;;", "fn fn fn", "\u00e9\u00e9\u00e9", "fn f() { let x = $; }", "let x = \U0001f600.", "a."):
        reported = client.open(text, URI)
        assert isinstance(reported, list)
        for method in methods:
            for where in edges:
                answer = client.ask(method, where)
                assert "error" not in answer, answer
        renamed = client.ask("rename", edges[1], newName="x")
        assert renamed.get("error", {}).get("code", -32602) == -32602  # refused, never an internal failure
    assert client.ask("willSaveWaitUntil")["error"]["code"] == -32601
    assert client.request("shutdown", {}, 3)["result"] is None
    client.send("exit")
    assert client.proc.wait(timeout=TIMEOUT) == 0


def test_closing_a_document_clears_its_diagnostics(client):
    client.start()
    assert client.open(BROKEN)
    client.send("textDocument/didClose", {"textDocument": {"uri": URI}})
    assert client.diagnostics() == []


def test_exit_without_shutdown_is_a_failure(client):
    client.start()
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


# Completion ----------------------------------------------------------------------------------------


def test_completion_offers_the_fields_and_methods_of_a_record_local():
    doc = Document(PROGRAM)
    assert doc.diagnostics == []
    # `scale` takes rw<Pair> first and `area` implements Shape for Pair: both are reachable as methods.
    assert labels(doc, PROGRAM, "pair.a)", 5) == {"a", "b", "area", "scale"}
    detail = {i["label"]: i["detail"] for i in completion(doc, PROGRAM.index("pair.a)") + 5)}
    assert detail["a"] == "u64" and detail["area"].startswith("fn area(")


def test_completion_reaches_fields_through_a_borrowed_parameter():
    doc = Document(PROGRAM)
    assert labels(doc, PROGRAM, "p.a = ", 2) == {"a", "b", "area", "scale"}


def test_completion_shows_a_declared_field_extent():
    """`price` holds `rows` elements, so the extent belongs in what the field offers."""
    source = "struct Chart { rows:usize; price:Buf[f64][rows]; }\nfn go(c:ro<Chart>) -> usize { return c.rows; }\n"
    doc = Document(source)
    assert doc.diagnostics == []
    offered = {i["label"]: i["detail"] for i in completion(doc, source.index("c.rows") + 2)}
    assert offered["price"] == "Buf[f64][rows]" and offered["rows"] == "usize"


def test_completion_instantiates_a_generic_library_container():
    doc = Document(PROGRAM)
    offered = {i["label"]: i["detail"] for i in completion(doc, PROGRAM.index("v.capacity") + 2)}
    assert offered["data"] == "Buf[u64]" and offered["len"] == "usize"  # Vec[T] with T := u64
    assert {"push", "pop", "capacity", "reserve"} <= set(offered)
    assert "new" not in offered and "with_capacity" not in offered  # neither takes a receiver


def test_completion_of_a_module_alias_hides_its_private_names():
    doc = Document(PROGRAM)
    offered = labels(doc, PROGRAM, "m.new", 2)
    assert {"Map", "new", "insert", "find"} <= offered
    assert not offered & {"probe", "place", "grow"}  # std.map keeps these to itself


def test_completion_offers_the_variants_of_an_enum():
    doc = Document(PROGRAM)
    assert labels(doc, PROGRAM, "Op.Read", 3) == {"Read", "Write"}


def test_completion_does_not_leak_locals_across_functions():
    doc = Document(PROGRAM)
    inside = labels(doc, PROGRAM, "p.a * by", 0)
    assert {"p", "by"} <= inside and not inside & {"pair", "op", "total"}


def test_completion_sees_a_binding_the_last_good_analysis_never_saw():
    good = Document(PROGRAM)
    typing = PROGRAM.replace("  return 0;", "  let fresh = pair.a;\n  let y = f\n  return 0;")
    doc = Document(typing, good)
    assert doc.diagnostics and doc.good is good  # the buffer is broken; the analysis is the old one
    offered = labels(doc, typing, "let y = f", 9)
    assert {"fresh", "pair", "v", "total"} <= offered  # the new binder and the old ones
    assert {"Pair", "scale", "vec", "len", "u64", "match"} <= offered  # declarations, builtins, words


def test_completion_offers_modules_after_import_and_recipes_after_derive():
    text = (
        "import std.core (Ord);\nstruct Pair { a:u64; b:u64; }\nderive eq for Pair;\nfn main() -> i32 { return 0; }\n"
    )
    doc = Document(text)
    assert doc.diagnostics == []
    assert {"std.vec", "std.core", "std.wire"} <= labels(doc, text, "import std.core", 7)
    assert labels(doc, text, "derive eq", 7) == {"eq", "ord", "hash"}


def test_completion_offers_promises_in_a_generic_bound():
    good = Document("import std.core (Ord);\nfn main() -> i32 { return 0; }\n")
    typing = "import std.core (Ord);\nfn less[T: \nfn main() -> i32 { return 0; }\n"
    offered = labels(Document(typing, good), typing, "fn less[T: ", 11)
    assert {"Ord", "copy", "affine", "linear", "integer", "scalar"} <= offered
    assert "main" not in offered


def test_completion_answers_a_buffer_that_never_compiled():
    doc = Document("struct Pair { a:u64; }\nfn f() -> u64 { let x = nope; return x; }\n")
    assert doc.good is None
    offered = labels(doc, doc.text, "return x", 0)
    assert {"x", "Pair", "f", "return", "len"} <= offered  # tokens alone still know this much


# Signature help ------------------------------------------------------------------------------------


def test_signature_help_names_the_parameter_being_written():
    doc = Document(PROGRAM)
    answer = signature_help(doc, PROGRAM.index("scale(pair, 2)") + len("scale(pair, "))
    assert answer["signatures"][0]["label"] == "fn scale(p:rw<Pair>, by:u64)"
    assert [p["label"] for p in answer["signatures"][0]["parameters"]] == ["p:rw<Pair>", "by:u64"]
    assert answer["activeParameter"] == 1
    assert signature_help(doc, PROGRAM.index("scale(pair, 2)") + len("scale("))["activeParameter"] == 0


def test_signature_help_resolves_an_alias_and_shifts_for_method_syntax():
    doc = Document(PROGRAM)
    aliased = signature_help(doc, PROGRAM.index("vec.push(v, pair.a)") + len("vec.push(v, "))
    assert aliased["signatures"][0]["label"] == "fn push[T:affine](v:rw<Vec[T]>, item:T)"
    assert aliased["activeParameter"] == 1
    method = signature_help(doc, PROGRAM.index("v.capacity()") + len("v.capacity("))
    assert method["signatures"][0]["label"].startswith("fn capacity[T:affine](v:ro<Vec[T]>)")
    assert method["activeParameter"] == 1  # the receiver already filled the first parameter
    assert signature_help(doc, PROGRAM.index("let op")) is None


def test_signature_help_follows_a_call_that_leaves_its_extents_out():
    """`dot(a, a)` leaves out `n`: a second signature without it becomes the active one, and counts from there."""
    source = "fn dot(n:usize, xs:ro<u64>[n], ys:ro<u64>[n]) -> u64 = xs[0] * ys[0];\n"
    short = source + "fn main() -> i32 { let a = Buf[u64](8); return i32(dot(a, a)); }\n"
    full = source + "fn main() -> i32 { let a = Buf[u64](8); return i32(dot(len(a), a, a)); }\n"
    left = signature_help(Document(short), short.index("dot(a, a)") + len("dot(a, "))
    assert left["activeSignature"] == 1 and left["activeParameter"] == 1
    assert [p["label"] for p in left["signatures"][1]["parameters"]] == ["xs:ro<u64>[n]@host", "ys:ro<u64>[n]@host"]
    written = signature_help(Document(full), full.index("dot(len(a)") + len("dot(len(a), "))
    assert written["activeSignature"] == 0 and written["activeParameter"] == 1


# References, rename and definition -------------------------------------------------------------------


def test_references_and_rename_of_a_declaration():
    doc = Document(PROGRAM)
    at = PROGRAM.index("fn scale") + 4
    assert [r["range"]["start"]["line"] for r in references(doc, URI, at)] == [11, 15]
    assert prepare_rename(doc, at)["placeholder"] == "scale"
    edits = rename(doc, URI, at, "magnify")["changes"][URI]
    assert len(edits) == 2
    assert Document(applied(doc, edits)).diagnostics == []


def test_references_and_rename_of_a_local():
    doc = Document(PROGRAM)
    at = PROGRAM.index("scale(pair, 2)") + 6
    assert len(references(doc, URI, at)) == 4  # the binder and its three uses
    edits = rename(doc, URI, at, "point")["changes"][URI]
    changed = applied(doc, edits)
    assert "let mut point = Pair(2, 3);" in changed and "pair" not in changed
    assert Document(changed).diagnostics == []


def test_rename_refuses_everything_it_cannot_prove():
    doc = Document(PROGRAM)

    def refuse(needle, offset, fresh="fresh"):
        at = PROGRAM.index(needle) + offset
        assert prepare_rename(doc, at) is None
        with pytest.raises(ValueError):
            rename(doc, URI, at, fresh)

    refuse("fn scale", 0)  # a reserved word
    refuse("v.capacity", 2)  # a name of another module, reached as a method
    refuse("vec.push", 4)  # a name of another module, reached through its alias
    refuse("usize(pair", 0)  # a builtin
    refuse("area(self", 0)  # a trait member: one document cannot see every implementation
    at = PROGRAM.index("let mut pair") + 8
    for taken in ("mut", "Pair", "total", "len", "9lives"):  # word, declaration, local, builtin, not a name
        with pytest.raises(ValueError):
            rename(doc, URI, at, taken)
    broken = Document("fn f() -> u64 { let x = nope; return x; }\n")
    assert prepare_rename(broken, broken.text.index("let x") + 4) is None


def test_rename_refuses_a_declaration_a_method_call_could_reach():
    # `p.a` is a field and `pair.area()` a method: a bare name written after a `.` is not renameable.
    text = "struct Pair { a:u64; }\nfn a(p:ro<Pair>) -> u64 = p.a;\nfn main() -> i32 { return 0; }\n"
    doc = Document(text)
    assert doc.diagnostics == []
    assert prepare_rename(doc, text.index("fn a(") + 3) is None
    assert references(doc, URI, text.index("fn a(") + 3) == []


def test_definition_goes_into_the_packaged_library():
    doc = Document(PROGRAM)
    into = definition(doc, URI, PROGRAM.index("vec.push") + 5)
    assert into["uri"].endswith("/cairn/std/vec.cairn")
    library = Path(into["uri"].removeprefix("file://")).read_text(encoding="utf-8")
    line = library.splitlines()[into["range"]["start"]["line"]]
    assert line.startswith("pub fn push[T:affine]")
    assert line[into["range"]["start"]["character"] : into["range"]["end"]["character"]] == "push"
    # A bare name brought in by `import std.core (Option);` resolves the same way.
    text = "import std.core (Option);\nfn f() -> Option[u64] { return Option.Some(1); }\n"
    assert definition(Document(text), URI, text.index("-> Option") + 3)["uri"].endswith("/std/core.cairn")
    assert definition(doc, URI, PROGRAM.index("scale(pair")) is not None  # this document still wins for its own


def test_hover_adds_the_signature_and_effect_row_of_a_callee():
    shown = hover(Document(PROGRAM), PROGRAM.index("vec.push") + 5)["contents"]["value"]
    assert "fn push[T:affine](v:rw<Vec[T]>, item:T)" in shown
    assert "Effects: " in shown and "`alloc`" in shown


# The new features over the protocol -------------------------------------------------------------------


def test_completion_in_a_buffer_broken_mid_expression(client):
    client.start()
    assert client.open(PROGRAM) == []
    typing = PROGRAM.replace("  return 0;", "  let y = pair.\n  return 0;")
    assert client.change(typing)  # the buffer no longer compiles
    answered = client.ask("completion", place(typing, "let y = pair.", 13))
    assert {i["label"] for i in answered["result"]} == {"a", "b", "area", "scale"}
    helped = client.ask("signatureHelp", place(typing, "scale(pair, 2)", 6))
    assert helped["result"]["signatures"][0]["label"] == "fn scale(p:rw<Pair>, by:u64)"


def test_the_new_features_use_utf16_code_units(client):
    client.start()
    text = 'struct Pair { a:u64; b:u64; }\nfn main() -> i32 { let s = "\U0001f600\U0001f600"; let p = Pair(1, 2); return 0; }\n'
    assert client.open(text) == []
    answered = client.ask("completion", place(text, "Pair(1, 2)", 10))
    assert {"p", "s", "Pair", "main"} <= {i["label"] for i in answered["result"]}
    renamed = client.ask("rename", place(text, "let p = Pair", 4), newName="point")
    edit = renamed["result"]["changes"][URI][0]["range"]
    assert edit["start"]["character"] == place(text, "let p = Pair", 4)["character"]
    assert edit["end"]["character"] == edit["start"]["character"] + 1


def test_rename_over_the_protocol_refuses_with_an_error(client):
    client.start()
    client.open(PROGRAM)
    refused = client.ask("rename", place(PROGRAM, "vec.push", 4), newName="shove")
    assert refused["error"]["code"] == -32602 and "one document" in refused["error"]["message"]
    assert client.ask("prepareRename", place(PROGRAM, "vec.push", 4))["result"] is None


# Editor assets -------------------------------------------------------------------------------------

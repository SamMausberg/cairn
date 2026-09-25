"""`cairn lsp` semantic tokens, inlay hints, code actions and documented hovers.

Each feature is called directly on a `Document` for its detail and once over the protocol through the client of
`test_lsp.py`; every quick fix is applied and the result compiled again.
"""

import pytest
from test_lsp import URI, Client, applied, place

from cairn.editor.lsp.document import Document
from cairn.editor.lsp.fixes import code_actions
from cairn.editor.lsp.highlighting import KINDS, LEGEND, MODIFIERS, semantic_tokens
from cairn.editor.lsp.hints import inlay_hints
from cairn.editor.lsp.navigation import hover

PROGRAM = """module geo;
import std.vec as vec;

// A point on the grid.
struct Pair { a:u64; b:u64; }

enum Op { Read; Write; }

const LIMIT:usize = 8;

trait Shape { fn area(self:ro<Self>) -> u64; }

impl Shape for Pair { fn area(self:ro<Pair>) -> u64 = self.a * self.b; }

// Stretch the first coordinate.
fn scale(p:rw<Pair>, by:u64) { p.a = p.a * by; }

fn dot(n:usize, xs:ro<u64>[n], ys:ro<u64>[n]) -> u64 effects(read:xs, read:ys, trap, ffi_precondition) {
  let mut t:u64 = 0;
  for i in 0..n { t = t + xs[i] * ys[i]; }
  return t;
}

fn first[T:copy](n:usize, xs:ro<T>[n]) -> T = xs[0];

pub fn main() -> i32 {
  let mut pair = Pair(2, 3);
  scale(pair, 2);
  let mut v = vec.new[u64]();
  vec.push(v, pair.a);
  v.push(4);
  let op = Op.Read;
  let w = Buf[u64](LIMIT);
  let d = dot(w, w) + dot(w[0..4], w[4..8]);
  let f = first[u64](w);
  let mut out = vec.new[u8]();
  out.extend_from("ab");
  return 0;
}
"""


def decoded(doc):
    """The semantic tokens as (text, kind, modifiers), read back through the relative encoding."""
    data, lines, out = semantic_tokens(doc)["data"], doc.text.split("\n"), []
    line = column = 0
    for k in range(0, len(data), 5):
        delta, start, length, kind, bits = data[k : k + 5]
        line, column = line + delta, (column if delta == 0 else 0) + start
        mods = {m for i, m in enumerate(MODIFIERS) if bits >> i & 1}
        out.append((lines[line][column : column + length], KINDS[kind], frozenset(mods)))
    return out


def kinds(doc, text):
    return {(k, m) for t, k, m in decoded(doc) if t == text}


@pytest.fixture(scope="module")
def doc():
    d = Document(PROGRAM)
    assert d.diagnostics == [], d.diagnostics
    return d


def test_semantic_tokens_tell_names_apart_by_what_they_are(doc):
    assert ("struct", frozenset({"declaration"})) in kinds(doc, "Pair")
    assert ("struct", frozenset()) in kinds(doc, "Pair")
    assert kinds(doc, "Op") == {("enum", frozenset({"declaration"})), ("enum", frozenset())}
    assert ("enumMember", frozenset({"declaration"})) in kinds(doc, "Read") and ("enumMember", frozenset()) in kinds(
        doc, "Read"
    )
    assert ("interface", frozenset({"declaration"})) in kinds(doc, "Shape")
    assert kinds(doc, "LIMIT") == {
        ("variable", frozenset({"declaration", "readonly"})),
        ("variable", frozenset({"readonly"})),
    }
    assert kinds(doc, "a") == {("property", frozenset({"declaration"})), ("property", frozenset())}
    assert ("method", frozenset({"declaration"})) in kinds(doc, "area")
    assert kinds(doc, "scale") == {("function", frozenset({"declaration"})), ("function", frozenset())}
    assert kinds(doc, "vec") == {("namespace", frozenset())}
    assert ("function", frozenset()) in kinds(doc, "push") and ("method", frozenset()) in kinds(doc, "push")
    assert ("typeParameter", frozenset()) in kinds(doc, "T")


def test_semantic_tokens_mark_mutability_and_the_library(doc):
    assert kinds(doc, "p") == {("parameter", frozenset({"mutable"}))}  # a rw borrow may be assigned
    assert kinds(doc, "by") == {("parameter", frozenset({"readonly"}))}
    assert kinds(doc, "pair") == {("variable", frozenset({"mutable"}))}
    assert kinds(doc, "op") == {("variable", frozenset({"readonly"}))}
    assert kinds(doc, "i") == {("variable", frozenset({"readonly"}))}
    assert kinds(doc, "u64") == {("type", frozenset({"defaultLibrary"}))}
    assert kinds(doc, "Buf") == {("struct", frozenset({"defaultLibrary"}))}


def test_semantic_tokens_read_an_effect_row(doc):
    row = [(t, k) for t, k, _ in decoded(doc) if t in {"read", "trap", "ffi_precondition"}]
    assert row == [("read", "effect"), ("read", "effect"), ("trap", "effect"), ("ffi_precondition", "effect")]
    assert ("parameter", frozenset()) in kinds(doc, "xs")


def test_semantic_tokens_color_a_bare_variant_as_a_variant():
    text = """import std.core (Option);
fn find(n:usize, xs:ro<u64>[n], x:u64) -> Option[usize] {
  for i in 0..n { if xs[i] == x { return Some(i); } }
  return None;
}
fn main() -> i32 {
  let xs = Buf[u64](4);
  match find(xs, 0) { Some(at) => { return i32(at); } None => { return 1; } }
}
"""
    doc = Document(text)
    assert doc.diagnostics == [], doc.diagnostics
    assert kinds(doc, "Some") == kinds(doc, "None") == {("enumMember", frozenset())}
    assert kinds(doc, "Option") == {("enum", frozenset())}
    assert kinds(doc, "at") == {("variable", frozenset({"readonly"}))}


def test_semantic_tokens_of_a_buffer_that_does_not_compile_still_name_its_declarations():
    broken = Document("fn f(x:u64) -> u64 {\n  let mut y = x;\n  return missing;\n}\n")
    assert broken.diagnostics
    assert {(t, k) for t, k, _ in decoded(broken)} >= {("f", "function"), ("x", "parameter"), ("y", "variable")}


def test_semantic_tokens_count_utf16_units():
    text = "// 𝄞 wide\nfn f(x:u64) -> u64 = x;\n"
    assert [(t, k) for t, k, _ in decoded(Document(text)) if k != "type"] == [
        ("f", "function"),
        ("x", "parameter"),
        ("x", "parameter"),
    ]
    assert LEGEND["tokenTypes"] == KINDS and set(MODIFIERS) >= {"declaration", "readonly", "mutable"}


def labels(doc, needle=None):
    hints = inlay_hints(doc, None)
    return [h["label"] for h in hints if needle is None or doc.text.split("\n")[h["position"]["line"]].count(needle)]


def test_inlay_hints_show_each_row_after_its_signature(doc):
    hints = {h["label"]: h for h in inlay_hints(doc, None)}
    row = hints["effects: ffi_precondition, read:xs, read:ys, trap"]
    line = PROGRAM.split("\n")[row["position"]["line"]]
    assert line.startswith("fn dot(") and line[row["position"]["character"]] == "{"
    assert "effects: read:p, trap, write:p" in hints
    assert any(k.startswith("effects: alloc") for k in hints)  # main takes storage


def test_inlay_hints_show_the_extents_a_call_leaves_out(doc):
    said = labels(doc)
    assert "n = len(w)," in said and "n = 4 - 0," in said
    assert 'n = len("ab"),' in said  # through method syntax: the receiver is the vector, the view is the literal
    assert not any(label.startswith("n = ") and "LIMIT" in label for label in said)


def test_inlay_hints_show_the_type_of_an_untyped_let(doc):
    said = labels(doc)
    assert ":Pair" in said and ":Vec[u64]" in said and ":Buf[u64]" in said and ":u64" in said
    assert ":u64" not in labels(doc, "let mut t:u64")  # a written type is not repeated


def test_inlay_hints_stay_in_the_requested_range(doc):
    line = PROGRAM.split("\n").index("  let op = Op.Read;")
    span = {"start": {"line": line, "character": 0}, "end": {"line": line, "character": 40}}
    assert [h["label"] for h in inlay_hints(doc, span)] == [":Op"]


def test_an_instance_whose_type_arguments_nest_is_read_as_its_function():
    """`twice[Box[u64]]` is an instance of `twice`: its row is the hint after twice's signature and the effects its
    hover names, and its locals have their types."""
    text = """struct Box[T] { v:T; }
fn twice[T:copy](x:T, n:u64) -> Box[T] {
  let m = n * 2;
  return Box[T](x);
}
fn main() -> i32 {
  let b = twice[Box[u64]](Box[u64](3), 4);
  return i32(b.v.v);
}
"""
    doc = Document(text)
    assert doc.diagnostics == [] and doc.rows["twice[Box[u64]]"] == ["trap"]
    rows = [h for h in inlay_hints(doc, None) if h["label"] == "effects: trap"]
    assert [text.split("\n")[h["position"]["line"]][:9] for h in rows] == ["fn twice[", "fn main()"]
    assert ":u64" in labels(doc, "let m")
    assert hover(doc, text.index("twice[Box") + 1)["contents"]["value"].endswith("Effects: `trap`.")


def fixed(text, needle, offset=0):
    """The one quick fix at `needle`, applied, and the diagnostic of the result (None when it compiles)."""
    doc = Document(text)
    at = place(text, needle, offset)
    actions = code_actions(doc, URI, {"start": at, "end": at})
    assert len(actions) == 1, (doc.diagnostics, actions)
    out = applied(doc, actions[0]["edit"]["changes"][URI])
    after = Document(out)
    return actions[0]["title"], out, after.diagnostics[0]["code"] if after.diagnostics else None


def test_a_match_gets_its_missing_arms_spelled_like_the_others():
    text = "enum Op { Read; Write; Flush; }\nfn cost(op:Op) -> u64 {\n  match op {\n    Op.Read => { return 1; }\n    Op.Write => { return 2; }\n  }\n  return 0;\n}\n"
    title, out, left = fixed(text, "match")
    assert title == "Add the missing arm" and "    Op.Flush => { }\n  }" in out and left is None


def test_a_missing_payload_arm_gets_a_fresh_binder():
    text = "import std.core (Option);\nfn f(o:Option[u64], some:u64) -> u64 { match o { Option.None => { return 0; } } return some; }\n"
    _, out, left = fixed(text, "match")
    assert "Option.Some(some2) => { } }" in out and left is None


def test_an_assigned_let_becomes_let_mut():
    title, out, left = fixed("fn main() -> i32 { let x:u64 = 1; x = 2; return 0; }", "x = 2")
    assert title == "Declare x as let mut" and "let mut x:u64 = 1;" in out and left is None


def test_a_packaged_module_gets_its_import():
    title, out, left = fixed("// entry\nfn main() -> i32 { let v = vec.new[u64](); return 0; }\n", "vec.new")
    assert title == "Import std.vec" and out.startswith("import std.vec;\n// entry") and left is None
    _, out, left = fixed("import std.core (Option);\nfn f(v:ro<vec.Vec[u64]>) -> usize = v.len;\n", "fn f")
    assert out.split("\n")[:2] == ["import std.core (Option);", "import std.vec;"] and left is None


def test_no_fix_is_offered_where_the_repair_is_a_choice():
    for text, needle in [("fn kind(first:u8) -> u8 { if first == 71 { return 1; } }", "fn kind"),
                         ("fn f(x:u64) { x = 2; }", "x = 2"), ("fn main() -> i32 { let x:u64 = 1; x = 2; return 0; }", "main")]:  # fmt: skip
        doc = Document(text)
        at = place(text, needle)
        assert code_actions(doc, URI, {"start": at, "end": at}) == [], text


def test_hover_shows_the_comment_above_a_declaration(doc):
    said = hover(doc, PROGRAM.index("scale(pair") + 1)["contents"]["value"]
    assert "Stretch the first coordinate." in said and "`write:p`" in said
    said = hover(doc, PROGRAM.index("Pair(2, 3)") + 1)["contents"]["value"]
    assert "A point on the grid." in said
    reserve = Document(
        "import std.vec as vec;\nfn main() -> i32 { let mut v = vec.new[u64](); vec.reserve(v, 8); return 0; }\n"
    )
    said = hover(reserve, reserve.text.index("reserve(") + 1)["contents"]["value"]
    assert "Doubling keeps pushes amortized constant" in said


def test_the_new_features_over_the_protocol():
    c = Client()
    try:
        capabilities = c.start()["result"]["capabilities"]
        assert capabilities["semanticTokensProvider"]["legend"] == LEGEND and capabilities["inlayHintProvider"]
        assert capabilities["codeActionProvider"] == {"codeActionKinds": ["quickfix"]}
        assert c.open(PROGRAM) == []
        tokens = c.ask("semanticTokens/full")["result"]["data"]
        assert tokens == semantic_tokens(Document(PROGRAM))["data"] and len(tokens) % 5 == 0
        whole = {"start": {"line": 0, "character": 0}, "end": {"line": 200, "character": 0}}
        assert "n = len(w)," in [h["label"] for h in c.ask("inlayHint", range=whole)["result"]]
        broken = "fn main() -> i32 { let x:u64 = 1; x = 2; return 0; }"
        c.change(broken)
        at = place(broken, "x = 2")
        actions = c.ask("codeAction", range={"start": at, "end": at}, context={"diagnostics": []})["result"]
        assert [a["title"] for a in actions] == ["Declare x as let mut"]
    finally:
        c.close()


def test_every_refusal_is_a_diagnostic_on_its_own_range_with_its_own_fix():
    text = "fn f() -> bool = 1;\nfn main() -> i32 { let x:u64 = 1; x = 2; return 0; }\n"
    doc = Document(text)
    assert [(d["code"], d["range"]["start"]) for d in doc.diagnostics] == [
        ("E-TYPE-MISMATCH", place(text, "1;")),
        ("E-IMMUTABLE", place(text, "x = 2")),
    ]
    title, out, left = fixed(text, "x = 2")  # the fix of the second refusal, and the first is still there
    assert title == "Declare x as let mut" and "let mut x:u64 = 1;" in out and left == "E-TYPE-MISMATCH"

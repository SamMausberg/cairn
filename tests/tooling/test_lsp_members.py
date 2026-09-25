"""Rename and references of a field or a variant across the files of a project, as one checked transaction.

Every admitted rename is applied to the files, compiled and run natively; every refusal changes nothing. A field is
written in its declaration, a declared extent, a `lends` clause, an access, a construction's type and an unpack,
and a variant in its declaration, `Sum.Variant`, a bare constructor and a match arm.
"""

import subprocess

import pytest
from test_lsp import Client, applied, lines, place

from cairn.compiler.cairnc import compile_source
from cairn.editor import members as members_module
from cairn.editor.document import Document
from cairn.editor.workspace import Refused, prepare_rename, references, rename, workspace
from cairn.projects.build import build
from cairn.projects.project import load_project

MANIFEST = (
    '[project]\nname = "shapes"\nsources = ["src/shapes.cairn", "src/main.cairn"]\n'
    '[build]\nkind = "exe"\narch = "baseline"\n'
)
SHAPES = """module shapes;

pub struct Size { w:u64; h:u64; }
pub struct Line { len:usize; data:Buf[u8][len]; }
pub struct Text { bytes:Buf[u8]; used:usize; lends bytes[0..used]; }
pub enum Shape { Dot; Box(Size); }
pub enum Mode { On; Off; }

pub fn area(s:ro<Size>) -> u64 = s.w * s.h;

pub fn cost(s:Shape) -> u64 {
  match s {
    Shape.Dot => return 1;
    Box(z) => return area(z);
  }
}

pub fn first(n:usize, xs:ro<u8>[n]) -> u8 = xs[0];
"""
MAIN = """module app;
import shapes;
import shapes (Size, Shape, Mode, Text, Line);

fn main() -> i32 {
  let mut z = Size(2, 3);
  z.w += 1;
  let big:Shape = Box(z);
  if shapes.cost(big) != 9 || shapes.cost(Shape.Dot) != 1 { return 1; }
  let mode = Mode.On;
  if mode == Off { return 2; }
  let mut t = Text(Buf[u8](2), 1);
  t.bytes[0] = 7;
  if shapes.first(t) != 7 { return 3; }
  let l = Line(4, Buf[u8](4));
  if l.len != 4 { return 4; }
  let Size(w, h) = Size(5, 6);
  if w * h != 30 { return 5; }
  return 0;
}
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "cairn.toml").write_text(MANIFEST)
    (tmp_path / "src/shapes.cairn").write_text(SHAPES)
    (tmp_path / "src/main.cairn").write_text(MAIN)
    return tmp_path


def held(root):
    main = (root / "src/main.cairn").resolve()
    return workspace(main.as_uri(), {main.as_uri(): MAIN}), main.as_uri()


def at(needle, offset=0, text=MAIN):
    return text.index(needle) + offset


@pytest.mark.parametrize(
    "needle,offset,expected",
    [
        ("z.w", 2, [("main.cairn", 6), ("shapes.cairn", 2), ("shapes.cairn", 8)]),  # declaration, two accesses
        ("t.bytes", 2, [("main.cairn", 12), ("shapes.cairn", 4), ("shapes.cairn", 4)]),  # the lends clause too
        ("l.len", 2, [("main.cairn", 15), ("shapes.cairn", 3), ("shapes.cairn", 3)]),  # a declared extent too
        ("Box(z)", 0, [("main.cairn", 7), ("shapes.cairn", 5), ("shapes.cairn", 13)]),  # bare, and a match arm
        ("Shape.Dot)", 6, [("main.cairn", 8), ("shapes.cairn", 5), ("shapes.cairn", 12)]),
        ("== Off", 3, [("main.cairn", 10), ("shapes.cairn", 6)]),  # a bare variant the checker typed as Mode
    ],
)
def test_references_to_a_field_or_a_variant_reach_every_file(project, needle, offset, expected):
    ws, uri = held(project)
    assert lines(references(ws, uri, at(needle, offset))) == expected


@pytest.mark.parametrize(
    "needle,offset,fresh,old",
    [
        ("z.w", 2, "width", "w:u64"),
        ("t.bytes", 2, "store", "bytes:Buf"),
        ("l.len", 2, "count", "len:usize"),
        ("Box(z)", 0, "Frame", "Box(Size)"),
        ("Shape.Dot)", 6, "Point", "Dot;"),
        ("== Off", 3, "Idle", "Off;"),
    ],
)
def test_a_member_rename_is_applied_everywhere_and_the_program_still_runs(project, needle, offset, fresh, old):
    ws, uri = held(project)
    edits = rename(ws, uri, at(needle, offset), fresh)["changes"]
    for name in ("shapes.cairn", "main.cairn"):
        path = (project / "src" / name).resolve()
        path.write_text(applied(Document(path.read_text(), analyse=False), edits.get(path.as_uri(), [])))
    assert old not in (project / "src/shapes.cairn").read_text()
    rebuilt = load_project(project)
    compile_source(rebuilt.source)
    record = build(rebuilt, cxx="clang++", timeout=120)
    assert subprocess.run([record["artifact"]], timeout=30).returncode == 0


def test_a_local_that_shares_a_field_s_spelling_is_not_the_field(project):
    """`let Size(w, h)` binds a local w. The field's references leave it out; the local's, whose spelling is also
    written after a `.`, are refused rather than guessed, as the one-document rule has always done."""
    ws, uri = held(project)
    assert ("main.cairn", 16) not in lines(references(ws, uri, at("z.w", 2)))
    assert references(ws, uri, at("let Size(w", 9)) == []
    assert prepare_rename(ws, uri, at("let Size(w", 9)) is None


@pytest.mark.parametrize(
    "needle,offset,fresh,why",
    [
        ("z.w", 2, "h", "already written"),  # the other field of Size
        ("z.w", 2, "area", "already written"),  # a function's name
        ("Box(z)", 0, "Dot", "already written"),  # a sibling variant
        ("z.w", 2, "len", "builtin"),
        ("z.w", 2, "fn", "reserved"),
    ],
)
def test_a_member_rename_that_could_collide_is_refused_whole(project, needle, offset, fresh, why):
    ws, uri = held(project)
    with pytest.raises(Refused, match=why):
        rename(ws, uri, at(needle, offset), fresh)


def test_a_library_member_is_not_renamed(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "cairn.toml").write_text('[project]\nname = "v"\nsources = ["src/main.cairn"]\n')
    text = "import std.vec (Vec);\nfn main() -> i32 {\n  let mut v = vec.new[u8]();\n  v.push(1);\n  return i32(v.len) - 1;\n}\n"
    (tmp_path / "src/main.cairn").write_text(text)
    uri = (tmp_path / "src/main.cairn").resolve().as_uri()
    ws = workspace(uri, {uri: text})
    with pytest.raises(Refused, match="library"):
        rename(ws, uri, at("v.len", 2, text), "size")
    assert prepare_rename(ws, uri, at("v.len", 2, text)) is None


def test_a_field_a_recipe_reads_is_renamed_with_what_the_recipe_derives(tmp_path):
    """`derive eq` compares fields by name, so its generated impl follows the field; the program still checks the
    same and runs the same, and the receipt entries are unchanged."""
    (tmp_path / "src").mkdir()
    (tmp_path / "cairn.toml").write_text('[project]\nname = "d"\nsources = ["src/main.cairn"]\n[build]\nkind = "exe"\n')
    text = (
        "import std.core (Eq);\nstruct P { x:u64; y:u64; }\nderive eq for P;\n"
        "fn main() -> i32 {\n  let a = P(1, 2);\n  if !Eq.same(a, P(1, 2)) || a.x != 1 { return 1; }\n  return 0;\n}\n"
    )
    (tmp_path / "src/main.cairn").write_text(text)
    uri = (tmp_path / "src/main.cairn").resolve().as_uri()
    edits = rename(workspace(uri, {uri: text}), uri, at("a.x", 2, text), "left")["changes"][uri]
    assert len(edits) == 2  # the declaration and the access: the recipe names no field itself
    renamed = applied(Document(text, analyse=False), edits)
    (tmp_path / "src/main.cairn").write_text(renamed)
    record = build(load_project(tmp_path), cxx="clang++", timeout=120)
    assert subprocess.run([record["artifact"]], timeout=30).returncode == 0


def test_a_field_whose_name_a_recipe_writes_into_a_declaration_is_refused(tmp_path):
    """A recipe that names a function after a field (`cols.unrolled`'s Trade_unrolled_price) would rename that
    function too, which a caller may name; the receipt recheck sees the change and refuses."""
    (tmp_path / "src").mkdir()
    (tmp_path / "cairn.toml").write_text('[project]\nname = "d"\nsources = ["src/main.cairn"]\n')
    text = (
        "recipe getter for R {\n  each f in R {\n    fn get_$f(r:ro<R>) -> u64 = r.$f;\n  }\n}\n"
        "struct P { x:u64; }\nderive getter for P;\n"
        "fn main() -> i32 { let p = P(1); if p.x != get_x(p) { return 1; } return 0; }\n"
    )
    (tmp_path / "src/main.cairn").write_text(text)
    uri = (tmp_path / "src/main.cairn").resolve().as_uri()
    compile_source(text)
    with pytest.raises(Refused, match="would not compile: E-CALLEE"):  # the recipe now writes get_left
        rename(workspace(uri, {uri: text}), uri, at("p.x", 2, text), "left")


def test_the_recheck_refuses_a_member_rename_the_token_search_got_wrong(project, monkeypatch):
    """The transaction does not trust members.py: an access it missed leaves the program refusing to compile."""
    ws, uri = held(project)
    real = members_module.Members.tokens
    monkeypatch.setattr(members_module.Members, "tokens", lambda self, *named: real(self, *named)[:-1])
    with pytest.raises(Refused, match="would not compile"):
        rename(ws, uri, at("z.w", 2), "width")


def test_a_member_rename_over_the_protocol_edits_both_files(project):
    main, shapes = (project / "src/main.cairn").resolve().as_uri(), (project / "src/shapes.cairn").resolve().as_uri()
    client = Client()
    try:
        client.start()
        assert client.open(MAIN, uri=main) == []
        params = {"textDocument": {"uri": main}, "position": place(MAIN, "z.w", 2), "newName": "width"}
        changes = client.request("textDocument/rename", params, 7)["result"]["changes"]
        assert set(changes) == {main, shapes} and len(changes[main]) == 1 and len(changes[shapes]) == 2
        where = client.request("textDocument/definition", {k: params[k] for k in ("textDocument", "position")}, 8)
        assert where["result"]["uri"] == shapes and where["result"]["range"]["start"] == {"line": 2, "character": 18}
    finally:
        client.close()

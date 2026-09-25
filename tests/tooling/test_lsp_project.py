"""References and rename across the files of a project, and the rename as one checked transaction.

Every rename that is admitted is applied to the files and the project is compiled again; every refusal names its
reason and changes nothing. The protocol is driven through the client of `test_lsp.py`.
"""

import json
import pathlib
import subprocess

import pytest
from test_lsp import Client, applied, lines, place

from cairn.cli import main
from cairn.compiler.cairnc import compile_source
from cairn.editor.lsp import workspace as ws_module
from cairn.editor.lsp.document import Document
from cairn.editor.lsp.edits import document_highlights
from cairn.editor.lsp.edits import references as one_document_references
from cairn.editor.lsp.edits import rename as one_document_rename
from cairn.editor.lsp.lenses import code_lenses
from cairn.editor.lsp.workspace import Refused, prepare_rename, references, rename, workspace
from cairn.projects.build import build
from cairn.projects.project import load_project

MANIFEST = (
    '[project]\nname = "geo"\nsources = ["src/geo.cairn", "src/main.cairn"]\n[build]\nkind = "exe"\narch = "baseline"\n'
)
GEO = """module geo;

pub struct Pair { a:u64; b:u64; }

pub fn scale(p:rw<Pair>, by:u64) { p.a *= by; }

pub fn area(p:ro<Pair>) -> u64 = p.a * p.b;

extern fn getpid() -> i32 effects(io);
"""
MAIN = """module app;
import geo;
import geo (area);

fn twice(p:rw<geo.Pair>) { geo.scale(p, 2); }

fn main() -> i32 {
  let mut pair = geo.Pair(2, 3);
  twice(pair);
  pair.scale(1);
  if area(pair) != 12 { return 1; }
  return 0;
}
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "cairn.toml").write_text(MANIFEST)
    (tmp_path / "src/geo.cairn").write_text(GEO)
    (tmp_path / "src/main.cairn").write_text(MAIN)
    return tmp_path


def opened(root, **held):
    """The project as an editor holding `held` (file name -> text) would see it, with main.cairn's uri."""
    main = (root / "src/main.cairn").resolve()
    buffers = {(root / "src" / name).resolve().as_uri(): text for name, text in held.items()}
    return workspace(main.as_uri(), buffers or {main.as_uri(): MAIN}), main.as_uri()


def forgetting_the_last(target):
    """`workspace.target` with the last occurrence it finds left out, as a token search that missed one would."""

    def forgetful(w, at):
        kind, full, tokens = target(w, at)
        return kind, full, tokens[:-1]

    return forgetful


def after(root, edits):
    """Each file of the project with the rename's edits applied, as {name: text}."""
    out = {}
    for name in ("geo.cairn", "main.cairn"):
        path = (root / "src" / name).resolve()
        doc = Document(path.read_text(), analyse=False)
        out[name] = applied(doc, edits.get(path.as_uri(), []))
    return out


@pytest.mark.parametrize(
    "needle,offset,expected",
    [
        ("geo.scale(p", 5, [("geo.cairn", 4), ("main.cairn", 4), ("main.cairn", 9)]),  # qualified, and a method call
        ("area(pair", 0, [("geo.cairn", 6), ("main.cairn", 2), ("main.cairn", 10)]),  # through `import geo (area)`
        ("geo.Pair(2", 5, [("geo.cairn", 2), ("geo.cairn", 4), ("geo.cairn", 6), ("main.cairn", 4), ("main.cairn", 7)]),
        ("let mut pair", 8, [("main.cairn", 7), ("main.cairn", 8), ("main.cairn", 9), ("main.cairn", 10)]),
        ("fn main", 3, [("main.cairn", 6)]),  # references reach what a rename may not
    ],
)
def test_references_reach_every_file_of_the_project(project, needle, offset, expected):
    ws, uri = opened(project)
    assert lines(references(ws, uri, MAIN.index(needle) + offset)) == expected


@pytest.mark.parametrize(
    "needle,offset,fresh,old,new",
    [
        ("geo.scale(p", 5, "stretch", "scale", "stretch"),
        ("area(pair", 0, "size", "area", "size"),
        ("geo.Pair(2", 5, "Point", "Pair", "Point"),
        ("twice(pair", 0, "double", "twice", "double"),
        ("let mut pair", 8, "point", "pair", "point"),
    ],
)
def test_a_rename_is_applied_to_every_file_and_the_project_still_builds(project, needle, offset, fresh, old, new):
    ws, uri = opened(project)
    edits = rename(ws, uri, MAIN.index(needle) + offset, fresh)["changes"]
    files = after(project, edits)
    for name, text in files.items():
        (project / "src" / name).write_text(text)
    rebuilt = load_project(project)
    written = Document(rebuilt.source, analyse=False).code
    assert old not in {t.s for t in written} and new in {t.s for t in written}  # every occurrence, in every file
    compile_source(rebuilt.source)
    record = build(rebuilt, cxx="clang++", timeout=120)
    assert subprocess.run([record["artifact"]], timeout=30).returncode == 0


@pytest.mark.parametrize("crlf,mark", [(True, False), (False, True), (True, True)], ids=["crlf", "mark", "both"])
def test_a_crlf_or_marked_file_the_editor_has_not_opened_is_found_at_its_own_characters(project, crlf, mark):
    geo = GEO.replace("\n", "\r\n") if crlf else GEO
    (project / "src/geo.cairn").write_bytes((b"\xef\xbb\xbf" if mark else b"") + geo.encode())
    ws, uri = opened(project)
    found = [r["range"] for r in references(ws, uri, MAIN.index("geo.scale(p") + 5) if r["uri"].endswith("geo.cairn")]
    at = GEO.splitlines()[4].index("scale")
    assert found == [{"start": {"line": 4, "character": at}, "end": {"line": 4, "character": at + 5}}]


def test_an_unsaved_buffer_is_what_the_project_is_read_as(project):
    typed = MAIN.replace("  return 0;\n}", "  twice(pair);\n  return 0;\n}")
    ws, uri = opened(project, **{"main.cairn": typed})
    assert len(references(ws, uri, typed.index("twice(pair"))) == 3  # the declaration and two calls, one unsaved


@pytest.mark.parametrize(
    "needle,offset,fresh,why",
    [
        ("fn main", 3, "start", "entry point"),
        ("geo.scale(p", 5, "area", "already written"),  # it would collide with geo.area
        ("geo.scale(p", 5, "len", "builtin"),
        ("geo.scale(p", 5, "fn", "reserved"),
        ("geo.scale(p", 5, "9x", "identifier"),
        ("p:rw<geo", 0, "pair", "already written"),  # a local of another function has that name
    ],
)
def test_a_rename_that_could_change_a_meaning_is_refused_whole(project, needle, offset, fresh, why):
    ws, uri = opened(project)
    with pytest.raises(Refused, match=why):
        rename(ws, uri, MAIN.index(needle) + offset, fresh)


def test_a_library_or_foreign_declaration_is_not_renamed(project, tmp_path):
    (project / "src/main.cairn").write_text(MAIN.replace("module app;\n", "module app;\nimport std.vec;\n"))
    ws, uri = opened(project, **{"main.cairn": (project / "src/main.cairn").read_text()})
    text = (project / "src/main.cairn").read_text()
    assert prepare_rename(ws, uri, text.index("std.vec") + 4) is None
    geo = (project / "src/geo.cairn").resolve().as_uri()
    held = workspace(geo, {geo: GEO})
    with pytest.raises(Refused, match="foreign"):
        rename(held, geo, GEO.index("getpid"), "pid")


def test_the_recheck_refuses_an_edit_set_the_name_rules_got_wrong(project, monkeypatch):
    """The transaction does not trust the token search: an occurrence missed, or one too many, fails it."""
    ws, uri = opened(project)
    monkeypatch.setattr(ws_module, "target", forgetting_the_last(ws_module.target))
    with pytest.raises(Refused, match="would not compile"):
        rename(ws, uri, MAIN.index("geo.scale(p") + 5, "stretch")


def test_the_recheck_refuses_a_rename_that_compiles_but_changes_a_callee(tmp_path, monkeypatch):
    """A rename of geo.area that missed the bare call would leave `area(3)` reaching the root's `area`: the program
    still compiles and now returns 1. The receipt says main calls something else, so the rename is refused."""
    (tmp_path / "src").mkdir()
    sources = '["src/base.cairn", "src/geo.cairn", "src/main.cairn"]'
    (tmp_path / "cairn.toml").write_text(f'[project]\nname = "shadow"\nsources = {sources}\n')
    (tmp_path / "src/base.cairn").write_text("fn area(x:u64) -> u64 = x + 100;\n")
    (tmp_path / "src/geo.cairn").write_text("module geo;\npub fn area(x:u64) -> u64 = x * x;\n")
    entry = "module app;\nimport geo (area);\nfn main() -> i32 {\n  if area(3) != 9 { return 1; }\n  return 0;\n}\n"
    (tmp_path / "src/main.cairn").write_text(entry)
    uri = (tmp_path / "src/main.cairn").resolve().as_uri()
    ws = workspace(uri, {uri: entry})
    monkeypatch.setattr(ws_module, "target", forgetting_the_last(ws_module.target))  # misses the call
    with pytest.raises(Refused, match="change what the program does"):
        rename(ws, uri, entry.index("area(3)"), "size")


def test_rename_over_the_protocol_edits_two_files(project):
    client = Client()
    try:
        client.start()
        main, geo = (project / "src/main.cairn").resolve().as_uri(), (project / "src/geo.cairn").resolve().as_uri()
        assert client.open(MAIN, uri=main) == []
        assert client.open(GEO, uri=geo) == []
        params = {"textDocument": {"uri": main}, "position": place(MAIN, "geo.scale(p", 5), "newName": "stretch"}
        renamed = client.request("textDocument/rename", params, 7)["result"]["changes"]
        assert set(renamed) == {main, geo} and len(renamed[main]) == 2 and len(renamed[geo]) == 1
        params = {"textDocument": {"uri": main}, "position": place(MAIN, "fn main", 3), "newName": "start"}
        refused = client.request("textDocument/rename", params, 8)
        assert refused["error"]["code"] == -32602 and "entry point" in refused["error"]["message"]
    finally:
        client.close()


def test_a_file_that_imports_another_is_analysed_with_its_project(project):
    """Alone, `import geo;` is unknown; within the project it resolves, and a refusal in geo reaches both files."""
    client = Client()
    try:
        client.start()
        main, geo = (project / "src/main.cairn").resolve().as_uri(), (project / "src/geo.cairn").resolve().as_uri()
        assert client.open(MAIN, uri=main) == []
        hover = client.request("textDocument/hover", {"textDocument": {"uri": main},
                                                      "position": place(MAIN, "geo.scale(p", 5)}, 5)  # fmt: skip
        assert "fn scale(p:rw<geo.Pair>, by:u64)" in hover["result"]["contents"]["value"]
        assert client.open(GEO, uri=geo) == []
        broken = GEO.replace("p.a * p.b", "p.a * p.c")
        client.send("textDocument/didChange", {"textDocument": {"uri": geo}, "contentChanges": [{"text": broken}]})
        here, there = client.diagnostics(geo), client.diagnostics(main)
        assert here[0]["code"] == "E-FIELD" and here[0]["range"]["start"]["line"] == 6
        assert there[0]["code"] == "E-FIELD" and there[0]["message"].startswith("src/geo.cairn:7: ")
    finally:
        client.close()


def test_code_lenses_run_each_test_and_main(project):
    text = MAIN + "\ntest doubles {\n  let mut p = geo.Pair(1, 1);\n  twice(p);\n  assert(p.a == 2);\n}\n"
    (project / "src/main.cairn").write_text(text)
    uri = (project / "src/main.cairn").resolve().as_uri()
    lenses = code_lenses(Document(text), uri, {uri: text})
    manifest = str((project / "cairn.toml").resolve())
    assert [
        (lens["command"]["title"], lens["command"]["command"], lens["command"]["arguments"]) for lens in lenses
    ] == [("Run", "cairn.run", [manifest]), ("Run test", "cairn.runTest", [manifest, "app.doubles"])]
    assert code_lenses(Document(text), "file:///nowhere/x.cairn", {}) == []  # nothing to run a buffer with


def test_a_lens_runs_exactly_its_own_test(project, capsys):
    """`doubles_too` fails and contains the name `doubles`: a lens that ran by substring would fail with it."""
    text = MAIN + "\ntest doubles {\n  let mut p = geo.Pair(1, 1);\n  twice(p);\n  assert(p.a == 2);\n}\n"
    text += "\ntest doubles_too {\n  assert(false);\n}\n"
    (project / "src/main.cairn").write_text(text)
    uri = (project / "src/main.cairn").resolve().as_uri()
    lens = code_lenses(Document(text), uri, {uri: text})[1]
    assert main(["test", *lens["command"]["arguments"][:1], "--test", lens["command"]["arguments"][1]]) == 0
    record = json.loads(capsys.readouterr().out)
    assert [t["name"] for t in record["blocks"]["tests"]] == ["app.doubles"] and record["tests"] == []
    assert main(["test", str(project), "--test", "app.double"]) == 2  # a name, never a prefix
    assert "named 'app.double'" in capsys.readouterr().out


def test_highlights_mark_where_a_name_is_bound_or_assigned():
    text = "fn f(n:u64) -> u64 {\n  let mut t:u64 = 0;\n  t += n;\n  return t;\n}\n"
    kinds = [(h["range"]["start"]["line"], h["kind"]) for h in document_highlights(Document(text), text.index("t:u64"))]
    assert kinds == [(1, 3), (2, 3), (3, 2)]  # bound, assigned, read


def test_workspace_symbols_search_the_projects_at_the_workspace_roots(project):
    client = Client()
    try:
        client.request("initialize", {"capabilities": {}, "rootUri": project.resolve().as_uri()}, 1)
        found = client.request("workspace/symbol", {"query": "sca"}, 2)["result"]
        assert [(s["name"], s["containerName"], s["location"]["uri"].rsplit("/", 1)[1]) for s in found] == [
            ("scale", "geo", "geo.cairn")
        ]
        main = (project / "src/main.cairn").resolve().as_uri()
        client.open(MAIN, uri=main)
        params = {"textDocument": {"uri": main}, "position": place(MAIN, "let mut pair", 8)}
        lit = client.request("textDocument/documentHighlight", params, 3)["result"]
        assert [(h["range"]["start"]["line"], h["kind"]) for h in lit] == [(7, 3), (8, 2), (9, 2), (10, 2)]
        lenses = client.request("textDocument/codeLens", {"textDocument": {"uri": main}}, 4)["result"]
        assert [(lens["command"]["command"], lens["range"]["start"]["line"]) for lens in lenses] == [("cairn.run", 6)]
    finally:
        client.close()


CONTINUED = (
    '[project]\nname = "geo"\nsources = ["src/geo.cairn", "src/more.cairn", "src/main.cairn"]\n'
    '[build]\nkind = "exe"\narch = "baseline"\n'
)
AREA = "module geo;\n\n// The square of a side.\npub fn area(x:u64) -> u64 = x * x;\n"
MORE = "pub fn double(x:u64) -> u64 = area(x) * 2;\n\ntest doubles {\n  assert(double(3) == 18);\n}\n"
ENTRY = "module app;\nimport geo;\n\nfn main() -> i32 {\n  if geo.double(3) != 18 { return 1; }\n  return 0;\n}\n"


@pytest.fixture
def continued(tmp_path):
    """more.cairn opens no module, so the compiler reads it as the rest of geo, where geo.cairn left off."""
    (tmp_path / "src").mkdir()
    (tmp_path / "cairn.toml").write_text(CONTINUED)
    for name, text in [("geo", AREA), ("more", MORE), ("main", ENTRY)]:
        (tmp_path / f"src/{name}.cairn").write_text(text)
    return tmp_path


def test_a_file_that_opens_no_module_is_in_the_module_the_compiler_says(continued):
    uri = (continued / "src/more.cairn").resolve().as_uri()
    geo = (continued / "src/geo.cairn").resolve().as_uri()
    client = Client()
    try:
        client.request("initialize", {"capabilities": {}, "rootUri": continued.resolve().as_uri()}, 1)
        assert client.open(MORE, uri=uri) == []
        at = {"textDocument": {"uri": uri}, "position": place(MORE, "area(x)")}
        shown = client.request("textDocument/hover", at, 5)["result"]["contents"]["value"]
        assert "fn area(x:u64) -> u64" in shown and "Effects: `trap`." in shown
        found = client.request("textDocument/definition", at, 6)["result"]
        assert found["uri"] == geo and found["range"]["start"] == {"line": 3, "character": 7}
        lenses = client.request("textDocument/codeLens", {"textDocument": {"uri": uri}}, 7)["result"]
        assert [lens["command"]["arguments"][1] for lens in lenses] == ["geo.doubles"]  # the name cairn test takes
        symbols = client.request("workspace/symbol", {"query": "double"}, 8)["result"]
        assert [(s["name"], s["containerName"]) for s in symbols] == [("double", "geo"), ("doubles", "geo")]
    finally:
        client.close()
    ws = workspace(uri, {uri: MORE})
    assert lines(references(ws, uri, MORE.index("area(x)"))) == [("geo.cairn", 3), ("more.cairn", 0)]
    edits = rename(ws, uri, MORE.index("area(x)"), "square")["changes"]
    assert sorted(u.rsplit("/", 1)[1] for u in edits) == ["geo.cairn", "more.cairn"]


def test_a_buffer_that_does_not_compile_still_reads_its_module_from_the_project(continued):
    """With no analysis to ask, the same rule is read from the combined source: the file before opened geo."""
    uri = (continued / "src/more.cairn").resolve().as_uri()
    broken = MORE.replace("area(x) * 2", "area(x) *")
    held = ws_module.context(uri, {uri: broken})
    doc = Document(broken, within=ws_module.within(*held, uri))
    assert doc.program is None and doc.module_at(broken.index("double")) == "geo"
    assert set(doc.modules()) == {"geo"}


def test_the_rule_read_from_tokens_is_the_compiler_s_on_every_example_project():
    root = pathlib.Path(__file__).resolve().parents[2]
    for manifest in sorted((root / "examples").rglob("cairn.toml")):
        project = load_project(manifest)
        for f in ws_module.files_of(project):
            inside = (project.source, f.start, project.site)
            asked, read = Document(f.text, within=inside), Document(f.text, analyse=False, within=inside)
            assert asked.program is not None and asked.modules() == read.modules(), (manifest, f.uri)


IMPLEMENTED = """module lib;

pub fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut s:u64 = 0;
  for i in 0..n { s = add_wrap(s, xs[i]); }
  return s;
}

pub fn total_by2(n:usize, xs:ro<u64>[n]) -> u64 implements total when n % 2 == 0 {
  let mut s:u64 = 0;
  for i in 0..n / 2 { s = add_wrap(s, add_wrap(xs[2 * i], xs[2 * i + 1])); }
  return s;
}

plan total use total_by2;
"""
SELECTING = (
    "module app;\nimport lib;\n\nfn main() -> i32 {\n  let mut xs = Buf[u64](4);\n  xs[1] = 5;\n"
    "  if lib.total(xs) != 5 { return 1; }\n  return 0;\n}\n"
)


@pytest.fixture
def implemented(tmp_path):
    (tmp_path / "src").mkdir()
    sources = '["src/lib.cairn", "src/main.cairn"]'
    (tmp_path / "cairn.toml").write_text(f'[project]\nname = "impl"\nsources = {sources}\n[build]\nkind = "exe"\n')
    (tmp_path / "src/lib.cairn").write_text(IMPLEMENTED)
    (tmp_path / "src/main.cairn").write_text(SELECTING)
    uri = (tmp_path / "src/lib.cairn").resolve().as_uri()
    return tmp_path, workspace(uri, {uri: IMPLEMENTED}), uri


@pytest.mark.parametrize(
    "needle,offset,expected",
    [
        ("fn total(", 3, [("lib.cairn", 2), ("lib.cairn", 8), ("lib.cairn", 14), ("main.cairn", 6)]),
        ("implements total", 11, [("lib.cairn", 2), ("lib.cairn", 8), ("lib.cairn", 14), ("main.cairn", 6)]),
        ("plan total", 5, [("lib.cairn", 2), ("lib.cairn", 8), ("lib.cairn", 14), ("main.cairn", 6)]),
        ("fn total_by2", 3, [("lib.cairn", 8), ("lib.cairn", 14)]),
        ("use total_by2", 4, [("lib.cairn", 8), ("lib.cairn", 14)]),
    ],
)
def test_references_follow_a_function_into_implements_and_plan_use(implemented, needle, offset, expected):
    _, ws, uri = implemented
    assert lines(references(ws, uri, IMPLEMENTED.index(needle) + offset)) == expected


@pytest.mark.parametrize(
    "needle,offset,fresh,reference,chosen",
    [
        ("implements total", 11, "sum_all", "lib.sum_all", "lib.total_by2"),  # the reference, from its clause
        ("use total_by2", 4, "pairs", "lib.total", "lib.pairs"),  # the implementation, from the plan that selects it
        ("let mut s", 8, "acc", "lib.total", "lib.total_by2"),  # a local of both: each function's own
    ],
)
def test_a_rename_through_implements_and_plan_use_keeps_the_selection(implemented, needle, offset, fresh, reference,
                                                                        chosen):  # fmt: skip
    root, ws, uri = implemented
    before = compile_source(load_project(root).source)[1]["functions"]
    edits = rename(ws, uri, IMPLEMENTED.index(needle) + offset, fresh)["changes"]
    for path in (root / "src/lib.cairn", root / "src/main.cairn"):
        doc = Document(path.read_text(), analyse=False)
        path.write_text(applied(doc, edits.get(path.resolve().as_uri(), [])))
    rebuilt = load_project(root)
    after = compile_source(rebuilt.source)[1]["functions"]
    assert after[reference]["runs"] == chosen and after[chosen]["implements"] == reference
    assert set(after[reference]["implementations"]) == {chosen}
    old = before["lib.total"]["implementations"]["lib.total_by2"]["identity"]
    assert after[reference]["implementations"][chosen]["identity"] != old  # the identity digests what is written
    record = build(rebuilt, cxx="clang++", timeout=120)
    assert subprocess.run([record["artifact"]], timeout=30).returncode == 0


def test_a_rename_of_a_parameterized_implementation_renames_every_instance(implemented):
    root, _, uri = implemented
    tuned = IMPLEMENTED.replace("total_by2(", "total_by[K:nat](").replace(
        "n % 2 == 0 {", "n % K == 0 tune K in [2, 4] {"
    )
    tuned = tuned.replace("n / 2 { s = add_wrap(s, add_wrap(xs[2 * i], xs[2 * i + 1])); }",
                          "n / K { for j in 0..K { s = add_wrap(s, xs[K * i + j]); } }")  # fmt: skip
    tuned = tuned.replace("use total_by2;", "use total_by[4];")
    (root / "src/lib.cairn").write_text(tuned)
    edits = rename(workspace(uri, {uri: tuned}), uri, tuned.index("use total_by") + 4, "blocked")["changes"]
    for path in (root / "src/lib.cairn", root / "src/main.cairn"):
        doc = Document(path.read_text(), analyse=False)
        path.write_text(applied(doc, edits.get(path.resolve().as_uri(), [])))
    after = compile_source(load_project(root).source)[1]["functions"]
    assert after["lib.total"]["runs"] == "lib.blocked[4]" and "use blocked[4];" in (root / "src/lib.cairn").read_text()
    assert set(after["lib.total"]["implementations"]) == {"lib.blocked[2]", "lib.blocked[4]"}
    assert after["lib.blocked[4]"]["instance_of"] == "lib.blocked"


def test_one_document_alone_follows_implements_and_plan_use():
    text = IMPLEMENTED.replace("module lib;\n\n", "")
    doc = Document(text)
    for needle, offset, count in (("implements total", 11, 3), ("use total_by2", 4, 2)):
        assert len(one_document_references(doc, "file:///alone.cairn", text.index(needle) + offset)) == count
        edits = one_document_rename(doc, "file:///alone.cairn", text.index(needle) + offset, "fresh")["changes"]
        compile_source(applied(doc, edits["file:///alone.cairn"]))


def test_each_file_shows_the_first_refusal_and_every_further_one_of_its_own(project):
    """The first refusal of the project reaches every open file, as it always has; each further one shows only in
    the file it is in, on its own range."""
    geo, main = ((project / f"src/{name}.cairn").resolve().as_uri() for name in ("geo", "main"))
    buffers = {
        geo: GEO.replace("p.a * p.b", "p.a * p.c"),
        main: MAIN.replace("  return 0;", "  let z:bool = 1;\n  return 0;"),
    }
    shown = {}
    for uri, text in buffers.items():
        doc = Document(text, within=ws_module.within(*ws_module.context(uri, buffers), uri))
        shown[uri] = [(d["code"], d["range"]["start"]["line"], d["message"].split("\n")[0]) for d in doc.diagnostics]
    assert shown[geo] == [("E-FIELD", 6, "Unknown field c.")]
    assert shown[main] == [
        ("E-FIELD", 0, "src/geo.cairn:7: Unknown field c."),
        ("E-TYPE-MISMATCH", 11, "Expected bool, got u64."),
    ]

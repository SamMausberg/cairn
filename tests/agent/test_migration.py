"""Checked interface migrations: one authorized signature change through every caller, in all files or in none."""

import json
import os

import pytest

from cairn.agent.hosts.edits import HANDLES, EditHost, EditSession
from cairn.agent.hosts.migration import Migration
from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.projects.project import load_project
from emitted import code_of as code

LIB = """module lib;
pub fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for i in 0..n { sum = add_wrap(sum, u32(bytes[i])); }
  return sum;
}
pub fn unrelated(x:u32) -> u32 = x;
"""
MAIN = """import lib;
fn frame(n:usize, bytes:ro<u8>[n]) -> u32 { return lib.checksum(bytes); }
fn twice(n:usize, bytes:ro<u8>[n]) -> u32 {
  let a = lib.checksum(bytes);
  return a + frame(bytes);
}
fn main() -> i32 { let t = twice("ab"); if t != 2 * (97 + 98) { return 1; } return 0; }
"""
TO = "fn checksum(n:usize, bytes:ro<u8>[n], seed:u32) -> u32"
NEW = {
    "lib.checksum": "pub fn checksum(n:usize, bytes:ro<u8>[n], seed:u32) -> u32 {\n  let mut sum:u32 = seed;\n"
    "  for i in 0..n { sum = add_wrap(sum, u32(bytes[i])); }\n  return sum;\n}",
    "frame": "fn frame(n:usize, bytes:ro<u8>[n]) -> u32 { return lib.checksum(bytes, 0); }",
    "twice": "fn twice(n:usize, bytes:ro<u8>[n]) -> u32 {\n  let a = lib.checksum(bytes, 0);\n  return a + frame(bytes);\n}",
}
GROWN = {  # frame now allocates, so twice binds its call first (E-EFFECT-ORDER otherwise)
    **NEW,
    "frame": "fn frame(n:usize, bytes:ro<u8>[n]) -> u32 {\n  let b = Buf[u8](1);\n  let s = lib.checksum(bytes, 0);\n"
    "  return s;\n}",
    "twice": "fn twice(n:usize, bytes:ro<u8>[n]) -> u32 {\n  let a = lib.checksum(bytes, 0);\n  let b = frame(bytes);\n"
    "  return a + b;\n}",
}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/lib.cairn").write_text(LIB)
    (tmp_path / "src/main.cairn").write_text(MAIN)
    (tmp_path / "cairn.toml").write_text('[project]\nname = "demo"\nsources = ["src/main.cairn", "src/lib.cairn"]\n')
    return tmp_path


def files(root):
    return {p: (root / p).read_text() for p in ("src/lib.cairn", "src/main.cairn")}


def reply(m, functions):
    return {"protocol": "cairn.migration/1", "authorization": m.authorization, "functions": functions}


def test_a_migration_finds_every_caller_across_files_and_writes_all_of_them(project):
    m = Migration(project, "lib.checksum", TO)
    packet = m.packet()
    assert m.callers == ["frame", "twice"] and set(packet["functions"]) == {"lib.checksum", "frame", "twice"}
    assert packet["functions"]["frame"] == {"file": "src/main.cairn", "role": "caller",
                                            "source": "fn frame(n:usize, bytes:ro<u8>[n]) -> u32 { return lib.checksum(bytes); }"}  # fmt: skip
    assert packet["change"]["lib.checksum"] == {"from": "fn checksum(n:usize, bytes:ro<u8>[n]@host) -> u32",
                                                "to": "fn checksum(n:usize, bytes:ro<u8>[n]@host, seed:u32) -> u32"}  # fmt: skip
    result = m.apply(reply(m, NEW))
    assert result["status"] == "applied" and set(result["files"]) == {"src/lib.cairn", "src/main.cairn"}
    after = files(project)
    assert "pub fn checksum(n:usize, bytes:ro<u8>[n], seed:u32) -> u32" in after["src/lib.cairn"]  # pub kept
    assert after["src/main.cairn"].count("lib.checksum(bytes, 0)") == 2 and "unrelated" in after["src/lib.cairn"]
    compile_source(load_project(project).source)  # The written tree is the checked one.
    assert not list(project.rglob("*.migration"))


def test_a_stale_authorization_changes_nothing(project):
    m = Migration(project, "lib.checksum", TO)
    (project / "src/main.cairn").write_text(MAIN + "// an edit made after the authorization\n")
    before = files(project)
    assert code(lambda: m.apply(reply(m, NEW))) == "E-SESSION" and files(project) == before


@pytest.mark.parametrize(
    ("functions", "error"),
    [
        ({**NEW, "lib.unrelated": "fn unrelated(x:u32) -> u32 = x + 1;"}, "E-MIGRATION-SCOPE"),  # widening
        ({**NEW, "main": "fn main() -> i32 { return 0; }"}, "E-MIGRATION-SCOPE"),
        ({**NEW, "lib.checksum": NEW["lib.checksum"].replace("seed:u32", "seed:u64")}, "E-SIGNATURE"),
        ({**NEW, "frame": NEW["frame"].replace("-> u32", "-> u64").replace("0);", "0) + 0;")}, "E-SIGNATURE"),
        ({"lib.checksum": NEW["lib.checksum"]}, "E-ARITY"),  # the callers were left behind
        ({**NEW, "frame": "fn other(x:u32) -> u32 = x;"}, "E-MIGRATION"),
        ({**NEW, "frame": "pub " + NEW["frame"]}, "E-MIGRATION"),  # visibility is not the reply's to change
        ({**NEW, "lib.checksum": NEW["lib.checksum"].removeprefix("pub ")}, "E-MIGRATION"),
        ({**NEW, "frame": NEW["frame"] + "\nconst SNEAK:u32 = 7;"}, "E-MIGRATION"),  # a declaration of its own
        ({**NEW, "frame": NEW["frame"] + "\nplan twice { grain 1; }"}, "E-MIGRATION"),  # a schedule for another
        ({**NEW, "frame": NEW["frame"] + "\nimport std.core (Option);"}, "E-MIGRATION"),
    ],
)  # fmt: skip
def test_a_refused_reply_writes_no_file(project, functions, error):
    m = Migration(project, "lib.checksum", TO)
    before = files(project)
    assert code(lambda: m.apply(reply(m, functions))) == error and files(project) == before


def test_a_reply_cannot_hide_a_declaration_behind_a_comment(tmp_path):
    """A replacement is spliced over the old declaration, so a trailing line comment would swallow whatever the
    file wrote after it on that line; the declarations of the whole file must survive, not only its functions."""
    path = tmp_path / "p.cairn"
    path.write_text("fn scale(x:u64) -> u64 = x * 2; const K:u64 = 3;\nfn main() -> i32 { return i32(scale(3)); }\n")
    before = path.read_text()
    m = Migration(path, "scale", "fn scale(x:u64, k:u64) -> u64")
    hidden = {
        "scale": "fn scale(x:u64, k:u64) -> u64 = x * k; //",
        "main": "fn main() -> i32 { return i32(scale(3, 2)); }",
    }
    assert code(lambda: m.apply(reply(m, hidden))) == "E-DECLARATION" and path.read_text() == before


def test_an_allowed_effect_may_be_gained(project):
    m = Migration(project, "lib.checksum", TO)
    assert code(lambda: m.apply(reply(m, GROWN))) == "E-CALLER-EFFECT"  # a Buf brings alloc, free and zero_init
    allowed = Migration(project, "lib.checksum", TO, effects=("alloc", "free", "zero_init"))
    assert allowed.apply(reply(allowed, GROWN))["status"] == "applied"


def test_a_refusal_names_the_file_and_line_of_the_text_the_reply_wrote(project):
    m = Migration(project, "lib.checksum", TO)
    typo = {**NEW, "lib.checksum": NEW["lib.checksum"].replace("= seed", "= sede")}
    with pytest.raises(Diagnostic) as refused:
        m.apply(reply(m, typo))
    data = refused.value.data
    assert data["code"] == "E-UNBOUND" and data["file"] == "src/lib.cairn" and data["line"] == 3


def test_a_failure_part_way_through_writing_puts_back_every_file(project, monkeypatch):
    m = Migration(project, "lib.checksum", TO)
    before, renamed = files(project), []
    real = os.replace

    def fail_second(source, target):
        if renamed:
            raise OSError("disk full")
        renamed.append(target)
        real(source, target)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(OSError):
        m.apply(reply(m, NEW))
    assert renamed and files(project) == before and not list(project.rglob("*.migration"))


def test_a_reply_is_bound_to_its_authorization_and_an_edit_session_never_reaches_this_class(project):
    m = Migration(project, "lib.checksum", TO)
    other = Migration(project, "lib.checksum", TO.replace("seed:u32", "seed:u64"))
    assert code(lambda: m.apply(reply(other, NEW))) == "E-SESSION"
    assert code(lambda: m.apply({**reply(m, NEW), "also": {}})) == "E-REQUEST"
    source = load_project(project).source
    host = EditHost()
    host.open(source, "frame")
    migrate = {"protocol": HANDLES, "handle": "e1", "kind": "migration", "authorization": m.authorization}
    assert code(lambda: host.respond(migrate)) == "E-REQUEST"
    s = EditSession(source, "lib.checksum")  # A one-function edit keeps its signature, whatever it asks.
    changed = {"protocol": "cairn.edit/1", "session": s.session, "kind": "body",
               "replacement": NEW["lib.checksum"].split(" -> u32 ", 1)[1]}  # fmt: skip
    assert code(lambda: s.check(changed)) == "E-UNBOUND"  # a body cannot reach a parameter it would need
    assert code(lambda: Migration(project, "lib.checksum", TO.replace("checksum", "digest"))) == "E-MIGRATION"


def test_migrate_prints_a_packet_and_applies_a_reply_from_the_command_line(project, capsys):
    from cairn.cli import main

    assert main(["migrate", str(project), "--symbol", "lib.checksum", "--to", TO]) == 0
    packet = json.loads(capsys.readouterr().out)
    (project / "reply.json").write_text(json.dumps({**packet["reply"], "functions": NEW}))
    assert (
        main(["migrate", str(project), "--symbol", "lib.checksum", "--to", TO, "--reply", str(project / "reply.json")])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert (
        main(["migrate", str(project), "--symbol", "lib.checksum", "--to", TO, "--reply", str(project / "reply.json")])
        == 1
    )
    assert json.loads(capsys.readouterr().out)["code"] == "E-SESSION"  # the tree it answered is gone

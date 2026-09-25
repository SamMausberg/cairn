"""A write keeps each file's own form: its byte-order mark, and its line endings.

`cairn tune --write`, plan sessions, edit sessions and implementation sessions all write through
`agent/write_back.py`. A file with CRLF endings, a byte-order mark or both is read and written as faithfully as a
plain one: the mark stays, every line of a CRLF file stays CRLF, and in a file of mixed endings the lines a write
leaves alone keep theirs while the lines it adds or changes take the ending most of the file's lines have.
"""

import shutil
from pathlib import Path

import pytest

from cairn.agent.mcp_tools import Tools
from cairn.agent.write_back import faithful
from cairn.compiler.cairnc import compile_source
from cairn.editor.formatting import format_source
from cairn.perf.plan_source import write_plan
from cairn.projects.project import load_project
from sources import cairn_sources

ROOT = Path(__file__).resolve().parents[2]
MARK = b"\xef\xbb\xbf"
MANIFEST = '[project]\nname = "twin"\nsources = ["src/main.cairn", "src/lib.cairn"]\n'
MAIN = "import lib;\n\nfn main() -> i32 { return 0; }\n"
LIB = """module lib;

// Fills out with a mixed value per element.
pub fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }

fn mix(v:u64) -> u64 = mul_wrap(v ^ shr(v, 29), 0xbf58476d1ce4e5b9);

pub fn bump(x:u64) -> u64 { return add_wrap(x, 1); }
"""
FORMS = {"crlf": (True, False), "mark": (False, True), "both": (True, True)}


def form(text: str, crlf: bool, mark: bool) -> bytes:
    return (MARK if mark else b"") + (text.replace("\n", "\r\n") if crlf else text).encode()


def project(root: Path, crlf: bool, mark: bool) -> Path:
    (root / "src").mkdir()
    (root / "cairn.toml").write_text(MANIFEST)
    (root / "src/main.cairn").write_bytes(form(MAIN, crlf, mark))
    (root / "src/lib.cairn").write_bytes(form(LIB, crlf, mark))
    return root


def kept_form(data: bytes, crlf: bool, mark: bool) -> str:
    """The file's text once its form is checked: the mark exactly when it had one, and one ending throughout."""
    assert data.startswith(MARK) == mark and data.count(MARK) == int(mark)
    text = data.removeprefix(MARK).decode()
    assert (text.count("\r\n") == text.count("\n")) if crlf else "\r" not in text
    return text.replace("\r\n", "\n")


def test_a_write_keeps_the_mark_and_ends_new_lines_as_the_file_does():
    new = "a\nb\nplan f { grain 1; }\nc\n"
    assert faithful(b"a\nb\nc\n", new) == new.encode()
    assert faithful(b"a\r\nb\r\nc\r\n", new) == new.replace("\n", "\r\n").encode()
    assert faithful(MARK + b"a\nb\nc\n", new) == MARK + new.encode()
    assert faithful(MARK + b"a\r\nb\r\nc\r\n", new) == MARK + new.replace("\n", "\r\n").encode()
    assert faithful(b"a\nb\nc\n", "a\r\nb\r\nz\r\n") == b"a\nb\nz\n"  # a reply's CRLF does not change an LF file
    assert faithful(b"a\r\nb", "a\nb\nc") == b"a\r\nb\r\nc"  # a last line without an ending keeps none


def test_mixed_endings_keep_every_untouched_line_and_new_lines_take_the_usual_ending():
    before = b"one\r\ntwo\r\nthree\nfour\r\nfive\n"  # three CRLF, two LF
    after = "zero\none\ntwo\nthree\nnew\nfour\nfive\nsix\n"
    assert faithful(before, after) == b"zero\r\none\r\ntwo\r\nthree\nnew\r\nfour\r\nfive\nsix\r\n"
    tie = b"one\r\ntwo\n"  # a tie is LF
    assert faithful(tie, "one\nmid\ntwo\n") == b"one\r\nmid\ntwo\n"
    assert faithful(b"x\r\ny\r\nz\n", "x\ny\nq\n") == b"x\r\ny\r\nq\r\n"  # a changed line takes the usual one


@pytest.mark.parametrize("name", FORMS)
def test_tune_write_writes_a_plan_into_a_crlf_or_marked_file(tmp_path, name):
    crlf, mark = FORMS[name]
    root = project(tmp_path, crlf, mark)
    assert write_plan(root / "cairn.toml", "lib.spread", {"grain": 1, "lanes": 8}) == "src/lib.cairn"
    text = kept_form((root / "src/lib.cairn").read_bytes(), crlf, mark)
    assert text == LIB.replace("} }\n", "} }\nplan spread { grain 1; lanes 8; }\n", 1)
    assert (root / "src/main.cairn").read_bytes() == form(MAIN, crlf, mark)  # a file the plan does not reach
    assert compile_source(load_project(root).source)[1]["functions"]["lib.spread"]["plan"] == {"grain": 1, "lanes": 8}


def test_tune_write_keeps_the_endings_of_a_mixed_file(tmp_path):
    root = project(tmp_path, False, False)
    lines = LIB.splitlines(keepends=True)
    mixed = "".join(line.replace("\n", "\r\n") if i % 3 else line for i, line in enumerate(lines))  # CRLF mostly
    assert "} }\n" in mixed  # the declaration the plan follows ends in LF
    (root / "src/lib.cairn").write_bytes(mixed.encode())
    write_plan(root / "cairn.toml", "lib.spread", {"grain": 1})
    written = (root / "src/lib.cairn").read_bytes().decode()
    assert written == mixed.replace("} }\n", "} }\nplan spread { grain 1; }\r\n", 1)


@pytest.mark.parametrize("name", FORMS)
def test_a_plan_session_writes_twice_into_a_crlf_or_marked_file(tmp_path, name):
    crlf, mark = FORMS[name]
    root = project(tmp_path, crlf, mark)
    tools = Tools(root)
    packet, failed = tools.call("plan_open", {"path": ".", "symbol": "lib.spread"})
    assert not failed
    answer, failed = tools.call("plan_reply", {"reply": {**packet["reply"], "items": {"grain": 1}}})
    assert not failed and answer["written"] == ["src/lib.cairn"]
    again = {**packet["reply"], "session": answer["next_session"], "items": {"grain": 64}}
    answer, failed = tools.call("plan_reply", {"reply": again})  # what it wrote is what it judged, endings aside
    assert not failed and answer["written"] == ["src/lib.cairn"], answer
    text = kept_form((root / "src/lib.cairn").read_bytes(), crlf, mark)
    assert text == LIB.replace("} }\n", "} }\nplan spread { grain 64; }\n", 1)


@pytest.mark.parametrize("name", FORMS)
def test_an_edit_session_writes_into_a_crlf_or_marked_file(tmp_path, name):
    crlf, mark = FORMS[name]
    root = project(tmp_path, crlf, mark)
    tools = Tools(root)
    packet, failed = tools.call("edit_open", {"path": ".", "symbol": "lib.bump"})
    assert not failed
    request = {**packet["draft_protocol"], "replacement": "{\n  return add_wrap(x, 2);\n}"}
    answer, failed = tools.call("edit_request", {"request": request})
    assert not failed and answer["written"] == ["src/lib.cairn"], answer
    text = kept_form((root / "src/lib.cairn").read_bytes(), crlf, mark)
    assert text == LIB.replace("{ return add_wrap(x, 1); }", "{\n  return add_wrap(x, 2);\n}")


@pytest.mark.skipif(not shutil.which("clang++"), reason="validation builds the implementation natively")
def test_an_implementation_session_writes_into_a_crlf_and_marked_file(tmp_path):
    shutil.copytree(ROOT / "examples/implementations", tmp_path / "impl")
    root = tmp_path / "impl"
    for path in cairn_sources(root / "src"):
        path.write_bytes(form(path.read_text(), True, True))
    tools = Tools(root)
    packet, failed = tools.call("implementation_open", {"path": ".", "reference": "prefix"})
    assert not failed, packet
    source = (root / "candidates/prefix_blocks.cairn").read_text()
    answer, failed = tools.call("implementation_submit", {"request": {**packet["reply"], "source": source}})
    assert not failed and answer["status"] == "validated" and answer["written"] == ["src/prefix.cairn"], answer
    text = kept_form((root / "src/prefix.cairn").read_bytes(), True, True)
    assert "fn prefix_blocks(" in text and format_source(text) == text  # one blank line parts each declaration

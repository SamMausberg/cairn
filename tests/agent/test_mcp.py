"""`cairn mcp`: a real subprocess driven through the Model Context Protocol, and the write-back behind it.

Each session opened on a path writes an admitted change into the file it came from and nothing else; a refused
request writes nothing; a file changed since the session read it makes the write stale (E-SESSION). Every read has a
deadline, so a server that hangs fails the suite instead of stalling it.
"""

import json
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cairn.agent import write_back
from cairn.agent.mcp import PROTOCOLS, Server
from cairn.agent.mcp_tools import TOOLS, Tools
from cairn.compiler.cairnc import compile_source
from cairn.editor.formatting import format_source
from cairn.projects.project import ProjectError, load_project

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 120
MANIFEST = '[project]\nname = "twin"\nsources = ["src/main.cairn", "src/lib.cairn", "src/other.cairn"]\n'
MAIN = "import lib;\nimport other;\n\nfn main() -> i32 { return 0; }\n"
LIB = """module lib;

// Fills out with a mixed value per element.
pub fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }

fn mix(v:u64) -> u64 = mul_wrap(v ^ shr(v, 29), 0xbf58476d1ce4e5b9);

pub fn bump(x:u64) -> u64 { return add_wrap(x, 1); }
"""
OTHER = "module other;\n\npub fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = u64(i); } }\n"


class Client:
    """`cairn mcp` in a process of its own, started in `where`."""

    def __init__(self, where: Path):
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "bin/cairn"), "mcp"], cwd=where,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)  # fmt: skip
        self.inbox: queue.Queue = queue.Queue()
        threading.Thread(target=self.pump, daemon=True).start()
        self.last = 0

    def pump(self) -> None:
        for line in self.proc.stdout:
            self.inbox.put(line)
        self.inbox.put(None)

    def send(self, text: str) -> None:
        self.proc.stdin.write(text.encode() + b"\n")
        self.proc.stdin.flush()

    def read(self) -> dict:
        line = self.inbox.get(timeout=TIMEOUT)
        assert line is not None, "the server ended"
        return json.loads(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        self.last += 1
        self.send(json.dumps({"jsonrpc": "2.0", "id": self.last, "method": method,
                              **({"params": params} if params is not None else {})}))  # fmt: skip
        answer = self.read()
        assert answer["id"] == self.last
        return answer

    def tool(self, name: str, **arguments) -> tuple[dict, bool]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})["result"]
        [content] = result["content"]
        assert content["type"] == "text"
        return json.loads(content["text"]), result["isError"]

    def close(self) -> int:
        self.proc.stdin.close()
        return self.proc.wait(timeout=TIMEOUT)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "cairn.toml").write_text(MANIFEST)
    for name, text in (("main", MAIN), ("lib", LIB), ("other", OTHER)):
        (tmp_path / f"src/{name}.cairn").write_text(text)
    return tmp_path


@pytest.fixture
def client(project):
    c = Client(project)
    yield c
    if c.proc.poll() is None:
        c.proc.kill()


def files(root: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted((root / "src").glob("*.cairn"))}


def test_the_handshake_negotiates_a_version_and_lists_a_few_one_sentence_tools(client):
    asked = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "test", "version": "0"}})["result"]  # fmt: skip
    assert asked["protocolVersion"] == "2025-06-18" and asked["capabilities"] == {"tools": {"listChanged": False}}
    assert asked["serverInfo"]["name"] == "cairn"
    client.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))  # no answer is owed
    assert client.request("ping")["result"] == {}
    tools = client.request("tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == [t["name"] for t in TOOLS] and len(tools) <= 8
    for t in tools:  # one sentence each, and a schema any client can read
        assert t["description"].count(". ") == 0 and t["description"].endswith(".")
        assert t["inputSchema"]["type"] == "object"
    newest = client.request("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}})["result"]
    assert newest["protocolVersion"] == PROTOCOLS[0]  # a version it does not speak gets its newest
    assert client.close() == 0


def test_protocol_errors_are_json_rpc_errors_and_the_session_goes_on(client):
    client.send("{not json")
    assert client.read()["error"]["code"] == -32700
    client.send(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "resources/list"}))
    assert client.read() == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32601, "message": "Unknown method "
                                                                   "resources/list."}}  # fmt: skip
    unknown = client.request("tools/call", {"name": "build", "arguments": {}})
    assert unknown["error"]["code"] == -32602 and "check" in unknown["error"]["message"]
    client.send(json.dumps({"jsonrpc": "1.0", "id": 8, "method": "ping"}))
    assert client.read()["error"]["code"] == -32600
    client.send(json.dumps([{"jsonrpc": "2.0", "id": 9, "method": "ping"}, {"jsonrpc": "2.0", "method": "x"}]))
    assert client.read() == [{"jsonrpc": "2.0", "id": 9, "result": {}}]
    record, failed = client.tool("check", path="src/main.cairn", source="fn main() -> i32 { return 0; }")
    assert failed and record["code"] == "E-REQUEST"  # arguments a tool cannot take are its result, not a crash
    assert client.request("ping")["result"] == {}


def test_check_answers_typed_or_the_refusal_at_its_file_and_line(client, project):
    record, failed = client.tool("check", path=".")
    assert not failed and record["status"] == "typed" and record["formal_status"] == "not-verified"
    (project / "src/lib.cairn").write_text(LIB.replace("add_wrap(x, 1)", "add_wrap(x, y)"))
    record, failed = client.tool("check", path=".")
    assert failed and record["code"] == "E-UNBOUND" and record["file"] == "src/lib.cairn" and record["line"] == 8
    record, failed = client.tool("check", source="fn f(x:u64) -> u64 { return x + 1; }\n")
    assert not failed and record["status"] == "typed"


def test_an_edit_session_writes_an_admitted_edit_back_and_nothing_else(client, project):
    before = files(project)
    packet, failed = client.tool("edit_open", path=".", symbol="lib.bump")
    assert not failed and packet["handle"] == "e1" and packet["write_back"] == "src/lib.cairn"
    request = {**packet["draft_protocol"], "replacement": "{ return add_wrap(x, 2); }"}
    answer, failed = client.tool("edit_request", request=request)
    assert not failed and answer["status"] == "typed" and answer["written"] == ["src/lib.cairn"]
    now = files(project)
    assert now["lib.cairn"] == LIB.replace("add_wrap(x, 1)", "add_wrap(x, 2)")
    assert {k: v for k, v in now.items() if k != "lib.cairn"} == {k: v for k, v in before.items() if k != "lib.cairn"}
    compile_source(load_project(project).source)


def test_a_refused_edit_writes_nothing(client, project):
    packet, _ = client.tool("edit_open", path=".", symbol="lib.bump")
    request = {**packet["draft_protocol"], "replacement": "{ return x + 1; }"}  # checked + may trap: a new effect
    answer, failed = client.tool("edit_request", request=request)
    assert failed and answer["code"] == "E-EFFECT-EXPANSION" and "written" not in answer
    expand = {**packet["expand_protocol"], "symbols": ["nothing_here"]}
    answer, failed = client.tool("edit_request", request=expand)
    assert failed and answer["code"] == "E-SYMBOL"
    assert files(project)["lib.cairn"] == LIB


def test_a_file_changed_since_the_session_read_it_makes_the_write_stale(client, project):
    packet, _ = client.tool("edit_open", path=".", symbol="lib.bump")
    (project / "src/other.cairn").write_text(OTHER + "\n// touched by a person\n")
    request = {**packet["draft_protocol"], "replacement": "{ return add_wrap(x, 2); }"}
    answer, failed = client.tool("edit_request", request=request)
    assert failed and answer["code"] == "E-SESSION" and answer["files"] == ["src/other.cairn"]
    assert files(project)["lib.cairn"] == LIB
    packet, _ = client.tool("edit_open", path=".", symbol="lib.bump")  # a new session reads the files again
    answer, failed = client.tool("edit_request", request={**request, "handle": packet["handle"]})
    assert not failed and answer["written"] == ["src/lib.cairn"]
    answer, failed = client.tool("edit_request", request={**request, "handle": packet["handle"],
                                                          "replacement": "{ return add_wrap(x, 3); }"})  # fmt: skip
    assert failed and answer["code"] == "E-SESSION"  # the session judged against what it read, which is gone
    assert "add_wrap(x, 2)" in files(project)["lib.cairn"]


def test_a_plan_session_on_a_named_module_writes_the_plan_into_that_module_s_file(client, project):
    packet, failed = client.tool("plan_open", path=".", symbol="lib.spread")
    assert not failed and packet["written_in"] == {"module": "lib", "file": "src/lib.cairn", "after_line": 4}
    answer, failed = client.tool("plan_reply", reply={**packet["reply"], "items": {"grain": 1, "lanes": 8}})
    assert not failed and answer["status"] == "admitted" and answer["written"] == ["src/lib.cairn"]
    text = files(project)["lib.cairn"]
    assert "out[i] = mix(u64(i)); } }\nplan spread { grain 1; lanes 8; }\n" in text
    assert files(project)["other.cairn"] == OTHER
    receipts = compile_source(load_project(project).source)[1]["functions"]
    assert receipts["lib.spread"]["plan"] == {"grain": 1, "lanes": 8} and "plan" not in receipts["other.spread"]
    refused, failed = client.tool("plan_reply", reply={**packet["reply"], "session": answer["next_session"],
                                                       "items": {"lanes": 5000}})  # fmt: skip
    assert failed and refused["code"] == "E-PLAN" and files(project)["lib.cairn"] == text
    again = {**packet["reply"], "session": answer["next_session"], "items": {"lanes": 4}}
    (project / "src/lib.cairn").write_text(text + "\n")
    answer, failed = client.tool("plan_reply", reply=again)
    assert failed and answer["code"] == "E-SESSION" and answer["files"] == ["src/lib.cairn"]
    assert files(project)["lib.cairn"] == text + "\n"


def test_a_plan_written_under_the_qualified_name_elsewhere_moves_into_its_module(client, project):
    (project / "src/main.cairn").write_text(MAIN + "plan lib.spread { lanes 2; }\n")
    packet, _ = client.tool("plan_open", path=".", symbol="lib.spread")
    assert packet["current"] == {"lanes": 2}
    answer, failed = client.tool("plan_reply", reply={**packet["reply"], "items": {"grain": 64}})
    assert not failed and answer["written"] == ["src/lib.cairn", "src/main.cairn"]  # two files, one change
    assert files(project)["main.cairn"] == MAIN and "plan spread { grain 64; }" in files(project)["lib.cairn"]


def test_state_and_a_delta_since_a_digest_this_server_sent(client, project):
    first, failed = client.tool("state", path=".")
    assert not failed and first["modules"]["lib"]["bump"][0] == "fn bump(x:u64) -> u64"
    (project / "src/lib.cairn").write_text(LIB.replace("pub fn bump(x:u64) -> u64 { return add_wrap(x, 1); }\n", ""))
    change, failed = client.tool("state", path=".", since=first["digest"])
    assert not failed and change["modules"] == {"lib": {"bump": None}}
    unknown, failed = client.tool("state", path=".", since="0" * 64)
    assert failed and unknown["code"] == "E-SESSION"


# The same rules without a process: the version table, and the split of a combined source into files.


def test_every_version_it_speaks_is_answered_as_asked():
    server = Server()
    for version in PROTOCOLS:
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": version, "capabilities": {}}})  # fmt: skip
        assert answer["result"]["protocolVersion"] == version
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}) is None
    assert server.handle([])["error"]["code"] == -32600


def test_a_combined_source_splits_back_into_the_files_it_came_from(project):
    loaded = load_project(project)
    assert loaded.split(loaded.source) == {"src/main.cairn": MAIN, "src/lib.cairn": LIB, "src/other.cairn": OTHER}
    both = loaded.source.replace("return 0;", "return 1;").replace("u64(i); }", "u64(i) + 1; }")
    parts = loaded.split(both)
    assert parts["src/main.cairn"] == MAIN.replace("return 0;", "return 1;") and parts["src/lib.cairn"] == LIB
    assert parts["src/other.cairn"] == OTHER.replace("u64(i); }", "u64(i) + 1; }")
    grown = loaded.source.replace(MAIN, MAIN + "\n\n")  # text added where one file ends and the next begins
    assert loaded.split(grown)["src/main.cairn"] == MAIN + "\n\n"
    forged = loaded.source.replace(MAIN, MAIN + "// source: src/lib.cairn\nfn f() { }\n")
    with pytest.raises(ProjectError, match="where no file begins"):  # text naming a file cannot move a boundary
        loaded.split(forged)
    single = load_project(project / "src/lib.cairn")
    assert single.split("fn f() { }\n") == {"lib.cairn": "fn f() { }\n"}


def test_an_implementation_session_writes_a_validated_implementation_beside_its_reference(tmp_path):
    """The example's reference, under the policy its regressions file pinned: a submission that reaches for the
    tolerance writes nothing, and the repaired blocked prefix sum validates and lands after the reference."""
    shutil.copytree(ROOT / "examples/implementations", tmp_path / "impl")
    root = tmp_path / "impl"
    original = (root / "src/prefix.cairn").read_text()
    client = Client(root)
    try:
        wrong, failed = client.tool("implementation_open", path=".", reference="prefix", policy={"budget": 8})
        assert failed and wrong["code"] == "E-TEST-POLICY"  # the project pinned its policy; the agent takes it
        packet, failed = client.tool("implementation_open", path=".", reference="prefix")
        assert not failed and packet["write_back"] == "src/prefix.cairn" and packet["pinned"]["tests"]["budget"] == 128
        source = (root / "candidates/prefix_blocks.cairn").read_text()
        loose = {**packet["reply"], "source": source, "tolerance": {"absolute": 1.0, "relative": 0.0}}
        refused, failed = client.tool("implementation_submit", request=loose)
        assert failed and refused["code"] == "E-TOLERANCE" and (root / "src/prefix.cairn").read_text() == original
        answer, failed = client.tool("implementation_submit", request={**packet["reply"], "source": source})
        assert not failed and answer["status"] == "validated" and answer["written"] == ["src/prefix.cairn"]
        assert answer["select_with"] == "plan prefix use prefix_blocks;" and not answer["selected"]
        written = (root / "src/prefix.cairn").read_text()
        assert written.startswith(original[: original.index("// Four elements")]) and "fn prefix_blocks(" in written
        assert format_source(written) == written  # cairn fmt --check still passes on the file it wrote
        receipts = compile_source(load_project(root).source)[1]["functions"]
        assert "prefix_blocks" in receipts["prefix"]["implementations"]
        kept = (root / ".cairn/history/records.jsonl").read_text().splitlines()
        assert [json.loads(r)["kind"] for r in kept] == ["failure", "validation"]  # both submissions, as history
    finally:
        client.close()


def test_a_session_writes_only_the_project_s_own_files_inside_the_served_directory(project, tmp_path_factory):
    (project / "deps/geo/src").mkdir(parents=True)
    (project / "deps/geo/cairn.toml").write_text('[project]\nname = "geo"\nsources = ["src/geo.cairn"]\n')
    geo = "module geo;\n\npub fn twice(x:u64) -> u64 { return add_wrap(x, x); }\n"
    (project / "deps/geo/src/geo.cairn").write_text(geo)
    (project / "cairn.toml").write_text(MANIFEST + '\n[dependencies]\ngeo = "deps/geo"\n')
    (project / "src/main.cairn").write_text("module app;\n" + MAIN)  # after geo's file, a file opens its own module
    tools = Tools(project)
    packet, failed = tools.call("edit_open", {"path": ".", "symbol": "geo.twice"})
    assert not failed
    request = {**packet["draft_protocol"], "replacement": "{ return add_wrap(x, add_wrap(x, 0)); }"}
    answer, failed = tools.call("edit_request", {"request": request})
    assert failed and "vendored" in answer["message"] and (project / "deps/geo/src/geo.cairn").read_text() == geo
    assert tools.edits.admitted["e1"] == []  # the host keeps no change the files did not take
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    (elsewhere / "x.cairn").write_text("fn f() -> u64 = 1;\n")
    answer, failed = tools.call("check", {"path": str(elsewhere / "x.cairn")})
    assert failed and answer["code"] == "E-REQUEST" and "outside" in answer["message"]


def test_a_file_saved_between_the_check_and_the_write_is_not_overwritten(project, monkeypatch):
    tools = Tools(project)
    packet, _ = tools.call("plan_open", {"path": ".", "symbol": "lib.spread"})
    real = write_back.replace

    def saved_meanwhile(root, texts, suffix=".cairn-write", expected=None):
        (root / "src/lib.cairn").write_text(LIB + "// saved by a person\n")
        return real(root, texts, suffix, expected)

    monkeypatch.setattr(write_back, "replace", saved_meanwhile)
    answer, failed = tools.call("plan_reply", {"reply": {**packet["reply"], "items": {"grain": 1}}})
    assert failed and answer["code"] == "E-SESSION" and answer["files"] == ["src/lib.cairn"]
    assert (project / "src/lib.cairn").read_text() == LIB + "// saved by a person\n"
    assert not list((project / "src").glob("*.cairn-write"))
    assert tools.plans.current["lib.spread"] == packet["session"]  # the host did not move on without the files

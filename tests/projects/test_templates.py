"""`cairn new --template NAME`: every template is a project that checks, builds, runs and passes its own test blocks
as it is created, and each does what its comments say, held to an independent model or to a live client."""

import json
import re
import socket
import subprocess
import time
from pathlib import Path

import pytest

from cairn.cli import create_project, main, templates
from cairn.compiler.cairnc import compile_source
from cairn.projects.build import build
from cairn.projects.project import ProjectError, load_project
from emitted import watched


def made(tmp_path, template, name="made"):
    root = tmp_path / name
    assert create_project(root, template)["status"] == "created"
    return root


@pytest.mark.parametrize("template", templates())
def test_every_template_is_a_project_whose_tests_pass_as_created(tmp_path, template, capsys):
    root = made(tmp_path, template)
    project = load_project(root)
    assert project.name == "made" and (root / ".gitignore").read_text() == "build/\n"
    compile_source(project.source)
    assert main(["test", str(root), "--format", "json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["blocks"]["tests"], "every template carries at least one test block"


def test_an_unknown_template_is_refused_and_nothing_is_made(tmp_path):
    with pytest.raises(ProjectError, match="No template 'gui'"):
        create_project(tmp_path / "made", "gui")
    assert not (tmp_path / "made").exists()


def wc(text: bytes) -> tuple[int, int, int]:
    """What wc counts: newlines, runs of bytes that are not white space, and bytes."""
    return text.count(b"\n"), len(text.split()), len(text)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_cli_template_counts_as_wc_does(tmp_path, cxx):
    root = made(tmp_path, "cli")
    one, two = tmp_path / "one.txt", tmp_path / "two words.txt"
    one.write_bytes(b"alpha beta\n\n  gamma\tdelta\r\nend")
    two.write_bytes(b"")
    record = build(load_project(root), cxx=cxx, timeout=180)
    done = subprocess.run([record["artifact"], str(one), str(two)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    rows = [(*wc(one.read_bytes()), str(one)), (*wc(two.read_bytes()), str(two))]
    rows.append((*(sum(r[k] for r in rows) for k in range(3)), "total"))
    assert done.stdout == "".join(f"{a:8d}{b:8d}{c:8d} {name}\n" for a, b, c, name in rows)
    missing = subprocess.run([record["artifact"], str(tmp_path / "absent")], capture_output=True, timeout=30)
    assert missing.returncode == 1 and missing.stdout == b""


def test_the_lib_template_builds_a_library_and_its_c_header(tmp_path, capsys):
    root = made(tmp_path, "lib", "checks")
    assert main(["build", str(root), "--header", "--format", "json"]) == 0
    record = json.loads(capsys.readouterr().out)
    header = Path(record["header"]).read_text(encoding="utf-8")
    assert record["artifact"].endswith("libchecks.so") and "crc32" in header and "Checksum" in header


def test_the_parallel_template_sums_what_the_closed_form_says_and_is_race_free(tmp_path):
    root = made(tmp_path, "parallel")
    record = build(load_project(root), cxx="clang++", timeout=180)
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and f"a million: {sum(i * i for i in range(10**6))}\n" in done.stdout
    cpp = compile_source(load_project(root).source)[0]
    raced = watched(tmp_path, cpp, "clang++", "thread")
    assert raced.returncode == 0 and "a million" in raced.stdout, raced.stderr[-2000:]  # it ran, under TSan


def test_the_service_template_echoes_each_client_and_stops_on_quit(tmp_path):
    root = made(tmp_path, "service", "echo")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    source = root / "src/main.cairn"
    source.write_text(re.sub(r"const PORT:u16 = \d+;", f"const PORT:u16 = {port};", source.read_text()))
    record = build(load_project(root), cxx="clang++", timeout=180)
    server = subprocess.Popen([record["artifact"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            try:
                first = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError:
                if server.poll() is not None:
                    err = server.communicate()[1]
                    if server.returncode == 2:
                        pytest.skip("this kernel refuses io_uring: " + err.strip())
                    raise AssertionError(f"the service exited {server.returncode}: {err}") from None
                time.sleep(0.05)
        second = socket.create_connection(("127.0.0.1", port), timeout=5)
        with first, second:
            first.sendall(b"hello\n")
            second.sendall(b"from two\n")
            assert first.recv(100) == b"hello\n" and second.recv(100) == b"from two\n"
            second.sendall(b"quit\n")
            assert server.wait(timeout=30) == 0
        assert f"listening on 127.0.0.1:{port}" in server.stdout.read()
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=10)

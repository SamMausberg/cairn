"""The applications under examples/apps: each one builds, runs and judges itself.

Every app returns 0 only when its own checks pass, so the assertions here are mostly "it built
and it agreed with itself". The service is the exception: Python plays the client.

Device programs run only under `make gpu`, one at a time (`support.device_reason`). Apps that import
std.io are built with clang++ only, because a sum carrying an owner (Result[File, IoError])
emits a designated initializer that g++ rejects under -Werror=missing-field-initializers.
"""

import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.build import build
from cairn.projects.project import load_project
from emitted import on_device

APPS = Path(__file__).resolve().parents[2] / "examples" / "apps"
NAMES = ["kvstore", "service", "simulator", "gpu_pipeline"]


def built(root, tmp_path, cxx="clang++", timeout=240):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    record = build(load_project(root), output=tmp_path / "build", cxx=cxx, timeout=timeout)
    assert record["status"] == "native-built", record.get("stderr", "")[:4000]
    return record["artifact"]


def copied(name, tmp_path):
    """A private copy of one app, so a test may edit its source without touching the tree."""
    target = tmp_path / name
    shutil.copytree(APPS / name, target, ignore=shutil.ignore_patterns("build"))
    return target


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize("name", NAMES)
def test_every_app_typechecks(name):
    generated, receipt = compile_source(load_project(APPS / name).source)
    assert receipt["function_count"] > 0
    # The app's own generics are all instantiated; what is left unchecked is std API it does
    # not use, which tests/language/test_std.py instantiates instead.
    assert all(name.startswith("std.") for name in receipt["uninstantiated_templates"])
    assert "int main" not in generated  # the entry point is added by the build, not the source


def test_kvstore_survives_reopen_and_compaction(tmp_path):
    artifact = built(APPS / "kvstore", tmp_path)
    done = subprocess.run([artifact], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    assert "self-check passed" in done.stdout


def test_kvstore_effects_show_the_log_and_the_index():
    receipt = compile_source(load_project(APPS / "kvstore").source)[1]["functions"]
    row = set(receipt["put"]["effects"])
    assert {"io", "ffi:write", "ffi:fsync", "alloc", "free"} <= row
    assert "write:s" in row and "read:f" in row


def talk(stream, line):
    stream.write(line + "\n")
    stream.flush()
    return stream.readline().strip()


def test_service_speaks_its_line_protocol(tmp_path):
    root = copied("service", tmp_path)
    source = root / "src" / "main.cairn"
    port = free_port()
    source.write_text(re.sub(r"const PORT:u16 = \d+;", f"const PORT:u16 = {port};", source.read_text()))
    artifact = built(root, tmp_path)
    server = subprocess.Popen([artifact], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        client = None
        for _ in range(100):  # the listener is up within a few milliseconds of exec
            try:
                client = socket.create_connection(("127.0.0.1", port), timeout=5)
                break
            except OSError as refused:
                if server.poll() is not None:
                    raise AssertionError("service exited before it listened") from refused
                time.sleep(0.05)
        assert client is not None, "service never accepted a connection"
        with client, client.makefile("rw", newline="\n") as stream:
            assert talk(stream, "put alpha hello world") == "+ok"
            assert talk(stream, "get alpha") == "= hello world"
            assert talk(stream, "get missing") == "-missing"
            assert talk(stream, "del alpha") == "+ok"
            assert talk(stream, "del alpha") == "-missing"
            assert talk(stream, "get alpha") == "-missing"
            assert talk(stream, "nonsense here") == "-error"
            assert talk(stream, "put kept value") == "+ok"
        # a second connection is served after the first one closes, and the table survives
        with (
            socket.create_connection(("127.0.0.1", port), timeout=5) as again,
            again.makefile("rw", newline="\n") as stream,
        ):
            assert talk(stream, "get kept") == "= value"
            assert talk(stream, "quit") == "+bye"
        assert server.wait(timeout=30) == 0
        assert f"listening on {port}" in server.stdout.read()
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=10)


@pytest.mark.parametrize("name", ["simulator", "gpu_pipeline"])
def test_device_apps_agree_with_the_host(tmp_path, name):
    with on_device():
        artifact = built(APPS / name, tmp_path, timeout=290)
        done = subprocess.run([artifact], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    assert "agree" in done.stdout or "matches the host" in done.stdout


def test_simulator_writes_the_same_lane_body_three_ways():
    """The three back ends differ only in placement, and only the device one is a kernel."""
    receipt = compile_source(load_project(APPS / "simulator").source)[1]["functions"]
    assert "par:host" in receipt["step_threads"]["effects"]
    assert "par:device" in receipt["step_device"]["effects"]
    assert not {"par:host", "par:device"} & set(receipt["step_loop"]["effects"])
    assert set(receipt["blend"]["effects"]) <= {"local_read", "local_write"}


def test_gpu_pipeline_transfers_are_visible():
    receipt = compile_source(load_project(APPS / "gpu_pipeline").source)[1]["functions"]
    row = set(receipt["main"]["effects"])
    assert {"transfer:h2d", "transfer:d2h", "gpu_alloc", "gpu_free", "par:device"} <= row

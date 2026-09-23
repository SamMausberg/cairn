"""The applications under examples/apps: each one builds, runs and judges itself.

Every app returns 0 only when its own checks pass, so the assertions here are mostly "it built
and it agreed with itself". The service is the exception: Python plays the client.

Device programs run only under `make gpu`, one at a time (`support.device_reason`). Apps that import
std.io are built with clang++ only, because a sum carrying an owner (Result[File, IoError])
emits a designated initializer that g++ rejects under -Werror=missing-field-initializers.
"""

import os
import re
import resource
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.project import load_project
from emitted import artifact, on_device

APPS = Path(__file__).resolve().parents[2] / "examples" / "apps"
NAMES = ["kvstore", "service", "simulator", "gpu_pipeline", "wordfreq", "classifier", "panel", "matmul"]
OPEN_FILES = resource.getrlimit(resource.RLIMIT_NOFILE)


def built(root, tmp_path, cxx="clang++", timeout=240):
    return artifact(root, cxx, timeout, output=tmp_path / "build")


def copied(name, tmp_path):
    """A private copy of one app, so a test may edit its source without touching the tree."""
    target = tmp_path / name
    shutil.copytree(APPS / name, target, ignore=shutil.ignore_patterns("build"))
    return target


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_every_app_is_listed():
    """An app under examples/apps that no test names would go unchecked; analytics has a module of its own."""
    assert sorted([*NAMES, "analytics"]) == sorted(p.name for p in APPS.iterdir() if (p / "cairn.toml").is_file())


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


def started_service(tmp_path, cxx="clang++", clients=None, descriptors=None):
    """The service built on a free port and running, with a client connection factory. `clients` shrinks its
    table; `descriptors` is the soft limit on its open files from the start."""
    root = copied("service", tmp_path)
    source = root / "src" / "main.cairn"
    port = free_port()
    text = re.sub(r"const PORT:u16 = \d+;", f"const PORT:u16 = {port};", source.read_text())
    if clients is not None:
        text = re.sub(r"const CLIENTS:usize = \d+;", f"const CLIENTS:usize = {clients};", text)
    source.write_text(text)
    limit = (lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (descriptors, OPEN_FILES[1]))) if descriptors else None
    artifact = built(root, tmp_path, cxx)
    server = subprocess.Popen([artifact], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=limit)

    def connect():
        for _ in range(100):  # the listener is up within a few milliseconds of exec
            try:
                return socket.create_connection(("127.0.0.1", port), timeout=5)
            except OSError as refused:
                if server.poll() is not None:
                    raise AssertionError("service exited before it listened") from refused
                time.sleep(0.05)
        raise AssertionError("service never accepted a connection")

    return server, connect, port


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_service_serves_clients_at_once_from_one_thread(tmp_path, cxx):
    """Three clients connected together, their requests interleaved: the ring answers each as its line arrives,
    where the blocking service served one connection until it closed. Only the main thread ever runs."""
    server, connect, _ = started_service(tmp_path, cxx)
    try:
        streams = [connect().makefile("rw", newline="\n") for _ in range(3)]
        assert talk(streams[0], "put shared 1") == "+ok"
        assert talk(streams[1], "get shared") == "= 1"
        assert talk(streams[2], "put shared 3") == "+ok"
        assert talk(streams[0], "get shared") == "= 3"
        threads = int(Path(f"/proc/{server.pid}/status").read_text().split("Threads:")[1].split()[0])
        assert threads == 1, f"the service runs {threads} threads"
        streams[1].close()  # one client leaves; the others are still served
        assert talk(streams[2], "del shared") == "+ok"
        assert talk(streams[0], "quit") == "+bye"
        assert server.wait(timeout=30) == 0  # the accept and the idle receive were cancelled, not awaited
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=10)


def stopped(server):
    if server.poll() is None:
        server.kill()
        server.wait(timeout=10)


def cpu_seconds(pid):
    """User plus system time the process has used, from /proc (fields 14 and 15, in clock ticks)."""
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def test_service_turns_away_a_client_past_its_table(tmp_path):
    """Overload is an answer: with a table of two, the third connection hears -busy and is closed, and the two
    it holds are served on as before."""
    server, connect, _ = started_service(tmp_path, clients=2)
    try:
        first, second = (connect().makefile("rw", newline="\n") for _ in range(2))
        assert talk(first, "put a 1") == "+ok" and talk(second, "get a") == "= 1"
        with connect() as third, third.makefile("r", newline="\n") as heard:
            assert heard.readline().strip() == "-busy"
            assert heard.readline() == ""  # and then the service closed it
        assert talk(first, "get a") == "= 1"
        assert talk(second, "quit") == "+bye"
        assert server.wait(timeout=30) == 0
    finally:
        stopped(server)


def test_service_waits_out_a_descriptor_shortage(tmp_path):
    """With no descriptor left, an accept fails at once; the service pauses before the next instead of spinning
    on it, and admits the waiting client as soon as another leaves."""
    server, connect, _ = started_service(tmp_path)
    try:
        first = connect()
        early = first.makefile("rw", newline="\n")
        assert talk(early, "put k v") == "+ok"
        held = len(list(Path(f"/proc/{server.pid}/fd").iterdir()))
        resource.prlimit(server.pid, resource.RLIMIT_NOFILE, (held, OPEN_FILES[1]))  # not one more
        waiting = connect()  # the kernel completes the handshake; the service cannot accept it yet
        before = cpu_seconds(server.pid)
        time.sleep(1.0)
        spent = cpu_seconds(server.pid) - before
        assert spent < 0.5, f"the service spent {spent:.2f}s of CPU in a second of refused accepts"
        early.close()
        first.close()  # its descriptor comes free, and the next attempt admits the waiting client
        with waiting, waiting.makefile("rw", newline="\n") as late:
            assert talk(late, "get k") == "= v"
            assert talk(late, "quit") == "+bye"
        assert server.wait(timeout=30) == 0
    finally:
        stopped(server)


def test_service_says_when_the_kernel_has_no_ring_for_it(tmp_path):
    """Descriptors 0 to 3 only: the listener takes the last, io_uring_setup fails with EMFILE, and the service
    reports it and exits 2 instead of trapping."""
    server, _, _ = started_service(tmp_path, descriptors=4)
    try:
        out, err = server.communicate(timeout=30)
        assert server.returncode == 2, (server.returncode, out, err)
        assert "no io_uring here, errno 24" in out and "listening" not in out
    finally:
        stopped(server)


def test_service_speaks_its_line_protocol(tmp_path):
    server, connect, port = started_service(tmp_path)
    try:
        client = connect()
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

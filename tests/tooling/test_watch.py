"""`cairn check --watch`: the same check as without it, again each time a file the project reads changes, until the
person interrupts it."""

import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOOD = "fn main() -> i32 = 0;\n"
BAD = "fn main() -> i32 = true;\n"


def reader(stream, lines):
    for line in stream:
        lines.put(line.rstrip("\n"))


def next_matching(lines, test, timeout=60):
    """Lines until one satisfies `test`; that one, or a failure at the deadline."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            line = lines.get(timeout=max(0.1, end - time.monotonic()))
        except queue.Empty:
            break
        if test(line):
            return line
    raise AssertionError("the watched check never said it")


def change(path, text):
    """Rewrite a file so its time moves on even on a coarse clock."""
    before = path.stat().st_mtime_ns
    path.write_text(text)
    while path.stat().st_mtime_ns == before:
        time.sleep(0.01)
        path.write_text(text)


def test_a_watched_check_answers_each_change_until_interrupted(tmp_path):
    source = tmp_path / "prog.cairn"
    source.write_text(GOOD)
    watched = subprocess.Popen(
        [sys.executable, str(ROOT / "bin/cairn"), "check", str(source), "--watch", "--format", "human"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=reader, args=(watched.stdout, lines), daemon=True).start()
    try:
        assert next_matching(lines, lambda line: line.startswith("typed:")) == "typed: 1 function"
        change(source, BAD)
        assert next_matching(lines, lambda line: line.startswith("error[")).startswith("error[E-TYPE-MISMATCH]")
        change(source, GOOD)
        assert next_matching(lines, lambda line: line.startswith("typed:")) == "typed: 1 function"
        watched.send_signal(signal.SIGINT)
        assert watched.wait(timeout=30) == 0  # an interrupt is how a watch ends, not a failure
    finally:
        if watched.poll() is None:
            watched.kill()
            watched.wait(timeout=10)


def test_a_broken_manifest_is_watched_until_it_is_fixed(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cairn").write_text(GOOD)
    manifest = tmp_path / "cairn.toml"
    manifest.write_text('[project]\nname = "p"\nsources = ["src/missing.cairn"]\n')
    watched = subprocess.Popen(
        [sys.executable, str(ROOT / "bin/cairn"), "check", str(tmp_path), "--watch", "--format", "human"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=reader, args=(watched.stdout, lines), daemon=True).start()
    try:
        assert "E-PROJECT-OR-ENVIRONMENT" in next_matching(lines, lambda line: line.startswith("unknown["))
        change(manifest, '[project]\nname = "p"\nsources = ["src/main.cairn"]\n')
        assert next_matching(lines, lambda line: line.startswith("typed:")) == "typed: 1 function"
        watched.send_signal(signal.SIGINT)
        assert watched.wait(timeout=30) == 0
    finally:
        if watched.poll() is None:
            watched.kill()
            watched.wait(timeout=10)

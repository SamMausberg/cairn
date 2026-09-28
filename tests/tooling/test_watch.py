"""`cairn check --watch`: the same check as without it, again each time a file the project reads changes, until the
person interrupts it."""

import contextlib
import json
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


@contextlib.contextmanager
def watching(target, form):
    """`cairn check TARGET --watch` running, with a queue of the lines it prints; killed if a test leaves it running."""
    watched = subprocess.Popen(
        [sys.executable, str(ROOT / "bin/cairn"), "check", str(target), "--watch", "--format", form],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=reader, args=(watched.stdout, lines), daemon=True).start()
    try:
        yield watched, lines
    finally:
        if watched.poll() is None:
            watched.kill()
            watched.wait(timeout=10)


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
    with watching(source, "human") as (watched, lines):
        assert next_matching(lines, lambda line: line.startswith("typed:")) == "typed: 1 function"
        change(source, BAD)
        assert next_matching(lines, lambda line: line.startswith("error[")).startswith("error[E-TYPE-MISMATCH]")
        change(source, GOOD)
        assert next_matching(lines, lambda line: line.startswith("typed:")) == "typed: 1 function"
        watched.send_signal(signal.SIGINT)
        assert watched.wait(timeout=30) == 0  # an interrupt is how a watch ends, not a failure


def test_a_broken_manifest_is_watched_until_it_is_fixed(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cairn").write_text(GOOD)
    manifest = tmp_path / "cairn.toml"
    manifest.write_text('[project]\nname = "p"\nsources = ["src/missing.cairn"]\n')
    with watching(tmp_path, "human") as (watched, lines):
        assert "E-PROJECT-OR-ENVIRONMENT" in next_matching(lines, lambda line: line.startswith("unknown["))
        change(manifest, '[project]\nname = "p"\nsources = ["src/main.cairn"]\n')
        assert next_matching(lines, lambda line: line.startswith("typed:")) == "typed: 1 function"
        watched.send_signal(signal.SIGINT)
        assert watched.wait(timeout=30) == 0


def test_a_watched_check_prints_one_json_record_per_line(tmp_path):
    """JSON Lines: a reader that takes a line at a time gets each round's whole record, an accepted round and a
    refused one alike, and nothing that is not a record."""
    source = tmp_path / "prog.cairn"
    source.write_text(GOOD)
    with watching(source, "json") as (watched, lines):
        first = json.loads(next_matching(lines, lambda line: line.strip() != ""))
        assert first["status"] == "typed" and first["functions"] == 1
        change(source, BAD)
        refused = json.loads(next_matching(lines, lambda line: line.strip() != ""))
        assert refused["status"] == "rejected" and refused["code"] == "E-TYPE-MISMATCH"
        watched.send_signal(signal.SIGINT)
        assert watched.wait(timeout=30) == 0
        while not lines.empty():  # whatever else came out is a whole record on its own line too
            left = lines.get()
            assert not left.strip() or isinstance(json.loads(left), dict)


def test_a_check_outside_a_watch_still_prints_the_indented_record_at_a_terminal(tmp_path, capsys, monkeypatch):
    from cairn.cli import main

    source = tmp_path / "prog.cairn"
    source.write_text(GOOD)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)  # a person who asked for the record
    assert main(["check", str(source), "--format", "json"]) == 0
    assert capsys.readouterr().out.startswith('{\n  "status": "typed"')
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)  # piped, one compact line
    assert main(["check", str(source), "--format", "json"]) == 0
    assert capsys.readouterr().out.startswith('{"status":"typed","functions":1,')

"""examples/apps/wordfreq against an independent oracle: Python's Counter over the same bytes.

The app is built as `cairn build` builds it, under both compilers, and once more under AddressSanitizer with leak
detection, then run on generated files with ties, case, digits, bytes past ASCII and an empty file. Its errors are
checked too: a file it cannot read, no file at all and a count that is not a number.
"""

import os
import random
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.project import load_project
from cairn.verify.runner import run_tests
from emitted import artifact, emit

APP = Path(__file__).resolve().parents[2] / "examples" / "apps" / "wordfreq"


def oracle(paths: list[Path], shown: int = 10) -> str:
    counts: Counter = Counter()
    for path in paths:
        counts.update(w.lower() for w in re.findall(rb"[A-Za-z0-9]+", path.read_bytes()))
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    width = len(str(rows[0][1])) if rows else 1
    lines = [f"{count:>{width}} {word.decode()}" for word, count in rows[:shown]]
    return "".join(line + "\n" for line in lines) + f"{sum(counts.values())} words, {len(counts)} distinct\n"


def corpus(tmp_path: Path) -> list[Path]:
    rng = random.Random(4096)
    vocabulary = ["the", "The", "THE", "cairn", "Lane", "lease", "x86", "64", "owner", "effect", "a", "b", "ab"]
    files = []
    for k in range(3):
        words = [rng.choice(vocabulary) for _ in range(rng.randrange(200, 2000))]
        separators = [" ", "\n", ", ", ".\t", "--", "é", "\xff"]
        text = "".join(w + rng.choice(separators) for w in words)
        path = tmp_path / f"part{k}.txt"
        path.write_bytes(text.encode("utf-8", "surrogateescape"))
        files.append(path)
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    return [*files, empty]


def built(tmp_path: Path, cxx: str) -> str:
    return artifact(APP, cxx, 240, output=tmp_path / "build")


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_counts_are_what_python_counts(tmp_path, cxx):
    artifact = built(tmp_path, cxx)
    files = corpus(tmp_path)
    done = subprocess.run([artifact, *map(str, files)], capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode() == oracle(files)
    few = subprocess.run([artifact, "-n", "3", str(files[0])], capture_output=True, timeout=60)
    assert few.stdout.decode() == oracle(files[:1], 3)
    none = subprocess.run([artifact, "-n", "0", str(files[3])], capture_output=True, timeout=60)
    assert none.stdout == b"0 words, 0 distinct\n"


def test_it_is_clean_under_address_sanitizer(tmp_path):
    """Every Vec it grows and every word it keeps is released: LeakSanitizer runs at exit."""
    cpp = compile_source(load_project(APP).source, "", ("main",))[0]
    source, executable = emit(tmp_path, cpp)
    flags = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    subprocess.run(["clang++", *flags, source, "-o", executable], check=True, timeout=240)
    files = corpus(tmp_path)
    env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1"}
    done = subprocess.run([executable, *map(str, files)], capture_output=True, timeout=120, env=env)
    assert done.returncode == 0 and b"Sanitizer" not in done.stderr, done.stderr[-3000:]
    assert done.stdout.decode() == oracle(files)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_its_test_blocks_pass(tmp_path, cxx):
    """words.cairn tests count_words through a Map, which no JSON contract can pass it."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    record = run_tests(load_project(APP), cxx=cxx, jobs=2, output=tmp_path / "build")
    assert record["status"] == "passed-test-blocks", record["tests"]
    assert [t["name"] for t in record["tests"]] == [
        "letters_and_case",
        "count_words_folds_case_and_counts_each_word_once",
    ]


def test_it_says_what_went_wrong(tmp_path):
    artifact = built(tmp_path, "clang++")
    missing = subprocess.run([artifact, str(tmp_path / "nowhere.txt")], capture_output=True, text=True, timeout=60)
    assert missing.returncode == 1 and missing.stderr == f"wordfreq: cannot read {tmp_path / 'nowhere.txt'}: errno 2\n"
    for arguments in ([], ["-n", "3"], ["-n", "three", "x.txt"]):
        wrong = subprocess.run([artifact, *arguments], capture_output=True, text=True, timeout=60)
        assert wrong.returncode == 2 and wrong.stderr == "usage: wordfreq [-n N] FILE...\n", arguments


def test_the_documented_run_is_what_it_prints(tmp_path):
    """docs/examples.md shows a run on tale.txt; this holds that text block to the program."""
    page = (APP.parents[2] / "docs/examples.md").read_text()
    shown = page.split("## examples/apps/wordfreq", 1)[1].split("```text\n", 1)[1].split("```", 1)[0]
    done = subprocess.run(
        [built(tmp_path, "clang++"), "-n", "5", str(APP / "tale.txt")], capture_output=True, timeout=60
    )
    assert done.stdout.decode() == shown == oracle([APP / "tale.txt"], 5)

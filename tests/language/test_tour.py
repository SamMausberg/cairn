"""docs/guide/tour.md is executable: every program in it is compiled, built and run; it must exit 0."""

import pathlib
import re
import shutil
import subprocess

import pytest

from cairn.compiler.cairnc import RUNTIME_FILES, compile_source

TOUR = (pathlib.Path(__file__).resolve().parents[2] / "docs/guide/tour.md").read_text(encoding="utf-8")
PROGRAMS = dict(re.findall(r"^## (\d+\. [^\n]+)\n.*?```cairn\n(.*?)```", TOUR, flags=re.S | re.M))


def test_the_tour_has_its_twelve_programs():
    assert len(PROGRAMS) == 12 and TOUR.count("```cairn") == 12


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
@pytest.mark.parametrize("title", PROGRAMS)
def test_a_tour_program_runs(tmp_path, title):
    source = PROGRAMS[title]
    entry = "app.main" if "module app;" in source else "main"
    cpp = compile_source(source, roots=(entry,))[0]
    (tmp_path / "p.cpp").write_text(
        cpp + f"int main() {{ return static_cast<int>(cf_{entry.replace('.', '_')}()); }}\n"
    )
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-pthread", "-fsanitize=address,undefined"]
    subprocess.run(["clang++", *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=180)
    assert subprocess.run([tmp_path / "p"], timeout=60).returncode == 0

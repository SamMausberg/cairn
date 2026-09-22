"""The tour in docs/guide.md is executable: every program in it is compiled, built and run; it must exit 0."""

import pathlib
import re

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import SANITIZED, run

GUIDE = (pathlib.Path(__file__).resolve().parents[2] / "docs/guide.md").read_text(encoding="utf-8")
TOUR = GUIDE.split("\n## The tour in twelve programs\n")[1].split("\n## Where to go next\n")[0]
PROGRAMS = dict(re.findall(r"^### (\d+\. [^\n]+)\n.*?```cairn\n(.*?)```", TOUR, flags=re.S | re.M))


def test_the_tour_has_its_twelve_programs():
    assert len(PROGRAMS) == 12 and TOUR.count("```cairn") == 12


@pytest.mark.parametrize("title", PROGRAMS)
def test_a_tour_program_runs(tmp_path, title):
    source = PROGRAMS[title]
    entry = "app.main" if "module app;" in source else "main"
    done = run(tmp_path, compile_source(source, roots=(entry,))[0], *SANITIZED, "-pthread", entry=entry)
    assert done.returncode == 0, done.stderr[-4000:]

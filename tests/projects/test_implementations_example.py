"""examples/implementations: a reference with two implementations runs natively under both compilers with the address
and leak sanitizers, its kept regression replays, and the scripted session refuses a wrong candidate with its shrunk
input and a looser tolerance, and admits the repaired one.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.project import load_project
from emitted import watched

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "examples/implementations"


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_selected_implementation_runs_clean_under_both_compilers(tmp_path, cxx):
    cpp, receipt = compile_source(load_project(HERE).source)
    assert receipt["functions"]["prefix"]["runs"] == "prefix_by4"
    done = watched(tmp_path, cpp, cxx, "address")
    assert done.returncode == 0 and "agree at every length" in done.stdout, done.stderr


def test_the_scripted_session_refuses_the_wrong_candidate_and_admits_the_repaired_one():
    kept = (HERE / "regressions/prefix.json").read_text()
    done = subprocess.run([sys.executable, str(HERE / "loop.py")], capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, done.stderr[-3000:]
    exchange = json.loads(done.stdout)["exchange"]
    first = exchange[0]["host"]
    assert first["code"] == "E-VALIDATION"
    assert first["finite"]["failed"]["inputs"]["n"] == 16
    assert first["finite"]["failed"]["inputs"]["xs"] == [0] * 7 + [1] + [0] * 8
    assert [step["host"].get("code") for step in exchange[1:3]] == ["E-TOLERANCE", "E-DOMAIN"]
    assert exchange[3]["host"]["status"] == "validated" and exchange[3]["host"]["finite"]["kept_cases"] == 1
    assert (HERE / "regressions/prefix.json").read_text() == kept  # the kept case was already there, unmoved

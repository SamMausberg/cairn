"""The preregistered baseline suite builds here, and says the same thing its preregistration does.

Nothing is timed. This test runs `bench/suite/harness.py --build-only` over one kernel, which builds
every arm under both compilers with the project's own flags and prices each arm's safety boundaries
against the CAIRN build receipt. It also checks the parts of the preregistration a script can check:
that the kernel table and the kernel directories agree, that every kernel is named in
PREREGISTRATION.md, that the harness refuses to write under `evidence/`, and that the one shape the
suite preregisters as refused really is refused with the code it names.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "bench/suite"
PY = sys.executable
needs_gcc = pytest.mark.skipif(not shutil.which("g++"), reason="g++ is not installed")
needs_clang = pytest.mark.skipif(not shutil.which("clang++"), reason="clang++ is not installed")

sys.path.insert(0, str(SUITE))
import harness  # noqa: E402


def test_the_kernel_table_and_the_kernel_directories_agree():
    on_disk = {p.name for p in (SUITE / "kernels").iterdir() if p.is_dir()}
    assert on_disk == set(harness.KERNELS), "a kernel is preregistered before it exists, and never after"
    for name, spec in harness.KERNELS.items():
        for arm in (*spec["cairn_arms"], *spec["arms"]):
            assert (SUITE / "kernels" / name / f"{arm}.cpp").exists(), f"{name} has no {arm} arm"
        assert (SUITE / "kernels" / name / "kernel.cairn").exists()
        assert (SUITE / "kernels" / name / "oracle.py").exists()


def test_every_kernel_is_named_in_the_preregistration():
    text = (SUITE / "PREREGISTRATION.md").read_text(encoding="utf-8")
    for name, spec in harness.KERNELS.items():
        assert name in text, f"{name} is measured but not preregistered"
        assert spec["claim"] in {"ratio", "semantic_difference", "expressiveness"}
    assert str(harness.WIN_RATIO) in text, "the acceptance threshold is preregistered, not chosen later"


def test_every_kernel_source_is_accepted():
    for name in harness.KERNELS:
        done = subprocess.run(
            [PY, "bin/cairn", "check", f"bench/suite/kernels/{name}/kernel.cairn"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=300,
        )
        assert done.returncode == 0, f"{name}: {done.stdout[-2000:]}{done.stderr[-2000:]}"
        assert json.loads(done.stdout)["status"] == "typed"


def test_a_preregistered_refusal_is_still_refused():
    """The suite reports an asymmetry only where the compiler really refuses the shape."""
    for name, spec in harness.KERNELS.items():
        if "rejects" not in spec:
            continue
        path, code = spec["rejects"]
        done = subprocess.run(
            [PY, "bin/cairn", "check", f"bench/suite/kernels/{name}/{path}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=300,
        )
        report = json.loads(done.stdout)
        assert report["status"] == "rejected" and report["code"] == code, report


def test_the_harness_never_writes_under_evidence(tmp_path):
    done = subprocess.run(
        [PY, "bench/suite/harness.py", "--build-only", "--out", str(ROOT / "evidence/nowhere")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert done.returncode != 0 and "evidence" in done.stderr


@needs_clang
@needs_gcc
def test_one_kernel_builds_every_arm_with_equal_boundaries(tmp_path):
    """Build only: every arm of one kernel, both compilers, the project's flags, nothing measured."""
    done = subprocess.run(
        [PY, "bench/suite/harness.py", "--build-only", "--kernels", "saxpy_f32", "--out", str(tmp_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=1800,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    record = json.loads((tmp_path / "suite.json").read_text())

    wanted = [b for b in record["builds"] if b["status"] != "unavailable"]
    assert wanted, "an arm whose library is unavailable is recorded, never silently dropped"
    for build in wanted:
        if build["status"] == "did-not-build":
            # A row whose schedule this host's runtime cannot link is a recorded fact with the
            # linker's own message, never a silent gap. Only the rows below are required to build.
            assert build["stderr"].strip(), f"{build['exe']} failed without saying why"
            continue
        assert not build["stderr"].strip(), f"{build['exe']} built with a warning:\n{build['stderr']}"
    for build in wanted:
        if build["grain_row"] in ("not_applicable", "library_default"):
            assert build["status"] == "built", f"{build['exe']} did not build:\n{build.get('stderr', '')}"

    safety = record["safety"]["saxpy_f32"]
    assert safety["cairn_boundaries"] == {"entry": 5, "element": 3, "arithmetic": 0, "conversion": 0}
    for name, arm in safety["arms"].items():
        guarded = "|guarded|" in name
        assert arm["equal_to_cairn"] is guarded, f"{name} should be {'equal' if guarded else 'unequal'} to cairn"
        assert arm["guard_failure_path"]["status"] == "read", f"{name}: {arm['guard_failure_path']}"
        want = "present" if guarded else "absent"
        assert arm["boundary_in_the_object"] == want, f"{name}: {arm['boundary_in_the_object']}"

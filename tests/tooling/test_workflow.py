"""The CI workflow's own rules: actions pinned by commit, read-only permissions, nothing run on a GPU, one check that
needs every job, and a device job that reaches every test compiling device code.

GitHub runs the workflow, not this suite, so these read its text: they fail on the change that would break a rule,
before a run shows it or, for the pins and the permissions, where no run would.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
CI = ROOT / ".github/workflows/ci.yml"


def code(path: Path) -> str:
    """A workflow without its comments."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line.split(" #")[0] for line in lines if not line.lstrip().startswith("#"))


def jobs(text: str) -> dict[str, str]:
    """Each job of a workflow by its id, with the text of its definition."""
    body = text[text.index("\njobs:\n") + len("\njobs:\n") :]
    heads = list(re.finditer(r"^  ([\w-]+):\n", body, re.M))
    ends = [m.start() for m in heads[1:]] + [len(body)]
    return {m.group(1): body[m.end() : end] for m, end in zip(heads, ends, strict=True)}


def test_every_action_is_pinned_by_commit_and_names_its_release():
    for path in WORKFLOWS:
        for used in re.findall(r"^\s*(?:- )?uses: (.+)$", path.read_text(encoding="utf-8"), re.M):
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+(\.\d+)*", used), f"{path.name}: {used}"


def test_every_workflow_reads_the_repository_and_writes_nothing():
    for path in WORKFLOWS:
        text = code(path)
        assert re.search(r"^permissions:\n  contents: read$", text, re.M), path.name
        assert not re.search(r":\s*write\b", text), path.name


def test_every_workflow_runs_on_pull_requests_and_on_main():
    for path in WORKFLOWS:
        text = code(path)
        assert re.search(r"^  pull_request:$", text, re.M) and re.search(r"^    branches: \[main\]$", text, re.M)


def test_no_workflow_runs_code_on_a_device():
    for path in WORKFLOWS:
        text = code(path)
        for gate in ("CAIRN_GPU_TESTS", "make gpu", "tune-device", "calibrate-device", "bench/gpu"):
            assert gate not in text, f"{path.name} names {gate}"


def test_the_one_check_a_pull_request_needs_needs_every_job():
    """Branch protection requires `ci-passed` alone, so a job added to the workflow counts only once it is here."""
    listed = jobs(code(CI))
    passed = listed.pop("ci-passed")
    assert re.search(r"^    name: ci-passed$", passed, re.M) and re.search(r"^    if: always\(\)$", passed, re.M)
    needs = re.search(r"^    needs: \[(.*)\]$", passed, re.M).group(1).split(", ")
    assert sorted(needs) == sorted(listed)
    assert 'test "$result" = success' in passed  # failed, cancelled and skipped all fail it


# What a test module that needs nvcc says: a device build by the suite's helper, or a skip without nvcc. Every module
# that ran nvcc in a full run of the suite says one of these.
DEVICE_SIGNS = re.compile(r'device_build\(|which\("nvcc"\)|\bNVCC\b|needs_nvcc')


def test_the_device_job_reaches_every_test_module_that_compiles_device_code():
    """The device job runs `make device-build` with nvcc present; a module outside its list would compile device
    code in no job at all, since no other runner has nvcc."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    listed = re.search(r"^DEVICE_TESTS = ((?:.*\\\n)*.*)$", makefile, re.M).group(1).replace("\\\n", " ").split()
    modules = {entry.split("::")[0] for entry in listed}
    assert all((ROOT / module).is_file() for module in modules), sorted(m for m in modules if not (ROOT / m).is_file())
    tests = sorted(p for p in (ROOT / "tests").rglob("test_*.py") if p.resolve() != Path(__file__).resolve())
    compiling = {str(p.relative_to(ROOT)) for p in tests if DEVICE_SIGNS.search(p.read_text(encoding="utf-8"))}
    assert compiling <= modules, f"add to DEVICE_TESTS in the Makefile: {sorted(compiling - modules)}"
    assert "make device-build PYTHON=python NVCC_HOST=${{ matrix.host }}" in code(CI)

"""The CI workflow's own rules: actions pinned by commit, read-only permissions, nothing run on a GPU, one check that
needs every job, and a device job that reaches every test compiling device code; and the owner's `make gpu`, which
reaches every test running device code.

GitHub runs the workflow, not this suite, so these read its text: they fail on the change that would break a rule,
before a run shows it or, for the pins and the permissions, where no run would.
"""

import ast
import re
from pathlib import Path

from emitted import DEVICE_RUNS
from support import device_reason

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
        for gate in ("CAIRN_GPU_TESTS", "CAIRN_GPU_TRAPS", "make gpu", "tune-device", "calibrate-device", "bench/gpu"):
            assert gate not in text, f"{path.name} names {gate}"


def test_the_one_check_a_pull_request_needs_needs_every_job():
    """Branch protection requires `ci-passed` alone, so a job added to the workflow counts only once it is here."""
    listed = jobs(code(CI))
    passed = listed.pop("ci-passed")
    assert re.search(r"^    name: ci-passed$", passed, re.M) and re.search(r"^    if: always\(\)$", passed, re.M)
    needs = re.search(r"^    needs: \[(.*)\]$", passed, re.M).group(1).split(", ")
    assert sorted(needs) == sorted(listed)
    assert 'test "$result" = success' in passed  # failed, cancelled and skipped all fail it


def test_a_pull_request_skips_only_the_compatibility_jobs_which_main_runs():
    """A pull request runs ten jobs and a push to main eighteen, and `ci-passed` accepts a skipped job on a pull
    request alone, so a compatibility job cannot be skipped where it is the gate."""
    listed = jobs(code(CI))
    skipped = {
        name
        for name, text in listed.items()
        if re.search(r"^    if: github.event_name != 'pull_request' *$", text, re.M)
    }
    assert skipped == {"compilers", "python", "arm"}
    assert """'[\"13.2\"]' || '[\"12.9\", \"13.2\"]'""" in listed["device"]
    assert (
        'test "$result" = success || { test "$EVENT" = pull_request && test "$result" = skipped; }'
        in listed["ci-passed"]
    )


def makefile_list(name: str) -> set[str]:
    """The test modules a list in the Makefile names, each of which exists."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    listed = re.search(rf"^{name} = ((?:.*\\\n)*.*)$", makefile, re.M).group(1).replace("\\\n", " ").split()
    modules = {entry.split("::")[0] for entry in listed}
    assert all((ROOT / module).is_file() for module in modules), sorted(m for m in modules if not (ROOT / m).is_file())
    return modules


def modules_saying(signs: re.Pattern) -> set[str]:
    """Every test module but this one whose text `signs` finds."""
    tests = sorted(p for p in (ROOT / "tests").rglob("test_*.py") if p.resolve() != Path(__file__).resolve())
    return {str(p.relative_to(ROOT)) for p in tests if signs.search(p.read_text(encoding="utf-8"))}


# What a test module that needs nvcc says: a device build by the suite's helper, or a skip without nvcc. Every module
# that ran nvcc in a full run of the suite says one of these.
DEVICE_SIGNS = re.compile(r'device_build\(|which\("nvcc"\)|\bNVCC\b|needs_nvcc')


def test_the_device_job_reaches_every_test_module_that_compiles_device_code():
    """The device job runs `make device-build` with nvcc present; a module outside its list would compile device
    code in no job at all, since no other runner has nvcc."""
    compiling, modules = modules_saying(DEVICE_SIGNS), makefile_list("DEVICE_TESTS")
    assert compiling <= modules, f"add to DEVICE_TESTS in the Makefile: {sorted(compiling - modules)}"
    assert "make device-build PYTHON=python NVCC_HOST=${{ matrix.host }}" in code(CI)


def test_make_gpu_runs_every_test_that_runs_device_code():
    """The owner's single device run is `make gpu`: a module outside GPU_TESTS would run its device code nowhere, and
    one inside it that runs none only lengthens that run. Its --device-runs keeps a test whose body or fixture calls
    a device-run helper, so no other function of such a module may call one."""
    running, modules = modules_saying(DEVICE_RUNS), makefile_list("GPU_TESTS")
    assert running == modules, (
        f"GPU_TESTS in the Makefile: add {sorted(running - modules)}, drop {sorted(modules - running)}"
    )
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "\tCAIRN_GPU_TESTS=1 $(PYTHON) -m pytest -q -p no:xdist --device-runs $(GPU_TESTS)\n" in makefile
    for module in sorted(modules):
        text = (ROOT / module).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.FunctionDef) and DEVICE_RUNS.search(ast.get_source_segment(text, node) or ""):
                fixture = any("fixture" in ast.unparse(d) for d in node.decorator_list)
                assert node.name.startswith("test_") or fixture, f"{module}: {node.name} runs device code"


def test_a_deliberate_device_trap_needs_a_variable_of_its_own(monkeypatch):
    """Under `make gpu` alone a test that traps on the device skips; the refusal comes before anything is asked of
    the machine, and a trap is never let through where no other device run would be."""
    monkeypatch.setenv("CAIRN_GPU_TESTS", "1")
    monkeypatch.delenv("CAIRN_GPU_TRAPS", raising=False)
    assert "CAIRN_GPU_TRAPS=1" in device_reason(trap=True)
    monkeypatch.delenv("CAIRN_GPU_TESTS")
    monkeypatch.setenv("CAIRN_GPU_TRAPS", "1")
    assert "CAIRN_GPU_TESTS=1" in device_reason(trap=True)

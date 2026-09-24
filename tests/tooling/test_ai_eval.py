"""The 1.1 evaluation's harness, offline: its arms, its three new tasks, its audit of the plugin arm and its analysis.

No model runs here. The plugin arm gets its own read-only copy of the plugin beside its sandbox, with its skill and
MCP tools allowed and no other skill or MCP server; the other arms keep the 1.0 session. The tally repair plants a
race that CAIRN's checker refuses, C++'s thread sanitizer reports and Rust can only write with `unsafe`.
"""

import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "bench" / "ai"
sys.path.insert(0, str(BENCH))
import analysis  # noqa: E402
import checking  # noqa: E402
import subjects  # noqa: E402
import tasks  # noqa: E402
from scoring import audit, breakdown  # noqa: E402

# bench/suite has a harness.py too, which tests/tooling/test_bench_suite.py imports as `harness`.
_spec = importlib.util.spec_from_file_location("ai_harness", BENCH / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

needs_tools = pytest.mark.skipif(
    not (shutil.which("clang++") and shutil.which("cargo") and shutil.which("setarch")),
    reason="the hidden check builds with clang++ and cargo and runs under setarch",
)


def test_the_study_has_thirteen_tasks_four_arms_and_rotates_the_arms():
    study = harness.STUDIES["v1_1"]
    assert study["tasks"][:10] == [t.name for t in tasks.ORIGINAL]
    assert study["tasks"][10:] == ["sieve", "block_scan", "tally"]
    assert study["arms"] == ("plugin", "cairn", "cpp", "rust") and study["jobs"] == 3
    cells = harness.order(study["tasks"], list(study["arms"]), "v1_1")
    assert len(cells) == 52 and cells[:4] == [("histogram", a) for a in ("plugin", "cairn", "cpp", "rust")]
    assert cells[4:8] == [("chunk_sums", a) for a in ("cairn", "cpp", "rust", "plugin")]
    assert harness.order(["varint"], ["cairn", "cpp", "rust"]) == [
        ("varint", "rust"),
        ("varint", "cairn"),
        ("varint", "cpp"),
    ]


def test_every_arm_reads_the_same_task_and_only_the_plugin_arm_may_read_the_plugin():
    for task in tasks.TASKS:
        texts = {arm: subjects.task_md(task, arm) for arm in subjects.LANGUAGE}
        assert len({t.split("## Your environment")[0] for t in texts.values()}) == 1, task.name
        assert "the files of the CAIRN plugin" in texts["plugin"]
        assert all("the files of the CAIRN plugin" not in texts[a] for a in ("cairn", "cpp", "rust"))
        assert "docs/" not in texts["plugin"].split("## Your environment")[1]
        assert "The CAIRN plugin for Claude Code is installed" in texts["plugin"]
    scan = tasks.BY_NAME["block_scan"]
    assert "--emulate --device-target sm_120" in subjects.task_md(scan, "cairn")
    assert "std::barrier" in subjects.task_md(scan, "cpp") and "std::sync::Barrier" in subjects.task_md(scan, "rust")


def test_the_plugin_arm_gets_no_docs_in_its_sandbox_and_a_read_only_plugin_beside_it(tmp_path):
    task = tasks.BY_NAME["tally"]
    box = subjects.sandbox(task, "plugin", tmp_path / "box")
    assert {str(p.relative_to(box)) for p in box.rglob("*") if p.is_file()} == {
        "TASK.md",
        "cairn.toml",
        "src/main.cairn",
    }
    docs = subjects.sandbox(task, "cairn", tmp_path / "docs")
    assert (docs / "docs" / "guide.md").exists()
    source = tmp_path / "toolchain" / "plugin"
    for part in subjects.PLUGIN_PARTS:
        shutil.copytree(ROOT / part, source / part)
    copy = subjects.plugin_copy(source, tmp_path / "plugins" / "tally" / "plugin")
    assert (copy / "skills" / "cairn" / "SKILL.md").exists() and (copy / "docs" / "language.md").exists()
    assert not any(p.name.startswith("reference") for p in copy.rglob("*")) and not (copy / "bench").exists()
    with pytest.raises(PermissionError):
        (copy / "skills" / "cairn" / "SKILL.md").write_text("changed")
    subjects.unlock(copy)
    assert not copy.exists()


def test_the_plugin_session_loads_only_the_plugin():
    argv = subjects.command(subjects.LIMITS, Path("/runs/plugins/x/plugin"))
    assert "--restricted" in argv and "--strict-mcp-config" not in argv and "--disable-slash-commands" not in argv
    assert argv[argv.index("--plugin-dir") + 1] == argv[argv.index("--add-dir") + 1] == "/runs/plugins/x/plugin"
    assert set(argv[argv.index("--tools") + 1].split(",")) == {"Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill"}
    assert argv[argv.index("--allowedTools") + 1].split() == [*"Bash Read Write Edit Glob Grep Skill".split(),
                                                              "mcp__plugin_cairn_cairn"]  # fmt: skip
    plain = subjects.command(subjects.LIMITS)
    assert "--plugin-dir" not in plain and "--strict-mcp-config" in plain


def test_a_subject_of_the_study_sees_its_own_tmp_and_work_and_the_runs_stay_outside_tmp(tmp_path):
    hide = subjects.hidden(Path("/runs"))
    assert (str(ROOT), "0555") in hide and ("/runs/runs", "0755") in hide and ("/runs/tmp", "0755") in hide
    assert (str(Path.home() / ".claude" / "projects"), "1777") in hide
    argv = subjects.isolated(["claude", "-p", "hello"], Path("/runs/tmp/x/cpp"), [Path("/runs/runs/x/cpp")], hide)
    assert argv[:2] == ["unshare", "-Urm"] and "--pid" in argv and "--kill-child" in argv
    assert argv[-4:] == ["--", "claude", "-p", "hello"]
    spec = json.loads(argv[argv.index("/usr/bin/python3") + 2])
    assert spec["tmp"] == "/runs/tmp/x/cpp" and spec["keep"] == ["/runs/runs/x/cpp"] and spec["uid"] == os.getuid()
    assert harness.STUDIES["v1_1"]["isolated"] and not harness.STUDIES["v1_0"]["isolated"]
    assert Path("/tmp") not in harness.STUDIES["v1_1"]["root"].parents
    with pytest.raises(SystemExit):
        harness.main(["--study", "v1_1", "run", "--phase", "counted", "--root", "/tmp/cairn-aieval"])
    with pytest.raises(SystemExit):
        harness.main(["--study", "v1_1", "run", "--phase", "primary", "--root", str(tmp_path)])


@pytest.mark.skipif(not shutil.which("unshare"), reason="isolation needs unshare")
def test_an_isolated_command_sees_its_own_tmp_and_sandbox_and_not_the_repository(tmp_path):
    root, sandbox, scratch = tmp_path / "root", tmp_path / "root" / "runs" / "x", tmp_path / "root" / "tmp" / "x"
    other = root / "runs" / "y"
    for d in (sandbox, scratch, other):
        d.mkdir(parents=True)
    (sandbox / "mine.txt").write_text("mine\n")
    (other / "theirs.txt").write_text("theirs")
    hide = [(str(ROOT), "0555"), (str(root / "runs"), "0755")]
    probe = f"ls {ROOT}; ls {root / 'runs'}; cat {sandbox / 'mine.txt'}; echo x > /tmp/made; id -u; ls /proc | grep -c '^[0-9]'"
    done = subprocess.run(subjects.isolated(["sh", "-c", probe], scratch, [sandbox], hide), capture_output=True,
                          text=True, timeout=60)  # fmt: skip
    if done.returncode != 0 and "Operation not permitted" in done.stderr:
        pytest.skip("this machine does not allow user namespaces")
    assert done.returncode == 0, done.stderr
    # The checkout is empty, only its own run is there, and it sees only its own few processes.
    seen = done.stdout.split()
    assert seen[:3] == ["x", "mine", str(os.getuid())] and int(seen[3]) < 10, seen
    assert (scratch / "made").read_text() == "x\n" and (ROOT / "bench").exists()


def test_only_the_plugin_arm_has_a_copy_of_the_plugin_to_remove():
    places = {arm: harness.plugin_place(Path("/runs"), "counted", 2, "sieve", arm) for arm in subjects.LANGUAGE}
    assert places == {"plugin": Path("/runs/plugins/counted/r2/sieve/plugin"), "cairn": None, "cpp": None, "rust": None}


def test_the_audit_allows_the_subjects_own_plugin_and_flags_another_and_a_write_into_it(tmp_path):
    plugin = "/root/plugins/counted/r1/x/plugin"
    other = "/root/plugins/counted/r2/x/plugin"
    uses = [
        {
            "type": "tool_use",
            "id": "a",
            "name": "Read",
            "input": {"file_path": f"{plugin}/skills/cairn/cards/owners.md"},
        },
        {
            "type": "tool_use",
            "id": "b",
            "name": "Bash",
            "input": {"command": f"cat {plugin}/skills/cairn/../../docs/memory.md"},
        },
        {"type": "tool_use", "id": "c", "name": "Read", "input": {"file_path": f"{other}/skills/cairn/SKILL.md"}},
        {"type": "tool_use", "id": "d", "name": "Edit", "input": {"file_path": f"{plugin}/skills/cairn/SKILL.md"}},
        {"type": "tool_use", "id": "e", "name": "Skill", "input": {"skill": "cairn:cairn"}},
        {"type": "tool_use", "id": "f", "name": "mcp__plugin_cairn_cairn__check", "input": {"path": "."}},
        {"type": "tool_use", "id": "g", "name": "Bash", "input": {"command": "cd .. && ls"}},
    ]
    results = [{"type": "tool_result", "tool_use_id": "f", "content": '{"status": "rejected", "code": "E-LEASED"}'}]
    lines = [{"type": "assistant", "message": {"content": uses}}, {"type": "user", "message": {"content": results}}]
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines))
    found = audit(transcript, "/root/runs/counted/r1/x/plugin", "/root", plugin)
    kinds = sorted((f["why"], f["what"][:24]) for f in found["flags"])
    assert kinds == [
        ("parent", "cd .. && ls"),
        ("path", "/root/plugins/counted/r2"),
        ("plugin-write", "/root/plugins/counted/r1"),
    ]
    assert found["compile_runs"] == 1 and found["compile_runs_failed"] == 1
    described = breakdown(transcript, "cairn", plugin)
    assert described["docs_calls"] == 4  # its own two reads, the edit and the skill


def test_the_audit_flags_a_search_of_the_whole_file_system_and_a_result_that_shows_another_subjects_work(tmp_path):
    root, sandbox = "/root", "/root/runs/counted/r1/x/cairn"
    commands = ["find / -maxdepth 6 -iname docs 2>/dev/null", "grep -rn blocks / 2>/dev/null", "cd / && ls",
                "python3 -c 'print(7 / 2)'", "find . -name '*.cairn'", "cat src/main.cairn"]  # fmt: skip
    results = [
        "",
        "",
        "",
        "3.5",
        "/root/runs/counted/r1/x/plugin/src/main.cairn",
        "see bench/ai/tasks/x/reference.cairn",
    ]
    uses = [{"type": "tool_use", "id": str(k), "name": "Bash", "input": {"command": c}} for k, c in enumerate(commands)]
    back = [{"type": "tool_result", "tool_use_id": str(k), "content": r} for k, r in enumerate(results)]
    lines = [{"type": "assistant", "message": {"content": uses}}, {"type": "user", "message": {"content": back}}]
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines))
    found = audit(transcript, sandbox, root)
    assert [f["why"] for f in found["flags"]].count("root") == 3  # nor a division in a script
    shown = [f["what"] for f in found["flags"] if f["why"] == "result"]
    assert any(what.startswith("/root/runs/counted/r1/x/plugin/") for what in shown)  # another subject's program
    assert any("bench/ai/tasks" in what for what in shown)  # a task's reference, by its folder
    assert len(found["flags"]) == 5


def row(task, replicate, arm, solved, usd, tokens, failure=None, contaminated=False):
    return {"task": task, "replicate": replicate, "arm": arm, "solved": solved, "tokens_cost_usd": usd,
            "tokens_total": tokens, "failure": None if solved else failure, "contaminated": contaminated,
            "turns": 10, "wall_seconds": 60.0, "tokens_output": 1, "compile_runs": 2, "compile_runs_failed": 0,
            "stop": "success"}  # fmt: skip


def test_cost_per_solved_task_charges_failures_to_the_solved_and_the_bootstrap_is_fixed():
    arms = ("plugin", "cpp")
    rows = [row("a", 1, "plugin", True, 1.0, 100), row("a", 1, "cpp", True, 0.5, 50),
            row("b", 1, "plugin", False, 3.0, 300, "abort"), row("b", 1, "cpp", True, 0.5, 50),
            row("c", 1, "plugin", True, 2.0, 200), row("c", 1, "cpp", False, 1.0, 100, "sanitizer"),
            row("d", 1, "plugin", True, 9.0, 900, contaminated=True), row("d", 1, "cpp", True, 0.5, 50)]  # fmt: skip
    result = analysis.analyse(rows, arms)
    b = result["bootstrap"]
    assert b["plugin.usd_per_solved"]["estimate"] == 3.0  # 6 dollars over 2 solved; the contaminated subject is out
    assert b["cpp.usd_per_solved"]["estimate"] == round(2.5 / 3, 4)
    assert b["plugin/cpp.usd_per_solved"]["estimate"] == round(3.0 / (2.5 / 3), 4)
    assert b["plugin.solve_rate"]["estimate"] == round(2 / 3, 4)
    low, high = b["plugin/cpp.usd_per_solved"]["interval_95"]
    assert low == "inf" or high == "inf" or low <= b["plugin/cpp.usd_per_solved"]["estimate"] <= high
    assert analysis.analyse(rows, arms) == result  # the same seed gives the same intervals
    assert result["safety_failures"] == {"plugin": {"sanitizer": 0, "abort": 1, "panic": 0, "unsafe": 0},
                                         "cpp": {"sanitizer": 1, "abort": 0, "panic": 0, "unsafe": 0}}  # fmt: skip
    assert result["solved_pairs"]["plugin/cpp"] == {"cells": 3, "only_plugin": 1, "only_cpp": 1, "mcnemar_p": 1.0}
    assert (
        analysis.per_solved([{"plugin": row("a", 1, "plugin", False, 1.0, 1)}], "plugin", "tokens_cost_usd") == math.inf
    )
    assert "| plugin/cpp |" in analysis.markdown({**result}, rows, arms)


def cairn_check(source: str, where: Path) -> dict:
    (where / "src").mkdir(parents=True)
    (where / "cairn.toml").write_text(checking.CAIRN_TOML)
    (where / "src" / "main.cairn").write_text(source)
    done = subprocess.run([sys.executable, str(ROOT / "bin" / "cairn"), "check", str(where), "--format", "json"],
                          capture_output=True, text=True, timeout=300)  # fmt: skip
    return json.loads(done.stdout)


@pytest.mark.parametrize("name", [t.name for t in tasks.CHECKED])
def test_each_new_cairn_reference_types_and_the_tally_starter_is_refused_for_its_race(name, tmp_path):
    read = lambda which: (BENCH / "tasks" / name / f"{which}.cairn").read_text()
    assert cairn_check(read("reference"), tmp_path / "reference")["status"] == "typed"
    starter = cairn_check(read("starter"), tmp_path / "starter")
    if name == "tally":  # the shared tally lent to every task: refused before anything runs
        assert starter["status"] == "rejected" and starter["code"] == "E-LEASED", starter
    else:
        assert starter["status"] == "typed"


def test_the_constructs_the_judge_requires():
    scan, sieve = tasks.BY_NAME["block_scan"], tasks.BY_NAME["sieve"]
    ok = lambda task, language, text: all(p.search(text) for p in checking.constructs(task, language))
    assert ok(scan, "cairn", "blocks b in g threads t in 256 { barrier; } buffer x:u64[n]@device = zeroed;")
    assert not ok(scan, "cairn", "blocks b in g threads t in 256 { barrier; }")  # a host region: no device memory
    assert not ok(scan, "cpp", "std::thread t; t.join();") and ok(scan, "cpp", "std::thread t; std::barrier b(2);")
    assert ok(scan, "rust", "thread::scope(|s| {}); Barrier::new(4);") and not ok(
        scan, "rust", "thread::scope(|s| {});"
    )
    assert ok(sieve, "cairn", "spawn f(x) into g;") and not ok(sieve, "cpp", "int main() {}")
    assert checking.constructs(tasks.BY_NAME["records"], "cpp") == []


@needs_tools
@pytest.mark.parametrize("language", checking.LANGUAGES)
def test_the_tally_reference_passes_and_its_starter_fails_where_each_language_catches_the_race(language, tmp_path):
    task = tasks.BY_NAME["tally"]
    read = lambda which: (BENCH / "tasks" / "tally" / f"{which}.{checking.EXTENSION[language]}").read_text()
    passed = checking.judge(task, language, read("reference"), scratch=tmp_path / "reference")
    assert passed.passed, passed.record()
    failed = checking.judge(task, language, read("starter"), scratch=tmp_path / "starter")
    # C++ loses counts in the address build or, if the race happens to lose none, is reported by the thread sanitizer.
    want = {"cpp": ("output", "sanitizer"), "rust": ("unsafe",), "cairn": ("build",)}[language]
    assert not failed.passed and failed.reason in want, failed.record()

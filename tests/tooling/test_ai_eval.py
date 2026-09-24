"""The 1.1 evaluation's harness, offline: its arms, its three new tasks, its audit of the plugin arm and its analysis.

No model runs here. The plugin arm gets its own read-only copy of the plugin beside its sandbox, with its skill and
MCP tools allowed and no other skill or MCP server; the other arms keep the 1.0 session. The tally repair plants a
race that CAIRN's checker refuses, C++'s thread sanitizer reports and Rust can only write with `unsafe`.
"""

import json
import math
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
import harness  # noqa: E402
import subjects  # noqa: E402
import tasks  # noqa: E402
from scoring import audit, breakdown  # noqa: E402

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

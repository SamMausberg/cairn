"""The equal-budget AI benchmark's harness, offline: no model runs here.

Every task has its spec, a starter and a reference in each of the three languages; its SPEC.md example agrees with
the oracle, and every hidden case follows the input rules the subject is given. For two tasks, one of them threaded,
each language's reference passes the hidden check and its starter fails it, through the same builds, sanitizers and
comparisons that judge a subject. `python3 bench/ai/harness.py verify` runs that check for every task. The 1.1
evaluation's arms, tasks and analysis are tested in test_ai_eval.py.
"""

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "bench" / "ai"
sys.path.insert(0, str(BENCH))
import checking  # noqa: E402
import subjects  # noqa: E402
import tasks  # noqa: E402

needs_tools = pytest.mark.skipif(
    not (shutil.which("clang++") and shutil.which("cargo") and shutil.which("setarch")),
    reason="the hidden check builds with clang++ and cargo and runs under setarch",
)


def test_every_task_has_a_spec_and_six_programs_and_is_preregistered():
    assert {p.name for p in (BENCH / "tasks").iterdir() if p.is_dir()} == set(tasks.BY_NAME)
    for study, names in (("PREREGISTRATION.md", tasks.ORIGINAL), ("PREREGISTRATION_V1_1.md", tasks.TASKS)):
        text = (BENCH / study).read_text(encoding="utf-8")
        for task in names:
            assert f"`{task.name}`" in text, f"{task.name} is not named in {study}"
    for task in tasks.TASKS:
        for which in ("starter", "reference"):
            for ext in checking.EXTENSION.values():
                assert (BENCH / "tasks" / task.name / f"{which}.{ext}").exists(), (task.name, which, ext)


def test_every_example_agrees_with_its_oracle_and_every_hidden_case_follows_the_rules():
    for task in tasks.TASKS:
        given, wanted = tasks.example(task)
        assert task.oracle(given) == wanted, task.name
        cases = tasks.hidden_cases(task)
        assert cases == tasks.hidden_cases(task), f"{task.name}: the hidden cases must not change between runs"
        assert all(tasks.VALID[task.name](c) for c in cases), task.name


def test_a_repair_starter_is_its_reference_with_one_small_planted_defect():
    # A repair of the 1.1 evaluation plants a defect CAIRN's checker refuses, so its CAIRN fix is held apart
    # (tests/tooling/test_ai_eval.py); in C++ and Rust it is as small as every other repair's.
    for task in (t for t in tasks.TASKS if t.kind == "repair"):
        for language, ext in checking.EXTENSION.items():
            if task in tasks.CHECKED and language == "cairn":
                continue
            starter = (BENCH / "tasks" / task.name / f"starter.{ext}").read_text().splitlines()
            reference = (BENCH / "tasks" / task.name / f"reference.{ext}").read_text().splitlines()
            changed = [line for line in reference if line not in starter] + [
                line for line in starter if line not in reference
            ]
            assert 1 <= len(changed) <= 6, (task.name, ext, changed)


def test_every_task_md_states_the_same_rules_in_every_language():
    for task in tasks.TASKS:
        texts = {language: subjects.task_md(task, language) for language in checking.LANGUAGES}
        heads = {t.split("## Your environment")[0] for t in texts.values()}
        assert len(heads) == 1, f"{task.name}: the task itself must read the same in every language"
        assert all("Do not read, list or search any file outside it" in t for t in texts.values())


def test_the_sandbox_holds_the_task_the_starter_and_for_cairn_the_documentation(tmp_path):
    task = tasks.BY_NAME["varint"]
    box = subjects.sandbox(task, "cairn", tmp_path / "box")
    names = {str(p.relative_to(box)) for p in box.rglob("*") if p.is_file()}
    assert {"TASK.md", "cairn.toml", "src/main.cairn", "docs/guide.md", "docs/language.md"} <= names
    assert not any(n.startswith(("bench", "tests", "src/cairn")) or "reference" in n for n in names)
    rust = subjects.sandbox(task, "rust", tmp_path / "rust")
    assert {str(p.relative_to(rust)) for p in rust.rglob("*") if p.is_file()} == {
        "TASK.md",
        "Cargo.toml",
        "src/main.rs",
    }


def test_the_subject_session_is_restricted_and_has_no_web_tools():
    argv = subjects.command(subjects.LIMITS)
    assert "--restricted" in argv and "--strict-mcp-config" in argv and "--disable-slash-commands" in argv
    tools = argv[argv.index("--tools") + 1].split(",")
    assert set(tools) == {"Bash", "Read", "Write", "Edit", "Glob", "Grep"}
    env = subjects.environment(Path("/nowhere/bin"))
    assert {k for k in env if k.startswith(("CLAUDE", "CAIRN"))} == {"CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"}
    assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"


def test_the_audit_flags_a_path_outside_the_sandbox_and_the_network(tmp_path):
    import json

    from scoring import audit

    transcript = tmp_path / "t.jsonl"
    uses = [
        {
            "type": "tool_use",
            "id": "a",
            "name": "Bash",
            "input": {"command": "cat /home/someone/cairn/bench/ai/tasks/x"},
        },
        {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "curl https://example.com"}},
        {
            "type": "tool_use",
            "id": "c",
            "name": "Bash",
            "input": {"command": "clang++ main.cpp -o /tmp/mine && /tmp/mine"},
        },
        {"type": "tool_use", "id": "d", "name": "Read", "input": {"file_path": "/root/runs/pilot/x/cpp/main.cpp"}},
    ]
    spill = str(Path.home() / ".claude/projects/-root-runs-counted-x-cairn/s/tool-results/b.txt")
    uses.append({"type": "tool_use", "id": "e", "name": "Bash", "input": {"command": f"cat {spill}; echo $((j*n//k))"}})
    lines = [
        {"type": "system", "message": "a line whose message is text"},
        {"type": "assistant", "message": {"content": uses}},
    ]
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines))
    found = audit(transcript, "/root/runs/counted/x/cairn", "/root")
    kinds = [(f["why"], f.get("what", "")[:20]) for f in found["flags"]]
    assert ("path", "/home/someone/cairn/") in kinds
    assert any(why == "network" for why, _ in kinds)
    assert ("path", "/root/runs/pilot/x/c") in kinds  # another subject's sandbox
    assert not any("/tmp/mine" in what for _, what in kinds)  # its own scratch file
    assert len(found["flags"]) == 3  # nor its own saved tool output, nor a floor division
    assert found["compile_runs"] == 1


def test_a_doubled_slash_still_names_a_path_but_a_floor_division_or_a_comment_does_not(tmp_path):
    import json

    from scoring import audit

    commands = ["cat //etc/hostname", "ls ///home", "echo $(((n+1)//2)) $((7//2))", "printf '//note\\n' > a.c"]
    uses = [{"type": "tool_use", "id": str(k), "name": "Bash", "input": {"command": c}} for k, c in enumerate(commands)]
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"content": uses}}) + "\n")
    found = audit(transcript, "/root/runs/counted/x/cairn", "/root")
    assert sorted(f["what"] for f in found["flags"]) == ["/etc/hostname", "/home"]


@needs_tools
@pytest.mark.parametrize("name", ["split_sum", "varint"])
@pytest.mark.parametrize("language", checking.LANGUAGES)
def test_the_reference_passes_the_hidden_check_and_the_starter_fails_it(name, language, tmp_path):
    task = tasks.BY_NAME[name]
    read = lambda which: (BENCH / "tasks" / name / f"{which}.{checking.EXTENSION[language]}").read_text()
    passed = checking.judge(task, language, read("reference"), scratch=tmp_path / "reference")
    assert passed.passed, passed.record()
    failed = checking.judge(task, language, read("starter"), scratch=tmp_path / "starter")
    assert not failed.passed and failed.reason in ("output", "exit", "abort", "panic", "sanitizer"), failed.record()

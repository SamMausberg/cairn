"""Every script under tools/ and bench/ still runs here, and still says something true.

Each test drives the real command line from the testing section of docs/MANUAL.md in a child process and checks one
fact out of that tool's own JSON or stdout, so a tool that quietly stops working fails here
instead of at a release gate. Nothing needs a network; a missing compiler, Z3 or GPU skips with
a reason. Tools are one test each so `-n` spreads the slow ones (verify, validate_semantics,
curriculum_verify and validate_systems are the long poles). The four harnesses that share
results/*.o take a file lock, because they overwrite the same artifacts.
"""

import fcntl
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
RESULTS = ROOT / "results"
TOOLS = sorted(p for top in ("tools", "bench") for p in (ROOT / top).rglob("*.py") if p.name != "__init__.py")
needs_gcc = pytest.mark.skipif(not shutil.which("g++"), reason="g++ is not installed")
needs_z3 = pytest.mark.skipif(not shutil.which("z3"), reason="the z3 solver is not installed")
needs_clang = pytest.mark.skipif(not shutil.which("clang++"), reason="clang++ is not installed")


def tool(*args, timeout=300, expect=0, env=None):
    """Run one tool exactly as an operator would and return its stdout."""
    done = subprocess.run(
        [PY, *[str(a) for a in args]],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )
    assert done.returncode == expect, (
        f"{args[0]} exited {done.returncode}\n{done.stdout[-2000:]}\n{done.stderr[-4000:]}"
    )
    return done.stdout


def parsed(text):
    """The JSON object a tool printed, even when it printed a human line first."""
    start = text.index("{")
    return json.loads(text[start : text.rindex("}") + 1])


@contextmanager
def exclusive():
    """One writer at a time for the harnesses that all regenerate results/native.{cpp,o}."""
    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / ".smoke.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def test_host_profile_is_this_machine():
    from support import ARCHS, FAMILY, best_profile, profile_flags

    chosen = best_profile("clang++")
    assert chosen in ARCHS and chosen != "baseline"
    marches = [f for f in profile_flags("exe", chosen) if f.startswith("-march=")]
    assert marches == [f"-march={chosen}"], "the toolchain table owns -march, and states it once"
    assert FAMILY in {"x86-64", "armv8-a"}


@pytest.mark.parametrize("path", TOOLS, ids=lambda p: p.name)
def test_no_architecture_is_hard_wired(path):
    """No script may name a CPU profile itself; cairn.toolchain resolves every one of them."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = path.read_text(encoding="utf-8")
    for doc in [ast.get_docstring(tree, clean=False)] + [
        ast.get_docstring(n, clean=False)
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]:
        if doc:
            body = body.replace(doc, "")
    code = "\n".join(line.split("#")[0] for line in body.splitlines())
    for banned in ["x86", "AMD64", "-march=", "aarch64", "armv8", "armv9"]:
        assert banned not in code, f"{path.name} names {banned} instead of asking cairn.toolchain"


@needs_clang
def test_build(tmp_path):
    out = parsed(tool("tools/release/build.py", "examples/basics/native.cairn", "--out", tmp_path))
    assert out["status"] == "built" and Path(out["library"]).exists()
    receipt = json.loads((tmp_path / "native_receipt.json").read_text())
    assert receipt["native_build"]["arch"] == out["arch"] and receipt["function_count"] > 0
    # Generated code may include any runtime header, so the build must leave all of them behind.
    assert {p.name for p in tmp_path.glob("*.hpp")} >= {"cairn_runtime.hpp", "cairn_owners.hpp", "cairn_parallel.hpp"}


@needs_clang
@needs_gcc
@pytest.mark.skipif(os.environ.get("CAIRN_VERIFY"), reason="this suite is already a child of tools/checks/verify.py")
def test_verify():
    """The whole native gate except the suite selection, which would otherwise re-enter this file."""
    with exclusive():
        out = parsed(
            tool(
                "tools/checks/verify.py",
                "--gcc",
                "--sanitize",
                "--tests",
                "tests/language/test_compiler.py",
                timeout=900,
            )
        )
    assert out["status"] == "all requested checks passed" and out["commands"] >= 14
    record = json.loads((RESULTS / "verification_run.json").read_text())
    assert [c["exit_code"] for c in record["commands"]] == [0] * len(record["commands"])
    assert record["environment"]["arch_profile"] == out["arch_profile"]
    assert (RESULTS / "libnative_gcc.so").exists() and (RESULTS / "sanitize").exists()


@needs_clang
def test_codegen_only():
    with exclusive():
        printed = tool("bench/cpu/codegen_only.py", timeout=600)
    result = json.loads((RESULTS / "codegen.json").read_text())
    rows = result["comparisons"]
    assert len(rows) == 9 and all(r["cairn_bytes"] > 0 and r["cpp_bytes"] > 0 for r in rows)
    assert result["identical_sections"] == sum(r["bytes_equal"] and r["relocations_equal"] for r in rows)
    assert f"{result['identical_sections']} of 9" in printed and not result["native_timing_performed"]


@needs_clang
def test_bench_run():
    with exclusive():
        tool("bench/cpu/run.py", timeout=900)
    summary = json.loads((RESULTS / "timing_summary.json").read_text())
    assert summary and all(row["pairs"] > 1 and row["cairn_median_ns"] > 0 for row in summary)
    where = json.loads((RESULTS / "benchmark_environment.json").read_text())
    assert where["pinned_cpu"] in where["available_cpus"] and where["arch_profile"] in where["flags"][-2]
    assert len(json.loads((RESULTS / "codegen_equivalence.json").read_text())) == 9


def test_density(tmp_path):
    with exclusive():
        printed = tool("tools/checks/density.py", "--output", tmp_path / "density.json")
    result = json.loads((tmp_path / "density.json").read_text())
    assert len(result["algorithm_pairs"]) == 9
    assert result["aggregate_algorithms"]["cpp_over_cairn"] > 1
    assert "ByT5" in parsed(printed)["tokenizer"]


@needs_clang
@needs_gcc
@needs_z3
def test_validate_semantics():
    out = parsed(tool("tools/checks/validate_semantics.py", "--gcc", timeout=900))
    assert out["status"] == "passed" and out["functions"] > 20
    assert [row["cases"] for row in out["native"]] and len(out["native"]) == 2
    assert all(row["trap_cases"] > 0 for row in out["native"]) and out["smt_pinned_cases"] > 0


@needs_clang
@needs_gcc
def test_validate_systems():
    out = parsed(tool("tools/checks/validate_systems.py", timeout=900))
    assert out["status"].startswith("passed-finite-tests")
    assert set(out["compilers"]) == {"clang++", "g++"} and out["new_expression_identity_edits"] > 0
    assert out["expected_aborts"]["clang++"] == [-6, -6, -6, -6]


def test_measure_context(tmp_path):
    printed = parsed(tool("tools/ai/measure_context.py", "--output", tmp_path / "context.json"))
    result = json.loads((tmp_path / "context.json").read_text())
    prior = set(json.loads((ROOT / "bench/cpu/fixtures/cards_05.json").read_text())["cards"])
    assert result["aggregate"]["packet_count"] > 0 and result["aggregate"]["before"] > result["aggregate"]["after"]
    assert all(set(row["cards"]) <= prior for row in result["rows"]), "a legacy comparison uses 0.5 cards only"
    assert result["current_only"] and all(row["legacy_comparison"] is None for row in result["current_only"])
    assert any(set(row["cards"]) - prior for row in result["current_only"])
    assert printed["aggregate"]["current_only_packet_count"] == len(result["current_only"])


@needs_clang
def test_mutation_checks():
    printed = tool("tools/checks/mutation_checks.py", timeout=600)
    result = json.loads((RESULTS / "mutation_checks.json").read_text())
    assert result["cases"] == result["detected"] == 8 and not result["model_generated"]
    assert f"Detected {result['detected']}" in printed


@needs_clang
@needs_z3
def test_sketch_demo(tmp_path):
    out = parsed(tool("tools/ai/sketch_demo.py", "--out", tmp_path, timeout=600))
    assert out["status"] == "passed" and not out["model_used"]
    assert all(run["semantic_checker_calls"] > 0 for run in out["searches"])
    assert json.loads((tmp_path / "semantic_receipt.json").read_text())["status"] == "smt-equivalent"


@needs_clang
def test_demo(tmp_path):
    out = parsed(tool("tools/ai/demo.py", "--out", tmp_path, timeout=600))
    assert out["status"] == "passed-reserved-finite-tests" and out["attempts"] == 3
    assert out["adapter_kind"] == "scripted-fixture" and not out["security_sandbox"]
    assert "compact" in (tmp_path / "selection_after.cairn").read_text()


@needs_clang
def test_agent_loop(tmp_path):
    out = parsed(
        tool(
            "tools/ai/agent_loop.py",
            "examples/agent/selection_before.cairn",
            "--contract",
            "examples/agent/task.json",
            "--out-dir",
            tmp_path / "loop",
            "--adapter-kind",
            "scripted-fixture",
            "--adapter",
            PY,
            ROOT / "examples/agent/scripted_adapter.py",  # The adapter runs in its own scratch directory.
            timeout=600,
        )
    )
    assert out["status"] == "passed-reserved-finite-tests" and out["reserved_cases"] > 0
    assert (tmp_path / "loop/candidate.cairn").exists()
    assert json.loads((tmp_path / "loop/transcript.json").read_text())["trace"], "the transcript keeps every attempt"


def test_agent():
    typed = parsed(tool("tools/ai/agent.py", "check", "examples/agent/selection_before.cairn"))
    assert typed["status"] == "typed" and typed["receipt"]["function_count"] > 0
    packet = parsed(
        tool("tools/ai/agent.py", "packet", "examples/agent/selection_before.cairn", "--symbol", "select_gt")
    )
    assert packet["symbol"] == "select_gt" and "base" in packet["rule_cards"]
    sites = json.loads(
        tool("tools/ai/agent.py", "sites", "examples/agent/selection_before.cairn", "--symbol", "select_gt")
    )
    assert sites and all(site["symbol"] == "select_gt" for site in sites)


def test_agent_metrics():
    printed = tool("tools/ai/agent_metrics.py", timeout=600)
    result = json.loads((RESULTS / "agent_metrics.json").read_text())
    assert result["identity_expression_edits"] > 0 and result["all_identity_edits_preserve_generated_cpp"]
    assert str(result["identity_expression_edits"]) in printed and result["model_runs"] == 0


def test_check_compact_forms():
    result = parsed(tool("tools/checks/check_compact_forms.py"))
    assert len(result["pairs"]) == 3
    assert all(
        p["generated_cpp_identical"] and p["compact_utf8_bytes"] < p["explicit_utf8_bytes"] for p in result["pairs"]
    )


def test_curriculum(tmp_path):
    result = parsed(tool("tools/ai/curriculum.py", "--out", tmp_path, timeout=600))
    assert result["tasks"] == result["train"] + result["heldout"] and result["contrastive_pairs"] > 0
    assert not result["model_training_performed"]
    assert len(json.loads((tmp_path / "all_tasks_with_oracles.json").read_text())) == result["tasks"]
    # Every contrastive pair is a rejection the compiler still performs, with the code it still emits.
    pairs = [json.loads(line) for line in (tmp_path / "contrastive.jsonl").read_text().splitlines()]
    assert len(pairs) == result["contrastive_pairs"] and all(p["diagnostic"]["code"] for p in pairs)


@needs_clang
@needs_gcc
def test_curriculum_verify():
    printed = tool("tools/checks/curriculum_verify.py", "--gcc", timeout=900)
    result = json.loads((RESULTS / "curriculum_validation.json").read_text())
    assert result["status"] == "all finite teaching cases passed" and result["model_runs"] == 0
    assert len(result["results"]) == 2 and all(r["cases"] > 0 for r in result["results"])
    assert all(e["status"] == "passed-finite-tests" for r in result["results"] for e in r["entries"])
    assert str(result["results"][0]["tasks"]) in printed


@needs_clang
@needs_gcc
@needs_z3
def test_semantic_corpus(tmp_path):
    result = parsed(tool("tools/checks/semantic_corpus.py", "--root", tmp_path, timeout=900))
    assert result["status"] == "passed" and result["tasks"] > 0
    assert result["positive_smt_labels"] == result["negative_smt_counterexamples"] == result["tasks"]
    assert len(result["native"]) == 2 and not result["fine_tuning_performed"]
    assert json.loads((tmp_path / "audit.json").read_text())


@needs_z3
def test_protocol_curriculum(tmp_path):
    shutil.copy(ROOT / "training/semantic/audit.json", tmp_path / "audit.json")
    result = parsed(tool("tools/ai/protocol_curriculum.py", "--root", tmp_path, timeout=900))
    assert result["status"] == "passed" and result["executed_protocol_lessons"] > 0
    assert result["fresh_semantic_checks"] == 2 * result["executed_protocol_lessons"]
    assert result["wrong_turns_never_sft_targets"] and not result["model_or_training_run"]


@needs_z3
def test_semantic_check():
    base = ROOT / "examples/proof_scope"
    equivalent = parsed(tool("tools/checks/semantic_check.py", base / "reference.cairn", base / "candidate.cairn", "--symbol", "average"))  # fmt: skip
    assert equivalent["status"] == "smt-equivalent" and not equivalent["lean_verified"]
    # Exit 2 is the honest answer for a symbol outside the scalar model, not a pass.
    unsupported = parsed(tool("tools/checks/semantic_check.py", base / "mixed.cairn", base / "mixed.cairn", "--symbol", "scaled", expect=2))  # fmt: skip
    assert unsupported["status"] in {"unknown", "unsupported"}


@needs_clang
def test_task_eval():
    result = parsed(
        tool("tools/ai/task_eval.py", "examples/agent/selection_after.cairn", "--contract", "examples/agent/task.json")
    )
    assert result["status"] == "passed-finite-tests" and result["cases"] > 0


def test_audit_repository():
    if not (ROOT / ".git").exists():
        pytest.skip("the history audit needs a git checkout, not an exported tree")
    done = subprocess.run(
        [PY, "tools/release/audit_repository.py"], cwd=ROOT, text=True, capture_output=True, timeout=300
    )
    result = parsed(done.stdout)
    assert done.returncode == (1 if result["findings"] else 0)
    assert result["commits"] > 0 and result["distinct_blobs"] > 0 and result["bytes_scanned"] > 0


def test_publish_private(tmp_path):
    """The preflight, with fakes: no network, no gh, no repository is created."""
    from release.publish_private import publish

    calls = []
    head = "0" * 40
    answers = {("rev-parse", "--show-toplevel"): str(ROOT), ("branch", "--show-current"): "main", ("rev-parse", "HEAD"): head}  # fmt: skip

    def fake(argv, *, cwd):
        calls.append(argv)
        return answers.get(tuple(argv[1:]), "")

    result = publish(ROOT, "Owner/repo", run=fake, audit_fn=lambda root: {"findings": []})
    assert result["head"] == head
    assert result["status"] == "local-preflight-passed" and result["remote_created"] is False
    assert all(argv[0] == "git" for argv in calls), "a preflight never reaches gh or the network"
    stopped = parsed(tool("tools/release/publish_private.py", "not a repository", expect=1))
    assert stopped["status"] == "stopped" and "force push" in stopped["policy"]


def test_export_lean_certificates():
    printed = tool("tools/checks/export_lean_certificates.py", "--check", timeout=600)
    assert "up to date" in printed or "drift" in printed


def test_parallel_gpu_flags():
    """The device benchmark is not run here: it writes release evidence. Its flags are still checked."""
    if not shutil.which("nvcc"):
        pytest.skip("nvcc is not installed")
    sys.path.insert(0, str(ROOT / "bench/gpu"))
    import parallel_gpu

    from support import best_profile, profile_flags

    expected = [f for f in profile_flags("exe", best_profile("g++")) if not f.startswith(("-std", "-O"))]
    # The one authorized departure: CCCL 3 needs the host pass to parse exceptions (cairn_gpu.hpp's note).
    expected = [("-fexceptions" if f == "-fno-exceptions" else f) for f in expected]
    assert expected == parallel_gpu.HOST, "the host half of the device build is the shared contract"
    assert "-fno-exceptions" not in parallel_gpu.HOST, "the swap starts from the shared contract"
    assert "-Werror" in parallel_gpu.HOST and "--fmad=false" in parallel_gpu.DEVICE


@needs_clang
@needs_gcc
def test_host_region_benchmark_builds_under_the_contract(tmp_path):
    """The host region benchmark is not timed here: it writes release evidence. It must still build
    warning free under the project's own flags, with both compilers, against the real headers."""
    sys.path.insert(0, str(ROOT / "bench/host_regions"))
    import host_regions
    from support import best_profile, profile_flags

    assert host_regions.COMPILERS == ("g++", "clang++")
    arch = best_profile(*host_regions.COMPILERS)
    for cxx in host_regions.COMPILERS:
        line = [cxx, *profile_flags("exe", arch), f"-I{ROOT / 'src/cairn/runtime'}"]
        line += [str(ROOT / "bench/host_regions/host_regions.cpp"), "-o", str(tmp_path / f"probe_{cxx[0]}")]
        made = subprocess.run(line, capture_output=True, text=True, timeout=600)
        assert made.returncode == 0, made.stderr[-3000:]
        assert made.stderr == "", f"the benchmark must build without a warning:\n{made.stderr}"

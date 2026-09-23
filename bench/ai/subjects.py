"""Subjects: a fresh headless Claude Code session per task and language, in a sandbox outside the repository.

A sandbox holds TASK.md, the starter in its project layout and, for CAIRN, the language documentation. The session
starts with no memory, no CLAUDE.md or AGENTS.md, no skills, no MCP servers and no web tools (`--restricted`,
`--strict-mcp-config`, `--disable-slash-commands`, a fixed tool list), and its whole transcript is kept as JSON Lines.
CAIRN is installed for the subjects from a wheel of this checkout into a virtual environment of its own, so the
command they run is the installed compiler, and the repository is never on their path.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from checking import CAIRN_TOML, CARGO_TOML, SOURCE
from tasks import Task

REPO = Path(__file__).resolve().parents[2]
PROMPT = "Read TASK.md in this directory and do what it says."
TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
# The reference a newcomer to CAIRN reads: the guide, the language reference, the library and the command line.
CAIRN_DOCS = ["guide.md", "language.md", "memory.md", "abstractions.md", "concurrency.md", "numerics.md", "library.md",
              "tools.md", "std_api.md"]  # fmt: skip
LIMITS = {"model": "claude-sonnet-5", "effort": "high", "max_turns": 80, "max_budget_usd": 5.0, "wall_seconds": 2400}

JUDGED = {
    "cpp": (
        "Write C++20 in `main.cpp`, using only the standard library. `clang++` and `g++` are installed.\n\n"
        "The judged build is `clang++ -std=c++20 -O1 -g -fno-omit-frame-pointer -fno-sanitize-recover=all -pthread "
        "-fsanitize=address,undefined main.cpp`, run with leak detection on{thread}. A sanitizer report fails the run."
    ),
    "rust": (
        "Write Rust (edition 2021) in `src/main.rs`, using only the standard library: no crates and no `unsafe`. "
        "`cargo` works offline here, and `Cargo.toml` is given.\n\n"
        "The judged build is `cargo build` (the debug profile, with overflow checks on). A panic fails the run."
    ),
    "cairn": (
        "Write CAIRN in `src/main.cairn`, using only its standard library and no `unsafe`. CAIRN is a new systems "
        "language that compiles to C++, and you have probably never seen it. Its documentation is in `docs/`: start "
        "with `docs/guide.md`; `docs/language.md`, `docs/memory.md` and `docs/concurrency.md` are the reference, "
        "`docs/library.md` and `docs/std/` the standard library, and `docs/tools.md` the command line. The `cairn` "
        "command is installed: `cairn check .` checks the program, `cairn run . < input.txt` builds and runs it, and "
        "`cairn build .` builds it and says where the executable is.\n\n"
        "The judged build is `cairn build .`, whose generated C++ is then compiled again with `clang++ -O1 -g "
        "-fno-omit-frame-pointer -fno-sanitize-recover=all -pthread -fsanitize=address,undefined` and run with leak "
        "detection on{thread}. A sanitizer report or a failed CAIRN guard (a trap) fails the run."
    ),
}
THREAD_NOTE = ", and once more built with `-fsanitize=thread` in place of `-fsanitize=address,undefined`"


def task_md(task: Task, language: str) -> str:
    """TASK.md: the task's SPEC.md, then the environment and how the answer is judged, the same words for every
    language but the paragraph about its build."""
    spec = task.spec.split("\n", 1)[1].lstrip("\n")
    judged = JUDGED[language].format(thread=THREAD_NOTE if task.threads else "")
    return (
        f"# Task: {task.name}\n\n{spec}\n"
        "## Your environment\n\n"
        "Work in this directory. Do not read, list or search any file outside it, except the compilers and tools you "
        "run, and do not use the network. Your session has a fixed budget of turns, time and tokens, the same for "
        "every language, so work steadily.\n\n"
        f"{judged}\n\n"
        "## How your program is judged\n\n"
        f"When you stop, only `{SOURCE[language]}` is kept. It is built as above and run on hidden inputs that follow "
        "the rules of this task, including large inputs and edge cases, not only the example. It passes when, on "
        "every hidden input, it exits with status 0 and prints exactly the expected output. Test it yourself before "
        "you stop.\n"
    )


def toolchain(root: Path) -> Path:
    """A bin directory holding `cairn`, installed from a wheel of this checkout into a virtual environment of its
    own, beside a snapshot of the documentation CAIRN subjects are given; built once per root, so every subject of a
    run gets the same compiler and the same documentation even if the checkout moves on."""
    bin_dir = root / "bin"
    if (bin_dir / "cairn").exists():
        return bin_dir
    docs = root / "docs"
    shutil.rmtree(docs, ignore_errors=True)
    docs.mkdir(parents=True)
    for name in CAIRN_DOCS:
        shutil.copy(REPO / "docs" / name, docs / name)
    shutil.copytree(REPO / "docs" / "std", docs / "std")
    vcs = ["git", "-C", str(REPO)]
    head = subprocess.run([*vcs, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run([*vcs, "status", "--porcelain", "--", "src", "docs"], capture_output=True, text=True).stdout
    (root / "COMMIT").write_text(head + (" with uncommitted changes to src or docs" if dirty.strip() else "") + "\n")
    wheel_dir, venv = root / "wheel", root / "venv"
    shutil.rmtree(wheel_dir, ignore_errors=True)
    pip = [sys.executable, "-m", "pip"]
    subprocess.run([*pip, "wheel", "--no-index", "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheel_dir),
                    str(REPO)], check=True, capture_output=True)  # fmt: skip
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    wheel = next(wheel_dir.glob("*.whl"))
    subprocess.run([str(venv / "bin" / "python"), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
                   check=True, capture_output=True)  # fmt: skip
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "cairn").symlink_to(venv / "bin" / "cairn")
    return bin_dir


def environment(tools: Path) -> dict:
    """The subject's environment: the user's, with the installed `cairn` first on PATH and nothing of the checkout."""
    keep = [p for p in os.environ.get("PATH", "").split(":") if p and "cairn" not in p]
    for extra in ["/opt/llvm-21.1.8/bin", str(Path.home() / ".cargo" / "bin")]:
        if extra not in keep:
            keep.append(extra)
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("CAIRN", "CLAUDE", "VIRTUAL_ENV", "PYTHON", "VSCODE"))
    }
    return {**env, "PATH": ":".join([str(tools), *keep])}


def sandbox(task: Task, language: str, where: Path, docs: Path = REPO / "docs") -> Path:
    """A fresh directory holding TASK.md, the starter in its project layout and, for CAIRN, the documentation."""
    shutil.rmtree(where, ignore_errors=True)
    source = where / SOURCE[language]
    source.parent.mkdir(parents=True)
    ext = {"cpp": "cpp", "rust": "rs", "cairn": "cairn"}[language]
    shutil.copy(REPO / "bench" / "ai" / "tasks" / task.name / f"starter.{ext}", source)
    if language == "rust":
        (where / "Cargo.toml").write_text(CARGO_TOML)
    if language == "cairn":
        (where / "cairn.toml").write_text(CAIRN_TOML)
        (where / "docs").mkdir()
        for name in CAIRN_DOCS:
            shutil.copy(docs / name, where / "docs" / name)
        shutil.copytree(docs / "std", where / "docs" / "std")
    (where / "TASK.md").write_text(task_md(task, language))
    return where


def digest(where: Path) -> dict[str, str]:
    """sha256 of every file a subject is given, so a result names exactly what its subject read."""
    return {
        str(p.relative_to(where)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(where.rglob("*"))
        if p.is_file()
    }


def command(limits: dict) -> list[str]:
    return [
        "claude", "-p", PROMPT, "--model", limits["model"], "--effort", limits["effort"],
        "--output-format", "stream-json", "--verbose", "--restricted", "--tools", TOOLS,
        "--allowedTools", TOOLS.replace(",", " "), "--permission-mode", "dontAsk", "--strict-mcp-config",
        "--disable-slash-commands", "--no-session-persistence", "--max-turns", str(limits["max_turns"]),
        "--max-budget-usd", str(limits["max_budget_usd"]),
    ]  # fmt: skip


def run_subject(task: Task, language: str, where: Path, record_dir: Path, tools: Path, limits: dict) -> dict:
    """Run one subject to the end in `where`; its transcript and final program go to `record_dir`."""
    given = digest(sandbox(task, language, where, tools.parent / "docs"))
    record_dir.mkdir(parents=True, exist_ok=True)
    transcript = record_dir / "transcript.jsonl"
    started = time.time()
    with transcript.open("w") as out:
        proc = subprocess.Popen(command(limits), cwd=where, env=environment(tools), stdout=out, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True)  # fmt: skip
        try:
            _, err = proc.communicate(timeout=limits["wall_seconds"])
            killed = False
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
            killed = True
    wall = time.time() - started
    final = (where / SOURCE[language]).read_text(errors="replace") if (where / SOURCE[language]).exists() else ""
    (record_dir / Path(SOURCE[language]).name).write_text(final)
    result = last_result(transcript)
    return {
        "task": task.name,
        "language": language,
        "limits": limits,
        "given": given,
        "wall_seconds": round(wall, 1),
        "killed_at_wall_limit": killed,
        "exit_code": proc.returncode,
        "stderr": (err or "")[-2000:],
        "result": result,
        "sandbox": str(where),
        "toolchain_commit": (tools.parent / "COMMIT").read_text().strip()
        if (tools.parent / "COMMIT").exists()
        else None,
        "transcript": str(transcript),
    }


def last_result(transcript: Path) -> dict | None:
    """The platform's own closing record of the session: usage, cost, turns and why it stopped."""
    found = None
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("type") == "result":
            found = message
    if found is None:
        return None
    keep = ("subtype", "is_error", "num_turns", "duration_ms", "duration_api_ms", "total_cost_usd", "usage",
            "modelUsage", "stop_reason", "terminal_reason", "permission_denials", "api_error_status")  # fmt: skip
    return {k: found.get(k) for k in keep}

"""Subjects: a fresh headless Claude Code session per task and arm, in a sandbox outside the repository.

An arm is a language and what the subject is given for it: `cpp` and `rust`; `cairn`, the documentation in the
sandbox; and `plugin`, CAIRN's Claude Code plugin (its skill, the `cairn` command, its language server and `cairn mcp`)
loaded from a read-only copy beside the sandbox. A sandbox holds TASK.md and the starter in its project layout. The
session starts with no memory, no CLAUDE.md or AGENTS.md, no web tools and a fixed tool list (`--restricted`); every
arm but `plugin` also has no skills and no MCP servers (`--strict-mcp-config`, `--disable-slash-commands`), and the
`plugin` arm has only the plugin's. The whole transcript is kept as JSON Lines. CAIRN is installed for the subjects
from a wheel of this checkout into a virtual environment of its own, so the command they run is the installed
compiler, and the repository is never on their path.
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

from checking import CAIRN_TOML, CARGO_TOML, EMULATE, SOURCE
from tasks import Task

REPO = Path(__file__).resolve().parents[2]
PROMPT = "Read TASK.md in this directory and do what it says."
TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
# The reference a newcomer to CAIRN reads: the guide, the language reference, the library and the command line.
CAIRN_DOCS = ["guide.md", "language.md", "memory.md", "abstractions.md", "concurrency.md", "devices.md",
              "numerics.md", "library.md", "tools.md", "std_api.md"]  # fmt: skip
LIMITS = {"model": "claude-sonnet-5", "effort": "high", "max_turns": 80, "max_budget_usd": 5.0, "wall_seconds": 2400}
# The language each arm is written and judged in; `plugin` differs from `cairn` only in what the subject is given.
LANGUAGE = {"plugin": "cairn", "cairn": "cairn", "cpp": "cpp", "rust": "rust"}
# What the plugin arm's copy of the plugin holds: the manifest, the skill, the command and the documentation the
# skill points to (`${CLAUDE_SKILL_DIR}/../../docs/`), as an install from this checkout has them, and nothing else.
PLUGIN_PARTS = [".claude-plugin", "skills", "docs"]
# No claude.ai connector and none of Claude Code's own bundled skills reach any arm.
QUIET = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1"}

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
        "command is installed: `cairn check .` checks the program, `cairn run .{emulate} < input.txt` builds and runs "
        "it, and `cairn build .{emulate}` builds it and says where the executable is.\n\n"
        "The judged build is `cairn build .{emulate}`, whose generated C++ is then compiled again with `clang++ -O1 -g "
        "-fno-omit-frame-pointer -fno-sanitize-recover=all -pthread -fsanitize=address,undefined` and run with leak "
        "detection on{thread}. A sanitizer report or a failed CAIRN guard (a trap) fails the run."
    ),
}
# The plugin arm reads the same paragraph but for the documentation, which comes with the plugin instead.
JUDGED["plugin"] = JUDGED["cairn"].replace(
    "Its documentation is in `docs/`: start with `docs/guide.md`; `docs/language.md`, `docs/memory.md` and "
    "`docs/concurrency.md` are the reference, `docs/library.md` and `docs/std/` the standard library, and "
    "`docs/tools.md` the command line. The `cairn` command is installed:",
    "The CAIRN plugin for Claude Code is installed in this session: its `cairn` skill, its language server and its "
    "`cairn` MCP server. The `cairn` command is installed:",
)
# What a task asks of each language beyond its SPEC.md: the constructs a GPU-style task must be written with.
NOTES = {
    "block_scan": {
        "cpp": "For this task the team is `std::thread`s that meet at a `std::barrier` (or `pthread_barrier_wait`), "
        "and the judge checks that the program uses both.",
        "rust": "For this task the team is threads from `std::thread` that meet at a `std::sync::Barrier`, and the "
        "judge checks that the program uses both.",
        "cairn": "For this task the team is a cooperative region, `blocks ... threads ...`, whose threads meet at "
        "`barrier`, over `@device` arrays: the kernel is written for a GPU. There is no GPU here, so the judged build "
        "emulates the device on host threads, and the judge checks that the program has `blocks ... threads`, "
        "`barrier` and `@device`.",
    }
}
PLUGIN_FILES = " and the files of the CAIRN plugin"
THREAD_NOTE = ", and once more built with `-fsanitize=thread` in place of `-fsanitize=address,undefined`"


def task_md(task: Task, arm: str) -> str:
    """TASK.md: the task's SPEC.md, then the environment and how the answer is judged, the same words for every
    arm but the paragraph about its build."""
    language = LANGUAGE[arm]
    spec = task.spec.split("\n", 1)[1].lstrip("\n")
    emulate = " " + " ".join(EMULATE) if task.emulate else ""
    judged = JUDGED[arm].format(thread=THREAD_NOTE if task.threads else "", emulate=emulate)
    if note := NOTES.get(task.name, {}).get(language):
        judged += " " + note
    return (
        f"# Task: {task.name}\n\n{spec}\n"
        "## Your environment\n\n"
        "Work in this directory. Do not read, list or search any file outside it, except the compilers and tools you "
        f"run{PLUGIN_FILES if arm == 'plugin' else ''}, and do not use the network. Your session has a fixed budget "
        "of turns, time and tokens, the same for every language, so work steadily.\n\n"
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
    plugin = root / "plugin"
    shutil.rmtree(plugin, ignore_errors=True)
    for part in PLUGIN_PARTS:
        shutil.copytree(REPO / part, plugin / part)
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
    (plugin / "bin").mkdir()
    (plugin / "bin" / "cairn").symlink_to(venv / "bin" / "cairn")
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
    return {**env, **QUIET, "PATH": ":".join([str(tools), *keep])}


def sandbox(task: Task, arm: str, where: Path, docs: Path = REPO / "docs") -> Path:
    """A fresh directory holding TASK.md, the starter in its project layout and, for the `cairn` arm, the
    documentation."""
    shutil.rmtree(where, ignore_errors=True)
    language = LANGUAGE[arm]
    source = where / SOURCE[language]
    source.parent.mkdir(parents=True)
    ext = {"cpp": "cpp", "rust": "rs", "cairn": "cairn"}[language]
    shutil.copy(REPO / "bench" / "ai" / "tasks" / task.name / f"starter.{ext}", source)
    if language == "rust":
        (where / "Cargo.toml").write_text(CARGO_TOML)
    if language == "cairn":
        (where / "cairn.toml").write_text(CAIRN_TOML)
    if arm == "cairn":
        (where / "docs").mkdir()
        for name in CAIRN_DOCS:
            shutil.copy(docs / name, where / "docs" / name)
        shutil.copytree(docs / "std", where / "docs" / "std")
    (where / "TASK.md").write_text(task_md(task, arm))
    return where


def plugin_copy(source: Path, where: Path) -> Path:
    """The plugin arm's own read-only copy of the plugin, beside its sandbox, so no subject can change what another
    reads."""
    unlock(where)
    shutil.copytree(source, where, symlinks=True)
    for path in [where, *where.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o222)
    return where


def unlock(where: Path) -> None:
    """Remove a read-only copy made by `plugin_copy`."""
    if where.exists():
        for path in [where, *where.rglob("*")]:
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o200)
        shutil.rmtree(where)


def digest(where: Path) -> dict[str, str]:
    """sha256 of every file a subject is given, so a result names exactly what its subject read."""
    return {
        str(p.relative_to(where)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(where.rglob("*"))
        if p.is_file()
    }


def command(limits: dict, plugin: Path | None = None) -> list[str]:
    """The session's command line. With `plugin`, the plugin arm's: that plugin loaded and readable, its skill and
    MCP tools allowed, and no other skill or MCP server, which the environment's QUIET settings keep out."""
    if plugin is None:
        tools, allowed, isolation = TOOLS, TOOLS.replace(",", " "), ["--strict-mcp-config", "--disable-slash-commands"]
    else:
        tools = TOOLS + ",Skill"
        allowed = tools.replace(",", " ") + " mcp__plugin_cairn_cairn"
        isolation = ["--plugin-dir", str(plugin), "--add-dir", str(plugin)]
    return [
        "claude", "-p", PROMPT, "--model", limits["model"], "--effort", limits["effort"],
        "--output-format", "stream-json", "--verbose", "--restricted", "--tools", tools,
        "--allowedTools", allowed, "--permission-mode", "dontAsk", *isolation,
        "--no-session-persistence", "--max-turns", str(limits["max_turns"]),
        "--max-budget-usd", str(limits["max_budget_usd"]),
    ]  # fmt: skip


def isolated(argv: list[str], scratch: Path, keep: list[Path], hide: list[tuple[str, str]]) -> list[str]:
    """`argv` run where it sees `scratch` as its /tmp, an empty directory at each path of `hide` (with the mode given),
    and the directories of `keep` where they are: isolation.py, in a user and mount namespace of its own, as the
    caller's user and group."""
    spec = {"tmp": str(scratch), "keep": [str(k) for k in keep], "hide": hide, "uid": os.getuid(), "gid": os.getgid()}
    script = Path(__file__).resolve().parent / "isolation.py"
    return ["unshare", "-Urm", "/usr/bin/python3", str(script), json.dumps(spec), "--", *argv]


def hidden(root: Path) -> list[tuple[str, str]]:
    """What no subject sees: every checkout of the repository, every subject's sandbox, plugin copy and /tmp under the
    run's root (its own are mounted back), and the saved sessions and tool output of every Claude Code session."""
    here = Path(__file__).resolve().parents[2]
    listed = subprocess.run(["git", "-C", str(here), "worktree", "list", "--porcelain"], capture_output=True, text=True)
    checkouts = [line.split(" ", 1)[1] for line in listed.stdout.splitlines() if line.startswith("worktree ")]
    runs = [str(root / part) for part in ("runs", "plugins", "tmp")]
    return [*((c, "0555") for c in checkouts or [str(here)]), *((r, "0755") for r in runs),
            (str(Path.home() / ".claude" / "projects"), "1777")]  # fmt: skip


def run_subject(
    task: Task,
    arm: str,
    where: Path,
    record_dir: Path,
    tools: Path,
    limits: dict,
    plugin: Path | None = None,
    scratch: Path | None = None,
    hide: list[tuple[str, str]] | None = None,
) -> dict:
    """Run one subject to the end in `where`; its transcript and final program go to `record_dir`. The plugin arm
    gets its own copy of the plugin at `plugin`. With `scratch` the session sees that directory as its /tmp, and
    nothing at the paths of `hide` but its own sandbox and plugin."""
    language = LANGUAGE[arm]
    given = digest(sandbox(task, arm, where, tools.parent / "docs"))
    if arm == "plugin":
        assert plugin is not None, "the plugin arm needs a place for its copy of the plugin"
        given.update({f"plugin/{k}": v for k, v in digest(plugin_copy(tools.parent / "plugin", plugin)).items()})
    else:
        plugin = None
    record_dir.mkdir(parents=True, exist_ok=True)
    transcript = record_dir / "transcript.jsonl"
    argv = command(limits, plugin)
    if scratch is not None:
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        argv = isolated(argv, scratch, [where, *([plugin] if plugin else [])], hide or [])
    started, load = time.time(), [round(x, 1) for x in os.getloadavg()]
    with transcript.open("w") as out:
        proc = subprocess.Popen(argv, cwd=where, env=environment(tools), stdout=out, stderr=subprocess.PIPE,
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
        "arm": arm,
        "language": language,
        "plugin": str(plugin) if plugin else None,
        "scratch": str(scratch) if scratch else None,
        "hidden": [path for path, _ in hide or []],
        "limits": limits,
        "given": given,
        "started_at": round(started, 1),
        "load_average": {"start": load, "end": [round(x, 1) for x in os.getloadavg()]},  # 1, 5 and 15 minutes
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

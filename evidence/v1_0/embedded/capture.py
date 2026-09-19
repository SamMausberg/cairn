"""Regenerate evidence/v1_0/embedded/ from a clean build. Run from the repository root.

    .venv/bin/python evidence/v1_0/embedded/capture.py

Nothing here is a test: the assertions live in tests/test_freestanding.py. This script only builds
the two images the way `cairn build` does, runs them under QEMU, and writes down exactly what came
back, together with the versions of every tool that touched the result.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from cairn.build import build  # noqa: E402
from cairn.project import load_project  # noqa: E402
from cairn.toolchain import emulator  # noqa: E402

from cairn.version import VERSION  # noqa: E402


def tool(*command: str) -> str:
    finished = subprocess.run(command, capture_output=True, text=True, timeout=60)
    return (finished.stdout + finished.stderr).strip()


def capture(path: Path, out: Path) -> dict:
    record = build(load_project(path), output=out, timeout=120)
    if record["status"] != "native-built":
        raise SystemExit(record.get("stderr") or record.get("message"))
    machine = emulator(record["target"], record["artifact"])
    started = time.monotonic()
    run = subprocess.run(machine, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    return {
        "project": str(path.relative_to(ROOT)),
        "build_command": record["command"],
        "qemu_command": machine,
        "artifact_sha256": record["artifact_sha256"],
        "elf": record["artifact"],
        "uart": run.stdout,
        "stderr": run.stderr,
        "exit_status": run.returncode,
        "seconds": round(time.monotonic() - started, 3),
        "size": tool("size", record["artifact"]),
        "undefined_symbols": tool("nm", "-u", record["artifact"]).splitlines(),
    }


def main() -> None:
    out = HERE / "build"
    runs = {
        "application": capture(ROOT / "examples/embedded", out),
        "trap": capture(ROOT / "examples/embedded/trap", out),
    }
    versions = {
        name: tool(*command)
        for name, command in {
            "cairn": (sys.executable, str(ROOT / "bin/cairn"), "--version"),
            "clang++": ("clang++", "--version"),
            "qemu-system-aarch64": ("qemu-system-aarch64", "--version"),
            "ld": ("ld", "--version"),
            "nm": ("nm", "--version"),
        }.items()
    }
    (HERE / "versions.txt").write_text("\n\n".join(f"$ {k} --version\n{v}" for k, v in versions.items()) + "\n")
    (HERE / "transcript.txt").write_text(
        "".join(
            f"$ {' '.join(run['qemu_command'])}\n{run['uart']}$ echo $?\n{run['exit_status']}\n\n"
            for run in runs.values()
        )
    )
    (HERE / "size-nm.txt").write_text(
        "".join(
            f"$ size {run['elf']}\n{run['size']}\n$ nm -u {run['elf']}\n"
            + ("".join(s + "\n" for s in run["undefined_symbols"]) or "(none)\n")
            + "\n"
            for run in runs.values()
        )
    )
    (HERE / "summary.json").write_text(
        json.dumps(
            {
                "schema": "cairn.evidence/1",
                "subject": "freestanding profile, target aarch64-virt",
                "compiler": VERSION,
                "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "host": dict(zip(("system", "release", "machine"), platform.uname()[::2], strict=False)),
                "machine": "QEMU virt, cortex-a72, 128 MiB, MMU off, semihosting exit",
                "runs": runs,
                "claims": [
                    "the image links no C library and no C++ runtime: nm -u is empty",
                    "fn main() -> i32 reaches the emulator's exit status unchanged",
                    "a failed bounds guard stops the machine with status 134, not 'unreachable'",
                    "effects alloc/free/io/gpu_*/transfer:*/par:*/ffi:* are refused by name",
                ],
                "not_proved": [
                    "the emitter, the C++ backend and the start-up code are tested, not verified",
                    "QEMU virt is not silicon: no cache, timing or peripheral behaviour is claimed",
                    "one board and one compiler; other AArch64 boards are untried",
                ],
                "versions": versions,
                "tests": ".venv/bin/python -m pytest -q tests/test_freestanding.py",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(json.dumps({name: {"exit_status": run["exit_status"], "size": run["size"]} for name, run in runs.items()}))


if __name__ == "__main__":
    main()

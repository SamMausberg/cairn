"""What every demo prints the same way: `cairn` run as a reader would type it, a path as they would type it, and the
CPU a timing ran on."""

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cairn(*args: str) -> subprocess.CompletedProcess:
    """The command line a reader would type, run from the repository root."""
    return subprocess.run([sys.executable, str(ROOT / "bin/cairn"), *args], cwd=ROOT, capture_output=True, text=True)


def rel(path: Path) -> str:
    """`path` as a reader types it: from the repository root when it lies inside it."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def cpu() -> str:
    """The CPU's model name as Linux reports it, or the machine's architecture where it reports none."""
    info = Path("/proc/cpuinfo")
    for line in info.read_text().splitlines() if info.exists() else []:
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.machine()

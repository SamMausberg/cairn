#!/usr/bin/env python3
"""Conservative tracked-history checks. Not a comprehensive secret detector."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
from pathlib import Path

RULES = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "aws-access-id": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "provider-key": re.compile(rb"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}"),
    "slack-token": re.compile(rb"xox[baprs]-[0-9A-Za-z-]{20,}"),
}
BAD_NAMES = {".env", ".netrc", ".npmrc", ".pypirc", "credentials.json", "id_rsa", "id_ed25519", "hosts.yml"}
BAD_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".so", ".o", ".a", ".dll", ".exe", ".zip", ".whl", ".bundle"}
PNG = b"\x89PNG\r\n\x1a\n"
LIMIT, RECORD_LIMIT = 2_000_000, 4_000_000  # bytes of one file; a text record under evidence/ may reach the second


def allowance(name: str) -> str:
    """What a path may hold beyond the rules for every file: a demo's frame, or a larger text record."""
    if name.startswith("demos/") and name.endswith(".png"):
        return "picture"
    return "record" if name.startswith("evidence/") else ""


def picture(name: str, data: bytes) -> bool:
    """A frame a demo's README shows: a PNG under demos/, which the demo's test draws again and compares."""
    return allowance(name) == "picture" and data.startswith(PNG) and len(data) < 200_000


def too_large(name: str, data: bytes) -> bool:
    """Past two megabytes, or past four for a text record under evidence/; a binary never gets the larger limit."""
    record = allowance(name) == "record" and b"\0" not in data
    return len(data) > (RECORD_LIMIT if record else LIMIT)


def audit(root: Path) -> dict:
    def git(*args: str) -> bytes:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=30).stdout

    commits = git("rev-list", "--all").decode().splitlines()
    if len(commits) > 1000:
        raise ValueError("History exceeds the bounded audit; perform an independent full audit.")
    # first: each distinct blob under each allowance, with the path and commit it was first seen at there. A blob is
    # judged once at a path of each kind, so a record under evidence/ does not admit the same bytes anywhere else.
    findings, first, count = [], {}, 0
    for commit in commits:
        for record in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not record:
                continue
            metadata, raw = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            name = raw.decode("utf-8")
            p = Path(name)
            if "\n" in name or p.name in BAD_NAMES or p.name.startswith(".env.") or p.suffix in BAD_SUFFIXES:
                findings.append({"path": name, "rule": "excluded-path", "commit": commit})
            if mode not in {"100644", "100755"} or kind != "blob":
                findings.append({"path": name, "rule": "symlink-or-submodule", "commit": commit})
                continue
            first.setdefault((oid, allowance(name)), (name, commit))
            if len(first) > 20000:
                raise ValueError("History exceeds bounded blob audit.")
    # One `git cat-file --batch` reads every blob, where a pair of processes per blob spent minutes starting up.
    request = "".join(f"{oid}\n" for oid, _ in first).encode()
    with subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    ) as batch:
        feeder = threading.Thread(target=lambda: (batch.stdin.write(request), batch.stdin.close()))
        feeder.start()
        for (oid, _), (name, commit) in first.items():
            header = batch.stdout.readline().split()
            if header[:2] != [oid.encode(), b"blob"]:
                raise ValueError(f"git cat-file answered {header!r} for blob {oid}.")
            size = int(header[2])
            data = batch.stdout.read(size + 1)[:size]
            if too_large(name, data):
                findings.append({"path": name, "rule": "large-file", "commit": commit})
                continue
            count += len(data)
            if b"\0" in data and not picture(name, data):
                findings.append({"path": name, "rule": "binary-file", "commit": commit})
            for rule, pattern in RULES.items():
                if pattern.search(data):
                    # Never print a credential or a matching source snippet.
                    findings.append({"path": name, "rule": rule, "commit": commit})
        feeder.join()
    if batch.returncode != 0:
        raise ValueError("git cat-file --batch failed.")
    return {
        "status": "no-pattern-findings" if not findings else "blocked",
        "commits": len(commits),
        "distinct_blobs": len({oid for oid, _ in first}),
        "bytes_scanned": count,
        "findings": findings,
        "scope": "all reachable commits; finite credential patterns, not a guarantee",
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[2])
    a = p.parse_args()
    result = audit(a.root)
    print(json.dumps(result, indent=2))
    return 0 if not result["findings"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

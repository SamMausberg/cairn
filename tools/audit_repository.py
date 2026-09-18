#!/usr/bin/env python3
"""Conservative tracked-history checks. Not a comprehensive secret detector."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
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


def audit(root: Path) -> dict:
    def git(*args: str) -> bytes:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=30).stdout

    commits = git("rev-list", "--all").decode().splitlines()
    if len(commits) > 1000:
        raise ValueError("History exceeds the bounded audit; perform an independent full audit.")
    findings, seen, count = [], set(), 0
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
            if oid in seen:
                continue
            seen.add(oid)
            if len(seen) > 20000:
                raise ValueError("History exceeds bounded blob audit.")
            size = int(git("cat-file", "-s", oid))
            if size > 2_000_000:
                findings.append({"path": name, "rule": "large-file", "commit": commit})
                continue
            data = git("cat-file", "blob", oid)
            count += len(data)
            if b"\0" in data:
                findings.append({"path": name, "rule": "binary-file", "commit": commit})
            for rule, pattern in RULES.items():
                if pattern.search(data):
                    # Never print a credential or a matching source snippet.
                    findings.append({"path": name, "rule": rule, "commit": commit})
    return {
        "status": "no-pattern-findings" if not findings else "blocked",
        "commits": len(commits),
        "distinct_blobs": len(seen),
        "bytes_scanned": count,
        "findings": findings,
        "scope": "all reachable commits; finite credential patterns, not a guarantee",
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    a = p.parse_args()
    result = audit(a.root)
    print(json.dumps(result, indent=2))
    return 0 if not result["findings"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

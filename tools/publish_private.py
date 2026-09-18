#!/usr/bin/env python3
"""Create a NEW private personal GitHub repository, verify it, then push main.

Default is a local-only preflight. Requires --execute for remote actions and an
already authenticated GitHub CLI. No token is requested, read, printed or stored.
No existing remote repository is reused. No force push, deletion or visibility
change is available. Privacy checks cannot prevent a concurrent administrator
from changing visibility after verification.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from audit_repository import audit


class PublishError(RuntimeError):
    pass


def command(argv: list[str], *, cwd: Path) -> str:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120)
    if result.returncode:
        # Do not echo arbitrary native/credential-helper output into a receipt.
        raise PublishError(
            f"{argv[0]} command failed with exit {result.returncode}. No retry or force push attempted."
        )
    return result.stdout.strip()


def validate_private(record: dict, owner: str, name: str) -> None:
    if (
        not isinstance(record, dict)
        or record.get("private") is not True
        or record.get("visibility") not in {None, "private"}
        or record.get("full_name", "").lower() != f"{owner}/{name}".lower()
        or record.get("owner", {}).get("login", "").lower() != owner.lower()
        or record.get("owner", {}).get("type") != "User"
        or record.get("fork") is not False
    ):
        raise PublishError(
            "Repository identity/privacy verification failed. No further upload is allowed."
        )


def publish(
    root: Path, repository: str, *, execute: bool = False, run=command, audit_fn=audit
) -> dict:
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", repository
    ):
        raise PublishError("Use an explicit personal OWNER/REPOSITORY.")
    owner, name = repository.split("/")
    root = root.resolve()

    def git(*args):
        return run(["git", *args], cwd=root)

    def gh(*args):
        return run(["gh", "api", "--hostname", "github.com", *args], cwd=root)

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise PublishError("Run only against the exact repository root.")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise PublishError("Commit or remove untracked/modified source before publishing.")
    if git("branch", "--show-current") != "main":
        raise PublishError("The initial branch must be main; no checkout is performed.")
    if git("remote"):
        raise PublishError(
            "This first-publication tool refuses a repository with any existing remote."
        )
    head = git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise PublishError("Invalid commit identity.")
    git("fsck", "--strict")
    scan = audit_fn(root)
    if scan["findings"]:
        raise PublishError(
            "Tracked-history scan blocked publication. Inspect audit_repository.py output."
        )
    if not execute:
        return {
            "status": "local-preflight-passed",
            "remote_created": False,
            "repository": repository,
            "head": head,
            "visibility_required": "private",
            "audit": scan,
            "next_action": "Repeat with --execute after authenticating gh on your own machine.",
        }
    identity = json.loads(gh("user"))
    if identity.get("login", "").lower() != owner.lower() or identity.get("type") != "User":
        raise PublishError("Authenticated account does not match the requested personal owner.")
    # Creation is the collision check. A name conflict aborts without touching that repository.
    created = json.loads(
        gh(
            "--method",
            "POST",
            "user/repos",
            "-f",
            "name=" + name,
            "-F",
            "private=true",
            "-F",
            "auto_init=false",
            "-F",
            "has_wiki=false",
            "-F",
            "has_projects=false",
            "-f",
            "description=CAIRN native language development",
        )
    )
    validate_private(created, owner, name)
    endpoint = "repos/" + repository
    confirmed = json.loads(gh(endpoint))
    validate_private(confirmed, owner, name)
    repo_id = created.get("id")
    if type(repo_id) is not int or confirmed.get("id") != repo_id:
        raise PublishError("Repository identity changed after creation; upload stopped.")
    if git("status", "--porcelain", "--untracked-files=all") or git("rev-parse", "HEAD") != head:
        raise PublishError("Local source changed after preflight; upload stopped.")
    # The helper is command-scoped. No global/local credential configuration changes.
    url = f"https://github.com/{owner}/{name}.git"
    git(
        "-c",
        "credential.helper=",
        "-c",
        "credential.helper=!gh auth git-credential",
        "-c",
        "core.hooksPath=/dev/null",
        "push",
        url,
        head + ":refs/heads/main",
    )
    final = json.loads(gh(endpoint))
    validate_private(final, owner, name)
    if final.get("id") != repo_id:
        raise PublishError("Final repository identity mismatch.")
    branch = json.loads(gh(endpoint + "/git/ref/heads/main"))
    if branch.get("object", {}).get("sha") != head:
        raise PublishError("Remote branch differs from the requested commit.")
    git("remote", "add", "origin", url)
    return {
        "status": "published-private",
        "repository": repository,
        "head": head,
        "repository_id": repo_id,
        "private": True,
        "url": final.get("html_url"),
        "force_push": False,
        "privacy_boundary": "Verified before and after push; subsequent administrator changes are outside this tool.",
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repository", help="Personal OWNER/REPO, for example SamMausberg/cairn")
    p.add_argument(
        "--execute",
        action="store_true",
        help="Actually create and push; default performs local checks only.",
    )
    a = p.parse_args()
    try:
        print(
            json.dumps(
                publish(Path(__file__).resolve().parents[1], a.repository, execute=a.execute),
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, PublishError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "status": "stopped",
                    "message": str(error),
                    "policy": "No retries, repository deletion, force push or public fallback.",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

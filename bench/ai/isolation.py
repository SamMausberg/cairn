"""What a subject's session can see of the machine: its own /tmp, its own sandbox and plugin, and nothing of the
repository's checkouts, the other subjects or other Claude Code sessions.

`subjects.isolated` runs `unshare -Urm python3 isolation.py SPEC -- ARGV...`. In a user and mount namespace of its
own, as root there, this mounts the subject's scratch directory over /tmp and an empty file system over each
directory to hide, mounts the subject's own directories back where they were, and then runs ARGV as the caller's user
and group. Nothing outside the namespace changes, and the namespace ends with the session.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys

MS_BIND, MS_REC = 4096, 16384


def main(argv: list[str]) -> None:
    spec, command = json.loads(argv[0]), argv[2:]
    # Each directory to keep is opened before anything is hidden, and mounted back through its descriptor after.
    keep = {path: os.open(path, os.O_PATH | os.O_DIRECTORY) for path in spec["keep"]}
    subprocess.run(["mount", "--bind", spec["tmp"], "/tmp"], check=True)
    for path, mode in spec["hide"]:
        if os.path.isdir(path):
            subprocess.run(["mount", "-t", "tmpfs", "-o", f"mode={mode},size=256m", "hidden", path], check=True)
    libc = ctypes.CDLL(None, use_errno=True)
    for path, fd in keep.items():
        os.makedirs(path, exist_ok=True)
        # The kernel follows /proc/self/fd/N to the directory opened above; mount(8) would look its old path up again.
        if libc.mount(f"/proc/self/fd/{fd}".encode(), path.encode(), None, MS_BIND | MS_REC, None) != 0:
            raise OSError(ctypes.get_errno(), f"cannot mount {path} back")
        os.close(fd)
    user = ["unshare", "--user", f"--map-user={spec['uid']}", f"--map-group={spec['gid']}", "--"]
    os.execvp("unshare", [*user, *command])


if __name__ == "__main__":
    main(sys.argv[1:])

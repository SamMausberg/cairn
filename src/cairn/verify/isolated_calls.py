"""Each call of a validation case in a process of its own.

One child process (`python -m cairn.verify.isolated_calls`) loads the libraries under test and answers one JSON
request per line. For each request it forks, and the forked process makes the one call and writes back what it
returned and what it left in every rw view. A trap, a crash or a call past its limit ends only that process, and is
the call's outcome: `trap` for SIGABRT, which a failed guard raises, `timeout` for the alarm, `crash` for anything
else. The parent that starts the child (`Calls`) never loads the code under test.
"""

from __future__ import annotations

import contextlib
import ctypes as C
import json
import os
import signal
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..compiler.tree import FLOAT
from .boundaries import Param

PR_SET_DUMPABLE = 4  # prctl(2): a process that is not dumpable writes no core and wakes no crash handler
CTYPES: dict[str, Any] = {"bool": C.c_bool, "u8": C.c_uint8, "u16": C.c_uint16, "u32": C.c_uint32, "u64": C.c_uint64,
                          "usize": C.c_size_t, "i8": C.c_int8, "i16": C.c_int16, "i32": C.c_int32, "i64": C.c_int64,
                          "f32": C.c_float, "f64": C.c_double}  # fmt: skip


def encode(ty: str, value: Any) -> Any:
    """A result as JSON that keeps every bit: a float as its hexadecimal text, after rounding to its width."""
    if ty in FLOAT:
        value = struct.unpack("f", struct.pack("f", value))[0] if ty == "f32" else float(value)
        return value.hex()
    return bool(value) if ty == "bool" else int(value)


def invoke(request: dict[str, Any]) -> dict[str, Any]:
    """One call, in the forked process: arguments in, result and every rw view out."""
    lib = C.CDLL(request["lib"])
    function = getattr(lib, request["symbol"])
    params = [Param(**p) for p in request["params"]]
    returns = request["returns"]
    function.argtypes = [CTYPES[p.ty] if p.kind != "view" else C.c_void_p for p in params]
    function.restype = None if returns == "void" else CTYPES[returns]
    args, arrays = [], {}
    offsets = request["case"].get("offsets", {})
    for p in params:
        value = request["case"]["args"][p.name]
        if p.kind != "view":
            args.append(CTYPES[p.ty](value))
            continue
        skip = offsets.get(p.name, 0)
        storage = (CTYPES[p.ty] * (len(value) + skip + 1))(*([0] * skip), *value)  # one spare: a zero extent
        arrays[p.name] = (storage, skip)
        args.append(C.addressof(storage) + skip * C.sizeof(CTYPES[p.ty]))
    result = function(*args)
    after = {}
    for p in params:
        if p.kind == "view" and p.mode == "rw":
            storage, skip = arrays[p.name]
            after[p.name] = [encode(p.ty, x) for x in storage[skip : skip + len(request["case"]["args"][p.name])]]
    out: dict[str, Any] = {"outcome": "return", "after": after}
    if returns != "void":
        out["return"] = encode(returns, result)
    return out


def isolated(request: dict[str, Any]) -> dict[str, Any]:
    """`invoke` in a forked process: a trap, a crash or a call past its limit is the outcome, not an error."""
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - the forked call
        os.close(read)
        C.CDLL(None).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)  # no crash handler starts for a call that traps
        signal.alarm(request.get("seconds", 5))
        code = 0
        try:
            data = json.dumps(invoke(request)).encode()
            with os.fdopen(write, "wb") as out:
                out.write(data)
        except BaseException:
            code = 3
        os._exit(code)
    os.close(write)
    with os.fdopen(read, "rb") as source:
        data = source.read()
    _, status = os.waitpid(pid, 0)
    if os.WIFSIGNALED(status):
        name = signal.Signals(os.WTERMSIG(status)).name
        return {"outcome": "timeout" if name == "SIGALRM" else "trap" if name == "SIGABRT" else "crash", "signal": name}
    if os.WEXITSTATUS(status) != 0 or not data:
        return {"outcome": "crash", "exit_code": os.WEXITSTATUS(status)}
    return json.loads(data)


def child() -> int:
    """Answer one JSON request per line with one JSON outcome per line, until stdin closes."""
    for line in sys.stdin:
        print(json.dumps(isolated(json.loads(line))), flush=True)
    return 0


class Calls:
    """The child process, and the calls it answers."""

    def __init__(self, memory_mib: int = 2048):
        from .testing import limited

        here = str(Path(__file__).resolve().parents[2])
        self.process = subprocess.Popen(
            [sys.executable, "-m", "cairn.verify.isolated_calls"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, env={**os.environ, "PYTHONPATH": here}, preexec_fn=lambda: limited(3600, memory_mib),
        )  # fmt: skip
        self.count = 0

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self.process.stdin and self.process.stdout
        self.count += 1
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        return json.loads(line) if line else {"outcome": "crash", "reason": "the validation child ended"}

    def close(self) -> None:
        with contextlib.suppress(OSError):
            if self.process.stdin:
                self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


if __name__ == "__main__":
    sys.exit(child())

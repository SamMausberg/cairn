#!/usr/bin/env python3
"""Test-only native return/trap observer, not the production runtime.

Checked traps become C++ exceptions and noexcept is removed ONLY in this
instrumented build. That makes thousands of edge cases observable in-process.
It does not verify actual process-abort behavior or establish native refinement.
"""

from __future__ import annotations

import ctypes
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.compiler.cairnc import Emitter, compile_program
from cairn.compiler.lower.codegen import mangle
from cairn.verify.scalar.semantics import prepared
from support import best_profile, profile_flags, runtime_headers, version

CT = {
    "bool": ctypes.c_bool,
    "u8": ctypes.c_uint8,
    "u16": ctypes.c_uint16,
    "u32": ctypes.c_uint32,
    "u64": ctypes.c_uint64,
    "usize": ctypes.c_uint64,
    "i32": ctypes.c_int32,
    "i64": ctypes.c_int64,
}


class NativeScalar:
    def __init__(self, source: str, cxx="clang++"):
        self.functions = prepared(source).functions  # prepared() also enforces the scalar source limit.
        self.temp = tempfile.TemporaryDirectory(prefix="cairn_scalar_test_")
        self.root = Path(self.temp.name)
        program, checker, _ = compile_program(source)
        emitter = Emitter(program, checker)
        cpp = emitter.emit()
        instrumented = [0]

        def instrument(name: str, text: str) -> str:
            """Only the header holding the one abort becomes throwing; the others are verbatim."""
            if "std::abort();" not in text:
                return text
            assert text.count("std::abort();") == 1
            instrumented[0] += 1
            return "struct NativeTrap {};\n" + text.replace(" noexcept", "").replace(
                "std::abort();", "throw NativeTrap{};"
            )

        cpp = cpp.replace(" noexcept", "")
        for name, f in self.functions.items():
            if f.ret.name not in CT or any(t.name not in CT or t.mode != "value" for _, t in f.params):
                raise ValueError("NativeScalar accepts scalar test fixtures only.")
            params = ", ".join(emitter.type(t) + " " + n for n, t in f.params)
            args = ", ".join(n for n, _ in f.params)
            cpp += (
                f'\nextern "C" bool observe_{name}({params}{", " if params else ""}{emitter.type(f.ret)}* result) {{\n'
            )
            cpp += f"  try {{ *result=cf_{mangle(name)}({args}); return true; }} catch(NativeTrap&) {{ return false; }}\n}}\n"
        runtime_headers(self.root, instrument)
        assert instrumented[0] == 1, "Exactly one runtime header carries the trap."
        (self.root / "scalar.cpp").write_text(cpp)
        # Traps are observed as exceptions here, so this build alone drops -fno-exceptions/-fno-rtti.
        flags = profile_flags("library", best_profile(cxx), drop=("-O3", "-fno-exceptions", "-fno-rtti"), add=["-O2"])
        self.command = [cxx, *flags, str(self.root / "scalar.cpp"), "-o", str(self.root / "scalar.so")]
        cp = subprocess.run(self.command, capture_output=True, text=True, timeout=45)
        if cp.returncode:
            raise RuntimeError(cp.stderr)
        self.compiler = version(cxx).splitlines()[0]
        self.generated_sha256 = hashlib.sha256(cpp.encode()).hexdigest()
        self.runtime_sha256 = hashlib.sha256((self.root / "cairn_runtime.hpp").read_bytes()).hexdigest()
        self.lib = ctypes.CDLL(str(self.root / "scalar.so"))
        for name, f in self.functions.items():
            fn = getattr(self.lib, "observe_" + name)
            fn.argtypes = [CT[t.name] for _, t in f.params] + [ctypes.POINTER(CT[f.ret.name])]
            fn.restype = ctypes.c_bool

    def outcome(self, name, args):
        f = self.functions[name]
        value = CT[f.ret.name]()
        ok = getattr(self.lib, "observe_" + name)(*[args[n] for n, _ in f.params], ctypes.byref(value))
        return (
            {"defined": True, "return": value.value} if ok else {"defined": False, "trap": "instrumented-native-trap"}
        )

    def close(self):
        self.temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

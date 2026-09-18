#!/usr/bin/env python3
"""Native oracles, lifetime observation and sanitizers for the 0.6 additions.

No network, model call or benchmark. Finite tests are not a compiler proof.
"""

from __future__ import annotations

import ctypes as C
import hashlib
import importlib.util
import json
import os
import random
import resource
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R / "src"))
from cairn.agent_tools import PROTOCOL, EditSession
from cairn.cairnc import RUNTIME, compile_source
from cairn.linear_certificates import audit_collector
from cairn.project import load_project


def module(name):
    spec = importlib.util.spec_from_file_location(name, R / "tests" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def oracle(data):
    if not data:
        return (3, 0, 0)
    value = 0
    for i, b in enumerate(data):
        if b not in range(48, 58):
            return (1, 0, i)
        value = 10 * value + b - 48
        if value >= 2**64:
            return (2, 0, i)
    return (0, value, 0)


def main():
    rng = random.Random(18092026)
    project = load_project(R / "examples/systems")
    cpp, receipt = compile_source(project.source)
    data = [
        b"",
        b"0",
        b"00",
        b"42",
        b"18446744073709551615",
        b"18446744073709551616",
        b"9" * 21,
        b"0" * 80,
        b"1" * 80,
        b"-1",
        b" 1",
        bytes([255]),
        b"12x",
        b"99\x00",
    ]
    data += [str(rng.randrange(2**64)).encode() for _ in range(500)]
    data += [str(rng.randrange(2**80)).encode() for _ in range(300)]
    data += [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 50))) for _ in range(300)]
    arrays = [bytes(rng.randrange(256) for _ in range(n)) for n in range(129)]
    arrays += [bytes(rng.randrange(256) for _ in range(rng.randrange(257))) for _ in range(1000)]
    arrays += [bytes([v]) * n for n in [0, 1, 3, 64, 256] for v in [0, 1, 2, 254, 255]]
    result = {
        "status": "running",
        "seed": 18092026,
        "compilers": {},
        "finite_tests_only": True,
        "source_sha256": receipt["source_sha256"],
        "runtime_sha256": receipt["runtime_sha256"],
        "native_timing_performed": False,
        "lean_verified": False,
    }

    class Observation(C.Structure):
        _fields_ = [("kind", C.c_uint32), ("value", C.c_uint64), ("offset", C.c_size_t)]

    def execute(args, **kw):
        cp = subprocess.run(args, text=True, capture_output=True, timeout=90, **kw)
        if cp.returncode:
            raise RuntimeError(
                json.dumps(
                    {
                        "command": args,
                        "returncode": cp.returncode,
                        "stdout": cp.stdout,
                        "stderr": cp.stderr,
                    }
                )
            )
        return cp

    with tempfile.TemporaryDirectory(prefix="cairn-systems-") as directory:
        t = Path(directory)
        (t / "candidate.cpp").write_text(cpp)
        (t / "cairn_runtime.hpp").write_text(RUNTIME)
        flags = [
            "-std=c++20",
            "-O2",
            "-ffp-contract=off",
            "-fno-fast-math",
            "-fno-exceptions",
            "-fno-rtti",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-variable",
            "-Wno-unused-parameter",
            "-Wno-unused-but-set-variable",
        ]
        for cxx in ["clang++", "g++"]:
            so = t / (cxx.replace("+", "p") + ".so")
            execute([cxx, *flags, "-shared", "-fPIC", str(t / "candidate.cpp"), "-o", str(so)])
            lib = C.CDLL(str(so))
            lib.cf_observe_decimal.argtypes = [C.c_size_t, C.POINTER(C.c_uint8)]
            lib.cf_observe_decimal.restype = Observation
            lib.cf_sort_bytes.argtypes = [C.c_size_t, C.POINTER(C.c_uint8), C.POINTER(C.c_uint8)]
            lib.cf_sort_bytes.restype = None
            lib.cf_sorted_even.argtypes = lib.cf_sort_bytes.argtypes
            lib.cf_sorted_even.restype = C.c_size_t
            for raw in data:
                buf = (C.c_uint8 * len(raw))(*raw)
                got = lib.cf_observe_decimal(len(raw), buf)
                assert (got.kind, got.value, got.offset) == oracle(raw), (raw, oracle(raw))
                assert bytes(buf) == raw
            for raw in arrays:
                n = len(raw)
                inp = (C.c_uint8 * n)(*raw)
                out = (C.c_uint8 * n)(*([173] * n))
                lib.cf_sort_bytes(n, out, inp)
                assert list(out) == sorted(raw) and bytes(inp) == raw
                out = (C.c_uint8 * n)(*([173] * n))
                count = lib.cf_sorted_even(n, out, inp)
                expected = sorted(x for x in raw if x % 2 == 0)
                assert (
                    count == len(expected)
                    and list(out) == expected + [173] * (n - count)
                    and bytes(inp) == raw
                )
            result["compilers"][cxx] = {
                "version": execute([cxx, "--version"]).stdout.splitlines()[0],
                "flags": flags,
                "parser_cases": len(data),
                "sort_cases": len(arrays),
                "sorted_filter_cases": len(arrays),
                "source_preserved_in_all_cases": True,
            }
        # Inspect actual new[]/delete[] calls at O0 in a separate test-only build.
        owners = module("test_memory").SOURCE + module("test_loop_control").SOURCE
        owner_cpp, _ = compile_source(owners)
        (t / "owners.cpp").write_text(owner_cpp)
        observer = """#include <cstdlib>
#include <new>
#include <cassert>
static unsigned allocations=0, releases=0;
void* operator new[](std::size_t n,const std::nothrow_t&) noexcept { ++allocations; return std::malloc(n); }
void operator delete[](void* p) noexcept { if(p)++releases; std::free(p); }
void operator delete[](void* p,std::size_t) noexcept { if(p)++releases; std::free(p); }
#include "owners.cpp"
int main(){
 for(std::size_t n: {std::size_t(0),std::size_t(1),std::size_t(4),std::size_t(32)}) {
  auto a=allocations,b=releases;cf_sum(n);assert(allocations-a==(n?1:0)&&releases-b==(n?1:0));
  a=allocations;b=releases;cf_early(n);auto e=n<4?n:4;assert(allocations-a==e&&releases-b==e);
  a=allocations;b=releases;cf_loop(n);e=n<9?n:9;assert(allocations-a==e&&releases-b==e);
  a=allocations;b=releases;cf_in_match(n);e=n<5?n:5;assert(allocations-a==e&&releases-b==e);
 }
 auto a=allocations,b=releases;cf_w();assert(allocations-a==7&&releases-b==7);
 assert(allocations==releases);
}
"""
        (t / "observer.cpp").write_text(observer)
        result["lifetime_observer"] = {}
        for cxx in ["clang++", "g++"]:
            exe = t / "observer"
            execute([cxx, "-std=c++20", "-O0", str(t / "observer.cpp"), "-o", str(exe)])
            execute([str(exe)])
            result["lifetime_observer"][cxx] = {
                "cases": 17,
                "all_allocations_released": True,
                "instrumented_O0_only": True,
            }
        # Production runtime, no allocation replacement, real ASan/UBSan/LSan.
        sanitizer = """#include <algorithm>
#include <cassert>
#include <vector>
#include "candidate.cpp"
#include "owners.cpp"
int main(){
 for(std::size_t n=0;n<1024;++n){
  std::vector<std::uint8_t> a(n),b(n,173),expected;
  for(std::size_t i=0;i<n;++i)a[i]=std::uint8_t(i*73+n);
  expected=a;std::sort(expected.begin(),expected.end());cf_sort_bytes(n,b.data(),a.data());assert(b==expected);
  expected.erase(std::remove_if(expected.begin(),expected.end(),[](auto x){return x%2;}),expected.end());
  std::fill(b.begin(),b.end(),173);auto k=cf_sorted_even(n,b.data(),a.data());assert(k==expected.size());
  for(std::size_t i=0;i<k;++i)assert(b[i]==expected[i]);
  for(std::size_t i=k;i<n;++i)assert(b[i]==173);
  auto observation=cf_observe_decimal(n,a.data());(void)observation;
  cf_sum(n);cf_early(n);cf_loop(n);cf_in_match(n);cf_w();
 }
 assert(cf_main()==0);
}
"""
        # Both translation units include the same guarded header through #pragma once.
        (t / "sanitize.cpp").write_text(sanitizer)
        exe = t / "sanitize"
        execute(
            [
                "clang++",
                "-std=c++20",
                "-O1",
                "-g",
                "-fsanitize=address,undefined",
                "-fno-omit-frame-pointer",
                str(t / "sanitize.cpp"),
                "-o",
                str(exe),
            ]
        )
        execute(
            [str(exe)],
            env={
                **os.environ,
                "ASAN_OPTIONS": "detect_leaks=1",
                "UBSAN_OPTIONS": "halt_on_error=1",
            },
        )
        result["sanitizers"] = {
            "compiler": "clang++",
            "array_lengths": 1024,
            "address": True,
            "undefined": True,
            "leak": True,
            "normal_runtime": True,
            "lifetime_loop_and_match_exits": True,
        }
        # Explicit native bounds and tag failures must abort, not return a value.
        traps = """enum R {V(u64); E;} fn consume(r:R)->u64 {match r {R.V(v)=>{return v;}R.E=>{return 0;}}}
fn too_big()->u64 {buffer x:u64[1152921504606846976]=zeroed;return 0;}
fn empty_load()->u64 {buffer x:u64[0]=zeroed;return x[0];}
fn stack_load()->u64 {stack x:u64[0]=zeroed;return x[0];}"""
        (t / "traps.cpp").write_text(compile_source(traps)[0])
        (t / "trap_driver.cpp").write_text(
            "#include \"traps.cpp\"\nint main(int argc,char**argv){switch(argv[1][0]){case 'a':cf_too_big();break;case 'b':cf_empty_load();break;case 'c':cf_stack_load();break;default:cf_consume(ct_R{9,{.v_V=0}});}}"
        )
        result["expected_aborts"] = {}

        def no_core():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        for cxx in ["clang++", "g++"]:
            exe = t / "traps"
            execute([cxx, "-std=c++20", "-O2", str(t / "trap_driver.cpp"), "-o", str(exe)])
            codes = []
            for name in ["a", "b", "c", "d"]:
                cp = subprocess.run(
                    [str(exe), name], text=True, capture_output=True, timeout=5, preexec_fn=no_core
                )
                assert cp.returncode == -6, (name, cp.returncode, cp.stderr)
                codes.append(cp.returncode)
            result["expected_aborts"][cxx] = codes
    # Every inspected expression in new constructs survives identity replacement.
    identity_count = 0
    for source in [
        module("test_memory").SOURCE,
        module("test_sums").SOURCE,
        module("test_loop_control").SOURCE,
    ]:
        before, receipt = compile_source(source)
        for name in receipt["functions"]:
            session = EditSession(source, name)
            for key, site in session.sites.items():
                changed, _ = session.check(
                    {
                        "protocol": PROTOCOL,
                        "session": session.session,
                        "kind": "expr",
                        "site": key,
                        "replacement": site["source"],
                    }
                )
                assert compile_source(changed)[0] == before
                identity_count += 1
    result["new_expression_identity_edits"] = identity_count
    result["arithmetic_certificates"] = audit_collector()
    result["status"] = "passed-finite-tests-and-exact-linear-certificates"
    out = R / "results"
    out.mkdir(exist_ok=True)
    (out / "systems_06.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "arithmetic_certificates"}, indent=2))


if __name__ == "__main__":
    main()

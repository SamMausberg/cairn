"""A loop or a cycle of calls that a program never leaves stays in the native code. C++ lets a compiler assume that a
loop without I/O, volatile or atomic accesses ends, and clang++ and g++ both deleted such a loop, so the program
went on past it. The host compilers get -fno-finite-loops (projects/toolchain.py), and nvcc's device pass, which has
no such option, meets CR_PROGRESS at the top of each `while` body and each function that calls itself
(runtime/cairn_runtime.hpp)."""

import shutil
import subprocess

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.target import parse
from cairn.projects.toolchain import command, extension_flags, flags, host_family, sanitized
from emitted import NVCC_HOST, artifact, emit

# x starts even and grows by two, so it never equals 1: a loop with an exit it never takes. Both compilers returned 7.
EXIT_NEVER_TAKEN = """fn spin(x0:u64) -> u64 {
  let mut x = x0;
  while x != 1 { x = add_wrap(x, 2); }
  return x;
}

fn main() -> i32 {
  if spin(2) == 1 { return 7; }
  return 3;
}
"""
# No exit at all, in a function whose call does nothing else: clang++ gave hang, main and the C entry one address.
NO_EXIT = """fn hang() { while true { } }

fn main() -> i32 {
  hang();
  return 5;
}
"""
# The first loop again as a function that calls itself, which clang++ also answered with 7.
CALLS_ITSELF = """fn spin(x:u64) -> u64 {
  if x == 1 { return x; }
  return spin(add_wrap(x, 2));
}

fn main() -> i32 {
  if spin(2) == 1 { return 7; }
  return 3;
}
"""
LOOPS = {"exit-never-taken": EXIT_NEVER_TAKEN, "no-exit": NO_EXIT}
BUILDS = [("clang++", None), ("clang++", "address"), ("g++", None), ("g++", "address")]


def test_every_host_compiler_line_says_a_loop_may_not_end():
    """A build, a sanitized build, a freestanding image, nvcc's host pass and a PyTorch extension's two passes."""
    lines = [flags(None, "exe"), flags(None, "library"), sanitized(["c++", *flags(None, "exe")], "address")]
    lines += [flags(None, "exe", "aarch64-virt")] if host_family() == "armv8-a" else []
    extension = extension_flags(parse("sm_90"))
    lines += [extension["cflags"], extension["cuda_cflags"][-1].split(",")]
    if shutil.which("nvcc") and shutil.which("g++"):
        device = command("g++", "p.cpp", "p", cuda=True, device=parse("sm_90"))
        lines.append(device[device.index("-Xcompiler") + 1].split(","))
    assert all("-fno-finite-loops" in line for line in lines), lines


def built(tmp_path, source: str, cxx: str, sanitizer: str | None) -> str:
    """`source` built as `cairn build` builds an executable, checked by `sanitizer` when one is named."""
    path = tmp_path / "endless.cairn"
    path.write_text(source, encoding="utf-8")
    return artifact(path, cxx, kind="exe", sanitizer=sanitizer)


@pytest.mark.parametrize(("cxx", "sanitizer"), BUILDS, ids=lambda x: x or "plain")
@pytest.mark.parametrize("source", LOOPS.values(), ids=LOOPS.keys())
def test_a_loop_the_program_never_leaves_is_still_running_when_its_time_is_up(tmp_path, source, cxx, sanitizer):
    executable = built(tmp_path, source, cxx, sanitizer)
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([executable], capture_output=True, timeout=3)


@pytest.mark.parametrize(("cxx", "sanitizer"), BUILDS, ids=lambda x: x or "plain")
def test_a_function_that_calls_itself_forever_never_returns(tmp_path, cxx, sanitizer):
    """At -O3 each compiler turns the call into a loop, which runs on; the sanitized build at -O1 may keep the calls
    until the stack runs out, which AddressSanitizer reports. Neither returns 7 or 3."""
    executable = built(tmp_path, CALLS_ITSELF, cxx, sanitizer)
    try:
        done = subprocess.run([executable], capture_output=True, text=True, timeout=3)
    except subprocess.TimeoutExpired:
        return
    assert done.returncode < 0 or "stack-overflow" in done.stderr, (done.returncode, done.stderr[-500:])


# The same three in a device lane. Every lane runs the loop first, so no lane reaches its store.
LANES = {
    "exit-never-taken": EXIT_NEVER_TAKEN.split("fn main")[0]
    + "fn main() -> i32 {\n  buffer d:u64[4]@device = zeroed;\n  parallel i in 4 { d[i] = spin(2 + u64(i) * 0); }\n"
    "  buffer h:u64[4] = zeroed;\n  transfer(h, d);\n  return i32(h[0]);\n}\n",
    "no-exit": NO_EXIT.split("fn main")[0]
    + "fn main() -> i32 {\n  buffer d:u64[4]@device = zeroed;\n  parallel i in 4 { hang(); d[i] = 5; }\n"
    "  buffer h:u64[4] = zeroed;\n  transfer(h, d);\n  return i32(h[0]);\n}\n",
    "calls-itself": CALLS_ITSELF.split("fn main")[0]
    + "fn main() -> i32 {\n  buffer d:u64[4]@device = zeroed;\n  parallel i in 4 { d[i] = spin(2 + u64(i) * 0); }\n"
    "  buffer h:u64[4] = zeroed;\n  transfer(h, d);\n  return i32(h[0]);\n}\n",
}


def ptx(tmp_path, source: str, target: str) -> str:
    """The PTX the project's own device command line gives `source` for `target`; nothing runs."""
    if not shutil.which("nvcc") or not shutil.which(NVCC_HOST):
        pytest.skip(f"needs nvcc and {NVCC_HOST}")
    cpp, receipt = compile_source(source)
    assert "cuda" in receipt["requires"]
    program, out = emit(tmp_path, cpp, entry=None)
    line = [
        part for part in command(NVCC_HOST, program, out + ".ptx", cuda=True, device=parse(target)) if part != "-shared"
    ]
    line.insert(line.index("-o"), "-ptx")
    done = subprocess.run(line, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]
    return (tmp_path / "p.ptx").read_text()


@pytest.mark.parametrize("target", ["sm_90", "sm_120"])  # CUDA 13.0 compiles below sm_100 with NVVM 7, above with 20
@pytest.mark.parametrize("name", LANES)
def test_device_code_keeps_a_loop_the_program_never_leaves(tmp_path, name, target):
    """Without CR_PROGRESS, nvcc 13.0 compiled the lane of `no-exit` for both targets, and `exit-never-taken` and
    `calls-itself` for sm_120, to a store after nothing: the loop was gone. A lane may store only once it has called
    the function that loops."""
    code = ptx(tmp_path, LANES[name], target)
    assert "st.global" not in code or "call.uni" in code, code[-3000:]

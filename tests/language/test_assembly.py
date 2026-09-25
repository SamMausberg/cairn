"""Typed assembly: what it refuses and why, what x86-64 instructions compute when both compilers build them under
the address and undefined sanitizers, and PTX in the SASS nvcc makes of it for sm_120."""

import math
import re
import shutil
import subprocess

import pytest

from cairn.agent.projection import canonical_source
from cairn.agent.teaching import select_cards
from cairn.compiler.cairnc import compile_source
from cairn.compiler.primitives.machine import host, satisfies
from cairn.projects.build import build as build_project
from cairn.projects.project import load_project
from emitted import code_of, contract, device_build, on_device, refused, watched

HOST = host()
OTHER = "aarch64" if HOST == "x86_64" else "x86_64"
# A device region: the lane indexes an @device view, so it runs as CUDA lanes whatever the asm does.
LANE = "fn f(n:usize, out:rw<u32>[n]@device, xs:ro<u32>[n]@device) {{ parallel i in n {{ let seen = xs[i]; unsafe {{ {} }} }} }}"
HOSTED = "fn f(n:usize, out:rw<u64>[n], xs:ro<u64>[n], x:u64) -> u64 {{ unsafe {{ {} }} return 0; }}"
REGISTER = "rax" if HOST == "x86_64" else "x0"

REJECTIONS = {
    "typed assembly runs inside unsafe": (
        "E-UNSAFE", f'fn f(x:u64) -> u64 {{ asm {HOST} "op %0" (out r:u64 = x); return r; }}'),
    "a bool output has no register constraint": (
        "E-ASM-CONSTRAINT", HOSTED.format(f'asm {HOST} "op %0, %1" (out b:bool, x);')),
    "a record input has no register constraint": (
        "E-ASM-CONSTRAINT", "struct P { a:u64; }\n" + HOSTED.format(f'let p = P(x); asm {HOST} "op %0" (p);')),
    "a storage float has no register constraint": (
        "E-ASM-CONSTRAINT", HOSTED.format(f'let h = f16(1.0); asm {HOST} "op %0" (h);')),
    "PTX has no 8-bit register constraint": (
        "E-ASM-CONSTRAINT", LANE.format('asm ptx sm_75 "mov.b16 %0, 1;" (out r:u8); out[i] = u32(r);')),
    "the template names an operand the asm does not have": (
        "E-ASM-OPERANDS", HOSTED.format(f'asm {HOST} "op %0, %2" (out r:u64, x);')),
    "every operand is named in the template": (
        "E-ASM-OPERANDS", HOSTED.format(f'asm {HOST} "op %0" (out r:u64, x);')),
    "outputs come before inputs": (
        "E-ASM-OPERANDS", HOSTED.format(f'asm {HOST} "op %0, %1" (x, out r:u64);')),
    "a literal percent sign is written %%": (
        "E-ASM-OPERANDS", HOSTED.format(f'asm {HOST} "op %0, %rax" (out r:u64 = x);')),
    "PTX takes no operand modifier": (
        "E-ASM-OPERANDS", LANE.format('asm ptx sm_75 "brev.b32 %w0, %1;" (out r:u32, xs[i]); out[i] = r;')),
    "PTX runs only in device code": (
        "E-ASM-TARGET", HOSTED.format('asm ptx sm_75 "mov.u64 %0, %1;" (out r:u64, x);')),
    "PTX in a plain function is host code even when a lane calls it": (
        "E-ASM-TARGET", 'fn g(x:u32) -> u32 { unsafe { asm ptx sm_75 "brev.b32 %0, %1;" (out r:u32, x); return r; } }\n'
        "fn f(n:usize, out:rw<u32>[n]@device) { parallel i in n { out[i] = g(u32(i)); } }"),
    "host assembly does not run in a device lane": (
        "E-ASM-TARGET", LANE.format(f'asm {HOST} "op %0" (out r:u32 = xs[i]); out[i] = r;')),
    "PTX names the architecture it needs": (
        "E-ASM-TARGET", LANE.format('asm ptx "brev.b32 %0, %1;" (out r:u32, xs[i]); out[i] = r;')),
    "an architecture is sm_ and a number": (
        "E-ASM-TARGET", LANE.format('asm ptx blackwell "brev.b32 %0, %1;" (out r:u32, xs[i]); out[i] = r;')),
    "host assembly names no architecture": (
        "E-ASM-TARGET", HOSTED.format(f'asm {HOST} sm_75 "op %0" (out r:u64 = x);')),
    "the target is ptx, x86_64 or aarch64": (
        "E-ASM-TARGET", HOSTED.format('asm riscv64 "op %0" (out r:u64 = x);')),
    "an array passed by its address declares what the asm does to it": (
        "E-ASM-EFFECT", HOSTED.format(f'asm {HOST} "op %0, %1" (out r:u64, xs);')),
    "a lane's PTX declares the memory it reads through an address": (
        "E-ASM-EFFECT", LANE.format('asm ptx sm_75 "ld.global.nc.u32 %0, [%1];" (out v:u32, xs); out[i] = v;')),
    "a declared read names an address operand": (
        "E-ASM-EFFECT", HOSTED.format(f'asm {HOST} "op %0" (out r:u64 = x) effects(read:xs);')),
    "asm declares no effect outside its vocabulary": (
        "E-ASM-EFFECT", HOSTED.format(f'asm {HOST} "op %0" (out r:u64 = x) effects(alloc);')),
    "a barrier is not lane-safe": (
        "E-ASM-LANE", LANE.format('asm ptx sm_75 "bar.sync 0;" effects(barrier);')),
    "an atomic is not lane-safe": (
        "E-ASM-LANE", LANE.format('asm ptx sm_75 "red.global.add.u32 [%0], 1;" (out) effects(write:out, atomic);')),
    "a lane writes an outer array only at its own element, which an address does not say": (
        "E-PARALLEL-RACE", LANE.format('asm ptx sm_75 "st.global.u32 [%0], 1;" (out) effects(write:out);')),
    "a lane reads through an address no array the lanes write": (
        "E-PARALLEL-RACE", LANE.format('out[i] = 1; asm ptx sm_75 "ld.global.u32 %0, [%1];" (out v:u32, out) '
                                       "effects(read:out); out[i] = v;")),
    "a written address needs a writable array": (
        "E-WRITE-LEASE", HOSTED.format(f'asm {HOST} "op %0" (xs) effects(write:xs);')),
    "device code reaches no host memory by address": (
        "E-PLACEMENT", "fn f(n:usize, out:rw<u32>[n]@device, xs:ro<u32>[n]) { parallel i in n { unsafe { "
        'asm ptx sm_75 "ld.global.u32 %0, [%1];" (out v:u32, xs) effects(read:xs); out[i] = v; } } }'),
    "a host clobber names a register of the target": (
        "E-ASM-CLOBBER", HOSTED.format(f'asm {HOST} "op %0" (out r:u64 = x) clobbers(sp);')),
    "a clobber is named once": (
        "E-ASM-CLOBBER", HOSTED.format(f'asm {HOST} "op %0" (out r:u64 = x) clobbers({REGISTER}, {REGISTER});')),
    "PTX registers are virtual and clobber nothing": (
        "E-ASM-CLOBBER", LANE.format('asm ptx sm_75 "brev.b32 %0, %1;" (out r:u32, xs[i]) clobbers(r1); out[i] = r;')),
    "an output is a fresh name": (
        "E-SHADOW", HOSTED.format(f'asm {HOST} "op %0" (out x:u64);')),
    "a pure function holds no assembly": (
        "E-EFFECT-CEILING", f'fn f(x:u64) -> u64 pure {{ unsafe {{ asm {HOST} "op %0" (out r:u64 = x); return r; }} }}'),
    "two assembly calls do not sit beside each other": (
        "E-EFFECT-ORDER", f'fn g(x:u64) -> u64 {{ unsafe {{ asm {HOST} "op %0" (out r:u64 = x); return r; }} }}\n'
        "fn f(x:u64) -> u64 = g(x) + g(x);"),
    "a host lane runs no host assembly": (
        "E-PARALLEL-CALL", f'fn g(x:u64) -> u64 {{ unsafe {{ asm {HOST} "op %0" (out r:u64 = x); return r; }} }}\n'
        "fn f(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = g(u64(i)); } }"),
    "an array lent to a task is not passed by address": (
        "E-LEASED", "fn fill(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = 1; } }\n"
        f'fn f(n:usize, out:rw<u64>[n]) {{ let t = spawn fill(out); unsafe {{ asm {HOST} "op %0" (out) '
        "effects(read:out); } wait(t); }"),
}  # fmt: skip


@pytest.mark.parametrize("rule", REJECTIONS)
def test_typed_assembly_refuses(rule):
    code, source = REJECTIONS[rule]
    refused(code, source)


def test_assembly_for_another_host_family_is_typed_but_not_built_here(tmp_path):
    source = f'fn main() -> i32 {{ unsafe {{ asm {OTHER} "op %0" (out r:u64 = 1); }} return 0; }}\n'
    assert f"asm:{OTHER}" in compile_source(source)[1]["requires"]  # checking says nothing of the machine
    path = tmp_path / "other.cairn"
    path.write_text(source)
    assert code_of(lambda: build_project(load_project(path), kind="exe")) == "E-ASM-TARGET"


# Every operand kind, register class and clobber the x86-64 lowering has, each against a value Python computes.
X86 = """
// The byte order of a word reversed: an output that starts at an input.
fn swapped(x:u64) -> u64 {
  unsafe {
    asm x86_64 "bswapq %0" (out r:u64 = x);
    return r;
  }
}

// The high half of a 128-bit product: mulq writes rdx:rax, which the statement names.
fn high(a:u64, b:u64) -> u64 {
  unsafe {
    asm x86_64 "movq %1, %%rax; mulq %2; movq %%rdx, %0" (out hi:u64, a, b) clobbers(rax, rdx);
    return hi;
  }
}

// A wrapping sum read through the array's address.
fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  unsafe {
    asm x86_64 "xorq %0, %0; movq %2, %%rcx; jrcxz 2f; 1: addq -8(%1,%%rcx,8), %0; decq %%rcx; jnz 1b; 2:"
      (out t:u64, xs, n) clobbers(rcx) effects(read:xs);
    return t;
  }
}

// Every element set to v through the array's address, by rep stosq.
fn fill(n:usize, out:rw<u64>[n], v:u64) {
  unsafe {
    asm x86_64 "movq %0, %%rdi; movq %1, %%rcx; movq %2, %%rax; rep stosq" (out, n, v) clobbers(rdi, rcx, rax)
      effects(write:out);
  }
}

// A correctly rounded square root and an f32 sum, in SSE registers.
fn root(x:f64) -> f64 {
  unsafe {
    asm x86_64 "sqrtsd %1, %0" (out r:f64, x);
    return r;
  }
}

fn sum32(a:f32, b:f32) -> f32 {
  unsafe {
    asm x86_64 "addss %1, %0" (out r:f32 = a, b);
    return r;
  }
}

// An 8-bit register, and a 32-bit one named through the k modifier.
fn rotated(b:u8) -> u8 {
  unsafe {
    asm x86_64 "rolb $1, %0" (out r:u8 = b);
    return r;
  }
}

fn negated(x:i32) -> i32 {
  unsafe {
    asm x86_64 "negl %k0" (out r:i32 = x);
    return r;
  }
}

// No outputs, so volatile; a fence, so a memory clobber.
fn settle() {
  unsafe { asm x86_64 "mfence" effects(fence); }
}

// The time-stamp counter depends on more than its inputs, so the statement says volatile.
fn stamp() -> u64 {
  unsafe {
    asm volatile x86_64 "rdtsc; shlq $32, %%rdx; orq %%rdx, %%rax; movq %%rax, %0" (out t:u64) clobbers(rax, rdx);
    return t;
  }
}

fn main() -> i32 {
  let mut xs = Buf[u64](5);
  fill(xs, 7);
  xs[4] = 18446744073709551615;
  settle();
  let t = stamp();
  let a = swapped(0x0102030405060708);                // each asm call is a statement: C++ leaves operand order open
  let b = high(18446744073709551615, 3);
  let c = total(xs);
  println(a, ' ', b, ' ', c, ' ', xs[0]);
  let d = root(2.0);
  let e = sum32(1.5, 2.25);
  let f = rotated(129);
  let g = negated(-2147483647);
  println(d, ' ', e, ' ', f, ' ', g, ' ', t > 0);
  return 0;
}
"""


def oracle() -> str:
    """What X86 prints, computed by Python from what each instruction is defined to do."""
    swapped = int.from_bytes((0x0102030405060708).to_bytes(8, "little"), "big")
    high = ((2**64 - 1) * 3) >> 64
    total = (4 * 7 + 2**64 - 1) % 2**64
    rotated = ((129 << 1) | (129 >> 7)) & 0xFF
    return f"{swapped} {high} {total} 7\n{math.sqrt(2.0)!r} 3.75 {rotated} 2147483647 true\n"


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_x86_assembly_computes_what_its_instructions_define(tmp_path, cxx):
    if HOST != "x86_64":
        pytest.skip("x86_64 assembly builds on an x86-64 host")
    done = watched(tmp_path, compile_source(X86)[0], cxx, "address,undefined")
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, done.stderr[-3000:]
    assert done.stdout == oracle()


def test_the_lowering_states_constraints_clobbers_and_volatile():
    cpp = compile_source(X86)[0]
    assert '__asm__("bswapq %0" : "+r"(v_r) :  : "cc");' in cpp  # pure: the compiler may move or merge it
    assert '"=&r"(v_hi)' in cpp and '"rax", "rdx", "cc"' in cpp  # an output never shares an input's register
    assert '__asm__ volatile("mfence" :  :  : "cc", "memory");' in cpp
    assert '__asm__ volatile("rdtsc' in cpp and '"=&x"(v_r)' in cpp


def test_the_receipt_and_the_row_say_what_the_statement_declares():
    receipt = compile_source(X86)[1]["functions"]
    assert receipt["total"]["assembly"] == [{"line": 21, "target": "x86_64", "effects": ["read:xs"], "volatile": True,
                                             "trust": "declared-not-checked"}]  # fmt: skip
    assert {"asm:x86_64", "read:xs"} <= set(receipt["total"]["effects"])
    assert {"asm:x86_64", "fence"} <= set(receipt["settle"]["effects"])
    assert receipt["swapped"]["effects"] == ["asm:x86_64"]
    assert receipt["total"]["syntactic_check_sites"]["assembly"] == 1


PTX = """
// A bit reversal in each lane, and a helper that loads through the read-only cache behind a fence.
fn reversed(n:usize, out:rw<u32>[n]@device, xs:ro<u32>[n]@device) {
  parallel i in n {
    unsafe {
      asm ptx sm_75 "brev.b32 %0, %1;" (out r:u32, xs[i]);
      out[i] = r;
    }
  }
}

kernel fn cached(n:usize, xs:ro<u32>[n]@device, i:usize) -> u32 {
  unsafe {
    asm ptx sm_75 "fence.acq_rel.gpu;" effects(fence);
    asm ptx sm_75 "ld.global.nc.u32 %0, [%1];" (out v:u32, xs) effects(read:xs);
    return v + u32(i);
  }
}

fn loads(n:usize, out:rw<u32>[n]@device, xs:ro<u32>[n]@device) {
  parallel i in n { out[i] = cached(xs, i); }
}
"""


def sass(tmp_path, source: str) -> str:
    if not shutil.which("cuobjdump"):
        pytest.skip("needs cuobjdump")
    obj = device_build(tmp_path, compile_source(source)[0], entry=None)
    return subprocess.run(["cuobjdump", "-sass", str(obj)], capture_output=True, text=True, check=True).stdout


def test_ptx_compiles_for_sm_120_and_its_instructions_are_in_the_sass(tmp_path):
    code = sass(tmp_path, PTX)
    assert "BREV" in code and "LDG.E.CONSTANT" in code and "MEMBAR" in code


def test_a_device_pass_for_an_older_architecture_stops_at_the_statement(tmp_path):
    with pytest.raises(AssertionError, match="The PTX in reversed needs sm_121"):
        device_build(tmp_path, compile_source(PTX.replace('sm_75 "brev', 'sm_121 "brev'))[0], entry=None)


def test_a_build_for_a_device_target_the_ptx_does_not_run_on_is_refused(tmp_path):
    path = tmp_path / "newer.cairn"
    path.write_text(PTX.replace('sm_75 "brev', 'sm_121 "brev'))
    assert code_of(lambda: build_project(load_project(path), device_target="sm_120")) == "E-ASM-TARGET"


def test_ptx_rows_and_requirements():
    _, receipt = compile_source(PTX)
    assert receipt["requires"] == ["cuda", "ptx:sm_75"]
    assert {"asm:ptx", "fence", "read:xs"} <= set(receipt["functions"]["cached"]["effects"])
    assert "asm:ptx" in receipt["functions"]["loads"]["effects"]


@pytest.mark.parametrize(
    ("target", "capability", "runs"),
    [("sm_120", "sm_75", True), ("sm_120", "sm_121", False), ("sm_90", "sm_90a", False), ("sm_90a", "sm_90a", True),
     ("sm_100a", "sm_90a", False), ("sm_103f", "sm_100f", True), ("sm_103", "sm_100f", False),
     ("sm_120f", "sm_100f", False), ("native", "sm_75", False)],
)  # fmt: skip
def test_an_architecture_satisfies_what_ptx_needs(target, capability, runs):
    assert satisfies(target, capability) == runs


@pytest.mark.parametrize("source", [X86, PTX])
def test_the_canonical_projection_keeps_typed_assembly(source):
    canonical = canonical_source(source)
    assert canonical_source(canonical) == canonical
    assert compile_source(canonical)[0] == compile_source(source)[0]


def test_the_assembly_card_is_chosen_by_the_asm_token():
    assert "assembly" in select_cards('fn f() { unsafe { asm x86_64 "mfence" effects(fence); } }')
    assert "assembly" not in select_cards("fn f(x:u64) -> u64 = x;")


COOPERATIVE = """
fn block_bits(n:usize, x:ro<u32>[n]@device, g:usize, out:rw<u32>[g]@device) {
  blocks b in g threads t in 256 {
    shared partial:u32[256] = zeroed;
    let i = b * 256 + t;
    if i < n {
      unsafe {
        asm ptx sm_75 "brev.b32 %0, %1;" (out r:u32, x[i]);
        partial[t] = r;
      }
    }
    barrier;
    if t == 0 { out[b] = partial[0]; }
  }
}
"""


def test_a_thread_of_a_cooperative_region_runs_typed_ptx(tmp_path):
    assert "BREV" in sass(tmp_path, COOPERATIVE)


def passes(cpp: str, name: str) -> list[list[str]]:
    """The variables of `name`'s lane body in the order it first reads them, as the host pass and the device pass of
    nvcc each preprocess it: the variables its closure captures."""
    body = "\n".join(line for line in cpp.splitlines() if not line.lstrip().startswith("#include"))
    seen = []
    for defined in ([], ["-D__CUDA_ARCH__=1200"]):
        text = subprocess.run(["g++", "-E", "-P", *defined, "-x", "c++", "-"], input=body, capture_output=True,
                              text=True, check=True).stdout  # fmt: skip
        lane = text.split(f"ci_{name}(")[-1].split("[=]", 1)[1].split("\n}\n")[0]
        order: list[str] = []
        for word in re.findall(r"\bv_[a-z0-9_]+\b", lane):
            if word not in order:
                order.append(word)
        seen.append(order)
    return seen


WIDE = """
fn sums4(n:usize, x:ro<f32>[n]@device, m:usize, out:rw<f32>[m]@device) {
  parallel i in m {
    let offset:u64 = u64(i) * 16;
    unsafe {
      asm ptx sm_75 "{ .reg .u64 a; add.u64 a, %4, %5; ld.global.cg.v4.f32 {%0, %1, %2, %3}, [a]; }" (out a0:f32, out a1:f32, out a2:f32, out a3:f32, x, offset) effects(read:x);
      out[i] = (a0 + a1) + (a2 + a3);
    }
  }
}
"""


@pytest.mark.skipif(not shutil.which("g++"), reason="needs g++")
def test_both_passes_of_a_lane_read_the_same_variables():
    """nvcc builds a lane's kernel from a closure it lays out in each pass, and a kernel whose captures differ between
    the passes does not launch: measured on an RTX 5070 Ti, a lane whose view reached only its PTX failed with
    'invalid resource handle', and a cooperative region's with 'invalid device function'. Every input is evaluated
    in both passes, so both read the same variables in the same order."""
    for source, name in ((PTX, "reversed"), (WIDE, "sums4"), (COOPERATIVE, "block_bits")):
        host_pass, device_pass = passes(compile_source(source)[0], name)
        assert host_pass == device_pass, (name, host_pass, device_pass)
    assert "v_x" in passes(compile_source(WIDE)[0], "sums4")[0]  # the view the PTX alone reads


RUNS = (
    WIDE
    + PTX
    + COOPERATIVE
    + """
fn main() -> i32 {
  let n:usize = 4096;
  let q:usize = n / 4;
  let g:usize = n / 256;
  buffer x:f32[n]@device = zeroed;
  buffer bits:u32[n]@device = zeroed;
  buffer out:f32[q]@device = zeroed;
  buffer turned:u32[n]@device = zeroed;
  buffer firsts:u32[g]@device = zeroed;
  parallel i in n { x[i] = f32(i % 8); bits[i] = u32(i); }
  sums4(x, out);
  reversed(turned, bits);
  block_bits(bits, firsts);
  buffer back:f32[q] = zeroed;
  buffer flipped:u32[n] = zeroed;
  buffer first:u32[g] = zeroed;
  transfer(back, out);
  transfer(flipped, turned);
  transfer(first, firsts);
  for k in 0..q { if back[k] != f32((4 * k) % 8 + (4 * k + 1) % 8 + (4 * k + 2) % 8 + (4 * k + 3) % 8) { return 1; } }
  if flipped[1] != 2147483648 || flipped[2] != 1073741824 { return 2; }
  if first[0] != 0 || first[1] != 8388608 { return 3; }
  return 0;
}
"""
)


def test_typed_ptx_in_a_lane_and_a_cooperative_region_runs_on_the_device(tmp_path):
    """Run only under `make gpu`: a 16-byte load in a lane, a bit reversal in a lane and in a cooperative region."""
    cpp = compile_source(RUNS)[0]
    with on_device():
        done = contract(tmp_path, cpp, "g++", cuda=True, timeout=600)
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])

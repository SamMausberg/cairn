"""`reduce op out[k] for|parallel i in n yield e;` writes the total into one element, where the reduction runs.

The form is `reduce`'s with the binding replaced by an element: the operators, the order rules and checked unsigned
`+` are the same, out[k] must be an rw view or buffer of the yields' type (E-REDUCE-TARGET, E-TYPE-MISMATCH), no
yield may read out (E-PARALLEL-RACE), and over @device views out must be @device (E-PLACEMENT): the total stays on the
device for the work after it, no copy crosses and no wait follows, and a checked sum that overflows traps in a lane. A
`scan` whose total nobody reads keeps its prefixes on the device the same way. On the host the form is the fold, then
one store. The device programs run emulated on host threads under both compilers, compile for sm_120, and run on a
device under `make gpu` alone.
"""

import signal

import pytest

from cairn.compiler.cairnc import compile_program, compile_source
from cairn.compiler.lower import execution
from cairn.compiler.lower.header import header
from cairn.verify.scalar.semantics import equivalent
from emitted import (
    contract,
    device_build,
    hosted_library,
    on_device,
    printed,
    ran_emulated,
    ran_on_device,
    refused,
    round_trips,
    sanitized,
    sanitizers,
    watched,
)

VIEWS = (
    "fn f(n:usize, x:ro<u64>[n], s:ro<i64>[n], d:ro<f64>[n], out:rw<u64>[4], small:rw<u32>[4], fo:rw<f64>[1],\n"
    "     so:rw<i64>[1],\n"
    "     dx:ro<u64>[n]@device, dout:rw<u64>[4]@device) {\n  "
)


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-REDUCE-TARGET", "reduce + x[0] for i in n yield x[i];"),  # a read-only view cannot take the total
        ("E-REDUCE-TARGET", "let mut k:u64 = 0; reduce + k[0] for i in n yield x[i];"),
        ("E-TYPE-MISMATCH", "reduce + small[0] for i in n yield x[i];"),  # the element holds the yields' type
        ("E-PLACEMENT", "reduce + out[0] for i in n yield dx[i];"),  # a device total lands in device memory
        ("E-PLACEMENT", "reduce + dout[0] for i in n yield x[i];"),  # and a device element takes a device total
        ("E-PARALLEL-RACE", "reduce add_wrap out[0] for i in 4 yield out[i];"),  # no yield reads what takes the total
        ("E-REDUCE-OP", "reduce + so[0] for i in n yield s[i];"),  # a signed partial sum may overflow alone
        ("E-REDUCE-OP", "reduce * out[0] for i in n yield x[i];"),  # a checked product may overflow before a zero
        ("E-REDUCE-ORDER", "reduce + fo[0] parallel i in n yield d[i];"),
        ("E-COLLECT-BINDING", "let t = reduce + out[0] for i in n yield x[i];"),  # an element, or a binding
        ("E-COLLECT-BINDING", "reduce + for i in n yield x[i];"),
        ("E-UNBOUND", "reduce + out[i] for i in n yield x[i];"),  # the element is one, named before the lanes
    ],
)
def test_rejections(code, body):
    refused(code, VIEWS + body + "\n}")


def test_an_element_lent_to_queued_work_cannot_take_a_total():
    source = """fn f(n:usize, x:ro<u64>[n]@device, out:rw<u64>[n]@device) {
  let t = spawn parallel i in n { out[i] = x[i]; };
  reduce + out[0] for i in n yield x[i];
  wait(t);
}"""
    refused("E-LEASED", source)


def test_derive_grad_refuses_a_reduction_into_an_element():
    source = (
        "fn run(n:usize, out:rw<f64>[1], x:ro<f64>[n]) { reduce + out[0] for i in n yield x[i]; }\n"
        "derive grad[x] for run;\n"
    )
    assert "written into an element" in refused("E-GRAD-FORM", source)["message"]


def test_the_value_model_leaves_the_form_out_and_says_unknown():
    source = "fn f(n:usize, x:ro<u64>[4], out:rw<u64>[4]) -> u64 { reduce add_wrap out[1] for i in 4 yield x[i]; return 1; }\n"
    assert equivalent(source, source, "f")["status"] == "unknown"


HOST = """
fn row_sums(r:usize, c:usize, rc:usize, x:ro<u64>[rc], sums:rw<u64>[r]) {
  for k in 0..r { reduce + sums[k] for j in c yield x[k * c + j]; }
}
fn mixed(n:usize, x:ro<u64>[n], out:rw<u64>[3]) {
  reduce ^ out[0] parallel i in n yield mul_wrap(x[i], 0x9e3779b97f4a7c15);
  reduce max out[1] parallel i in n yield x[i];
  reduce + out[2] parallel i in n yield x[i];
}
fn halves(n:usize, x:ro<f64>[n], out:rw<f64>[2]) { reduce + out[1] for i in n yield f64(x[i]) * 0.5; }
fn main() -> i32 {
  let r:usize = 37;
  let c:usize = 1001;
  let rc = r * c;
  buffer x:u64[rc] = zeroed;
  for i in 0..rc { x[i] = mul_wrap(u64(i), 2654435761) % 100000; }
  buffer sums:u64[r] = zeroed;
  row_sums(r, c, rc, x, sums);
  for k in 0..r {
    let mut want:u64 = 0;
    for j in 0..c { want += x[k * c + j]; }
    if sums[k] != want { return 1; }
  }
  buffer out:u64[3] = zeroed;
  mixed(rc, x, out);
  let mut hash:u64 = 0;
  let mut most:u64 = 0;
  let mut total:u64 = 0;
  for i in 0..rc {
    hash = hash ^ mul_wrap(x[i], 0x9e3779b97f4a7c15);
    most = max(most, x[i]);
    total += x[i];
  }
  if out[0] != hash || out[1] != most || out[2] != total { return 2; }
  out[2] = 7;
  mixed(0, x[0..0], out);                             // nothing to fold: each element takes its operator's identity
  if out[0] != 0 || out[1] != 0 || out[2] != 0 { return 3; }
  buffer fx:f64[c] = zeroed;
  for i in 0..c { fx[i] = f64(i % 9); }
  buffer fo:f64[2] = zeroed;
  fo[0] = 5.0;
  halves(c, fx, fo);
  let mut half:f64 = 0.0;
  for i in 0..c { half = half + fx[i] * 0.5; }
  if fo[1] != half || fo[0] != 5.0 { return 4; }     // the element named, and no other
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_on_the_host_each_element_takes_the_folds_total(tmp_path, cxx):
    """In order and pooled, over 37,037 elements and over none, under clang++ with the address and undefined-behaviour
    sanitizers and under g++."""
    done = contract(tmp_path, compile_source(HOST)[0], cxx, *sanitizers(cxx))
    assert done.returncode == 0, (done.returncode, done.stderr[-3000:])


def test_the_pooled_reductions_race_nowhere(tmp_path):
    done = watched(tmp_path, compile_source(HOST)[0], "clang++", "thread")
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]


OVERFLOW = """
fn main() -> i32 {
  let n:usize = 5000;
  buffer x:u32[n] = zeroed;
  for i in 0..n { x[i] = 1000000; }
  buffer out:u32[1] = zeroed;
  reduce + out[0] parallel i in n yield x[i];         // five billion does not fit in u32
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_checked_total_that_overflows_traps_before_the_element_is_written(tmp_path, cxx):
    done = contract(tmp_path, compile_source(OVERFLOW)[0], cxx)
    assert done.returncode == -signal.SIGABRT, (done.returncode, done.stderr[-2000:])


KERNELS = """
fn stats(n:usize, m:usize, x:ro<u32>[n]@device, out:rw<u64>[4]@device) {
  reduce + out[0] parallel i in n yield u64(x[i]);    // checked, and it stays on the device
  reduce max out[1] for i in n yield u64(x[i]);
  reduce add_wrap out[2] for i in n yield mul_wrap(u64(x[i]), u64(x[i]));
  reduce + out[3] for i in m yield u64(x[i]);         // m is 0: the identity
}
fn share(n:usize, x:ro<u32>[n]@device, total:rw<f32>[1]@device, y:rw<f32>[n]@device) {
  reduce + total[0] for i in n yield f32(x[i] % 7);   // whole numbers: every order adds them exactly
  parallel i in n { y[i] = f32(x[i] % 7) / total[0]; }
}
fn prefix(n:usize, out:rw<u32>[n]@device, x:ro<u32>[n]@device) {
  scan + exclusive out for i in n yield x[i] % 5;     // no total for the host: the prefixes stay on the device
  parallel i in n { out[i] = out[i] * 2; }
}
"""

DEVICE = (
    KERNELS
    + """
fn main() -> i32 {
  let n:usize = 20011;
  let none:usize = 0;
  buffer hx:u32[n] = zeroed;
  for i in 0..n { hx[i] = u32(mul_wrap(u64(i), 2654435761) % 1000003); }
  buffer x:u32[n]@device = zeroed;
  transfer(x, hx);
  buffer dout:u64[4]@device = zeroed;
  buffer seven:u64[4] = zeroed;
  seven[3] = 7;
  transfer(dout, seven);
  stats(n, none, x, dout);
  buffer out:u64[4] = zeroed;
  transfer(out, dout);
  let mut total:u64 = 0;
  let mut most:u64 = 0;
  let mut squares:u64 = 0;
  for i in 0..n {
    total += u64(hx[i]);
    most = max(most, u64(hx[i]));
    squares = add_wrap(squares, mul_wrap(u64(hx[i]), u64(hx[i])));
  }
  if out[0] != total || out[1] != most || out[2] != squares || out[3] != 0 { return 1; }
  buffer dtotal:f32[1]@device = zeroed;
  buffer dy:f32[n]@device = zeroed;
  share(n, x, dtotal, dy);
  buffer y:f32[n] = zeroed;
  transfer(y, dy);
  let mut sevens:f32 = 0.0;
  for i in 0..n { sevens = sevens + f32(hx[i] % 7); }
  for i in 0..n { if y[i] != f32(hx[i] % 7) / sevens { return 2; } }
  buffer dp:u32[n]@device = zeroed;
  prefix(n, dp, x);
  buffer p:u32[n] = zeroed;
  transfer(p, dp);
  let mut run:u32 = 0;
  for i in 0..n {
    if p[i] != run * 2 { return 3; }
    run += hx[i] % 5;
  }
  return 0;
}
"""
)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_device_program_runs_emulated_on_host_threads_and_agrees(tmp_path, cxx):
    ran_emulated(tmp_path, compile_source(DEVICE)[0], cxx)


def test_the_emulated_device_program_races_nowhere(tmp_path):
    done = watched(tmp_path, compile_source(DEVICE)[0], "clang++", "thread", emulate=True)
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]


def test_the_device_program_compiles_for_sm_120(tmp_path):
    device_build(tmp_path, compile_source(DEVICE)[0], entry="main", timeout=900)


def test_the_device_program_agrees_on_the_device(tmp_path):
    ran_on_device(tmp_path, compile_source(DEVICE)[0])


DEVICE_OVERFLOW = """
fn main() -> i32 {
  let n:usize = 5000;
  buffer hx:u32[n] = zeroed;
  for i in 0..n { hx[i] = 1000000; }
  buffer x:u32[n]@device = zeroed;
  transfer(x, hx);
  buffer out:u32[1]@device = zeroed;
  reduce + out[0] for i in n yield x[i];              // five billion does not fit in u32: the lane that stores traps
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_checked_total_that_overflows_traps_in_a_lane_emulated(tmp_path, cxx):
    done = contract(tmp_path, compile_source(DEVICE_OVERFLOW)[0], cxx, emulate=True)
    assert done.returncode == -signal.SIGABRT, (done.returncode, done.stderr[-2000:])


def test_a_checked_total_that_overflows_traps_on_the_device(tmp_path):
    """A deliberate device trap: `make gpu` runs it only with CAIRN_GPU_TRAPS=1."""
    cpp = compile_source(DEVICE_OVERFLOW)[0]
    with on_device(trap=True):
        done = contract(tmp_path, cpp, "g++", cuda=True)
    assert done.returncode == -signal.SIGABRT, (done.returncode, done.stderr[-2000:])


LIBRARY = """
pub fn normalize(n:usize, x:rw<f32>[n]@device, total:rw<f32>[1]@device) {
  reduce + total[0] for i in n yield x[i];
  parallel i in n { x[i] = x[i] / total[0]; }
}
pub fn normalize_on_host(n:usize, x:rw<f32>[n]@device) {
  let t = reduce + for i in n yield x[i];
  parallel i in n { x[i] = x[i] / t; }
}
"""

CALLER = r"""
#include <cstdio>
#include <vector>
#include "gpu_host.hpp"
#include "lib.h"
int main() {
  const std::size_t n = 4099;
  int wrong = 0;
  cr::gpu::Buffer<float> x(n), total(1);
  auto fill = [&] { for(std::size_t i = 0; i < n; ++i) x.data()[i] = float(i % 13); };
  const auto& c = cr::gpu::counted;
  for(int on_host = 0; on_host < 2; ++on_host) {
    std::size_t waits = 0, copies = 0, launches = 0, calls = 0;
    for(int k = 0; k < 3; ++k) {  // the first call grows the arena; the last is counted
      fill();
      const std::size_t w = c.stream_waits, cp = c.copies, l = c.launches, lc = c.library_calls;
      if(on_host) cf_normalize_on_host(n, x.data());
      else cf_normalize(n, x.data(), total.data());
      waits = c.stream_waits - w, copies = c.copies - cp, launches = c.launches - l, calls = c.library_calls - lc;
      float sum = 0;
      for(std::size_t i = 0; i < n; ++i) sum += float(i % 13);
      for(std::size_t i = 0; i < n; ++i) wrong += x.data()[i] != float(i % 13) / sum;
      wrong += !on_host && total.data()[0] != sum;
      wrong += cr::gpu::unwaited() != 0;
    }
    std::printf("{\"on_host\": %d, \"waits\": %zu, \"copies\": %zu, \"launches\": %zu, \"library_calls\": %zu}\n",
                on_host, waits, copies, launches, calls);
  }
  std::printf("{\"wrong\": %d, \"early\": %zu}\n", wrong, c.early.load());
  return wrong;
}
"""


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_a_total_that_stays_on_the_device_is_neither_copied_nor_waited_for(tmp_path, cxx):
    """On the counting host machine: the total the next region reads stays in device memory, so a call makes no copy
    and one wait, when it returns, where reading the total on the host makes a copy and waits for it and for the
    region."""
    rows = printed(hosted_library(tmp_path, LIBRARY, CALLER, cxx, *sanitized(cxx), "-pthread",
                                  stand_in="gpu_host.hpp"))  # fmt: skip
    assert rows[-1] == {"wrong": 0, "early": 0}
    stays, crosses = rows[0], rows[1]
    assert stays == {"on_host": 0, "waits": 1, "copies": 0, "launches": 2, "library_calls": 1}, stays
    assert crosses == {"on_host": 1, "waits": 2, "copies": 1, "launches": 1, "library_calls": 1}, crosses


def test_rows_headers_and_the_projection():
    """The form's row is a device reduction's, with the element written and nothing crossing; it runs held beside the
    region that reads it, and has no enqueued entry, since its scratch may grow."""
    program, checker, _ = compile_program(KERNELS + LIBRARY)
    receipt = compile_source(KERNELS + LIBRARY)[1]
    rows = {name: set(receipt["functions"][name]["effects"]) for name in ("stats", "share", "prefix", "normalize")}
    assert {"par:device", "gpu_alloc", "gpu_free", "write:out", "trap"} <= rows["stats"]
    assert not any(e.startswith("transfer:") for row in rows.values() for e in row)
    held = {f.name for f in program.functions if execution.held(checker, f)}
    assert held == {"stats", "share", "prefix", "normalize"}
    declared = header(LIBRARY, "lib", device=True)[0]
    assert "cq_normalize" not in declared and "normalize: it allocates device memory" in declared
    cpp = compile_source(KERNELS)[0]
    assert cpp.count("cr::gpu::reduce_into<") == 5 and "cr::gpu::scan_into<true, " in cpp
    assert "reduce_on" not in cpp and "scan_on" not in cpp
    for source in (KERNELS, HOST):
        canonical = round_trips(source)
        assert "reduce + out[" in canonical and "reduce max out[1]" in canonical

"""`plan f { grain G; lanes L; }` sets how f's host regions are claimed, apart from the code that says what they do.

A region's lanes are race free and finish before the next statement, so every split of its indices across threads
is one the region already allows: a plan changes the time a region takes, never its result or its effect row. The
checker refuses a plan that names nothing it can schedule.
"""

import os
import shutil

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from emitted import contract, refused, watched

SCALE = "fn scale(n:usize, out:rw<u64>[n], x:ro<u64>[n]) { parallel i in n { out[i] = x[i] * 3; } }\n"
DEVICE = "fn dev(n:usize, out:rw<f32>[n]@device) { parallel i in n { out[i] = 1.0; } }\n"
LOOP = "fn walk(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = 1; } }\n"


@pytest.mark.parametrize(
    ("code", "source"),
    [
        ("E-PLAN", SCALE + "plan nothing { grain 4; }"),
        ("E-PLAN", LOOP + "plan walk { grain 4; }"),  # nothing to schedule
        ("E-PLAN", DEVICE + "plan dev { lanes 2; }"),  # a device region is scheduled by the device
        ("E-PLAN", SCALE + "plan scale { grain 4; }\nplan scale { lanes 2; }"),
        ("E-PLAN", SCALE + "plan scale { tile 4; }"),
        ("E-PLAN", SCALE + "plan scale { grain 4; grain 8; }"),
        ("E-PLAN", SCALE + "plan scale { grain 0; }"),
        ("E-PLAN", SCALE + "plan scale { lanes 0; }"),
        ("E-PLAN", SCALE + "plan scale { lanes 2000; }"),
        ("E-STATIC", SCALE + "plan scale { grain n; }"),
    ],
)
def test_rejections(code, source):
    refused(code, source)


def test_a_plan_changes_how_a_region_is_claimed_and_nothing_the_checker_says():
    planned, receipt = compile_source(SCALE + "plan scale { grain 256; lanes 3; }")
    plain = compile_source(SCALE)[1]["functions"]["scale"]
    assert receipt["functions"]["scale"]["plan"] == {"grain": 256, "lanes": 3}
    assert {k: v for k, v in receipt["functions"]["scale"].items() if k != "plan"} == plain
    assert "}, 1, 256, 3);" in planned
    assert "}, 1, 0, 2);" in compile_source(SCALE + "plan scale { lanes 2; }")[0]


def test_a_plan_is_its_own_item_in_the_canonical_projection():
    source = SCALE + "plan scale { lanes 3; }\n"
    canonical = canonical_source(source)
    assert "plan scale { lanes 3; }" in canonical
    assert compile_source(canonical)[0] == compile_source(source)[0] and canonical_source(canonical) == canonical


# Sixty-four heavy lanes: the pool would leave a region this short to one thread, and a plan spreads it.
HEAVY = """
fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..20000 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
fn once(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = mix(u64(i)); } }
PLAN
fn main() -> i32 {
  let n:usize = 64;
  buffer a:u64[n] = zeroed;
  buffer b:u64[n] = zeroed;
  spread(n, a);
  once(n, b);
  for i in 0..n { if a[i] != b[i] { return 1; } }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize(
    "plan", ["", "plan spread { grain 1; }", "plan spread { grain 5; lanes 3; }", "plan spread { lanes 1; }"]
)
def test_every_plan_computes_what_the_loop_computes(tmp_path, cxx, plan):
    done = contract(
        tmp_path, compile_source(HEAVY.replace("PLAN", plan))[0], cxx, env={**os.environ, "CAIRN_LANES": "8"}
    )
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_a_planned_region_is_clean_under_thread_sanitizer(tmp_path):
    done = watched(
        tmp_path, compile_source(HEAVY.replace("PLAN", "plan spread { grain 1; lanes 4; }"))[0], "clang++", "thread"
    )
    assert done.returncode == 0, done.stderr[-4000:]
    assert "WARNING: ThreadSanitizer" not in done.stderr

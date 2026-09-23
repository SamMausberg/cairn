"""A region's name survives every edit that does not touch the region, and a plan names the regions it changes."""

import pytest

from cairn.perf.regions import applied, identified

TWO = """fn mix(v:u64) -> u64 { return mul_wrap(v, 3); }
fn pipe(n:usize, out:rw<f64>[n], x:ro<f64>[n], a:f64) {
  buffer t:f64[n] = zeroed;
  parallel i in n { t[i] = a * x[i]; }
  parallel j in n { out[j] = t[j] + 1.0; }
}
"""
BLUR = """fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 1 && i + 1 < n { out[i] = x[i - 1] + x[i] * 2.0 + x[i + 1]; }
    else { out[i] = x[i]; }
  }
}
"""


def ids(source, function="pipe"):
    return [r["id"] for r in identified(source, function)]


def test_a_region_keeps_its_name_through_edits_that_do_not_touch_it():
    before = ids(TWO)
    assert len(before) == 2 and all(i.startswith("pipe@") for i in before)
    assert [r["line"] for r in identified(TWO, "pipe")] == [4, 5]
    unrelated = [
        "// a new first line\n\n" + TWO,  # every line moves
        TWO.replace("mul_wrap(v, 3)", "mul_wrap(v, 5)"),  # another function
        TWO.replace("  buffer t:f64[n] = zeroed;", "  // scratch\n  buffer t:f64[n] = zeroed;"),  # a comment
        TWO.replace("{ t[i] = a * x[i]; }", "{\n    t[i] = a * x[i];\n  }"),  # the region's own layout
        TWO + "plan pipe { fuse 2; }\n",  # a plan changes how it runs, not which region it is
    ]
    for source in unrelated:
        assert ids(source) == before
    touched = ids(TWO.replace("t[i] = a * x[i];", "t[i] = a * x[i] + 1.0;"))
    assert touched[0] != before[0] and touched[1] == before[1]


def test_two_regions_written_alike_are_told_apart_by_order():
    same = TWO.replace("parallel j in n { out[j] = t[j] + 1.0; }", "parallel i in n { t[i] = a * x[i]; }")
    first, second = ids(same)
    assert second == first + ".2"


def test_a_plan_names_the_regions_it_changes():
    fused = applied(TWO + "plan pipe { fuse 2; }\n", "pipe")
    head, tail = ids(TWO)
    assert fused[head]["fuse"] == fused[tail]["fuse"] == [head, tail]
    (region,) = ids(BLUR, "blur")
    assert applied(BLUR + "plan blur { stage 1; block 128; }\n", "blur")[region] == {
        "block": 128,
        "stage": {"radius": 1, "arrays": ["x"]},
    }
    assert applied(BLUR, "blur")[region] == {}
    with pytest.raises(ValueError):
        identified(BLUR, "nothing")

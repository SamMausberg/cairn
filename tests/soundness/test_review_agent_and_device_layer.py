"""The adversarial review of what landed after 1.0.0: each defect it found pinned by the program or request that showed
it, and the attacks that were correctly refused or held, kept so that a later change cannot reopen them.

`evidence/v1_1/review/README.md` has the write-up.
"""

import shutil

import pytest

from cairn.compiler.cairnc import Diagnostic, compile_program, compile_source
from cairn.projects.target import parse
from cairn.verify.validation.validation import validate
from emitted import sanitizers

# --- Fixed: a check that reports every refusal judged a row an implementation had not yet joined -------------------

SCALE = """fn scale(n:usize, xs:rw<u64>[n]) effects(pure, write:xs, par:host) {
  for i in 0..n { xs[i] = xs[i] * 2; }
}

fn scale_lanes(n:usize, xs:rw<u64>[n]) implements scale {
  parallel i in n { xs[i] = xs[i] * 2; }
}

plan scale use scale_lanes;

fn g(n:usize, xs:rw<u64>[n]) effects(pure, write:xs) {
  scale(n, xs);
}
"""
BROKEN = """
fn broken() -> u64 {
  return missing;
}
"""


def every(source: str) -> dict:
    with pytest.raises(Diagnostic) as error:
        compile_program(source, every=True)
    assert error.value.abandoned is None
    return error.value.data


def test_a_caller_of_a_reference_is_not_judged_on_a_row_its_implementations_have_not_joined():
    """A reference's row joins its implementations' only once the implementation rules have run, and a check with a
    refusal never runs them. `g` exceeds its ceiling through `scale_lanes`, which a clean check refuses; beside an
    unrelated refusal, `g` was reported neither as refused nor as not judged, so the record implied a verdict it did
    not reach."""
    with pytest.raises(Diagnostic) as alone:
        compile_program(SCALE)
    assert alone.value.data["code"] == "E-EFFECT-CEILING" and alone.value.data["line"] == 11
    record = every(SCALE + BROKEN)
    assert record["code"] == "E-UNBOUND"
    reported = [(d["code"], d["line"]) for d in record.get("further", [])]
    assert ("E-EFFECT-CEILING", 11) in reported or record.get("not_judged", 0) >= 2, record


# --- Fixed: the fix for a type mismatch said a float conversion traps, which it never does --------------------------


@pytest.mark.parametrize(
    ("want", "got"), [("f32", "u64"), ("f32", "f64"), ("f64", "u64"), ("u32", "u64"), ("u8", "f32")]
)
def test_the_conversion_a_type_mismatch_suggests_is_stated_as_it_behaves(want, got):
    """`f32(x)` rounds, to infinity past the range, and `f64(x)` of a u64 past 2^53 rounds too; only an integer
    target is range checked (docs/language.md). The fix said every conversion traps outside its target's range, which
    told an agent a rounding conversion was checked."""
    from cairn.agent.diagnostics import fix

    hint = fix({"code": "E-TYPE-MISMATCH", "message": "", "expected_type": want, "actual_type": got})
    assert hint is not None and f"{want}(x)" in hint
    if want in {"f32", "f64"}:
        assert "trap" not in hint and "round" in hint, hint
    else:
        assert "traps outside" in hint, hint


# --- Fixed: the parallel card left out what keeps a function from its one wait and its cq_NAME entry ---------------


def test_the_parallel_card_names_everything_that_keeps_a_function_waiting():
    """compiler/lower/execution.py waits after each operation when a row holds an atomic, a lock, a call through a function
    value, a machine register or host assembly, and gives no cq_NAME entry. The card listed only transfers, device
    allocation, tickets, I/O, foreign calls and @unified views, so an agent could expect an enqueued entry for a
    function that takes a lock."""
    from cairn.agent.teaching import CARDS

    said = next(p for p in CARDS["parallel"].split("\n") if "cq_NAME" in p)
    for construct in ("atomic", "lock", "function value", "machine register", "host assembly", "@unified"):
        assert construct in said, construct


# --- Fixed: a validation asked to emulate a device said it had, for a program with no device code -----------------

TOTAL = """fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for i in 0..n { sum = add_wrap(sum, xs[i]); }
  return sum;
}

fn total_by2(n:usize, xs:ro<u64>[n]) -> u64 implements total when n % 2 == 0 {
  let mut a:u64 = 0;
  for k in 0..n / 2 { a = add_wrap(a, add_wrap(xs[2 * k], xs[2 * k + 1])); }
  return a;
}
"""


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_a_host_validation_asked_to_emulate_says_it_ran_on_the_host():
    """`cairn validate --emulate` of a program with no device code runs it as any host validation, and its evidence
    is finite-tested; its record still named the device target as emulated, so one record said both."""
    record = validate(TOTAL, "total", "total_by2", {"budget": 8}, emulate=parse("sm_120"))
    assert record["status"] == "passed" and record["evidence"] == "finite-tested" and "emulation" not in record
    assert record["target"] == {"kind": "host"}, record["target"]


# --- Fixed: an implementation's host atomic update let its reference's callers skip their waits --------------------

COUNTED = """fn bump(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, hits:ro<Atomic[u64]>)
    effects(pure, read:x, write:out, read:hits, par:device, atomic) {
  parallel i in n { out[i] = 2.0 * x[i]; }
}
fn bump_counted(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, hits:ro<Atomic[u64]>) implements bump {
  parallel i in n { out[i] = 2.0 * x[i]; }
  let _ = hits.fetch_add(1, Order.relaxed);
}
plan bump use bump_counted;
fn twice(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, hits:ro<Atomic[u64]>) {
  bump(n, out, x, hits);
  bump(n, out, x, hits);
}
"""


@pytest.mark.parametrize("name", ["bump", "twice"])
def test_a_host_atomic_update_in_a_selected_implementation_keeps_every_wait(name):
    """A function's row holds `atomic` from the implementation its reference runs, and an update of host memory is
    something another host thread sees. The rule that lets a device lane's atomic update wait once walked only the
    bodies a call names, never the implementation a plan runs in the reference's place, so `twice` was held to one
    wait while each `bump_counted` bumped `hits` before its region had run."""
    from cairn.compiler.lower import execution

    _, checker, _ = compile_program(COUNTED)
    assert "atomic" in checker.rows[name]
    assert execution.unwaited(checker, checker.fs[name]) == execution.OBSERVES["atomic"]
    assert not execution.held(checker, checker.fs[name])


# --- Fixed: a prediction priced a program on a card whose target its build refuses ----------------------------------

BULK = """fn scale(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) { parallel i in n { out[i] = a * x[i]; } }
fn scale_bulk(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) implements scale needs(tma) {
  parallel i in n { out[i] = a * x[i]; }
}
plan scale use scale_bulk;
"""


def test_a_prediction_for_a_target_the_build_refuses_is_refused_as_the_build_refuses_it():
    """`plan scale use scale_bulk;` needs tma, which sm_80 lacks, so a build for sm_80 is E-IMPL-TARGET. `cairn predict
    --card a100` and `--card all` still priced it on the A100 for sm_80, a time for code that cannot exist."""
    from cairn.perf.profile import card, carrying, default
    from cairn.perf.report import across, report

    with pytest.raises(Diagnostic) as refused:
        report(BULK, profile=carrying(default(), card("a100")), device=parse("sm_80"))
    assert refused.value.data["code"] == "E-IMPL-TARGET"
    cards = {c["card"]: c for c in across(BULK)["cards"]}
    assert cards["a100-sxm4-80gb"]["refused"]["code"] == "E-IMPL-TARGET"
    assert "refused" not in cards["h100-sxm5"] and cards["h100-sxm5"]["device_target"] == "sm_90a"


# --- Fixed: a function value that may be a function without a verdict ended the check on a KeyError ----------------

VALUE = """fn bad() -> u64 { return missing; }
fn h(n:u64) -> u64 = bad();
fn apply(k:fn(u64) -> u64, n:u64) -> u64 = k(n);
fn main() -> i32 { let x = apply(h, 1); return 0; }
"""
REFERENCE_VALUE = """fn apply(k:fn(u64) -> u64, n:u64) -> u64 = k(n);
fn same(n:u64) -> u64 = n;
fn f(n:u64) -> u64 = apply(same, n);
fn g(n:u64) -> u64 implements f = apply(f, n);
plan f use g;
"""


@pytest.mark.parametrize("source", [VALUE, REFERENCE_VALUE + BROKEN], ids=["refused-callee", "reference"])
def test_a_call_through_a_function_value_that_may_reach_an_unjudged_function_is_not_judged(source):
    """`h` is checked, but it calls a refused body, so its row is unknown and it gets no verdict. `apply` calls a
    function value that may be `h`, and the ceilings were computed for it anyway: the fixed point read `h`'s row,
    which it had left out, and the check ended on a KeyError, so every later refusal was lost and the fault hidden."""
    record = every(source)
    assert record.get("not_judged", 0) >= 2, record


# --- Fixed: cairn tune chose an implementation on a validation made under any policy --------------------------------

ZERO = """fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for i in 0..n { sum = add_wrap(sum, xs[i]); }
  return sum;
}

fn total_zero(n:usize, xs:ro<u64>[n]) -> u64 implements total {
  return 0;
}
"""


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_tune_does_not_choose_an_implementation_validated_on_one_point_of_its_domain(tmp_path):
    """`total_zero` returns 0 for every input. Validated with the domain narrowed to n = 0, it passed, Z3 called it
    equivalent there, and the history kept it as finite-tested; `cairn tune` then chose `plan total use total_zero;`
    for n = 1e6, and `--write` would write it. The history's contract digests the domain and the tolerance, but the
    search cited a validation under any contract; now it chooses only on one at least as strict as the reference's
    policy, and the row says which policy the narrow one used."""
    from cairn.perf.tuning.tune import tune
    from cairn.projects.project import load_project
    from cairn.verify.validation.validation import validate_project

    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cairn").write_text(ZERO)
    (tmp_path / "cairn.toml").write_text('[project]\nname = "weak"\nsources = ["src/main.cairn"]\n')
    history = tmp_path / "history"
    narrow = {"domain": {"extents": {"n": [0, 0]}}, "budget": 4}
    record = validate_project(load_project(tmp_path), "total_zero", narrow, history=history)
    assert record["status"] == "passed"
    answer = tune(ZERO, "total", [{"n": 1e6}], history=history)
    assert answer["chosen"].get("use") != "total_zero", answer["chosen"]
    [row] = [r for r in answer["candidates"] if r.get("use") == "total_zero"]
    assert row["validated"].startswith("validated only under a weaker policy than the reference's: it ran 4 generated")
    assert '"extents": {"n": [0, 0]}' in row["validated"]


# --- Fixed: a shared table every thread wrote before a barrier was refused where a data index read it --------------

LOOKUP = """fn lookup(g:usize, n:usize, table:ro<u32>[32], x:ro<u32>[n], out:rw<u32>[n]) {
  blocks b in g threads t in 32 {
    shared lut:u32[32];
    lut[t] = table[t];
    barrier;
    let i = b * 32 + t;
    if i < n { out[i] = lut[usize(x[i] % 32)]; }
  }
}

fn check(n:usize) -> i32 {
  buffer table:u32[32] = zeroed;
  for j in 0..32 { table[j] = u32(j) * 7 + 3; }
  buffer x:u32[n] = zeroed;
  for j in 0..n { x[j] = u32(j) * 13; }
  buffer out:u32[n] = zeroed;
  lookup((n + 31) / 32, n, table, x, out);
  for j in 0..n { if out[j] != table[usize(x[j] % 32)] { return 1; } }
  return 0;
}

fn main() -> i32 { return check(100); }
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_shared_table_written_whole_before_a_barrier_may_be_read_at_any_index(tmp_path, cxx):
    """Every element of `lut` is written before the barrier, so whichever element a data index names was written
    first, and an index past the end stops at its guard. The rule refused any index it could not follow, even there,
    which pushed a lookup table to `= zeroed` and a fill it does not need. It runs on host threads, where an unzeroed
    array starts each block filled with a pattern, and gives the table's values under both compilers."""
    from emitted import contract

    cpp = compile_source(LOOKUP)[0]
    flags = sanitizers(cxx)
    done = contract(tmp_path, cpp, cxx, *flags)
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


def test_a_table_written_in_part_is_still_refused_at_an_index_it_cannot_follow():
    from emitted import refused

    refused("E-COOP-UNWRITTEN", LOOKUP.replace("lut[t] = table[t];", "if t < 31 { lut[t] = table[t]; }"))


def test_the_lookup_table_s_threads_race_nowhere(tmp_path):
    from emitted import watched

    done = watched(tmp_path, compile_source(LOOKUP)[0], "clang++", "thread")
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]

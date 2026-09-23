"""cairn diff: one evidence class per function, the exact deltas beside it, and the semantic version it implies.

Each class is pinned by a pair of programs whose answer is known without the tool: a spelling the emitter writes the
same way, a rewrite Z3 can decide, a change with an input that shows it, a loop no bound covers. Witnesses are replayed
natively under every compiler present, and git revisions are read from a scratch repository without a checkout.
"""

import json
import shutil
import subprocess

import pytest

from cairn.cli import main
from cairn.verify.diff import diff, holds, single
from cairn.verify.testing import evaluate

COMPILERS = [c for c in ("clang++", "g++") if shutil.which(c)]

SUGAR = {
    "bare variants": (
        "import std.core (Option);\nfn f(x:u64) -> Option[u64] { if x == 0 { return Option.None; } return Option.Some(x); }\n",
        "import std.core (Option);\nfn f(x:u64) -> Option[u64] { if x == 0 { return None; } return Some(x); }\n",
    ),
    "one-statement arms": (
        "import std.core (Option);\n"
        "fn f(o:Option[u64]) -> u64 { match o { Option.Some(v) => { return v; } Option.None => { return 0; } } }\n",
        "import std.core (Option);\nfn f(o:Option[u64]) -> u64 { match o { Some(v) => return v; None => return 0; } }\n",
    ),
    "compound assignment": (
        "fn f(x:u64) -> u64 { let mut t:u64 = x; t = t + 3; t = t * 2; return t; }\n",
        "fn f(x:u64) -> u64 { let mut t:u64 = x; t += 3; t *= 2; return t; }\n",
    ),
    "renamed locals and parameters": (
        "fn f(x:u64) -> u64 { let doubled = x * 2; return doubled + 1; }\n",
        "fn f(y:u64) -> u64 { let twice = y * 2; return twice + 1; }\n",
    ),
}


@pytest.mark.parametrize("form", sorted(SUGAR))
def test_a_short_form_is_identical_code(form):
    old, new = SUGAR[form]
    assert single(old, new, "f") == {"class": "identical-code"}
    record = diff(old, new, predict=False)
    assert record["summary"] == {"identical-code": 1} and record["semver"]["level"] == "none"
    assert holds(record, "identical")


def test_a_call_statement_and_an_element_update_are_proven_not_identical():
    """A dropped binding and `xs[i] += v` emit other C++ (no local, one bounds guard), so Z3 decides them."""
    dropped = single("fn g(x:u64) -> u64 = x + 1;\nfn f(x:u64) -> u64 { let unused = g(x); return x; }\n",
                     "fn g(x:u64) -> u64 = x + 1;\nfn f(x:u64) -> u64 { g(x); return x; }\n", "f")  # fmt: skip
    assert dropped["class"] == "smt-equivalent" and "quantification" in dropped["evidence"]
    at = "fn f(n:usize, k:usize, xs:rw<u64>[n]) { xs[k] = xs[k] + 1; }\n"
    record = diff(at, at.replace("xs[k] = xs[k] + 1;", "xs[k] += 1;"), predict=False)
    entry = record["functions"]["f"]
    assert entry["class"] == "smt-equivalent" and entry["own_code"] == "changed"
    assert entry["deltas"]["emitted_guards"] == {"before": 4, "after": 3}  # the place is evaluated, and guarded, once
    assert holds(record, "equivalent") and not holds(record, "identical")


BUMP = ("fn bump(x:u32) -> u32 = x + 1;\nfn twice(x:u32) -> u32 { let y = bump(x); return y * 2; }\n",
        "fn bump(x:u32) -> u32 = x + 2;\nfn twice(x:u32) -> u32 { let y = bump(x); return y * 2; }\n")  # fmt: skip


def test_a_behaviour_change_has_a_witness_every_compiler_replays():
    record = diff(*BUMP, predict=False)
    bump, twice = record["functions"]["bump"], record["functions"]["twice"]
    assert bump["class"] == twice["class"] == "behavior-changed"
    w = bump["witness"]
    x = w["inputs"]["x"]
    before, after = w["before"], w["after"]
    # An independent reading of the two bodies: u32 addition that traps past the range.
    assert before == ({"defined": True, "return": x + 1, "written": {}} if x + 1 < 2**32 else {"defined": False,
                      "trap": before.get("trap")})  # fmt: skip
    assert after == ({"defined": True, "return": x + 2, "written": {}} if x + 2 < 2**32 else {"defined": False,
                     "trap": after.get("trap")})  # fmt: skip
    assert w["native"] == {cxx: {"before": "agrees", "after": "agrees"} for cxx in COMPILERS}
    assert twice["own_code"] == "identical"  # its own code did not change; what it calls did
    assert not holds(record, "equivalent") and record["semver"]["level"] == "major"


@pytest.mark.skipif(not COMPILERS, reason="no native compiler")
def test_a_witness_that_aborts_is_replayed_as_an_abort():
    old = "fn half(x:u32) -> u32 = x / 2;\n"
    new = "fn half(x:u32) -> u32 = 100 / x;\n"  # divides by zero at x = 0, where the old version returns 0
    entry = diff(old, new, predict=False)["functions"]["half"]
    assert entry["class"] == "behavior-changed"
    assert {s: entry["witness"]["native"][c][s] for c in COMPILERS for s in ("before", "after")} == {
        "before": "agrees", "after": "agrees"}  # fmt: skip


LOOP = "fn spin(n:u64) -> u64 { let mut t:u64 = 0; let mut i:u64 = 0; while i < n { t += i; i += 1; } return t; }\n"


def test_an_unbounded_loop_is_unknown_and_never_counted_as_unchanged():
    record = diff(LOOP, LOOP.replace("return t;", "return t + 0;"), predict=False)
    entry = record["functions"]["spin"]
    assert entry["class"] == "unknown" and "unrolling budget" in entry["reason"]
    assert entry["bounded"] == {"where": "n <= 16", "status": "smt-equivalent"}  # evidence, and only inside the bound
    assert record["semver"] == {"level": "unknown", "at_least": "patch", "reasons": [], "unproven": ["spin"]}
    assert not holds(record, "equivalent")


def test_a_difference_inside_the_bound_is_a_behaviour_change():
    changed = LOOP.replace("return t;", "if n == 5 { return 0; } return t;")
    entry = diff(LOOP, changed, predict=False)["functions"]["spin"]
    assert entry["class"] == "behavior-changed" and entry["witness"]["found_where"] == "n <= 16"
    assert entry["witness"]["inputs"] == {"n": 5} and entry["witness"]["before"]["return"] == 10


SHAPES = (
    "fn gone(x:u8) -> u8 = x;\nfn old_name(x:u64) -> u64 = x ^ 7;\nfn wide(x:u32) -> u32 = x;\n",
    "fn new_name(x:u64) -> u64 = x ^ 7;\nfn wide(x:u64) -> u32 = u32(x);\nfn fresh() -> u8 = 3;\n",
)


def test_structural_changes_are_named_and_not_compared_value_for_value():
    record = diff(*SHAPES, predict=False)
    classes = {n: e["class"] for n, e in record["functions"].items()}
    assert classes == {"gone": "removed", "new_name": "renamed", "wide": "signature-changed", "fresh": "added"}
    assert record["functions"]["new_name"]["from"] == "old_name"
    wide = record["functions"]["wide"]["deltas"]
    assert wide["signature"]["after"]["params"] == [["x", "u64"]] and wide["effects"]["added"] == ["trap"]
    reasons = record["semver"]["reasons"]
    assert record["semver"]["level"] == "major"
    assert {"gone was removed", "old_name is now named new_name", "wide's signature changed", "fresh was added"} <= set(
        reasons)  # fmt: skip


LIB = "module lib;\npub struct Pair { a:u64; b:u64; }\npub fn sum(p:Pair) -> u64 = p.a + p.b;\nfn helper(x:u64) -> u64 = x;\n"


@pytest.mark.parametrize(
    "new,level,reason",
    [
        (LIB, "none", None),
        (LIB.replace("fn helper(x:u64) -> u64 = x;", "fn helper(x:u64) -> u64 = x + 0;"), "patch", None),
        (LIB + "pub fn more() -> u64 = 1;\n", "minor", "lib.more was added"),
        (LIB.replace("= p.a + p.b;", "{ let b = Buf[u64](1); return p.a + p.b; }"), "major",
         "lib.sum's effect row gained alloc, free, zero_init"),
        (LIB.replace("b:u64; }", "b:u64; c:u8; }"), "major", "type lib.Pair's definition changed"),
        (LIB.replace("pub fn sum", "fn sum"), "major", "lib.sum is no longer public"),
    ],
)  # fmt: skip
def test_the_semantic_version_reads_the_public_interface(new, level, reason):
    verdict = diff(LIB, new, predict=False)["semver"]
    assert verdict["level"] == level
    assert reason is None or reason in verdict["reasons"], verdict


def test_tests_are_compared_by_their_code():
    old = "fn one() -> u64 = 1;\ntest a { let x = one(); }\ntest b { let x = one(); }\n"
    new = "fn one() -> u64 = 1;\ntest a { let x = one(); }\ntest b { let y = one() + 1; }\ntest c { let z = one(); }\n"
    assert diff(old, new, predict=False)["tests"] == {"a": "identical-code", "b": "changed", "c": "added"}


def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   check=True, capture_output=True)  # fmt: skip


@pytest.mark.skipif(not shutil.which("git"), reason="git is absent")
def test_revisions_are_read_without_touching_the_working_tree(tmp_path, capsys, monkeypatch):
    project = tmp_path / "repo/app"
    (project / "src").mkdir(parents=True)
    (project / "cairn.toml").write_text('[project]\nname = "app"\nsources = ["src/main.cairn"]\n')
    main_file = project / "src/main.cairn"
    main_file.write_text("fn bump(x:u32) -> u32 = x + 1;\nfn main() -> i32 = 0;\n")
    git(tmp_path / "repo", "init", "-q")
    git(tmp_path / "repo", "add", ".")
    git(tmp_path / "repo", "commit", "-qm", "one")
    main_file.write_text("fn bump(x:u32) -> u32 = x + 2;\nfn main() -> i32 = 0;\n")
    git(tmp_path / "repo", "commit", "-qam", "two")
    main_file.write_text("fn bump(x:u32) -> u32 = 2 + x;\nfn main() -> i32 = 0;\n")  # uncommitted, and must stay so
    monkeypatch.chdir(tmp_path / "repo")
    assert main(["diff", "HEAD~1", "HEAD", "--in", "app", "--format", "json", "--no-predict"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["functions"]["bump"]["class"] == "behavior-changed"
    assert record["old"]["kind"] == "revision" and len(record["old"]["commit"]) == 40
    assert main(["diff", "HEAD", "app", "--in", "app", "--format", "json", "--no-predict", "--require",
                 "equivalent"]) == 0  # fmt: skip
    assert json.loads(capsys.readouterr().out)["functions"]["bump"]["class"] == "smt-equivalent"
    assert main_file.read_text() == "fn bump(x:u32) -> u32 = 2 + x;\nfn main() -> i32 = 0;\n"
    status = subprocess.run(["git", "-C", str(tmp_path / "repo"), "status", "--porcelain"], capture_output=True,
                            text=True, check=True).stdout  # fmt: skip
    assert status == " M app/src/main.cairn\n"


def test_the_terminal_and_the_pull_request_read_the_same_record(tmp_path, capsys):
    old, new = tmp_path / "old.cairn", tmp_path / "new.cairn"
    old.write_text(BUMP[0])
    new.write_text(BUMP[1])
    page = tmp_path / "pr.md"
    code = main(["diff", str(old), str(new), "--format", "human", "--markdown", str(page), "--no-predict",
                 "--require", "equivalent"])  # fmt: skip
    assert code == 1  # a behaviour change fails the gate
    said = capsys.readouterr().out.splitlines()
    assert said[0].endswith("2 behavior-changed") and said[1].lstrip().startswith("behavior-changed  bump")
    text = page.read_text()
    assert "| `bump` | behavior-changed |" in text and "Semantic version: **major**" in text


def test_a_refused_version_is_named(tmp_path, capsys):
    old, new = tmp_path / "old.cairn", tmp_path / "new.cairn"
    old.write_text("fn f(x:u32) -> u32 = x;\n")
    new.write_text("fn f(x:u32) -> u32 = y;\n")
    assert main(["diff", str(old), str(new), "--format", "json"]) == 1
    refused = json.loads(capsys.readouterr().out)
    assert refused["code"] == "E-UNBOUND" and refused["version"] == str(new) and refused["side"] == "new"


def test_the_library_compared_with_itself_is_identical_code(capsys):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert main(["diff", str(root), str(root), "--std", "--format", "json", "--no-predict", "--require",
                 "identical"]) == 0  # fmt: skip
    record = json.loads(capsys.readouterr().out)
    assert (
        set(record["summary"]) == {"identical-code", "identical-source"} and record["summary"]["identical-code"] > 100
    )


@pytest.mark.skipif(not COMPILERS, reason="no native compiler")
def test_the_task_runner_calls_a_module_function_by_its_c_symbol():
    source = "module m;\npub fn f(x:u32) -> u32 = x + 1;\n"
    result = evaluate(source, {"schema": "cairn.task/1", "symbol": "m.f", "cases": [{"args": {"x": 4}, "return": 5}]},
                      COMPILERS[0])  # fmt: skip
    assert result["status"] == "passed-finite-tests"


def test_a_device_program_is_never_run_to_replay_a_witness():
    device = "fn scale(n:usize, out:rw<f32>[n]@device) { parallel i in n { out[i] = 2.0; } }\n"
    old, new = device + "fn bump(x:u32) -> u32 = x + 1;\n", device + "fn bump(x:u32) -> u32 = x + 2;\n"
    entry = diff(old, new, predict=False)["functions"]["bump"]
    assert entry["class"] == "behavior-changed"
    assert {s for sides in entry["witness"]["native"].values() for s in sides.values()} <= {
        "not replayed: a program with device code is compiled here, never run"}  # fmt: skip


PICK = "fn pick[T:copy](x:T, y:T, first:bool) -> T { if first { return x; } return y; }\n"


def test_a_template_no_code_instantiates_is_compared_by_its_tokens_and_never_left_out():
    same = diff(PICK + "fn one() -> u64 = 1;\n", PICK + "fn one() -> u64 = 1;\n", predict=False)
    assert same["functions"]["pick"] == {"class": "identical-source", "template": True}
    assert same["semver"]["level"] == "none" and holds(same, "identical")
    rewritten = PICK.replace("if first { return x; } return y;", "if !first { return y; } return x;")
    changed = diff(PICK, rewritten, predict=False)
    assert (
        changed["functions"]["pick"]["class"] == "unknown"
        and "tokens changed" in changed["functions"]["pick"]["reason"]
    )
    assert changed["semver"]["unproven"] == ["pick"] and changed["semver"]["level"] == "unknown"
    assert not holds(changed, "equivalent")
    used = "fn use() -> u64 = pick(1, 2, true);\n"  # an instance has code, and is compared as a function
    instanced = diff(PICK + used, rewritten + used, predict=False)["functions"]
    assert instanced["pick[u64]"]["class"] == "smt-equivalent" and instanced["use"]["class"] == "smt-equivalent"


def test_a_template_that_names_a_changed_function_is_not_identical_source():
    helper = "fn base(x:u64) -> u64 = x;\nfn wrap[T:copy](x:T) -> T { let b = base(1); return x; }\n"
    entry = diff(helper, helper.replace("= x;", "= x + 1;"), predict=False)["functions"]["wrap"]
    assert entry["class"] == "unknown" and "base" in entry["reason"]


def test_a_program_past_the_value_model_s_limit_is_compared_through_the_modules_a_function_reaches():
    filler = "module pad;\n" + "".join(f"pub fn p{i}(x:u64) -> u64 = x + {i};\n" for i in range(2000))
    small = "module m;\npub fn f(x:u32) -> u32 = x + 1;\n"
    old, new = filler + small, filler + small.replace("x + 1", "1 + x")
    assert len(old.encode()) > 64000
    entry = diff(old, new, predict=False)["functions"]["m.f"]
    assert entry["class"] == "smt-equivalent"


def test_an_unproven_public_function_makes_the_level_unknown_unless_it_is_already_major(tmp_path, capsys):
    spun = LOOP.replace("return t;", "return t + 0;")
    minor = diff(LOOP, spun + "fn more() -> u64 = 1;\n", predict=False)["semver"]
    assert minor["level"] == "unknown" and minor["at_least"] == "minor" and minor["unproven"] == ["spin"]
    major = diff(LOOP + "fn gone() -> u64 = 1;\n", spun, predict=False)["semver"]
    assert major["level"] == "major" and "at_least" not in major and major["unproven"] == ["spin"]
    old, new, page = tmp_path / "old.cairn", tmp_path / "new.cairn", tmp_path / "pr.md"
    old.write_text(LOOP)
    new.write_text(spun)
    assert main(["diff", str(old), str(new), "--format", "human", "--no-predict", "--markdown", str(page)]) == 0
    said = capsys.readouterr().out.splitlines()
    assert said[-2:] == [
        "semver: unknown (at least patch)",
        "  unknown, because these public functions are unproven: spin",
    ]
    text = page.read_text()
    assert "Semantic version: **unknown (at least patch)**." in text
    assert "It is unknown, because these public functions are unproven: `spin`." in text


def test_a_comparison_that_runs_past_its_limit_is_stopped_and_unknown():
    import time

    from cairn.verify.diff import isolated

    def forever():
        while True:
            pass

    start = time.monotonic()
    stopped = isolated(forever, 1.0)
    assert stopped == {"class": "unknown", "reason": "The comparison ran past its 1 s limit and was stopped."}
    assert time.monotonic() - start < 10
    assert isolated(lambda: {"class": "smt-equivalent"}, 30) == {"class": "smt-equivalent"}
    failed = isolated(lambda: 1 / 0, 30)
    assert failed["class"] == "unknown" and "ZeroDivisionError" in failed["reason"]


def test_a_generic_instance_past_the_size_limit_keeps_the_module_that_instantiates_it():
    filler = "module pad;\n" + "".join(f"pub fn p{i}(x:u64) -> u64 = x + {i};\n" for i in range(2000))
    lib = "module lib;\npub fn pick[T:copy](x:T, y:T) -> T = x;\n"
    user = "module user;\nimport lib;\npub fn go(x:u32) -> u32 = lib.pick(x, 3);\n"
    old, new = (
        filler + lib + user,
        filler + lib.replace("= x;", "{ let kept = x; let unused = y; return kept; }") + user,
    )
    assert len(old.encode()) > 64000
    entry = diff(old, new, predict=False)["functions"]["lib.pick[u32]"]
    assert entry["class"] == "smt-equivalent", entry  # through the solver, on the modules that make the instance

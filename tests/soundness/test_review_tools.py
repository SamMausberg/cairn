"""The second adversarial review of what was added after 0.8.3: what it found, each fix pinned by the program that
showed it, and the attacks that were correctly refused or held, kept so that a later change cannot reopen them.

`evidence/v1_0/review/README.md` has the write-up. The first review's table is `test_review_forms.py`.
"""

import importlib.util
import json
import shutil
import subprocess
import tempfile
from fractions import Fraction

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.compiler.lower.header import binding
from cairn.editor.lsp.document import Document
from cairn.editor.lsp.workspace import rename, workspace
from cairn.perf.tuning.plan_source import write_plan
from cairn.projects.build import build
from cairn.projects.new import create_project
from cairn.projects.project import ProjectError, load_project
from cairn.projects.revision import read
from cairn.verify.diff import single
from emitted import watched
from oracles.float_formats import f32


def git(repo, *args, data=None):
    done = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True, check=True)
    return done.stdout.decode().strip()


# --- Fixed: a revision cannot write outside the directory it is read into ------------------------------------------


@pytest.mark.skipif(not shutil.which("git"), reason="the attack is a git tree")
def test_a_revision_whose_tree_names_dot_dot_is_refused_and_writes_nothing_outside(tmp_path, monkeypatch):
    """`git mktree` accepts an entry named `..`, which no checkout would write but `cairn diff` did: reading such a
    revision wrote its file beside the scratch directory, so reviewing a crafted branch could overwrite any file."""
    repo, outside = tmp_path / "repo", tmp_path / "scratch"
    repo.mkdir()
    outside.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(outside))  # the scratch directory is made inside `outside`
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "cairn.toml").write_text('[project]\nname = "p"\nsources = ["a.cairn"]\n')
    (repo / "a.cairn").write_text("fn main() -> i32 = 0;\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "clean")
    blob = git(repo, "hash-object", "-w", "--stdin", data=b"PWNED\n")
    inner = git(repo, "mktree", data=f"100644 blob {blob}\tescaped.txt\n".encode())
    listing = f"100644 blob {git(repo, 'rev-parse', 'HEAD:a.cairn')}\ta.cairn\n"
    listing += f"100644 blob {git(repo, 'rev-parse', 'HEAD:cairn.toml')}\tcairn.toml\n040000 tree {inner}\t..\n"
    top = git(repo, "mktree", data=listing.encode())
    git(repo, "update-ref", "refs/heads/evil", git(repo, "commit-tree", top, "-p", "HEAD", "-m", "evil"))
    with pytest.raises(ProjectError, match="not a path inside the tree"):
        read("evil", cwd=repo)
    assert not list(outside.glob("escaped.txt")) and not list(tmp_path.rglob("escaped.txt"))
    assert "fn main" in read("evil~1", cwd=repo).source  # an ordinary revision still reads


# --- Fixed: an enum's definition is part of the code a diff compares ----------------------------------------------


def test_a_reordered_enum_is_not_identical_code_to_a_function_that_names_it():
    """`identical-code` covers a function and every type it names. A tag-only enum is emitted as `enum class ct_E`,
    which the definition table did not read, so reordering its variants left every function naming it identical."""
    old = "enum E { A; B; }\nfn f(x:u64) -> u64 { if pick(x) == E.A { return 1; } return 2; }\n"
    old += "fn pick(x:u64) -> E { if x > 3 { return E.A; } return E.B; }\n"
    new = old.replace("enum E { A; B; }", "enum E { B; A; }")
    assert single(old, new, "f")["class"] == "smt-equivalent"  # the same answer, from code that is not the same
    assert single(old, new, "pick")["class"] == "signature-changed"  # its result's type is defined differently
    assert single(old, old, "f")["class"] == "identical-code"


# --- Fixed: cairn tune --write writes one plan, in the file that declares the function, or nothing -----------------


def two_modules(root):
    """Module x declares a sequential f and says so in a comment; module y declares the f with a region."""
    (root / "src").mkdir(parents=True)
    (root / "cairn.toml").write_text(
        '[project]\nname = "tw"\nsources = ["src/x.cairn", "src/y.cairn", "src/main.cairn"]\n'
    )
    (root / "src/x.cairn").write_text(
        "module x;\n// fn f is the sequential one.\npub fn f(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = 1; } }\n"
    )
    (root / "src/y.cairn").write_text(
        "module y;\npub fn f(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = u64(i) * 2; } }\n"
    )
    (root / "src/main.cairn").write_text(
        "import x;\nimport y;\nfn main() -> i32 { let mut b = Buf[u64](4); x.f(b); y.f(b); return 0; }\n"
    )


def test_a_tuned_plan_goes_to_the_module_that_declares_the_function_and_replaces_its_old_one(tmp_path, capsys):
    """The file was the first whose text matched `fn f`, a comment in another module included, and the plan was
    written under its qualified name while only the short one was stripped: a second write added a second plan, and
    the project was refused (E-PLAN)."""
    two_modules(tmp_path)
    before = (tmp_path / "src/x.cairn").read_text()
    for lanes in (2, 4):
        assert write_plan(tmp_path, "y.f", {"grain": 1, "lanes": lanes}) == "src/y.cairn"
    written = (tmp_path / "src/y.cairn").read_text()
    assert (tmp_path / "src/x.cairn").read_text() == before
    assert written.count("plan") == 1 and "plan f { grain 1; lanes 4; }" in written
    compile_source(load_project(tmp_path).source)  # still accepted
    assert main(["tune", str(tmp_path), "--symbol", "y.f", "--at", "n=3000", "--write", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["written"] == "src/y.cairn"
    compile_source(load_project(tmp_path).source)


def test_a_plan_the_project_would_refuse_is_not_written(tmp_path):
    two_modules(tmp_path)
    before = (tmp_path / "src/y.cairn").read_text()
    with pytest.raises(ProjectError, match="E-PLAN"):
        write_plan(tmp_path, "y.f", {"lanes": 5000})  # past the 1024 a plan may ask for
    with pytest.raises(ProjectError, match="E-PLAN"):
        write_plan(tmp_path, "x.f", {"grain": 64})  # x.f has no region to plan
    with pytest.raises(ProjectError, match="nowhere to go"):
        write_plan(tmp_path, "std.vec.push", {"grain": 64})
    assert (tmp_path / "src/y.cairn").read_text() == before


# --- Held: the reused task threads -----------------------------------------------------------------------------------

# Each program reuses the same task threads round after round: a region started from a task, a task that spawns its
# own, a group refilled with owners released on the worker, and owner results handed back. The runtime keeps no
# per-thread state, so nothing a task leaves on its thread reaches the next one; these hold that under both
# sanitizers with both compilers.
REUSED = {
    # A task that runs a host region on a reused thread, again and again, each time over a part the task was lent.
    "regions_in_tasks": """
fn fill(n:usize, out:rw<u64>[n], start:u64) { parallel i in n { out[i] = start + u64(i); } }
fn main() -> i32 {
  let n:usize = 70000;
  let m:usize = 2 * n;
  let mut data = Buf[u64](m);
  for round in 0..40 {
    let a = spawn fill(data[0..n], u64(round));
    let b = spawn fill(data[n..m], u64(round) + 1);
    wait(a);
    wait(b);
    if data[0] != u64(round) || data[n] != u64(round) + 1 { return 1; }
  }
  return 0;
}
""",
    # A task that spawns and waits a task of its own, so a reused thread holds a worker while it runs.
    "nested_spawns": """
fn leaf(x:u64) -> u64 = x * 2;
fn middle(x:u64) -> u64 {
  let t = spawn leaf(x);
  let got = wait(t);
  return got + 1;
}
fn main() -> i32 {
  let mut sum:u64 = 0;
  for k in 0..300 {
    let t = spawn middle(u64(k));
    sum += wait(t);
  }
  if sum != 300 * 299 + 300 { return 1; }
  return 0;
}
""",
    # A group refilled round after round, each task taking and releasing an owner on its own thread.
    "group_owners": """
fn keep(k:u64, round:u64) -> u64 {
  let mut b = Buf[u64](100);
  b[0] = k;
  let mut t:u64 = 0;
  for x in b { t += x; }
  return t + round;
}
fn main() -> i32 {
  for round in 0..60 {
    let g = Group[u64](8);
    for k in 0..8 { spawn keep(u64(k), u64(round)) into g; }
    let mut sum:u64 = 0;
    for k in 0..8 { sum += collect(g); }
    wait(g);
    if sum != 28 + 8 * u64(round) { return 1; }
  }
  return 0;
}
""",
    # A task whose result is an owner, returned to the spawner across a reused thread.
    "owner_results": """
fn make(n:usize, v:u64) -> Buf[u64] {
  let mut b = Buf[u64](n);
  for i in 0..n { b[i] = v; }
  return b;
}
fn main() -> i32 {
  let mut total:u64 = 0;
  for k in 0..200 {
    let t = spawn make(64, u64(k));
    let b = wait(t);
    total += b[63];
  }
  if total != 199 * 200 / 2 { return 1; }
  return 0;
}
""",
}


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("sanitizer", ["thread", "address,undefined"])
@pytest.mark.parametrize("name", sorted(REUSED))
def test_a_reused_task_thread_carries_nothing_from_one_task_to_the_next(tmp_path, name, sanitizer, cxx):
    done = watched(tmp_path, compile_source(REUSED[name])[0], cxx, sanitizer)
    assert done.returncode == 0, done.stderr[-3000:]
    assert "WARNING" not in done.stderr and "ERROR" not in done.stderr, done.stderr[-3000:]


# --- Fixed: a header and a library of different versions no longer link ---------------------------------------------

V1 = "struct Pair { a:u64; b:u32; }\npub fn first(p:Pair) -> u64 = p.a;\n"
V2 = "struct Pair { b:u32; a:u64; }\npub fn first(p:Pair) -> u64 = p.a;\n"  # the same entry, the record reordered
HOST = '#include "lib.h"\nint main(void) { ct_Pair p = {0}; p.a = 7; return cf_first(p) == 7 ? 0 : 1; }\n'


def versioned(tmp_path, name, source):
    root = tmp_path / name
    (root / "src").mkdir(parents=True)
    (root / "src/lib.cairn").write_text(source)
    (root / "cairn.toml").write_text('[project]\nname = "lib"\nsources = ["src/lib.cairn"]\n')
    record = build(load_project(root), kind="library", header=True, output=tmp_path / f"{name}-out", timeout=180)
    assert record["status"] == "native-built", record.get("stderr", "")[:2000]
    return root, record["directory"]


@pytest.mark.skipif(not shutil.which("clang"), reason="needs a C compiler")
def test_a_program_built_with_a_stale_header_does_not_link_against_the_new_library(tmp_path):
    """A C program links by name alone, so a header of one version and a library of another linked and passed a
    record the library laid out otherwise. The header now names the library's interface identity."""
    _, old = versioned(tmp_path, "v1", V1)
    _, new = versioned(tmp_path, "v2", V2)
    (tmp_path / "host.c").write_text(HOST)

    def linked(header_dir, library_dir):
        return subprocess.run(["clang", "-std=c11", f"-I{header_dir}", tmp_path / "host.c", f"-L{library_dir}", "-llib",
                               f"-Wl,-rpath,{library_dir}", "-o", tmp_path / "host"], capture_output=True, text=True)  # fmt: skip

    stale = linked(old, new)
    assert stale.returncode != 0 and "cairn_interface_lib_" in stale.stderr, stale.stderr
    assert linked(new, new).returncode == 0
    assert subprocess.run([tmp_path / "host"], timeout=30).returncode == 0


def test_a_binding_of_one_version_refuses_to_load_a_library_of_another(tmp_path):
    root, _ = versioned(tmp_path, "v1", V1)
    _, new = versioned(tmp_path, "v2", V2)
    path = tmp_path / "lib_binding.py"
    path.write_text(binding(load_project(root).source, "lib"))
    spec = importlib.util.spec_from_file_location("lib_binding", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ImportError, match="not the version of lib"):
        module.load(f"{new}/liblib.so")


# --- Fixed: a rename carries the task contracts that name the function ----------------------------------------------


def test_a_rename_rewrites_the_task_contract_that_names_the_function(tmp_path):
    """The rename checked that the program changed nothing but a name, and left `tests/average.json` naming
    `average`: every file it edited compiled, and `cairn test` then refused the contract (invalid-contract)."""
    root = tmp_path / "demo"
    create_project(root)
    math = root / "src/math.cairn"
    text = math.read_text()
    edit = rename(workspace(math.as_uri(), {}), math.as_uri(), text.index("fn average") + 3, "midpoint")["changes"]
    contract = (root / "tests/average.json").resolve().as_uri()
    assert contract in edit and len(edit) == 3  # math.cairn, main.cairn and the contract
    for uri, edits in edit.items():
        path = root / uri.split("/demo/", 1)[1]
        doc = Document(path.read_text(), analyse=False)
        out = doc.text
        for e in sorted(edits, key=lambda e: -doc.offset(e["range"]["start"])):
            out = out[: doc.offset(e["range"]["start"])] + e["newText"] + out[doc.offset(e["range"]["end"]) :]
        path.write_text(out)
    assert '"symbol": "midpoint"' in (root / "tests/average.json").read_text()
    assert main(["test", str(root), "--format", "json"]) == 0


# --- Fixed: an owner moved into a group's task is the task's, not lent ---------------------------------------------

KEEP = "fn keep(b:Buf[u64]) -> u64 = u64(len(b)) + b[0];\n"
MOVED_IN = (
    KEEP + "fn main() -> i32 {\n  let g = Group[u64](4);\n"
    "  for k in 0..4 { let mut b = Buf[u64](k + 1); b[0] = u64(k); spawn keep(b) into g; }\n"
    "  let mut sum:u64 = 0;\n  for k in 0..4 { sum += collect(g); }\n  wait(g);\n"
    "  if sum != 1 + 2 + 3 + 4 + 0 + 1 + 2 + 3 { return 1; }\n  return 0;\n}\n"
)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_loop_may_move_a_fresh_owner_into_a_group_each_time_round(tmp_path, cxx):
    """The group's rule for a loop read every place the submission touched as lent, a Buf passed by value
    included, so moving a fresh owner in each iteration was E-LEASED although nothing of it stayed with the
    spawner. Each owner is released once, on the task's thread."""
    done = watched(tmp_path, compile_source(MOVED_IN)[0], cxx, "address,undefined")
    assert done.returncode == 0, done.stderr[-3000:]


@pytest.mark.parametrize(
    "source,code",
    [
        (  # a view lent in a loop is still lent to the group until its wait
            "fn fill(n:usize, out:rw<u64>[n]) { out[0] = 1; }\nfn main() -> i32 { let mut v = Buf[u64](4);\n"
            "  let g = Group[void](4);\n  for k in 0..4 { spawn fill(v) into g; }\n  wait(g);\n  return 0;\n}\n",
            "E-LEASED",
        ),
        (  # and an owner moved in is gone from the spawner
            KEEP + "fn main() -> i32 { let g = Group[u64](1); let b = Buf[u64](1); spawn keep(b) into g;\n"
            "  let again = b[0];\n  let r = collect(g);\n  wait(g);\n  return 0;\n}\n",
            "E-MOVED",
        ),
    ],
)
def test_what_a_group_still_holds_is_still_refused(source, code):
    with pytest.raises(Diagnostic) as refused:
        compile_source(source)
    assert refused.value.data["code"] == code, refused.value.data


# --- Held: derived gradients outside their own tests, and quantization at its boundaries ---------------------------

# Each f is differentiated by `derive grad` and its gradient compared, natively, with a central difference.
GRADIENTS = {
    "nested derived calls": "fn g(y:f64) -> f64 = y * y * y;\nfn h(y:f64) -> f64 = g(y) + 3.0 * y;\n"
    "derive grad for g;\nderive grad for h;\nfn f(x:f64) -> f64 = h(g(x)) / (1.0 + x * x);",
    "returning branches": "fn f(x:f64) -> f64 { if x > 1.0 { return x * x; } if x < -1.0 { return -x; } "
    "return 2.0 * x - 1.0; }",
    "a sum a loop adds into": "fn f(x:f64) -> f64 {\n  let mut t:f64 = 0.0;\n  for k in 0..5 { t += x * f64(k) + x * x; }"
    "\n  return t;\n}",
    "sqrt and division": "fn f(x:f64) -> f64 = sqrt(x * x + 1.0) / (x + 3.0);",
    "a chain of lets": "fn f(x:f64) -> f64 {\n  let a = x * 2.0;\n  let b = a * a - x;\n  let c = b / (a + 10.0);\n"
    "  return c * c + a;\n}",
    "abs and floor": "fn f(x:f64) -> f64 = abs(x) * 3.0 + floor(x);",
}
POINTS = (0.3, -1.7, 2.6, 0.55, -0.45)
CHECK = """
derive grad for f;
fn close(x:f64) -> bool {
  let h:f64 = 0.000001;
  let mut d:f64 = 0.0;
  let got = f_grad(x, 1.0, d);
  let fd = (f(x + h) - f(x - h)) / (2.0 * h);
  return abs(d - fd) <= 0.0001 * (1.0 + abs(fd)) && got == f(x);
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("name", sorted(GRADIENTS))
def test_a_derived_gradient_agrees_with_a_central_difference(tmp_path, name, cxx):
    tests = "".join(f"  if !close({x}) {{ return {k + 1}; }}\n" for k, x in enumerate(POINTS))
    source = GRADIENTS[name] + "\n" + CHECK + "fn main() -> i32 {\n" + tests + "  return 0;\n}\n"
    done = watched(tmp_path, compile_source(source)[0], cxx, "address,undefined")
    assert done.returncode == 0, f"point {done.returncode}: {done.stderr[-2000:]}"


@pytest.mark.parametrize(
    "form",
    [
        "fn f(x:f64) -> f64 {\n  let mut t:f64 = x * x;\n  t = t * x;\n  return t;\n}",  # overwritten, not a sum
        "fn f(x:f64) -> f64 {\n  let mut t:f64 = x;\n  let mut k:u32 = 0;\n  while k < 3 { t = t * x; k += 1; }\n"
        "  return t;\n}",  # a counter beside the sum
    ],
)
def test_a_gradient_form_the_generator_cannot_write_is_refused_not_guessed(form):
    with pytest.raises(Diagnostic) as refused:
        compile_source(form + "\nderive grad for f;\n")
    assert refused.value.data["code"] == "E-GRAD-FORM"


def rational_quantize(x: float, scale: float, lo: int, hi: int) -> int:
    """x / scale in exact rationals, rounded to nearest with ties to even, clamped: the rule docs/numerics.md states."""
    q = Fraction(f32(x)) / Fraction(f32(scale))
    whole = q.numerator // q.denominator
    rest = q - whole
    rounded = whole + 1 if rest > Fraction(1, 2) or (rest == Fraction(1, 2) and whole % 2) else whole
    return max(lo, min(hi, rounded))


QUANTIZED = [  # (type, x, scale): ties at both ends, saturation, an overflowing quotient, subnormals, signed zero
    ("i8", 63.75, 0.5), ("i8", -64.25, 0.5), ("i8", 127.5, 1.0), ("i8", -128.5, 1.0), ("i8", 3e38, 1e-38),
    ("i8", -3e38, 1e-38), ("u8", -0.4, 1.0), ("u8", 255.5, 1.0), ("u8", 254.5, 1.0), ("i16", 2.5, 1.0),
    ("i16", -2.5, 1.0), ("u16", 65535.49, 1.0), ("i8", 1.401298464324817e-45, 1.401298464324817e-45),
    ("i8", 0.0, 3.0), ("i8", -0.0, 3.0),
]  # fmt: skip
RANGE = {"i8": (-128, 127), "u8": (0, 255), "i16": (-32768, 32767), "u16": (0, 65535)}


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_quantize_at_its_boundaries_is_the_rational_rule(tmp_path, cxx):
    checks = "".join(
        f"  if i64(quantize[{t}]({x!r}, {s!r})) != {rational_quantize(x, s, *RANGE[t])} {{ return {k + 1}; }}\n"
        for k, (t, x, s) in enumerate(QUANTIZED)
    )
    done = watched(tmp_path, compile_source("fn main() -> i32 {\n" + checks + "  return 0;\n}\n")[0], cxx, "undefined")
    assert done.returncode == 0, (
        f"point {done.returncode}: {QUANTIZED[done.returncode - 1] if done.returncode > 0 else ''}"
    )


# --- Refused or stopped: what the review tried that the rules already answer ---------------------------------------


@pytest.mark.parametrize(
    "fresh,why",
    [("average", "already written"), ("len", "builtin"), ("fn", "reserved word"), ("2x", "not an identifier")],
)
def test_a_rename_that_could_change_a_name_s_meaning_is_refused(tmp_path, fresh, why):
    root = tmp_path / "demo"
    create_project(root)
    main_file = root / "src/main.cairn"
    text = main_file.read_text()
    ws = workspace(main_file.as_uri(), {})
    with pytest.raises(ValueError, match=why):
        rename(ws, main_file.as_uri(), text.index("mean"), fresh)
    with pytest.raises(ValueError, match="entry point"):
        rename(ws, main_file.as_uri(), text.index("fn main") + 3, "start")


DRAWN = "import std.image;\nimport std.draw;\n"
STOPPED = {  # each stops at a guard: an image has at least one pixel, and a coordinate sum is checked
    "an empty image": "fn main() -> i32 { let a = image.new(0, 4); return 0; }\n",
    "a corner past the largest coordinate": "fn main() -> i32 {\n  let mut a = image.new(8, 8);\n"
    "  draw.rect(a, 9223372036854775807, 0, 9223372036854775807, 1, 7);\n  return 0;\n}\n",
}


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("name", sorted(STOPPED))
def test_a_drawing_the_library_cannot_address_stops_at_a_guard(tmp_path, name, cxx):
    done = watched(tmp_path, compile_source(DRAWN + STOPPED[name])[0], cxx, "address,undefined")
    assert done.returncode == -6, (done.returncode, done.stderr[-1500:])  # SIGABRT from the guard, nothing else

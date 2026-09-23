"""The second adversarial review of what was added after 1.3: what it found, each fix pinned by the program that
showed it, and the attacks that were correctly refused or held, kept so that a later change cannot reopen them.

`evidence/v1_4/review/README.md` has the write-up. The first review's table is `test_review_1_4.py`.
"""

import shutil
import subprocess
import tempfile

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import compile_source
from cairn.perf.tune import write_plan
from cairn.projects.project import ProjectError, load_project
from cairn.projects.revision import read
from cairn.verify.diff import single


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
    assert '"written": "src/y.cairn"' in capsys.readouterr().out
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

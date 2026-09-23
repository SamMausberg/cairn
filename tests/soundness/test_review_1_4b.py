"""The second adversarial review of what was added after 1.3: what it found, each fix pinned by the program that
showed it, and the attacks that were correctly refused or held, kept so that a later change cannot reopen them.

`evidence/v1_4/review/README.md` has the write-up. The first review's table is `test_review_1_4.py`.
"""

import shutil
import subprocess
import tempfile

import pytest

from cairn.projects.project import ProjectError
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

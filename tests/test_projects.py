import json
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.agent_tools import PROTOCOL, EditSession, canonical_source
from cairn.build import build
from cairn.cairnc import Diagnostic, Parser, compile_source
from cairn.cli import create_project, main
from cairn.project import ProjectError, load_project
from cairn.scalar_semantics import equivalent
from cairn.sketches import ScalarContract, Sketch


@pytest.mark.parametrize(
    "body,ty,params",
    [
        ("add_wrap(x,1)", "u64", "x:u64"),
        ("x > 3", "bool", "x:u64"),
        ("(x & y) + shr(x ^ y,1)", "u64", "x:u64,y:u64"),
        ("x/2", "u32", "x:u32"),
        ("true", "bool", ""),
        ("x*2.0", "f32", "x:f32"),
    ],
)
def test_expression_body_same_native_code(body, ty, params):
    concise = f"fn f({params})->{ty} = {body};"
    explicit = f"fn f({params})->{ty} {{return {body};}}"
    assert compile_source(concise)[0] == compile_source(explicit)[0]
    assert compile_source(canonical_source(concise))[0] == compile_source(concise)[0]


@pytest.mark.parametrize(
    "source",
    [
        "fn f() = 1;",
        "fn f()->void = 1;",
        "fn f()->u64 = true;",
        "fn f()->u64 = ;",
        "fn f()->u64 = 1",
        "fn f()->u64 = 1; return 2;",
        "fn f()->u64 = {return 1;};",
    ],
)
def test_bad_expression_bodies_rejected(source):
    with pytest.raises(Diagnostic):
        compile_source(source)


@pytest.mark.parametrize("mode", ["ro", "rw"])
def test_host_elision(mode):
    short = f"fn f(n:usize,x:{mode}<u64>[n])->u64 = x[0];"
    assert compile_source(short)[0] == compile_source(short.replace("[n]", "[n]@host"))[0]


@pytest.mark.parametrize("space", ["global", "shared", "device", "cpu"])
def test_host_elision_does_not_accept_other_spaces(space):
    with pytest.raises(Diagnostic):
        compile_source(f"fn f(x:ro<u64>[1]@{space})->u64=x[0];")


def test_else_if_same_code():
    src = "fn f(x:u64)->u64 {if x==0{return 1;} else if x==1{return 2;} else{return 3;}}"
    explicit = "fn f(x:u64)->u64 {if x==0{return 1;} else {if x==1{return 2;} else{return 3;}}}"
    assert compile_source(src)[0] == compile_source(explicit)[0]


def test_expression_sketch_retains_comments_and_checks_whole_module():
    src = "// review note\nfn f(x:u64)->u64 = add_wrap(x,1); // tail\nfn g(x:u64)->u64 = f(x);\n"
    sk = Sketch(src, "f").hole("increment", "1")
    changed = sk.fill(increment="2").source
    assert changed == src.replace("x,1", "x,(2)")
    assert compile_source(changed)


def test_body_edit_can_replace_expression_body():
    s = EditSession("fn f(x:u64)->u64=add_wrap(x,1);", "f")
    new, _ = s.check({"protocol": PROTOCOL, "session": s.session, "kind": "body", "replacement": "{return x;}"})
    assert new == "fn f(x:u64)->u64{return x;}"


def test_expression_body_semantic_feedback():
    ref = "fn f(x:u64)->u64 {return add_wrap(x,1);}"
    good = "fn f(x:u64)->u64 = add_wrap(x,1);"
    bad = "fn f(x:u64)->u64 = add_wrap(x,2);"
    assert equivalent(ref, good, "f")["status"] == "smt-equivalent"
    assert equivalent(ref, bad, "f")["status"] == "counterexample"


def make(tmp_path):
    root = tmp_path / "demo"
    create_project(root)
    return root


def test_project_is_ordered_and_pinned(tmp_path):
    root = make(tmp_path)
    p = load_project(root)
    assert [x.path for x in p.units] == ["src/math.cairn", "src/main.cairn"]
    assert compile_source(p.source)[1]["function_count"] == 2
    original = p.receipt()
    (root / "src/main.cairn").write_text("fn main()->i32=0;")
    assert load_project(root).receipt() != original


def test_project_error_maps_to_original_file(tmp_path):
    root = make(tmp_path)
    (root / "src/main.cairn").write_text("fn main()->i32 {\nreturn true;\n}")
    p = load_project(root)
    with pytest.raises(Diagnostic) as exc:
        compile_source(p.source)
    loc = p.locate(exc.value)
    assert loc["file"] == "src/main.cairn" and loc["line"] == 2


@pytest.mark.parametrize(
    "manifest",
    [
        '[project]\nname="a"\nsources=["../x.cairn"]',
        '[project]\nname="a"\nsources=["/x.cairn"]',
        '[project]\nname="a"\nsources=["src/./main.cairn"]',
        '[project]\nname="a"\nsources=["src/main.cairn","src/main.cairn"]',
        '[project]\nname="a"\nsources=["src/main.cairn"]\nhook="curl attacker"',
        '[project]\nname="a"\nsources=[]',
        '[project]\nname="a"\nsources=[1]',
        '[project]\nname="../a"\nsources=["src/main.cairn"]',
        '[project]\nname="a"\nsources=["src/main.cairn"]\n[build]\ncxx="sh"',
        '[project]\nname="a"\nsources=["src/main.cairn"]\n[build]\nkind="gpu"',
        '[project]\nname="a"\nsources=["src/main.cairn"]\n[build]\nkind=[]',
    ],
)
def test_bad_manifests_fail_closed(tmp_path, manifest):
    root = make(tmp_path)
    (root / "cairn.toml").write_text(manifest)
    with pytest.raises(ProjectError):
        load_project(root)


def test_symlink_source_rejected(tmp_path):
    root = make(tmp_path)
    (root / "src/math.cairn").unlink()
    external = tmp_path / "external.cairn"
    external.write_text("fn x()->u64=0;")
    (root / "src/math.cairn").symlink_to(external)
    with pytest.raises(ProjectError):
        load_project(root)


def test_new_never_overwrites(tmp_path):
    root = make(tmp_path)
    before = (root / "cairn.toml").read_bytes()
    with pytest.raises(FileExistsError):
        create_project(root)
    assert before == (root / "cairn.toml").read_bytes()


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_native_project_and_tests(tmp_path, cxx):
    import shutil

    if not shutil.which(cxx):
        pytest.skip("Native compiler unavailable")
    root = make(tmp_path)
    record = build(load_project(root), cxx=cxx)
    assert record["status"] == "native-built"
    assert subprocess.run([record["artifact"]], timeout=5).returncode == 0
    assert main(["test", str(root), "--cxx", cxx]) == 0
    again = build(load_project(root), cxx=cxx)
    assert again["directory"] != record["directory"]
    assert Path(record["artifact"]).exists()


def test_unknown_tool_does_not_produce_output(tmp_path):
    root = make(tmp_path)
    with pytest.raises(ProjectError):
        build(load_project(root), cxx="nonexistent-cairn-compiler")
    assert not (root / "build").exists()


@pytest.mark.parametrize("src", ["fn main()->u64=0;", "fn main(x:i32)->i32=x;", "fn foo()->i32=0;"])
def test_bad_executable_entry(tmp_path, src):
    p = tmp_path / "bad.cairn"
    p.write_text(src)
    with pytest.raises(ProjectError):
        build(load_project(p), kind="exe")


def test_output_symlink_rejected(tmp_path):
    root = make(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "build").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ProjectError):
        build(load_project(root))
    assert list(elsewhere.iterdir()) == []

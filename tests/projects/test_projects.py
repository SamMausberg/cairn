import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.agent.agent_tools import PROTOCOL, EditSession
from cairn.agent.projection import canonical_source
from cairn.agent.sketches import Sketch
from cairn.cli import create_project, main
from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.projects.build import build
from cairn.projects.project import ProjectError, load_project
from cairn.verify.scalar_semantics import equivalent
from emitted import refused


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
    functions = compile_source(p.source)[1]["functions"]
    assert sorted(f for f in functions if not f.startswith("std.")) == ["average", "main"]
    original = p.receipt()
    (root / "src/main.cairn").write_text("fn main()->i32=0;")
    assert load_project(root).receipt() != original


def test_a_tree_may_hold_a_second_manifest_named_by_its_path(tmp_path):
    root = make(tmp_path)
    (root / "small.toml").write_text('[project]\nname = "small"\nsources = ["src/math.cairn"]\n')
    assert [x.path for x in load_project(root / "small.toml").units] == ["src/math.cairn"]
    assert load_project(root).name == "demo" and load_project(root / "cairn.toml").name == "demo"
    (root / "notes.txt").write_text("not a manifest")
    with pytest.raises(ProjectError):
        load_project(root / "notes.txt")


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


MODULES = """
module geometry;
pub struct Rect { w:u64; h:u64; }
pub trait Shape { fn area(self:ro<Self>) -> u64; }
impl Shape for Rect { fn area(self:ro<Rect>) -> u64 = self.w * self.h; }
pub fn scale(r:Rect, k:u64) -> Rect = Rect(r.w * k, r.h * k);

module stats;
pub fn total(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = t + xs[i]; } return t; }
pub fn largest[T](a:T, b:T) -> T { if a < b { return b; } return a; }
pub fn collect(k:usize) -> u64 { let mut v = Buf[u64](k); for i in 0..k { v[i] = u64(i); } return total(len(v), v); }

module app;
import geometry;
import stats;
fn measure(s:ro<dyn geometry.Shape>) -> u64 = area(s);
pub fn main() -> i32 {
  let r = geometry.scale(geometry.Rect(2, 3), 2);
  let sum = stats.collect(5);
  if measure(r) + stats.largest(sum, 3) != 24 + 10 { return 1; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_incremental_builds_relink_only_the_module_whose_body_changed(tmp_path, cxx):
    """Objects are reused only by content: same unit, same shared interface, same command, same compiler."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    path = tmp_path / "program.cairn"

    def built(source):
        path.write_text(source, encoding="utf-8")
        record = build(load_project(path), kind="exe", cxx=cxx, timeout=180, incremental=True)
        assert record["status"] == "native-built", record.get("stderr")
        assert subprocess.run([record["artifact"]], timeout=30).returncode == 0
        return {unit["unit"]: unit["reused"] for unit in record["units"]}

    assert built(MODULES) == {"geometry.cpp": False, "stats.cpp": False, "app.cpp": False, "0start.cpp": False}
    assert all(built(MODULES).values())  # Nothing changed: nothing compiles, everything links.
    body = MODULES.replace("t = t + xs[i];", "t = add_wrap(t, xs[i]);")
    assert built(body) == {"geometry.cpp": True, "stats.cpp": False, "app.cpp": True, "0start.cpp": True}
    signature = MODULES.replace("k:u64) -> Rect", "k:u64, spare:u64) -> Rect").replace("3), 2);", "3), 2, 0);")
    assert not any(built(signature).values())  # The shared interface changed, so every unit is rebuilt.
    whole = build(load_project(path), kind="exe", cxx=cxx, timeout=180)
    assert whole["units"] == [] and subprocess.run([whole["artifact"]], timeout=30).returncode == 0
    # A module may be called `entry`: the start-up unit's file name is not one a module can have.
    named = "module entry;\npub fn helper() -> u64 = 41;\nmodule app;\nimport entry;\n"
    path.write_text(named + "pub fn main() -> i32 { if entry.helper() != 41 { return 1; } return 0; }\n")
    record = build(load_project(path), kind="exe", cxx=cxx, timeout=180, incremental=True)
    assert record["status"] == "native-built" and subprocess.run([record["artifact"]], timeout=30).returncode == 0


SMALL = "module lib;\npub fn f() -> i32 = 0;\nmodule app;\nimport lib;\npub fn main() -> i32 = lib.f();\n"


def incremental(path: Path, source: str | None = None) -> dict:
    """One tiny three-unit program, built with the object cache."""
    if source is not None:
        path.write_text(source, encoding="utf-8")
    return build(load_project(path), kind="exe", cxx="clang++", timeout=120, incremental=True)


def test_a_cached_object_is_reused_only_while_its_bytes_still_match_its_key(tmp_path):
    """A name in the cache is not evidence: an object is linked only under the digest stored beside it, so a
    replaced, truncated or half-written file is compiled again."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    path = tmp_path / "program.cairn"
    first = incremental(path, SMALL)
    assert first["status"] == "native-built" and not any(unit["reused"] for unit in first["units"])
    forged = Path(next(unit["object"] for unit in incremental(path, SMALL.replace("= 0;", "= 42;"))["units"]))
    cached = Path(next(unit["object"] for unit in first["units"] if unit["unit"] == "lib.cpp"))
    digest = cached.with_suffix(".sha256")
    assert digest.is_file() and all(unit["reused"] for unit in incremental(path, SMALL)["units"])
    cached.write_bytes(forged.read_bytes())  # The object of another program, under this program's key.
    record = incremental(path, SMALL)
    assert {unit["unit"]: unit["reused"] for unit in record["units"]}["lib.cpp"] is False
    assert record["status"] == "native-built"
    assert subprocess.run([record["artifact"]], timeout=30).returncode == 0  # what a clean build does, not 42
    cached.write_bytes(cached.read_bytes()[:64])  # A half object from an interrupted build is not linked either.
    assert incremental(path)["status"] == "native-built"
    digest.unlink()
    assert {unit["unit"]: unit["reused"] for unit in incremental(path)["units"]}["lib.cpp"] is False
    cached.unlink()
    cached.symlink_to(forged)
    with pytest.raises(ProjectError, match="plain files"):
        incremental(path)


def test_the_object_cache_stays_inside_the_project_build_directory(tmp_path):
    """`build/objects` is refused as a symbolic link or a file, exactly as the build directory itself is."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    path = tmp_path / "program.cairn"
    path.write_text(SMALL, encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "build").mkdir()
    (tmp_path / "build/objects").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ProjectError, match="object cache"):
        incremental(path)
    assert list(elsewhere.iterdir()) == []
    (tmp_path / "build/objects").unlink()
    (tmp_path / "build/objects").write_text("not a directory")
    with pytest.raises(ProjectError, match="object cache"):
        incremental(path)


def test_a_unit_that_times_out_or_is_killed_records_the_same_build_as_one_unit_does(tmp_path, monkeypatch):
    """The record is the build's answer, under `--incremental` as for a whole program; no object is left behind."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    path = tmp_path / "program.cairn"
    path.write_text(SMALL, encoding="utf-8")
    ran = subprocess.run

    def compiling(command):
        return any(str(argument).endswith(".cpp") for argument in command)

    def expires(command, **kw):
        if compiling(command):
            raise subprocess.TimeoutExpired(command, kw.get("timeout", 1))
        return ran(command, **kw)

    def killed(command, **kw):
        return subprocess.CompletedProcess(command, -9, "", "Killed") if compiling(command) else ran(command, **kw)

    monkeypatch.setattr(subprocess, "run", expires)
    for flag in (False, True):  # The whole-program path and the object cache answer a timeout the same way.
        record = build(load_project(path), kind="exe", timeout=5, incremental=flag)
        assert (record["schema"], record["status"]) == ("cairn.build/1", "unknown") and record["message"]
        assert json.loads((Path(record["directory"]) / "receipt.json").read_text())["status"] == "unknown"
    assert list((tmp_path / "build/objects").iterdir()) == []  # A timed-out unit leaves no object under its key.
    monkeypatch.setattr(subprocess, "run", killed)
    record = build(load_project(path), kind="exe", timeout=5, incremental=True)
    assert record["status"] == "native-build-failed" and record["exit_code"] == -9
    assert list((tmp_path / "build/objects").iterdir()) == []


def vendored(root: Path) -> Path:
    """app -> deps/geometry -> deps/geometry/deps/units, all inside the application's root."""
    files = {
        "cairn.toml": '[project]\nname = "app"\nsources = ["src/main.cairn"]\n[build]\nkind = "exe"\n'
        '[dependencies]\ngeometry = "deps/geometry"\n',
        "src/main.cairn": "module app;\nimport geometry.shapes as shapes;\nimport units;\n"
        "pub fn main() -> i32 { let r = shapes.Rect(units.metres(2), 3); return i32(shapes.area(r)) - 6; }\n",
        "deps/geometry/cairn.toml": '[project]\nname = "geometry"\nsources = ["src/shapes.cairn"]\n'
        '[dependencies]\nunits = "deps/units"\n',
        "deps/geometry/src/shapes.cairn": "module geometry.shapes;\nimport units;\npub struct Rect { w:u64; h:u64; }\n"
        "fn secret(r:ro<Rect>) -> u64 = r.w;\npub fn area(r:ro<Rect>) -> u64 = units.metres(secret(r)) * r.h;\n",
        "deps/geometry/deps/units/cairn.toml": '[project]\nname = "units"\nsources = ["units.cairn"]\n',
        "deps/geometry/deps/units/units.cairn": "module units;\npub fn metres(v:u64) -> u64 = v;\n",
    }
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


def test_vendored_dependencies_load_first_stay_private_and_are_pinned(tmp_path):
    project = load_project(vendored(tmp_path))
    assert [d["name"] for d in project.dependencies] == ["units", "geometry"]  # A dependency's own come first.
    assert project.units[0].path == "deps/geometry/deps/units/units.cairn"
    assert all(len(d["source_sha256"]) == 64 for d in project.receipt()["dependencies"])
    record = build(project, cxx="clang++", timeout=120)
    assert record["status"] == "native-built" and subprocess.run([record["artifact"]], timeout=30).returncode == 0
    (tmp_path / "src/main.cairn").write_text(
        "module app;\nimport geometry.shapes as shapes;\n"
        "pub fn main() -> i32 { return i32(shapes.secret(shapes.Rect(1, 1))); }\n"
    )
    refused("E-PRIVATE", load_project(tmp_path).source)


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (("cairn.toml", 'geometry = "deps/geometry"', 'geometry = "../elsewhere"'), "inside the project"),
        (("cairn.toml", 'geometry = "deps/geometry"', 'shapes = "deps/geometry"'), "another name"),
        (("cairn.toml", 'geometry = "deps/geometry"', 'geometry = "deps/./geometry"'), "inside the project"),
        # One directory is one project under one name: a second name for it is not skipped as a diamond.
        (
            ("cairn.toml", 'geometry = "deps/geometry"\n', 'geometry = "deps/geometry"\nshapes = "deps/geometry"\n'),
            "another name",
        ),
        (("deps/geometry/src/shapes.cairn", "module geometry.shapes;\n", ""), "modules only"),
        (("cairn.toml", "[dependencies]", "[hooks]\npre = 1\n[dependencies]"), "Unknown manifest tables"),
        # A dependency's manifest is read by the same checker as the root's: tables, options and build values.
        (("deps/geometry/cairn.toml", "[dependencies]", '[hooks]\npre = "rm -rf /"\n[dependencies]'), "hooks"),
        (("deps/geometry/cairn.toml", "[dependencies]", '[build]\narch = "nonsense"\n[dependencies]'), "architecture"),
        (("deps/geometry/cairn.toml", "[dependencies]", '[build]\ntarget = "nonsense"\n[dependencies]'), "target"),
        (("deps/geometry/cairn.toml", 'name = "geometry"', 'name = "geometry"\nwhatever = 1'), "Unknown manifest"),
        (("deps/geometry/cairn.toml", "[dependencies]", 'tests = ["../t.json"]\n[dependencies]'), "Noncanonical"),
        # No project source declares a module of the packaged library, whoever wrote it.
        (("deps/geometry/deps/units/units.cairn", "module units;", "module std.core;"), "packaged library"),
        (("src/main.cairn", "module app;", "module std.core;"), "packaged library"),
        # A module belongs to one project of the build: neither side may reopen the other's.
        (("deps/geometry/src/shapes.cairn", "module geometry.shapes;", "module app;"), "belongs to geometry"),
        (("deps/geometry/src/shapes.cairn", "module geometry.shapes;", "module units;"), "belongs to units"),
        # Sources are concatenated, so a file with no header would declare into the last dependency's module.
        (("src/main.cairn", "module app;\n", ""), "belongs to geometry"),
    ],
)
def test_a_dependency_is_data_inside_the_root_and_nothing_else(tmp_path, change, why):
    relative, old, new = change
    path = vendored(tmp_path) / relative
    assert old in path.read_text()
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(ProjectError, match=why):
        load_project(tmp_path)


def test_a_symbolic_link_is_not_a_dependency(tmp_path):
    root = vendored(tmp_path / "app")
    (tmp_path / "outside").mkdir()
    shutil.copytree(root / "deps/geometry", tmp_path / "outside/geometry")
    shutil.rmtree(root / "deps/geometry")
    (root / "deps/geometry").symlink_to(tmp_path / "outside/geometry")
    with pytest.raises(ProjectError, match="symbolic links"):
        load_project(root)


def test_one_name_is_one_project_of_the_build(tmp_path):
    """A name pins one project in the receipt, so no dependency may take the root's name or another's."""
    root = vendored(tmp_path)
    manifest = root / "cairn.toml"
    second = root / "deps/other/cairn.toml"
    second.parent.mkdir(parents=True)
    second.write_text('[project]\nname = "units"\nsources = ["u.cairn"]\n')
    (root / "deps/other/u.cairn").write_text("module other;\npub fn one() -> u64 = 1;\n")
    manifest.write_text(manifest.read_text() + 'units = "deps/other"\n')  # units is already vendored elsewhere
    with pytest.raises(ProjectError, match="named units"):
        load_project(root)
    second.write_text('[project]\nname = "app"\nsources = ["u.cairn"]\n')
    manifest.write_text(manifest.read_text().replace('units = "deps/other"', 'app = "deps/other"'))
    with pytest.raises(ProjectError, match="named app"):  # not even the root project's own name
        load_project(root)


def test_a_project_of_one_file_declares_no_module_of_the_packaged_library(tmp_path):
    """One `.cairn` file is a project too: it may not shadow `std.core` any more than a manifest's sources may."""
    path = tmp_path / "one.cairn"
    path.write_text("module std.core;\npub fn backdoor() -> i32 = 99;\n")
    with pytest.raises(ProjectError, match="packaged library"):
        load_project(path)
    path.write_text("module std_core;\npub fn fine() -> i32 = 0;\n")  # only `std` and `std.*` are the library's
    assert load_project(path).name == "one"


def test_the_entry_point_is_the_root_project_s_own(tmp_path):
    """A dependency is a library: it neither supplies an executable's `main` nor denies the project its own."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    root = vendored(tmp_path)
    shapes = root / "deps/geometry/src/shapes.cairn"
    shapes.write_text(shapes.read_text() + "pub fn main() -> i32 = 42;\n")
    own = (root / "src/main.cairn").read_text()
    (root / "src/main.cairn").write_text("module app;\npub fn helper() -> i32 = 1;\n")
    with pytest.raises(ProjectError, match="exactly one fn main"):  # never the dependency's, which would exit 42
        build(load_project(root), cxx="clang++", timeout=120)
    (root / "src/main.cairn").write_text(own)
    record = build(load_project(root), cxx="clang++", timeout=120)
    assert record["status"] == "native-built", record.get("stderr")
    assert subprocess.run([record["artifact"]], timeout=30).returncode == 0  # the project's main, not the library's


def test_the_guide_shows_the_project_new_creates(tmp_path):
    """docs/guide.md prints the template and what check and run answer for it; both follow create_project."""
    guide = (Path(__file__).resolve().parents[2] / "docs/guide.md").read_text()
    section = guide.split("## A project\n", 1)[1].split("## A wrong edit\n", 1)[0]
    root = make(tmp_path)
    assert (root / "cairn.toml").read_text() in section
    sources = (root / "src/math.cairn").read_text() + "\n" + (root / "src/main.cairn").read_text()
    assert f"```cairn\n{sources}```" in section
    functions = compile_source(load_project(root).source)[1]["functions"]
    library = sum(1 for name in functions if name.startswith("std."))
    assert f"typed: {len(functions) - library} functions, and {library} from the library" in section
    assert f'"functions": {len(functions)}, "library_functions": {library},' in section
    record = build(load_project(root), cxx="clang++", timeout=120)
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0 and f"```text\n{done.stdout}```" in section

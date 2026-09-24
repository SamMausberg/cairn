"""What large codebases lean on: `cairn graph`, the size limits, compile_commands.json, and the Bazel rules."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler import expansion
from cairn.compiler.cairnc import compile_source
from cairn.projects import build as build_module
from cairn.projects.build import build
from cairn.projects.graph import graph
from cairn.projects.project import ProjectError, load_project
from emitted import refused

ROOT = Path(__file__).resolve().parents[2]

GEOMETRY = """module shop.geometry;
pub struct Box { w:u64; h:u64; }
pub fn area(b:Box) -> u64 = b.w * b.h;
fn unused() -> u64 = 1;
"""
PRICING = """module shop.pricing;
import shop.geometry (Box);
pub fn price(b:Box, rate:u64) -> u64 = geometry.area(b) * rate;
"""
MAIN = """module shop;
import shop.pricing;
import shop.geometry (Box);
pub fn main() -> i32 {
  let p = pricing.price(Box(2, 3), 5);
  if p != 30 { return 1; }
  return 0;
}
"""


def shop(root: Path, geometry: str = GEOMETRY) -> Path:
    (root / "src").mkdir(parents=True, exist_ok=True)
    for name, text in {"geometry": geometry, "pricing": PRICING, "main": MAIN}.items():
        (root / f"src/{name}.cairn").write_text(text)
    sources = ", ".join(f'"src/{n}.cairn"' for n in ("geometry", "pricing", "main"))
    (root / "cairn.toml").write_text(f'[project]\nname = "shop"\nsources = [{sources}]\n\n[build]\nkind = "exe"\n')
    return root


def test_the_graph_names_each_files_modules_and_each_modules_imports_exports_and_dependents(tmp_path):
    g = graph(load_project(shop(tmp_path)))
    assert g["schema"] == "cairn.graph/1" and g["project"] == "shop" and not g["cyclic"]
    assert [(f["path"], f["modules"]) for f in g["files"]] == [
        ("src/geometry.cairn", ["shop.geometry"]), ("src/pricing.cairn", ["shop.pricing"]), ("src/main.cairn", ["shop"])
    ]  # fmt: skip
    geometry = g["modules"]["shop.geometry"]
    assert geometry["imports"] == [] and geometry["dependents"] == ["shop", "shop.pricing"]
    assert geometry["exports"] == ["shop.geometry.Box", "shop.geometry.area"]  # `unused` is not public
    assert g["modules"]["shop.pricing"]["imports"] == ["shop.geometry"]
    assert g["order"].index("shop.geometry") < g["order"].index("shop.pricing") < g["order"].index("shop")
    assert "interface_sha256" not in geometry  # only --interfaces checks the program


def test_a_body_edit_moves_the_source_hash_and_a_signature_edit_moves_the_interface_hash(tmp_path):
    before = graph(load_project(shop(tmp_path / "a")), with_interfaces=True)["modules"]["shop.geometry"]
    body = graph(load_project(shop(tmp_path / "b", GEOMETRY.replace("b.w * b.h", "b.h * b.w"))), True)
    body = body["modules"]["shop.geometry"]
    wider = GEOMETRY.replace("pub fn area(b:Box) -> u64 = b.w * b.h;", "pub fn area(b:ro<Box>) -> u64 = b.w * b.h;")
    signature = graph(load_project(shop(tmp_path / "c", wider)), True)["modules"]["shop.geometry"]
    assert body["source_sha256"] != before["source_sha256"] and body["interface_sha256"] == before["interface_sha256"]
    assert signature["interface_sha256"] != before["interface_sha256"]


def test_an_import_cycle_is_reported_and_every_module_still_listed(tmp_path):
    (tmp_path / "loop.cairn").write_text("module a;\nimport b;\npub fn f() -> u64 = 1;\n"
                                         "module b;\nimport a;\npub fn g() -> u64 = 2;\n")  # fmt: skip
    g = graph(load_project(tmp_path / "loop.cairn"))
    assert g["cyclic"] and sorted(g["order"]) == ["a", "b"]


def test_the_command_prints_the_record_or_one_line_per_module(tmp_path, capsys):
    root = shop(tmp_path)
    assert main(["graph", str(root), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["modules"]["shop"]["imports"] == ["shop.geometry", "shop.pricing"]
    assert main(["graph", str(root), "--format", "human"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "shop: 3 modules in 3 files" and any(line.startswith("  shop.pricing  [src/pricing.cairn]")
                                                                for line in lines)  # fmt: skip


def test_a_program_past_the_old_limit_of_2048_functions_is_accepted():
    source = "".join(f"fn item{k}() -> u64 = {k};\n" for k in range(2100))
    assert compile_source(source)[1]["function_count"] >= 2100


def test_each_size_limit_names_what_it_counted(monkeypatch):
    monkeypatch.setattr(expansion, "MAX_FUNCTIONS", 10)
    error = refused("E-EXPANSION-LIMIT", "".join(f"fn item{k}() -> u64 = {k};\n" for k in range(11)))
    assert "holds 11 functions" in error["message"] and "limit of 10" in error["message"]
    monkeypatch.setattr(expansion, "MAX_FUNCTIONS", 1 << 20)
    monkeypatch.setattr(expansion, "MAX_NODES", 5)
    error = refused("E-EXPANSION-LIMIT", "fn f() -> u64 = 1 + 2 + 3 + 4;")
    assert "syntax nodes" in error["message"] and "limit of 5" in error["message"]


def test_a_manifest_lists_up_to_1024_sources(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    names = [f"src/m{k}.cairn" for k in range(1025)]
    for k, name in enumerate(names):
        (tmp_path / name).write_text(f"module m{k};\npub fn f() -> u64 = {k};\n")
    listed = ", ".join(f'"{n}"' for n in names[:100])
    (tmp_path / "cairn.toml").write_text(f'[project]\nname = "many"\nsources = [{listed}]\n')
    assert len(load_project(tmp_path).units) == 100
    listed = ", ".join(f'"{n}"' for n in names)
    (tmp_path / "cairn.toml").write_text(f'[project]\nname = "many"\nsources = [{listed}]\n')
    with pytest.raises(ProjectError, match=r"1\.\.1024"):
        load_project(tmp_path)


@pytest.mark.parametrize("incremental", [False, True])
def test_a_build_writes_the_compile_commands_of_the_code_it_generated(tmp_path, incremental):
    if not shutil.which("clang++"):
        pytest.skip("needs clang++")
    record = build(load_project(shop(tmp_path)), incremental=incremental)
    assert record["status"] == "native-built", record.get("stderr")
    entries = json.loads((Path(record["directory"]) / "compile_commands.json").read_text())
    files = {Path(e["file"]).name for e in entries}
    assert files == ({"0start.cpp", "shop.cpp", "shop_geometry.cpp", "shop_pricing.cpp"} if incremental
                     else {"program.cpp"})  # fmt: skip
    assert all(e["directory"] == record["directory"] and e["file"] in e["arguments"] for e in entries)
    assert all(Path(e["file"]).is_file() for e in entries)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_many_units_compile_against_a_precompiled_header_and_mean_what_they_meant(tmp_path, cxx, monkeypatch):
    if not shutil.which(cxx):
        pytest.skip(f"needs {cxx}")
    monkeypatch.setattr(build_module, "PRECOMPILE_AT", 1)
    (tmp_path / "src").mkdir()
    names = []
    for k in range(20):
        names.append(f"src/m{k}.cairn")
        (tmp_path / names[-1]).write_text(f"module chain.m{k};\n" + (f"import chain.m{k - 1};\n" if k else "")
                                          + f"pub fn at() -> u64 = {f'm{k - 1}.at() + ' if k else ''}{k};\n")  # fmt: skip
    names.append("src/main.cairn")
    (tmp_path / names[-1]).write_text('module chain;\nimport chain.m19;\npub fn main() -> i32 {\n  println("sum = ", '
                                      "m19.at());\n  return 0;\n}\n")  # fmt: skip
    listed = ", ".join(f'"{n}"' for n in names)
    (tmp_path / "cairn.toml").write_text(f'[project]\nname = "chain"\nsources = [{listed}]\n\n[build]\nkind = "exe"\n')
    record = build(load_project(tmp_path), cxx=cxx, incremental=True)
    assert record["status"] == "native-built", record.get("stderr")
    made = {p.name for p in Path(record["directory"]).iterdir()}
    assert made & {"program.hpp.pch", "pch.hpp.gch"}, made  # the header was precompiled, not read as text
    ran = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0 and ran.stdout == "sum = 190\n"


def test_the_bazel_example_builds_runs_and_tests_its_cairn_targets(tmp_path):
    """examples/bazel through rules_cairn (bazel/): a library checked as validation, a binary, a test."""
    bazel = shutil.which("bazel") or shutil.which("bazelisk")
    if not bazel or not shutil.which("clang++"):
        pytest.skip("needs bazel and clang++")
    workspace = tmp_path / "bazel"  # a copy, so nothing Bazel writes lands in the checkout
    shutil.copytree(ROOT / "examples/bazel", workspace)
    (workspace / "MODULE.bazel").write_text(
        (workspace / "MODULE.bazel").read_text().replace('"../../bazel"', f'"{ROOT / "bazel"}"')
        .replace('cairn.local(path = "../..")', f'cairn.local(path = "{ROOT}")')
    )  # fmt: skip
    if ROOT.is_relative_to("/tmp"):  # Bazel's sandbox mounts its own /tmp, which hides a checkout there
        with (workspace / ".bazelrc").open("a") as rc:
            rc.write(f"build --sandbox_add_mount_pair={ROOT}\n")
    root = ["--output_user_root", str(tmp_path / "root")]
    env = {**os.environ, "HOME": os.environ.get("HOME", str(tmp_path))}

    def bazel_do(*args: str, path: str | None = None) -> subprocess.CompletedProcess:
        where = {**env, "PATH": path} if path else env
        return subprocess.run([bazel, *root, *args], cwd=workspace, capture_output=True, text=True, env=where,
                              timeout=900)  # fmt: skip

    try:
        built = bazel_do("build", "//...")
        if "Failed to fetch" in built.stderr or "Unable to download" in built.stderr:
            pytest.skip("Bazel could not fetch its own modules here: " + built.stderr[-400:])
        assert built.returncode == 0, built.stderr[-4000:]
        ran = bazel_do("run", "//:shop")
        assert ran.returncode == 0 and "price = 30" in ran.stdout, ran.stderr[-4000:]
        tested = bazel_do("test", "//:pricing_test", "--test_output=errors")
        assert tested.returncode == 0, tested.stdout[-4000:] + tested.stderr[-4000:]
        test_file = workspace / "pricing/pricing_test.cairn"
        test_file.write_text(test_file.read_text().replace("5), 30)", "5), 31)"))
        failed = bazel_do("test", "//:pricing_test", "--test_output=errors")  # a failing test block fails the target
        assert failed.returncode != 0 and "prices_by_area" in failed.stdout + failed.stderr, failed.stderr[-4000:]
        (workspace / "geometry/geometry.cairn").write_text(
            (workspace / "geometry/geometry.cairn").read_text().replace("b.w * b.h", "b.w * b.missing")
        )
        refused_build = bazel_do("build", "//:geometry")  # the validation action runs cairn check
        assert refused_build.returncode != 0 and "E-" in refused_build.stderr, refused_build.stderr[-4000:]
        older = tmp_path / "older"  # a python3 first on PATH that is not 3.11 or later
        older.mkdir()
        (older / "python3").write_text("#!/bin/sh\nexit 1\n")
        (older / "python3").chmod(0o755)
        stale = bazel_do("build", "//:shop", path=f"{older}:{os.environ['PATH']}")
        assert stale.returncode != 0 and "CAIRN needs Python 3.11 or later" in stale.stderr, stale.stderr[-4000:]
    finally:
        bazel_do("shutdown")

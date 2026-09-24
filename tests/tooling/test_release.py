"""A release states one version, in every place that states it, and the changelog has that version's section."""

import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile

import pytest

from cairn import __version__
from release.yaml_subset import read

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_version_is_stated_once():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lakefile = tomllib.loads((ROOT / "proofs/lakefile.toml").read_text(encoding="utf-8"))
    extension = json.loads((ROOT / "editors/vscode/package.json").read_text(encoding="utf-8"))
    capabilities = json.loads((ROOT / "docs/project/capabilities.json").read_text(encoding="utf-8"))
    card = (ROOT / "src/cairn/agent/teaching.py").read_text(encoding="utf-8")
    citation = read(ROOT / "CITATION.cff")
    major_minor = ".".join(__version__.split(".")[:2])
    assert pyproject["project"]["version"] == __version__
    assert lakefile["version"] == __version__
    assert extension["version"] == __version__
    assert capabilities["profile"] == "cairn-native/" + __version__
    assert citation["version"] == __version__ and citation["repository-code"].endswith("/cairn")
    assert f"CAIRN {major_minor} is a checked systems language" in card
    for module in ("bazel/MODULE.bazel", "examples/bazel/MODULE.bazel"):  # the Bazel rules carry the release too
        assert f'"rules_cairn", version = "{__version__}"' in (ROOT / module).read_text(encoding="utf-8"), module


def test_the_changelog_opens_with_this_release():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    sections = re.findall(r"^## (\d+\.\d+\.\d+)$", changelog, flags=re.M)
    assert sections and sections[0] == __version__, sections[:2]


def test_the_license_is_declared_and_shipped():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["license"] == "MIT OR Apache-2.0"
    for name in pyproject["project"]["license-files"]:
        assert (ROOT / name).read_text(encoding="utf-8").strip(), name
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "LICENSE-MIT" in readme and "LICENSE-APACHE" in readme


def test_the_development_install_carries_the_build_backend_make_wheel_needs():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = re.search(r"^wheel:\n\t(.*)$", (ROOT / "Makefile").read_text(encoding="utf-8"), flags=re.M)
    assert wheel and "--no-index" in wheel[1] and "--no-build-isolation" in wheel[1]  # offline, from this environment
    for requirement in pyproject["build-system"]["requires"]:
        assert requirement in pyproject["project"]["optional-dependencies"]["dev"], requirement


def test_the_wheel_builds_offline_and_holds_the_package_and_nothing_else(tmp_path):
    if importlib.util.find_spec("setuptools") is None:
        pytest.skip("needs the '.[dev]' install, which carries setuptools")
    tree = tmp_path / "tree"  # a copy, so the build writes nothing into the checkout
    shutil.copytree(ROOT / "src", tree / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    for name in ("pyproject.toml", "README.md", "LICENSE-MIT", "LICENSE-APACHE"):
        shutil.copy(ROOT / name, tree / name)
    built = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation", "--wheel-dir",
         str(tmp_path / "dist"), str(tree)],
        capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert built.returncode == 0, built.stdout[-2000:] + built.stderr[-2000:]
    with zipfile.ZipFile(tmp_path / "dist" / f"cairn_language-{__version__}-py3-none-any.whl") as wheel:
        names = set(wheel.namelist())
        entry_points = wheel.read(f"cairn_language-{__version__}.dist-info/entry_points.txt").decode()
    assert "cairn = cairn.cli:main" in entry_points
    for shipped in ("runtime/cairn_runtime.hpp", "std/vec.cairn", "templates/AGENTS.md", "py.typed"):
        assert "cairn/" + shipped in names, shipped
    assert any(name.startswith("cairn/targets/") for name in names)
    assert not {name.split("/")[0] for name in names} - {"cairn", f"cairn_language-{__version__}.dist-info"}

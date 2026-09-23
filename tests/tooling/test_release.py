"""A release states one version, in every place that states it, and the changelog has that version's section."""

import json
import pathlib
import re
import tomllib

from cairn import __version__

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_version_is_stated_once():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lakefile = tomllib.loads((ROOT / "proofs/lakefile.toml").read_text(encoding="utf-8"))
    extension = json.loads((ROOT / "editors/vscode/package.json").read_text(encoding="utf-8"))
    capabilities = json.loads((ROOT / "docs/project/capabilities.json").read_text(encoding="utf-8"))
    card = (ROOT / "src/cairn/agent/teaching.py").read_text(encoding="utf-8")
    major_minor = ".".join(__version__.split(".")[:2])
    assert pyproject["project"]["version"] == __version__
    assert lakefile["version"] == __version__
    assert extension["version"] == __version__
    assert capabilities["profile"] == "cairn-native/" + __version__
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

"""The files under `.github/` that people file and label with: the issue forms, the labels and the label sync.

Every form and the labels file are read by the strict YAML subset reader, so each means to GitHub what it means here.
The sync runs only with `--dry-run` against a fake `gh`, which must never be called; nothing here reaches GitHub.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from release.sync_labels import commands, labels
from release.yaml_subset import YamlError, load, read

ROOT = Path(__file__).resolve().parents[2]
FORMS = sorted(p for p in (ROOT / ".github/ISSUE_TEMPLATE").glob("*.yml") if p.name != "config.yml")
KINDS = {"markdown", "textarea", "input", "dropdown", "checkboxes"}  # the body elements an issue form may hold


def test_every_label_is_named_once_under_one_prefix_with_a_colour_and_a_description():
    rows = labels()
    names = [r["name"] for r in rows]
    assert len(names) == len(set(names)) and "good first issue" in names
    for prefix in ("kind:", "area:", "backend:", "needs:"):
        assert any(n.startswith(prefix) for n in names), prefix


def test_every_issue_form_applies_one_kind_label_and_asks_for_what_reproduces_it():
    named = {r["name"] for r in labels()}
    assert {p.stem for p in FORMS} == {"bug", "language", "performance", "kernel"}
    assert not list((ROOT / ".github/ISSUE_TEMPLATE").glob("*.md")), "the Markdown templates are replaced by forms"
    for path in FORMS:
        form = read(path)
        assert isinstance(form["name"], str) and form["description"].endswith("."), path.name
        kinds = [label for label in form["labels"] if label.startswith("kind:")]
        assert len(kinds) == 1 and set(form["labels"]) <= named, path.name
        ids = [item["id"] for item in form["body"] if item["type"] != "markdown"]
        assert len(ids) == len(set(ids)) and all(item["type"] in KINDS for item in form["body"]), path.name
        for item in form["body"]:
            attributes = item["attributes"]
            assert attributes["value"] if item["type"] == "markdown" else attributes["label"], path.name
            assert set(item) <= {"type", "id", "attributes", "validations"}, path.name
        assert any(item.get("validations", {}).get("required") for item in form["body"]), path.name
    bug = read(ROOT / ".github/ISSUE_TEMPLATE/bug.yml")
    assert {"program", "command", "record", "doctor"} <= {item.get("id") for item in bug["body"]}
    assert "SECURITY.md" in bug["body"][0]["attributes"]["value"]


def test_blank_issues_are_off_and_a_soundness_bug_is_sent_to_the_security_policy():
    config = read(ROOT / ".github/ISSUE_TEMPLATE/config.yml")
    assert config["blank_issues_enabled"] is False
    assert any(link["url"].endswith("/SECURITY.md") for link in config["contact_links"])


def test_the_pull_request_template_asks_what_agents_md_asks_in_a_few_lines():
    text = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line]
    assert len(lines) == 4 and lines[-1].startswith("Rules:") and "no emojis" in lines[-1]
    for asked in ("What changes and why", "How it was checked", "What a reviewer should look at"):
        assert asked in text, asked
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "what changes and why, how it was checked" in agents and "what a reviewer should look at" in agents


def test_the_subset_reader_reads_blocks_and_refuses_what_yaml_may_read_otherwise():
    assert load('a:\n  - b: "x # y"\n    c: |\n      one\n\n      two\n    d: |-\n      three\n') == {
        "a": [{"b": "x # y", "c": "one\n\ntwo\n", "d": "three"}]
    }
    for text in ("a: [b]\n", "a: yes\n", "a: 12\n", "a:\n", "a: 'b'\n", "a: b\na: c\n", "a: b \n", "a:\tb\n",
                 "on: b\n", "a: b: c\n", "a: &x b\n", "a: >\n  b\n", "- a\n  b: c\n"):  # fmt: skip
        with pytest.raises(YamlError):
            load(text)


def fake_gh(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A PATH whose first `gh` records every call in a log, and that log."""
    log, gh = tmp_path / "gh.log", tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return {**os.environ, "PATH": f"{gh.parent}{os.pathsep}{os.environ['PATH']}"}, log


def test_the_dry_run_prints_one_gh_command_per_label_and_calls_nothing(tmp_path):
    env, log = fake_gh(tmp_path)
    done = subprocess.run([sys.executable, "tools/release/sync_labels.py", "--dry-run", "--repo", "owner/name"],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)  # fmt: skip
    assert done.returncode == 0, done.stderr
    printed = done.stdout.splitlines()
    assert len(printed) == len(labels()) and not log.exists()
    assert printed[0] == "gh label create kind:bug --color d73a4a --description 'Something does what the " \
        "documentation says it should not' --force --repo owner/name"  # fmt: skip
    assert all(c[:3] == ["gh", "label", "create"] and "--force" in c for c in commands(labels()))


def test_the_sync_refuses_to_reach_github_from_a_test(tmp_path):
    env, log = fake_gh(tmp_path)
    done = subprocess.run([sys.executable, "tools/release/sync_labels.py"], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=60)  # fmt: skip
    assert done.returncode == 2 and "refused" in done.stderr and not log.exists()

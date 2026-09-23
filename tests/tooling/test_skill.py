"""The agent skill under skills/cairn and the Claude Code plugin around it: generated, well formed, and true."""

import json
import pathlib
import re

from cairn import __version__
from cairn.agent import skill
from cairn.compiler.cairnc import compile_source

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "cairn"
STANDARD = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}  # the Agent Skills fields


def frontmatter(text: str) -> dict[str, str]:
    head = text.split("---\n")[1]
    top = {}
    for line in head.splitlines():
        if line and not line.startswith(" "):
            key, _, value = line.partition(":")
            top[key] = json.loads(value) if value.strip().startswith('"') else value.strip()
    return top


def test_the_committed_skill_is_a_fresh_render():
    assert skill.main(["--check"]) == 0, "run `make editors`"


def test_the_skill_uses_only_the_agent_skills_fields_and_their_limits():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    top = frontmatter(text)
    assert set(top) <= STANDARD, set(top) - STANDARD
    assert top["name"] == SKILL.name and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", top["name"])
    assert 0 < len(top["description"]) <= 1024
    assert len(top["compatibility"]) <= 500
    assert len(text.splitlines()) < 500  # the body is read whole whenever the skill fires


def test_every_link_in_the_skill_reaches_a_file():
    for page in SKILL.rglob("*.md"):
        prose = re.sub(r"```.*?```|`[^`]*`", "", page.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", prose):
            if not target.startswith("http"):
                assert (page.parent / target).exists(), (page, target)


def test_every_card_and_every_code_the_cards_name_is_in_the_skill():
    files = skill.render()
    for name in skill.CARDS:
        assert name in skill.CORE or f"cards/{name}.md" in files
    for code in skill.codes():
        assert f"`{code}`" in files["codes.md"]


def test_the_example_in_the_skill_compiles():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```cairn\n(.*?)```", text, re.S)
    assert blocks
    for block in blocks:
        compile_source(block)


def test_the_plugin_and_its_marketplace_state_this_release():
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "cairn" and plugin["version"] == __version__
    [entry] = market["plugins"]
    assert entry["name"] == plugin["name"] and entry["version"] == __version__ and entry["source"] == "./"
    server = plugin["lspServers"]["cairn"]
    assert server["command"] == "${CLAUDE_PLUGIN_ROOT}/bin/cairn" and server["args"] == ["lsp"]
    assert (ROOT / "bin/cairn").stat().st_mode & 0o111  # the plugin's bin/ goes on PATH as it is

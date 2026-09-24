"""The agent skill under skills/cairn and the Claude Code plugin around it: generated, well formed, and true."""

import json
import pathlib
import re

from cairn import __version__
from cairn.agent import skill
from cairn.agent.teaching import CORE, OWNER, every_card
from cairn.compiler.cairnc import Diagnostic, compile_source

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


def test_every_card_and_every_code_is_in_the_skill_file_that_holds_its_card():
    files = skill.render()
    for name in every_card():
        assert name in CORE or f"cards/{name}.md" in files
    for code, card in OWNER.items():
        assert code in files["SKILL.md" if card in CORE else f"cards/{card}.md"], (code, card)


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


def test_the_plugin_runs_the_mcp_server_and_names_its_eval_suite():
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert plugin["mcpServers"] == {"cairn": {"command": "${CLAUDE_PLUGIN_ROOT}/bin/cairn", "args": ["mcp"]}}
    assert (ROOT / plugin["experimental"]["evals"]).is_dir()


def front(text: str) -> dict[str, str]:
    """The flat `key: value` lines of a Markdown file's frontmatter."""
    head = text.split("---\n")[1]
    return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in head.splitlines())}


def test_each_eval_case_uses_read_only_tools_and_its_program_is_refused_with_the_code_it_grades():
    """The suite is not run here (it spends a model's usage); what the suite claims about the compiler is checked."""
    cases = sorted(p.parent for p in (ROOT / "bench/skill").glob("*/prompt.md"))
    assert 5 <= len(cases) <= 6
    for case in cases:
        prompt = (case / "prompt.md").read_text(encoding="utf-8")
        assert front(prompt)["allowed_tools"] == "[Read, Glob, Grep, Skill]"  # no Bash: no sandbox on this machine
        graders = {p.stem: front(p.read_text(encoding="utf-8")) for p in (case / "graders").glob("*.md")}
        assert all(g["name"] == stem and g["type"] in {"regex", "llm", "tool_used"} for stem, g in graders.items())
        assert graders["skill"]["tool"] == "Skill"
        if "code" not in graders:
            continue
        [program] = re.findall(r"```cairn\n(.*?)```", prompt, re.S)
        code = re.fullmatch(r"'(E-[A-Z-]+)\\b'", graders["code"]["pattern"]).group(1)
        try:
            compile_source(program)
        except Diagnostic as error:
            assert error.data["code"] == code, case.name
        else:
            raise AssertionError(f"{case.name}: the program checks, but the case grades a refusal")


def test_the_loop_keeps_a_design_cairn_cannot_say_through_a_foreign_implementation():
    loop = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("## Loop")[1].split("## Core rules")[0]
    assert "foreign implementation" in loop and "cairn validate" in loop and "cards/foreign.md" in loop

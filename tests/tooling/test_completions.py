"""`cairn completions bash|zsh`: every command, option and choice the parser knows is in both scripts, both scripts
are valid in their shell, and the bash one completes what a person types when bash runs it."""

import shutil
import subprocess

import pytest

from cairn.cli import main, parser
from cairn.editor.shells import commands, options, positionals


def script(shell, capsys):
    assert main(["completions", shell]) == 0
    return capsys.readouterr().out


def section(text: str, name: str) -> str:
    """The line of a script's per-command case that belongs to `name`."""
    return next(line for line in text.splitlines() if line.strip().startswith(f"{name})"))


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_every_command_option_and_choice_is_offered(shell, capsys):
    text = script(shell, capsys)
    table = commands(parser())
    assert {"check", "run", "test", "new", "diff", "completions"} <= set(table)
    for name, (_, sub) in table.items():
        line = section(text, name)
        for a in options(sub):
            for flag in a.option_strings:
                assert flag in line, (shell, name, flag)
            for choice in a.choices or ():
                assert str(choice) in text, (shell, name, choice)
        for a in positionals(sub):
            for choice in a.choices or ():
                assert str(choice) in line, (shell, name, choice)


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_the_script_is_valid_in_its_shell(shell, capsys, tmp_path):
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed")
    path = tmp_path / f"cairn.{shell}"
    path.write_text(script(shell, capsys))
    done = subprocess.run([shell, "-n", str(path)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr


def completed(path, *typed):
    """What bash offers for the command line `typed`, whose last word is the one being completed."""
    words = " ".join(f"'{w}'" for w in typed)
    probe = f'source "{path}"; COMP_WORDS=({words}); COMP_CWORD={len(typed) - 1}; _cairn; printf "%s\\n" "${{COMPREPLY[@]}}"'
    done = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return [line for line in done.stdout.splitlines() if line]


def test_bash_completes_commands_options_and_choices(capsys, tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    path = tmp_path / "cairn.bash"
    path.write_text(script("bash", capsys))
    assert completed(path, "cairn", "che") == ["check"]
    assert completed(path, "cairn", "check", "--gen") == ["--generics"]
    assert completed(path, "cairn", "new", "demo", "--template", "") == ["default", "cli", "lib", "parallel", "service"]
    assert completed(path, "cairn", "check", "--format", "j") == ["json"]
    assert completed(path, "cairn", "completions", "") == ["bash", "zsh"]
    assert "--test" in completed(path, "cairn", "test", "--")

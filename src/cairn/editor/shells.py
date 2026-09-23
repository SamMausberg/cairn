"""`cairn completions bash|zsh`: a completion script written from the command line's own parser, so every command,
option and choice the parser knows is offered, and nothing it does not."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def commands(p: argparse.ArgumentParser) -> dict[str, tuple[str, argparse.ArgumentParser]]:
    """Each subcommand with its one-line help and its own parser, in the order the parser declares them."""
    sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help or "" for a in sub._choices_actions}
    return {name: (helps.get(name, ""), parser) for name, parser in sub.choices.items()}


def options(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [a for a in parser._actions if a.option_strings]


def positionals(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [a for a in parser._actions if not a.option_strings]


def words(values) -> str:
    return " ".join(str(v) for v in values)


def bash(p: argparse.ArgumentParser) -> str:
    table = commands(p)
    values, per = [], []
    for name, (_, parser) in table.items():
        for a in options(parser):
            if a.choices:
                values += [f'    "{name} {o}") words="{words(a.choices)}" ;;' for o in a.option_strings]
        chosen = next((a.choices for a in positionals(parser) if a.choices), None)
        flags = words(o for a in options(parser) for o in a.option_strings)
        per.append(f'    {name}) words="{flags}"' + (f'; given="{words(chosen)}"' if chosen else "") + " ;;")
    return "\n".join(
        [
            "# bash completion for cairn, written by `cairn completions bash` from the command line's own parser.",
            "# Load it with: source <(cairn completions bash)",
            "_cairn() {",
            '  local cur=${COMP_WORDS[COMP_CWORD]} prev=${COMP_WORDS[COMP_CWORD-1]} words="" given=""',
            "  if (( COMP_CWORD == 1 )); then",
            f'    COMPREPLY=($(compgen -W "{words(table)} --help --version" -- "$cur"))',
            "    return",
            "  fi",
            '  case "${COMP_WORDS[1]} $prev" in',
            *values,
            "  esac",
            '  if [[ -n $words ]]; then COMPREPLY=($(compgen -W "$words" -- "$cur")); return; fi',
            '  case "${COMP_WORDS[1]}" in',
            *per,
            "  esac",
            '  if [[ $cur == -* ]]; then COMPREPLY=($(compgen -W "$words" -- "$cur"))',
            '  elif [[ -n $given ]]; then COMPREPLY=($(compgen -W "$given" -- "$cur"))',
            '  else COMPREPLY=($(compgen -f -- "$cur")); fi',
            "}",
            "complete -o filenames -o bashdefault -F _cairn cairn",
            "",
        ]
    )


def said(text: str) -> str:
    """A help text as zsh's `_arguments` shows it: its first clause, with nothing zsh would read as syntax."""
    first = re.split(r"(?<=[.;])\s", text or "", maxsplit=1)[0].rstrip(".;:")
    return re.sub(r"[\[\]:'`\\$]", "", first)[:72]


def zsh_value(a: argparse.Action) -> str:
    """What an option's value, or a positional, completes to: its choices, a file, or anything."""
    if a.choices:
        return f":{a.dest}:({words(a.choices)})"
    if a.type is Path or a.dest in {"path", "reference", "candidate", "paths", "directory"}:
        return f":{a.dest}:_files"
    return f":{a.dest}:"


def zsh(p: argparse.ArgumentParser) -> str:
    table = commands(p)
    listed = [f"    '{name}:{said(help)}'" for name, (help, _) in table.items()]
    cases = []
    for name, (_, parser) in table.items():
        specs = []
        for a in options(parser):
            repeat = "*" if isinstance(a, argparse._AppendAction) else ""
            value = "" if a.nargs == 0 else zsh_value(a)
            specs += [f"'{repeat}{o}[{said(a.help)}]{value}'" for o in a.option_strings]
        for k, a in enumerate(positionals(parser), 1):
            specs.append(f"'{'*' if a.nargs in {'+', '*'} else k}{zsh_value(a)}'")
        cases.append(f"    {name}) _arguments -s {' '.join(specs)} ;;")
    return "\n".join(
        [
            "#compdef cairn",
            "# zsh completion for cairn, written by `cairn completions zsh` from the command line's own parser.",
            "# Put it on $fpath as _cairn, or load it with: source <(cairn completions zsh)",
            "_cairn() {",
            "  local -a commands",
            "  commands=(",
            *listed,
            "  )",
            "  if (( CURRENT == 2 )); then",
            "    _describe -t commands 'cairn command' commands",
            "    return",
            "  fi",
            "  local command=$words[2]",
            "  shift words",
            "  (( CURRENT-- ))",
            "  case $command in",
            *cases,
            "  esac",
            "}",
            'if (( $+functions[compdef] )); then compdef _cairn cairn; else _cairn "$@"; fi',
            "",
        ]
    )


def completion_script(p: argparse.ArgumentParser, shell: str) -> str:
    return bash(p) if shell == "bash" else zsh(p)

"""The agent skill: `skills/cairn/`, written from what the compiler already holds.

The skill follows the Agent Skills format (a `SKILL.md` with `name` and `description`, then files it points to), so
Claude Code, the Claude apps and other agents that read that format load it the same way. It costs an agent almost
nothing until it is used: only the description is listed. `SKILL.md` then gives the loop, the three rule cards
every packet carries, an example that compiles, and an index; a card, the table of refusals or a chapter of `docs/`
is read only when the program needs it.

Nothing here is written by hand twice. The cards and the words that select them come from `teaching.py`, the fixes
from `diagnostics.py`, the commands from the command line's parser. `python -m cairn.agent.skill` rewrites the
directory, `--check` fails while a committed file differs from a fresh render, and `make editors` runs it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..version import __version__
from .diagnostics import HINTS
from .teaching import CARDS, CODES, CORE, OWNER, TOOL_CARDS, every_card, triggers

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "skills" / "cairn"
REPOSITORY = "https://github.com/SamMausberg/cairn"

DESCRIPTION = (
    "Write, check, test and tune programs in CAIRN, a checked systems language that compiles to C++20 for the CPU "
    "and CUDA for NVIDIA GPUs. Use when a task involves .cairn files, a cairn.toml project or the cairn command, or "
    "asks for a CAIRN program. Gives the rules the compiler enforces (checked arithmetic, owners that move, "
    "second-class borrows, leases, race-free lanes, effect rows), the fix for each diagnostic code, and the check, "
    "test and run loop."
)
COMPATIBILITY = (
    "Linux on x86-64 or AArch64 with Python 3.11+ and Clang or GCC with C++20. Needs the cairn command: bin/cairn "
    "of a CAIRN checkout, pip install of it, or the Claude Code plugin, which puts it on PATH. nvcc for device code."
)

EXAMPLE = """\
// Two tasks fill the two halves of an array, then main adds it up.
fn fill(n:usize, out:rw<u64>[n], start:u64) {
  for i in 0..n { out[i] = start + u64(i); }     // checked: an overflow traps
}

fn halves(n:usize, data:rw<u64>[n]) {
  let mid = n / 2;
  let left = spawn fill(data[0..mid], 0);        // left holds data[0..mid] until wait
  let right = spawn fill(data[mid..n], u64(mid));
  wait(left);
  wait(right);
}

test halves_count_up {
  let mut data = Buf[u64](10);
  halves(data);                                  // n is len(data)
  assert_eq(data[9], 9);
}

fn main() -> i32 {
  let mut data = Buf[u64](1000);                 // an owner, released at scope exit
  halves(data);
  let total = reduce + for i in len(data) yield data[i];
  println("total = ", total);
  return 0;
}
"""

LOOP = """\
1. Write the program. One file with `fn main() -> i32` runs as is; `cairn new NAME` makes a project (a data-only `cairn.toml`, `src/`, a test).
2. Run `cairn check PATH --format json` until it prints `"status": "typed"`. A refusal names a `code`, a line and a column, and `further` lists every other refusal the check could judge on its own, so fix them all before checking again. Look each code up in [codes.md](codes.md), read the card it names, and change the code the rule is about. Never widen an effect ceiling, turn `ro` into `rw`, add `unsafe` or delete a check to get past a refusal.
3. `cairn test PATH` runs every `test` block in a process of its own; `cairn run PATH` builds and runs, with arguments after `--`.
4. Before tuning, read the costs instead of guessing: `cairn doc PATH` prints each signature with its effect row, `cairn explain PATH` the guards, allocations and waits left at run time, `cairn predict PATH` a time per function from a machine profile, without running anything.
5. To make a function faster, leave it as the reference and write an implementation beside it: `fn g(...) implements f when COND { }`, with natural parameters to search if useful. `cairn validate PATH --symbol g` tests it against the reference on generated boundary inputs, `cairn tune PATH --symbol f` searches plans and validated implementations within its budgets and `--write` selects the winner (`plan f use g;`). Never edit the reference, a tolerance or a test to make an implementation pass.
6. An agent without a shell gets the same through `cairn mcp`: `check`, `state`, and the edit, plan and implementation sessions, which write an admitted change back to its files."""

AVOID = """\
- Habits from Rust or C++: there are no `&`/`&mut` references, lifetimes, `::` paths, `as` casts (write `u64(x)`) or tail-expression returns. `impl` is only `impl Trait for T`; `value.f(args)` calls a plain `fn f(v, args)` from the type's module. Text is `ro<u8>[n]` or `Vec[u8]`; there is no `String`.
- Invented libraries: only what a file declares, the builtins the cards name and the `std.*` modules exist; `cairn doc --std` lists every `std` signature.
- Guessing a fix: each diagnostic code has one rule behind it, and its card says what that rule accepts."""


def commands() -> list[tuple[str, str]]:
    from ..commands import parser

    sub = next(a for a in parser()._actions if isinstance(a, argparse._SubParsersAction))
    return [(c.dest, " ".join((c.help or "").split())) for c in sub._choices_actions]


def paragraphs(text: str) -> str:
    """A card, one paragraph per line, as a text block: its `Buf[u64](n)` is code, never a Markdown link."""
    return "```text\n" + "\n\n".join(line.strip() for line in text.strip().splitlines() if line.strip()) + "\n```"


def card_file(name: str, words: list[str]) -> str:
    uses = ", ".join(f"`{w}`" for w in words) if words else "the forms it describes"
    said = f"Sent to an agent when the program uses {uses}." if name in CARDS else "A refusal names this card."
    if listed := CODES[name].split():
        said += " Codes: " + ", ".join(f"`{c}`" for c in sorted(listed)) + "."
    return f"# The {name} card\n\n{said}\n\n{paragraphs(every_card()[name])}\n"


def codes_file() -> str:
    rows = ["# Refusals", "", "Each code the compiler, the hosts and `cairn` can name, with the card that states its "
            "rule. The fix is the smallest change that keeps the program's meaning.", "",
            "| Code | Card | Fix |", "|---|---|---|"]  # fmt: skip
    for code, card in sorted(OWNER.items()):
        where = f"[{card}](cards/{card}.md)" if card not in CORE else f"[{card}](SKILL.md#core-rules)"
        rows.append(f"| `{code}` | {where} | {HINTS.get(code, '')} |")
    rows += ["", "Codes are stable across releases."]
    return "\n".join(rows) + "\n"


def skill_file() -> str:
    found = triggers()
    index = ["| Card | Read it when the program uses |", "|---|---|"]
    for name in CARDS:
        if name not in CORE:
            words = ", ".join(f"`{w}`" for w in found[name]) or "the forms it describes"
            index.append(f"| [{name}](cards/{name}.md) | {words} |")
    table = ["| Command | What it does |", "|---|---|"] + [f"| `cairn {c}` | {h} |" for c, h in commands()]
    core = "\n\n".join(paragraphs(CARDS[name]) for name in CORE)
    core += "\n\nTheir codes: " + "; ".join(f"{n} " + ", ".join(sorted(CODES[n].split())) for n in CORE) + "."
    front = "\n".join([
        "---", "name: cairn", f"description: {json.dumps(DESCRIPTION)}", "license: MIT OR Apache-2.0",
        f"compatibility: {json.dumps(COMPATIBILITY)}", "metadata:", f'  version: "{__version__}"',
        '  generated-by: "python -m cairn.agent.skill"', "---",
    ])  # fmt: skip
    body = [
        "# CAIRN", "",
        "CAIRN is its own language, not Rust, C++ or Python with different spelling. The compiler refuses races, "
        "uses of moved values and unchecked effects before anything runs, traps on overflow and out-of-bounds "
        "access, and infers what each function costs. Read the core rules below before writing code, and a card "
        "before using what it covers. This file's directory holds `codes.md` and `cards/`; in Claude Code it is "
        "`${CLAUDE_SKILL_DIR}`, so a card is `${CLAUDE_SKILL_DIR}/cards/owners.md`.", "",
        "## Loop", "", LOOP, "",
        "## Core rules", "", core, "",
        "## A program", "", "Tasks, leases, a test block and a checked reduction; `cairn run` prints `total = 499500`.",
        "", "```cairn", EXAMPLE.rstrip(), "```", "",
        "## Cards", "", "Each card states one part of the language and the codes of its rules. The compiler sends "
        "the same cards to an agent it hosts, picked by the words below.", "", *index, "",
        "A refusal from a host or the command line names one of " + ", ".join(f"[{n}](cards/{n}.md)" for n in TOOL_CARDS)
        + ".", "",
        "## Mistakes that cost the most", "", AVOID, "",
        "## Commands", "", "A command that reports takes `--format json`, the default when its output is piped.", "", *table, "",
        "## More", "",
        "The reference is the `docs/` directory of the CAIRN repository: `language.md`, `memory.md`, "
        "`abstractions.md`, `concurrency.md`, `devices.md` and `numerics.md` for the language, `library.md` and `std/` for the "
        "standard library, `tools.md` for the commands, `agents.md` for the edit host. From a checkout or the "
        f"Claude Code plugin it is `${{CLAUDE_SKILL_DIR}}/../../docs/`; otherwise {REPOSITORY}/tree/main/docs. Every "
        "example there compiles, so copy from it rather than from memory of another language.",
    ]  # fmt: skip
    return front + "\n\n" + "\n".join(body) + "\n"


def render() -> dict[str, str]:
    """Every file of the skill, by its path under `skills/cairn/`."""
    found = triggers()
    files = {"SKILL.md": skill_file(), "codes.md": codes_file()}
    files |= {f"cards/{n}.md": card_file(n, found.get(n, [])) for n in every_card() if n not in CORE}
    return files


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    files = render()
    stale = sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*.md") if p.relative_to(OUT).as_posix()
                   not in files) if OUT.exists() else []  # fmt: skip
    changed = [name for name, text in files.items() if not (OUT / name).exists() or (OUT / name).read_text() != text]
    if check:
        for name in changed + stale:
            print(f"skills/cairn/{name} differs from a fresh render; run `make editors`.", file=sys.stderr)
        return 1 if changed or stale else 0
    for name in stale:
        (OUT / name).unlink()
    for name, text in files.items():
        (OUT / name).parent.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

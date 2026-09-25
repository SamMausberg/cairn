"""The agent skill: `skills/cairn/`, written from what the compiler already holds.

The skill follows the Agent Skills format (a `SKILL.md` with `name` and `description`, then files it points to), so
Claude Code, the Claude apps and other agents that read that format load it the same way. It costs an agent almost
nothing until it is used: only the description is listed. `SKILL.md` then gives the loop, the three rule cards
every packet carries, an example that compiles, and an index; a card or a chapter of `docs/` is read only when the
program needs it. What a refusal teaches is not repeated: the refusal names its card and carries its fix, and
`cairn rules` prints any card, so the skill holds each rule once and every command is in `cairn --help`.

Nothing here is written by hand twice. The cards, their codes and the words that select them come from
`teaching.py`. `python -m cairn.agent.skill` rewrites the directory, `--check` fails while a committed file differs
from a fresh render, and `make editors` runs it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..version import __version__
from .teaching import CARDS, CODES, CORE, TOOL_CARDS, every_card, triggers

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
// Reads integers from standard input; prints how many, and how many are negative, counted on two tasks.
import std.core (Option, Result);
import std.io as io;
import std.text as text;
import std.vec (Vec);

fn negatives(n:usize, xs:ro<i64>[n]) -> usize {
  let mut k:usize = 0;                              // an unannotated 0 would be a u64
  for x in xs { if x < 0 { k += 1; } }
  return k;
}

fn main() -> i32 {
  let mut input = vec.new[u8]();                    // a growable owner, freed at scope exit
  stack chunk:u8[4096] = zeroed;
  let mut more = true;
  while more {
    match io.read_stdin(4096, chunk) {
      Ok(got) => { if got == 0 { more = false; } else { vec.extend_from(input, got, chunk[0..got]); } }
      Err(_) => more = false;
    }
  }
  let mut values = vec.new[i64]();
  let mut lo:usize = 0;
  while lo < input.len {
    let mut hi = lo;
    while hi < input.len && input.data[hi] > 32 { hi += 1; }
    if hi > lo {
      match text.parse_i64(hi - lo, input.data[lo..hi]) {   // a part is written where it is passed
        Ok(v) => vec.push(values, v);
        Err(_) => return 1;
      }
    }
    lo = hi + 1;
  }
  let n = values.len;
  let halves = Group[usize](2);
  spawn negatives(values.data[0..n / 2]) into halves;   // each task reads its own part; n is len of it
  spawn negatives(values.data[n / 2..n]) into halves;
  let first = collect(halves);                      // a call that joins or writes is its own statement
  let second = collect(halves);
  wait(halves);
  println("count ", n, " negative ", first + second);
  return 0;
}
"""

LOOP = """\
1. Write the program. One file with `fn main() -> i32` runs as is; `cairn new NAME` makes a project (a data-only `cairn.toml`, `src/`, a test).
2. Run `cairn check PATH --format json` until it prints `"status": "typed"`. A refusal gives a `code`, a line and a column, the `card` that states its rule (`cairn rules CODE` prints it) and, when the compiler can state one, a `repair_hint`; `further` lists every other refusal the check could judge on its own, so fix them all before checking again. Change the code the rule is about. Never widen an effect ceiling, turn `ro` into `rw`, add `unsafe` or delete a check to get past a refusal.
3. `cairn run PATH < input` builds and runs, with arguments after `--`; `--sanitize address` or `--sanitize thread` runs the build a sanitizer checks. `cairn test PATH` runs every `test` block in a process of its own.
4. For speed, read costs instead of guessing (`cairn explain PATH`, `cairn predict PATH`), then leave the function as the reference and write an implementation beside it, `fn g(...) implements f when COND { }`, which `cairn validate` holds to the reference and `cairn tune` selects ([implementations](cards/implementations.md)); a design CAIRN cannot say is a foreign implementation ([foreign](cards/foreign.md)), never a slower or narrower workaround. `cairn mcp` serves the same to an agent without a shell.

`cairn --help` lists every command, and a command that reports prints JSON when piped."""

AVOID = """\
- Habits from Rust or C++: no `&`/`&mut`, lifetimes, `::` paths, `as` casts (write `u64(x)`), tuples (a `struct`), tail-expression returns, or `if` and `match` as values (`let mut x = b; if c { x = a; }`). `impl` is only `impl Trait for T`; `value.f(args)` calls a plain `fn f(v, args)` from the type's module. Text is `ro<u8>[n]` or `Vec[u8]`, never a `String`.
- An integer literal is a `u64` unless something expects another type: `let mut i:usize = 0;` for an index. A signed minimum is a literal, `-9223372036854775808`.
- A `Buf[T](n)` is `len(b)` long, which the checker does not tie to `n`: pass `f(b)` and the call supplies `len(b)`, or pass the part `b[0..n]`. A part `xs[lo..hi]` is written only as a call's argument. A `Vec`'s length is `v.len` and its elements `v.data[i]`.
- Invented libraries: only what a file declares, the builtins the cards name and the `std.*` modules exist; `cairn doc --std --module std.text` prints one module's signatures.
- Guessing a fix: each diagnostic code has one rule behind it, and its card says what that rule accepts."""


def card_link(name: str) -> str:
    """Where a person or an editor reads a card: its page in this checkout's skill, else in the repository's."""
    page, anchor = ("SKILL.md", "#core-rules") if name in CORE else (f"cards/{name}.md", "")
    here = OUT / page
    return (here.as_uri() if here.is_file() else f"{REPOSITORY}/blob/main/skills/cairn/{page}") + anchor


def paragraphs(text: str) -> str:
    """A card, one paragraph per line, as a text block: its `Buf[u64](n)` is code, never a Markdown link."""
    return "```text\n" + "\n\n".join(line.strip() for line in text.strip().splitlines() if line.strip()) + "\n```"


SELECTED = {"views": ["ro", "rw"], "records": ["struct", "enum"], "sums": ["enum"]}  # what a host's packet also reads


def selected_by(name: str, found: dict[str, list[str]]) -> str:
    return " ".join(SELECTED.get(name, []) + found.get(name, []))


def card_file(name: str, words: str) -> str:
    said = f"Selected by {words}." if name in CARDS else "A host or the command line names this card in a refusal."
    if listed := CODES[name].split():
        said += " Codes: " + " ".join(sorted(listed)) + "."
    return f"# The {name} card\n\n{said}\n\n{paragraphs(every_card()[name])}\n"


def skill_file() -> str:
    found = triggers()
    index = [f"- {n}: {selected_by(n, found)}" for n in CARDS if n not in CORE]
    core = "\n\n".join(paragraphs(CARDS[name]) for name in CORE)
    core += "\n\nTheir codes: " + "; ".join(f"{n} " + " ".join(sorted(CODES[n].split())) for n in CORE) + "."
    front = "\n".join([
        "---", "name: cairn", f"description: {json.dumps(DESCRIPTION)}", "license: MIT OR Apache-2.0",
        f"compatibility: {json.dumps(COMPATIBILITY)}", "metadata:", f'  version: "{__version__}"',
        '  generated-by: "python -m cairn.agent.skill"', "---",
    ])  # fmt: skip
    body = [
        "# CAIRN", "",
        "CAIRN is its own language, not Rust, C++ or Python with different spelling. The compiler refuses races, "
        "uses of moved values and unchecked effects before anything runs, and traps on overflow and out-of-bounds "
        "access. Read the core rules below before writing code, and a card before using what it covers. A card is "
        "`cards/NAME.md` beside this file (`${CLAUDE_SKILL_DIR}/cards/owners.md` in Claude Code), and `cairn rules "
        "NAME` prints it.", "",
        "## Loop", "", LOOP, "",
        "## Core rules", "", core, "",
        "## A program", "", "Standard input, integers, a Vec and two tasks; `printf '3 -1 4 -9' | cairn run .` prints "
        "`count 4 negative 2`.",
        "", "```cairn", EXAMPLE.rstrip(), "```", "",
        "## Cards", "", "Each card, `cards/NAME.md`, states one part of the language and the codes of its rules. A host "
        "sends an agent the cards its program's words select, below, and `cairn rules FILE` names them.", "", *index,
        "", "A refusal from a host or the command line may name " + ", ".join(TOOL_CARDS) + ".", "",
        "## Mistakes that cost the most", "", AVOID, "",
        "## More", "",
        "The reference is `docs/` of the CAIRN repository, `${CLAUDE_SKILL_DIR}/../../docs/` from a checkout or the "
        f"Claude Code plugin, else {REPOSITORY}/tree/main/docs: `language.md`, `memory.md`, `abstractions.md`, "
        "`concurrency.md`, `devices.md` and `numerics.md` for the language, `library.md` for the standard library, "
        "`tools.md` for the commands, `agents.md` for the hosts. Every example there compiles, so copy from it rather "
        "than from memory of another language.",
    ]  # fmt: skip
    return front + "\n\n" + "\n".join(body) + "\n"


def render() -> dict[str, str]:
    """Every file of the skill, by its path under `skills/cairn/`."""
    found = triggers()
    files = {"SKILL.md": skill_file()}
    files |= {f"cards/{n}.md": card_file(n, selected_by(n, found)) for n in every_card() if n not in CORE}
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

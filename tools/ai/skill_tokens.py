#!/usr/bin/env python3
"""What an agent reads of the CAIRN skill: each file of `skills/cairn/`, and what writing the tour takes.

`SKILL.md` is read whenever the skill fires, and a card when the program uses what it covers. The tour's cost is
`SKILL.md` and the cards the twelve programs of `docs/guide.md` select (`teaching.select_cards`, with views, records
and sums found from the program's own tokens), each card read once; `per_program` is the same for an agent that
writes one program alone. `card_texts` is every rule card as the hosts send it in a packet.

Tokens are tiktoken's `o200k_base` when the package and its cached vocabulary are present (`/usr/bin/python3` here),
else UTF-8 bytes. Neither is Claude's tokenizer, and a smaller count is not evidence that a model does better.

    python3 tools/ai/skill_tokens.py              # this checkout
    python3 tools/ai/skill_tokens.py --root DIR   # another tree, such as `git archive COMMIT | tar -x -C DIR`
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from support import tokenizer

TOUR = re.compile(r"^### (\d+\. [^\n]+)\n.*?```cairn\n(.*?)```", re.S | re.M)


def measure(root: Path) -> dict:
    sys.path.insert(0, str(root / "src"))  # the tree measured selects its own cards
    from cairn.agent.teaching import CARDS, select_cards
    from cairn.compiler.lexing import lex

    unit, count = tokenizer() or ("utf8_bytes", lambda text: len(text.encode("utf-8")))
    skill = root / "skills" / "cairn"
    files = {p.relative_to(skill).as_posix(): count(p.read_text(encoding="utf-8")) for p in sorted(skill.rglob("*.md"))}
    guide = (root / "docs" / "guide.md").read_text(encoding="utf-8")
    tour = guide.split("\n## The tour in twelve programs\n")[1].split("\n## Where to go next\n")[0]

    def read(source: str) -> list[str]:
        words = {t.s for t in lex(source)}
        chosen = select_cards(source, has_views=bool(words & {"ro", "rw"}), has_records="struct" in words,
                              has_sums="enum" in words)  # fmt: skip
        return sorted(n for n in chosen if f"cards/{n}.md" in files)  # the core cards are SKILL.md's

    per_program = {}
    for title, source in TOUR.findall(tour):
        cards = read(source)
        per_program[title] = {"cards": cards, "tokens": files["SKILL.md"] + sum(files[f"cards/{n}.md"] for n in cards)}
    union = sorted({n for row in per_program.values() for n in row["cards"]})
    return {
        "unit": unit,
        "files": files,
        "skill_total": sum(files.values()),
        "cards_total": sum(v for k, v in files.items() if k.startswith("cards/")),
        "card_texts": count("\n\n".join(CARDS.values())),
        "tour": {
            "programs": len(per_program),
            "cards": union,
            "tokens": files["SKILL.md"] + sum(files[f"cards/{n}.md"] for n in union),
            "per_program": per_program,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="the tree to measure (default: this checkout)")
    parser.add_argument("--output", type=Path, help="also write the record here")
    a = parser.parse_args()
    record = measure(a.root.resolve())
    text = json.dumps(record, indent=2) + "\n"
    if a.output:
        a.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

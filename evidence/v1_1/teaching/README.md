# What an agent reads to learn CAIRN, in tokens

The preregistered benchmark found that CAIRN subjects used 11.6 times the tokens of C++ subjects, most of it reading the documentation ([v1_0/ai_benchmark](../../v1_0/ai_benchmark/RESULTS.md)). This record counts what the agent skill asks an agent to read, before and after the work that made refusals carry their rule card and fix. It counts tokens only: no model ran, and a smaller count is not evidence that a model solves more, repairs faster or spends less.

## What ran

`tools/ai/skill_tokens.py` counts each file of `skills/cairn/`, and the tour: what an agent reads to write the twelve programs of [docs/guide.md](../../../docs/guide.md#the-tour-in-twelve-programs). That is `SKILL.md` and the cards the programs select, each card read once, with `per_program` the same for an agent that writes one program alone. A program selects the cards `teaching.select_cards` sends a host's agent for it, with views, records and sums found from its own words. It also counts every rule card's text as a host sends it in a packet.

Tokens are tiktoken 0.12.0's `o200k_base`, under `/usr/bin/python3` (Python 3.12.3) on the owner's x86-64 WSL2 machine, on 24 September 2026. `o200k_base` is one real BPE vocabulary, not Claude's tokenizer.

```sh
git archive 93dadac | tar -x -C before
python3 tools/ai/skill_tokens.py --root before --output evidence/v1_1/teaching/before.json
python3 tools/ai/skill_tokens.py --output evidence/v1_1/teaching/after.json
```

`93dadac` is main before this work. `after.json` is the tree of the commit that last wrote it, where the loop in `SKILL.md` also says that a check reports every refusal it can judge on its own.

## Counts

| What is read | Before | After | Change |
|---|---|---|---|
| `SKILL.md`, read whenever the skill fires | 3,381 | 2,645 | -21.8% |
| the tour: `SKILL.md` and thirteen cards, each once | 7,657 | 7,022 | -8.3% |
| one tour program alone, the mean of twelve | 4,350 | 3,506 | -19.4% |
| `codes.md` | 2,726 | removed | |
| every file of the skill | 15,927 | 13,792 | -13.4% |
| every card's text as a host sends it | 8,868 | 8,889 | +0.2% |

| Tour program | Before | After |
|---|---|---|
| 1. Values, checked arithmetic, explicit conversions | 3,381 | 2,645 |
| 2. Views | 4,032 | 3,075 |
| 3. Records, sums, `match` and `try` | 4,136 | 3,189 |
| 4. Generics | 4,464 | 3,622 |
| 5. Traits, static and dynamic | 4,223 | 3,557 |
| 6. Owners | 4,472 | 3,515 |
| 7. Closures | 4,320 | 3,590 |
| 8. Tasks | 4,586 | 3,834 |
| 9. Lanes | 5,069 | 4,341 |
| 10. Generators | 5,262 | 4,342 |
| 11. Modules and the library | 4,447 | 3,498 |
| 12. The foreign boundary | 3,809 | 2,858 |

## What changed

The skill no longer repeats what a refusal now says. Every refusal names the card that states its rule and carries the fix the compiler can state, and `cairn rules` prints any card, so `codes.md`, a table of every code with its card and fix, is gone, and each card lists its own codes. The table of every command, 811 tokens of `SKILL.md`, is `cairn --help`. The card index is one line a card.

The closures card was sent for any program with `||` or `|` in it, which is most programs; it is now selected by a closure argument, a function type or `dyn`. That is most of the drop in the twelve single programs besides `SKILL.md`.

No rule, limit or code was taken out of a card. The cards grew by six, for what the hosts, the command line and the compiler's limits refuse, and every card's text grew by the few rules that no card stated before, such as `const`; a card sent in a packet is 0.2% larger. The six new cards are not read to write a program.

## What this does not show

Whether an agent reads less, repairs faster or solves more with the new refusals is untested: that needs sessions of a model, and none ran. The tour counts what an agent that follows the skill would read, not what a session read. The equal-budget benchmark was not rerun.

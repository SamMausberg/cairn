# What an agent reads to find a library function

What was run to compare the size of `cairn find`'s answer with what the 1.1 evaluation's CAIRN subjects read to learn the same fact. This measures the size of an answer. No model ran, and nothing here says that an agent does better with `cairn find`: a subject given it might ask other questions, ask them differently, or read the same files anyway.

Measured on 2026-09-25 on an AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2 (Linux 6.18), Python 3.12.3, from the branch that adds `cairn find`. Tokens are tiktoken's `o200k_base`, which is not Claude's tokenizer.

## What ran

```sh
python3 tools/ai/find_sizes.py --output evidence/v1_2/discovery/sizes.json
```

`tools/ai/find_sizes.py` reads the transcripts of the 27 counted CAIRN subjects of the 1.1 evaluation (`evidence/v1_1/ai_eval/subjects/counted`, the 14 documentation and 13 plugin subjects the friction record counts) through `tools/ai/friction.py`. It takes every documentation lookup they made: a search, a file read under `docs/`, or a `cairn doc`, `cairn rules` or `cairn --help` command, but not the skill or its cards. There are 171 lookups, returning 345,206 tokens. A lookup is counted under a question when the pattern it searched or the file it read matches that question's expression in `QUESTIONS`, so a whole `std/text.md` counts for parsing an integer and for searching text both. A lookup that names no question counts under none. The attribution is by pattern and is approximate, as the friction record's was.

For each question the script then asks `cairn find` through the `find` tool of `cairn mcp`, with the words or types an agent would give, and counts the answer twice: the JSON text a client receives, and the lines a terminal prints. A `Rec` record is declared in the program for the two queries that name it.

## What it showed

Tokens. "read" is what the lookups about a question returned, summed over every subject and as the median of one subject's sum; the largest single read is one lookup's result.

| question | lookups (subjects) | read, all subjects | read, median subject | largest single read | `cairn find` query | answer, mcp / terminal |
|---|---|---|---|---|---|---|
| read standard input | 12 (10) | 17,560 | 1,910 | 1,910 (`docs/std/io.md`) | `read stdin` | 78 / 70 |
| parse an integer from text | 14 (11) | 14,645 | 1,259 | 1,984 (`cat std/vec.md std/text.md`) | `parse integer` | 75 / 66 |
| | | | | | `--takes 'ro<u8>[n]' --returns i64` | 47 / 39 |
| a Vec of records | 11 (11) | 11,401 | 951 | 1,984 (`cat std/vec.md std/text.md`) | `vec push` | 85 / 77 |
| | | | | | `--takes 'Vec[Rec]'` | 344 / 344 |
| a Buf of records | 6 (4) | 5,844 | 1,354 | 2,663 (`grep -rn "Buf" docs/*.md`) | `--takes usize --returns 'Buf[Rec]'` | 189 / 180 |
| tasks and groups | 9 (8) | 49,914 | 8,340 | 8,340 (`docs/concurrency.md`) | `spawn task group` | 69 / 60 |
| the limits of i64 | 5 (1) | 1,725 | 1,725 | 726 (`grep` of `docs/roadmap.md`) | `i64 minimum` | 100 / 92 |
| signed overflow | 7 (2) | 2,431 | 1,216 | 1,724 (`grep -rn "add_wrap" docs/`) | `signed overflow` | 35 / 26 |
| search text | 12 (11) | 14,631 | 1,259 | 1,984 (`cat std/vec.md std/text.md`) | `find substring` | 53 / 45 |
| | | | | | `--takes 'ro<u8>[n]' --takes 'ro<u8>[n]' --returns usize` | 348 / 338 |
| the library, whole | 33 (10) | 55,570 | 6,834 | 8,623 (`docs/library.md`) | none | |

Every answer is smaller than the median subject's reading for its question: 11 to 121 times smaller for a word query, and 7 times for the `Buf` type query. The two broad type queries answer in 344 and 348 tokens, about a third of what the median subject read for the same fact; the one for `Vec[Rec]` lists its ten best lines and counts the rest. The whole-library reads, `library.md`, `std_api.md` and `cairn doc --std` read up front, have no single query that replaces them: each question above is one query.

What each answer names, from `sizes.json`: `std.io.read_stdin`; `std.text.parse_i64` and `std.text.parse_u64`; `std.vec.push` and `std.vec.reserve`, or for `Vec[Rec]` seventeen, best fit first: twelve of `std.vec`, `std.mem.copy` and `fill`, `std.sort.sort_by`, `std.arena.insert` and the builtin `take`; `Buf`, then `std.vec.remove`, `std.vec.swap_remove` and `std.map.remove`, which give one back as an `Option` from a `Vec` or a `Map` of them; the builtins `Group` and `wait`; the conversion line that states the literal `-9223372036854775808`, named by `i64`, and `std.text.write_i64`; `add_wrap`, whose line says that checked `+ - *` abort on overflow, signed or unsigned; `std.text.find`, or for two views and a `usize` five functions headed by `std.text.find`, then four that rename, append or write a file.

A query answered from words took about two milliseconds once the index was built, and building it for the library took 0.23 seconds. A type query took 0.46 seconds for one view, 0.81 for one view and an `i64` wanted back, 0.47 for a `Vec[i64]` and 0.96 for two views and a `usize` wanted back, the median of three, because it checks one probe for every way the values fill every function's parameters, 648 to 1,686 probes here. Four values, the most a query takes, made 5,952 probes for four `i64` and took 4.2 seconds. The load average was between 20 and 28 while these timings ran, from other agents' test suites.

## What this does not show

These are sizes, not outcomes. The subjects searched with the documentation of 1.1, which has since been rewritten, so a subject today might read less than the table's "read" columns. The per-subject median counts only the subjects whose lookups matched a question's pattern. Nothing here was run with a model, and the friction record's accounting of what a read costs over a session (each result paid again at every later request) is not repeated here.

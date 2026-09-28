# Working on this project

This is a CAIRN project. CAIRN is a checked systems language that compiles to C++20; its compiler refuses races, uses of moved values and unchecked effects before anything runs. `cairn.toml` is data: every source file is listed under `sources`.

## The loop

1. `cairn check . --format json` until its `status` is `typed`. A refusal names one `code`, a file, a line and a column, the `card` that states its rule and, when the compiler can state one, a `repair_hint`.
2. `cairn test .` runs every `test` block in a process of its own.
3. `cairn run .` builds and runs; the program's arguments go after `--`.

## Where the rules are

The CAIRN agent skill holds them: `skills/cairn/` of the CAIRN repository, or the Claude Code plugin, which also puts `cairn` on `PATH`. `cairn rules CODE` prints the card a refusal names, offline. `cairn doc --std --module text` lists the signatures of one module of the standard library, and `cairn doc .` this project's, each with its effect row. Nothing else exists: no library a file does not declare or import, and no syntax from Rust, C++ or Python.

## What not to do

Change the code a refusal is about. Never widen an effect ceiling, turn `ro` into `rw`, add `unsafe`, or delete a check or a test to get past a refusal, and never weaken a test to make it pass.

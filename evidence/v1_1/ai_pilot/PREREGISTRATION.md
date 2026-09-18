# Fresh-model pilot: preregistration

Written and committed before any subject ran. Nothing below is changed afterwards; deviations are reported in `RESULTS.md`.

**Question.** Given only the rule cards in `src/cairn/teaching.py` and compiler diagnostics, can a model that has never seen CAIRN write correct programs in it across the 1.x feature range?

**Not a question here.** Whether CAIRN is easier or denser than C++ or Rust. There is no comparison arm, no equal-budget control and no tokenizer accounting, so no advantage of any kind may be concluded from this pilot.

**Subjects.** One fresh Claude Opus 5 subagent per task (the strongest model the repository owner allows for delegated work), started with an empty context. Each works in a directory outside the repository that contains `CARDS.md` (all rule cards, the only documentation), `TASK.md` and `check.py`. It may run `python check.py` at most six times; that reports compiler diagnostics and whether the constructs the task requires are present, never test results. It is told not to read any other file on the machine and not to use the network. Its tool calls are audited afterwards from its transcript: a subject that opened any file outside its directory, or ran the checker more than six times, is reported as contaminated and excluded from the headline count (and still shown).

**Tasks.** The nine in `tools/ai_pilot.py` (`mean machine dedupe score rotate poly dot halves recipe`): checked arithmetic over views, payload sums with `match`, the `compact` form, traits with bounded generics, heap owners, closures, `reduce`, tasks over array parts, and a user-written recipe. Each has 40 hidden cases generated from a fixed seed by a reference implementation; every task was first solved by the maintainer's own reference solution to show it is solvable as stated.

**Metric.** `solved`: the final `solution.cairn` compiles, contains the required constructs and passes all hidden cases (`python tools/ai_pilot.py score DIR`). Reported per task with the number of checker runs used and the source size. One attempt per task, no reruns, no cherry-picking: every subject that is started is reported.

**Known weaknesses.** Nine small function-level tasks; one model family, which also wrote the cards and the tasks; the subject can see the checker's path and is trusted, then audited, not sandboxed; the hidden cases are finite tests, not proofs.

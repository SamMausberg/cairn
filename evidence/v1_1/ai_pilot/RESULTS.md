# Fresh-model pilot: results

Run once, on 18 September 2026, exactly as preregistered (`PREREGISTRATION.md`, committed first at `141d674`; the tool was then only reformatted, with the same seeds and hidden cases, at `4da80b5`). No subject was rerun and none is omitted. `results.json` holds the per-task record; `solutions/` holds what each subject wrote, unedited.

| task | constructs it had to use | solved | checker runs (of 6) | source bytes |
|---|---|---|---|---|
| mean | checked arithmetic over a view | yes | 2 | 1034 |
| machine | payload `enum`, `match`, a fixed stack | yes | 1 | 1753 |
| dedupe | the `compact` form | yes | 1 | 284 |
| score | a trait, two impls, a generic with two bounded parameters | yes | 1 | 559 |
| rotate | a heap `Buf` | yes | 1 | 434 |
| poly | a closure passed as `ro<fn>` | yes | 1 | 251 |
| dot | `reduce add_wrap` | yes | 1 | 150 |
| halves | two `spawn`ed tasks over array parts, `wait` | yes | 1 | 424 |
| recipe | a user-written `recipe` with `each`, and `derive` | yes | 1 | 464 |

**9 of 9 solved** against 40 hidden cases each; 8 of 9 with the first program the subject compiled. The audit of every subject's tool calls found no file opened outside its own directory, no network use, no sub-agents, and at most 2 checker runs. The `recipe` task used a language feature that was designed the same day, so nothing about it can have been in any training data: the subject had the rule card and nothing else.

**What this does and does not show.** It shows that the rule cards plus compiler diagnostics are enough for this model to write small correct CAIRN programs across the 1.x feature range, first try, without seeing the repository. It does not show an advantage over C++, Rust or anything else (there is no comparison arm and no token accounting), it says nothing about larger programs or about models from another family, and the subjects were trusted and audited rather than sandboxed. The tasks, the cards and the subjects all come from one model family, which is the main threat to validity. The roadmap's equal-budget, multi-language experiment is still not done.

**What the subjects found confusing** (each was asked for one thing; this is the most useful output of the pilot and it has been fed back into the cards in the commit after this one): that `&&`/`||` exist and short-circuit is never stated; ordinary loops are shown as `for i in lo..hi` while `compact`, `parallel` and `reduce` write `for i in n`; `reduce` is documented only under parallel regions; the conversion form `u64(x)` and operator precedence appear only in passing; nothing says that `wait(t)` yields the task's result or shows `let name:Type = ...`; whether a `stack`/`buffer` declared without `mut` can be written by element; how `buffer x:T[n]` relates to `Buf[T](n)`; where the implicit borrow of a by-value record into `ro<Self>` comes from; and, in the recipe card, whether a fact such as `unsigned` applies to a field or its type and whether the `|` in `fold | each` is an operator or a placeholder.

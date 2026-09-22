# Edit context, whole scripted tasks

`context.json` is the output of `python3 tools/ai/measure_context.py --tiktoken o200k_base --output evidence/v1_4/context/context.json`, run on the tree of the commit that added it. No model took part.

The script picks the first six editable functions of each of five programs (`examples/apps/kvstore`, `analytics`, `service`, `simulator` and `examples/systems`), thirty tasks in all. Each task is one authored transcript: the packet, a reply with a type error, its diagnostic, and the function's own body as the correct reply, with its admission. Under a focused packet the transcript also expands the first callee written in the program, because a real agent has to read a body before it relies on one. Every message is counted once, and again as a model rereads it: the whole conversation so far on every turn it reads.

| Setting | Tokens, once | Bytes, once | Tokens with rereading | Turns |
|---|---|---|---|---|
| component packet, `cairn.edit/1` (the 1.3 protocol) | 287,064 | 971,543 | 832,966 | 60 |
| component packet, `cairn.edit/2`, cold | 277,368 (0.966) | 953,663 (0.982) | 811,954 (0.975) | 60 |
| focused packet, `cairn.edit/2`, cold | 73,741 (0.257) | 285,027 (0.293) | 221,833 (0.266) | 71 |
| focused packet, `cairn.edit/2`, warm | 46,717 (0.163) | 172,113 (0.177) | 562,986 (0.676) | 71 |

Tokens are OpenAI's `o200k_base` encoding, which is not Claude's tokenizer; bytes are UTF-8. Ratios are against the first row. Cold means a new host for every task. Warm means one host and one conversation per program, so each rule card and the boundary text are sent once, and the rereading column charges the whole growing conversation on every turn, which is what a model without prompt caching pays.

On these transcripts a focused packet cuts what a model reads per task to about a quarter, at eleven more turns in sixty for the expansions. Handles alone save a fifth of what the model writes and little else. The per-program totals are in `rows`; the smallest saving is `examples/systems` (0.68 of the 1.3 bytes), whose call graph is already small.

What this does not show: that a model given the focused packet solves as many tasks, or needs no more repair rounds. A real agent may expand more than once, and every expansion shrinks the saving. Only a trial with model subjects can measure that, and none has run.

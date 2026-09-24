# Edit-protocol trial: preregistration

This file fixes the design of the trial before any subject runs. Anything that changes afterwards is reported as a deviation beside the results, under `evidence/v1_0/protocol_trial/`. The harness is `tools/ai/protocol_trial.py`.

## Question

Does the focused packet, which shows a function's source and the interfaces around it and discloses more only when the agent asks, lower the total effort of a model repairing one function, at equal correctness, compared with the component packet of CAIRN 0.8.3, which shows the whole call-graph component at once?

The proposed target is half the effort. It is a target for the measurement, not a claim, and the rules below say what may be claimed from the numbers.

## Arms

Both arms use the `cairn.edit/2` host, with handles in place of digests, the same rule cards, the same effect ceiling and the same admission checks. They differ only in what the packet shows.

| Arm | Packet | May expand |
|---|---|---|
| component | the target's call-graph component in both directions, as source, and every type of the program | no |
| focused | the target's source, and the signature, effect row and comment of every function it calls or that calls it, with the types those name | yes, up to 32 names per request |

Scripted replies on thirty edits of the same programs put the focused packet at about a quarter of the component packet's context (`evidence/v1_0/context/`). This trial asks whether a model, which may expand more, repair more and think longer, keeps that saving.

## Tasks

Twelve repairs, each a real function of a shipped example with one planted bug, and a bug report that names the symptom and not the fix. They are `TASKS` in the harness: `above_loop`, `venue_tasks`, `notional` and `plan_first` in `examples/apps/analytics`; `replay_tail`, `apply_kind`, `digest_order` and `checksum_check` in `examples/apps/kvstore`; `overflow_digit`, `digit_range`, `sort_top` and `even_bit` in `examples/systems`.

The hidden check of a task is the program's own self-check (`main` returns 0), every finite contract its manifest lists, and, for `digest_order` and `digit_range`, cases the harness computes from a reference written in Python. `protocol_trial.py verify` shows, before any subject runs, that the program as shipped passes its hidden check and the planted program fails it, for every task. `protocol_trial.py rehearse` shows that every task can be solved through the host under both arms: a scripted subject that sends the shipped body is solved twelve times out of twelve in each.

## Subjects and budgets

One fresh subject per task and arm, 24 in all, each a subagent of the same model at the same effort setting, started with an empty context and the single instruction `Read TASK.md in <sandbox> and do what it says.` Tasks run in the order of `TASKS`; for the odd-numbered tasks the component arm runs first, for the even-numbered ones the focused arm does. Subjects run one at a time.

A sandbox holds `TASK.md`, `PACKET.json`, `host.py` and `state.json`, and no source. A subject has 8 host calls and 20 minutes. It may not read any file outside its sandbox or use the network. It finishes by submitting; its answer is its last admitted edit. A subject that stops without submitting, or whose admitted edits are all refused by the hidden check, has not solved its task. Each subject runs once. There are no reruns and no replacements, and every subject that starts is reported.

## Measures

The primary measure of effort is a subject's total tokens as the platform that ran it reports them: input, including cached input, plus output. Whoever runs the trial records that number per subject, with the platform's own field names, beside the sandbox.

`protocol_trial.py score` measures the rest from the transcripts: bytes and `o200k_base` tokens the subject read from the host (the packet and every answer) and wrote to it, host calls, expansions and refusals. A task is solved when the submitted edit passes the hidden check.

## Analysis and what may be claimed

For each arm: tasks solved, and the sums of every measure. The effort ratio R is the focused arm's summed primary effort over the component arm's. The per-task ratios are reported too, with their median.

- "The focused packet halves effort" may be written only if R is at most 0.5 and the focused arm solves at least as many tasks as the component arm, less one.
- "The focused packet lowers effort" may be written only if R is below 1 under the same condition on solved tasks.
- Otherwise the numbers are reported without either sentence.

The host-visible measures are secondary: they are what the protocol controls, and the primary measure is what the agent spends. A subject that read a file outside its sandbox or used the network is contaminated. It is listed with its numbers and left out of R and of the solved counts.

## Known weaknesses

Twelve small repairs in three example programs, in a language the subjects have not seen before. One model family, which also wrote the language, the cards, the tasks and the bug reports. The planted bugs are single-line mutations, so they reward reading one function closely and may favour the focused arm. The hidden checks are finite. Subjects are audited from their transcripts, not isolated by the operating system. With twelve pairs, the trial can show a large difference and cannot show a small one. `o200k_base` is not the subjects' own tokenizer, which is why the primary measure is the platform's count.

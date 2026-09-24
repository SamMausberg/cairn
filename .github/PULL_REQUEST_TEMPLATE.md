## Summary

<!-- What is true after this change that was not before, in two or three sentences. Lead with what a user or an agent sees. -->

## Motivation

<!-- The problem this solves, and why this approach over the ones you considered. Link the roadmap issue: "Closes #N" or "Part of #N". -->

## Changes

<!-- One line per change, grouped by area (compiler, runtime, tools, agent, docs, tests). Name the file that owns each rule you touched. -->

-

## Behaviour and compatibility

<!-- What a program, a command's output or a record now does differently. New or changed diagnostic codes. Whether any existing program is accepted, refused, emitted or printed differently, and whether emitted C++ or CUDA changes for programs that do not use this change. -->

## Testing

<!-- The commands you ran and what they reported, with counts. What each new test pins. The compilers and sanitizers used, and what could not run here (for example, a device run). -->

```sh
make lint
python -m pytest -q tests -n 4
```

## Evidence and claims

<!-- The claims this adds, changes or withdraws, each with its kind: accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked or benchmarked. Records added under evidence/. Write "none" if it changes no claim. -->

## Performance

<!-- Before and after, how and where it was measured, or "not measured". A cairn predict figure is a prediction and is labelled as one. Write "none" if speed is not affected. -->

## Checklist

- [ ] The title is one plain sentence saying what is now true; it becomes the squash commit's subject
- [ ] Every behaviour added has a test, and every new refusal a rejection test naming its code
- [ ] The documentation is updated in the file that owns the subject, and its examples compile
- [ ] Generated files are current (`make editors`, `make docs`)
- [ ] A language change carries everything [AGENTS.md](../AGENTS.md) lists: rule card, reference entry, `capabilities.json`, projection round-trip, native tests under clang++ and g++ with the sanitizer that bites
- [ ] No emojis, no attribution trailers, nothing unrelated to the change

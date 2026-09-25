# Security

This page says what CAIRN does not protect against, what counts as a security bug, and how to report one.

## What CAIRN does not protect

CAIRN is not a security sandbox. The process limits that `cairn run` applies, such as its memory cap, stop a runaway program and isolate nothing. Compile and run untrusted programs, adapters and compilers under isolation that the operating system provides. [docs/internals.md](docs/internals.md#safety-and-trust) states what an accepted program promises, on what evidence, and what the foreign boundary trusts.

## What counts as a security bug

A soundness bug is a security bug. It is an accepted program that, outside `unsafe` and `extern`, uses a moved owner, aliases a mutable borrow, keeps a borrow past its call, races, or reaches memory in the wrong placement, such as device memory from host code. A guard that can be skipped is a security bug too. So is a manifest that reads or writes outside its project root, and a build that executes anything a manifest or an export's record names.

A tool that writes outside the project or scratch directory it was given is a security bug as well. That covers `cairn diff` reading a revision, `tune --write`, `shot`, a migration, a rename, and a change that `cairn mcp` writes back.

## How to report one

Report it privately to the repository owner, through GitHub's private vulnerability reporting for this repository or by direct message, with the program that shows it. Do not open a public issue for it until a fix has landed. Reports get an acknowledgement within a week. A fix is released with a test that pins it: a rejection test naming the diagnostic code, or a behaviour test naming the exit status.

## Supported versions

The supported version is the latest release on `main`. The records under `evidence/` from earlier internal milestones are history, and none of them is a supported version.

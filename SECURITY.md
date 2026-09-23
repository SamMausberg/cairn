# Security

CAIRN is a young compiler. It is not a security sandbox, and the process limits `cairn run` applies are protections against runaway programs, never isolation. Compile and run untrusted programs, adapters and compilers under operating-system isolation. [docs/internals.md](docs/internals.md#safety-and-trust) states what an accepted program promises, on what evidence, and what the foreign boundary trusts.

A soundness bug is a security bug: an accepted program that uses a moved owner, aliases a mutable borrow, keeps a borrow past its call, races, or reaches memory of the wrong placement outside `unsafe` and `extern`. So is a guard that can be skipped, a manifest that reads or writes outside its project root, a build that executes anything a manifest names, and a tool that writes outside the project or scratch directory it was given: `cairn diff` reading a revision, `tune --write`, `shot`, a migration or a rename.

Report one privately to the repository owner through GitHub's private vulnerability reporting for this repository, or by direct message, with the program that shows it. Do not open a public issue for it until a fix has landed. Reports get an acknowledgement within a week, and a fix is released with a rejection test naming the diagnostic code, or a behaviour test naming the exit status, that pins it.

The supported version is the latest release on `main`. Earlier releases are recorded under `evidence/` and are not patched.

# Run notes, 18 September 2026

The untouched imported 0.4 test suite passed 232 tests. The split package passed the same 232. The complete final suite passed 299 tests. Logs are retained separately; counts refer to actual executions, not generated test definitions.

The aggregate tools/verify.py --gcc --sanitize command hit the tool execution limit after ten successful children. Its JSON contains only those completed children. The remaining GCC native tests, sanitizer build/execution, code-section comparison and density accounting were executed explicitly. Separate output files record those completions. There is no fabricated aggregate-success message.

An offline wheel was built and installed in a fresh virtual environment outside the repository. The installed CLI created a new project, checked and emitted it, built a shared library, ran executables under both compilers, passed 81 task cases under each, supplied an inspection packet, and obtained scalar average equivalence. These checks do not rely on importing the source checkout. Clean Git-clone validation is recorded separately after the implementation commit.

Publication tests simulate GitHub responses and validate local Git history. No real remote was created or pushed. The connected account was identified, but the connector had no repository creation action and no authenticated GitHub CLI was present. Local-only publication preflight is checked after committing.

The scalar translator and Z3 remain trusted. Native tests, sanitizer output and object equality are not proofs. No new timing, GPU test, model training, fresh-model experiment or Lean build ran. The large prior arithmetic-validation and teaching-data campaigns were not rerun; inherited training records remain historical inputs.

The final clean clone at implementation commit 6a072ab passed all 299 tests, ran its native example, passed 81 task cases and passed the local-only publication preflight. An earlier packaging attempt was stopped by the staged whitespace gate before the implementation commit, so its baseline-only clone was rejected as a release candidate. Its 232 tests were not counted as a successful 0.5 distribution test. A later multi-command clone-validation call hit the execution limit after tests and native execution; the remaining task and preflight checks were run separately and passed.

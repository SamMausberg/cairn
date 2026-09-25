# Skill evals

This folder is a `claude plugin eval` suite, which `.claude-plugin/plugin.json` names under `experimental.evals`. Each directory is one case: `prompt.md`, with the tools it allows in its frontmatter, and `graders/`. Five cases give a refused program, and they grade the diagnostic code by a regular expression, the fix by a rubric, and whether the skill fired. The sixth, `write-checksum`, asks for a program. The cases use only the Read, Glob, Grep and Skill tools.

Running the suite needs the `claude` command and spends model usage, so no test or make target runs it, and it has not been run. `tests/tooling/test_skill.py` checks what the cases claim about the compiler: each program is refused with the code its case grades.

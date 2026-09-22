What is now true, in one sentence:

How it is checked (commands and what they reported):

For a language change, each of these is in this pull request ([AGENTS.md](../AGENTS.md) says why):

- [ ] the elaboration and failure policy
- [ ] the effects it adds
- [ ] a rejection test naming its diagnostic code
- [ ] a behaviour test run natively under g++ and clang++ with the sanitizer that bites
- [ ] a rule card in `agent/teaching.py`
- [ ] the reference entry and `docs/project/capabilities.json`
- [ ] the canonical projection round-tripping to identical native code

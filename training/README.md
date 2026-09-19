# Inherited teaching fixtures

`source/` holds the 0.3 program lessons that 0.4 retained. `semantic/` holds the 0.4 same-contract scalar preferences, obligations and protocol repairs. These are inputs kept for audit, not new results. Historical compiler hashes, solver results, packet IDs and version labels may still refer to their original release, so re-run the generator and checker for a new admission record rather than relabelling an old receipt.

`tools/ai/curriculum.py` regenerates `source/`; `tools/checks/semantic_corpus.py` and `tools/ai/protocol_curriculum.py` regenerate `semantic/`. `tools/checks/curriculum_verify.py` and `tools/checks/mutation_checks.py` check `source/` against independent finite oracles.

No model was trained. Answers and oracles are included here and are not secret held-out data. Some older static contrast lessons change their API or meaning, so do not reward them as contract-preserving repairs.

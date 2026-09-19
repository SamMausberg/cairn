# agent

The fixture behind the edit and repair loop: a task contract, a source with a deliberate off-by-one (`>=` where the task says strictly greater), the repaired source, and an adapter whose three answers are hard-coded.

```sh
python3 tools/ai/demo.py --out /tmp/agentdemo
# {"status": "passed-reserved-finite-tests", "attempts": 3,
#  "public_cases": 3, "reserved_cases": 5, "adapter_kind": "scripted-fixture"}
```

`scripted_adapter.py` is a fixture, not a model: it replies with a type error, then a behavioural error, then the correct body, which is what makes the transcript reproducible. `task.json` names the symbol, the allowed effects and the cases; some of them are reserved, so the adapter never sees what it is finally judged on. `prefix_sum.cairn` is a second, unrelated symbol the harnesses use.

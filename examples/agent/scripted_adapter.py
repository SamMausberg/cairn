#!/usr/bin/env python3
"""A THREE-STEP SCRIPTED FIXTURE, NOT AN AI MODEL OR AN AGENT EVALUATION.

Exercises the runner with (1) a type error, (2) a behavioral error, (3) a
correct body. This program has the demonstration solution hard-coded.
"""

import json
import sys

request = json.load(sys.stdin)
packet = json.loads(request["messages"][1]["content"])
bodies = [
    "{ return true; }",
    "{ let used=compact out for i in n where x[i]>=threshold yield x[i]; return used; }",
    "{ let used=compact out for i in n where x[i]>threshold yield x[i]; return used; }",
]
reply = {"protocol": "cairn.edit/2", "handle": packet["handle"], "kind": "body"}
print(json.dumps({**reply, "replacement": bodies[min(request["attempt"], 2)]}))

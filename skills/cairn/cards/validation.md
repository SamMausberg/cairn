# The validation card

A host or the command line names this card in a refusal. Codes: E-DOMAIN E-REFERENCE E-TEST-POLICY E-TOLERANCE E-VALIDATION.

```text
An implementation session pins its reference, the tolerance, the test policy and the permitted inputs. A submission is one implementation of that reference, new or replacing one of the same name, with the helpers it calls and nothing else (E-DECLARATION); it never redefines the reference or implements another function (E-REFERENCE), and names no tolerance (E-TOLERANCE), cases, seed, budget, policy or test block (E-TEST-POLICY), and no domain, inputs or precondition (E-DOMAIN): narrow where it applies with when instead. The host rechecks the program under every rule of the implementations card and validates the submission against the reference under the pinned policy; a failing or undecided validation is E-VALIDATION, with the shrunk input when one failed. Fix the algorithm for every input its condition admits, not for that one.
```

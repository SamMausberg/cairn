# The sketches card

A host or the command line names this card in a refusal. Codes: E-SKETCH-BUDGET E-SKETCH-CHOICES E-SKETCH-CONTRACT E-SKETCH-EMPTY E-SKETCH-NAME E-SKETCH-OVERLAP E-SKETCH-SEALED E-SKETCH-SITE.

```text
A host may ask for named expressions instead of a body. The reply is one JSON object that maps every slot to one expression string, with no other key, no comment in a value, 64000 bytes of choices and 128000 of reply at most (E-SKETCH-CHOICES). A sketch has 1 to 16 descriptive slot names (E-SKETCH-EMPTY, E-SKETCH-NAME), each on exactly one expression and none inside another (E-SKETCH-SITE, E-SKETCH-OVERLAP), and its slots are fixed once it is sent (E-SKETCH-SEALED). A semantic check needs a fixed reference of the same function (E-SKETCH-CONTRACT), and a search tries 1 to 4096 candidates within its budget (E-SKETCH-BUDGET).
```

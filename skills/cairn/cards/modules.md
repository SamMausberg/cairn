# The modules card

Selected by import module pub. Codes: E-IMPORT E-MANGLE E-PRIVATE.

```text
module net.http; names the module of what follows, and pub exports. import net.http; allows http.get(...); import a.b as c; renames; import std.core (Option, Result); also brings those names in unqualified. std.* ships with the compiler and nothing is downloaded. A private name of another module is not callable (E-PRIVATE): request context instead of guessing.
```

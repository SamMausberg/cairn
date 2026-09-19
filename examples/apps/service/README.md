# service

A TCP key/value service on 127.0.0.1, one client at a time, shut down by the client.

```text
put <key> <value>   ->  +ok
get <key>           ->  = <value>  |  -missing
del <key>           ->  +ok        |  -missing
quit                ->  +bye, and the service exits 0
anything else       ->  -error
```

```sh
cairn run examples/apps/service                                   # blocks: "service: listening on 39800"
printf 'put a hello\nget a\ndel a\nget a\nquit\n' | nc 127.0.0.1 39800
```

```text
+ok
= hello
+ok
-missing
+bye
```

The port is `const PORT:u16` in `src/main.cairn`. `tests/projects/test_apps.py` copies the project and rewrites it with a free port, so parallel test workers never collide.

## What it demonstrates

A linear `Socket` per connection: `let c = try net.accept(server); defer net.close(c);` inside the accept loop. The deferred close runs at the end of each iteration, and the server socket's own `defer` outlives it. Forgetting either is `E-LINEAR-LEAK`, not a descriptor leak.

Parsing without substrings. A borrow cannot be returned, so `word_end` answers with an index and the request is sliced at the call site: `find(t, k, line[key_lo..key_hi])`. The parts carry one bounds guard each, and nothing is copied until a key is actually stored.

One buffer, no allocation on the request path. `serve` keeps a 4096-byte `buffer`, finds a newline in the filled prefix, answers, then shifts the remainder down with an ordinary loop. `mem.copy` would be rejected here (`E-ALIAS`), which is the right answer for a memmove.

State that outlives connections: the `Table` lives in `run`, so the second client sees what the first one stored.

## Effect rows worth noticing

From the build receipt, minus the `ffi_precondition`, `diverge`, `local_read`, `local_write`, `stack_storage` and `ffi:__errno_location` that every caller of `std.io` carries:

```text
respond  alloc, ffi:send, free, io, mmio, read:c, read:line, read:t, trap, write:t, zero_init
serve    alloc, ffi:recv, ffi:send, free, io, mmio, read:c, read:t, trap, write:t, zero_init
run      + ffi:socket, ffi:bind, ffi:listen, ffi:accept, ffi:setsockopt, ffi:close, ffi:write
```

`respond` reads the connection and the request and writes only the table, and the row says which argument each read and write belongs to. The calls that open and close a socket (`ffi:socket`, `ffi:bind`, `ffi:listen`, `ffi:accept`, `ffi:setsockopt`, `ffi:close`) appear only in `run`, so the protocol layer provably cannot open or close a connection behind the loop's back. `alloc` is there because `get` builds its answer in a `Vec`; a reply of a fixed shape would not allocate at all.

## Known limits

Blocking and sequential: one `accept` at a time, no timeout and no poll. A client that opens a connection and says nothing stops the service until it goes away.

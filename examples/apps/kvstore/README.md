# kvstore — a crash-safe log-structured store

An append-only log of checksummed records with the index in memory, put/get/delete, reopen by
replay, and compaction. `main` runs the whole scenario against a file of its own and returns 0
only if every check passes.

```
cd /home/ubuntu/cairn && .venv/bin/python bin/cairn run examples/apps/kvstore
# kvstore: self-check passed
```

## What it demonstrates

* **`derive wire`** for the 16-byte record header: `struct Header { check:u32; kind:u32;
  key_len:u32; val_len:u32; }` plus `derive wire for Header;` gives little-endian
  `encode_Header` / `decode_Header` with no padding and no hand-written shifting.
* **A linear `File` beside the state, not inside it.** `Store` holds only the index and the
  valid log length; the File is a separate local with `defer io.close(f)`. A linear value inside
  a struct could never be consumed (`take` leaves a shell that still demands consumption), so
  this is the shape the type system asks for.
* **`try` with `defer`.** Every step is `let x = try ...;` and the deferred close runs on the
  failing path too. `try` refuses to leave a function while a linear value is live, so the
  `defer` is what makes the whole style legal.
* **`Map[u64, Record]` with owner values.** The index owns both the key and the value bytes
  (`Record { key:Vec[u8]; value:Vec[u8]; }`). Replacing a key releases the old value inside the
  map; `remove` moves it out.
* **Recovery that is a loop, not a promise.** `replay` stops at the first record that is short,
  truncated or fails its checksum, `ftruncate`s the file back to the last whole record and
  leaves the descriptor there. The scenario tears the log twice — nine bytes of a header, then a
  complete record with a wrong checksum — and expects both to vanish.

## Design notes

The index is keyed by the 64-bit FNV digest of the key and *also* stores the key bytes, so every
hit is verified byte for byte and a digest collision is a miss rather than a wrong answer. Values
live in memory; the log is the durability layer, replayed on open and rewritten by compaction.

`compact_log` writes the live records to `<path>.tmp` and renames it over the log. The caller
must reopen afterwards: the descriptor it still holds names the replaced file.

## Effect rows worth noticing

```
put          alloc, free, io, ffi:write, ffi:fsync, read:f, read:key, read:s, write:s, mmio, trap
replay       alloc, free, io, ffi:read, ffi:lseek, ffi:ftruncate, read:f, write:s, stack_storage
compact_log  io, ffi:open, ffi:write, ffi:fsync, ffi:rename, ffi:close, read:s, read:path
```

`put` says `ffi:fsync`, so durability is visible in the row of everything that calls it — there
is no way to make a write durable without it showing up. `compact_log` has no `alloc`: rewriting
the log copies bytes through one stack header and the existing Vec storage. `mmio` comes from
`std.sys.errno`, which reads errno's address with one volatile load.

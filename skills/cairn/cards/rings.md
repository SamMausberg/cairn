# The rings card

Sent to an agent when the program uses `IoRing`.
Codes: `E-PINNED`.

```text
let mut q = IoRing(n); declares, in place, a kernel ring of up to n operations in flight with no thread each. q.read(fd, data, count, offset, tag), q.write(...), q.recv(fd, data, count, tag), q.send(...) and q.accept(fd, tag) move the Buf[u8] data into the ring: it is dead until let data = q.next(tag, result); hands the next finished one back with its tag and the kernel's result (a count, a descriptor or -errno; io.outcome(result) makes it a Result). wait(q), or defer wait(q), consumes the ring where it was declared, after every operation finishes. A ring may be lent rw to a callee or a task, never stored, passed by value or returned (E-PINNED). Every submission returns through next exactly once, with -errno if the kernel refused it; a ring the kernel would not set up is down, q.status() is that -errno, and each submission comes straight back with it. Submitting when q.room() == 0, or next when q.pending() == 0, traps. q.timeout(ns, tag) finishes with -ETIME after ns; q.cancel(tag) stops what runs under tag, which still returns through next with -ECANCELED or its own result. Host only; effects io, alloc, free.
```

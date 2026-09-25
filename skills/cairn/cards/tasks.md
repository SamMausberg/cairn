# The tasks card

Selected by Atomic Group Mutex collect spawn wait. Codes: E-LEASED E-SPAWN.

```text
let t = spawn f(args); runs a declared function on its own thread and gives a linear ticket that wait(t) must consume in the same function; let r = wait(t); is f's result, and wait(t); alone serves when f returns nothing. Until then every place lent to the task is leased (E-LEASED): nobody writes what it reads or touches what it writes, and visibly disjoint parts (d[0..mid], d[mid..n], d[K..2*K] with K constant) may be lent mutably to different tasks. What is leased is the place lent, not the local it sits in: two fields of one record (spawn f(box.a) beside spawn g(box.b)) go to two tasks, and len(box.a) still reads while box.a's elements are lent, while lending the record whole leases every field in it, and box.a = Buf[u64](2) under a lease of box.a is refused.

let g = Group[u64](4); holds up to 4 tasks at once: spawn f(args) into g; hands the task to g with no ticket, let r = collect(g); is the result of whichever task finishes next (an empty or a full g traps), and wait(g); joins the rest and drops their results. Every place lent to any task of g, on any path, is leased until wait(g); collect returns none, so a loop lends g only ro places.

Atomic[u64] and Mutex[T] are declared in place and shared by ro borrow: a.fetch_add(1, Order.relaxed) always names its memory order, and m.with(|s:rw<T>| { ... }) is the only way into a mutex. Tickets, groups, atomics and mutexes are never stored, passed by value or returned.

Device work can be queued: let up = spawn transfer(x, a); let k = spawn parallel i in n after up { y[i] = x[i]; }; each returns at once with a linear ticket that leases the views it touches until wait; after orders it behind live tickets on the device and lets it share what they hold. Only device regions and transfers are queued.
```

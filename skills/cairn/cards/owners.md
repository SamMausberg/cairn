# The owners card

Sent to an agent when the program uses `Array`, `Buf`, `defer`, `linear`, `swap`, `take`.
Codes: `E-EXTENT`, `E-EXTENT-FIELD`, `E-LINEAR-BRANCH`, `E-LINEAR-LEAK`, `E-MOVE-IN-LOOP`, `E-MOVED`, `E-PARTIAL-MOVE`.

```text
let mut b = Buf[u64](n); is a first-class zeroed heap array, and Array[u64, 4]() an inline one. Owners are affine: binding, passing by value or returning one moves it, and the old name is dead (E-MOVED). An owner never moves out of a place (E-PARTIAL-MOVE): take(place) moves it out and leaves zero, swap(a, b) exchanges two places, and let Conn(sock, sent) = c; consumes a whole record and binds every field, the way out for a linear field. An outer owner cannot move inside a loop, closure or lane (E-MOVE-IN-LOOP).

A linear struct value is consumed exactly once on every path (E-LINEAR-LEAK, E-LINEAR-BRANCH); defer call(x); schedules that one visible call for every normal exit of its block. ro<T> and rw<T> borrow one value and read and assign like it; x[lo..hi] passes a part of an array with one dynamic guard, and two parts are disjoint only if they visibly share a boundary.

Letting an owner go charges free where the release is: the end of the block or match arm holding it, a return that leaves while it is held, a function handed one that passes it on to nobody, and the place a new value is assigned over. A linear value need not own storage, so consuming one charges nothing on its own.

In struct Chart { rows:usize; price:Buf[f64][rows]; } the extent is an earlier usize field of the record (E-EXTENT), and len(c.price) == c.rows then holds of every value, so total(c.rows, c.price) passes the field whole with no part guard. Nothing checks it at run time, so Chart(n, Buf[f64](n)) writes the Buf inline on the same n as rows, and neither half is assigned, taken, swapped or lent rw alone (E-EXTENT-FIELD). Moving, take, swap and zeroed storage carry the record whole and keep it.
```

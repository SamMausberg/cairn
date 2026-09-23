# The records card

Sent to an agent when the program uses the forms it describes. Codes: `E-RECORD-TYPE`.

```text
struct Pair {x:u64; y:u64;} is a value record: Pair(a,b), p.x. Only mutable locals and rw borrows change. Fields are any value type, never a borrow, void or the record itself (E-RECORD-TYPE); a record is copyable if its fields are; copying costs work. A Buf field may name an earlier usize field as extent: price:Buf[f64][rows]. Tag-only enum Op {Read; Write;} uses Op.Read and ==. Sums have their own card; neither is a dynamic object or allocation.
```

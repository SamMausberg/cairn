# records

Write a program that parses order records, one per line, and reports the first error in each line by kind and column.

## Input

Standard input is a sequence of lines separated by the byte `\n` (10). If the input does not end with `\n`, its last line still counts; a `\n` at the very end does not start another line. An empty input has no lines. Lines are numbered from 1.

A record is three fields separated by commas (`,`, byte 44): `id,qty,price`. No field is quoted, and no byte other than `\n` is special: a space, a tab or a `\r` is an ordinary byte.

- `id` is a decimal number from 0 to 4294967295.
- `qty` is a decimal number from 0 to 65535.
- `price` is digits, one `.`, and exactly two digits: `12.50`, `0.07`, `000.10`. Its value in cents is the digits before the point times 100 plus the two digits after it, and it must be at most 9223372036854775807.

Leading zeros are allowed everywhere and do not count against a range: `00000000000000000000042` is 42.

## Checks

Columns count bytes from 1 at the start of the line. The checks run in this order, and a line reports only the first that fails:

1. Field count. Fewer than three fields: `missing`, at the column one past the end of the line. More than three: `extra`, at the column of the third comma.
2. Each field in turn, `id`, then `qty`, then `price`:
   1. An empty field: `empty`, at the column where the field starts (for an empty field that is the column of the comma after it, or one past the end of the line for the last field).
   2. A byte that is not allowed: `digit`, at the column of the first such byte. In `id` and `qty` only the digits `0` to `9` are allowed; in `price`, digits and `.`.
   3. For `price` only, a shape other than one or more digits, one `.` and exactly two digits: `format`, at the column where the field starts.
   4. A value above the field's maximum: `range`, at the column where the field starts.

## Output

One line per input line, in order: `ok ID QTY CENTS` with the three values in plain decimal (no leading zeros), or `err LINE COLUMN KIND`.

## Example

Input:

```
7,3,12.50
7,,1.00
8,70000,1.00
9,1,1.5
10,2,3.00,4
x
11,2,0000000000000000000009.99
```

Output:

```
ok 7 3 1250
err 2 3 empty
err 3 3 range
err 4 5 format
err 5 10 extra
err 6 2 missing
ok 11 2 999
```

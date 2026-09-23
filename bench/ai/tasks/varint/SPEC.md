# varint

Repair a program that decodes a stream of variable-length integers.

## What the program should do

Standard input is one line of lowercase hexadecimal, two digits per byte; it may be empty. The bytes are a sequence of unsigned LEB128 integers: each byte gives seven bits of the value, least significant group first, and a byte with its top bit (128) set means another byte of the same integer follows.

For each integer, print its value in decimal on a line of its own. Stop at the first error and print it:

- `err overflow O` when the integer does not fit in 64 bits: its tenth byte is above 1 (which covers a tenth byte with its top bit set).
- `err truncated O` when the input ends inside an integer, that is, after a byte with its top bit set.

`O` is the offset of the integer's first byte, counted in bytes from 0. The overflow check is made at the tenth byte before looking for an eleventh.

## Bug report

"Decoding `ff` should print `err truncated 0`. The decoder does not report it: depending on the build it prints a number, prints garbage or crashes. The same happens whenever the input ends in the middle of a number."

Find the defect and fix it, changing as little of the program as the fix needs. Every other input must keep the behaviour described above.

## Example

Input:

```
00e5018e0280808080808080808001
```

Output:

```
0
229
270
9223372036854775808
```

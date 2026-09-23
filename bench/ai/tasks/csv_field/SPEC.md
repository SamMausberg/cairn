# csv_field

Repair a program that prints one column of a comma-separated file.

## What the program should do

The first line of standard input is `k`, a decimal number from 0 to 100. The rest of the input is rows separated by the byte `\n` (10). If the input does not end with `\n`, its last row still counts; a `\n` at the very end does not start another row.

Fields in a row are separated by commas, with no quoting. For each row, in order, the program prints field `k` (counting from 0) followed by a newline, or `none` when the row has fewer than `k + 1` fields. A field may be empty, and then the program prints an empty line.

## Bug report

"When the file does not end with a newline and the column I ask for is the last one on the last row, the program crashes or prints the wrong text for that row. With a newline at the end of the file it is fine."

Find the defect and fix it, changing as little of the program as the fix needs. Every other input must keep the behaviour described above.

## Example

Input:

```
1
a,b,c
d
,e,
x,yz
```

Output:

```
b
none
e
yz
```

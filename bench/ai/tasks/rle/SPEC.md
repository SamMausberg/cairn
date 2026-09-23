# rle

Write a program that decodes a run-length encoded byte stream into an output of bounded size, refusing input that is cut short or that would decode past the bound.

## Input

Standard input has two lines. The first is `cap`, a decimal number from 0 to 1000000: the most bytes the output may hold. The second is the encoded bytes as lowercase hexadecimal, two digits per byte; it may be empty. The input is always well formed in this sense.

## Format

The encoded bytes are a sequence of packets. Each packet starts with a control byte `c`:

- `c` below 128: a literal packet. The next `c + 1` bytes are copied to the output.
- `c` of 128 or more: a repeat packet. The next byte is written to the output `c - 126` times (2 to 129 times).

Decoding ends at the end of the input.

## Output

When a packet's bytes run past the end of the input, print `err truncated O`, where `O` is the offset of its control byte, counted in bytes from 0. When a complete packet would make the output longer than `cap`, print `err full O` for its control byte. A packet that is cut short is `truncated` whatever its length. The first packet that fails decides the answer, and nothing else is printed.

Otherwise print `ok L H`: the output length, and the 64-bit FNV-1a hash of the output as 16 lowercase hexadecimal digits. FNV-1a starts from 14695981039346656037 and, for each byte `b` of the output in order, sets the hash to `(hash XOR b) * 1099511628211` modulo 2^64.

## Example

Input:

```
10
0261626383ff
```

Output:

```
ok 8 64959258fcef53f8
```

The first packet copies `61 62 63` ("abc"), the second writes `ff` five times.

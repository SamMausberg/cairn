# pool

Write a program that keeps a pool of byte buffers named by handles, where a handle to a freed buffer is detected as stale instead of reaching another buffer.

## Input

Standard input holds one command per line, tokens separated by single spaces. Every number fits an unsigned 64-bit integer.

## The pool

The pool has numbered slots, from 0 upwards. A slot is free until a buffer is placed in it, and again after that buffer is freed. Each slot counts how many buffers it has held; a handle is a slot number `I` and that count `G` at the time the buffer was placed there, so the first buffer in slot 0 is `0 1`, and the next buffer placed in slot 0 is `0 2`. A handle is live while its buffer is in the pool. Any other pair of numbers is stale.

## Commands

- `new S` (S from 0 to 4096): make a buffer of `S` zero bytes and place it in the lowest-numbered free slot. Print `h I G`, its handle.
- `set I G OFF V` (V from 0 to 255): if `I G` is stale, print `stale`. Otherwise, if `OFF` is not below the buffer's size, print `bounds`. Otherwise set byte `OFF` to `V` and print `ok`.
- `sum I G`: print `stale`, or the sum of the buffer's bytes in decimal.
- `move I G J H`: if either handle is stale, print `stale`; if they are the same handle, print `same`. Otherwise append the bytes of buffer `I G` to the end of buffer `J H`, free buffer `I G`, and print `ok N`, where `N` is the new size of buffer `J H`. Sizes can grow past 4096 this way.
- `free I G`: print `stale`, or free the buffer and print `ok`.

After the last command, print `live L B`: the number of live buffers and the total of their sizes.

## Example

Input:

```
new 3
new 2
set 0 1 2 200
set 0 1 3 1
move 0 1 1 1
sum 1 1
sum 0 1
new 1
free 0 1
sum 0 2
```

Output:

```
h 0 1
h 1 1
ok
bounds
ok 5
200
stale
h 0 2
stale
0
live 2 6
```

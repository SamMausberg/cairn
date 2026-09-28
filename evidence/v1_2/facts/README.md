# Guards the facts discharge: products, quotients and sums

The checker's facts (`compiler/check/facts.py`) now discharge three more kinds of guard site. A usize `x * C`, with `x` an immutable value and `C` a positive constant, needs no overflow guard where the facts cap the product at the largest usize. `let q = a / C` adds the fact that `q * C` is at most `a`, so at most every bound of `a` and the largest usize. A sum's lower bound comes from either operand, where before it came only from the left one with a constant on the right. `verify/elision.py` decides each of the three again from the cited facts alone, and `proofs/Cairn/Facts.lean` adds `mulOk` with `mul_sound`, `divFacts` with `divFacts_hold`, and the symmetric bound inside `bounds_sound`. This record holds what ran to check that, at commit 3fcf2c2 of `c13/facts-mul-div` (the tool flag of the last run is 8621d3d), against `main` at a39cd3d.

## What ran

The machine is the GH200 host: 64 Neoverse V2 cores, Ubuntu 22.04.5, clang 14.0.0, g++ 11.4.0, Lean 4.34.0 as `proofs/lean-toolchain` pins it. Other agents ran their test suites on the same cores throughout, at a load average near 20. Load slows these runs and changes nothing they compare. Nothing here ran on the GPU.

| Record | Command | Result |
|---|---|---|
| `facts_differential.json` | `python tools/checks/differential_facts.py --count 20000` | 20,000 inputs, seed 1: `facts.py` and `Facts.lean` agreed on all eight decisions and on every fact a quotient adds |
| `guards_differential.json` | `python tools/checks/differential_guards.py --programs 2000 --batch 50 --workers 3 --gcc --output ...` | 1,864 functions, 178,944 cases per compiler, clang++ with AddressSanitizer and UndefinedBehaviorSanitizer and g++ without: no case differed |
| `guards_differential_sanitized.json` | the same with `--sanitize-gcc` | the same functions and cases, both compilers under both sanitizers: no case differed |
| `identity.json` | `python tools/checks/emission_identity.py snapshot` at a39cd3d, then `compare` at 3fcf2c2, both with `--normalize guards` | 2,194 programs; 41 emit fewer guards, 13,465 in all before and 13,191 after; none emits more, and none changed otherwise |

## The Lean differential

The harness asks the real Python functions and their Lean transliterations the same generated questions: the six decisions of before (an index below its extent, `+`, `-`, a shift count, a narrowing to `u32`, a part inside its view), `product` against `mulOk` for a `*` of two generated operands, and an index `x5 * C` with `x5 < x4` after `let x4 = a / C`, which reads the facts `quotient` adds. It also compares those facts one by one, as text. In the 20,000 inputs each decision said yes somewhere: the multiply 668 times, the index after a quotient 1,686 times, and the quotient added 33,427 facts. `tests/verification/test_differential_facts.py` runs 300 inputs in the fast suite and plants a slip in each new rule (a quotient bounded by its dividend's lower bounds, a product taken without a cap, a sum's lower bound one too high); the comparison reports each of them within those 300.

## The guard differential

The generator now also writes products by a constant, `let q = a / C` with loops over `j` below `q` that read `x[j * C + off]` or add `j * C + off`, products `let w = src * C` beside the multiples below them, and sums taken apart again (`let s = a + b; s - b`). `k` near the largest usize makes each kind of guard fail somewhere. Of the 2,000 functions the checker refused 136, as it refuses a loop index named outside its loop or an owner bound twice, and each of the other 1,864 was built twice: as emitted, with the 4,606 guards the checker discharged and the audit accepted left out, and with every guard. Each function ran on 96 inputs in a process of its own. The two builds printed the same value, the same trap or the same status in every case, under each compiler and sanitizer setting above.

What this establishes is finite-tested agreement on these generated programs and inputs, and a model-level proof of the rule. It says nothing about shapes the generator does not write, such as lanes, records or cooperative regions; the bench programs below and `tests/soundness/test_established.py` cover some of those, and the audit decides every dropped guard in them again.

## Where the guards went

The 41 programs that emit fewer guards are listed in `identity.json` with their counts. The device examples lose the most: `examples/tensor/transpose.cairn` 136 to 67, `tile64.cairn` 114 to 69, `tile32.cairn` 73 to 40, and `examples/cooperative` 93 to 77. The three bench kernels that trail hand-written CUDA (`evidence/v1_1/device_perf`) are not among the programs `emission_identity.py` covers; their discharged overflow sites, from each function's receipt:

| Function | Overflow sites | Discharged at a39cd3d | Discharged at 3fcf2c2 |
|---|---|---|---|
| `bench/device/layernorm.cairn` `layernorm` | 15 | 0 | 6 |
| `bench/device/stencil.cairn` `jacobi_blocks` | 14 | 4 | 7 |
| `bench/device/reduce.cairn` `final_sum` | 3 | 0 | 2 |
| `bench/device/reduce.cairn` `block_sums` | 8 | 0 | 0 |
| `bench/device/reduce_wide.cairn` `sum_one_pass` | 16 | 0 | 4 |

`block_sums` indexes `(k * g + b) * 256 + t` with `g` a parameter, a product of two unknowns that no rule here bounds. The audit refused none of these proposals. One refusal turned up while this was built: a finish (`then threads t in 256 { }`) is checked as a region of one block, so its facts cite that region, while the audit keyed them by the finish statement. It refused the first proof that cited a finish's thread bound, `k * 256 + t` in `reduce_wide.cairn`'s finish, and kept the guard. The audit now keys a finish's facts by the region the checker checks it as.

Guard counts show what the C++ checks, not how fast it runs; no timing is part of this record.

## Products of two constants and of a product atom

Until `c13/facts-lean-agreement`, `facts.py` and `Facts.lean` read two shapes of product differently, and the Lean differential above never generated either. `facts.py:times` folded two constants (`2 * 3` is 6) and named a product atom times a constant as an atom of its own (`k*4*2`), where `Facts.lean`'s `exact` gave no term for either. The audit in `verify/elision.py` sided with Lean: it refused every proposal that rested on either shape and kept the guard, so no guard was left out on a rule Lean does not prove, but such a program's receipt counted the refusal under `refused_discharges`. `x[2 * 3]` in a view of 8, `let q = n / (2 * 4)` with `x[k * 8]` below it, and `if k * 4 * 2 < n { return x[k * 4 * 2]; }` each had one.

Now the three read a product alike. Two constants multiply in `Facts.lean`, whose `exact_sound` and `exact_mul` cover the case, and in the audit. A product atom times a constant is no term in `facts.py`, as in the other two. That second shape is narrowed rather than modelled: naming `(k * 4) * 2` as the atom `k*8` would also let the lane rule place `out[b * 4 * 2 + j]` inside lane `b`'s block, which it refuses today, and a rule that accepts more programs is a language change of its own.

| Record | Command | Result |
|---|---|---|
| `facts_differential_products.json` | `python tools/checks/differential_facts.py --count 20000` | 20,000 inputs, seed 1: `facts.py` and `Facts.lean` agreed on all nine decisions and on every fact a quotient adds |
| `identity_products.json` | `emission_identity.py snapshot` with the compiler of fd64dd2 over this branch's programs, then `compare` on the branch, both with `--normalize guards` | 2,289 programs; none changed otherwise, none emits more guards, and 4 emit fewer, 16,071 guards in all before and 16,066 after |
| `guards_differential_products.json` | `python tools/checks/differential_guards.py --programs 2000 --batch 50 --workers 3 --gcc --sanitize-gcc --output ...` | 1,869 functions, 179,424 cases per compiler, clang++ and g++ both under AddressSanitizer and UndefinedBehaviorSanitizer, with the 4,592 guards the checker discharged and the audit accepted left out: no case differed |

The Lean differential now writes both shapes among its expressions and as the two sides of a `*`, writes the quotient's multiple `x5 * C` four ways (either way round, with the divisor as `1 * C`, and times 1 again), and asks a ninth decision, a product minus itself, which only the two sides' exact terms decide; it said yes 11,588 times in the 20,000 inputs. `tests/verification/test_differential_facts.py` plants the two old readings in `facts.py`, a product atom times a constant named as an atom and two constants left unfolded, and the comparison reports each within 300 inputs. The guard differential's divisors now include `(2 * 4)` and `(2 * 128)`, and a multiple below a quotient is sometimes written `j * 1 * C`.

Of the four programs that emit fewer guards, three are the tests written for this change; the fourth, in `tests/language/test_layouts.py`, loses the guard on `16 * 18 - 2`, whose right side the audit now reads as 288. No example, `std` module or docs block changed.

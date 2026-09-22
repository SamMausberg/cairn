// The safety boundary a baseline must carry to be compared with CAIRN at all.
//
// Boundaries 1 to 4 of bench/suite/PREREGISTRATION.md are written here once and included by every
// baseline arm. Each macro forwards to the very function src/cairn/compiler/codegen.py emits at
// that site, so a guarded baseline does not imitate a CAIRN guard, it calls it. Build with
// -DBENCH_GUARDED=1 for the guarded arm and -DBENCH_GUARDED=0 for the unguarded one; nothing else
// about the two builds differs, so the pair prices the boundary and nothing else.
//
// -DBENCH_GUARDED=2 is the matched arm of the 1.4 addendum: it keeps exactly the categories named by
// -DBENCH_KEEP_ENTRY, _ELEMENT, _ARITHMETIC and _CONVERSION, which bench/suite/harness.py sets to the
// categories the emitted CAIRN kernel still guards once the checker's established sites are gone.
//
// Boundaries 5 and 6 are not expressible as macros: they are the strict floating contract and the
// build line, which src/cairn/projects/toolchain.py owns and bench/suite/harness.py passes to every
// arm unchanged.
#pragma once
#include "cairn_owners.hpp"
#include "cairn_runtime.hpp"

#if BENCH_GUARDED != 2
#define BENCH_KEEP_ENTRY BENCH_GUARDED
#define BENCH_KEEP_ELEMENT BENCH_GUARDED
#define BENCH_KEEP_ARITHMETIC BENCH_GUARDED
#define BENCH_KEEP_CONVERSION BENCH_GUARDED
#endif

// Emitter.function in codegen.py writes cr::view once per array parameter and cr::disjoint once per pair that
// includes a writer; Emitter.e_index writes cr::at at every element access; Emitter.e_binary
// writes cr::add/sub/mul/divide at every checked integer operation; builtins.lower_convert writes
// cr::convert for a narrowing integer conversion and cr::truncate for float to integer.
#if BENCH_KEEP_ENTRY
#define BG_VIEW(p, n) cr::view(p, n)
#define BG_DISJOINT(a, na, b, nb) cr::disjoint(a, na, b, nb)
#else
#define BG_VIEW(p, n) ((void)(p), (void)(n))
#define BG_DISJOINT(a, na, b, nb) ((void)(a), (void)(na), (void)(b), (void)(nb))
#endif
#if BENCH_KEEP_ELEMENT
#define BG_AT(p, i, n) cr::at(p, i, n)
// x[lo..hi] handed to a callee expecting `want` elements: cairn_owners.hpp checks the part once and
// returns a plain pointer, which is what the emitter writes for a visibly disjoint slice.
#define BG_PART(p, lo, hi, n, want) cr::part(p, lo, hi, n, want)
#else
// The same program with the boundary removed. An ordinary C++ baseline is written this way, and the
// difference between the two columns is what the boundary costs on this machine.
#define BG_AT(p, i, n) ((void)(n), (p)[i])
#define BG_PART(p, lo, hi, n, want) ((void)(hi), (void)(n), (void)(want), (p) + (lo))
#endif
#if BENCH_KEEP_ARITHMETIC
#define BG_ADD(T, a, b) cr::add<T>(a, b)
#define BG_SUB(T, a, b) cr::sub<T>(a, b)
#define BG_MUL(T, a, b) cr::mul<T>(a, b)
#define BG_DIVIDE(T, a, b) cr::divide<T>(a, b)
// A shift is checked too: cr::shr traps on a count at or beyond the width, which is the third
// boundary's remaining site and the one the receipt calls `shift`.
#define BG_SHR(T, a, n) cr::shr<T>(a, n)
#else
#define BG_ADD(T, a, b) static_cast<T>((a) + (b))
#define BG_SUB(T, a, b) static_cast<T>((a) - (b))
#define BG_MUL(T, a, b) static_cast<T>((a) * (b))
#define BG_DIVIDE(T, a, b) static_cast<T>((a) / (b))
#define BG_SHR(T, a, n) static_cast<T>((a) >> (n))
#endif
#if BENCH_KEEP_CONVERSION
#define BG_CONVERT(T, x) cr::convert<T>(x)
#define BG_TRUNCATE(T, x) cr::truncate<T>(x)
#else
#define BG_CONVERT(T, x) static_cast<T>(x)
#define BG_TRUNCATE(T, x) static_cast<T>(x)
#endif

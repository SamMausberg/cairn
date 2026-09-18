# Lean 4 proofs for the bounded collector

This is a machine-checked Lean 4 development about one construct: CAIRN's
bounded collector,

```
let used = compact out for i in n where pred(i) yield proj(i);
```

which lowers to the guardless loop

```c
k = 0;
for (i = 0; i < n; ++i) { if (pred(i)) { out[k] = proj(i); ++k; } }
used = k;
```

where `out` has capacity exactly `n` and `pred`/`proj` never read `out`.  The
store `out[k]` is emitted with **no** dynamic bounds check; what licenses that is
the cursor invariant `0 <= k <= i <= n <= M`, where `M` is the largest
representable cursor.

It is not a whole-compiler proof.  Read "What is NOT proved" before quoting
anything from here.

## Layout

| File | Contents |
| --- | --- |
| `Cairn/Affine.lean` | `Form`, `Form.eval`, `Rule`, `Certificate`, the computable `check`, and `check_sound`. |
| `Cairn/CollectorCertificates.lean` | **Generated.** The 17 obligations and their certificates as Lean data, `all_checked`, and one corollary per obligation. |
| `Cairn/Collector.lean` | Executable model of the loop; the invariant derived *from* the certificates; store-in-bounds, stable selection, and increment bounds. |
| `Cairn/Audit.lean` | `#print axioms` for every headline theorem. |
| `Cairn.lean` | Root module importing everything. |

No dependencies.  Lean 4 core only (`omega`, `decide`, `simp`, `Int`/`Nat`/`List`
lemmas); **Mathlib is deliberately not used**, so the trusted base is the pinned
Lean toolchain and nothing else, and the whole thing builds in about two seconds.

## What is proved

### 1. The certificate checker is sound

`check r c` accepts a rule/certificate pair exactly when, coefficient-wise,
`conclusion = (c0, 0, 0, 0, 0) + sum_j w_j * assumption_j` with every `w_j >= 0`
and `c0 >= 0` (a transcription of `check()` in
`src/cairn/linear_certificates.py`).  Soundness is universal over integer
assignments, not an enumeration of states:

```lean
theorem Cairn.check_sound {r : Rule} {c : Certificate} (h : check r c = true) :
    ∀ K I N M : Int, (∀ a ∈ r.assumptions, 0 ≤ a.eval K I N M) →
      0 ≤ r.conclusion.eval K I N M
```

### 2. All seventeen shipped certificates are accepted, by kernel computation

```lean
theorem Cairn.Collector.all_checked :
    certificates.all (fun rc => check rc.1 rc.2) = true := by decide
theorem Cairn.Collector.certificates_length : certificates.length = 17 := by decide
```

`decide`, not `native_decide`: the Lean kernel replays the exact integer
arithmetic.  Applying `check_sound` to each pair turns each named obligation into
a Lean theorem about arbitrary integers, e.g.

```lean
theorem Cairn.Collector.obligation_store_strictly_below_capacity (K I N M : Int)
    (h0 : 0 ≤ Form.eval ⟨0, 1, 0, 0, 0⟩ K I N M)
    (h1 : 0 ≤ Form.eval ⟨0, -1, 1, 0, 0⟩ K I N M)
    (h2 : 0 ≤ Form.eval ⟨0, 0, -1, 1, 0⟩ K I N M)
    (h3 : 0 ≤ Form.eval ⟨0, 0, 0, -1, 1⟩ K I N M)
    (h4 : 0 ≤ Form.eval ⟨-1, 0, -1, 1, 0⟩ K I N M) :
    0 ≤ Form.eval ⟨-1, -1, 0, 1, 0⟩ K I N M
```

### 3. The invariant, derived from those obligations

`Inv K I N M` bundles `0 ≤ K`, `K ≤ I`, `I ≤ N`, `N ≤ M`.  Each transition
theorem is proved **by applying the corresponding certified obligation**, not by
re-deriving the arithmetic with `omega`.  `omega` appears only to translate
between the affine encoding `c + ck*K + ci*I + cn*N + cm*M >= 0` and ordinary
inequalities, and between `Nat` indices and their `Int` images.

```lean
theorem Inv.init  (hn : 0 ≤ N) (hm : N ≤ M) : Inv 0 0 N M          -- initial.*
theorem Inv.emit  (h : Inv K I N M) (hlt : I < N) : Inv (K+1) (I+1) N M  -- emit.invariant.0-3
theorem Inv.skip  (h : Inv K I N M) (hlt : I < N) : Inv K (I+1) N M      -- skip.invariant.0-3
theorem Inv.store_nonneg         (h : Inv K I N M) (hlt : I < N) : 0 ≤ K   -- store.nonnegative
theorem Inv.store_lt_capacity    (h : Inv K I N M) (hlt : I < N) : K < N   -- store.strictly_below_capacity
theorem Inv.emit_increment_fits  (h : Inv K I N M) (hlt : I < N) : K + 1 ≤ M -- emit.cursor_increment_fits
theorem Inv.step_increment_fits  (h : Inv K I N M) (hlt : I < N) : I + 1 ≤ M -- step.input_increment_fits
theorem Inv.exit_le_capacity     (h : Inv K I N M) : K ≤ N                 -- exit.output_count_bounded
```

### 4. The executable model and its proofs

`State β` carries `(out : List β, k : Nat, i : Nat)`; `step` performs
`out[k] = proj x; ++k` on a selected input and always `++i`; `run` folds `step`
over the inputs; `collect` starts both cursors at zero.  The model really runs —
`Cairn/Collector.lean` contains a worked `decide`-checked example.

**(a) The invariant is preserved by every iteration** (each step uses only
`InvN.emit` / `InvN.skip`, i.e. the `emit.invariant.*` / `skip.invariant.*`
certificates):

```lean
theorem run_preserves_inv (pred : α → Bool) (proj : α → β) (n : Nat) (M : Int) :
    ∀ (xs : List α) (out : List β) (k i : Nat),
      InvN k i n M → i + xs.length ≤ n →
      InvN (run pred proj xs ⟨out, k, i⟩).k (run pred proj xs ⟨out, k, i⟩).i n M
```

**(b) Every store is in bounds, with no dynamic guard.**  For every position in
the input where the loop body runs — in particular every store:

```lean
theorem store_index_lt_capacity (pred : α → Bool) (proj : α → β)
    (xs : List α) (out : List β) (hcap : out.length = xs.length)
    (pre : List α) (x : α) (post : List α) (hsplit : xs = pre ++ x :: post) :
    (run pred proj pre ⟨out, 0, 0⟩).k < out.length

theorem store_index_lt_buffer_length (…same hypotheses…) :
    (run pred proj pre ⟨out, 0, 0⟩).k < (run pred proj pre ⟨out, 0, 0⟩).out.length
```

The second form is stated against the live buffer at the moment of the store, so
`List.set` (which is total and would silently discard an out-of-range write)
never discards anything.

**(c) Functional correctness — stable selection.**  With capacity exactly `n`:

```lean
theorem collect_spec (pred : α → Bool) (proj : α → β) (xs : List α) (out : List β)
    (hcap : out.length = xs.length) :
    (collect pred proj xs out).k = (xs.filter pred).length
    ∧ (collect pred proj xs out).k ≤ out.length
    ∧ (collect pred proj xs out).i = xs.length
    ∧ (collect pred proj xs out).out.length = out.length
    ∧ (collect pred proj xs out).out.take (collect pred proj xs out).k
        = (xs.filter pred).map proj
    ∧ (collect pred proj xs out).out.drop (collect pred proj xs out).k
        = out.drop (collect pred proj xs out).k
```

That is: the prefix `out[0..k)` is exactly `(inputs.filter pred).map proj` in
input order (stability), `k` is the number of selected inputs and is within
capacity, the buffer does not change length, and the tail `out[k..n)` is
untouched.  The general form, `run_spec`, gives the same statement from an
arbitrary `(k, i)` with `k ≤ i` and enough room left.

**(d) Neither increment overflows.**  With the capacity representable, `n ≤ M`:

```lean
theorem increments_fit (pred : α → Bool) (proj : α → β)
    (xs : List α) (out : List β) (hcap : out.length = xs.length)
    (M : Int) (hM : (out.length : Int) ≤ M)
    (pre : List α) (x : α) (post : List α) (hsplit : xs = pre ++ x :: post) :
    ((run pred proj pre ⟨out, 0, 0⟩).k : Int) + 1 ≤ M
    ∧ ((run pred proj pre ⟨out, 0, 0⟩).i : Int) + 1 ≤ M
```

## What is NOT proved

This development proves things about a **model**.  Each of the following remains
trusted, exactly as before:

* **The compiler-to-model correspondence.**  Nothing here relates
  `Cairn.Collector.step`/`run` to what `src/cairn` actually emits.  That the
  Python emitter produces this loop, with this capacity and these cursor
  updates, is trusted code review, not a theorem.
* **The generated C++ and the native code.**  No refinement theorem connects the
  model to the emitted C++, to the machine instructions a C++ compiler produces
  from it, to the runtime, or to any target memory model.
* **Machine arithmetic.**  The model uses mathematical `Int`/`Nat`.  `M` is a
  parameter standing for "largest representable cursor"; the theorems say the
  cursors stay `≤ M`, they do not model wrapping, `size_t`, or pointer
  arithmetic.
* **Alias, lifetime and initialization safety.**  That `out` is live, uniquely
  owned, correctly sized and initialized, and that `pred`/`proj` do not read or
  alias `out`, are assumptions of the model, not conclusions.
* **Everything else in the compiler.**  Parser, typechecker, allocator, foreign
  callers, operating system.  Proving one relation confers nothing on them.
* **The Python checker itself.**  `check_sound` is proved about the Lean
  transcription of `check()`.  That the transcription matches the Python byte
  for byte is human-checked; the Python-only guards it omits (rejecting
  non-`int` inputs and integers wider than 4096 bits) are representation hygiene
  for a dynamically typed host and carry no mathematical content.  The rule data
  itself is *generated* from the Python, so it cannot drift silently — see
  "Drift" below.

## Commands

```sh
# One-time toolchain install (binaries land in ~/.elan/bin)
curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y --default-toolchain none

# Build everything (elan reads proofs/lean-toolchain and fetches v4.34.0)
cd proofs && lake build

# Axiom audit on its own
cd proofs && lake env lean Cairn/Audit.lean

# Regenerate the certificate module from the Python rules
.venv/bin/python tools/export_lean_certificates.py

# Fail if the Lean file has drifted from the Python rules
.venv/bin/python tools/export_lean_certificates.py --check

# Both, plus the build and the axiom audit, under pytest
.venv/bin/python -m pytest -q tests/test_lean_proofs.py
```

## Pinned versions

| Component | Version |
| --- | --- |
| Lean | 4.34.0 (`leanprover/lean4:v4.34.0`, commit `293d5d0c0c3f3dded4688b3ccd6a33939ac5102b`) |
| Lake | 5.0.0-src+293d5d0 |
| elan | 4.2.4 |
| Dependencies | none (`lake-manifest.json` lists no packages; no Mathlib) |
| Host of record | Linux aarch64 (GH200) |

A clean `lake build` (after `rm -rf .lake/build`) takes about **1.9 s** wall
clock on the host of record.

## Axiom audit result

`Cairn/Audit.lean` runs `#print axioms` on 37 declarations: `check_sound`,
`check_sound'`, `eval_combine_nonneg`, `forall_mem_of_satisfies`, `all_checked`,
`certificates_length`, all 17 `obligation_*` corollaries, the 8 `Inv.*`
transition theorems, and `run_preserves_inv`, `run_spec`, `collect_spec`,
`store_index_lt_capacity`, `store_index_lt_buffer_length`, `increments_fit`.

The result, captured verbatim in `evidence/v1_0/lean/print-axioms.txt`:

* `Cairn.Collector.certificates_length` — **does not depend on any axioms**.
* `Cairn.forall_mem_of_satisfies` and `Cairn.Collector.all_checked` — `[propext]`.
* Every other declaration — `[propext, Quot.sound]`.

So the only axioms anywhere in this development are `propext` and `Quot.sound`,
both of which arrive through core-library lemmas and the `omega`/`simp`/`decide`
tactics.  **`Classical.choice` does not appear.**  There is no `sorryAx`
(no `sorry`), no `Lean.ofReduceBool` (no `native_decide`), no `axiom`
declaration, no `unsafe` and no `implemented_by`;
`tests/test_lean_proofs.py::test_lean_sources_contain_no_escape_hatches`
enforces that against the sources, and the build test enforces it against the
audit output.

## Drift

`Cairn/CollectorCertificates.lean` is generated by
`tools/export_lean_certificates.py` from `collector_rules()`.  Its header records
the SHA-256 of the exported obligation table, the same digest
`cairn certificates` reports (`5648cb8f…c1f2` at the time of writing).  Editing
the Python rules without regenerating makes
`tools/export_lean_certificates.py --check` — and therefore
`tests/test_lean_proofs.py` — fail.

## Evidence

`evidence/v1_0/lean/` holds the captured `lake build` log (with timing), the
`#print axioms` output, `lean --version`/`lake --version`/`elan --version`, and
`summary.json` (theorem-by-theorem axiom lists, toolchain, date, host, source
hashes).

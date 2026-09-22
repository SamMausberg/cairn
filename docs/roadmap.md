# Remaining gates

What is still missing is stated here as gates rather than plans, and [project/capabilities.json](project/capabilities.json) carries the same list as data. A gate closes only when its tests, its documentation and its evidence exist.

## Language

Recipes are library code over record schemas, naturals and names of functions. They do not take arbitrary expression fragments, no checked theorem is replayed per instance, and a recipe cannot yet declare a field extent (`price:Buf[f64][rows]`) in a record it generates, so `examples/apps/analytics` still passes its columns as parts.

The collector remains a closed, certified form: a user cannot write a loop that carries its own certificates.

Asynchronous I/O is a task over blocking I/O, and tasks are awaited in the order they are written. The named next step is a linear `Group[T]`: `submit` queues a spawned function into the group and `collect` yields the next result to finish, so many I/O tasks can be awaited in completion order, while tickets stay linear and leases stay as they are. Cancellation, device-loss recovery and multi-device collectives are absent. Device regions and transfers do queue as linear stream tickets ordered with `after`.

A lease names the place that was lent, so two tasks may take two fields of one record, and a `Buf` field may declare an earlier `usize` field as its extent, so `len(c.price)` and `c.rows` are one identity along a field path rooted in a local. An element of an array of records (`cs[0].price`) is still passed as a part and pays its guard.

`alloc` is charged where storage is taken and `free` where the release runs, but a constructor's row still carries `free` beside `alloc`. Full independence of the two effects is a refinement, not a rule change, and is not taken.

Separate compilation is opt-in: `--incremental` keeps one object per module, reused by content hash. Device programs and freestanding images are one translation unit, and no cross-module inlining is attempted in that mode.

Bounds cover traits, kinds and closed scalar classes, every template of `std` is certified once against its bounds, and `cairn check --generics` holds a project to the same on request. Nothing requires it: a program's unbounded templates are still accepted per instance.

## Proof

The collector certificates and loop model are Lean-checked, and so is a core ownership and lease calculus over locals, record field paths, whole owners, headers, elements, array parts with visible bounds and `parallel` regions. An accepted program there has no use-after-move, use-after-free, double free, leaked ticket, aliased call argument or data race, never gets stuck, and frees every cell exactly once, under any interleaving and every valuation of those bounds and of the lane count.

That calculus is written by hand beside the checker, not extracted from it. `tools/checks/differential_ownership.py` requires the two to classify generated programs of a shared fragment identically, and twenty thousand programs have agreed; relating them by something stronger than that would close this.

It assumes of the emitter that a part's `lo <= hi` guard runs before the task that borrows it starts, and that a region completes before the next statement. Both are tested, not proved.

A single element, a part of a part and a part with an invisible bound are modelled as the elements, conservatively, and the classifications agree with the checker on the programs the regression names. Closures, `lane:f` callbacks, device placement, `reduce`, `compact`, queued device work and declared field extents are outside the calculus.

The emitter's correspondence to the loop model and native refinement are unproved. Proving that the emitted loop refines the model, or generating it from the model, would close the first.

The SMT model covers records, tag-only enums and payload sums with `match` and `try`, IEEE `f32` and `f64`, fixed local storage, array views with their parts, `rw` borrows, function-local heap scratch, `compact`, host `reduce`, and loops it can unroll within a sixteen-iteration budget, with a per-function precondition where a symbolic extent needs one. It admits any tag in storage and any tag nested inside a value parameter, as the emitter does, and guards only the top-level tag of a value parameter, as the emitter does. It still refuses an owner that moves, recursion, tasks, lanes, device placement, closures, `dyn` and the foreign boundary, and reports as unknown an unbounded trip count and an observed NaN. An owner that moves is the named next SMT step.

[verification.md](verification.md) states what each model contains, what it assumes and what it leaves out.

## Performance

`evidence/v1_0/gpu/benchmark.json` is one machine and three kernels; device wins depend on transfer cost. `evidence/v1_2/host_regions` puts the size at which a host region beats the loop at about a hundred thousand cheap elements, or thirty thousand dearer ones, on one machine under both compilers.

`bench/suite/` is the preregistered CPU baseline suite: eight kernels against plain C++, OpenMP and oneTBB, each baseline built once guarded and once not, at equal worker counts, with safety boundaries counted from the build receipt and losses printed beside wins. Its first run is recorded under `evidence/v1_3/bench/` with what was not measured named. Nothing is claimed against tuned C++ or CUDA, nothing has run across more than one memory domain, and no other host has been measured.

## AI evidence

The edit protocol, packets and rule cards cover the whole language. One preregistered pilot has run (`evidence/v1_1/ai_pilot`): nine fresh subjects of one model family, given only the rule cards and compiler diagnostics, solved nine of nine small tasks against hidden tests. It shows the cards suffice for that; it shows no advantage over anything.

No experiment with equal budgets against C++ and Rust tooling, other model families or larger programs has run. Token counts are still byte counts. No corpus or deterministic search substitutes for that experiment.

## Packaging

A project can vendor other projects inside its root (`[dependencies]`), pinned by hash in every receipt. There is no registry, no version resolution and no fetching, by design. The package is installable from a checkout and builds as a wheel; it is not on any index.

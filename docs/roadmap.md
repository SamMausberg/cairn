# Remaining gates

Each gate below is open. A gate closes when its tests, its documentation and its evidence exist. [project/capabilities.json](project/capabilities.json) has the same list as data.

## Language

Recipes are library code over record schemas, naturals and names of functions. They cannot take arbitrary expression fragments, and no checked theorem is replayed for each instance. A recipe may declare a field extent in a record it generates (`$f:Buf[$t][rows]`), which is how `examples/apps/analytics` passes its columns whole.

The collector is a closed, certified form. A user cannot write a loop that carries its own certificates.

A demanding runtime needs four things from the language: bounded execution, resources it can reuse, ownership that holds across asynchronous work, and failure it handles explicitly. Task groups bound the tasks in flight and keep what they borrow leased across the asynchronous part. An I/O ring (`IoRing(n)`) keeps kernel reads, writes, sends, receives and accepts in flight from one thread, each owning its buffer until it is collected, with timeouts and cancellation by tag. A spawn reuses a parked task thread instead of starting one (`evidence/v1_4/runtime`), and an I/O ring allocates nothing per operation, but device work still makes a stream per ticket and allocates per reduction: `cr::gpu::Context` holds the reuse, tested against a mock device, and the lowering adopts it once a `make gpu` run has checked it. Three gates stay open beside that one: cancelling a task, recovery from the loss of a device, and collectives across devices. The last two cannot be exercised on the reference machine, which has one GPU and runs device code only under `make gpu`.

Blocking I/O can also run as a task. A task group (`Group[T](n)`, `spawn f(args) into g;`, `collect(g)`, `wait(g)`) returns its tasks in the order they finish. Groups are linear, lease what their tasks borrow exactly as tickets do, and take their storage where they are declared. A task cannot be cancelled, `collect` releases no lease before `wait(g)`, and a group lives on the host. The calculus models groups, including what a group holds after a branch join; a group submitted to inside a loop is tested and not modelled. Device regions and transfers queue as linear stream tickets ordered with `after`.

A lease names the place that was lent, so two tasks may take two fields of one record. A `Buf` field may declare an earlier `usize` field as its extent, which makes `len(c.price)` and `c.rows` one identity along a field path rooted in a local. An element of an array of records (`cs[0].price`) is still passed as a part and pays its guard.

`alloc` is charged where storage is taken and `free` where it is released, but a constructor's row still carries `free` beside `alloc`. Separating the two effects completely would refine the rows without changing a rule, and has not been done.

A plan sets how a function's regions run without changing a result: `grain` and `lanes` on the host, `block`, `per_lane` and `unroll` on the device, and `fuse` for adjacent regions whose bodies cannot trap and write nothing another could observe. `cairn tune` ranks the legal plans by prediction and times the best few on the host. Tiling, vector width, shared-memory staging, fusion of bodies that can trap and plans for queued device regions are open, and a device plan's behaviour test runs only under `make gpu`, which has not run it.

Separate compilation is opt-in: `--incremental` keeps one object per module and reuses it by content hash. Device programs and freestanding images are one translation unit, and that mode does no inlining across modules.

Bounds cover traits, kinds and closed scalar classes. Every template of `std` is certified once against its bounds, and `cairn check --generics` holds a project to the same standard on request. Nothing requires it: an unbounded template in a program is still accepted instance by instance.

## Proof

The collector certificates and the loop model are Lean-checked. So is a core calculus of ownership and leases over locals, record field paths, whole owners, headers, elements, array parts with visible bounds, task groups and `parallel` regions whose lanes own elements or blocks of one stride. An accepted program of that calculus has no use after move, use after free, double free, leaked ticket or group, use of a group after `wait`, aliased call argument or data race, never gets stuck, and frees every cell exactly once, under any interleaving and every valuation of the bounds and the lane count.

The guard-elision rule of `compiler/facts.py` is Lean-checked in `proofs/Cairn/Facts.lean`: a discharged index is in bounds and a discharged `+`, `-`, shift or narrowing cannot trap, wherever the facts in scope hold. That the checker keeps only true facts in scope is tested, and a differential run ties the Python rule to the Lean one. Every guard lowering leaves out is decided again by `verify/elision.py` from the facts it cites, without calling `facts.py`, and the conservative and optimized builds of 1,821 generated functions agreed on 174,816 cases per compiler (`evidence/v1_4/guards`). The audit's own rules are hand-written and are not in Lean.

The calculus is written by hand beside the checker. `tools/checks/differential_ownership.py` requires the two to classify generated programs of a shared fragment identically. Twenty thousand programs agreed on the run recorded in `evidence/v1_4/lean/differential.json`; 13,307 of them declare a task group, 3,731 submit to one inside a branch, and 1,711 have lanes write a block. A stronger link than that between the two is open.

The calculus blocks the spawner while a region runs, and `proofs/Cairn/Region.lean` proves that the lane pool's protocol does so: every index runs once, no worker is inside when the region returns, and a region returns without waiting for a worker to arrive. What stays assumed is that `cairn_parallel.hpp` performs those steps under the memory orders it uses, and that a part's `lo <= hi` guard runs on the spawning thread before the task starts. The second rests on C++ evaluating a lambda's captures where the lambda is written, and `tests/soundness` pins the emitted shape for `spawn` and for `spawn ... into g`.

A single element, a part of a part and a part with an invisible bound are modelled conservatively as the whole element range, and the regression programs show the checker classifying them the same way. Closures, `lane:f` callbacks, device placement, `reduce`, `compact`, queued device work and declared field extents are outside the calculus.

Nothing proves that the emitted collector loop refines the Lean model, or that native code refines the emitted C++. Proving the first, or generating the loop from the model, would close that gate.

The SMT model covers records, tag-only enums and payload sums with `match` and `try`, IEEE `f32` and `f64`, fixed local storage, array views and their parts, `rw` borrows, function-local heap scratch, `compact`, host `reduce`, and loops it can unroll within sixteen iterations, with a per-function precondition where a symbolic extent needs one. It admits any tag in storage and any tag nested inside a value parameter, and guards only the top-level tag of a value parameter, exactly as the emitter does. It follows an owner that moves: `take` and `swap` on locals, an owner passed by value, and an owner that is returned, observed by its length and elements. It refuses an owner held inside a record, a sum or an array, recursion, tasks, lanes, device placement, closures, `dyn` and the foreign boundary, and answers `unknown` for an unbounded trip count, an observed NaN, the storage floats and quantization, and a function that asserts. Owners inside values are the next SMT step.

[verification.md](verification.md) says what each model contains, what it assumes and what it leaves out.

## Performance

`evidence/v1_0/gpu/benchmark.json` covers one machine and three kernels, and whether the device wins depends on transfer cost. `evidence/v1_2/host_regions` finds that a host region beats the loop from about a hundred thousand cheap elements, or thirty thousand dearer ones, on one machine under both compilers.

`bench/suite/` is the preregistered CPU baseline suite: eight kernels against plain C++, OpenMP and oneTBB, each baseline built once with guards and once without, at equal worker counts, with safety boundaries counted from the build receipt and losses printed beside wins. Its first run, `evidence/v1_3/bench/` on a sixteen-thread x86-64 machine, found a CAIRN region level with OpenMP and oneTBB at equal guards wherever a region applies. It beat the guarded sequential loop by the preregistered margin from ten million elements on four kernels, and lost the three cases the preregistration predicted: the host `reduce` is a sequential fold, and the lane rule keeps a shared-bin histogram sequential. The guards cost a few percent at those sizes.

`cairn predict` prices a program from the work it counts and a machine profile, without building it. On one machine it was checked against host timings calibration never saw: a median error of 33 to 44 percent, a Kendall tau of 0.86 to 0.92 for its ranking, and wide host regions of 1e5 to 1e7 elements as its weak range (`evidence/v1_4/perf_model/`). Its device side is NVIDIA's published figures for one card. It gains a validation only from the owner's `make calibrate-device` and `make tune-device`, and neither has run, so no device prediction, plan or speed of light fraction is a measurement.

Nothing is claimed against tuned C++ or CUDA. Nothing has run across more than one memory domain, and only one x86-64 host and one AArch64 host have been measured.

## AI evidence

The edit protocol, the packets and the rule cards cover the whole language. One preregistered pilot has run (`evidence/v1_1/ai_pilot`): nine fresh subjects of one model family, given only the rule cards and compiler diagnostics, solved nine of nine small tasks against hidden tests. It had no comparison arm.

Packets now say how much of each callee's behaviour is established, from its interface alone to a reference Z3 checked when the session opened; `cairn state` stands in for a transcript when an agent resumes; and `cairn migrate` carries a signature change through every caller, in all files or none. Context is counted in `o200k_base` tokens, one real BPE vocabulary and not every model's. On thirty scripted edits a warm focused host now reads 0.84 of the tokens it read before these changes, and a refusal 0.68 (`evidence/v1_4/context/`). Every one of those numbers comes from authored transcripts: none shows that a model solves more tasks, or needs fewer turns, and no model has written a migration.

Open: the preregistered packet trial (`tools/ai/protocol_trial.md`), which needs fresh model subjects, and an experiment with equal budgets against C++ and Rust tooling, other model families and larger programs.

## Packaging

A project can vendor other projects inside its root (`[dependencies]`), pinned by hash in every receipt. There is no registry, no version resolution and no fetching. The package installs from a checkout and builds as a wheel, and is not on any package index.

A library builds with the C header of its checked entries and a ctypes binding, both tested on x86-64 Linux alone. No owner crosses the boundary yet, since no release function is generated for a `Buf`, and two CAIRN headers in one translation unit collide on their `ct_` type names.

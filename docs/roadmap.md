# Remaining gates

Each gate below is open. A gate closes when its tests, its documentation and its evidence exist. [project/capabilities.json](project/capabilities.json) has the same list as data.

## Language

Recipes are library code over record schemas, naturals and names of functions. They cannot take arbitrary expression fragments, and no checked theorem is replayed for each instance. A recipe may declare a field extent in a record it generates (`$f:Buf[$t][rows]`), which is how `examples/apps/analytics` passes its columns whole.

The collector is a closed, certified form. A user cannot write a loop that carries its own certificates.

A demanding runtime needs four things from the language: bounded execution, resources it can reuse, ownership that holds across asynchronous work, and failure it handles explicitly. Task groups bound the tasks in flight and keep what they borrow leased across the asynchronous part. An I/O ring (`IoRing(n)`) keeps kernel reads, writes, sends, receives and accepts in flight from one thread, each owning its buffer until it is collected, with timeouts and cancellation by tag. Three gates stay open: cancelling a task, recovery from the loss of a device, and collectives across devices. The last two cannot be exercised on the reference machine, which has one GPU and runs device code only under `make gpu`.

Blocking I/O can also run as a task. A task group (`Group[T](n)`, `spawn f(args) into g;`, `collect(g)`, `wait(g)`) returns its tasks in the order they finish. Groups are linear, lease what their tasks borrow exactly as tickets do, and take their storage where they are declared. A task cannot be cancelled, `collect` releases no lease before `wait(g)`, and a group lives on the host. The calculus models groups, including what a group holds after a branch join; a group submitted to inside a loop is tested and not modelled. Device regions and transfers queue as linear stream tickets ordered with `after`.

A lease names the place that was lent, so two tasks may take two fields of one record. A `Buf` field may declare an earlier `usize` field as its extent, which makes `len(c.price)` and `c.rows` one identity along a field path rooted in a local. An element of an array of records (`cs[0].price`) is still passed as a part and pays its guard.

`alloc` is charged where storage is taken and `free` where it is released, but a constructor's row still carries `free` beside `alloc`. Separating the two effects completely would refine the rows without changing a rule, and has not been done.

Separate compilation is opt-in: `--incremental` keeps one object per module and reuses it by content hash. Device programs and freestanding images are one translation unit, and that mode does no inlining across modules.

Bounds cover traits, kinds and closed scalar classes. Every template of `std` is certified once against its bounds, and `cairn check --generics` holds a project to the same standard on request. Nothing requires it: an unbounded template in a program is still accepted instance by instance.

## Proof

The collector certificates and the loop model are Lean-checked. So is a core calculus of ownership and leases over locals, record field paths, whole owners, headers, elements, array parts with visible bounds, task groups and `parallel` regions. An accepted program of that calculus has no use after move, use after free, double free, leaked ticket or group, use of a group after `wait`, aliased call argument or data race, never gets stuck, and frees every cell exactly once, under any interleaving and every valuation of the bounds and the lane count.

The calculus is written by hand beside the checker. `tools/checks/differential_ownership.py` requires the two to classify generated programs of a shared fragment identically. Twenty thousand programs agreed on the run recorded in `evidence/v1_4/lean/differential.json`; 13,384 of them declare a task group, and 3,738 submit to one inside a branch. A stronger link than that between the two is open.

The calculus blocks the spawner while a region runs, and `proofs/Cairn/Region.lean` proves that the lane pool's protocol does so: every index runs once, no worker is inside when the region returns, and a region returns without waiting for a worker to arrive. What stays assumed is that `cairn_parallel.hpp` performs those steps under the memory orders it uses, and that a part's `lo <= hi` guard runs on the spawning thread before the task starts. The second rests on C++ evaluating a lambda's captures where the lambda is written, and `tests/soundness` pins the emitted shape for `spawn` and for `spawn ... into g`.

A single element, a part of a part and a part with an invisible bound are modelled conservatively as the whole element range, and the regression programs show the checker classifying them the same way. Closures, `lane:f` callbacks, device placement, `reduce`, `compact`, queued device work and declared field extents are outside the calculus.

Nothing proves that the emitted collector loop refines the Lean model, or that native code refines the emitted C++. Proving the first, or generating the loop from the model, would close that gate.

The SMT model covers records, tag-only enums and payload sums with `match` and `try`, IEEE `f32` and `f64`, fixed local storage, array views and their parts, `rw` borrows, function-local heap scratch, `compact`, host `reduce`, and loops it can unroll within sixteen iterations, with a per-function precondition where a symbolic extent needs one. It admits any tag in storage and any tag nested inside a value parameter, and guards only the top-level tag of a value parameter, exactly as the emitter does. It follows an owner that moves: `take` and `swap` on locals, an owner passed by value, and an owner that is returned, observed by its length and elements. It refuses an owner held inside a record, a sum or an array, recursion, tasks, lanes, device placement, closures, `dyn` and the foreign boundary, and answers `unknown` for an unbounded trip count and an observed NaN. Owners inside values are the next SMT step.

[verification.md](verification.md) says what each model contains, what it assumes and what it leaves out.

## Performance

`evidence/v1_0/gpu/benchmark.json` covers one machine and three kernels, and whether the device wins depends on transfer cost. `evidence/v1_2/host_regions` finds that a host region beats the loop from about a hundred thousand cheap elements, or thirty thousand dearer ones, on one machine under both compilers.

`bench/suite/` is the preregistered CPU baseline suite: eight kernels against plain C++, OpenMP and oneTBB, each baseline built once with guards and once without, at equal worker counts, with safety boundaries counted from the build receipt and losses printed beside wins. Its first run, `evidence/v1_3/bench/` on a sixteen-thread x86-64 machine, found a CAIRN region level with OpenMP and oneTBB at equal guards wherever a region applies. It beat the guarded sequential loop by the preregistered margin from ten million elements on four kernels, and lost the three cases the preregistration predicted: the host `reduce` is a sequential fold, and the lane rule keeps a shared-bin histogram sequential. The guards cost a few percent at those sizes.

Nothing is claimed against tuned C++ or CUDA. Nothing has run across more than one memory domain, and only one x86-64 host and one AArch64 host have been measured.

## AI evidence

The edit protocol, the packets and the rule cards cover the whole language. One preregistered pilot has run (`evidence/v1_1/ai_pilot`): nine fresh subjects of one model family, given only the rule cards and compiler diagnostics, solved nine of nine small tasks against hidden tests. It had no comparison arm.

Open: an experiment with equal budgets against C++ and Rust tooling, other model families and larger programs. Token counts are still byte counts.

## Packaging

A project can vendor other projects inside its root (`[dependencies]`), pinned by hash in every receipt. There is no registry, no version resolution and no fetching. The package installs from a checkout and builds as a wheel, and is not on any package index.

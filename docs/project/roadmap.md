# Remaining acceptance gates

CAIRN 1.0 implements the breadth that 0.2 proposed, each feature with an application and with rejection and behaviour tests. The [README](../../README.md) lists what the language has.

What follows is what is still missing, stated as gates rather than plans. [capabilities.json](capabilities.json) carries the same list as data.

## Language

- Recipes are library code over record schemas, naturals and names of functions (`std.wire` replaced the closed wire generator byte for byte). They do not take arbitrary expression fragments and no checked theorem is replayed per instance, so a recipe's guarantee is that its output is checked like any other code.
- The collector remains a closed, certified form: a user cannot write a loop that carries its own certificates.
- Asynchronous I/O is a task over blocking I/O. A completion queue, cancellation, device-loss recovery and multi-device collectives are absent. Device regions and transfers do queue as linear stream tickets ordered with `after`.
- Separate compilation is opt-in: `--incremental` keeps one object per module, reused by content hash. Device programs and freestanding images are still one translation unit, and no cross-module inlining (LTO) is attempted in that mode.
- Bounds cover traits, kinds (`copy`, `affine`) and closed scalar classes, every template of `std` is certified once against its bounds, and `cairn check --generics` holds a project to the same on request, certifying a natural parameter through the instances its families name. Nothing requires it: a program's unbounded templates are still accepted per instance.

## Proof

- The collector certificates and loop model are Lean-checked, and so is a core ownership and lease calculus over locals, record field paths, whole owners, headers, elements, array parts with visible bounds and `parallel` regions. An accepted program there has no use-after-move, use-after-free, double free, leaked ticket, aliased call argument or data race, never gets stuck, and frees every cell exactly once, under any interleaving and every valuation of those bounds and of the lane count.
- That calculus is written by hand beside `checking.py`, not extracted from it. Relating the two by something stronger than review would close this.
- It assumes of the emitter that a part's `lo <= hi` guard runs before the task that borrows it starts, and that a region completes before the next statement. Both are tested, not proved.
- Single elements, parts of parts, closures, `lane:f` callbacks, device placement, `reduce`/`compact` and queued device work are outside the calculus.
- The emitter's correspondence to the loop model and native refinement are unproved. Proving that the emitted loop refines the model, or generating it from the model, would close the first.
- The SMT model covers records, tag-only enums and payload sums with `match` and `try`, IEEE `f32`/`f64`, fixed local storage, array views with their parts, `rw` borrows, function-local heap scratch, `compact`, host `reduce`, and loops it can unroll within a sixteen-iteration budget. It still rejects an owner that moves, recursion, tasks, lanes, device placement, closures, `dyn` and the foreign boundary, and reports as unknown rather than equal a trip count it cannot bound (which a pass over a symbolic extent is, until a precondition bounds it), a tag inside a view, two views of one array in one call, and an observed NaN.

[verification.md](../internals/verification.md) states what each model contains, what it assumes and what it leaves out.

## Performance

- `evidence/v1_0/gpu/benchmark.json` is one machine and three kernels; device wins depend on transfer cost.
- Host regions no longer create threads per statement: the first region of a process builds a lane pool and the rest reuse it. That moved the size at which a region beats the sequential loop from about ten million cheap elements to about a hundred thousand, and from three million to thirty thousand for a body costing about one and a half nanoseconds an element (`evidence/v1_2/host_regions`, one machine, both compilers). A region below sixteen thousand elements is still the loop it replaces, by design and by measurement.
- What remains unmeasured is the shape of the win: no tuned C++, OpenMP or TBB baseline has been built with equal flags and equal safety boundaries, nothing has been run on x86-64 or across more than one memory domain, and the two lane bodies measured both touch one element and nothing else. No claim is made against tuned C++ or CUDA baselines. A preregistered suite with equal safety boundaries would close this.

## AI evidence

- The edit protocol, packets and rule cards cover the whole language. One preregistered pilot has run (`evidence/v1_1/ai_pilot`): nine fresh subjects of one model family, given only the rule cards and compiler diagnostics, solved nine of nine small tasks against hidden tests, eight on the first compile, including a recipe for a feature designed that day. It shows the cards suffice for that; it shows no advantage over anything.
- No experiment with equal budgets against C++ and Rust tooling, other model families or larger programs has run. Token counts are still byte counts. No corpus or deterministic search substitutes for that experiment.

## Packaging

- A project can vendor other projects inside its root (`[dependencies]`), pinned by hash in every receipt. There is no registry, no version resolution and no fetching, by design.

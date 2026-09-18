# Remaining acceptance gates

CAIRN 1.0 implements the breadth that 0.2 proposed: generic types and functions, traits with static and explicit dynamic dispatch, affine and linear owners with second-class borrows, growable containers written in the language, typed error propagation, closures, modules with a packaged standard library, scoped tasks with leases, atomics and mutexes, race-free parallel regions on CPU threads and CUDA lanes with placement types, a foreign boundary with mandatory effects, and a freestanding profile. Each arrived with an application and with rejection and behavior tests. What follows is what is still missing, stated as gates rather than plans.

**Language.** Typed recipes written by library authors (the general form of `family`, `derive wire` and the collector) are not implemented; the three closed generators remain. Asynchronous I/O with completion tickets (tasks over blocking I/O exist), multi-device collectives and a checked integer reduction are absent. Separate compilation exists as one translation unit per program; per-module objects with interface digests are the next build step. Generic code is checked per instance, not once against its bounds.

**Proof.** The collector certificates and loop model are Lean-checked; the emitter's correspondence to that model, the ownership and lease calculus, and native refinement are not. The scalar SMT model still rejects memory, loops, sums and floats.

**Performance.** `evidence/v1_0/gpu/benchmark.json` is one machine and three kernels. Host regions create threads per statement and lose below roughly ten million cheap elements; device wins depend on transfer cost. No claim is made against tuned C++/CUDA baselines. That needs a preregistered suite with equal safety boundaries.

**AI.** The edit protocol, packets and rule cards cover the whole language, but no fresh-model experiment with equal budgets against C++ and Rust tooling has run. Token counts are still byte counts. No corpus or deterministic search substitutes for that experiment.

// CAIRN device execution: what generated code calls for device work, written once for the machine the including
// header names as cr::gpu::Machine. cairn_gpu.hpp names CUDA; tests/runtime/gpu_host.hpp names a host machine that
// counts every stream, allocation and wait, so the suite runs generated device programs without a device.
//
// Every operation takes the calling thread's execution context, here() (cairn_reuse.hpp). A region runs on the
// context's synchronous lane and returns once that stream has finished it, never by stopping the whole device, so
// the host sees its results and a guard that fired in a lane aborts the process as before. A reduction's, scan's or
// compaction's temporaries come from the context's arena, and queued work borrows a lane until its wait. The
// context is made by the thread's first device operation and kept for the thread's life: a pipeline run again
// makes no stream, allocates no temporary and waits for nothing but its own work. A C host that owns a stream binds
// it (bind below), and the thread's synchronous work then runs on that stream, after what the host queued there.
#pragma once
#include <cstring>
#include <type_traits>
#include "cairn_reuse.hpp"
namespace cr::gpu {

using reuse::Allocation;
using reuse::Budget;
using reuse::Dir;
using reuse::Scratch;
using reuse::Where;
using Context = reuse::Context<Machine>;
using Lent = reuse::Lent<Machine>;

// The calling thread's execution context. Its budget lets the arena grow to the largest request it has met, and it
// keeps what it grew to, so the arena is allocated again only when a larger request arrives.
inline Context& here() noexcept {
  thread_local Context held(Budget{0, reuse::UNBOUNDED});
  return held;
}

// A stream a C host owns, handed over as a cudaStream_t, for the calling thread's synchronous device work: that work
// runs after what the host queued there, and each call returns once its own work on it has finished. Null gives the
// thread its own stream back. `cairn build --header` declares this for C as NAME_device_stream.
inline void use_stream(void* stream) noexcept {
  if(stream) here().bind(static_cast<typename Machine::Stream>(stream));
  else here().unbind();
}

// Scoped owners, like cr::Buffer: zero initialized, released at scope exit, never copied, moved or returned. A
// failed allocation traps; n == 0 is legal and owns nothing. Device and unified storage is zeroed on the thread's
// synchronous lane, which is waited for, so later work on any stream sees zeros.
template<class T, Where W> class Owner final {
  T* p_ = nullptr;
public:
  explicit Owner(std::size_t n) noexcept {
    static_assert(std::is_trivially_copyable_v<T>);  // Zeroed bytes are a value; scalars, and the runtime's Sum.
    const std::size_t b = reuse::span<T>(n);
    if(!b) return;
    Context& ctx = here();
    p_ = static_cast<T*>(ctx.api().allocate(W, b));
    if constexpr(W == Where::pinned) std::memset(p_, 0, b);
    else reuse::synchronous(ctx, [&](typename Machine::Stream s) { ctx.api().zero(p_, b, s); });
  }
  ~Owner() noexcept {
    if(p_) here().api().release(W, p_);
  }
  Owner(const Owner&) = delete;
  Owner& operator=(const Owner&) = delete;
  Owner(Owner&&) = delete;
  Owner& operator=(Owner&&) = delete;
  T* data() const noexcept { return p_; }
};
template<class T> using Buffer = Owner<T, Where::device>;
template<class T> using Pinned = Owner<T, Where::pinned>;
template<class T> using Unified = Owner<T, Where::unified>;

// `parallel i in n` over device views: every i below n runs exactly once, whatever the block and the indices per
// thread a plan chose, and the call returns once the context's stream has run them.
template<unsigned U = 1, class F>
inline void run(Context& ctx, std::size_t n, F body, unsigned block = reuse::BLOCK, std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<F>, "a lane body crosses over as kernel arguments");
  if(!n) return;
  reuse::synchronous(ctx, [&](typename Machine::Stream s) { ctx.api().template lanes<U>(n, body, s, block, per_lane); });
}

// `plan f { vector W; }` (compiler/chunks.py): a lane runs W adjacent indices over one W-wide chunk of each array
// it touches only at [i], a single aligned load before its body and a single store after, where the scalar lanes
// made W of each. One check at launch decides the whole region: every chunked pointer on its chunk's width, or the
// scalar lanes run instead. The last indices past a whole chunk run one at a time, so every index runs once.
template<class T, unsigned W> struct alignas(sizeof(T) * W) Chunk final {
  static_assert((sizeof(T) * W & (sizeof(T) * W - 1)) == 0 && sizeof(T) * W <= 16, "one access of 16 bytes at most");
  T v[W];
  CR_HD static Chunk load(const T* p) noexcept { return *reinterpret_cast<const Chunk*>(p); }
  CR_HD void store(T* p) const noexcept { *reinterpret_cast<Chunk*>(p) = *this; }
  CR_HD T& operator[](unsigned k) noexcept { return v[k]; }
};
template<unsigned W, class... T> inline bool aligned(const T*... p) noexcept {
  return ((reinterpret_cast<std::uintptr_t>(p) % (sizeof(T) * W) == 0) && ...);
}
template<unsigned W, unsigned U = 1, class F, class G>
inline void run_vector(Context& ctx, std::size_t n, bool whole, F scalar, G chunk, unsigned block = reuse::BLOCK,
                       std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<F> && std::is_trivially_copyable_v<G>, "lanes cross as arguments");
  if(!whole) return run<U>(ctx, n, scalar, block, per_lane);  // a pointer off its chunk's width: the scalar lanes
  if(!n) return;
  reuse::synchronous(ctx, [&](typename Machine::Stream s) {
    ctx.api().template vector_lanes<W, U>(n, scalar, chunk, s, block, per_lane);
  });
}

// `plan f { stage R; }` (compiler/staging.py): a block runs its indices tile by tile, the same for every thread, so
// the barriers are reached by all of them. Each tile covers blockDim.x indices from `base`; `load` fills the block's
// shared memory with every staged array's elements from base - R to base + blockDim.x + R that lie inside it, and
// `body` runs at the thread's index reading them there. Every index runs once, after its tile is loaded.
template<class T> CR_HD constexpr std::size_t tile_bytes(std::size_t width) noexcept {
  return (width * sizeof(T) + 15) / 16 * 16;  // each tile starts on 16 bytes
}
template<std::size_t R, unsigned U = 1, class L, class F, class S>
inline void run_staged(Context& ctx, std::size_t n, L load, F body, S bytes, unsigned block = reuse::BLOCK,
                       std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<L> && std::is_trivially_copyable_v<F>, "lanes cross as arguments");
  if(!n) return;
  reuse::synchronous(ctx, [&](typename Machine::Stream s) {
    ctx.api().template staged_lanes<R, U>(n, load, body, bytes, s, block, per_lane);
  });
}

// `transfer(dst, src)`: n elements across, on the context's stream, returning once they have crossed.
template<class T> inline void copy_on(Context& ctx, T* dst, const T* src, std::size_t n, Dir d) noexcept {
  if(!n) return;
  reuse::synchronous(ctx, [&](typename Machine::Stream s) { ctx.api().copy(dst, src, reuse::span<T>(n), d, s); });
}

// `reduce` over device views, with its result on the host. The runtime default budget has no limit, so over_budget
// cannot be answered here; were it, the operation traps as a failed allocation does.
template<class T, class Op, class F>
inline T reduce_on(Context& ctx, std::size_t n, T identity, Op op, F value) noexcept {
  T result = identity;
  if(reuse::reduce(ctx, result, n, identity, op, value) != Scratch::ok) trap();
  return result;
}
template<bool Exclusive, class T, class R, class Op, class F>
inline T scan_on(Context& ctx, R* out, std::size_t n, T identity, Op op, F value) noexcept {
  T total = identity;
  if(reuse::scan<Exclusive>(ctx, total, out, n, identity, op, value) != Scratch::ok) trap();
  return total;
}
template<class T, class P, class F>
inline std::size_t compact_on(Context& ctx, T* out, std::size_t n, P pred, F value) noexcept {
  std::size_t used = 0;
  if(reuse::compact(ctx, &used, out, n, pred, value) != Scratch::ok) trap();
  return used;
}

// Queued device work, `spawn parallel ... after t { }` and `spawn transfer(...)`: on a lane the context lends, after
// the tickets in `after...`, returned at once. The lane comes back at the ticket's wait, once its work is done.
template<class F, class... After> inline Lent queue(Context& ctx, std::size_t n, F body, const After&... after) noexcept {
  static_assert(std::is_trivially_copyable_v<F>, "a lane body crosses over as kernel arguments");
  Lent t(ctx);
  (t.follow(after.mark()), ...);
  if(n) ctx.api().template lanes<1>(n, body, t.stream(), reuse::BLOCK, 1);
  return t;
}
template<class T, class... After>
inline Lent queue_copy(Context& ctx, T* dst, const T* src, std::size_t n, Dir d, const After&... after) noexcept {
  Lent t(ctx);
  (t.follow(after.mark()), ...);
  if(n) ctx.api().copy(dst, src, reuse::span<T>(n), d, t.stream());
  return t;
}
inline void wait(Lent&& t) noexcept { std::move(t).wait(); }

// The context API a program or a test may use directly, with a context of its own and a budget it declares.
template<class F, class... After> inline Lent launch_on(Context& ctx, std::size_t n, F body, const After&... after) noexcept {
  return queue(ctx, n, body, after...);
}
template<class T, class Op, class F>
inline Scratch reduce(Context& ctx, T& result, std::size_t n, T identity, Op op, F value) noexcept {
  return reuse::reduce(ctx, result, n, identity, op, value);
}
template<class T, class Op, class F, class... After>
inline Lent reduce_to(Context& ctx, T* out, std::size_t n, T identity, Op op, F value, Scratch* answer,
                      const After&... after) noexcept {
  return reuse::reduce_to(ctx, out, n, identity, op, value, answer, after...);
}
template<class T, class P, class F>
inline Scratch compact(Context& ctx, std::size_t* used, T* out, std::size_t n, P pred, F value) noexcept {
  return reuse::compact(ctx, used, out, n, pred, value);
}

}  // namespace cr::gpu

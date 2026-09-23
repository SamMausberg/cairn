// CAIRN device runtime: scoped device owners, lane launches, linear stream tickets, reduce and
// stable compaction, and execution contexts that lend streams and scratch to queued work. The body of `parallel i in n` is the same lambda the host path runs; only
// the entry point differs. Every entry point is synchronous unless its name says otherwise, and
// any CUDA error - including a guard that fired in a lane - aborts the process.
//
// Build (CUDA 12.8 with CUB from the toolkit and CUDA 13.2 with CCCL 3 both pass; g++ and clang++
// as -ccbin alike). One command; the lines below are joined by spaces:
//
//   nvcc -std=c++20 -O3 --fmad=false -arch=sm_120
//        --extended-lambda --expt-relaxed-constexpr -Werror all-warnings
//        -Xcompiler -Wall,-Wextra,-Werror,-Wno-unused-parameter,-Wno-unused-variable,
//                   -Wno-unused-but-set-variable,-fexceptions,-fno-rtti,
//                   -ffp-contract=off,-fno-fast-math
//        prog.cu -o prog
//
// (-Xcompiler takes one comma separated word: the three indented lines are one argument.)
//
// -fexceptions is the one departure from the host contract, and only for a device program's host
// pass: CCCL 3 (CUDA 13) reaches thrust/system/cuda/detail/util.h from CUB's dispatch headers,
// and its throw sites and system_error.inl's catch are unguarded, so -fno-exceptions refuses to
// parse them. Nothing in this runtime throws; every guard still aborts the process.
//
// --fmad=false is the device half of -ffp-contract=off: no contraction is ever authorized.
// --expt-relaxed-constexpr is required, not cosmetic: without it std::numeric_limits<T>::min()
// and std::in_range in cr::divide/cr::convert are host-only and nvcc merely warns (#20013-D).
// -Werror all-warnings promotes nvcc's own host/device warnings to errors; it is what turns a
// silently skipped guard into a build failure. -arch names the device target the build resolved
// (projects/target.py), never native. `#include <cub/cub.cuh>` is NOT usable: it drags in Thrust's
// system_error.inl, which needs -fexceptions. The four narrow CUB headers below do not.
//
// Device trap mechanism (measured, see tests/runtime/gpu_runtime.cu):
//   __trap()          chosen. The kernel dies, the context is poisoned, and every later call on
//                     it - the next sync, wait or copy - returns cudaErrorLaunchFailure, which
//                     cr::gpu::check turns into std::abort. No hang, no wrong answer, one PTX
//                     instruction on the failure path only, and NDEBUG cannot remove it.
//   assert(false)     works (cudaErrorAssert) but -DNDEBUG deletes it: measured, the kernel ran
//                     on and reported success. It also prints one line per failing lane.
//   __builtin_trap()  unusable: nvcc's device pass treats it as a host call (#20011-D) and drops
//                     it. Measured: the out of bounds kernel completed and sync said "no error".
//   fault word in unified memory: cannot stop the faulting lane, since cr::trap() is [[noreturn]]
//                     and cr::at() must still return a reference, so the bad access happens
//                     anyway. Rejected: it turns a guard into a race with a corrupted write.
#pragma once
#include <cstdio>
#include <cstring>
#include <cub/device/device_reduce.cuh>
#include <cub/device/device_scan.cuh>
#include <cuda_runtime.h>
#include <iterator>
#include "cairn_reuse.hpp"
#include "cairn_runtime.hpp"
namespace cr::gpu {

inline void check(cudaError_t e) noexcept {
  if(e == cudaSuccess) return;
  std::fprintf(stderr, "cairn: cuda: %s\n", cudaGetErrorString(e));
  trap();
}
template<class T> inline std::size_t bytes(std::size_t n) noexcept {
  if(n > static_cast<std::size_t>(std::numeric_limits<std::ptrdiff_t>::max()) / sizeof(T)) trap();
  return n * sizeof(T);
}

// Scoped owners, like cr::Buffer: zero initialized, released at scope exit, never copied, moved
// or returned. A failed allocation traps; n == 0 is legal and owns nothing.
enum class Where { device, pinned, unified };
template<class T, Where W> class Owner final {
  T* p_ = nullptr;
public:
  explicit Owner(std::size_t n) noexcept {
    static_assert(std::is_trivially_copyable_v<T>);  // Zeroed bytes are a value; scalars, and the runtime's Sum.
    const std::size_t b = bytes<T>(n);
    if(!b) return;
    void* q = nullptr;
    if constexpr(W == Where::device) check(cudaMalloc(&q, b));
    else if constexpr(W == Where::pinned) check(cudaMallocHost(&q, b));
    else check(cudaMallocManaged(&q, b));
    p_ = static_cast<T*>(q);
    if constexpr(W == Where::pinned) std::memset(p_, 0, b); else check(cudaMemset(p_, 0, b));
  }
  ~Owner() noexcept {
    if(!p_) return;
    if constexpr(W == Where::pinned) check(cudaFreeHost(p_)); else check(cudaFree(p_));
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

enum class Dir { h2d, d2h, d2d, h2h };
inline cudaMemcpyKind kind(Dir d) noexcept {
  switch(d) {
    case Dir::h2d: return cudaMemcpyHostToDevice;
    case Dir::d2h: return cudaMemcpyDeviceToHost;
    case Dir::d2d: return cudaMemcpyDeviceToDevice;
    case Dir::h2h: return cudaMemcpyHostToHost;
  }
  trap();
}
template<class T> inline void copy(T* dst, const T* src, std::size_t n, Dir d) noexcept {
  if(n) check(cudaMemcpy(dst, src, bytes<T>(n), kind(d)));
}

// One lane per i, strided so that n is limited by memory rather than by a grid dimension. Every i below n runs
// exactly once whatever the grid and block, which is why a plan may choose them: `block` threads a block, a grid
// that gives each thread about `per_lane` indices, and the stride loop unrolled `U` times.
constexpr unsigned BLOCK = 256, MAX_GRID = 65535, WARP = 32;
template<unsigned U, class F> __global__ void lanes(std::size_t n, F body) {
  const std::size_t step = std::size_t(gridDim.x) * blockDim.x;
#pragma unroll U
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += step) body(i);
}
// The most threads a block of this kernel can hold, read once: a planned block the kernel's registers cannot fill
// runs in whole warps as wide as fit, and computes the same.
template<unsigned U, class F> inline unsigned widest() noexcept {
  static const unsigned most = [] {
    cudaFuncAttributes held{};
    check(cudaFuncGetAttributes(&held, lanes<U, F>));
    return unsigned(held.maxThreadsPerBlock) / WARP * WARP;
  }();
  return most;
}
template<unsigned U = 1, class F>
inline void fire(std::size_t n, const F& body, cudaStream_t s, unsigned block = BLOCK, std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<F>, "a lane body crosses over as kernel arguments");
  if(!n) return;
  if(block > widest<U, F>()) block = widest<U, F>();
  const std::size_t each = std::size_t(block) * per_lane;
  const std::size_t g = (n + each - 1) / each;
  lanes<U><<<(g < MAX_GRID ? unsigned(g) : MAX_GRID), block, 0, s>>>(n, body);
  check(cudaGetLastError());
}
template<unsigned U = 1, class F>
inline void launch(std::size_t n, F body, unsigned block = BLOCK, std::size_t per_lane = 1) noexcept {
  fire<U>(n, body, nullptr, block, per_lane);
  check(cudaDeviceSynchronize());
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
template<unsigned W, unsigned U, class F, class G> __global__ void chunks(std::size_t n, F scalar, G chunk) {
  const std::size_t step = std::size_t(gridDim.x) * blockDim.x * W;
#pragma unroll U
  for(std::size_t i = (blockIdx.x * std::size_t(blockDim.x) + threadIdx.x) * W; i < n; i += step) {
    if(n - i >= W) chunk(i);
    else for(std::size_t k = i; k < n; ++k) scalar(k);
  }
}
template<unsigned W, unsigned U = 1, class F, class G>
inline void launch_vector(std::size_t n, bool whole, F scalar, G chunk, unsigned block = BLOCK,
                          std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<F> && std::is_trivially_copyable_v<G>, "lanes cross as arguments");
  if(!whole) return launch<U>(n, scalar, block, per_lane);  // a pointer off its chunk's width: the scalar lanes
  if(!n) return;
  static const unsigned most = [] {
    cudaFuncAttributes held{};
    check(cudaFuncGetAttributes(&held, chunks<W, U, F, G>));
    return unsigned(held.maxThreadsPerBlock) / WARP * WARP;
  }();
  if(block > most) block = most;
  const std::size_t each = std::size_t(block) * per_lane * W;
  const std::size_t g = (n + each - 1) / each;
  chunks<W, U><<<(g < MAX_GRID ? unsigned(g) : MAX_GRID), block>>>(n, scalar, chunk);
  check(cudaGetLastError());
  check(cudaDeviceSynchronize());
}

// `plan f { stage R; }` (compiler/staging.py): a block runs its indices tile by tile, the same for every thread, so
// the barriers are reached by all of them. Each tile covers blockDim.x indices from `base`; `load` fills the block's
// shared memory with every staged array's elements from base - R to base + blockDim.x + R that lie inside it, and
// `body` runs at the thread's index reading them there. Every index runs once, after its tile is loaded.
template<class T> CR_HD constexpr std::size_t tile_bytes(std::size_t width) noexcept {
  return (width * sizeof(T) + 15) / 16 * 16;  // each tile starts on 16 bytes
}
template<std::size_t R, unsigned U, class L, class F> __global__ void staged_lanes(std::size_t n, L load, F body) {
  extern __shared__ __align__(16) unsigned char cr_shared[];
  const std::size_t b = blockDim.x, w = b + 2 * R, t = threadIdx.x, step = std::size_t(gridDim.x) * b;
#pragma unroll U
  for(std::size_t base = blockIdx.x * b; base < n; base += step) {
    __syncthreads();  // every thread is done with the last tile before this one overwrites it
    load(base, w, t, b, cr_shared);
    __syncthreads();
    if(base + t < n) body(base + t, base, w, cr_shared);
  }
}
template<std::size_t R, unsigned U = 1, class L, class F, class S>
inline void launch_staged(std::size_t n, L load, F body, S bytes, unsigned block = BLOCK,
                          std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<L> && std::is_trivially_copyable_v<F>, "lanes cross as arguments");
  if(!n) return;
  static const unsigned most = [] {
    cudaFuncAttributes held{};
    check(cudaFuncGetAttributes(&held, staged_lanes<R, U, L, F>));
    return unsigned(held.maxThreadsPerBlock) / WARP * WARP;
  }();
  if(block > most) block = most;
  const std::size_t shared = bytes(std::size_t(block) + 2 * R);
  if(shared > 48 * 1024)  // past the default a kernel must ask for its dynamic shared memory
    check(cudaFuncSetAttribute(staged_lanes<R, U, L, F>, cudaFuncAttributeMaxDynamicSharedMemorySize, int(shared)));
  const std::size_t each = std::size_t(block) * per_lane;
  const std::size_t g = (n + each - 1) / each;
  staged_lanes<R, U><<<(g < MAX_GRID ? unsigned(g) : MAX_GRID), block, shared>>>(n, load, body);
  check(cudaGetLastError());
  check(cudaDeviceSynchronize());
}

// A linear ticket owning one stream: exactly one wait() consumes it, and dropping it unawaited
// traps. Ordering between tickets is recorded on the device through an event, never by stopping
// the host. The lane body needs no keep-alive: CUDA copies kernel arguments at launch.
class Ticket final {
  cudaStream_t s_ = nullptr;
  cudaEvent_t e_ = nullptr;
public:
  Ticket() noexcept {
    check(cudaStreamCreate(&s_));
    check(cudaEventCreateWithFlags(&e_, cudaEventDisableTiming));
  }
  Ticket(Ticket&& o) noexcept : s_(o.s_), e_(o.e_) { o.s_ = nullptr; o.e_ = nullptr; }
  Ticket(const Ticket&) = delete;
  Ticket& operator=(const Ticket&) = delete;
  Ticket& operator=(Ticket&&) = delete;  // a linear value is initialized, never overwritten
  ~Ticket() noexcept { if(s_) trap(); }
  cudaStream_t stream() const noexcept { return s_; }
  // An event that completes with everything queued on this ticket so far, for other work to be ordered after.
  cudaEvent_t mark() const noexcept {
    check(cudaEventRecord(e_, s_));
    return e_;
  }
  void follow(const Ticket& dep) const noexcept {
    check(cudaEventRecord(dep.e_, dep.s_));
    check(cudaStreamWaitEvent(s_, dep.e_, 0));
  }
  void wait() && noexcept { finish(); }
  void finish() noexcept {
    check(cudaStreamSynchronize(s_));
    check(cudaEventDestroy(e_));
    check(cudaStreamDestroy(s_));
    s_ = nullptr;
    e_ = nullptr;
  }
};
inline void wait(Ticket&& t) noexcept { t.finish(); }
// Work queued on a stream of its own, after the work of every ticket in `after...`.
template<class F, class... After> inline Ticket launch_async(std::size_t n, F body, const After&... after) noexcept {
  Ticket t;
  (t.follow(after), ...);
  fire(n, body, t.stream());
  return t;
}
template<class F> inline Ticket launch_after(const Ticket& dep, std::size_t n, F body) noexcept {
  Ticket t;
  t.follow(dep);
  fire(n, body, t.stream());
  return t;
}
template<class T, class... After>
inline Ticket copy_async(T* dst, const T* src, std::size_t n, Dir d, const After&... after) noexcept {
  Ticket t;
  (t.follow(after), ...);
  if(n) check(cudaMemcpyAsync(dst, src, bytes<T>(n), kind(d), t.stream()));
  return t;
}
template<class T>
inline Ticket copy_after(const Ticket& dep, T* dst, const T* src, std::size_t n, Dir d) noexcept {
  Ticket t;
  t.follow(dep);
  if(n) check(cudaMemcpyAsync(dst, src, bytes<T>(n), kind(d), t.stream()));
  return t;
}

// CUB asks for the return type of the combining operator in host code, which nvcc refuses to
// infer for an extended __device__ lambda. Naming T here lets the emitter pass a plain lambda.
template<class T, class Op> struct Binary {
  Op op;
  CR_DEVICE T operator()(T a, T b) const { return op(a, b); }
};

// A random-access input iterator whose element i is value(i). CUB 2 shipped this as its counting
// and transform input iterators; CCCL 3 deleted both in favour of thrust's, so the runtime
// carries its own eleven lines instead of a thrust dependency.
template<class T, class F> struct Indexed {
  using value_type = T;
  using reference = T;
  using pointer = void;
  using difference_type = std::ptrdiff_t;
  using iterator_category = std::random_access_iterator_tag;
  F value;
  std::size_t i = 0;
  CR_HD T operator*() const { return value(i); }
  CR_HD T operator[](difference_type d) const { return value(i + static_cast<std::size_t>(d)); }
  CR_HD Indexed operator+(difference_type d) const { return Indexed{value, i + static_cast<std::size_t>(d)}; }
  CR_HD Indexed& operator++() { ++i; return *this; }
};

// Transform-reduce of value(i) over [0,n). Association order is unspecified by contract, so the
// result is exact for associative integer ops and within the usual tolerance for float sums.
template<class T, class Op, class F> inline T reduce(std::size_t n, T identity, Op op, F value) noexcept {
  if(!n) return identity;
  const Indexed<T, F> in{value};
  const Binary<T, Op> fold{op};
  Buffer<T> out(1);
  std::size_t need = 0;
  check(cub::DeviceReduce::Reduce(nullptr, need, in, out.data(), n, fold, identity));
  Buffer<char> temp(need ? need : 1);  // a null temp pointer would mean "query" to CUB
  check(cub::DeviceReduce::Reduce(temp.data(), need, in, out.data(), n, fold, identity));
  T host = identity;
  copy(&host, out.data(), std::size_t(1), Dir::d2h);
  return host;
}

// What a scan carries, as the element its output holds: a checked sum's value without its overflow flag.
template<class T> CR_HD inline T plain(T x) noexcept { return x; }
template<class T> CR_HD inline T plain(Sum<T> x) noexcept { return x.v; }

// Scan of value(i) over [0,n) into out: out[i] is op over value(0..i], or over value(0..i) when Exclusive, and
// the answer is op over every value. The association order is CUB's, exact for the integer operators the language
// admits here. The inclusive scan lands in device scratch first, so a value(i) that reads out[i] reads it before
// anything writes it; one more pass writes out from the scratch, shifted by one place when Exclusive.
template<bool Exclusive, class T, class R, class Op, class F>
inline T scan(R* out, std::size_t n, T identity, Op op, F value) noexcept {
  if(!n) return identity;
  const Indexed<T, F> in{value};
  const Binary<T, Op> fold{op};
  Buffer<T> held(n);
  T* h = held.data();
  std::size_t need = 0;
  check(cub::DeviceScan::InclusiveScan(nullptr, need, in, h, fold, n));
  {
    Buffer<char> temp(need ? need : 1);  // a null temp pointer would mean "query" to CUB
    check(cub::DeviceScan::InclusiveScan(temp.data(), need, in, h, fold, n));
    check(cudaDeviceSynchronize());
  }
  launch(n, [=] CR_DEVICE(std::size_t i) { out[i] = plain(Exclusive ? (i ? h[i - 1] : identity) : h[i]); });
  T total = identity;
  copy(&total, h + (n - 1), std::size_t(1), Dir::d2h);
  return total;
}

// Stable compaction: value(i) for each selected i, in order, into the prefix of out; the tail of
// out is left alone. pred runs exactly once per i (its answer is kept), value only when selected.
template<class T, class P, class F>
inline std::size_t compact(T* out, std::size_t n, P pred, F value) noexcept {
  if(!n) return 0;
  Buffer<unsigned char> keep(n);
  Buffer<std::size_t> off(n);
  unsigned char* k = keep.data();
  std::size_t* o = off.data();
  launch(n, [=] CR_DEVICE(std::size_t i) { k[i] = pred(i) ? 1 : 0; });
  std::size_t need = 0;
  check(cub::DeviceScan::ExclusiveSum(nullptr, need, k, o, n));
  {
    Buffer<char> temp(need ? need : 1);  // a null temp pointer would mean "query" to CUB
    check(cub::DeviceScan::ExclusiveSum(temp.data(), need, k, o, n));
    check(cudaDeviceSynchronize());
  }
  launch(n, [=] CR_DEVICE(std::size_t i) { if(k[i]) out[o[i]] = value(i); });
  std::size_t last = 0;
  unsigned char tail = 0;
  copy(&last, o + (n - 1), std::size_t(1), Dir::d2h);
  copy(&tail, k + (n - 1), std::size_t(1), Dir::d2h);
  return last + (tail ? 1 : 0);
}

// Execution contexts (cairn_reuse.hpp) bound to CUDA. A context owns streams, events and one scratch arena,
// and lends them to queued work until that work has completed: a lane comes back at its ticket's wait, and
// the arena's next user is ordered after its last one on the device. The budget is declared when the context
// is made; an operation that needs more than it allows answers Scratch::over_budget and queues nothing. The
// default lowering does not use a context yet: every ticket still owns a stream of its own. Stream-ordered
// allocation is used only when the context is made with Allocation::stream_ordered.
struct Cuda {
  using Stream = cudaStream_t;
  using Event = cudaEvent_t;
  Stream make_stream() noexcept {
    cudaStream_t s = nullptr;
    check(cudaStreamCreate(&s));
    return s;
  }
  void destroy_stream(Stream s) noexcept { check(cudaStreamDestroy(s)); }
  Event make_event() noexcept {
    cudaEvent_t e = nullptr;
    check(cudaEventCreateWithFlags(&e, cudaEventDisableTiming));
    return e;
  }
  void destroy_event(Event e) noexcept { check(cudaEventDestroy(e)); }
  void record(Event e, Stream s) noexcept { check(cudaEventRecord(e, s)); }
  void wait_event(Stream s, Event e) noexcept { check(cudaStreamWaitEvent(s, e, 0)); }
  void sync_stream(Stream s) noexcept { check(cudaStreamSynchronize(s)); }
  void sync_event(Event e) noexcept { check(cudaEventSynchronize(e)); }
  void* alloc(std::size_t b) noexcept {
    void* p = nullptr;
    check(cudaMalloc(&p, b));
    return p;
  }
  void free(void* p) noexcept { check(cudaFree(p)); }
  void* alloc_async(std::size_t b, Stream s) noexcept {
    void* p = nullptr;
    check(cudaMallocAsync(&p, b, s));
    return p;
  }
  void free_async(void* p, Stream s) noexcept { check(cudaFreeAsync(p, s)); }
};
using Context = reuse::Context<Cuda>;
using reuse::Allocation;
using reuse::Budget;
using reuse::Scratch;

// Queued work on a lane a context lent. Linear like a Ticket: exactly one wait() consumes it, and that hands the
// lane back once its work has completed; dropping it unawaited traps.
class Lent final {
  Context* ctx_ = nullptr;
  Context::Lane* lane_ = nullptr;
public:
  explicit Lent(Context& c) noexcept : ctx_(&c), lane_(c.lend()) {}
  Lent(Lent&& o) noexcept : ctx_(o.ctx_), lane_(std::exchange(o.lane_, nullptr)) {}
  Lent(const Lent&) = delete;
  Lent& operator=(const Lent&) = delete;
  Lent& operator=(Lent&&) = delete;  // a linear value is initialized, never overwritten
  ~Lent() noexcept { if(lane_) trap(); }
  cudaStream_t stream() const noexcept { return lane_->stream; }
  Context::Lane& lane() const noexcept { return *lane_; }
  // An event that completes with everything queued here so far, for other work to be ordered after.
  cudaEvent_t mark() const noexcept {
    check(cudaEventRecord(lane_->event, lane_->stream));
    return lane_->event;
  }
  void follow(cudaEvent_t e) const noexcept { check(cudaStreamWaitEvent(lane_->stream, e, 0)); }
  void wait() && noexcept { ctx_->give_back(std::exchange(lane_, nullptr)); }
};

// Lanes after the tickets or lent work named in `after...`.
template<class F, class... After> inline Lent launch_on(Context& ctx, std::size_t n, F body, const After&... after) noexcept {
  Lent t(ctx);
  (t.follow(after.mark()), ...);
  fire(n, body, t.stream());
  return t;
}

inline constexpr std::size_t ALIGN = 256;  // what cudaMalloc guarantees, and what CUB's temporary storage wants
inline std::size_t aligned(std::size_t b) noexcept {
  if(b > std::numeric_limits<std::size_t>::max() - ALIGN) trap();
  return (b + ALIGN - 1) / ALIGN * ALIGN;
}

// Transform-reduce of value(i) over [0,n) into `out`, a device cell, queued on a lane of `ctx`: no host copy, and
// no allocation within the budget. The result stays on the device, for work ordered after the returned ticket.
// When CUB would need more scratch than the budget allows, nothing is queued and `*answer` says so.
template<class T, class Op, class F, class... After>
inline Lent reduce_to(Context& ctx, T* out, std::size_t n, T identity, Op op, F value, Scratch* answer,
                      const After&... after) noexcept {
  Lent t(ctx);
  (t.follow(after.mark()), ...);
  *answer = Scratch::ok;
  if(!n) {
    fire(1, [=] CR_DEVICE(std::size_t) { *out = identity; }, t.stream());
    return t;
  }
  const Indexed<T, F> in{value};
  const Binary<T, Op> fold{op};
  std::size_t need = 0;
  check(cub::DeviceReduce::Reduce(nullptr, need, in, out, n, fold, identity, t.stream()));
  void* temp = nullptr;
  *answer = ctx.acquire(aligned(need ? need : 1), t.lane(), &temp);
  if(*answer != Scratch::ok) return t;
  check(cub::DeviceReduce::Reduce(temp, need, in, out, n, fold, identity, t.stream()));
  ctx.release(t.lane());
  return t;
}

// The same reduction with its result on the host: one cell of the arena holds it on the device, and one
// copy and one synchronization bring it back. Nothing is allocated within the budget.
template<class T, class Op, class F>
inline Scratch reduce(Context& ctx, T& result, std::size_t n, T identity, Op op, F value) noexcept {
  if(!n) {
    result = identity;
    return Scratch::ok;
  }
  Lent t(ctx);
  const Indexed<T, F> in{value};
  const Binary<T, Op> fold{op};
  std::size_t need = 0;
  check(cub::DeviceReduce::Reduce(nullptr, need, in, static_cast<T*>(nullptr), n, fold, identity, t.stream()));
  const std::size_t cell = aligned(sizeof(T));
  void* base = nullptr;
  const Scratch answer = ctx.acquire(cell + aligned(need ? need : 1), t.lane(), &base);
  if(answer == Scratch::ok) {
    T* at = static_cast<T*>(base);
    check(cub::DeviceReduce::Reduce(static_cast<char*>(base) + cell, need, in, at, n, fold, identity, t.stream()));
    T host = identity;
    check(cudaMemcpyAsync(&host, at, sizeof(T), cudaMemcpyDeviceToHost, t.stream()));
    ctx.release(t.lane());
    std::move(t).wait();
    result = host;
    return answer;
  }
  std::move(t).wait();
  return answer;
}

// Stable compaction as compact() does it, with the flags, the offsets and CUB's storage carved from the arena
// and every step queued on one lane: one synchronization where compact() has three, and no allocation
// within the budget. `*used` is the number selected, or 0 when the budget answered over_budget.
template<class T, class P, class F>
inline Scratch compact(Context& ctx, std::size_t* used, T* out, std::size_t n, P pred, F value) noexcept {
  *used = 0;
  if(!n) return Scratch::ok;
  Lent t(ctx);
  std::size_t need = 0;
  check(cub::DeviceScan::ExclusiveSum(nullptr, need, static_cast<unsigned char*>(nullptr),
                                      static_cast<std::size_t*>(nullptr), n, t.stream()));
  const std::size_t flags = aligned(n), offsets = aligned(bytes<std::size_t>(n));
  void* base = nullptr;
  const Scratch answer = ctx.acquire(flags + offsets + aligned(need ? need : 1), t.lane(), &base);
  if(answer == Scratch::ok) {
    unsigned char* k = static_cast<unsigned char*>(base);
    std::size_t* o = reinterpret_cast<std::size_t*>(static_cast<char*>(base) + flags);
    fire(n, [=] CR_DEVICE(std::size_t i) { k[i] = pred(i) ? 1 : 0; }, t.stream());
    check(cub::DeviceScan::ExclusiveSum(static_cast<char*>(base) + flags + offsets, need, k, o, n, t.stream()));
    fire(n, [=] CR_DEVICE(std::size_t i) { if(k[i]) out[o[i]] = value(i); }, t.stream());
    std::size_t last = 0;
    unsigned char tail = 0;
    check(cudaMemcpyAsync(&last, o + (n - 1), sizeof(last), cudaMemcpyDeviceToHost, t.stream()));
    check(cudaMemcpyAsync(&tail, k + (n - 1), sizeof(tail), cudaMemcpyDeviceToHost, t.stream()));
    ctx.release(t.lane());
    std::move(t).wait();
    *used = last + (tail ? 1 : 0);
    return answer;
  }
  std::move(t).wait();
  return answer;
}

} // namespace cr::gpu

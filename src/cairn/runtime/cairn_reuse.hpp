// CAIRN execution contexts: streams, events and scratch that queued work borrows and gives back, kept apart
// from the machine they run on. A context never frees, relends or overwrites what the device may still be
// touching: a lane (a stream and its event) comes back only once its work has completed, and the scratch
// arena is handed to its next user ordered after its last one on the device, never by stopping the host.
// What a context holds is declared when it is made: a budget reserves scratch up front, may allow growth up
// to a stated limit, and answers a request beyond that limit with a defined failure instead of allocating.
// A caller that owns a stream may bind it: synchronous work then runs on it, after what the caller queued there.
// Synchronous work may be held: a run of it queues on one lane and waits once, when the run ends, and a call a caller
// enqueues on its own stream does not wait at all. Making a context calls nothing on the machine; its events and
// streams are made by the first work that needs them.
//
// Beside the context are the operations device work runs through, written once against the machine: synchronous
// work on the context's own lane, queued work on a lane lent until its wait, and the reductions, scans and
// compactions whose temporaries come from the arena. The machine is a template parameter. cairn_gpu.hpp binds it
// to CUDA, tests/runtime/reuse_runtime.cpp drives the bookkeeping with a mock device whose work completes only when
// the test says so, and tests/runtime/gpu_host.hpp runs generated programs on a host machine that counts every
// stream, allocation and wait.
#pragma once
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <limits>
#include <new>
#include <utility>
#include "cairn_runtime.hpp"
namespace cr::reuse {

constexpr unsigned BLOCK = 256, MAX_GRID = 65535, WARP = 32;  // as cairn_kernels.hpp's: a block, a grid's cap, a warp
constexpr std::size_t ALIGN = 256;  // what cudaMalloc guarantees, and what CUB's temporary storage wants
constexpr std::size_t UNBOUNDED = std::numeric_limits<std::size_t>::max();

// What a request for more scratch than the arena holds may do.
struct Budget {
  std::size_t reserve = 0;  // allocated when the context is made; a request within it never allocates
  std::size_t most = 0;     // the arena may grow up to this, and only when it is larger than `reserve`
};

// How the arena's storage is taken and released: synchronously, or ordered on a stream, which lets growth
// avoid stopping the host but may, as CUDA documents for its pools, add dependencies between streams.
enum class Allocation { synchronous, stream_ordered };

enum class Scratch { ok, over_budget };  // what a request for scratch answered

enum class Where { device, pinned, unified };  // where an owner's storage lives
enum class Dir { h2d, d2h, d2d, h2h };         // which way a copy crosses

template<class T> inline std::size_t span(std::size_t n) noexcept {  // the bytes of n elements, or a trap
  if(n > static_cast<std::size_t>(std::numeric_limits<std::ptrdiff_t>::max()) / sizeof(T)) trap();
  return n * sizeof(T);
}
inline std::size_t aligned(std::size_t b) noexcept {
  if(b > std::numeric_limits<std::size_t>::max() - ALIGN) trap();
  return (b + ALIGN - 1) / ALIGN * ALIGN;
}

template<class Api> class Context final {
public:
  using Stream = typename Api::Stream;
  using Event = typename Api::Event;
  // A stream and the event that marks the end of the work last queued on it.
  struct Lane {
    Stream stream{};
    Event event{};
    Lane* next = nullptr;  // the free list
  };

  explicit Context(Budget budget, Allocation how = Allocation::synchronous, Api api = Api{}) noexcept
      : api_(std::move(api)), budget_(budget), how_(how) {
    if(budget_.most < budget_.reserve) budget_.most = budget_.reserve;  // a limit below the reserve grants nothing
    if(budget_.reserve) arena_ = api_.alloc(budget_.reserve), capacity_ = budget_.reserve;
  }
  Context(const Context&) = delete;
  Context& operator=(const Context&) = delete;
  Context(Context&&) = delete;
  Context& operator=(Context&&) = delete;
  // Every lane must have come back: a lane still lent is work nobody waited for, which traps like a ticket. A
  // bound stream is the caller's, and is never destroyed here.
  ~Context() noexcept {
    if(lent_ != 0 || leased_ || holding_) trap();
    if(used_) api_.sync_event(marker_);  // nothing below may go while the device still reads the arena
    if(arena_) api_.free(arena_);
    for(Lane* lane = free_; lane != nullptr;) {
      Lane* next = lane->next;
      api_.destroy_event(lane->event);
      api_.destroy_stream(lane->stream);
      delete lane;
      lane = next;
    }
    if(bound_event_) api_.destroy_event(bound_.event);
    if(marked_) api_.destroy_event(marker_);
  }

  // A lane whose earlier work has completed, or a new one: never waits, and never shares a stream, so two
  // operations lent two lanes stay as independent as two tickets with streams of their own. `synchronous` asks
  // for the lane synchronous work runs on: the lane a held run already queues on, else the bound stream while one
  // is bound. Any other lane lent while a run is held or a stream is bound starts after what was queued there.
  Lane* lend(bool synchronous = false) noexcept {
    if(synchronous && held_) return held_;  // still lent, since the run began
    if(synchronous && binding_) {
      if(bound_lent_) trap();  // synchronous work does not nest
      bound_lent_ = true;
      ++lent_;
      return &bound_;
    }
    Lane* lane = free_;
    if(lane != nullptr) free_ = lane->next;
    else {
      lane = new(std::nothrow) Lane;
      if(lane == nullptr) trap();
      lane->stream = api_.make_stream();
      lane->event = api_.make_event();
      ++made_;
    }
    lane->next = nullptr;
    ++lent_;
    if(held_ || binding_) {
      Lane& before = held_ ? *held_ : bound_;
      api_.record(event(before), before.stream);
      api_.wait_event(lane->stream, before.event);
    }
    return lane;
  }
  // Wait for the lane's work, then keep the lane for the next operation. The caller's stream under an enqueued call
  // is the one lane nothing here waits for: the caller synchronizes it.
  void give_back(Lane* lane) noexcept {
    if(lane == held_) held_ = nullptr;
    if(!(enqueued_ && lane == &bound_)) api_.sync_stream(lane->stream);
    --lent_;
    if(lane == &bound_) {
      bound_lent_ = false;
      return;
    }
    lane->next = free_;
    free_ = lane;
  }

  // The end of an operation on the lane lend(true) gave: wait for its work, or, while a run is held, keep the lane
  // and leave the wait to the run's end.
  void finish(Lane* lane) noexcept {
    if(holding_) held_ = lane;
    else give_back(lane);
  }

  // Synchronous work runs on `stream`, which the caller owns and keeps alive, after what it queued there.
  void bind(Stream stream) noexcept {
    if(bound_lent_ || enqueued_) trap();
    bound_.stream = stream;
    binding_ = true;
  }
  void unbind() noexcept {
    if(bound_lent_ || enqueued_) trap();
    binding_ = false;
  }

  // A held run: synchronous work queues on one lane and nothing waits until the outermost run settles, once. Only
  // work nothing on the host reads before the run ends may be held (compiler/execution.py); an operation whose
  // result the host reads calls observed() first, which waits for the run so far.
  void hold() noexcept { ++holding_; }
  void settle() noexcept {
    if(!holding_) trap();
    if(--holding_ == 0 && held_) give_back(held_);
  }
  // An enqueued call: the run is held on the caller's stream and never waits, since the caller synchronizes that
  // stream. It starts from a thread holding nothing and ends with the thread's own binding back.
  void enqueue(Stream stream) noexcept {
    if(enqueued_ || holding_ || bound_lent_) trap();
    saved_ = {binding_, bound_.stream};
    bound_.stream = stream;
    binding_ = enqueued_ = true;
    holding_ = 1;
  }
  void leave() noexcept {
    if(!enqueued_ || holding_ != 1) trap();
    if(held_) give_back(held_);  // no wait: the caller's stream
    holding_ = 0;
    enqueued_ = false;
    binding_ = saved_.binding;
    bound_.stream = saved_.stream;
  }
  // Before an operation whose result the host reads when it returns, or which waits on the host: what a held run
  // queued is waited for first. Under an enqueued call nothing may wait, so it traps; the compiler admits no such
  // operation there.
  void observed() noexcept {
    if(enqueued_) trap();
    if(held_) give_back(held_);
  }

  // Scratch of at least `bytes` for work about to be queued on `lane`. The work is ordered after the arena's
  // previous user on the device, so the two never overlap, and the host does not wait. Within the reserve
  // nothing is allocated. Beyond it the arena grows only up to the budget's limit, and the old storage is
  // released only once its last user has finished with it; beyond the limit the answer is over_budget and
  // nothing changes. release() must follow once the work that uses it is queued.
  Scratch acquire(std::size_t bytes, Lane& lane, void** out) noexcept {
    if(leased_) trap();  // acquire and release pair up: one user holds the arena at a time
    if(bytes > capacity_) {
      if(bytes > budget_.most) return Scratch::over_budget;
      grow(bytes, lane);
    }
    if(!marked_) marker_ = api_.make_event(), marked_ = true;
    if(used_) api_.wait_event(lane.stream, marker_);
    *out = arena_;
    leased_ = true;
    return Scratch::ok;
  }
  // The work that uses the arena is queued on `lane`: the next user waits for it.
  void release(Lane& lane) noexcept {
    if(!leased_) trap();
    api_.record(marker_, lane.stream);
    used_ = true;
    leased_ = false;
  }

  std::size_t capacity() const noexcept { return capacity_; }
  std::size_t streams_made() const noexcept { return made_; }
  std::size_t lent() const noexcept { return lent_; }
  std::size_t grown() const noexcept { return grown_; }  // how many times the arena was replaced by a larger one
  bool bound() const noexcept { return binding_; }
  bool enqueued() const noexcept { return enqueued_; }
  Api& api() noexcept { return api_; }

private:
  Event event(Lane& lane) noexcept {  // the lane's event; the bound stream's is made the first time it is needed
    if(&lane == &bound_ && !bound_event_) bound_.event = api_.make_event(), bound_event_ = true;
    return lane.event;
  }
  void grow(std::size_t bytes, Lane& lane) noexcept {
    void* old = std::exchange(arena_, nullptr);
    if(how_ == Allocation::stream_ordered) {
      // Freed and taken again in stream order: the old storage goes once the previous user is done with it.
      if(old) {
        if(used_) api_.wait_event(lane.stream, marker_);
        api_.free_async(old, lane.stream);
      }
      arena_ = api_.alloc_async(bytes, lane.stream);
    } else {
      if(old) {
        if(used_) api_.sync_event(marker_);  // growth is the one step that may stop the host, and only here
        api_.free(old);
      }
      arena_ = api_.alloc(bytes);
      used_ = false;  // nothing queued can reach the new storage yet
    }
    capacity_ = bytes;
    ++grown_;
  }

  Api api_;
  Budget budget_;
  Allocation how_;
  Lane* free_ = nullptr;
  std::size_t lent_ = 0, made_ = 0, grown_ = 0;
  void* arena_ = nullptr;
  std::size_t capacity_ = 0;
  Event marker_{};       // recorded after the arena's last user; the next user's stream waits on it
  bool marked_ = false;  // whether marker_ has been made
  bool used_ = false;    // whether marker_ has been recorded since the arena last changed
  bool leased_ = false;  // between acquire and release
  Lane bound_{};         // the caller's stream while one is bound, and an event of this context's own
  bool binding_ = false, bound_lent_ = false, bound_event_ = false;
  std::size_t holding_ = 0;  // held runs open on this thread
  Lane* held_ = nullptr;     // the lane a held run queues on, lent until the run settles
  bool enqueued_ = false;    // an enqueued call: held on the caller's stream, never waited for here
  struct {
    bool binding;
    Stream stream;
  } saved_{};  // the thread's binding, back when the enqueued call leaves
};

// Queued work on a lane a context lent. Linear like a ticket: exactly one wait() consumes it, and that hands the
// lane back once its work has completed; dropping it unawaited traps.
template<class Api> class Lent final {
  using Lane = typename Context<Api>::Lane;
  Context<Api>* ctx_ = nullptr;
  Lane* lane_ = nullptr;
public:
  explicit Lent(Context<Api>& c) noexcept : ctx_(&c), lane_(c.lend()) {}
  Lent(Lent&& o) noexcept : ctx_(o.ctx_), lane_(std::exchange(o.lane_, nullptr)) {}
  Lent(const Lent&) = delete;
  Lent& operator=(const Lent&) = delete;
  Lent& operator=(Lent&&) = delete;  // a linear value is initialized, never overwritten
  ~Lent() noexcept { if(lane_) trap(); }
  typename Api::Stream stream() const noexcept { return lane_->stream; }
  Lane& lane() const noexcept { return *lane_; }
  // An event that completes with everything queued here so far, for other work to be ordered after.
  typename Api::Event mark() const noexcept {
    ctx_->api().record(lane_->event, lane_->stream);
    return lane_->event;
  }
  void follow(typename Api::Event e) const noexcept { ctx_->api().wait_event(lane_->stream, e); }
  void wait() && noexcept { ctx_->give_back(std::exchange(lane_, nullptr)); }
};

// Synchronous work: `queue(stream)` queues it on the context's synchronous lane, and the call returns once that
// lane has finished it, or at once in a held run, whose end waits for it. Nothing else is waited for, and after the
// lane's first use nothing is made.
template<class Api, class Q> inline void synchronous(Context<Api>& ctx, Q&& queue) noexcept {
  typename Context<Api>::Lane* lane = ctx.lend(true);
  queue(lane->stream);
  ctx.finish(lane);
}
// The same for work whose result the host reads when the call returns, such as a copy to host memory: it waits,
// in a held run too, and traps under an enqueued call, which never waits.
template<class Api, class Q> inline void observed(Context<Api>& ctx, Q&& queue) noexcept {
  ctx.observed();
  typename Context<Api>::Lane* lane = ctx.lend(true);
  queue(lane->stream);
  ctx.give_back(lane);
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

// What a scan carries, as the element its output holds: a checked sum's value without its overflow flag.
template<class T> CR_HD inline T plain(T x) noexcept { return x; }
template<class T> CR_HD inline T plain(Sum<T> x) noexcept { return x.v; }

// Transform-reduce of value(i) over [0,n), with its result on the host: one cell of the arena holds it on the
// device, and one copy and one wait for the synchronous lane bring it back. Nothing is allocated within the
// budget. Association order is unspecified by contract, so the result is exact for associative integer ops and
// within the usual tolerance for float sums. Over the budget nothing is queued and `result` is untouched.
template<class Api, class T, class Op, class F>
inline Scratch reduce(Context<Api>& ctx, T& result, std::size_t n, T identity, Op op, F value) noexcept {
  if(!n) {
    result = identity;
    return Scratch::ok;
  }
  ctx.observed();
  Api& api = ctx.api();
  typename Context<Api>::Lane* lane = ctx.lend(true);
  const Indexed<T, F> in{value};
  const Binary<T, Op> fold{op};
  std::size_t need = 0;
  api.reduce(nullptr, need, in, static_cast<T*>(nullptr), n, fold, identity, lane->stream);
  const std::size_t cell = aligned(sizeof(T));
  void* base = nullptr;
  const Scratch answer = ctx.acquire(cell + aligned(need ? need : 1), *lane, &base);
  T host = identity;
  if(answer == Scratch::ok) {
    T* at = static_cast<T*>(base);
    api.reduce(static_cast<char*>(base) + cell, need, in, at, n, fold, identity, lane->stream);
    api.copy(&host, at, sizeof(T), Dir::d2h, lane->stream);
    ctx.release(*lane);
  }
  ctx.give_back(lane);
  if(answer == Scratch::ok) result = host;
  return answer;
}

// The same reduction into `out`, a device cell, queued on a lane of `ctx` after `after...`: no host copy, and no
// allocation within the budget. The result stays on the device, for work ordered after the returned ticket.
// When the reduction would need more scratch than the budget allows, nothing is queued and `*answer` says so.
template<class Api, class T, class Op, class F, class... After>
inline Lent<Api> reduce_to(Context<Api>& ctx, T* out, std::size_t n, T identity, Op op, F value, Scratch* answer,
                           const After&... after) noexcept {
  ctx.observed();
  Api& api = ctx.api();
  Lent<Api> t(ctx);
  (t.follow(after.mark()), ...);
  *answer = Scratch::ok;
  if(!n) {
    api.template lanes<1>(1, [=] CR_DEVICE(std::size_t) { *out = identity; }, t.stream(), BLOCK, 1);
    return t;
  }
  const Indexed<T, F> in{value};
  const Binary<T, Op> fold{op};
  std::size_t need = 0;
  api.reduce(nullptr, need, in, out, n, fold, identity, t.stream());
  void* temp = nullptr;
  *answer = ctx.acquire(aligned(need ? need : 1), t.lane(), &temp);
  if(*answer != Scratch::ok) return t;
  api.reduce(temp, need, in, out, n, fold, identity, t.stream());
  ctx.release(t.lane());
  return t;
}

// Scan of value(i) over [0,n) into out: out[i] is op over value(0..i], or over value(0..i) when Exclusive, and
// `total` is op over every value. The association order is the machine's, exact for the integer operators the
// language admits here. The inclusive scan lands in arena scratch first, so a value(i) that reads out[i] reads it
// before anything writes it; one more pass writes out from the scratch, shifted by one place when Exclusive. Every
// step is queued on the synchronous lane, which is waited for once.
template<bool Exclusive, class Api, class T, class R, class Op, class F>
inline Scratch scan(Context<Api>& ctx, T& total, R* out, std::size_t n, T identity, Op op, F value) noexcept {
  if(!n) {
    total = identity;
    return Scratch::ok;
  }
  ctx.observed();
  Api& api = ctx.api();
  typename Context<Api>::Lane* lane = ctx.lend(true);
  const Indexed<T, F> in{value};
  const Binary<T, Op> fold{op};
  std::size_t need = 0;
  api.inclusive_scan(nullptr, need, in, static_cast<T*>(nullptr), fold, n, lane->stream);
  const std::size_t held = aligned(span<T>(n));
  void* base = nullptr;
  const Scratch answer = ctx.acquire(held + aligned(need ? need : 1), *lane, &base);
  T last = identity;
  if(answer == Scratch::ok) {
    T* h = static_cast<T*>(base);
    api.inclusive_scan(static_cast<char*>(base) + held, need, in, h, fold, n, lane->stream);
    api.template lanes<1>(
        n, [=] CR_DEVICE(std::size_t i) { out[i] = plain(Exclusive ? (i ? h[i - 1] : identity) : h[i]); },
        lane->stream, BLOCK, 1);
    api.copy(&last, h + (n - 1), sizeof(T), Dir::d2h, lane->stream);
    ctx.release(*lane);
  }
  ctx.give_back(lane);
  if(answer == Scratch::ok) total = last;
  return answer;
}

// Stable compaction: value(i) for each selected i, in order, into the prefix of out; the tail of out is left alone.
// pred runs exactly once per i (its answer is kept), value only when selected. The flags, the offsets and the
// machine's scan storage are carved from the arena and every step is queued on the synchronous lane: one wait,
// and no allocation within the budget. `*used` is the number selected, or 0 when the budget answered over_budget.
template<class Api, class T, class P, class F>
inline Scratch compact(Context<Api>& ctx, std::size_t* used, T* out, std::size_t n, P pred, F value) noexcept {
  *used = 0;
  if(!n) return Scratch::ok;
  ctx.observed();
  Api& api = ctx.api();
  typename Context<Api>::Lane* lane = ctx.lend(true);
  std::size_t need = 0;
  api.exclusive_sum(nullptr, need, static_cast<unsigned char*>(nullptr), static_cast<std::size_t*>(nullptr), n,
                    lane->stream);
  const std::size_t flags = aligned(n), offsets = aligned(span<std::size_t>(n));
  void* base = nullptr;
  const Scratch answer = ctx.acquire(flags + offsets + aligned(need ? need : 1), *lane, &base);
  std::size_t last = 0;
  unsigned char tail = 0;
  if(answer == Scratch::ok) {
    unsigned char* k = static_cast<unsigned char*>(base);
    std::size_t* o = reinterpret_cast<std::size_t*>(static_cast<char*>(base) + flags);
    api.template lanes<1>(n, [=] CR_DEVICE(std::size_t i) { k[i] = pred(i) ? 1 : 0; }, lane->stream, BLOCK, 1);
    api.exclusive_sum(static_cast<char*>(base) + flags + offsets, need, k, o, n, lane->stream);
    api.template lanes<1>(n, [=] CR_DEVICE(std::size_t i) { if(k[i]) out[o[i]] = value(i); }, lane->stream, BLOCK,
                          1);
    api.copy(&last, o + (n - 1), sizeof(last), Dir::d2h, lane->stream);
    api.copy(&tail, k + (n - 1), sizeof(tail), Dir::d2h, lane->stream);
    ctx.release(*lane);
  }
  ctx.give_back(lane);
  if(answer == Scratch::ok) *used = last + (tail ? 1 : 0);
  return answer;
}
}  // namespace cr::reuse

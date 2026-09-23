// CAIRN execution contexts: streams, events and scratch that queued work borrows and gives back, kept apart
// from the machine they run on. A context never frees, relends or overwrites what the device may still be
// touching: a lane (a stream and its event) comes back only once its work has completed, and the scratch
// arena is handed to its next user ordered after its last one on the device, never by stopping the host.
// What a context holds is declared when it is made: a budget reserves scratch up front, may allow growth up
// to a stated limit, and answers a request beyond that limit with a defined failure instead of allocating.
// The machine is a template parameter: cairn_gpu.hpp binds it to CUDA, and tests/runtime/reuse_runtime.cpp
// drives the same bookkeeping with a mock device whose work completes only when the test says so.
#pragma once
#include <cstddef>
#include <cstdint>
#include <new>
#include <utility>
#include "cairn_runtime.hpp"
namespace cr::reuse {

// What a request for more scratch than the arena holds may do.
struct Budget {
  std::size_t reserve = 0;  // allocated when the context is made; a request within it never allocates
  std::size_t most = 0;     // the arena may grow up to this, and only when it is larger than `reserve`
};

// How the arena's storage is taken and released: synchronously, or ordered on a stream, which lets growth
// avoid stopping the host but may, as CUDA documents for its pools, add dependencies between streams.
enum class Allocation { synchronous, stream_ordered };

enum class Scratch { ok, over_budget };  // what a request for scratch answered

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
    marker_ = api_.make_event();
    if(budget_.reserve) arena_ = api_.alloc(budget_.reserve), capacity_ = budget_.reserve;
  }
  Context(const Context&) = delete;
  Context& operator=(const Context&) = delete;
  Context(Context&&) = delete;
  Context& operator=(Context&&) = delete;
  // Every lane must have come back: a lane still lent is work nobody waited for, which traps like a ticket.
  ~Context() noexcept {
    if(lent_ != 0 || leased_) trap();
    if(used_) api_.sync_event(marker_);  // nothing below may go while the device still reads the arena
    if(arena_) api_.free(arena_);
    for(Lane* lane = free_; lane != nullptr;) {
      Lane* next = lane->next;
      api_.destroy_event(lane->event);
      api_.destroy_stream(lane->stream);
      delete lane;
      lane = next;
    }
    api_.destroy_event(marker_);
  }

  // A lane whose earlier work has completed, or a new one: never waits, and never shares a stream, so two
  // operations lent two lanes stay as independent as two tickets with streams of their own.
  Lane* lend() noexcept {
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
    return lane;
  }
  // Wait for the lane's work, then keep the lane for the next operation.
  void give_back(Lane* lane) noexcept {
    api_.sync_stream(lane->stream);
    lane->next = free_;
    free_ = lane;
    --lent_;
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
  Api& api() noexcept { return api_; }

private:
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
  bool used_ = false;    // whether marker_ has been recorded since the arena last changed
  bool leased_ = false;  // between acquire and release
};
}  // namespace cr::reuse

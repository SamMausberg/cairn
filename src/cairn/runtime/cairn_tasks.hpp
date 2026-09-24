// CAIRN task threads: the crew of reusable threads a `spawn` runs on, linear tickets and task groups.
// cairn_parallel.hpp includes this after the lane pool, whose bounded spin before a futex (lanes::linger) the
// crew waits with too, so generated code that includes cairn_parallel.hpp has both. Tasks never run on the lanes.
#pragma once
#include <atomic>
#include <condition_variable>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <new>
#include <thread>
#include <type_traits>
#include <utility>
#include "cairn_parallel.hpp"
namespace cr::par {

// Task threads. A spawn takes a thread that is parked, or starts a new one when none is, so a task never
// waits for a thread: every task starts at once, as it would on a thread made for it, and no arrangement of
// tasks that block on one another, or that never end, can hold another back. Only the thread is reused. A
// thread whose task has ended lingers, then parks; the ticket's wait hands it back and the next spawn wakes
// it instead of paying for a new one, and a group keeps its berths' threads until its own wait. At most KEEP
// threads stay parked; one handed back beyond that ends. Tasks never run on the lane pool, so a lane may
// spawn a task and wait for it. The effect row still says what a task may block on; nothing here needs to
// read it, because nothing ever waits for a thread to come free.
namespace crew {
enum : unsigned { IDLE = 0, RUN = 1, DONE = 2, QUIT = 3 };
using Job = void (*)(void*) noexcept;

// One thread and what it is doing. A worker is freed only by the thread that ends it, after joining it,
// so every other thread may touch its state for as long as it holds the worker.
struct alignas(64) Worker {
  std::mutex m;
  std::condition_variable wake;  // the thread waits here for RUN or QUIT
  std::condition_variable done;  // whoever holds the worker waits here for DONE
  std::atomic<unsigned> state{IDLE};
  Job job = nullptr;
  void* arg = nullptr;
  Worker* next = nullptr;  // the parked stack, guarded by the crew's mutex
  std::thread thread;
};

inline void serve(Worker& w) noexcept {
  for(;;) {
    const auto called = [&w] {  // relaxed while it spins; the acquiring load below is what acts on it
      const unsigned s = w.state.load(std::memory_order_relaxed);
      return s == RUN || s == QUIT;
    };
    if(!lanes::linger(called)) {
      std::unique_lock<std::mutex> hold(w.m);
      w.wake.wait(hold, called);
    }
    if(w.state.load(std::memory_order_acquire) == QUIT) return;
    w.job(w.arg);
    {
      std::lock_guard<std::mutex> hold(w.m);
      w.state.store(DONE, std::memory_order_release);
    }
    w.done.notify_all();
  }
}

// Start `job(arg)` on a worker that is idle or whose last job is DONE.
inline void give(Worker& w, Job job, void* arg) noexcept {
  {
    std::lock_guard<std::mutex> hold(w.m);
    if(w.state.load(std::memory_order_relaxed) == RUN) trap();  // one job at a time
    w.job = job;
    w.arg = arg;
    w.state.store(RUN, std::memory_order_release);
  }
  w.wake.notify_one();
}

// Return once the worker's job has ended: after this the job touches nothing, and the worker may be given again.
inline void finish(Worker& w) noexcept {
  const auto ended = [&w] { return w.state.load(std::memory_order_relaxed) == DONE; };
  if(lanes::linger(ended) && w.state.load(std::memory_order_acquire) == DONE) return;
  std::unique_lock<std::mutex> hold(w.m);  // the mutex orders what the job wrote before the store of DONE
  w.done.wait(hold, ended);
}

inline void end(Worker* w) noexcept {
  {
    std::lock_guard<std::mutex> hold(w->m);
    w->state.store(QUIT, std::memory_order_release);
  }
  w->wake.notify_one();
  w->thread.join();
  delete w;
}

#define CAIRN_TASK_CREW 1  // lets a benchmark built against older headers tell the two apart

class Crew final {
public:
  // A parked worker, or a new one. Never waits: the thread a task needs exists when this returns.
  Worker* take() noexcept {
    {
      std::lock_guard<std::mutex> hold(m_);
      if(parked_ != nullptr) {
        Worker* w = parked_;
        parked_ = w->next;
        --count_;
        return w;
      }
    }
    Worker* w = new(std::nothrow) Worker;
    if(w == nullptr) trap();
    w->thread = std::thread([w]() noexcept { serve(*w); });
    started_.fetch_add(1, std::memory_order_relaxed);
    return w;
  }
  // A worker whose job has finished: park it for the next spawn, or end it when KEEP are already parked.
  void hand_back(Worker* w) noexcept {
    {
      std::lock_guard<std::mutex> hold(m_);
      if(!retired_ && count_ < keep_) {
        w->state.store(IDLE, std::memory_order_relaxed);
        w->next = parked_;
        parked_ = w;
        ++count_;
        return;
      }
    }
    end(w);
  }
  // At exit: end every parked worker. A worker still held belongs to a ticket or a group nobody waited, which
  // the language makes unreachable; one handed back after this ends instead of parking.
  void retire() noexcept {
    Worker* all;
    {
      std::lock_guard<std::mutex> hold(m_);
      retired_ = true;
      all = std::exchange(parked_, nullptr);
      count_ = 0;
    }
    while(all != nullptr) end(std::exchange(all, all->next));
  }
  std::size_t started() const noexcept { return started_.load(std::memory_order_relaxed); }
  std::size_t parked() noexcept {
    std::lock_guard<std::mutex> hold(m_);
    return count_;
  }
  // As many parked threads as cores: enough for a pipeline to find its threads warm, and bounded, because a
  // parked thread still holds its stack's address space under the run's memory limit.
  static std::size_t keep() noexcept {
    const unsigned found = std::thread::hardware_concurrency();
    return found != 0 ? std::size_t(found) : std::size_t(1);
  }

private:
  std::mutex m_;
  Worker* parked_ = nullptr;
  std::size_t count_ = 0;
  const std::size_t keep_ = keep();
  bool retired_ = false;
  std::atomic<std::size_t> started_{0};
};

// Built by the first spawn and never destroyed, like the lane pool; the exit handler ends the parked threads.
inline Crew& shared() noexcept {
  static Crew* const one = []() noexcept {
    Crew* made = new(std::nothrow) Crew();
    if(made == nullptr) trap();
    if(std::atexit(+[]() noexcept { shared().retire(); }) != 0) trap();
    return made;
  }();
  return *one;
}
}  // namespace crew

// A linear ticket: exactly one wait() consumes it. The result cell is owned separately from the ticket,
// so a move does not disturb the running task. A dropped ticket is a contract breach: the type system
// makes it unreachable, the runtime makes it loud. The task's captures end on its own thread when it
// returns, as they would have with a thread made for it.
template<class T> class Task final {
  using Slot = std::conditional_t<std::is_void_v<T>, char, T>;
  template<class F> struct Carry {
    F f;
    Slot* out;
    static void run(void* held) noexcept {
      Carry* c = static_cast<Carry*>(held);
      if constexpr(std::is_void_v<T>) c->f(); else *c->out = c->f();
      delete c;
    }
  };
  std::unique_ptr<Slot> slot_;
  crew::Worker* w_ = nullptr;
  template<class F> explicit Task(F&& f) noexcept : slot_(new(std::nothrow) Slot{}) {
    using Held = Carry<std::decay_t<F>>;
    Held* c = slot_ ? new(std::nothrow) Held{std::forward<F>(f), slot_.get()} : nullptr;
    if(c == nullptr) trap();
    w_ = crew::shared().take();
    crew::give(*w_, &Held::run, c);
  }
public:
  template<class F> static Task spawn(F&& f) noexcept { return Task(std::forward<F>(f)); }
  Task(Task&& o) noexcept : slot_(std::move(o.slot_)), w_(std::exchange(o.w_, nullptr)) {}
  Task(const Task&) = delete;
  Task& operator=(const Task&) = delete;
  Task& operator=(Task&&) = delete;  // a linear value is initialized, never overwritten
  ~Task() noexcept { if(w_) trap(); }
  T wait() && noexcept {
    if(!w_) trap();
    crew::finish(*w_);
    crew::shared().hand_back(std::exchange(w_, nullptr));
    if constexpr(!std::is_void_v<T>) return std::move(*slot_);
  }
};

// A task group: up to `capacity` tasks in flight at once, collected in the order they finish. Linear
// like a ticket: exactly one wait() consumes it, waiting for whatever still runs and dropping every result
// nobody collected. Everything it will ever hold is allocated here, once, and a berth keeps the thread its
// first task took until wait() hands it back, so a group refilled round after round starts no threads after
// its first; a group that is full or empty traps instead of growing or blocking forever. A task never runs
// on the lane pool, and only the owning thread submits, collects and waits, so `spare_`, `free_` and
// `workers_` need no lock; the done ring is what the tasks write.
template<class T> class Group final {
  using Slot = std::conditional_t<std::is_void_v<T>, char, T>;
  template<class F> struct Carry {
    F f;
    Group* g;
    std::size_t k;
    static void run(void* held) noexcept {
      Carry* c = static_cast<Carry*>(held);
      Group* const g = c->g;
      const std::size_t k = c->k;
      if constexpr(std::is_void_v<T>) c->f(); else g->slots_[k] = c->f();
      delete c;  // the task's captures end on its own thread
      std::lock_guard<std::mutex> hold(g->m_);
      g->done_[(g->head_ + g->finished_) % g->capacity_] = k;
      ++g->finished_;
      g->cv_.notify_one();  // under the lock: once it is released this task touches nothing of the group
    }
  };
  const std::size_t capacity_;
  std::unique_ptr<Slot[]> slots_;             // one result cell per berth; reset by wait()
  std::unique_ptr<crew::Worker*[]> workers_;  // the thread each berth has held since its first task
  std::unique_ptr<std::size_t[]> free_;       // berths nothing runs in, as a stack
  std::unique_ptr<std::size_t[]> done_;       // berths whose task has finished, as a ring; guarded by m_
  std::size_t spare_ = 0;                     // entries of free_
  std::size_t head_ = 0;                      // the next berth to collect from done_
  std::size_t finished_ = 0;                  // berths queued in done_ and not yet collected
  std::mutex m_;
  std::condition_variable cv_;
public:
  explicit Group(std::size_t capacity) noexcept
      : capacity_(capacity), slots_(new(std::nothrow) Slot[capacity]{}),
        workers_(new(std::nothrow) crew::Worker*[capacity]()), free_(new(std::nothrow) std::size_t[capacity]),
        done_(new(std::nothrow) std::size_t[capacity]) {
    if(!slots_ || !workers_ || !free_ || !done_) trap();
    for(std::size_t k = capacity; k-- > 0;) free_[spare_++] = k;
  }
  Group(const Group&) = delete;
  Group& operator=(const Group&) = delete;
  Group(Group&&) = delete;
  Group& operator=(Group&&) = delete;
  ~Group() noexcept { if(slots_) trap(); }  // The type system makes an unwaited group unreachable; be loud.
  template<class F> void submit(F&& f) noexcept {
    if(spare_ == 0) trap();  // capacity_ tasks are already outstanding
    const std::size_t k = free_[--spare_];
    using Held = Carry<std::decay_t<F>>;
    Held* c = new(std::nothrow) Held{std::forward<F>(f), this, k};
    if(c == nullptr) trap();
    if(workers_[k] == nullptr) workers_[k] = crew::shared().take();
    crew::give(*workers_[k], &Held::run, c);
  }
  T collect() noexcept {
    if(spare_ == capacity_) trap();  // nothing is outstanding
    std::size_t k;
    {
      std::unique_lock<std::mutex> hold(m_);
      cv_.wait(hold, [this] { return finished_ != 0; });
      k = done_[head_];
      head_ = (head_ + 1) % capacity_;
      --finished_;
    }
    crew::finish(*workers_[k]);  // it has finished: this only waits for its thread to say so
    free_[spare_++] = k;
    if constexpr(!std::is_void_v<T>) return std::exchange(slots_[k], Slot{});
  }
  void wait() && noexcept {
    for(std::size_t k = 0; k < capacity_; ++k)
      if(workers_[k] != nullptr) {
        crew::finish(*workers_[k]);
        crew::shared().hand_back(std::exchange(workers_[k], nullptr));
      }
    slots_.reset();  // every result nobody collected is released here
    spare_ = capacity_;
  }
};
} // namespace cr::par

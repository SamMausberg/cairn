// Self-checking test for cr::reuse: exit 0 is a pass. With an argument it runs one death case, which must abort.
// The context's bookkeeping is driven here by a mock device, so what it promises is checked without a GPU: a lane
// comes back only after its work completed, scratch is never freed or handed on while queued work still touches
// it, a budget either suffices, grows only as far as it allows, or answers over_budget, and nothing leaks. The
// mock queues work per stream and completes it only when the test asks, in stream order and after every event
// a stream was told to wait for, so an ordering the context forgot to ask for shows up as a use of dead memory.
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <utility>
#include <vector>
#include "cairn_reuse.hpp"

static int failures = 0;
static long checked = 0;
#define CHECK(c) \
  do { \
    ++checked; \
    if(!(c)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } \
  } while(0)

// The machine the context runs on. Every stream is a queue of items; an item completes only once the items
// before it on its stream have, and, for a wait, once the stream it waits on has reached the recorded point.
struct Machine {
  enum Kind { touch, wait, alloc, release };
  struct Item {
    Kind kind;
    void* p = nullptr;          // what a touch uses, an alloc makes live or a release kills
    int other = -1;             // for a wait: the stream waited on
    std::size_t upto = 0;       // for a wait: how many of that stream's items must have completed
    int label = 0;              // a touch's name, for the order the test reads back
  };
  std::vector<std::vector<Item>> queue;  // per stream, everything ever queued
  std::vector<std::size_t> done;         // per stream, how many of its items have completed
  std::vector<bool> alive;               // per stream
  std::map<int, std::pair<int, std::size_t>> events;  // event -> (stream, items queued there when recorded)
  std::set<void*> live;                  // storage the device may touch now
  std::set<void*> taken;                 // storage allocated and not yet freed, as the host sees it
  std::vector<int> order;                // labels of touches, in the order they completed
  int violations = 0, streams = 0, streams_gone = 0, event_count = 0, events_gone = 0, allocs = 0, frees = 0;
  char heap[64][1] = {};                 // distinct addresses to hand out
  int next_block = 0;

  int make_stream() {
    queue.emplace_back();
    done.push_back(0);
    alive.push_back(true);
    ++streams;
    return int(queue.size()) - 1;
  }
  void run(int s) {  // complete everything queued on s, and whatever it waits for first
    while(done[s] < queue[s].size()) {
      Item& it = queue[s][done[s]];
      if(it.kind == wait)
        while(done[it.other] < it.upto) run_one(it.other);
      else if(it.kind == touch) {
        if(!live.count(it.p)) ++violations;  // the device used storage that was freed or not yet made
        order.push_back(it.label);
      } else if(it.kind == alloc) live.insert(it.p);
      else live.erase(it.p);
      ++done[s];
    }
  }
  void run_one(int s) {
    const std::size_t target = done[s] + 1;
    while(done[s] < target) {
      Item& it = queue[s][done[s]];
      if(it.kind == wait)
        while(done[it.other] < it.upto) run_one(it.other);
      else if(it.kind == touch) {
        if(!live.count(it.p)) ++violations;
        order.push_back(it.label);
      } else if(it.kind == alloc) live.insert(it.p);
      else live.erase(it.p);
      ++done[s];
    }
  }
  void* block() { return heap[next_block++ % 64]; }
};

// The Api a context is instantiated with: the mock above, through a pointer so the test can inspect it.
struct Mock {
  using Stream = int;
  using Event = int;
  Machine* m;
  Stream make_stream() { return m->make_stream(); }
  void destroy_stream(Stream s) {
    if(m->done[s] != m->queue[s].size()) ++m->violations;  // destroyed with work still queued
    m->alive[s] = false;
    ++m->streams_gone;
  }
  Event make_event() { return ++m->event_count; }
  void destroy_event(Event) { ++m->events_gone; }
  void record(Event e, Stream s) { m->events[e] = {s, m->queue[s].size()}; }
  void wait_event(Stream s, Event e) {
    const auto at = m->events.at(e);
    m->queue[s].push_back({Machine::wait, nullptr, at.first, at.second, 0});
  }
  void sync_stream(Stream s) { m->run(s); }
  void sync_event(Event e) {
    const auto at = m->events.at(e);
    while(m->done[at.first] < at.second) m->run_one(at.first);
  }
  void* alloc(std::size_t) {
    void* p = m->block();
    m->live.insert(p);
    m->taken.insert(p);
    ++m->allocs;
    return p;
  }
  void free(void* p) {
    m->live.erase(p);
    m->taken.erase(p);
    ++m->frees;
  }
  void* alloc_async(std::size_t, Stream s) {
    void* p = m->block();
    m->queue[s].push_back({Machine::alloc, p});
    m->taken.insert(p);
    ++m->allocs;
    return p;
  }
  void free_async(void* p, Stream s) {
    m->queue[s].push_back({Machine::release, p});
    m->taken.erase(p);
    ++m->frees;
  }
};
using Context = cr::reuse::Context<Mock>;
using cr::reuse::Allocation;
using cr::reuse::Budget;
using cr::reuse::Scratch;

// An operation that uses scratch: take the arena on a lane, queue a kernel that touches it, release it.
static void use(Context& c, Machine& m, Context::Lane& lane, std::size_t bytes, int label, Scratch want = Scratch::ok) {
  void* p = nullptr;
  const Scratch got = c.acquire(bytes, lane, &p);
  CHECK(got == want);
  if(got != Scratch::ok) return;
  m.queue[lane.stream].push_back({Machine::touch, p, -1, 0, label});
  c.release(lane);
}

static void test_lanes_are_reused() {
  Machine m;
  {
    Context c(Budget{256, 256}, Allocation::synchronous, Mock{&m});
    for(int k = 0; k < 100; ++k) {
      Context::Lane* lane = c.lend();
      use(c, m, *lane, 64, k);
      c.give_back(lane);
    }
    CHECK(c.streams_made() == 1);
    Context::Lane* a = c.lend();  // two lent at once are two streams
    Context::Lane* b = c.lend();
    CHECK(a->stream != b->stream && c.streams_made() == 2);
    c.give_back(a);
    c.give_back(b);
    CHECK(c.lent() == 0);
  }
  CHECK(m.allocs == 1 && m.frees == 1);  // the reserve, once, and nothing per operation
  CHECK(m.streams == m.streams_gone && m.event_count == m.events_gone);
  CHECK(m.taken.empty() && m.violations == 0);
}

// Two operations on two lanes share the arena: the second's kernel may complete only after the first's, even
// when the host asks for the second stream first, because its stream waits on the first's release.
static void test_scratch_users_are_ordered_on_the_device() {
  Machine m;
  {
    Context c(Budget{1024, 1024}, Allocation::synchronous, Mock{&m});
    Context::Lane* a = c.lend();
    Context::Lane* b = c.lend();
    use(c, m, *a, 512, 1);
    use(c, m, *b, 512, 2);
    c.give_back(b);  // runs b's stream, which must first run a's kernel
    c.give_back(a);
    CHECK((m.order == std::vector<int>{1, 2}));
  }
  CHECK(m.violations == 0 && m.taken.empty());
}

static void test_a_budget_answers_or_grows_as_declared() {
  Machine m;
  {
    Context fixed(Budget{256, 256}, Allocation::synchronous, Mock{&m});
    Context::Lane* lane = fixed.lend();
    use(fixed, m, *lane, 257, 0, Scratch::over_budget);  // no growth was allowed: a defined answer, no allocation
    CHECK(fixed.capacity() == 256 && fixed.grown() == 0 && m.allocs == 1);
    use(fixed, m, *lane, 256, 1);  // and the arena is still usable afterwards
    fixed.give_back(lane);
  }
  CHECK(m.violations == 0 && m.taken.empty());
  for(Allocation how : {Allocation::synchronous, Allocation::stream_ordered}) {
    Machine g;
    {
      Context c(Budget{128, 4096}, how, Mock{&g});
      Context::Lane* a = c.lend();
      Context::Lane* b = c.lend();
      use(c, g, *a, 128, 1);   // queued on a and not yet run
      use(c, g, *b, 2048, 2);  // grows while a's kernel may still touch the old arena
      use(c, g, *b, 8192, 3, Scratch::over_budget);
      CHECK(c.capacity() == 2048 && c.grown() == 1);
      c.give_back(a);
      c.give_back(b);
      CHECK((g.order == std::vector<int>{1, 2}));
    }
    CHECK(g.violations == 0);  // the old arena outlived its last user; the new one existed before its first
    CHECK(g.allocs == 2 && g.frees == 2 && g.taken.empty());
  }
}

// A context goes only once the device is done with its arena, and takes every stream and event with it.
static void test_a_context_ends_after_its_last_user() {
  Machine m;
  {
    Context c(Budget{64, 64}, Allocation::synchronous, Mock{&m});
    Context::Lane* lane = c.lend();
    use(c, m, *lane, 64, 7);
    // The lane is handed back without the host having run it: give_back waits for it, then the destructor
    // must still wait for the arena's last user before it frees the arena.
    c.give_back(lane);
  }
  CHECK(m.order == std::vector<int>{7});
  CHECK(m.violations == 0 && m.taken.empty() && m.streams == m.streams_gone);
}

// A stream the caller owns: synchronous work runs on it after what the caller queued there, a lane lent meanwhile
// starts after that work too, and the context never destroys the caller's stream.
static void test_a_bound_stream_stays_the_callers() {
  Machine m;
  const int mine = m.make_stream();
  void* p = m.block();
  m.live.insert(p);
  {
    Context c(Budget{64, 64}, Allocation::synchronous, Mock{&m});
    m.queue[mine].push_back({Machine::touch, p, -1, 0, 1});  // the caller's own work, not yet run
    c.bind(mine);
    Context::Lane* sync = c.lend(true);
    CHECK(sync->stream == mine && c.streams_made() == 0);
    c.give_back(sync);  // a wait for the caller's stream, so its work ran
    CHECK((m.order == std::vector<int>{1}));
    m.queue[mine].push_back({Machine::touch, p, -1, 0, 2});  // more of the caller's work, queued after the wait
    Context::Lane* lane = c.lend();
    m.queue[lane->stream].push_back({Machine::touch, p, -1, 0, 3});
    c.give_back(lane);  // runs the lane, which first waits for the caller's queued work
    CHECK((m.order == std::vector<int>{1, 2, 3}));
    c.unbind();
    Context::Lane* own = c.lend(true);
    CHECK(own->stream != mine && c.streams_made() == 1);
    c.give_back(own);
  }
  CHECK(m.alive[mine] && m.streams_gone == m.streams - 1);  // the caller's stream outlives the context
  CHECK(m.violations == 0);
}

// A held run: synchronous operations queue on one lane and nothing runs them until the run settles, once. An
// operation whose result the host reads first waits for what the run queued, and a lane lent meanwhile starts after
// it on the device.
static void test_a_held_run_waits_once() {
  Machine m;
  void* p = m.block();
  m.live.insert(p);
  {
    Context c(Budget{0, 0}, Allocation::synchronous, Mock{&m});
    c.hold();
    for(int k = 1; k <= 3; ++k) {
      Context::Lane* lane = c.lend(true);
      m.queue[lane->stream].push_back({Machine::touch, p, -1, 0, k});
      c.finish(lane);
    }
    CHECK(m.order.empty() && c.streams_made() == 1 && c.lent() == 1);  // queued on one lane, nothing waited for
    Context::Lane* other = c.lend();
    m.queue[other->stream].push_back({Machine::touch, p, -1, 0, 4});
    c.give_back(other);  // its stream first waits for the run's work
    CHECK((m.order == std::vector<int>{1, 2, 3, 4}));
    Context::Lane* lane = c.lend(true);
    m.queue[lane->stream].push_back({Machine::touch, p, -1, 0, 5});
    c.finish(lane);
    c.observed();  // a copy to host memory would come next: the run so far is waited for
    CHECK((m.order == std::vector<int>{1, 2, 3, 4, 5}) && c.lent() == 0);
    lane = c.lend(true);
    m.queue[lane->stream].push_back({Machine::touch, p, -1, 0, 6});
    c.finish(lane);
    c.settle();
    CHECK((m.order == std::vector<int>{1, 2, 3, 4, 5, 6}) && c.lent() == 0);
  }
  CHECK(m.violations == 0 && m.streams == m.streams_gone);
}

// An enqueued call runs on the caller's stream and waits for nothing: the context made no stream and no event,
// the work is still queued when the call ends, and the thread's own binding is back.
static void test_an_enqueued_call_leaves_the_wait_to_the_caller() {
  Machine m;
  const int mine = m.make_stream(), theirs = m.make_stream();
  void* p = m.block();
  m.live.insert(p);
  {
    Context c(Budget{0, 0}, Allocation::synchronous, Mock{&m});
    c.bind(theirs);
    c.enqueue(mine);
    c.hold();  // a held body inside the call: only the call's end matters
    for(int k = 1; k <= 2; ++k) {
      Context::Lane* lane = c.lend(true);
      CHECK(lane->stream == mine);
      m.queue[lane->stream].push_back({Machine::touch, p, -1, 0, k});
      c.finish(lane);
    }
    c.settle();
    c.leave();
    CHECK(m.order.empty() && m.done[mine] == 0 && c.lent() == 0);
    CHECK(c.streams_made() == 0 && m.event_count == 0 && c.bound() && !c.enqueued());
    m.run(mine);  // the caller synchronizes its stream
    CHECK((m.order == std::vector<int>{1, 2}));
    Context::Lane* lane = c.lend(true);
    CHECK(lane->stream == theirs);
    c.give_back(lane);
  }
  CHECK(m.violations == 0);
}

static int death(const char* name) {
  Machine m;
  if(!std::strcmp(name, "lane_not_given_back")) {
    Context c(Budget{64, 64}, Allocation::synchronous, Mock{&m});
    (void)c.lend();  // the context ends with a lane still lent: it must trap
  } else if(!std::strcmp(name, "scratch_acquired_twice")) {
    Context c(Budget{64, 64}, Allocation::synchronous, Mock{&m});
    Context::Lane* lane = c.lend();
    void* p = nullptr;
    (void)c.acquire(8, *lane, &p);
    (void)c.acquire(8, *lane, &p);
  } else if(!std::strcmp(name, "released_without_acquire")) {
    Context c(Budget{64, 64}, Allocation::synchronous, Mock{&m});
    Context::Lane* lane = c.lend();
    c.release(*lane);
  } else if(!std::strcmp(name, "bound_lane_lent_twice")) {
    Context c(Budget{64, 64}, Allocation::synchronous, Mock{&m});
    c.bind(m.make_stream());
    (void)c.lend(true);
    (void)c.lend(true);  // synchronous work does not nest on the caller's stream
  } else if(!std::strcmp(name, "observed_while_enqueued")) {
    Context c(Budget{64, 64}, Allocation::synchronous, Mock{&m});
    c.enqueue(m.make_stream());
    c.observed();  // a result the host would read, where nothing may wait
  } else if(!std::strcmp(name, "enqueued_inside_a_held_run")) {
    Context c(Budget{64, 64}, Allocation::synchronous, Mock{&m});
    c.hold();
    c.enqueue(m.make_stream());
  } else if(!std::strcmp(name, "settled_without_a_run")) {
    Context c(Budget{64, 64}, Allocation::synchronous, Mock{&m});
    c.settle();
  } else {
    std::fprintf(stderr, "unknown death case %s\n", name);
    return 2;
  }
  std::fprintf(stderr, "death case %s did not abort\n", name);
  return 3;
}

int main(int argc, char** argv) {
  static const char* cases[] = {"lane_not_given_back",     "scratch_acquired_twice",     "released_without_acquire",
                                "bound_lane_lent_twice",   "observed_while_enqueued",    "enqueued_inside_a_held_run",
                                "settled_without_a_run"};
  if(argc > 1 && !std::strcmp(argv[1], "--list")) {
    for(const char* c : cases) std::printf("%s\n", c);
    return 0;
  }
  if(argc > 1) return death(argv[1]);
  test_lanes_are_reused();
  test_scratch_users_are_ordered_on_the_device();
  test_a_budget_answers_or_grows_as_declared();
  test_a_context_ends_after_its_last_user();
  test_a_bound_stream_stays_the_callers();
  test_a_held_run_waits_once();
  test_an_enqueued_call_leaves_the_wait_to_the_caller();
  std::printf("reuse_runtime: %s after %ld checks\n", failures ? "FAILED" : "ok", checked);
  return failures ? 1 : 0;
}

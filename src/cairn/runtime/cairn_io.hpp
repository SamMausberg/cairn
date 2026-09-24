// CAIRN I/O rings: kernel operations in flight without a thread each, collected in the order they finish.
// A ring owns the memory of every operation it holds. Submitting moves a Buf into one of its berths, the
// kernel reads or writes that Buf's storage, and collect() hands the Buf back with the kernel's result,
// so no borrow ever outlives the call that made it and a buffer is reusable the moment it is collected.
// Every submission comes back through collect() exactly once: with the kernel's result, or with the errno
// the kernel refused it with, which is also how a ring the kernel would not set up answers. The ring is
// Linux io_uring over the raw system calls; nothing here allocates after construction.
#pragma once
#if defined(CAIRN_FREESTANDING)
#error "cairn_io.hpp is hosted: a freestanding image has no kernel, and toolchain.audit_effects rejects every effect that reaches this header."
#endif
#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <linux/time_types.h>  // __kernel_timespec, first: io_uring.h of Linux 5.15 does not include it
#include <linux/io_uring.h>
#include <memory>
#include <new>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <utility>
#include "cairn_owners.hpp"
namespace cr::io {

#if defined(CAIRN_IO_FAULTS)
// Test builds only: the next `refuse` submissions act as if the kernel had refused them with `errno_`, so the
// path a real -EAGAIN takes is exercised without starving the kernel. Production builds have no such hook.
namespace faults {
inline std::size_t refuse = 0;
inline std::int64_t errno_ = -EAGAIN;
inline std::int64_t refusal() noexcept { return refuse ? (--refuse, errno_) : 0; }
}  // namespace faults
#endif

// What an operation asks the kernel to do; the numbers are the ones the language lowers to.
enum class Op : std::uint8_t { read = 0, write = 1, recv = 2, send = 3, accept = 4, timeout = 5 };

// Up to `capacity` operations in flight at once. Linear in the source language: exactly one wait()
// consumes it, after every operation has finished, so no storage the kernel may still touch is ever
// released. What the environment decides is a value: a kernel that refuses io_uring leaves the ring down
// (status() says why, and each submission comes straight back with that errno), and a kernel that refuses
// one submission returns it the same way. What the program decides is a guard: a submission to a full ring
// or a collect with nothing in flight traps, and room() and pending() let a program see both coming.
class Ring final {
  int fd_ = -1;
  std::int64_t status_ = 0;                    // 0, or the -errno the kernel refused to set the ring up with
  std::size_t capacity_ = 0, spare_ = 0, outstanding_ = 0;
  std::unique_ptr<Buf<std::uint8_t>[]> held_;  // berth k owns the memory of the operation tagged k
  std::unique_ptr<std::uint64_t[]> tags_;      // the program's tag for berth k
  std::unique_ptr<std::size_t[]> free_;        // berths nothing is in flight in, as a stack
  std::unique_ptr<__kernel_timespec[]> times_; // the interval a timeout in berth k waits for
  std::unique_ptr<std::uint32_t[]> live_;      // berth k's generation while in flight, 0 when it is free
  std::unique_ptr<std::uint32_t[]> stopping_;  // the generation a cancel was asked for, at most once each
  std::uint32_t generation_ = 0;               // bumped per submission, so a stale cancel names nothing
  std::size_t cancels_ = 0;                    // cancel requests whose own completion has not been read
  std::unique_ptr<std::int64_t[]> answer_;     // berth k's result when it finished without reaching the kernel
  std::unique_ptr<std::size_t[]> ready_;       // those berths, first finished first, as a circular queue
  std::size_t ready_head_ = 0, ready_count_ = 0;
  static constexpr std::uint64_t CANCEL = std::uint64_t(1) << 63;  // marks a cancel request's completion
  // An operation's user_data is its berth and its generation; a cancel request's is marked by CANCEL.
  static std::uint64_t name(std::size_t k, std::uint32_t generation) noexcept {
    return (std::uint64_t(generation) << 32) | k;
  }
  void* sq_ = nullptr;
  void* cq_ = nullptr;
  io_uring_sqe* sqes_ = nullptr;
  std::size_t sq_bytes_ = 0, cq_bytes_ = 0, sqe_bytes_ = 0;
  unsigned *sq_tail_ = nullptr, *sq_mask_ = nullptr, *sq_array_ = nullptr;
  unsigned *cq_head_ = nullptr, *cq_tail_ = nullptr, *cq_mask_ = nullptr;
  io_uring_cqe* cqes_ = nullptr;

  static unsigned* at(void* base, unsigned offset) noexcept {
    return reinterpret_cast<unsigned*>(static_cast<char*>(base) + offset);
  }
  // Hand one entry to the kernel: 0, or the -errno it refused the entry with. The kernel reads the submission
  // queue only inside io_uring_enter (no SQPOLL thread), and a refused entry is one it did not consume, so
  // taking the tail back leaves the queue exactly as it was.
  std::int64_t push(const io_uring_sqe& e) noexcept {
    const unsigned tail = *sq_tail_, slot = tail & *sq_mask_;  // only this thread writes the tail
    sqes_[slot] = e;
    sq_array_[slot] = slot;
    std::atomic_ref<unsigned>(*sq_tail_).store(tail + 1, std::memory_order_release);
    std::int64_t refused = 0;
#if defined(CAIRN_IO_FAULTS)
    refused = faults::refusal();
#endif
    if(!refused) {
      const long done = enter(1, 0);
      if(done == 1) return 0;
      refused = done < 0 ? done : -EAGAIN;
    }
    std::atomic_ref<unsigned>(*sq_tail_).store(tail, std::memory_order_release);
    return refused;
  }
  // A refusal of a submission for want of kernel memory or an overflowing completion queue is the kernel's to
  // decide, so it is returned; any other failure means the ring itself is broken, and traps.
  long enter(unsigned submit, unsigned wait) noexcept {
    for(;;) {
      const long done = syscall(__NR_io_uring_enter, fd_, submit, wait, wait ? IORING_ENTER_GETEVENTS : 0u, nullptr, 0);
      if(done >= 0) return done;
      if(errno == EINTR) continue;
      if(submit && (errno == EAGAIN || errno == ENOMEM || errno == EBUSY)) return -errno;
      trap();
    }
  }
  // Berth k finished without the kernel: it comes back through collect() before anything still in flight.
  void answer(std::size_t k, std::int64_t result) noexcept {
    answer_[k] = result;
    ready_[(ready_head_ + ready_count_++) % capacity_] = k;
  }
  // Unmap what is mapped and close the descriptor; `why` is 0 after wait(), or the errno the set-up failed with.
  void down(std::int64_t why) noexcept {
    if(sqes_) munmap(sqes_, sqe_bytes_);
    if(cq_ && cq_ != sq_) munmap(cq_, cq_bytes_);
    if(sq_) munmap(sq_, sq_bytes_);
    sqes_ = nullptr, sq_ = cq_ = nullptr;
    if(fd_ >= 0) close(fd_);
    fd_ = -1;
    status_ = why;
  }

public:
  explicit Ring(std::size_t capacity) noexcept
      : capacity_(capacity), held_(new(std::nothrow) Buf<std::uint8_t>[capacity]),
        tags_(new(std::nothrow) std::uint64_t[capacity]()), free_(new(std::nothrow) std::size_t[capacity]),
        times_(new(std::nothrow) __kernel_timespec[capacity]()), live_(new(std::nothrow) std::uint32_t[capacity]()),
        stopping_(new(std::nothrow) std::uint32_t[capacity]()), answer_(new(std::nothrow) std::int64_t[capacity]()),
        ready_(new(std::nothrow) std::size_t[capacity]()) {
    if(capacity == 0 || capacity > 4096 || !held_ || !tags_ || !free_ || !times_ || !live_ || !stopping_) trap();
    if(!answer_ || !ready_) trap();
    for(std::size_t k = capacity; k-- > 0;) free_[spare_++] = k;
    io_uring_params p;
    std::memset(&p, 0, sizeof p);
    const long made = syscall(__NR_io_uring_setup, static_cast<unsigned>(capacity), &p);
    if(made < 0) {  // no io_uring here: a seccomp filter, a sysctl, no descriptor left
      down(-errno);
      return;
    }
    fd_ = static_cast<int>(made);
    sq_bytes_ = p.sq_off.array + p.sq_entries * sizeof(unsigned);
    cq_bytes_ = p.cq_off.cqes + p.cq_entries * sizeof(io_uring_cqe);
    const bool single = p.features & IORING_FEAT_SINGLE_MMAP;
    if(single) sq_bytes_ = cq_bytes_ = sq_bytes_ > cq_bytes_ ? sq_bytes_ : cq_bytes_;
    sq_ = mmap(nullptr, sq_bytes_, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE, fd_, IORING_OFF_SQ_RING);
    cq_ = single ? sq_ : mmap(nullptr, cq_bytes_, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE, fd_, IORING_OFF_CQ_RING);
    sqe_bytes_ = p.sq_entries * sizeof(io_uring_sqe);
    void* sqes = mmap(nullptr, sqe_bytes_, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE, fd_, IORING_OFF_SQES);
    if(sq_ == MAP_FAILED || cq_ == MAP_FAILED || sqes == MAP_FAILED) {
      const std::int64_t why = -errno;
      if(sq_ == MAP_FAILED) sq_ = nullptr;
      if(cq_ == MAP_FAILED) cq_ = nullptr;
      if(sqes != MAP_FAILED) sqes_ = static_cast<io_uring_sqe*>(sqes);
      down(why);
      return;
    }
    sqes_ = static_cast<io_uring_sqe*>(sqes);
    sq_tail_ = at(sq_, p.sq_off.tail), sq_mask_ = at(sq_, p.sq_off.ring_mask), sq_array_ = at(sq_, p.sq_off.array);
    cq_head_ = at(cq_, p.cq_off.head), cq_tail_ = at(cq_, p.cq_off.tail), cq_mask_ = at(cq_, p.cq_off.ring_mask);
    cqes_ = reinterpret_cast<io_uring_cqe*>(static_cast<char*>(cq_) + p.cq_off.cqes);
  }
  Ring(const Ring&) = delete;
  Ring& operator=(const Ring&) = delete;
  Ring(Ring&&) = delete;
  Ring& operator=(Ring&&) = delete;
  ~Ring() noexcept { if(outstanding_ || fd_ >= 0) trap(); }  // The type system makes an unwaited ring unreachable.

  std::int64_t status() const noexcept { return status_; }       // 0 when the kernel set the ring up
  std::size_t room() const noexcept { return spare_; }           // submissions it takes before it is full
  std::size_t pending() const noexcept { return outstanding_; }  // submissions collect() has still to return

  // Hand the kernel one operation on `fd` over the first `count` bytes of `data`, which the ring now owns.
  // `offset` is the file position for read and write, and ignored by the socket operations.
  void submit(Op op, int fd, Buf<std::uint8_t>&& data, std::size_t count, std::uint64_t offset, std::uint64_t tag) noexcept {
    if(spare_ == 0) trap();          // capacity_ operations are already in flight
    if(count > data.size()) trap();  // an operation never reaches past the storage it was given
    const std::size_t k = free_[--spare_];
    held_[k] = std::move(data);
    tags_[k] = tag;
    static constexpr std::uint8_t codes[] = {IORING_OP_READ, IORING_OP_WRITE, IORING_OP_RECV, IORING_OP_SEND,
                                             IORING_OP_ACCEPT, IORING_OP_TIMEOUT};
    io_uring_sqe e;
    std::memset(&e, 0, sizeof e);
    e.opcode = codes[static_cast<unsigned>(op)];
    e.fd = op == Op::timeout ? -1 : fd;
    if(op == Op::timeout) {  // `offset` is the interval in nanoseconds; no other completion ends it early
      times_[k] = {static_cast<long long>(offset / 1000000000u), static_cast<long long>(offset % 1000000000u)};
      e.addr = reinterpret_cast<std::uint64_t>(&times_[k]);
      e.len = 1;
    } else if(op != Op::accept) {
      e.addr = reinterpret_cast<std::uint64_t>(held_[k].data());
      e.len = count > 0x7ffff000u ? 0x7ffff000u : static_cast<unsigned>(count);  // the kernel's own ceiling
      e.off = (op == Op::read || op == Op::write) ? offset : 0;
    }
    ++outstanding_;
    if(status_) return answer(k, status_);  // a ring that is down answers at once, holding the Buf until collected
    generation_ = (generation_ % 0x7fffffffu) + 1;  // never 0, never reaching the CANCEL bit
    live_[k] = generation_;
    e.user_data = name(k, generation_);
    if(const std::int64_t refused = push(e)) {
      live_[k] = 0;  // the kernel never saw it, so nothing can cancel it
      answer(k, refused);
    }
  }

  // Ask the kernel to stop every operation in flight under `tag`. Each still finishes through collect(),
  // with -ECANCELED (or its own result, if it finished first) and its Buf, so nothing is lost or freed early.
  // A request the kernel refuses is not sent, and asking again sends it.
  void cancel(std::uint64_t tag) noexcept {
    for(std::size_t k = 0; k < capacity_; ++k) {
      if(!live_[k] || tags_[k] != tag || stopping_[k] == live_[k]) continue;
      io_uring_sqe e;
      std::memset(&e, 0, sizeof e);
      e.opcode = IORING_OP_ASYNC_CANCEL;
      e.fd = -1;
      e.addr = name(k, live_[k]);  // exactly this operation, never a later one in the same berth
      e.user_data = CANCEL | k;
      if(push(e)) continue;
      stopping_[k] = live_[k];  // so pending answers never outnumber the berths, and the kernel queue holds them
      ++cancels_;
    }
  }

  // The next operation to finish: its tag, the kernel's result (a byte count, a descriptor, or -errno),
  // and the Buf it was given, back in the program's hands.
  Buf<std::uint8_t> collect(std::uint64_t& tag, std::int64_t& result) noexcept {
    if(outstanding_ == 0) trap();  // nothing is in flight
    std::size_t k;
    if(ready_count_) {  // finished without the kernel, so first
      k = ready_[ready_head_];
      ready_head_ = (ready_head_ + 1) % capacity_;
      --ready_count_;
      result = answer_[k];
    } else for(;;) {
      const unsigned head = *cq_head_;  // only this thread writes the head
      while(std::atomic_ref<unsigned>(*cq_tail_).load(std::memory_order_acquire) == head) enter(0, 1);
      const io_uring_cqe& c = cqes_[head & *cq_mask_];
      const std::uint64_t data = c.user_data;
      result = c.res;
      std::atomic_ref<unsigned>(*cq_head_).store(head + 1, std::memory_order_release);
      k = static_cast<std::size_t>(data & 0xffffffffu);
      if(!(data & CANCEL)) break;
      --cancels_;  // a cancel request's own answer; the operation it named reports separately
    }
    live_[k] = 0;
    --outstanding_;
    tag = tags_[k];
    free_[spare_++] = k;
    return std::move(held_[k]);
  }

  // Every operation finished, every Buf nobody collected released, and the kernel's ring closed.
  void wait() && noexcept {
    std::uint64_t tag;
    std::int64_t result;
    while(outstanding_) (void)collect(tag, result);
    while(cancels_) {  // the answers to cancel requests that outlived the operations they named
      const unsigned head = *cq_head_;
      while(std::atomic_ref<unsigned>(*cq_tail_).load(std::memory_order_acquire) == head) enter(0, 1);
      std::atomic_ref<unsigned>(*cq_head_).store(head + 1, std::memory_order_release);
      --cancels_;
    }
    down(status_);
  }
};
} // namespace cr::io

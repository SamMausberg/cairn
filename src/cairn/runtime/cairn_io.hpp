// CAIRN I/O rings: kernel operations in flight without a thread each, collected in the order they finish.
// A ring owns the memory of every operation it holds. Submitting moves a Buf into one of its berths, the
// kernel reads or writes that Buf's storage, and collect() hands the Buf back with the kernel's result,
// so no borrow ever outlives the call that made it and a buffer is reusable the moment it is collected.
// The ring is Linux io_uring over the raw system calls; nothing here allocates after construction.
#pragma once
#if defined(CAIRN_FREESTANDING)
#error "cairn_io.hpp is hosted: a freestanding image has no kernel, and toolchain.audit_effects rejects every effect that reaches this header."
#endif
#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <linux/io_uring.h>
#include <memory>
#include <new>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <utility>
#include "cairn_owners.hpp"
namespace cr::io {

// What an operation asks the kernel to do; the numbers are the ones the language lowers to.
enum class Op : std::uint8_t { read = 0, write = 1, recv = 2, send = 3, accept = 4 };

// Up to `capacity` operations in flight at once. Linear in the source language: exactly one wait()
// consumes it, after every operation has finished, so no storage the kernel may still touch is ever
// released. A full ring or an empty collect traps instead of growing or blocking forever, and so does
// a kernel that refuses io_uring: the declaration is where that failure is visible.
class Ring final {
  int fd_ = -1;
  std::size_t spare_ = 0, outstanding_ = 0;
  std::unique_ptr<Buf<std::uint8_t>[]> held_;  // berth k owns the memory of the operation tagged k
  std::unique_ptr<std::uint64_t[]> tags_;      // the program's tag for berth k
  std::unique_ptr<std::size_t[]> free_;        // berths nothing is in flight in, as a stack
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
  int enter(unsigned submit, unsigned wait) noexcept {
    for(;;) {
      const long done = syscall(__NR_io_uring_enter, fd_, submit, wait, wait ? IORING_ENTER_GETEVENTS : 0u, nullptr, 0);
      if(done >= 0) return static_cast<int>(done);
      if(errno != EINTR) trap();
    }
  }

public:
  explicit Ring(std::size_t capacity) noexcept
      : held_(new(std::nothrow) Buf<std::uint8_t>[capacity]),
        tags_(new(std::nothrow) std::uint64_t[capacity]()), free_(new(std::nothrow) std::size_t[capacity]) {
    if(capacity == 0 || capacity > 4096 || !held_ || !tags_ || !free_) trap();
    io_uring_params p;
    std::memset(&p, 0, sizeof p);
    const long made = syscall(__NR_io_uring_setup, static_cast<unsigned>(capacity), &p);
    if(made < 0) trap();  // this kernel runs no io_uring here: the ring's declaration fails, visibly
    fd_ = static_cast<int>(made);
    sq_bytes_ = p.sq_off.array + p.sq_entries * sizeof(unsigned);
    cq_bytes_ = p.cq_off.cqes + p.cq_entries * sizeof(io_uring_cqe);
    const bool single = p.features & IORING_FEAT_SINGLE_MMAP;
    if(single) sq_bytes_ = cq_bytes_ = sq_bytes_ > cq_bytes_ ? sq_bytes_ : cq_bytes_;
    sq_ = mmap(nullptr, sq_bytes_, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE, fd_, IORING_OFF_SQ_RING);
    cq_ = single ? sq_ : mmap(nullptr, cq_bytes_, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE, fd_, IORING_OFF_CQ_RING);
    sqe_bytes_ = p.sq_entries * sizeof(io_uring_sqe);
    void* sqes = mmap(nullptr, sqe_bytes_, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE, fd_, IORING_OFF_SQES);
    if(sq_ == MAP_FAILED || cq_ == MAP_FAILED || sqes == MAP_FAILED) trap();
    sqes_ = static_cast<io_uring_sqe*>(sqes);
    sq_tail_ = at(sq_, p.sq_off.tail), sq_mask_ = at(sq_, p.sq_off.ring_mask), sq_array_ = at(sq_, p.sq_off.array);
    cq_head_ = at(cq_, p.cq_off.head), cq_tail_ = at(cq_, p.cq_off.tail), cq_mask_ = at(cq_, p.cq_off.ring_mask);
    cqes_ = reinterpret_cast<io_uring_cqe*>(static_cast<char*>(cq_) + p.cq_off.cqes);
    for(std::size_t k = capacity; k-- > 0;) free_[spare_++] = k;
  }
  Ring(const Ring&) = delete;
  Ring& operator=(const Ring&) = delete;
  Ring(Ring&&) = delete;
  Ring& operator=(Ring&&) = delete;
  ~Ring() noexcept { if(outstanding_ || fd_ >= 0) trap(); }  // The type system makes an unwaited ring unreachable.

  // Hand the kernel one operation on `fd` over the first `count` bytes of `data`, which the ring now owns.
  // `offset` is the file position for read and write, and ignored by the socket operations.
  void submit(Op op, int fd, Buf<std::uint8_t>&& data, std::size_t count, std::uint64_t offset, std::uint64_t tag) noexcept {
    if(spare_ == 0) trap();          // capacity_ operations are already in flight
    if(count > data.size()) trap();  // an operation never reaches past the storage it was given
    const std::size_t k = free_[--spare_];
    held_[k] = std::move(data);
    tags_[k] = tag;
    const unsigned tail = *sq_tail_, slot = tail & *sq_mask_;  // only this thread writes the tail
    io_uring_sqe& e = sqes_[slot];
    std::memset(&e, 0, sizeof e);
    static constexpr std::uint8_t codes[] = {IORING_OP_READ, IORING_OP_WRITE, IORING_OP_RECV, IORING_OP_SEND,
                                             IORING_OP_ACCEPT};
    e.opcode = codes[static_cast<unsigned>(op)];
    e.fd = fd;
    if(op != Op::accept) {
      e.addr = reinterpret_cast<std::uint64_t>(held_[k].data());
      e.len = count > 0x7ffff000u ? 0x7ffff000u : static_cast<unsigned>(count);  // the kernel's own ceiling
      e.off = (op == Op::read || op == Op::write) ? offset : 0;
    }
    e.user_data = k;
    sq_array_[slot] = slot;
    std::atomic_ref<unsigned>(*sq_tail_).store(tail + 1, std::memory_order_release);
    if(enter(1, 0) != 1) trap();
    ++outstanding_;
  }

  // The next operation to finish: its tag, the kernel's result (a byte count, a descriptor, or -errno),
  // and the Buf it was given, back in the program's hands.
  Buf<std::uint8_t> collect(std::uint64_t& tag, std::int64_t& result) noexcept {
    if(outstanding_ == 0) trap();  // nothing is in flight
    const unsigned head = *cq_head_;  // only this thread writes the head
    while(std::atomic_ref<unsigned>(*cq_tail_).load(std::memory_order_acquire) == head) enter(0, 1);
    const io_uring_cqe& c = cqes_[head & *cq_mask_];
    const std::size_t k = static_cast<std::size_t>(c.user_data);
    result = c.res;
    std::atomic_ref<unsigned>(*cq_head_).store(head + 1, std::memory_order_release);
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
    munmap(sqes_, sqe_bytes_);
    if(cq_ != sq_) munmap(cq_, cq_bytes_);
    munmap(sq_, sq_bytes_);
    close(fd_);
    fd_ = -1;
  }
};
} // namespace cr::io

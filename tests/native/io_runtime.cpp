// Self-checking test for cr::io::Ring: exit 0 is a pass. With an argument it runs one death case, which must
// abort the process; tests/runtime/test_native_runtime.py drives those as subprocesses. Built with the language
// contract flags. What it establishes: many operations in flight from one thread, results matched to their tags
// in the order they finish, every Buf handed back intact, the kernel's errors returned as values, and the ring's
// own limits enforced by traps.
#include <arpa/inet.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include "cairn_io.hpp"

using cr::Buf;
using cr::io::Op;
using cr::io::Ring;
static int failures = 0;
static long checked = 0;
#define CHECK(c) \
  do { \
    ++checked; \
    if(!(c)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } \
  } while(0)

static Buf<std::uint8_t> filled(std::size_t n, std::uint8_t seed) {
  Buf<std::uint8_t> b(n);
  for(std::size_t i = 0; i < n; ++i) b.data()[i] = static_cast<std::uint8_t>(seed + i * 7);
  return b;
}

// Sixty-four blocks written through a ring sixteen at a time, then read back the same way: every result is the
// block size, every tag comes back once, and every byte read is the byte written at that offset.
static void test_a_file_round_trip() {
  char path[] = "/tmp/cairn-ring-XXXXXX";
  const int fd = mkstemp(path);
  CHECK(fd >= 0);
  unlink(path);
  const std::size_t block = 4096, blocks = 64, depth = 16;
  {
    Ring ring(depth);
    std::size_t next = 0, done = 0;
    while(done < blocks) {
      while(next < blocks && next - done < depth) {
        ring.submit(Op::write, fd, filled(block, static_cast<std::uint8_t>(next)), block, next * block, next);
        ++next;
      }
      std::uint64_t tag = ~0ull;
      std::int64_t result = -1;
      Buf<std::uint8_t> back = ring.collect(tag, result);
      CHECK(tag < blocks && result == static_cast<std::int64_t>(block) && back.size() == block);
      CHECK(back.data()[1] == static_cast<std::uint8_t>(tag + 7));  // the Buf that comes back is the one given
      ++done;
    }
    std::move(ring).wait();
  }
  {
    Ring ring(depth);
    std::size_t seen = 0;
    bool right[64] = {};
    for(std::size_t k = 0; k < blocks; ++k) {
      if(k >= depth) {
        std::uint64_t tag;
        std::int64_t result;
        Buf<std::uint8_t> got = ring.collect(tag, result);
        bool same = result == static_cast<std::int64_t>(block);
        for(std::size_t i = 0; same && i < block; ++i) same = got.data()[i] == static_cast<std::uint8_t>(tag + i * 7);
        right[tag] = same;
        ++seen;
      }
      ring.submit(Op::read, fd, Buf<std::uint8_t>(block), block, k * block, k);
    }
    while(seen < blocks) {
      std::uint64_t tag;
      std::int64_t result;
      Buf<std::uint8_t> got = ring.collect(tag, result);
      bool same = result == static_cast<std::int64_t>(block);
      for(std::size_t i = 0; same && i < block; ++i) same = got.data()[i] == static_cast<std::uint8_t>(tag + i * 7);
      right[tag] = same;
      ++seen;
    }
    std::move(ring).wait();
    std::size_t good = 0;
    for(bool r : right) good += r;
    CHECK(good == blocks);
  }
  close(fd);
}

// A read of an empty pipe waits in the kernel, not in a thread; the write submitted after it finishes first,
// and the read then carries the bytes the write put in.
static void test_completion_order_on_a_pipe() {
  int p[2];
  CHECK(pipe(p) == 0);
  Ring ring(4);
  ring.submit(Op::read, p[0], Buf<std::uint8_t>(16), 16, 0, 1);
  Buf<std::uint8_t> hello(5);
  std::memcpy(hello.data(), "hello", 5);
  ring.submit(Op::write, p[1], std::move(hello), 5, 0, 2);
  std::uint64_t first, second;
  std::int64_t wrote, read;
  Buf<std::uint8_t> a = ring.collect(first, wrote);
  Buf<std::uint8_t> b = ring.collect(second, read);
  CHECK(first == 2 && wrote == 5 && second == 1 && read == 5);
  CHECK(std::memcmp(b.data(), "hello", 5) == 0 && a.size() == 5);
  std::move(ring).wait();
  close(p[0]);
  close(p[1]);
}

// send and recv over a socket pair, and a count below the Buf's size: only that prefix goes out.
static void test_send_and_recv() {
  int s[2];
  CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, s) == 0);
  Ring ring(2);
  Buf<std::uint8_t> msg = filled(100, 3);
  ring.submit(Op::send, s[0], std::move(msg), 40, 0, 7);
  ring.submit(Op::recv, s[1], Buf<std::uint8_t>(100), 100, 0, 8);
  std::int64_t sent = -1, got = -1;
  Buf<std::uint8_t> received;
  for(int k = 0; k < 2; ++k) {
    std::uint64_t tag;
    std::int64_t result;
    Buf<std::uint8_t> back = ring.collect(tag, result);
    if(tag == 7) sent = result;
    else received = std::move(back), got = result;
  }
  CHECK(sent == 40 && got == 40 && received.data()[39] == static_cast<std::uint8_t>(3 + 39 * 7));
  std::move(ring).wait();
  close(s[0]);
  close(s[1]);
}

// accept runs in the kernel while this thread connects; its result is the new descriptor.
static void test_accept() {
  const int listener = socket(AF_INET, SOCK_STREAM, 0);
  sockaddr_in at{};
  at.sin_family = AF_INET;
  at.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  socklen_t size = sizeof at;
  CHECK(bind(listener, reinterpret_cast<sockaddr*>(&at), sizeof at) == 0 && listen(listener, 4) == 0);
  CHECK(getsockname(listener, reinterpret_cast<sockaddr*>(&at), &size) == 0);
  Ring ring(1);
  ring.submit(Op::accept, listener, Buf<std::uint8_t>(), 0, 0, 9);
  const int client = socket(AF_INET, SOCK_STREAM, 0);
  CHECK(connect(client, reinterpret_cast<sockaddr*>(&at), sizeof at) == 0);
  std::uint64_t tag;
  std::int64_t result;
  (void)ring.collect(tag, result);
  CHECK(tag == 9 && result >= 0);
  if(result >= 0) close(static_cast<int>(result));
  std::move(ring).wait();
  close(client);
  close(listener);
}

// A failure is a value: reading a closed descriptor completes with -EBADF, and the Buf still comes back.
static void test_an_error_is_a_result() {
  Ring ring(1);
  ring.submit(Op::read, 12345, Buf<std::uint8_t>(8), 8, 0, 3);
  std::uint64_t tag;
  std::int64_t result;
  Buf<std::uint8_t> back = ring.collect(tag, result);
  CHECK(tag == 3 && result == -EBADF && back.size() == 8);
  std::move(ring).wait();
}

// wait() collects what nobody did before it releases anything, so an uncollected read still lands in storage
// the ring owns; this is the case that would be a use after free if wait released first.
static void test_wait_drains_before_it_releases() {
  int p[2];
  CHECK(pipe(p) == 0);
  CHECK(write(p[1], "abc", 3) == 3);
  Ring ring(2);
  ring.submit(Op::read, p[0], Buf<std::uint8_t>(3), 3, 0, 1);
  std::move(ring).wait();
  close(p[0]);
  close(p[1]);
}

// A timeout bounds how long a program waits on the kernel: the receive below has nobody to hear from, the
// timeout answers first with -ETIME, and cancelling the receive brings it back with -ECANCELED and its Buf.
static void test_a_timeout_and_a_cancel() {
  int s[2];
  CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, s) == 0);
  Ring ring(2);
  ring.submit(Op::recv, s[1], Buf<std::uint8_t>(16), 16, 0, 1);
  ring.submit(Op::timeout, -1, Buf<std::uint8_t>(), 0, 20000000, 2);  // twenty milliseconds
  std::uint64_t tag;
  std::int64_t result;
  (void)ring.collect(tag, result);
  CHECK(tag == 2 && result == -ETIME);
  ring.cancel(1);
  ring.cancel(1);  // asked twice: one request reaches the kernel
  Buf<std::uint8_t> back = ring.collect(tag, result);
  CHECK(tag == 1 && result == -ECANCELED && back.size() == 16);
  ring.cancel(1);  // nothing under that tag is in flight any more: nothing happens
  ring.submit(Op::send, s[0], Buf<std::uint8_t>(3), 3, 0, 1);  // the berth again, a new operation
  (void)ring.collect(tag, result);
  CHECK(tag == 1 && result == 3);  // an earlier cancel of the same berth did not reach it
  std::move(ring).wait();
  close(s[0]);
  close(s[1]);
}

// Each case leaves through _Exit inside its own scope, so a guard that failed to fire cannot be rescued by the
// destructor of an unwaited ring, which traps too.
static void survived(const char* name) {
  std::fprintf(stderr, "death case %s did not abort\n", name);
  std::_Exit(3);
}

static int death(const char* name) {
  if(!std::strcmp(name, "full")) {
    Ring ring(1);
    int p[2];
    if(pipe(p) != 0) return 2;
    ring.submit(Op::read, p[0], Buf<std::uint8_t>(1), 1, 0, 0);
    ring.submit(Op::read, p[0], Buf<std::uint8_t>(1), 1, 0, 1);  // one berth, two operations
    survived(name);
  } else if(!std::strcmp(name, "empty")) {
    Ring ring(2);
    std::uint64_t tag;
    std::int64_t result;
    (void)ring.collect(tag, result);  // nothing is in flight
    survived(name);
  } else if(!std::strcmp(name, "past_the_storage")) {
    Ring ring(1);
    ring.submit(Op::write, 1, Buf<std::uint8_t>(4), 5, 0, 0);  // five bytes of a four-byte Buf
    survived(name);
  } else if(!std::strcmp(name, "no_capacity")) {
    Ring ring(0);
    survived(name);
  }
  std::fprintf(stderr, "unknown death case %s\n", name);
  return 2;
}

int main(int argc, char** argv) {
  if(argc > 1 && !std::strcmp(argv[1], "--list")) {
    std::puts("full empty past_the_storage no_capacity");
    return 0;
  }
  if(argc > 1) return death(argv[1]);
  test_a_file_round_trip();
  test_completion_order_on_a_pipe();
  test_send_and_recv();
  test_accept();
  test_an_error_is_a_result();
  test_wait_drains_before_it_releases();
  test_a_timeout_and_a_cancel();
  if(failures) {
    std::fprintf(stderr, "%d of %ld checks failed\n", failures, checked);
    return 1;
  }
  std::printf("ok after %ld checks\n", checked);
  return 0;
}

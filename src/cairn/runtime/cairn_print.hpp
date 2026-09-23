// CAIRN print and format: every piece a call names is computed, left to right, before one byte is written, so an
// argument whose guard fails aborts with nothing written. print, println, eprint and eprintln write through one
// buffer of PIPE_BUF (4096) bytes on the caller's stack: a line of at most that many bytes leaves in one write(2),
// which a pipe keeps whole. A float is written in the shortest form that reads back to the same value, as
// std::to_chars writes it without a precision: fixed or exponent, whichever is shorter, fixed on a tie. format
// appends to a growable byte record and allocates at most once a call. A write the kernel refuses (a closed pipe,
// a full disk) ends that print where it stopped; nothing traps for it. Host only: a lane or an image never gets here.
#pragma once
#include "cairn_owners.hpp"
#include "cairn_runtime.hpp"
#include <cerrno>
#include <charconv>
#include <cstring>
#include <initializer_list>
#include <unistd.h>
namespace cr::out {
enum class Kind : std::uint8_t { text, sint, uint, boolean, byte, f32, f64 };
struct Piece {
  Kind kind = Kind::text;
  const std::uint8_t* p = nullptr;  // text: its bytes
  std::size_t n = 0;
  std::int64_t s = 0;               // a signed integer
  std::uint64_t u = 0;              // an unsigned integer, a bool, or the byte of a character literal
  float f = 0;
  double d = 0;
};
inline Piece text(const std::uint8_t* p, std::size_t n) noexcept { Piece x; x.p = p; x.n = n; return x; }
inline Piece integer(std::int64_t v) noexcept { Piece x; x.kind = Kind::sint; x.s = v; return x; }
inline Piece integer(std::uint64_t v) noexcept { Piece x; x.kind = Kind::uint; x.u = v; return x; }
inline Piece boolean(bool v) noexcept { Piece x; x.kind = Kind::boolean; x.u = v; return x; }
inline Piece byte(std::uint8_t v) noexcept { Piece x; x.kind = Kind::byte; x.u = v; return x; }
inline Piece real(float v) noexcept { Piece x; x.kind = Kind::f32; x.f = v; return x; }
inline Piece real(double v) noexcept { Piece x; x.kind = Kind::f64; x.d = v; return x; }

constexpr std::size_t WIDEST = 32;    // -2.2250738585072014e-308 is 24 characters, the widest a number renders
constexpr std::size_t BUFFER = 4096;  // PIPE_BUF on Linux: what one write to a pipe keeps whole

// The characters of a piece that is not text, into `out`, which holds WIDEST.
inline std::size_t render(const Piece& x, char* out) noexcept {
  char* end = out + WIDEST;
  switch (x.kind) {
    case Kind::sint: return static_cast<std::size_t>(std::to_chars(out, end, x.s).ptr - out);
    case Kind::uint: return static_cast<std::size_t>(std::to_chars(out, end, x.u).ptr - out);
    case Kind::f32: return static_cast<std::size_t>(std::to_chars(out, end, x.f).ptr - out);
    case Kind::f64: return static_cast<std::size_t>(std::to_chars(out, end, x.d).ptr - out);
    case Kind::boolean: std::memcpy(out, x.u ? "true" : "false", x.u ? 4 : 5); return x.u ? 4 : 5;
    case Kind::byte: out[0] = static_cast<char>(x.u); return 1;
    case Kind::text: break;
  }
  return 0;
}

// Every byte, or false once the kernel refuses one; an interrupted write is written again.
inline bool send(int fd, const char* p, std::size_t n) noexcept {
  while (n) {
    ssize_t k = ::write(fd, p, n);
    if (k < 0 && errno == EINTR) continue;
    if (k <= 0) return false;
    p += k;
    n -= static_cast<std::size_t>(k);
  }
  return true;
}

inline void write(int fd, bool line, std::initializer_list<Piece> pieces) noexcept {
  char buf[BUFFER];
  std::size_t used = 0;
  bool open = true;
  auto flush = [&] {
    open = open && send(fd, buf, used);
    used = 0;
  };
  for (const Piece& x : pieces) {
    if (x.kind != Kind::text) {
      if (BUFFER - used < WIDEST) flush();
      used += render(x, buf + used);
    } else if (x.n <= BUFFER - used) {
      if (x.n) std::memcpy(buf + used, x.p, x.n);
      used += x.n;
    } else if (flush(); x.n <= BUFFER) {
      std::memcpy(buf, x.p, x.n);
      used = x.n;
    } else {
      open = open && send(fd, reinterpret_cast<const char*>(x.p), x.n);
    }
  }
  if (line) {
    if (used == BUFFER) flush();
    buf[used++] = '\n';
  }
  flush();
}

inline std::size_t measure(const Piece& x) noexcept {
  char tmp[WIDEST];
  return x.kind == Kind::text ? x.n : render(x, tmp);
}

// The live bytes are data[0..len]: a len past the carrier is the guard lending that part pays. The record grows as
// std.vec grows, to at least twice its capacity, at least four bytes and at least what this call appends.
template <class N> inline void append(Buf<std::uint8_t>& data, N& len, std::initializer_list<Piece> pieces) noexcept {
  if (len > data.size()) trap();
  std::size_t total = 0, need = 0;
  for (const Piece& x : pieces)
    if (__builtin_add_overflow(total, measure(x), &total)) trap();
  if (__builtin_add_overflow(static_cast<std::size_t>(len), total, &need)) trap();
  if (need > data.size()) {
    std::size_t doubled = data.size() > SIZE_MAX / 2 ? need : data.size() * 2;
    Buf<std::uint8_t> bigger(std::max(need, std::max<std::size_t>(4, doubled)));
    if (len) std::memcpy(bigger.data(), data.data(), static_cast<std::size_t>(len));
    data = std::move(bigger);
  }
  std::uint8_t* at = data.data() + len;
  for (const Piece& x : pieces) {
    if (x.kind == Kind::text) {
      if (x.n) std::memcpy(at, x.p, x.n);
      at += x.n;
    } else {
      char tmp[WIDEST];
      std::size_t k = render(x, tmp);
      std::memcpy(at, tmp, k);
      at += k;
    }
  }
  len = static_cast<N>(need);
}
}  // namespace cr::out

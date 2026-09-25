// CUB's device-wide reduce and scan: the CUDA machine's calls a device `reduce`, `scan` and `compact` run on
// (cairn_exec.hpp's reduce_on, scan_on and compact_on reach them), and the older synchronous reduce, scan and compact
// that wait for the whole device. The lowering includes this header only in a program with a device collector
// (compiler/lower/region_lowering.py): CUB's headers are about half of what nvcc reads and compiles in a device
// build, and a program without a collector builds to the same kernels without them. It is empty outside nvcc, where
// an emulated build's collectors run on cairn_emulate.hpp's machine and a host stand-in brings its own.
#pragma once
#if defined(__CUDACC__)
#include <cub/device/device_reduce.cuh>
#include <cub/device/device_scan.cuh>
#include "cairn_gpu.hpp"
namespace cr::gpu {

template<class In, class Out, class Fold, class T>
void Cuda::reduce(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Fold fold, T identity,
                  Stream s) noexcept {
  check(cub::DeviceReduce::Reduce(temp, bytes, in, out, n, fold, identity, s));
}
template<class In, class Out, class Fold>
void Cuda::inclusive_scan(void* temp, std::size_t& bytes, In in, Out out, Fold fold, std::size_t n,
                          Stream s) noexcept {
  check(cub::DeviceScan::InclusiveScan(temp, bytes, in, out, fold, n, s));
}
template<class In, class Out>
void Cuda::exclusive_sum(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Stream s) noexcept {
  check(cub::DeviceScan::ExclusiveSum(temp, bytes, in, out, n, s));
}

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
}  // namespace cr::gpu
#endif

// Independently written C++ algorithms. Same FFI entry guards as CAIRN.
// Inner indices/arithmetic use the algorithm's invariants, not checked helpers.
#include "../../results/native/cairn_runtime.hpp"
extern "C" void cc_saxpy(std::size_t n,float* out,const float* x,const float* y,float a) noexcept {
  cr::view(out,n); cr::view(x,n); cr::view(y,n);
  cr::disjoint(out,n,x,n); cr::disjoint(out,n,y,n);
  for(std::size_t i=0;i<n;++i) out[i]=a*x[i]+y[i];
}
extern "C" double cc_dot(std::size_t n,const double* x,const double* y) noexcept {
  cr::view(x,n); cr::view(y,n);
  double total=0.0;
  for(std::size_t i=0;i<n;++i) total+=x[i]*y[i];
  return total;
}
extern "C" std::uint64_t cc_sum_wrap(std::size_t n,const std::uint64_t* x) noexcept {
  cr::view(x,n); std::uint64_t total=0;
  for(std::size_t i=0;i<n;++i) total+=x[i];
  return total;
}
extern "C" void cc_prefix(std::size_t n,std::uint64_t* out,const std::uint64_t* x) noexcept {
  cr::view(out,n); cr::view(x,n); cr::disjoint(out,n,x,n);
  std::uint64_t total=0;
  for(std::size_t i=0;i<n;++i) { total+=x[i]; out[i]=total; }
}
extern "C" std::size_t cc_count_gt(std::size_t n,const float* x,float threshold) noexcept {
  cr::view(x,n); std::size_t count=0;
  for(std::size_t i=0;i<n;++i) if(x[i]>threshold) ++count;
  return count;
}
extern "C" void cc_histogram(std::size_t n,std::uint64_t* out,const std::uint8_t* x) noexcept {
  cr::view(out,256); cr::view(x,n); cr::disjoint(out,256,x,n);
  for(std::size_t i=0;i<256;++i) out[i]=0;
  for(std::size_t i=0;i<n;++i) ++out[x[i]];
}
extern "C" std::size_t cc_compact_even(std::size_t n,std::uint64_t* out,const std::uint64_t* x) noexcept {
  cr::view(out,n); cr::view(x,n); cr::disjoint(out,n,x,n);
  std::size_t used=0;
  for(std::size_t i=0;i<n;++i) if((x[i]&1)==0) out[used++]=x[i];
  return used;
}
extern "C" std::size_t cc_lower_bound(std::size_t n,const std::uint64_t* x,std::uint64_t key) noexcept {
  cr::view(x,n); std::size_t lo=0,hi=n;
  while(lo<hi) { auto mid=lo+(hi-lo)/2; if(x[mid]<key) lo=mid+1; else hi=mid; }
  return lo;
}
extern "C" std::uint64_t cc_gcd(std::uint64_t a,std::uint64_t b) noexcept {
  while(b!=0) { auto t=a%b; a=b; b=t; } return a;
}

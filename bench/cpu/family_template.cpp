// A compact, independently authored C++20 family, not 256 copied functions.
// Same 256 mathematical operations. Table entry selection is a different API.
#include "../../results/cairn_runtime.hpp"
#include <array>
#include <utility>
template<std::size_t K>
void gain(std::size_t n,float* out,const float* x) noexcept {
  cr::view(out,n); cr::view(x,n); cr::disjoint(out,n,x,n);
  for(std::size_t i=0;i<n;++i) out[i]=x[i]*float(K);
}
template<std::size_t... I>
auto make_family(std::index_sequence<I...>) {
  return std::array{&gain<I+1>...};
}
static const auto family=make_family(std::make_index_sequence<256>{});
extern "C" void cc_gain(std::size_t k,std::size_t n,float* out,const float* x) noexcept {
  if(k<1 || k>256) cr::trap();
  family[k-1](n,out,x);
}

#include "../results/native.cpp"
#include "../results/wire.cpp"
#include <cassert>
#include <vector>
#include <numeric>
#include <iostream>
int main(){
  std::uint64_t state=7;
  for(std::size_t n=0;n<1024;++n){
    std::vector<std::uint64_t>x(n),y(n,0xDEADBEEF),z(n);
    for(auto& v:x){state=state*6364136223846793005ULL+1442695040888963407ULL;v=state^(state>>31);}
    std::uint64_t total=0;std::size_t pos=0;
    for(auto v:x){total+=v;if(!(v&1))z[pos++]=v;}
    assert(cf_sum_wrap(n,x.data())==total);
    assert(cf_compact_even(n,y.data(),x.data())==pos);
    for(std::size_t j=0;j<pos;++j)assert(y[j]==z[j]);
    for(std::size_t j=pos;j<n;++j)assert(y[j]==0xDEADBEEF);
    cf_prefix(n,y.data(),x.data());
    std::uint64_t acc=0;for(std::size_t j=0;j<n;++j){acc+=x[j];assert(y[j]==acc);}
    std::vector<std::uint8_t>bytes(n);std::uint64_t hist[256]{};
    for(std::size_t j=0;j<n;++j)bytes[j]=static_cast<std::uint8_t>(x[j]);
    cf_histogram(n,hist,bytes.data());
    assert(std::accumulate(hist,hist+256,std::uint64_t(0))==n);
    std::sort(x.begin(),x.end());
    auto key=state;auto it=std::lower_bound(x.begin(),x.end(),key);
    assert(cf_lower_bound(n,x.data(),key)==std::size_t(it-x.begin()));
  }
  for(std::uint64_t i=0;i<1000;++i){
    ct_Packet p{i,static_cast<std::uint32_t>(i),2,3,4,5,i+6,i+7};
    std::uint8_t b[36];cf_encode_Packet(b,p);auto q=cf_decode_Packet(b);
    assert(q.v_sequence==i && q.v_timestamp==i+7 && q.v_flags==3);
  }
  std::cout<<"1024 array lengths and 1000 codec cases passed with ASan+UBSan\n";
}

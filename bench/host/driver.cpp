#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <vector>
using sz=std::size_t; using u64=std::uint64_t;
#define DECL(P) \
extern "C" void P##_saxpy(sz,float*,const float*,const float*,float) noexcept; \
extern "C" double P##_dot(sz,const double*,const double*) noexcept; \
extern "C" u64 P##_sum_wrap(sz,const u64*) noexcept; \
extern "C" void P##_prefix(sz,u64*,const u64*) noexcept; \
extern "C" sz P##_count_gt(sz,const float*,float) noexcept; \
extern "C" void P##_histogram(sz,u64*,const std::uint8_t*) noexcept; \
extern "C" sz P##_compact_even(sz,u64*,const u64*) noexcept; \
extern "C" sz P##_lower_bound(sz,const u64*,u64) noexcept;
DECL(cf) DECL(cc)
volatile u64 sink=0;
struct Data {
  sz n; bool mixed; std::vector<float>x,y,out,ref;
  std::vector<double>dx,dy;
  std::vector<u64>u,sorted,ou,ru;
  std::vector<std::uint8_t>bytes;
  std::array<u64,256> hist{},rh{};
  explicit Data(sz n,bool mixed):n(n),mixed(mixed),x(n),y(n),out(n),ref(n),dx(n),dy(n),u(n),sorted(n),ou(n),ru(n),bytes(n) {
    u64 state=0x9182a356ec713ULL;
    for(sz i=0;i<n;++i) {
      state=state*6364136223846793005ULL+1442695040888963407ULL;
      u[i]=mixed ? state^(state>>29) : state; sorted[i]=i*4; bytes[i]=static_cast<std::uint8_t>(state>>27);
      x[i]=float(int((state>>17)%1024)-512)/8; y[i]=float(int((state>>31)%1024)-512)/8;
      dx[i]=x[i];dy[i]=y[i];
    }
  }
};
void check(bool ok) { if(!ok) { std::cerr<<"baseline validation failed\n"; std::abort(); } }
void validate(Data& d) {
  cf_saxpy(d.n,d.out.data(),d.x.data(),d.y.data(),1.25f);
  cc_saxpy(d.n,d.ref.data(),d.x.data(),d.y.data(),1.25f); check(d.out==d.ref);
  check(cf_dot(d.n,d.dx.data(),d.dy.data())==cc_dot(d.n,d.dx.data(),d.dy.data()));
  check(cf_sum_wrap(d.n,d.u.data())==cc_sum_wrap(d.n,d.u.data()));
  cf_prefix(d.n,d.ou.data(),d.u.data()); cc_prefix(d.n,d.ru.data(),d.u.data()); check(d.ou==d.ru);
  check(cf_count_gt(d.n,d.x.data(),1.0f)==cc_count_gt(d.n,d.x.data(),1.0f));
  cf_histogram(d.n,d.hist.data(),d.bytes.data()); cc_histogram(d.n,d.rh.data(),d.bytes.data()); check(d.hist==d.rh);
  auto a=cf_compact_even(d.n,d.ou.data(),d.u.data()); auto b=cc_compact_even(d.n,d.ru.data(),d.u.data());
  check(a==b && std::equal(d.ou.begin(),d.ou.begin()+a,d.ru.begin()));
  for(sz k=0;k<100;++k) {
    u64 key=(k*7919)%(d.n*4+1);
    auto ref=sz(std::lower_bound(d.sorted.begin(),d.sorted.end(),key)-d.sorted.begin());
    check(cf_lower_bound(d.n,d.sorted.data(),key)==ref && cc_lower_bound(d.n,d.sorted.data(),key)==ref);
  }
}
// Separate objects and no LTO: the driver cannot inline or delete kernel bodies.
[[gnu::noinline]] double time_one(int op,bool cairn,Data& d,sz reps) {
  u64 total=0;
  auto start=std::chrono::steady_clock::now();
  if(op==0) { auto f=cairn?cf_saxpy:cc_saxpy; for(sz r=0;r<reps;++r) f(d.n,d.out.data(),d.x.data(),d.y.data(),1.25f); }
  if(op==1) { auto f=cairn?cf_dot:cc_dot; double a=0; for(sz r=0;r<reps;++r) a+=f(d.n,d.dx.data(),d.dy.data()); total=static_cast<u64>(a<0?-a:a); }
  if(op==2) { auto f=cairn?cf_sum_wrap:cc_sum_wrap; for(sz r=0;r<reps;++r) total+=f(d.n,d.u.data()); }
  if(op==3) { auto f=cairn?cf_prefix:cc_prefix; for(sz r=0;r<reps;++r) f(d.n,d.ou.data(),d.u.data()); }
  if(op==4) { auto f=cairn?cf_count_gt:cc_count_gt; for(sz r=0;r<reps;++r) total+=f(d.n,d.x.data(),1.0f); }
  if(op==5) { auto f=cairn?cf_histogram:cc_histogram; for(sz r=0;r<reps;++r) f(d.n,d.hist.data(),d.bytes.data()); }
  if(op==6) { auto f=cairn?cf_compact_even:cc_compact_even; for(sz r=0;r<reps;++r) total+=f(d.n,d.ou.data(),d.u.data()); }
  if(op==7) { auto f=cairn?cf_lower_bound:cc_lower_bound; for(sz r=0;r<reps;++r) total+=f(d.n,d.sorted.data(),(r*7919)%(d.n*4+1)); }
  auto stop=std::chrono::steady_clock::now();
  sink=total+d.ou[0]+d.hist[0]+static_cast<u64>(d.out[0]<0?-d.out[0]:d.out[0]);
  return std::chrono::duration<double,std::nano>(stop-start).count()/double(reps);
}
int main() {
  const char* names[]={"saxpy","dot","sum_wrap","prefix","count_gt","histogram","compact_even","lower_bound"};
  std::cout<<"kernel,n,pattern,pair,first,reps,cairn_ns,cpp_ns\n"<<std::setprecision(12);
  for(bool mixed : {false,true}) for(sz n: {sz(64),sz(4096),sz(262144)}) {
    Data data(n,mixed); validate(data);
    for(int op=0;op<8;++op) {
      const sz reps=op==7?sz(16384):std::max(sz(8),sz(1048576)/n);
      time_one(op,true,data,8);time_one(op,false,data,8);
      for(int pair=0;pair<11;++pair) {
        double a,b;
        if(pair%2==0) {a=time_one(op,true,data,reps);b=time_one(op,false,data,reps);}
        else {b=time_one(op,false,data,reps);a=time_one(op,true,data,reps);}
        std::cout<<names[op]<<","<<n<<","<<(mixed?"mixed":"alternating")<<","<<pair<<","<<(pair%2==0?"cairn":"cpp")<<","<<reps<<","<<a<<","<<b<<"\n";
      }
    }
  }
}

full run of the preregistered suite, /home/sam/cairn/bench/suite/PREREGISTRATION.md
host Linux x86_64, profile x86-64-v4, 16 lanes, not pinned
compilers g++: g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0, clang++: clang version 21.1.8 (https://github.com/llvm/llvm-project 2078da43e25a4623cab2d0d60decddf709aaea28)
commit a0904898fa7be629df57d927d5a61363470fc406
acceptance: a win is a median ratio of at least win_ratio that holds at every larger size under both compilers, threshold 1.25

libraries
compiler  library  status     link line or reason                                            
--------  -------  ---------  ---------------------------------------------------------------
g++       omp      available  -fopenmp                                                       
g++       tbb      available  -ltbb                                                          
clang++   omp      available  -fopenmp -L/usr/lib/llvm-18/lib -Wl,-rpath,/usr/lib/llvm-18/lib
clang++   tbb      available  -ltbb                                                          

== saxpy_f32 ==
claim: ratio; in because two flops over twelve bytes, so a large region is bound by memory bandwidth

safety boundaries
arm                      entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-----------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)          5      0        0           0           -               -                     
cairn|guarded|clang++    5      0        0           0           yes             present               
cairn|guarded|g++        5      0        0           0           yes             present               
omp|guarded|clang++      5      3        0           0           no              present               
omp|guarded|g++          5      3        0           0           no              present               
omp|matched|clang++      5      0        0           0           yes             present               
omp|matched|g++          5      0        0           0           yes             present               
omp|unguarded|clang++    0      0        0           0           no              absent                
omp|unguarded|g++        0      0        0           0           no              absent                
plain|guarded|clang++    5      3        0           0           no              present               
plain|guarded|g++        5      3        0           0           no              present               
plain|matched|clang++    5      0        0           0           yes             present               
plain|matched|g++        5      0        0           0           yes             present               
plain|unguarded|clang++  0      0        0           0           no              absent                
plain|unguarded|g++      0      0        0           0           no              absent                
tbb|guarded|clang++      5      3        0           0           no              present               
tbb|guarded|g++          5      3        0           0           no              present               
tbb|matched|clang++      5      0        0           0           yes             present               
tbb|matched|g++          5      0        0           0           yes             present               
tbb|unguarded|clang++    0      0        0           0           no              absent                
tbb|unguarded|g++        0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000043                      0.050300                          0.002468                     0.047092                          0.001996                     0.041322                            0.002908                       0.000040                      0.000040                      0.000044                        0.000106                          0.007325                     0.000087                          0.007790                     0.000099                            0.007847                     
10000      0.000907                      0.045638                          0.002827                     0.049059                          0.002037                     0.110019                            0.002004                       0.001119                      0.000984                      0.000969                        0.000947                          0.011307                     0.001034                          0.009980                     0.001020                            0.010074                     
100000     0.008578                      0.038385                          0.003881                     0.017787                          0.004069                     0.040126                            0.003610                       0.010936                      0.012560                      0.013492                        0.005220                          0.012007                     0.007063                          0.013830                     0.005816                            0.013371                     
1000000    0.070096                      0.058022                          0.024841                     0.093657                          0.025428                     0.169270                            0.026286                       0.125237                      0.153364                      0.119481                        0.034665                          0.035575                     0.027172                          0.028871                     0.030451                            0.032796                     
10000000   1.617058                      1.688330                          2.025341                     2.276323                          4.153818                     3.054234                            2.697440                       3.010017                      4.963680                      3.269382                        1.697457                          1.479128                     1.648882                          1.582741                     1.588033                            1.894826                     
100000000  32.656898                     37.764808                         36.749320                    36.978498                         37.757947                    35.212327                           34.208018                      51.290037                     51.803918                     49.591142                       31.590522                         32.240396                    31.683875                         31.962861                    31.956808                           31.843533                    

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/cairn_claim_on_demand  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------------  ---------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000041                      0.092822                          0.058282                           0.076081                     0.053645                          0.051011                           0.075705                     0.052748                            0.038725                             0.029432                       0.000045                      0.000040                      0.000042                        0.000666                          0.008423                     0.000083                          0.008544                     0.000082                            0.007615                     
10000      0.000937                      0.085506                          0.092806                           0.080690                     0.062076                          0.071619                           0.035998                     0.079325                            0.073386                             0.027331                       0.000911                      0.000939                      0.001018                        0.005875                          0.014965                     0.001153                          0.012180                     0.001007                            0.009229                     
100000     0.007940                      0.100370                          0.071720                           0.077178                     0.106040                          0.085533                           0.061486                     0.006166                            0.006696                             0.003218                       0.012544                      0.012215                      0.011689                        0.020215                          0.032529                     0.005979                          0.012420                     0.006810                            0.011890                     
1000000    0.030341                      0.135119                          0.187322                           0.191608                     0.218927                          0.224480                           0.093443                     0.147418                            0.027718                             0.152713                       0.150073                      0.148008                      0.126697                        0.113185                          0.118340                     0.032414                          0.060958                     0.026586                            0.030304                     
10000000   1.851737                      2.265278                          1.764295                           5.270058                     2.082028                          2.359198                           8.071342                     1.554850                            1.729076                             1.749157                       4.432613                      3.678090                      3.011385                        1.935317                          1.841430                     1.710870                          1.470471                     1.524559                            2.097944                     
100000000  32.387100                     35.411449                         36.007880                          32.836854                    33.182312                         33.595305                          33.670034                    33.489204                           35.644724                            34.563699                      51.348917                     52.587406                     54.652819                       32.898317                         32.483722                    33.418516                         31.979493                    32.444261                           33.591856                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict          
-----------------------------------  ------------------  ------------------  -----------------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded         
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded         
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded         
omp/matched/cairn_claim_assigned     1.13                1.02                level            
omp/matched/cairn_claim_on_demand    -                   1.04                incomplete       
omp/matched/library_default          1.16                1.04                level            
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded         
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded         
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded         
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded         
plain/matched/not_applicable         1.59                1.62                win from n=100000
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded         
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded         
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded         
tbb/matched/cairn_claim_assigned     0.97                1.03                level            
tbb/matched/library_default          0.98                0.99                level            
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded         
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded         

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   0.932         
clang++   omp    library_default        0.931         
clang++   plain  not_applicable         0.967         
clang++   tbb    cairn_claim_assigned   1.012         
clang++   tbb    library_default        0.988         
g++       omp    cairn_claim_assigned   0.946         
g++       omp    cairn_claim_on_demand  0.990         
g++       omp    library_default        1.053         
g++       plain  not_applicable         1.064         
g++       tbb    cairn_claim_assigned   0.986         
g++       tbb    library_default        1.034         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0058    0.0487    1.1580  
clang++   omp/guarded/cairn_claim_assigned     120.3458  58.9505   53.4896 
clang++   omp/guarded/library_default          2.7252    2.1777    4.7334  
clang++   omp/matched/cairn_claim_assigned     81.2042   56.0061   101.2731
clang++   omp/matched/library_default          31.1565   64.6635   11.3966 
clang++   omp/unguarded/cairn_claim_assigned   59.6302   16.7968   76.2483 
clang++   omp/unguarded/library_default        243.4994  1.8957    2.0010  
clang++   plain/guarded/not_applicable         0.0044    0.0585    1.4658  
clang++   plain/matched/not_applicable         0.0067    0.0525    1.6830  
clang++   plain/unguarded/not_applicable       0.0033    0.0552    1.5899  
clang++   tbb/guarded/cairn_claim_assigned     0.0514    0.1034    2.3003  
clang++   tbb/guarded/library_default          2.9854    6.5263    11.2416 
clang++   tbb/matched/cairn_claim_assigned     0.0701    0.0958    2.4643  
clang++   tbb/matched/library_default          4.0821    6.5850    9.6698  
clang++   tbb/unguarded/cairn_claim_assigned   0.0419    0.0884    2.4419  
clang++   tbb/unguarded/library_default        2.6941    7.0748    8.5473  
g++       cairn/guarded/not_applicable         0.0051    0.0442    1.2613  
g++       omp/guarded/cairn_claim_assigned     28.9363   95.2387   82.0796 
g++       omp/guarded/cairn_claim_on_demand    2.1314    2.3372    66.3445 
g++       omp/guarded/library_default          93.9023   61.5554   22.7736 
g++       omp/matched/cairn_claim_assigned     49.4806   68.4754   44.8541 
g++       omp/matched/cairn_claim_on_demand    11.5937   53.9626   162.6777
g++       omp/matched/library_default          81.9860   152.1645  204.6596
g++       omp/unguarded/cairn_claim_assigned   86.0080   51.1441   99.4888 
g++       omp/unguarded/cairn_claim_on_demand  2.6306    161.5984  178.6978
g++       omp/unguarded/library_default        115.5861  52.4473   27.5924 
g++       plain/guarded/not_applicable         0.0041    0.0430    1.8661  
g++       plain/matched/not_applicable         0.0046    0.1112    1.8899  
g++       plain/unguarded/not_applicable       0.0032    0.0545    1.7602  
g++       tbb/guarded/cairn_claim_assigned     0.1574    0.7018    10.6979 
g++       tbb/guarded/library_default          3.2292    9.6753    10.5735 
g++       tbb/matched/cairn_claim_assigned     0.0449    0.0864    2.6810  
g++       tbb/matched/library_default          2.4877    8.8584    15.4996 
g++       tbb/unguarded/cairn_claim_assigned   0.0408    0.0831    2.7181  
g++       tbb/unguarded/library_default        3.0776    8.2776    9.9052  

== mixed_u64 ==
claim: ratio; in because a dependent integer mix in registers, so a large region is bound by the cores

safety boundaries
arm                      entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-----------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)          3      0        0           0           -               -                     
cairn|guarded|clang++    3      0        0           0           yes             present               
cairn|guarded|g++        3      0        0           0           yes             present               
omp|guarded|clang++      3      2        1           0           no              present               
omp|guarded|g++          3      2        1           0           no              present               
omp|matched|clang++      3      0        0           0           yes             present               
omp|matched|g++          3      0        0           0           yes             present               
omp|unguarded|clang++    0      0        0           0           no              absent                
omp|unguarded|g++        0      0        0           0           no              absent                
plain|guarded|clang++    3      2        1           0           no              present               
plain|guarded|g++        3      2        1           0           no              present               
plain|matched|clang++    3      0        0           0           yes             present               
plain|matched|g++        3      0        0           0           yes             present               
plain|unguarded|clang++  0      0        0           0           no              absent                
plain|unguarded|g++      0      0        0           0           no              absent                
tbb|guarded|clang++      3      2        1           0           no              present               
tbb|guarded|g++          3      2        1           0           no              present               
tbb|matched|clang++      3      0        0           0           yes             present               
tbb|matched|g++          3      0        0           0           yes             present               
tbb|unguarded|clang++    0      0        0           0           no              absent                
tbb|unguarded|g++        0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000613                      0.051646                          0.011922                     0.085085                          0.003655                     0.050741                            0.039407                       0.000686                      0.000616                      0.000654                        0.002867                          0.007661                     0.002993                          0.009528                     0.002800                            0.008080                     
10000      0.006446                      0.183164                          0.063585                     0.221265                          0.076884                     0.189239                            0.083215                       0.006895                      0.006986                      0.006335                        0.030613                          0.016305                     0.029646                          0.013410                     0.027969                            0.011611                     
100000     0.024062                      0.332101                          0.202380                     0.284689                          0.290093                     0.241257                            0.254086                       0.065191                      0.067199                      0.065734                        0.102767                          0.092288                     0.079895                          0.023726                     0.104512                            0.024971                     
1000000    0.127251                      1.267028                          0.911793                     1.317079                          1.140612                     1.508102                            1.485117                       0.634645                      0.638176                      0.716548                        0.555344                          0.476346                     0.525196                          0.129537                     0.497624                            0.144554                     
10000000   2.962911                      8.177281                          9.390755                     7.101613                          8.693524                     7.227927                            6.415937                       7.576143                      7.902918                      7.638191                        4.805442                          4.339496                     4.416477                          3.209860                     4.423813                            3.088169                     
100000000  46.671739                     68.930225                         63.729308                    64.354452                         70.150112                    63.959640                           60.222864                      80.197677                     83.981116                     82.960740                       54.926713                         50.410076                    51.617082                         47.055225                    51.090287                           47.081501                    

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/cairn_claim_on_demand  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------------  ---------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000569                      0.091754                          0.129149                           0.063297                     0.172276                          0.008350                           0.073507                     0.068349                            0.021105                             0.019648                       0.000475                      0.000474                      0.000475                        0.003505                          0.007453                     0.002826                          0.008348                     0.002986                            0.007846                     
10000      0.004965                      0.118313                          0.132116                           0.086105                     0.130065                          0.072347                           0.083297                     0.080267                            0.071557                             0.036874                       0.004962                      0.005030                      0.005149                        0.033235                          0.015652                     0.031274                          0.010106                     0.028982                            0.010462                     
100000     0.019143                      0.125453                          0.147966                           0.159291                     0.150275                          0.024983                           0.086673                     0.117318                            0.036269                             0.089963                       0.052754                      0.058230                      0.056695                        0.107193                          0.086565                     0.092746                          0.022013                     0.086146                            0.027238                     
1000000    0.091960                      0.811407                          0.627176                           0.622629                     0.212567                          0.176412                           0.219693                     0.128391                            0.205230                             0.221020                       0.500671                      0.530887                      0.531766                        0.657120                          0.451225                     0.498618                          0.094172                     0.583243                            0.115143                     
10000000   2.635561                      5.787354                          5.580731                           9.379620                     8.563495                          3.565603                           3.580878                     3.151519                            3.134065                             7.907870                       7.902024                      6.125389                      6.921789                        5.182334                          4.367198                     4.780253                          3.210844                     5.135502                            2.974279                     
100000000  46.454955                     58.873183                         55.024572                          54.963678                    49.130865                         47.883006                          48.969306                    49.940285                           48.478462                            49.119427                      72.068981                     71.790254                     72.183726                       55.300491                         49.399994                    51.564186                         47.812641                    51.912093                           46.401106                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict          
-----------------------------------  ------------------  ------------------  -----------------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded         
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded         
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded         
omp/matched/cairn_claim_assigned     1.38                1.06                level            
omp/matched/cairn_claim_on_demand    -                   1.03                incomplete       
omp/matched/library_default          1.50                1.05                level            
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded         
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded         
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded         
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded         
plain/matched/not_applicable         1.80                1.55                win from n=100000
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded         
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded         
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded         
tbb/matched/cairn_claim_assigned     1.11                1.11                level            
tbb/matched/library_default          1.01                1.03                level            
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded         
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded         

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   0.928         
clang++   omp    library_default        0.945         
clang++   plain  not_applicable         1.034         
clang++   tbb    cairn_claim_assigned   0.930         
clang++   tbb    library_default        0.934         
g++       omp    cairn_claim_assigned   0.848         
g++       omp    cairn_claim_on_demand  0.881         
g++       omp    library_default        0.894         
g++       plain  not_applicable         1.002         
g++       tbb    cairn_claim_assigned   0.939         
g++       tbb    library_default        0.939         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0382    0.6282    6.4167  
clang++   omp/guarded/cairn_claim_assigned     2.4690    59.3481   208.9624
clang++   omp/guarded/library_default          2.6935    19.5488   77.9494 
clang++   omp/matched/cairn_claim_assigned     40.3498   65.0476   185.9115
clang++   omp/matched/library_default          38.3035   120.1594  115.4005
clang++   omp/unguarded/cairn_claim_assigned   24.9410   74.5941   161.6516
clang++   omp/unguarded/library_default        4.8416    18.8882   127.2578
clang++   plain/guarded/not_applicable         0.0445    0.6899    10.4877 
clang++   plain/matched/not_applicable         0.0387    0.7678    11.1769 
clang++   plain/unguarded/not_applicable       0.0380    0.6580    10.5822 
clang++   tbb/guarded/cairn_claim_assigned     0.2173    2.8850    54.5650 
clang++   tbb/guarded/library_default          2.5126    6.8773    19.2553 
clang++   tbb/matched/cairn_claim_assigned     0.2184    3.9219    47.0769 
clang++   tbb/matched/library_default          2.5005    7.7326    12.4592 
clang++   tbb/unguarded/cairn_claim_assigned   0.2150    3.2384    54.7254 
clang++   tbb/unguarded/library_default        2.8184    11.8167   15.8758 
g++       cairn/guarded/not_applicable         0.0304    0.4804    4.9164  
g++       omp/guarded/cairn_claim_assigned     45.4252   66.2360   143.5267
g++       omp/guarded/cairn_claim_on_demand    6.4411    55.3353   110.7143
g++       omp/guarded/library_default          46.1331   79.9004   80.9577 
g++       omp/matched/cairn_claim_assigned     133.4020  145.5508  120.1010
g++       omp/matched/cairn_claim_on_demand    15.2346   7.8751    37.7259 
g++       omp/matched/library_default          83.4916   25.4668   27.9223 
g++       omp/unguarded/cairn_claim_assigned   3.3259    168.0557  199.5225
g++       omp/unguarded/cairn_claim_on_demand  2.1345    9.0642    48.8224 
g++       omp/unguarded/library_default        75.1610   84.0319   56.6104 
g++       plain/guarded/not_applicable         0.0368    0.5027    9.3791  
g++       plain/matched/not_applicable         0.0295    0.4669    9.9807  
g++       plain/unguarded/not_applicable       0.0297    0.4898    10.7298 
g++       tbb/guarded/cairn_claim_assigned     0.2232    3.7403    56.8081 
g++       tbb/guarded/library_default          2.5474    7.1001    20.6607 
g++       tbb/matched/cairn_claim_assigned     0.2361    3.1459    47.4836 
g++       tbb/matched/library_default          2.5238    10.1401   12.9155 
g++       tbb/unguarded/cairn_claim_assigned   0.2261    3.0772    49.1174 
g++       tbb/unguarded/library_default        2.5048    8.3586    9.1953  

== sum_u64_wrap ==
claim: ratio; in because wrapping addition is order independent, so a reassociating baseline is the same function

safety boundaries
arm                           entry  element  arithmetic  conversion  equal to cairn  boundary in the object
----------------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)               1      0        0           0           -               -                     
cairn_atomic|guarded|clang++  1      0        0           0           yes             present               
cairn_atomic|guarded|g++      1      0        0           0           yes             present               
cairn_pool|guarded|clang++    1      0        0           0           yes             present               
cairn_pool|guarded|g++        1      0        0           0           yes             present               
cairn|guarded|clang++         1      0        0           0           yes             present               
cairn|guarded|g++             1      0        0           0           yes             present               
omp|guarded|clang++           1      1        0           0           no              present               
omp|guarded|g++               1      1        0           0           no              present               
omp|matched|clang++           1      0        0           0           yes             present               
omp|matched|g++               1      0        0           0           yes             present               
omp|unguarded|clang++         0      0        0           0           no              absent                
omp|unguarded|g++             0      0        0           0           no              absent                
plain|guarded|clang++         1      1        0           0           no              present               
plain|guarded|g++             1      1        0           0           no              present               
plain|matched|clang++         1      0        0           0           yes             present               
plain|matched|g++             1      0        0           0           yes             present               
plain|unguarded|clang++       0      0        0           0           no              absent                
plain|unguarded|g++           0      0        0           0           no              absent                
tbb|guarded|clang++           1      1        0           0           no              present               
tbb|guarded|g++               1      1        0           0           no              present               
tbb|matched|clang++           1      0        0           0           yes             present               
tbb|matched|g++               1      0        0           0           yes             present               
tbb|unguarded|clang++         0      0        0           0           no              absent                
tbb|unguarded|g++             0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  cairn_atomic/guarded/not_applicable  cairn_pool/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  -----------------------------------  ---------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000036                      0.001873                             0.000044                           0.024175                          0.010900                     0.037795                          0.047778                     0.041631                            0.017003                       0.000038                      0.000033                      0.000037                        0.000079                          0.008162                     0.000090                          0.008439                     0.000075                            0.007915                     
10000      0.000611                      0.019605                             0.000628                           0.158966                          0.025079                     0.041945                          0.097780                     0.046596                            0.040147                       0.000598                      0.000658                      0.000602                        0.000665                          0.009142                     0.000773                          0.010500                     0.000783                            0.011163                     
100000     0.007350                      1.046499                             0.009899                           0.074418                          0.029181                     0.033457                          0.019487                     0.054232                            0.026696                       0.007578                      0.006439                      0.007263                        0.004234                          0.009592                     0.003979                          0.013246                     0.004226                            0.012302                     
1000000    0.085933                      10.670741                            0.021246                           0.110074                          0.077719                     0.127436                          0.020713                     0.114349                            0.094515                       0.092246                      0.071091                      0.070416                        0.018620                          0.024524                     0.019865                          0.040805                     0.019892                            0.036499                     
10000000   1.397119                      107.420343                           0.283101                           5.410968                          0.311118                     0.343491                          0.409779                     3.898377                            0.381245                       1.807089                      1.207072                      2.112195                        0.246887                          0.197129                     0.383613                          0.218168                     0.226953                            0.262215                     
100000000  25.344785                     1070.723753                          16.470790                          27.447583                         22.793720                    23.816783                         23.194557                    20.585498                           19.928560                      28.155796                     28.839270                     28.500355                       16.161883                         16.058955                    16.765399                         16.244032                    16.946380                           16.833974                    

median milliseconds, g++
n          cairn/guarded/not_applicable  cairn_atomic/guarded/not_applicable  cairn_pool/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/cairn_claim_on_demand  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  -----------------------------------  ---------------------------------  --------------------------------  ---------------------------------  ---------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000042                      0.001640                             0.000089                           0.041579                          0.040433                           0.003559                     0.013030                          0.007609                           0.055381                     0.020062                            0.059901                             0.030169                       0.000049                      0.000043                      0.000041                        0.000272                          0.008325                     0.000091                          0.009088                     0.000083                            0.008414                     
10000      0.000597                      0.019431                             0.000867                           0.111505                          0.052182                           0.047216                     0.042501                          0.073839                           0.013043                     0.043406                            0.014900                             0.011459                       0.000614                      0.000661                      0.000630                        0.002593                          0.012596                     0.000857                          0.008912                     0.000770                            0.010352                     
100000     0.006525                      0.991390                             0.010837                           0.054933                          0.133195                           0.235330                     0.014089                          0.040154                           0.003331                     0.014961                            0.011377                             0.003708                       0.006377                      0.006610                      0.006331                        0.011511                          0.023312                     0.004712                          0.015348                     0.004793                            0.012587                     
1000000    0.074142                      10.191647                            0.028834                           0.306744                          0.042149                           0.336356                     0.109612                          0.022111                           0.016713                     0.014350                            0.019980                             0.171135                       0.075398                      0.073801                      0.083251                        0.077637                          0.060317                     0.017332                          0.032228                     0.023670                            0.027939                     
10000000   1.620900                      105.738636                           0.227351                           1.548628                          1.070036                           0.785910                     0.265438                          0.401925                           0.413343                     0.530571                            0.336780                             0.451779                       1.717980                      2.482393                      2.027392                        0.504517                          0.463757                     0.239592                          0.193100                     0.234848                            0.156119                     
100000000  29.342130                     1064.389826                          16.472673                          18.116521                         17.279232                          17.731576                    17.069525                         17.127596                          18.445293                    17.770040                           16.591820                            16.964721                      30.586540                     31.489232                     32.609265                       15.931404                         16.906758                    16.459196                         16.948493                    16.494462                           16.249583                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict   
-----------------------------------  ------------------  ------------------  ----------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
omp/matched/cairn_claim_assigned     0.94                0.58                level     
omp/matched/cairn_claim_on_demand    -                   0.58                incomplete
omp/matched/library_default          0.92                0.63                level     
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded  
plain/matched/not_applicable         1.14                1.07                level     
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
tbb/matched/cairn_claim_assigned     0.66                0.56                loss      
tbb/matched/library_default          0.64                0.58                loss      
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  

ratio of baseline time to cairn_atomic time, above one favours cairn_atomic; equal boundaries only
column                               clang++             g++                 verdict   
-----------------------------------  ------------------  ------------------  ----------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
omp/matched/cairn_claim_assigned     0.02                0.02                loss      
omp/matched/cairn_claim_on_demand    -                   0.02                incomplete
omp/matched/library_default          0.02                0.02                loss      
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded  
plain/matched/not_applicable         0.03                0.03                loss      
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
tbb/matched/cairn_claim_assigned     0.02                0.02                loss      
tbb/matched/library_default          0.02                0.02                loss      
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  

ratio of baseline time to cairn_pool time, above one favours cairn_pool; boundaries equal, or more on the cairn side
column                               clang++             g++                 verdict                              
-----------------------------------  ------------------  ------------------  -------------------------------------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded                             
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded                             
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded                             
omp/matched/cairn_claim_assigned     1.45                1.04                level                                
omp/matched/cairn_claim_on_demand    -                   1.04                incomplete                           
omp/matched/library_default          1.41                1.12                level                                
omp/unguarded/cairn_claim_assigned   1.25                1.08                level; cairn checks more             
omp/unguarded/cairn_claim_on_demand  -                   1.01                incomplete; cairn checks more        
omp/unguarded/library_default        1.21                1.03                level; cairn checks more             
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded                             
plain/matched/not_applicable         1.75                1.91                win from n=1000000                   
plain/unguarded/not_applicable       1.73                1.98                win from n=1000000; cairn checks more
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded                             
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded                             
tbb/matched/cairn_claim_assigned     1.02                1.00                level                                
tbb/matched/library_default          0.99                1.03                level                                
tbb/unguarded/cairn_claim_assigned   1.03                1.00                level; cairn checks more             
tbb/unguarded/library_default        1.02                0.99                level; cairn checks more             

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   0.750         
clang++   omp    library_default        0.874         
clang++   plain  not_applicable         1.012         
clang++   tbb    cairn_claim_assigned   1.049         
clang++   tbb    library_default        1.048         
g++       omp    cairn_claim_assigned   0.981         
g++       omp    cairn_claim_on_demand  0.960         
g++       omp    library_default        0.957         
g++       plain  not_applicable         1.066         
g++       tbb    cairn_claim_assigned   1.035         
g++       tbb    library_default        0.961         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024   n=16384 
--------  -----------------------------------  --------  -------  --------
clang++   cairn/guarded/not_applicable         0.0036    0.0339   0.9740  
clang++   cairn_atomic/guarded/not_applicable  0.1069    1.7026   125.8659
clang++   cairn_pool/guarded/not_applicable    0.0067    0.0404   0.7606  
clang++   omp/guarded/cairn_claim_assigned     239.6557  47.6318  91.7991 
clang++   omp/guarded/library_default          96.2926   18.8938  51.7706 
clang++   omp/matched/cairn_claim_assigned     46.0546   40.4423  69.4176 
clang++   omp/matched/library_default          19.0410   6.6466   49.9870 
clang++   omp/unguarded/cairn_claim_assigned   18.4139   52.9184  45.6602 
clang++   omp/unguarded/library_default        89.5857   45.6372  2.9743  
clang++   plain/guarded/not_applicable         0.0033    0.0357   0.9786  
clang++   plain/matched/not_applicable         0.0034    0.0470   1.1161  
clang++   plain/unguarded/not_applicable       0.0051    0.0384   0.9871  
clang++   tbb/guarded/cairn_claim_assigned     0.0428    0.0787   1.5654  
clang++   tbb/guarded/library_default          3.1619    7.8023   8.4767  
clang++   tbb/matched/cairn_claim_assigned     0.0419    0.0735   1.6066  
clang++   tbb/matched/library_default          3.4871    7.4937   8.9938  
clang++   tbb/unguarded/cairn_claim_assigned   0.0427    0.0769   2.0110  
clang++   tbb/unguarded/library_default        3.1016    7.2998   12.0175 
g++       cairn/guarded/not_applicable         0.0038    0.0422   1.0667  
g++       cairn_atomic/guarded/not_applicable  0.1062    1.8796   136.5183
g++       cairn_pool/guarded/not_applicable    0.0072    0.0655   0.8567  
g++       omp/guarded/cairn_claim_assigned     19.6010   76.5406  161.4736
g++       omp/guarded/cairn_claim_on_demand    6.7890    24.8653  39.7393 
g++       omp/guarded/library_default          26.6113   16.9291  11.6109 
g++       omp/matched/cairn_claim_assigned     53.2617   30.0486  40.0314 
g++       omp/matched/cairn_claim_on_demand    54.5289   34.2819  204.2486
g++       omp/matched/library_default          32.4912   46.3765  35.3341 
g++       omp/unguarded/cairn_claim_assigned   60.1071   50.4745  57.2392 
g++       omp/unguarded/cairn_claim_on_demand  51.7737   26.2634  17.6654 
g++       omp/unguarded/library_default        10.5944   50.1916  170.7871
g++       plain/guarded/not_applicable         0.0035    0.0430   1.0950  
g++       plain/matched/not_applicable         0.0036    0.0456   1.0615  
g++       plain/unguarded/not_applicable       0.0025    0.0447   0.9881  
g++       tbb/guarded/cairn_claim_assigned     0.0774    0.5106   7.2210  
g++       tbb/guarded/library_default          2.8923    9.4600   8.7944  
g++       tbb/matched/cairn_claim_assigned     0.0431    0.0796   1.7365  
g++       tbb/matched/library_default          3.2049    6.6203   8.6575  
g++       tbb/unguarded/cairn_claim_assigned   0.0446    0.0823   1.7997  
g++       tbb/unguarded/library_default        3.6607    8.4568   6.5576  

== dot_f64 ==
claim: semantic_difference; in because a strict in-order fold and a reassociating reduction are different functions, not two speeds

safety boundaries
arm                      entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-----------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)          2      0        0           0           -               -                     
cairn|guarded|clang++    2      0        0           0           yes             present               
cairn|guarded|g++        2      0        0           0           yes             present               
omp|guarded|clang++      2      2        0           0           no              present               
omp|guarded|g++          2      2        0           0           no              present               
omp|matched|clang++      2      0        0           0           yes             present               
omp|matched|g++          2      0        0           0           yes             present               
omp|unguarded|clang++    0      0        0           0           no              absent                
omp|unguarded|g++        0      0        0           0           no              absent                
plain|guarded|clang++    2      2        0           0           no              present               
plain|guarded|g++        2      2        0           0           no              present               
plain|matched|clang++    2      0        0           0           yes             present               
plain|matched|g++        2      0        0           0           yes             present               
plain|unguarded|clang++  0      0        0           0           no              absent                
plain|unguarded|g++      0      0        0           0           no              absent                
tbb|guarded|clang++      2      2        0           0           no              present               
tbb|guarded|g++          2      2        0           0           no              present               
tbb|matched|clang++      2      0        0           0           yes             present               
tbb|matched|g++          2      0        0           0           yes             present               
tbb|unguarded|clang++    0      0        0           0           no              absent                
tbb|unguarded|g++        0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build
omp  clang++   did-not-build

did this arm compute the function the CAIRN source names, bit for bit
column                               compiler  at n=64
-----------------------------------  --------  -------
cairn/guarded/not_applicable         g++       yes    
plain/guarded/not_applicable         g++       yes    
plain/unguarded/not_applicable       g++       yes    
plain/matched/not_applicable         g++       yes    
omp/guarded/library_default          g++       yes    
omp/unguarded/library_default        g++       no     
omp/matched/library_default          g++       yes    
omp/guarded/cairn_claim_assigned     g++       yes    
omp/unguarded/cairn_claim_assigned   g++       yes    
omp/matched/cairn_claim_assigned     g++       yes    
omp/guarded/cairn_claim_on_demand    g++       yes    
omp/unguarded/cairn_claim_on_demand  g++       yes    
omp/matched/cairn_claim_on_demand    g++       yes    
tbb/guarded/library_default          g++       yes    
tbb/unguarded/library_default        g++       yes    
tbb/matched/library_default          g++       yes    
tbb/guarded/cairn_claim_assigned     g++       yes    
tbb/unguarded/cairn_claim_assigned   g++       yes    
tbb/matched/cairn_claim_assigned     g++       yes    
cairn/guarded/not_applicable         clang++   yes    
plain/guarded/not_applicable         clang++   yes    
plain/unguarded/not_applicable       clang++   yes    
plain/matched/not_applicable         clang++   yes    
omp/guarded/library_default          clang++   yes    
omp/unguarded/library_default        clang++   yes    
omp/matched/library_default          clang++   yes    
omp/guarded/cairn_claim_assigned     clang++   yes    
omp/unguarded/cairn_claim_assigned   clang++   yes    
omp/matched/cairn_claim_assigned     clang++   yes    
tbb/guarded/library_default          clang++   yes    
tbb/unguarded/library_default        clang++   yes    
tbb/matched/library_default          clang++   yes    
tbb/guarded/cairn_claim_assigned     clang++   yes    
tbb/unguarded/cairn_claim_assigned   clang++   yes    
tbb/matched/cairn_claim_assigned     clang++   yes    

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000726                      0.004273                          0.009760                     0.004530                          0.003195                     0.003957                            0.003478                       0.000640                      0.000637                      0.000746                        0.000716                          0.007885                     0.000799                          0.006878                     0.000722                            0.007779                     
10000      0.007139                      0.034174                          0.004265                     0.027565                          0.003428                     0.029031                            0.003865                       0.006751                      0.006766                      0.007136                        0.007135                          0.009276                     0.007507                          0.009121                     0.007411                            0.007277                     
100000     0.074391                      0.013785                          0.011049                     0.013454                          0.010137                     0.012021                            0.009693                       0.071086                      0.068855                      0.070064                        0.014769                          0.015892                     0.013799                          0.020494                     0.012253                            0.016766                     
1000000    0.726680                      0.128874                          0.090091                     0.069705                          0.064020                     0.070499                            0.064269                       0.703896                      0.725598                      0.686185                        0.087899                          0.100336                     0.074834                          0.079672                     0.074191                            0.070221                     
10000000   8.667089                      2.020646                          5.516120                     2.262001                          3.614748                     2.219351                            2.266467                       7.664629                      7.991607                      8.468852                        2.092075                          2.072423                     1.965108                          1.905936                     1.922829                            1.976850                     
100000000  79.983390                     43.321309                         45.375616                    39.284656                         38.978979                    46.561779                           38.181187                      81.264558                     81.496435                     82.897365                       33.065479                         32.727527                    33.058699                         32.577374                    34.039924                           32.831149                    

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/cairn_claim_on_demand  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------------  ---------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000672                      0.004067                          0.002312                           0.002148                     0.006338                          0.002415                           0.002869                     0.003893                            0.002271                             0.002591                       0.000652                      0.000642                      0.000705                        0.000729                          0.008631                     0.000727                          0.009413                     0.000793                            0.008889                     
10000      0.007867                      0.032699                          0.015598                           0.002496                     0.033214                          0.028738                           0.002520                     0.034398                            0.023146                             0.005781                       0.007430                      0.007122                      0.007626                        0.007104                          0.011731                     0.007512                          0.009040                     0.007489                            0.009853                     
100000     0.071934                      0.015675                          0.010738                           0.007180                     0.011505                          0.010979                           0.008286                     0.010311                            0.010714                             0.087539                       0.074094                      0.071753                      0.074518                        0.016851                          0.020752                     0.012934                          0.014060                     0.012552                            0.023697                     
1000000    0.737758                      0.089718                          0.170669                           0.074661                     0.057673                          0.064119                           0.061236                     0.157023                            0.059110                             0.217784                       0.717698                      0.746207                      0.754494                        0.092428                          0.071008                     0.072462                          0.063452                     0.076187                            0.080438                     
10000000   8.405554                      1.914938                          2.210421                           2.055879                     2.078545                          2.052492                           1.798502                     2.052255                            1.884459                             7.972416                       8.579767                      8.179056                      8.368170                        1.819100                          2.328695                     1.795908                          2.188399                     1.990373                            1.863937                     
100000000  83.732278                     34.719432                         34.487204                          35.751812                    34.519645                         35.390362                          35.323343                    33.801249                           34.957119                            34.492062                      86.787690                     87.667246                     84.956204                       33.663296                         33.115146                    33.947671                         33.695702                    33.371555                           34.280077                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict   
-----------------------------------  ------------------  ------------------  ----------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
omp/matched/cairn_claim_assigned     0.49                0.41                loss      
omp/matched/cairn_claim_on_demand    -                   0.42                incomplete
omp/matched/library_default          0.49                0.42                loss      
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded  
plain/matched/not_applicable         1.02                1.05                level     
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
tbb/matched/cairn_claim_assigned     0.41                0.41                loss      
tbb/matched/library_default          0.41                0.40                loss      
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   1.075         
clang++   omp    library_default        0.841         
clang++   plain  not_applicable         1.020         
clang++   tbb    cairn_claim_assigned   1.029         
clang++   tbb    library_default        1.003         
g++       omp    cairn_claim_assigned   0.974         
g++       omp    cairn_claim_on_demand  1.014         
g++       omp    library_default        0.965         
g++       plain  not_applicable         0.979         
g++       tbb    cairn_claim_assigned   0.991         
g++       tbb    library_default        1.035         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0300    0.6592    11.2307 
clang++   omp/guarded/cairn_claim_assigned     130.1763  3.9996    29.1915 
clang++   omp/guarded/library_default          3.3407    3.8326    4.8318  
clang++   omp/matched/cairn_claim_assigned     3.4915    3.6869    12.8736 
clang++   omp/matched/library_default          3.4468    3.2327    4.3567  
clang++   omp/unguarded/cairn_claim_assigned   3.2953    5.3354    14.1792 
clang++   omp/unguarded/library_default        8.0557    3.6523    4.0683  
clang++   plain/guarded/not_applicable         0.0255    0.6500    11.5854 
clang++   plain/matched/not_applicable         0.0267    0.6694    11.3058 
clang++   plain/unguarded/not_applicable       0.0261    0.7358    11.2060 
clang++   tbb/guarded/cairn_claim_assigned     0.0750    0.9351    11.1493 
clang++   tbb/guarded/library_default          3.1556    6.7898    10.3567 
clang++   tbb/matched/cairn_claim_assigned     0.0772    0.7460    11.5198 
clang++   tbb/matched/library_default          2.8406    8.2284    11.7021 
clang++   tbb/unguarded/cairn_claim_assigned   0.0793    0.8773    17.4701 
clang++   tbb/unguarded/library_default        2.8961    6.5070    8.7042  
g++       cairn/guarded/not_applicable         0.0222    0.6571    11.4751 
g++       omp/guarded/cairn_claim_assigned     2.1346    4.0722    45.9523 
g++       omp/guarded/cairn_claim_on_demand    135.1007  134.2196  119.4051
g++       omp/guarded/library_default          2.1021    2.3962    147.2586
g++       omp/matched/cairn_claim_assigned     2.9570    5.6242    14.3443 
g++       omp/matched/cairn_claim_on_demand    24.6692   32.4882   137.4807
g++       omp/matched/library_default          125.3539  1.9236    2.8708  
g++       omp/unguarded/cairn_claim_assigned   2.2626    3.5914    10.6587 
g++       omp/unguarded/cairn_claim_on_demand  59.7408   128.5897  12.4127 
g++       omp/unguarded/library_default        1.8203    2.1008    2.5167  
g++       plain/guarded/not_applicable         0.0277    0.7393    13.1495 
g++       plain/matched/not_applicable         0.0294    0.6825    11.8426 
g++       plain/unguarded/not_applicable       0.0216    0.6706    12.4008 
g++       tbb/guarded/cairn_claim_assigned     0.0740    0.7602    12.3205 
g++       tbb/guarded/library_default          2.9827    7.8338    10.9434 
g++       tbb/matched/cairn_claim_assigned     0.0762    0.7558    9.9859  
g++       tbb/matched/library_default          2.6229    6.7621    8.4100  
g++       tbb/unguarded/cairn_claim_assigned   0.0766    0.7352    11.2657 
g++       tbb/unguarded/library_default        3.0105    7.7625    8.5859  

== compact_even ==
claim: ratio; in because the certified collector against std::copy_if and a two-pass parallel compaction

safety boundaries
arm                      entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-----------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)          3      0        0           0           -               -                     
cairn|guarded|clang++    3      0        0           0           yes             present               
cairn|guarded|g++        3      0        0           0           yes             present               
omp|guarded|clang++      3      2        0           0           no              present               
omp|guarded|g++          3      2        0           0           no              present               
omp|matched|clang++      3      0        0           0           yes             present               
omp|matched|g++          3      0        0           0           yes             present               
omp|unguarded|clang++    0      0        0           0           no              absent                
omp|unguarded|g++        0      0        0           0           no              absent                
plain|guarded|clang++    3      2        0           0           no              present               
plain|guarded|g++        3      2        0           0           no              present               
plain|matched|clang++    3      0        0           0           yes             present               
plain|matched|g++        3      0        0           0           yes             present               
plain|unguarded|clang++  0      0        0           0           no              absent                
plain|unguarded|g++      0      0        0           0           no              absent                
tbb|guarded|clang++      3      2        0           0           no              present               
tbb|guarded|g++          3      2        0           0           no              present               
tbb|matched|clang++      3      0        0           0           yes             present               
tbb|matched|g++          3      0        0           0           yes             present               
tbb|unguarded|clang++    0      0        0           0           no              absent                
tbb|unguarded|g++        0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000315                      0.007578                          0.011090                     0.005736                          0.004272                     0.006058                            0.006293                       0.000507                      0.000240                      0.000237                        0.000787                          0.003804                     0.000555                          0.003609                     0.000563                            0.003137                     
10000      0.003288                      0.037210                          0.007937                     0.037921                          0.006832                     0.030014                            0.008767                       0.004465                      0.002784                      0.002889                        0.006506                          0.007986                     0.004532                          0.006822                     0.004846                            0.006729                     
100000     0.036461                      0.032830                          0.030240                     0.390195                          0.027455                     0.044781                            0.028416                       0.048729                      0.028856                      0.029292                        0.047747                          0.042967                     0.034988                          0.029047                     0.034780                            0.027742                     
1000000    0.579388                      0.571209                          0.784077                     0.690205                          0.492102                     0.480837                            0.611904                       0.745105                      0.586253                      0.569508                        0.548740                          0.576809                     0.521017                          0.562912                     0.492533                            0.515737                     
10000000   7.656949                      6.725226                          12.445504                    10.178689                         10.652411                    11.544100                           8.432628                       8.480982                      7.267745                      7.732220                        5.362567                          5.741762                     5.327962                          5.753489                     5.560412                            5.694543                     
100000000  81.214982                     87.249498                         92.887145                    96.333872                         86.309708                    87.439206                           88.512055                      84.840534                     80.132484                     80.398787                       78.841100                         79.789495                    78.940121                         78.682476                    79.556192                           78.771606                    

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/cairn_claim_on_demand  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------------  ---------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000447                      0.026880                          0.006715                           0.007542                     0.014558                          0.005291                           0.012005                     0.022894                            0.005049                             0.007528                       0.000658                      0.000401                      0.000436                        0.001218                          0.004272                     0.000738                          0.003296                     0.000625                            0.003384                     
10000      0.004297                      0.059593                          0.058629                           0.010472                     0.046929                          0.038382                           0.009089                     0.048272                            0.041384                             0.012747                       0.006744                      0.004510                      0.004645                        0.010735                          0.010591                     0.007089                          0.008368                     0.005811                            0.009268                     
100000     0.061764                      0.040915                          0.225039                           0.224242                     0.032368                          0.030318                           0.031559                     0.034475                            0.069076                             0.029271                       0.059685                      0.067116                      0.104177                        0.050518                          0.045569                     0.043921                          0.034108                     0.037468                            0.044540                     
1000000    1.185994                      0.521105                          0.552332                           0.650292                     0.488447                          0.484833                           0.497114                     0.756337                            0.457819                             0.375953                       0.797011                      0.945531                      1.060694                        0.640920                          0.663802                     0.578804                          0.519739                     0.570814                            0.623894                     
10000000   12.170738                     6.051720                          6.483890                           7.578009                     6.173930                          6.007354                           6.084529                     6.200166                            6.588208                             6.023741                       9.451887                      10.345829                     11.923331                       6.106787                          6.114645                     5.529636                          5.504788                     5.461151                            5.652085                     
100000000  121.631300                    82.731645                         81.849964                          79.575996                    80.457074                         80.781573                          80.553426                    79.755188                           79.763098                            78.753536                      93.692110                     110.740598                    117.251403                      78.469790                         79.648886                    78.693132                         81.032227                    79.040535                           80.871197                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict   
-----------------------------------  ------------------  ------------------  ----------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
omp/matched/cairn_claim_assigned     1.19                0.66                level     
omp/matched/cairn_claim_on_demand    -                   0.66                incomplete
omp/matched/library_default          1.06                0.66                level     
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded  
plain/matched/not_applicable         0.99                0.91                level     
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
tbb/matched/cairn_claim_assigned     0.97                0.65                level     
tbb/matched/library_default          0.97                0.67                level     
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   1.002         
clang++   omp    library_default        0.953         
clang++   plain  not_applicable         0.948         
clang++   tbb    cairn_claim_assigned   1.009         
clang++   tbb    library_default        0.987         
g++       omp    cairn_claim_assigned   0.964         
g++       omp    cairn_claim_on_demand  0.975         
g++       omp    library_default        0.990         
g++       plain  not_applicable         1.251         
g++       tbb    cairn_claim_assigned   1.007         
g++       tbb    library_default        1.015         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024   n=16384 
--------  -----------------------------------  --------  -------  --------
clang++   cairn/guarded/not_applicable         0.0197    0.3368   6.3378  
clang++   omp/guarded/cairn_claim_assigned     5.4264    10.6220  37.7781 
clang++   omp/guarded/library_default          13.2676   10.4932  9.4536  
clang++   omp/matched/cairn_claim_assigned     92.8977   60.8268  156.8718
clang++   omp/matched/library_default          5.5544    4.2652   9.8705  
clang++   omp/unguarded/cairn_claim_assigned   7.2327    4.6034   14.8425 
clang++   omp/unguarded/library_default        5.4076    14.5099  10.8378 
clang++   plain/guarded/not_applicable         0.0263    0.4516   8.2515  
clang++   plain/matched/not_applicable         0.0151    0.2363   4.2316  
clang++   plain/unguarded/not_applicable       0.0135    0.2775   4.4893  
clang++   tbb/guarded/cairn_claim_assigned     0.1994    0.7815   15.4178 
clang++   tbb/guarded/library_default          2.7389    4.2655   9.7608  
clang++   tbb/matched/cairn_claim_assigned     0.3312    0.5509   10.9354 
clang++   tbb/matched/library_default          2.5992    3.0417   9.2197  
clang++   tbb/unguarded/cairn_claim_assigned   0.1791    0.5530   11.7657 
clang++   tbb/unguarded/library_default        2.3467    3.1617   9.4559  
g++       cairn/guarded/not_applicable         0.0309    0.4079   6.8787  
g++       omp/guarded/cairn_claim_assigned     7.6887    34.1836  46.0080 
g++       omp/guarded/cairn_claim_on_demand    4.8868    5.2685   47.2679 
g++       omp/guarded/library_default          27.5249   11.9842  11.3159 
g++       omp/matched/cairn_claim_assigned     25.0994   30.8625  71.4270 
g++       omp/matched/cairn_claim_on_demand    180.9917  7.2576   33.3844 
g++       omp/matched/library_default          59.3095   56.7164  10.6087 
g++       omp/unguarded/cairn_claim_assigned   7.1408    28.9129  44.1583 
g++       omp/unguarded/cairn_claim_on_demand  4.3590    5.3114   40.3187 
g++       omp/unguarded/library_default        7.0999    11.9292  8.5389  
g++       plain/guarded/not_applicable         0.0608    1.4292   11.6682 
g++       plain/matched/not_applicable         0.0245    0.4212   7.0848  
g++       plain/unguarded/not_applicable       0.0250    0.4067   8.4595  
g++       tbb/guarded/cairn_claim_assigned     0.2012    1.3166   24.0544 
g++       tbb/guarded/library_default          2.4503    5.4060   13.3660 
g++       tbb/matched/cairn_claim_assigned     0.2699    0.7729   16.3335 
g++       tbb/matched/library_default          2.3928    3.2027   9.8407  
g++       tbb/unguarded/cairn_claim_assigned   0.1992    0.6984   13.6496 
g++       tbb/unguarded/library_default        2.4372    3.9612   12.9274 

== histogram_u32 ==
claim: expressiveness; in because the lane rule forbids the shared-bin parallel shape, so the CAIRN arm is sequential

safety boundaries
arm                           entry  element  arithmetic  conversion  equal to cairn  boundary in the object
----------------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)               3      0        0           0           -               -                     
cairn_blocks|guarded|clang++  3      0        7           0           no              present               
cairn_blocks|guarded|g++      3      0        7           0           no              present               
cairn|guarded|clang++         3      0        0           0           yes             present               
cairn|guarded|g++             3      0        0           0           yes             present               
omp|guarded|clang++           3      4        0           1           no              present               
omp|guarded|g++               3      4        0           1           no              present               
omp|matched|clang++           3      0        0           0           yes             present               
omp|matched|g++               3      0        0           0           yes             present               
omp|unguarded|clang++         0      0        0           0           no              absent                
omp|unguarded|g++             0      0        0           0           no              absent                
plain|guarded|clang++         3      4        0           1           no              present               
plain|guarded|g++             3      4        0           1           no              present               
plain|matched|clang++         3      0        0           0           yes             present               
plain|matched|g++             3      0        0           0           yes             present               
plain|unguarded|clang++       0      0        0           0           no              absent                
plain|unguarded|g++           0      0        0           0           no              absent                
tbb|guarded|clang++           3      4        0           1           no              present               
tbb|guarded|g++               3      4        0           1           no              present               
tbb|matched|clang++           3      0        0           0           yes             present               
tbb|matched|g++               3      0        0           0           yes             present               
tbb|unguarded|clang++         0      0        0           0           no              absent                
tbb|unguarded|g++             0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  cairn_blocks/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  -----------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000253                      0.000501                             0.075557                          0.007950                     0.008807                          0.014312                     0.008381                            0.013173                       0.000240                      0.000239                      0.000245                        0.000758                          0.008523                     0.000542                          0.008648                     0.000493                            0.009653                     
10000      0.002408                      0.003005                             0.015881                          0.008379                     0.014025                          0.008400                     0.019076                            0.007599                       0.002390                      0.002381                      0.002794                        0.006074                          0.009387                     0.004738                          0.009340                     0.003768                            0.009973                     
100000     0.026312                      0.024367                             0.015835                          0.014398                     0.014001                          0.012137                     0.020063                            0.011066                       0.027475                      0.024983                      0.028754                        0.018730                          0.018009                     0.018250                          0.013449                     0.011716                            0.013615                     
1000000    0.256193                      0.073965                             0.063758                          0.093290                     0.056255                          0.044602                     0.076933                            0.047453                       0.264628                      0.253630                      0.271545                        0.122804                          0.084228                     0.092262                          0.047914                     0.062133                            0.061538                     
10000000   2.547500                      0.549481                             0.523510                          0.506515                     0.475257                          0.324258                     1.416074                            0.328578                       2.574857                      2.780737                      2.757570                        0.951731                          0.595128                     0.719217                          0.342417                     0.494955                            0.328492                     
100000000  30.524771                     8.460550                             12.992410                         10.745376                    7.281379                          11.717480                    9.434975                            16.377814                      31.932774                     31.221127                     31.990724                       8.682691                          6.690262                     7.680246                          6.870237                     7.355755                            6.637974                     

median milliseconds, g++
n          cairn/guarded/not_applicable  cairn_blocks/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/cairn_claim_on_demand  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  -----------------------------------  --------------------------------  ---------------------------------  ---------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000368                      0.000500                             0.014014                          0.007169                           0.007929                     0.012490                          0.008582                           0.015614                     0.023729                            0.010157                             0.012448                       0.000371                      0.000330                      0.000346                        0.000591                          0.008785                     0.000405                          0.009852                     0.000421                            0.010223                     
10000      0.003538                      0.003155                             0.033138                          0.031963                           0.009328                     0.032037                          0.039029                           0.010172                     0.035958                            0.031086                             0.015887                       0.003853                      0.002828                      0.002982                        0.005463                          0.010972                     0.003836                          0.010858                     0.002944                            0.010414                     
100000     0.037299                      0.027825                             0.016992                          0.017738                           0.014873                     0.013307                          0.017417                           0.019341                     0.013175                            0.013594                             0.011828                       0.033288                      0.031887                      0.037916                        0.020904                          0.019274                     0.012939                          0.019031                     0.012045                            0.016853                     
1000000    0.352195                      0.131410                             0.242415                          0.209936                           0.077479                     0.219418                          0.084808                           0.055345                     0.065370                            0.091522                             0.054707                       0.332906                      0.344936                      0.343110                        0.114046                          0.086881                     0.062633                          0.106912                     0.078908                            0.058384                     
10000000   3.306134                      0.740924                             0.465572                          0.658932                           0.900387                     0.796464                          0.961550                           0.852261                     0.355352                            0.507288                             0.442460                       3.472154                      3.534293                      3.348824                        0.877550                          0.654597                     0.470995                          0.678634                     0.673315                            0.388947                     
100000000  40.154303                     8.795670                             9.510021                          8.170990                           10.603738                    9.304617                          9.162169                           8.452025                     9.916748                            8.076258                             9.312730                       40.737322                     40.376409                     39.100779                       8.761360                          8.098858                     7.936643                          7.426544                     7.872770                            7.822624                     

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict   
-----------------------------------  ------------------  ------------------  ----------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded  
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
omp/matched/cairn_claim_assigned     0.24                0.23                loss      
omp/matched/cairn_claim_on_demand    -                   0.23                incomplete
omp/matched/library_default          0.38                0.21                loss      
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded  
plain/matched/not_applicable         1.02                1.01                level     
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded  
tbb/matched/cairn_claim_assigned     0.25                0.20                loss      
tbb/matched/library_default          0.23                0.18                loss      
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  

ratio of baseline time to cairn_blocks time, above one favours cairn_blocks; boundaries equal, or more on the cairn side
column                               clang++             g++                 verdict                              
-----------------------------------  ------------------  ------------------  -------------------------------------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded                             
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded                             
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded                             
omp/matched/cairn_claim_assigned     0.86                1.06                level; cairn checks more             
omp/matched/cairn_claim_on_demand    -                   1.04                incomplete; cairn checks more        
omp/matched/library_default          1.38                0.96                level; cairn checks more             
omp/unguarded/cairn_claim_assigned   1.12                1.13                level; cairn checks more             
omp/unguarded/cairn_claim_on_demand  -                   0.92                incomplete; cairn checks more        
omp/unguarded/library_default        1.94                1.06                level; cairn checks more             
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded                             
plain/matched/not_applicable         3.69                4.59                win from n=1000000; cairn checks more
plain/unguarded/not_applicable       3.78                4.45                win from n=1000000; cairn checks more
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded                             
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded                             
tbb/matched/cairn_claim_assigned     0.91                0.90                level; cairn checks more             
tbb/matched/library_default          0.81                0.84                level; cairn checks more             
tbb/unguarded/cairn_claim_assigned   0.87                0.90                level; cairn checks more             
tbb/unguarded/library_default        0.78                0.89                level; cairn checks more             

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   0.726         
clang++   omp    library_default        1.524         
clang++   plain  not_applicable         1.002         
clang++   tbb    cairn_claim_assigned   0.847         
clang++   tbb    library_default        0.992         
g++       omp    cairn_claim_assigned   1.043         
g++       omp    cairn_claim_on_demand  0.988         
g++       omp    library_default        0.878         
g++       plain  not_applicable         0.960         
g++       tbb    cairn_claim_assigned   0.899         
g++       tbb    library_default        0.966         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024   n=16384
--------  -----------------------------------  --------  -------  -------
clang++   cairn/guarded/not_applicable         0.0297    0.2527   3.9858 
clang++   cairn_blocks/guarded/not_applicable  0.2873    0.5062   4.8128 
clang++   omp/guarded/cairn_claim_assigned     7.4339    7.0265   13.9330
clang++   omp/guarded/library_default          12.7516   8.3639   9.7833 
clang++   omp/matched/cairn_claim_assigned     7.7082    6.9534   12.4060
clang++   omp/matched/library_default          98.1267   9.3319   74.9685
clang++   omp/unguarded/cairn_claim_assigned   7.9505    8.5687   19.5050
clang++   omp/unguarded/library_default        29.9257   7.2233   9.6763 
clang++   plain/guarded/not_applicable         0.0293    0.2470   4.3150 
clang++   plain/matched/not_applicable         0.0291    0.2458   3.9475 
clang++   plain/unguarded/not_applicable       0.0293    0.2510   4.1983 
clang++   tbb/guarded/cairn_claim_assigned     0.7415    1.2840   13.7261
clang++   tbb/guarded/library_default          5.2267    9.4986   12.6598
clang++   tbb/matched/cairn_claim_assigned     0.7946    1.2408   12.1248
clang++   tbb/matched/library_default          4.0853    10.0513  9.9868 
clang++   tbb/unguarded/cairn_claim_assigned   1.1741    1.3776   9.2438 
clang++   tbb/unguarded/library_default        3.9428    8.4686   9.3424 
g++       cairn/guarded/not_applicable         0.0529    0.3404   5.7714 
g++       cairn_blocks/guarded/not_applicable  0.1981    1.0564   5.6377 
g++       omp/guarded/cairn_claim_assigned     8.6024    7.2077   32.2738
g++       omp/guarded/cairn_claim_on_demand    13.7321   6.7218   79.2050
g++       omp/guarded/library_default          171.2570  30.1909  9.6537 
g++       omp/matched/cairn_claim_assigned     5.5075    14.5343  17.6332
g++       omp/matched/cairn_claim_on_demand    5.8179    6.8053   38.8128
g++       omp/matched/library_default          6.6609    29.1650  23.7172
g++       omp/unguarded/cairn_claim_assigned   28.4818   12.5191  30.5852
g++       omp/unguarded/cairn_claim_on_demand  17.6983   8.3578   20.9575
g++       omp/unguarded/library_default        6.1152    11.1547  7.3363 
g++       plain/guarded/not_applicable         0.0524    0.3366   5.2339 
g++       plain/matched/not_applicable         0.0498    0.3355   4.7241 
g++       plain/unguarded/not_applicable       0.0476    0.3455   5.7940 
g++       tbb/guarded/cairn_claim_assigned     0.8606    1.2646   17.8131
g++       tbb/guarded/library_default          4.1539    10.4428  14.5370
g++       tbb/matched/cairn_claim_assigned     0.9182    1.4636   8.3412 
g++       tbb/matched/library_default          4.3059    13.9621  11.7990
g++       tbb/unguarded/cairn_claim_assigned   1.0764    1.4209   7.8912 
g++       tbb/unguarded/library_default        4.7075    8.4969   11.6659

== stencil_1d ==
claim: ratio; in because the out-of-place shape is accepted and the in-place shape is refused, which no C++ toolchain refuses
refusal: E-PARALLEL-RACE as preregistered; the compiler printed E-PARALLEL-RACE

safety boundaries
arm                         entry  element  arithmetic  conversion  equal to cairn  boundary in the object
--------------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)             3      0        0           0           -               -                     
cairn_wrap|guarded|clang++  3      2        0           0           no              present               
cairn_wrap|guarded|g++      3      2        0           0           no              present               
cairn|guarded|clang++       3      0        0           0           yes             present               
cairn|guarded|g++           3      0        0           0           yes             present               
omp|guarded|clang++         3      6        3           0           no              present               
omp|guarded|g++             3      6        3           0           no              present               
omp|matched|clang++         3      0        0           0           yes             present               
omp|matched|g++             3      0        0           0           yes             present               
omp|unguarded|clang++       0      0        0           0           no              absent                
omp|unguarded|g++           0      0        0           0           no              absent                
plain|guarded|clang++       3      6        3           0           no              present               
plain|guarded|g++           3      6        3           0           no              present               
plain|matched|clang++       3      0        0           0           yes             present               
plain|matched|g++           3      0        0           0           yes             present               
plain|unguarded|clang++     0      0        0           0           no              absent                
plain|unguarded|g++         0      0        0           0           no              absent                
tbb|guarded|clang++         3      6        3           0           no              present               
tbb|guarded|g++             3      6        3           0           no              present               
tbb|matched|clang++         3      0        0           0           yes             present               
tbb|matched|g++             3      0        0           0           yes             present               
tbb|unguarded|clang++       0      0        0           0           no              absent                
tbb|unguarded|g++           0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  cairn_wrap/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  ---------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000192                      0.000192                           0.431785                          0.018106                     0.977050                          0.018039                     0.034874                            0.002264                       0.000196                      0.000195                      0.000189                        0.000621                          0.005818                     0.000592                          0.005991                     0.000642                            0.005668                     
10000      0.002060                      0.001988                           1.457175                          0.201269                     3.805598                          3.754702                     0.385976                            0.038352                       0.001945                      0.002133                      0.002255                        0.005316                          0.007562                     0.005283                          0.007389                     0.005170                            0.007172                     
100000     0.024636                      0.024492                           0.104286                          0.097602                     0.014125                          4.125143                     0.114155                            0.107239                       0.019908                      0.020913                      0.020787                        0.015641                          0.016733                     0.018808                          0.019485                     0.019696                            0.016952                     
1000000    0.183279                      0.179311                           2.184289                          7.296487                     0.477702                          9.993715                     1.077098                            0.995939                       0.209036                      0.199312                      0.212412                        0.119044                          0.097005                     0.100591                          0.085869                     0.123571                            0.091887                     
10000000   1.356966                      1.392305                           6.501027                          5.047137                     2.652074                          15.689659                    7.532287                            8.317300                       2.664950                      2.447563                      2.913729                        0.941055                          0.745208                     0.834903                          0.813892                     1.077413                            0.820635                     
100000000  22.963583                     21.846101                          28.209652                         35.542127                    22.595392                         24.744738                    33.605461                           25.795828                      33.292863                     33.756452                     31.323563                       21.782036                         22.124684                    22.132597                         21.794675                    22.858552                           22.233902                    

median milliseconds, g++
n          cairn/guarded/not_applicable  cairn_wrap/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/matched/cairn_claim_assigned  omp/matched/cairn_claim_on_demand  omp/matched/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/matched/cairn_claim_assigned  tbb/matched/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  ---------------------------------  --------------------------------  ---------------------------------  ---------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000169                      0.000150                           0.058885                          0.122891                           0.002772                     0.117831                          0.122808                           0.090474                     0.119658                            0.135899                             0.202936                       0.000147                      0.000183                      0.000144                        0.000795                          0.006344                     0.000667                          0.006099                     0.000652                            0.007726                     
10000      0.001771                      0.001864                           0.136232                          0.125177                           0.003276                     0.118985                          0.123237                           0.026968                     0.141792                            0.170239                             0.352821                       0.001648                      0.001719                      0.001535                        0.006253                          0.008874                     0.006848                          0.009457                     0.006360                            0.010594                     
100000     0.023372                      0.020592                           0.154006                          0.134021                           0.013348                     0.119809                          0.117001                           0.050561                     0.120943                            0.119252                             0.186549                       0.017707                      0.016684                      0.014796                        0.021943                          0.027342                     0.020923                          0.024756                     0.019780                            0.026000                     
1000000    0.132809                      0.130467                           0.310525                          0.191724                           0.110138                     0.150186                          0.136005                           0.135477                     0.143403                            0.163315                             0.156940                       0.154187                      0.166395                      0.166333                        0.146191                          0.237004                     0.147098                          0.155538                     0.155615                            0.192223                     
10000000   1.266067                      1.152138                           1.429860                          1.097312                           0.869488                     0.687028                          0.514249                           0.712514                     0.580843                            0.488196                             0.772202                       2.274398                      2.231090                      1.933370                        1.226965                          1.120070                     1.211077                          1.020640                     1.157100                            1.031684                     
100000000  22.642527                     22.852190                          23.851076                         22.906579                          23.671267                    22.723905                         23.174491                          23.336549                    22.751900                           22.711622                            23.082115                      31.735871                     31.403465                     32.301478                       22.812052                         22.703490                    22.138002                         21.666733                    22.414742                           21.716627                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict            
-----------------------------------  ------------------  ------------------  -------------------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded           
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded           
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded           
omp/matched/cairn_claim_assigned     0.98                1.00                level              
omp/matched/cairn_claim_on_demand    -                   1.02                incomplete         
omp/matched/library_default          1.08                1.03                level              
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded           
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded           
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded           
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded           
plain/matched/not_applicable         1.47                1.39                win from n=10000000
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded           
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded           
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded           
tbb/matched/cairn_claim_assigned     0.96                0.98                level              
tbb/matched/library_default          0.95                0.96                level              
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded           
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded           

ratio of baseline time to cairn_wrap time, above one favours cairn_wrap; equal boundaries only
column                               clang++             g++                 verdict 
-----------------------------------  ------------------  ------------------  --------
omp/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded
omp/guarded/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded
omp/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded
omp/matched/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded
omp/matched/cairn_claim_on_demand    boundaries:unequal  boundaries:unequal  excluded
omp/matched/library_default          boundaries:unequal  boundaries:unequal  excluded
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded
plain/guarded/not_applicable         boundaries:unequal  boundaries:unequal  excluded
plain/matched/not_applicable         boundaries:unequal  boundaries:unequal  excluded
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded
tbb/guarded/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded
tbb/guarded/library_default          boundaries:unequal  boundaries:unequal  excluded
tbb/matched/cairn_claim_assigned     boundaries:unequal  boundaries:unequal  excluded
tbb/matched/library_default          boundaries:unequal  boundaries:unequal  excluded
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   1.191         
clang++   omp    library_default        0.726         
clang++   plain  not_applicable         0.941         
clang++   tbb    cairn_claim_assigned   1.049         
clang++   tbb    library_default        1.005         
g++       omp    cairn_claim_assigned   0.954         
g++       omp    cairn_claim_on_demand  0.991         
g++       omp    library_default        0.975         
g++       plain  not_applicable         1.018         
g++       tbb    cairn_claim_assigned   0.983         
g++       tbb    library_default        0.957         

back to back regions, microseconds per region
compiler  column                               n=64       n=1024    n=16384  
--------  -----------------------------------  ---------  --------  ---------
clang++   cairn/guarded/not_applicable         0.0304     0.2151    6.6322   
clang++   cairn_wrap/guarded/not_applicable    0.0161     0.2219    9.1694   
clang++   omp/guarded/cairn_claim_assigned     1583.6737  19.2681   5133.5293
clang++   omp/guarded/library_default          17.6183    32.4709   179.5953 
clang++   omp/matched/cairn_claim_assigned     1.8820     2.7073    23.2191  
clang++   omp/matched/library_default          2.0075     34.3794   440.2577 
clang++   omp/unguarded/cairn_claim_assigned   35.1491    105.3775  239.9660 
clang++   omp/unguarded/library_default        853.0927   19.0871   20.3787  
clang++   plain/guarded/not_applicable         0.0157     0.2396    4.3987   
clang++   plain/matched/not_applicable         0.0250     0.3841    3.9564   
clang++   plain/unguarded/not_applicable       0.0142     0.2045    3.6083   
clang++   tbb/guarded/cairn_claim_assigned     0.0689     0.5846    14.1943  
clang++   tbb/guarded/library_default          2.5105     6.0254    8.4520   
clang++   tbb/matched/cairn_claim_assigned     0.0703     0.5911    10.7848  
clang++   tbb/matched/library_default          2.6437     6.1249    9.2999   
clang++   tbb/unguarded/cairn_claim_assigned   0.0693     0.9908    13.0121  
clang++   tbb/unguarded/library_default        2.6810     5.7253    8.1603   
g++       cairn/guarded/not_applicable         0.0134     0.1744    10.1324  
g++       cairn_wrap/guarded/not_applicable    0.0135     0.1745    7.4139   
g++       omp/guarded/cairn_claim_assigned     125.8066   125.5036  131.0371 
g++       omp/guarded/cairn_claim_on_demand    170.2432   131.8771  122.8202 
g++       omp/guarded/library_default          21.6999    121.4584  86.2221  
g++       omp/matched/cairn_claim_assigned     120.8101   122.2424  141.5353 
g++       omp/matched/cairn_claim_on_demand    149.1662   183.1819  173.3880 
g++       omp/matched/library_default          153.9889   122.3839  137.2147 
g++       omp/unguarded/cairn_claim_assigned   119.8219   123.9622  142.0955 
g++       omp/unguarded/cairn_claim_on_demand  127.9601   121.0734  137.7497 
g++       omp/unguarded/library_default        87.4946    81.9819   10.5089  
g++       plain/guarded/not_applicable         0.0119     0.1670    2.9407   
g++       plain/matched/not_applicable         0.0113     0.1573    4.1988   
g++       plain/unguarded/not_applicable       0.0101     0.1588    2.9104   
g++       tbb/guarded/cairn_claim_assigned     0.1024     0.7059    14.4589  
g++       tbb/guarded/library_default          2.4074     8.0799    13.8815  
g++       tbb/matched/cairn_claim_assigned     0.0809     0.7050    16.6691  
g++       tbb/matched/library_default          2.4927     6.1926    9.9789   
g++       tbb/unguarded/cairn_claim_assigned   0.0757     0.7131    14.2677  
g++       tbb/unguarded/library_default        2.6953     6.5564    9.3010   

== tasks_split ==
claim: ratio; in because four visibly disjoint parts under leases, against std::thread, OpenMP sections and parallel_invoke

safety boundaries
arm                        entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-------------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)            2      4        3           0           -               -                     
cairn|guarded|clang++      2      4        3           0           yes             present               
cairn|guarded|g++          2      4        3           0           yes             present               
omp|guarded|clang++        2      5        4           4           no              present               
omp|guarded|g++            2      5        4           4           no              present               
omp|matched|clang++        2      5        4           0           no              present               
omp|matched|g++            2      5        4           0           no              present               
omp|unguarded|clang++      0      0        0           0           no              absent                
omp|unguarded|g++          0      0        0           0           no              absent                
plain|guarded|clang++      2      5        4           4           no              present               
plain|guarded|g++          2      5        4           4           no              present               
plain|matched|clang++      2      5        4           0           no              present               
plain|matched|g++          2      5        4           0           no              present               
plain|unguarded|clang++    0      0        0           0           no              absent                
plain|unguarded|g++        0      0        0           0           no              absent                
tbb|guarded|clang++        2      5        4           4           no              present               
tbb|guarded|g++            2      5        4           4           no              present               
tbb|matched|clang++        2      5        4           0           no              present               
tbb|matched|g++            2      5        4           0           no              present               
tbb|unguarded|clang++      0      0        0           0           no              absent                
tbb|unguarded|g++          0      0        0           0           no              absent                
threads|guarded|clang++    2      5        4           4           no              present               
threads|guarded|g++        2      5        4           4           no              present               
threads|matched|clang++    2      5        4           0           no              present               
threads|matched|g++        2      5        4           0           no              present               
threads|unguarded|clang++  0      0        0           0           no              absent                
threads|unguarded|g++      0      0        0           0           no              absent                

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/library_default  omp/matched/library_default  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/library_default  tbb/matched/library_default  tbb/unguarded/library_default  threads/guarded/not_applicable  threads/matched/not_applicable  threads/unguarded/not_applicable
---------  ----------------------------  ---------------------------  ---------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  ---------------------------  ---------------------------  -----------------------------  ------------------------------  ------------------------------  --------------------------------
1000       0.000621                      0.000950                     0.001113                     0.000917                       0.000077                      0.000074                      0.000072                        0.000439                     0.000395                     0.000391                       0.181388                        0.173571                        0.175536                        
10000      0.001504                      0.001014                     0.001666                     0.001087                       0.001061                      0.001079                      0.000976                        0.002059                     0.002145                     0.002151                       0.171440                        0.185734                        0.176998                        
100000     0.004452                      0.006897                     0.006698                     0.006836                       0.010653                      0.010880                      0.010646                        0.010421                     0.012116                     0.010057                       0.176501                        0.189750                        0.188552                        
1000000    0.029159                      0.031843                     0.038318                     0.057723                       0.080280                      0.110924                      0.081514                        0.076547                     0.072788                     0.076936                       0.199383                        0.213465                        0.209081                        
10000000   0.521398                      0.624096                     0.980347                     0.774533                       1.426551                      1.466975                      1.521753                        0.688364                     0.747917                     0.723724                       0.676711                        1.551158                        0.808853                        
100000000  30.803722                     33.533967                    32.913067                    32.702133                      35.129314                     34.856324                     37.304378                       31.873307                    32.937145                    33.014764                      32.235368                       34.277178                       32.105037                       

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/library_default  omp/matched/library_default  omp/unguarded/library_default  plain/guarded/not_applicable  plain/matched/not_applicable  plain/unguarded/not_applicable  tbb/guarded/library_default  tbb/matched/library_default  tbb/unguarded/library_default  threads/guarded/not_applicable  threads/matched/not_applicable  threads/unguarded/not_applicable
---------  ----------------------------  ---------------------------  ---------------------------  -----------------------------  ----------------------------  ----------------------------  ------------------------------  ---------------------------  ---------------------------  -----------------------------  ------------------------------  ------------------------------  --------------------------------
1000       0.000661                      0.000924                     0.000892                     0.000992                       0.000083                      0.000083                      0.000080                        0.000449                     0.000422                     0.000425                       0.186109                        0.174598                        0.174940                        
10000      0.001512                      0.001692                     0.001694                     0.001624                       0.001125                      0.001119                      0.001055                        0.002243                     0.002165                     0.002229                       0.178981                        0.178499                        0.175203                        
100000     0.005570                      0.004988                     0.005704                     0.005014                       0.011847                      0.012348                      0.012023                        0.011137                     0.014037                     0.010801                       0.181434                        0.181499                        0.178439                        
1000000    0.031735                      0.032743                     0.035196                     0.035311                       0.122841                      0.111637                      0.117086                        0.070110                     0.075826                     0.074897                       0.201597                        0.196300                        0.199749                        
10000000   0.919960                      0.567411                     0.751731                     0.647526                       1.948380                      2.244328                      1.836801                        0.814751                     0.679206                     0.776844                       0.795775                        0.938613                        0.824930                        
100000000  30.854155                     31.035125                    32.043707                    32.648027                      41.773336                     45.296897                     40.128012                       31.660530                    32.574118                    33.236990                      32.834166                       32.773739                       31.491271                       

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                            clang++             g++                 verdict 
--------------------------------  ------------------  ------------------  --------
omp/guarded/library_default       boundaries:unequal  boundaries:unequal  excluded
omp/matched/library_default       boundaries:unequal  boundaries:unequal  excluded
omp/unguarded/library_default     boundaries:unequal  boundaries:unequal  excluded
plain/guarded/not_applicable      boundaries:unequal  boundaries:unequal  excluded
plain/matched/not_applicable      boundaries:unequal  boundaries:unequal  excluded
plain/unguarded/not_applicable    boundaries:unequal  boundaries:unequal  excluded
tbb/guarded/library_default       boundaries:unequal  boundaries:unequal  excluded
tbb/matched/library_default       boundaries:unequal  boundaries:unequal  excluded
tbb/unguarded/library_default     boundaries:unequal  boundaries:unequal  excluded
threads/guarded/not_applicable    boundaries:unequal  boundaries:unequal  excluded
threads/matched/not_applicable    boundaries:unequal  boundaries:unequal  excluded
threads/unguarded/not_applicable  boundaries:unequal  boundaries:unequal  excluded

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm      grain row        at n=100000000
--------  -------  ---------------  --------------
clang++   omp      library_default  0.975         
clang++   plain    not_applicable   1.062         
clang++   tbb      library_default  1.036         
clang++   threads  not_applicable   0.996         
g++       omp      library_default  1.052         
g++       plain    not_applicable   0.961         
g++       tbb      library_default  1.050         
g++       threads  not_applicable   0.959         

back to back regions, microseconds per region
compiler  column                            n=64      n=1024    n=16384 
--------  --------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable      0.5540    0.5831    2.0073  
clang++   omp/guarded/library_default       0.7671    0.7441    1.6132  
clang++   omp/matched/library_default       0.8666    0.8447    1.5998  
clang++   omp/unguarded/library_default     0.8613    0.8882    1.6741  
clang++   plain/guarded/not_applicable      0.0102    0.0839    1.8374  
clang++   plain/matched/not_applicable      0.0099    0.0762    1.7106  
clang++   plain/unguarded/not_applicable    0.0064    0.0686    1.7888  
clang++   tbb/guarded/library_default       0.2435    0.4090    2.7293  
clang++   tbb/matched/library_default       0.2487    0.3730    3.1552  
clang++   tbb/unguarded/library_default     0.2287    0.3736    3.6235  
clang++   threads/guarded/not_applicable    175.4275  176.3658  176.5657
clang++   threads/matched/not_applicable    192.0022  180.0937  182.6409
clang++   threads/unguarded/not_applicable  184.1616  181.4983  171.3281
g++       cairn/guarded/not_applicable      0.5671    0.5924    1.7520  
g++       omp/guarded/library_default       1.3858    0.8779    2.1002  
g++       omp/matched/library_default       1.4351    0.9550    1.9991  
g++       omp/unguarded/library_default     1.3461    0.8937    2.0042  
g++       plain/guarded/not_applicable      0.0102    0.0840    1.8085  
g++       plain/matched/not_applicable      0.0107    0.0892    2.1229  
g++       plain/unguarded/not_applicable    0.0083    0.0793    1.7782  
g++       tbb/guarded/library_default       0.2332    0.4236    3.0459  
g++       tbb/matched/library_default       0.3149    0.4118    3.1945  
g++       tbb/unguarded/library_default     0.2315    0.4177    3.1525  
g++       threads/guarded/not_applicable    175.9076  181.1306  178.5736
g++       threads/matched/not_applicable    173.6339  172.2811  175.4445
g++       threads/unguarded/not_applicable  175.3346  175.8620  181.2563

One machine, one lane count, two compilers. Not a tuned-kernel claim, and not another host's numbers.

full run of the preregistered suite, /home/sam/cairn/bench/suite/PREREGISTRATION.md
host Linux x86_64, profile x86-64-v4, 16 lanes, not pinned
compilers g++: g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0, clang++: clang version 21.1.8 (https://github.com/llvm/llvm-project 2078da43e25a4623cab2d0d60decddf709aaea28)
commit 892b309d4e0fb399d10a6d0c2c5848b79109d22b
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
cairn (receipt)          5      3        0           0           -               -                     
cairn|guarded|clang++    5      3        0           0           yes             present               
cairn|guarded|g++        5      3        0           0           yes             present               
omp|guarded|clang++      5      3        0           0           yes             present               
omp|guarded|g++          5      3        0           0           yes             present               
omp|unguarded|clang++    0      0        0           0           no              absent                
omp|unguarded|g++        0      0        0           0           no              absent                
plain|guarded|clang++    5      3        0           0           yes             present               
plain|guarded|g++        5      3        0           0           yes             present               
plain|unguarded|clang++  0      0        0           0           no              absent                
plain|unguarded|g++      0      0        0           0           no              absent                
tbb|guarded|clang++      5      3        0           0           yes             present               
tbb|guarded|g++          5      3        0           0           yes             present               
tbb|unguarded|clang++    0      0        0           0           no              absent                
tbb|unguarded|g++        0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000044                      0.010365                          0.023131                     0.023234                            0.015136                       0.000041                      0.000044                        0.000093                          0.006674                     0.000087                            0.006727                     
10000      0.000930                      0.020335                          0.003680                     0.019419                            0.017357                       0.000906                      0.000941                        0.001174                          0.009361                     0.000977                            0.008256                     
100000     0.026476                      0.004334                          0.003498                     0.004252                            0.172663                       0.010752                      0.010611                        0.005546                          0.011329                     0.004965                            0.013412                     
1000000    0.224785                      0.024870                          0.234449                     0.204460                            0.025717                       0.124563                      0.122510                        0.032568                          0.027239                     0.026081                            0.026172                     
10000000   1.674639                      1.439524                          1.521171                     1.395929                            2.429826                       3.127569                      3.069911                        1.344306                          1.390129                     1.435143                            1.230871                     
100000000  29.452154                     30.331126                         30.333291                    31.535910                           30.136722                      43.225559                     42.527856                       29.076294                         29.247652                    29.640074                           29.174274                    

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000046                      0.100260                          0.101859                           0.107382                     0.105214                            0.107026                             0.100992                       0.000044                      0.000040                        0.000546                          0.006029                     0.000078                            0.005587                     
10000      0.000980                      0.100449                          0.102574                           0.122521                     0.093623                            0.105548                             0.103796                       0.000890                      0.000871                        0.005154                          0.010984                     0.000974                            0.007521                     
100000     0.019138                      0.108953                          0.106158                           0.112328                     0.101475                            0.107166                             0.106152                       0.010775                      0.010349                        0.015761                          0.029242                     0.005252                            0.009334                     
1000000    0.183768                      0.206369                          0.151178                           0.169592                     0.118448                            0.122459                             0.127910                       0.135518                      0.135546                        0.136884                          0.101566                     0.026486                            0.028732                     
10000000   1.396044                      1.549295                          1.282116                           4.775636                     1.559495                            1.187365                             1.276294                       2.801777                      3.547779                        1.406026                          1.491812                     1.341150                            1.402724                     
100000000  29.883302                     29.638015                         29.757636                          29.386980                    29.111201                           29.198318                            29.611273                      46.076236                     49.506342                       29.150042                         29.057592                    28.953377                           28.978427                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict            
-----------------------------------  ------------------  ------------------  -------------------
omp/guarded/cairn_claim_assigned     1.03                0.99                level              
omp/guarded/cairn_claim_on_demand    -                   1.00                incomplete         
omp/guarded/library_default          1.03                0.98                level              
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded           
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded           
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded           
plain/guarded/not_applicable         1.47                1.54                win from n=10000000
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded           
tbb/guarded/cairn_claim_assigned     0.99                0.98                level              
tbb/guarded/library_default          0.99                0.97                level              
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded           
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded           

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   1.040         
clang++   omp    library_default        0.994         
clang++   plain  not_applicable         0.984         
clang++   tbb    cairn_claim_assigned   1.019         
clang++   tbb    library_default        0.997         
g++       omp    cairn_claim_assigned   0.982         
g++       omp    cairn_claim_on_demand  0.981         
g++       omp    library_default        1.008         
g++       plain  not_applicable         1.074         
g++       tbb    cairn_claim_assigned   0.993         
g++       tbb    library_default        0.997         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0058    0.0458    9.1345  
clang++   omp/guarded/cairn_claim_assigned     51.6286   20.9099   52.7331 
clang++   omp/guarded/library_default          1.5668    155.1134  98.9671 
clang++   omp/unguarded/cairn_claim_assigned   18.2428   37.1157   39.8183 
clang++   omp/unguarded/library_default        1.8745    1.7763    46.9305 
clang++   plain/guarded/not_applicable         0.0039    0.0461    1.4434  
clang++   plain/unguarded/not_applicable       0.0032    0.0448    1.4435  
clang++   tbb/guarded/cairn_claim_assigned     0.0500    0.1024    2.5070  
clang++   tbb/guarded/library_default          2.3387    7.3370    9.5636  
clang++   tbb/unguarded/cairn_claim_assigned   0.0405    0.0809    2.3994  
clang++   tbb/unguarded/library_default        2.8491    6.8547    8.0297  
g++       cairn/guarded/not_applicable         0.0070    0.0511    7.0124  
g++       omp/guarded/cairn_claim_assigned     97.1729   100.6322  104.1285
g++       omp/guarded/cairn_claim_on_demand    104.0900  106.5220  100.5875
g++       omp/guarded/library_default          98.9559   124.3610  106.2202
g++       omp/unguarded/cairn_claim_assigned   98.2055   97.6053   100.8943
g++       omp/unguarded/cairn_claim_on_demand  82.3702   105.2668  138.9294
g++       omp/unguarded/library_default        97.5625   94.2880   102.5194
g++       plain/guarded/not_applicable         0.0042    0.0460    1.4748  
g++       plain/unguarded/not_applicable       0.0034    0.0485    1.6537  
g++       tbb/guarded/cairn_claim_assigned     0.0745    0.5700    11.3185 
g++       tbb/guarded/library_default          2.5207    6.0445    10.6799 
g++       tbb/unguarded/cairn_claim_assigned   0.0389    0.0866    2.0973  
g++       tbb/unguarded/library_default        2.9914    6.9533    7.7069  

== mixed_u64 ==
claim: ratio; in because a dependent integer mix in registers, so a large region is bound by the cores

safety boundaries
arm                      entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-----------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)          3      2        1           0           -               -                     
cairn|guarded|clang++    3      2        1           0           yes             present               
cairn|guarded|g++        3      2        1           0           yes             present               
omp|guarded|clang++      3      2        1           0           yes             present               
omp|guarded|g++          3      2        1           0           yes             present               
omp|unguarded|clang++    0      0        0           0           no              absent                
omp|unguarded|g++        0      0        0           0           no              absent                
plain|guarded|clang++    3      2        1           0           yes             present               
plain|guarded|g++        3      2        1           0           yes             present               
plain|unguarded|clang++  0      0        0           0           no              absent                
plain|unguarded|g++      0      0        0           0           no              absent                
tbb|guarded|clang++      3      2        1           0           yes             present               
tbb|guarded|g++          3      2        1           0           yes             present               
tbb|unguarded|clang++    0      0        0           0           no              absent                
tbb|unguarded|g++        0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000626                      0.215863                          0.017438                     0.079255                            0.019710                       0.000797                      0.000588                        0.002875                          0.006747                     0.002757                            0.007410                     
10000      0.006141                      0.308813                          0.072647                     0.359557                            0.082691                       0.006129                      0.006100                        0.026583                          0.015574                     0.026251                            0.009117                     
100000     0.066408                      0.213064                          0.201911                     0.395010                            0.190644                       0.061981                      0.060522                        0.093115                          0.084747                     0.081156                            0.018954                     
1000000    0.556200                      0.828765                          1.118568                     0.995250                            1.156259                       0.577912                      0.610047                        0.571916                          0.454639                     0.468576                            0.133356                     
10000000   4.748580                      5.813351                          7.300582                     5.794914                            5.293955                       6.677582                      6.819108                        4.326411                          3.816367                     4.070119                            2.461978                     
100000000  46.500864                     57.723451                         59.298760                    55.763694                           51.949483                      71.005383                     73.688531                       46.568426                         42.780456                    45.250753                           43.261553                    

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.002882                      0.096822                          0.103180                           0.104085                     0.095764                            0.104742                             0.112855                       0.000432                      0.000455                        0.003060                          0.007055                     0.002546                            0.009533                     
10000      0.028776                      0.100707                          0.104348                           0.116009                     0.096208                            0.103625                             0.100352                       0.005254                      0.005954                        0.029634                          0.016636                     0.026309                            0.011351                     
100000     0.069109                      0.130128                          0.114002                           0.165163                     0.112216                            0.110466                             0.113800                       0.060593                      0.058233                        0.082821                          0.080626                     0.074912                            0.025399                     
1000000    0.549375                      0.652765                          0.574522                           0.647794                     0.166120                            0.157769                             0.172119                       0.532166                      0.562998                        0.516061                          0.438969                     0.496206                            0.109506                     
10000000   4.414154                      5.490387                          5.038307                           5.557422                     2.641789                            2.429456                             2.621547                       6.905756                      6.417310                        4.335996                          3.933567                     4.025103                            2.803624                     
100000000  46.003353                     48.639360                         52.101966                          49.286148                    42.564077                           43.042798                            43.548373                      67.834469                     66.362061                       46.122024                         42.710681                    45.881163                           42.882300                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict            
-----------------------------------  ------------------  ------------------  -------------------
omp/guarded/cairn_claim_assigned     1.24                1.06                level              
omp/guarded/cairn_claim_on_demand    -                   1.13                incomplete         
omp/guarded/library_default          1.28                1.07                level              
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded           
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded           
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded           
plain/guarded/not_applicable         1.53                1.47                win from n=10000000
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded           
tbb/guarded/cairn_claim_assigned     1.00                1.00                level              
tbb/guarded/library_default          0.92                0.93                level              
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded           
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded           

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   0.966         
clang++   omp    library_default        0.876         
clang++   plain  not_applicable         1.038         
clang++   tbb    cairn_claim_assigned   0.972         
clang++   tbb    library_default        1.011         
g++       omp    cairn_claim_assigned   0.875         
g++       omp    cairn_claim_on_demand  0.826         
g++       omp    library_default        0.884         
g++       plain  not_applicable         0.978         
g++       tbb    cairn_claim_assigned   0.995         
g++       tbb    library_default        1.004         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0409    0.6000    29.6487 
clang++   omp/guarded/cairn_claim_assigned     14.5676   66.6065   287.5607
clang++   omp/guarded/library_default          72.2372   17.0105   87.9149 
clang++   omp/unguarded/cairn_claim_assigned   2.1191    64.0984   247.9309
clang++   omp/unguarded/library_default        1.5873    92.1694   94.3253 
clang++   plain/guarded/not_applicable         0.0364    0.6175    9.7351  
clang++   plain/unguarded/not_applicable       0.0363    0.5996    10.4105 
clang++   tbb/guarded/cairn_claim_assigned     0.1964    2.6969    43.8130 
clang++   tbb/guarded/library_default          2.5171    7.2925    17.2647 
clang++   tbb/unguarded/cairn_claim_assigned   0.1898    2.7211    42.0856 
clang++   tbb/unguarded/library_default        2.3389    6.6296    11.1915 
g++       cairn/guarded/not_applicable         0.1763    2.8373    24.7073 
g++       omp/guarded/cairn_claim_assigned     96.7676   95.1668   93.2924 
g++       omp/guarded/cairn_claim_on_demand    102.8224  100.3986  110.6993
g++       omp/guarded/library_default          93.6415   103.3929  109.7105
g++       omp/unguarded/cairn_claim_assigned   90.5381   96.5862   95.5924 
g++       omp/unguarded/cairn_claim_on_demand  100.8524  92.6037   112.3245
g++       omp/unguarded/library_default        96.1983   95.1328   92.7511 
g++       plain/guarded/not_applicable         0.0275    0.4449    9.0905  
g++       plain/unguarded/not_applicable       0.0274    0.4451    9.5157  
g++       tbb/guarded/cairn_claim_assigned     0.2101    3.2037    44.6709 
g++       tbb/guarded/library_default          2.3015    6.6269    22.5841 
g++       tbb/unguarded/cairn_claim_assigned   0.1927    2.6239    43.5981 
g++       tbb/unguarded/library_default        2.3106    10.8684   14.2643 

== sum_u64_wrap ==
claim: ratio; in because wrapping addition is order independent, so a reassociating baseline is the same function

safety boundaries
arm                           entry  element  arithmetic  conversion  equal to cairn  boundary in the object
----------------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)               1      1        0           0           -               -                     
cairn_atomic|guarded|clang++  1      1        0           0           yes             present               
cairn_atomic|guarded|g++      1      1        0           0           yes             present               
cairn|guarded|clang++         1      1        0           0           yes             present               
cairn|guarded|g++             1      1        0           0           yes             present               
omp|guarded|clang++           1      1        0           0           yes             present               
omp|guarded|g++               1      1        0           0           yes             present               
omp|unguarded|clang++         0      0        0           0           no              absent                
omp|unguarded|g++             0      0        0           0           no              absent                
plain|guarded|clang++         1      1        0           0           yes             present               
plain|guarded|g++             1      1        0           0           yes             present               
plain|unguarded|clang++       0      0        0           0           no              absent                
plain|unguarded|g++           0      0        0           0           no              absent                
tbb|guarded|clang++           1      1        0           0           yes             present               
tbb|guarded|g++               1      1        0           0           yes             present               
tbb|unguarded|clang++         0      0        0           0           no              absent                
tbb|unguarded|g++             0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  cairn_atomic/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  -----------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000035                      0.001682                             0.076147                          0.002704                     0.019382                            0.025418                       0.000036                      0.000033                        0.000075                          0.007111                     0.000073                            0.006868                     
10000      0.000560                      0.018698                             0.020342                          0.017845                     0.039519                            0.017715                       0.000594                      0.000614                        0.000617                          0.008049                     0.000607                            0.008560                     
100000     0.005797                      0.983194                             0.004884                          0.003313                     0.004083                            0.003476                       0.005934                      0.006047                        0.004016                          0.010453                     0.004674                            0.011468                     
1000000    0.068030                      11.245310                            0.020099                          0.352234                     0.016390                            0.011620                       0.064452                      0.072978                        0.017001                          0.020862                     0.021339                            0.020574                     
10000000   0.901135                      112.498774                           0.151453                          1.651274                     0.169779                            0.554887                       1.365712                      0.988405                        0.182466                          0.187462                     0.216854                            0.176096                     
100000000  20.920825                     1132.523044                          17.520510                         16.069238                    17.392570                           16.675147                      23.862024                     23.663530                       15.127801                         15.023386                    15.013972                           15.106985                    

median milliseconds, g++
n          cairn/guarded/not_applicable  cairn_atomic/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  -----------------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000043                      0.001416                             0.149884                          0.154790                           0.140556                     0.187799                            0.149535                             0.150219                       0.000043                      0.000040                        0.000287                          0.007379                     0.000077                            0.008716                     
10000      0.000625                      0.018520                             0.152771                          0.153195                           0.153038                     0.176456                            0.151392                             0.160909                       0.000604                      0.000644                        0.002522                          0.010233                     0.000630                            0.009247                     
100000     0.006720                      1.057088                             0.188292                          0.165578                           0.160905                     0.151698                            0.156002                             0.169570                       0.006629                      0.007015                        0.012183                          0.024526                     0.004216                            0.015727                     
1000000    0.069448                      11.472048                            0.252078                          0.230874                           0.199051                     0.167578                            0.159405                             0.175244                       0.071757                      0.073867                        0.057844                          0.084141                     0.020795                            0.037000                     
10000000   1.239817                      113.845277                           1.246822                          0.570491                           0.708897                     0.381841                            0.355847                             0.398343                       1.042369                      1.577592                        0.485724                          0.365505                     0.202692                            0.265007                     
100000000  23.214258                     1150.334090                          16.421905                         15.231419                          15.734244                    15.316230                           15.572811                            15.385938                      30.827276                     26.838895                       15.138636                         14.489084                    15.194876                           14.964819                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict   
-----------------------------------  ------------------  ------------------  ----------
omp/guarded/cairn_claim_assigned     0.84                0.71                level     
omp/guarded/cairn_claim_on_demand    -                   0.66                incomplete
omp/guarded/library_default          0.77                0.68                loss      
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  
plain/guarded/not_applicable         1.14                1.33                level     
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/cairn_claim_assigned     0.72                0.65                loss      
tbb/guarded/library_default          0.72                0.62                loss      
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   0.993         
clang++   omp    library_default        1.038         
clang++   plain  not_applicable         0.992         
clang++   tbb    cairn_claim_assigned   0.992         
clang++   tbb    library_default        1.006         
g++       omp    cairn_claim_assigned   0.933         
g++       omp    cairn_claim_on_demand  1.022         
g++       omp    library_default        0.978         
g++       plain  not_applicable         0.871         
g++       tbb    cairn_claim_assigned   1.004         
g++       tbb    library_default        1.033         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0052    0.0367    0.9494  
clang++   cairn_atomic/guarded/not_applicable  0.1044    1.7702    123.1760
clang++   omp/guarded/cairn_claim_assigned     20.4311   2.4323    65.2355 
clang++   omp/guarded/library_default          16.5909   65.7138   97.9613 
clang++   omp/unguarded/cairn_claim_assigned   172.6322  123.4518  20.9897 
clang++   omp/unguarded/library_default        18.0700   38.6309   3.1491  
clang++   plain/guarded/not_applicable         0.0034    0.0359    0.9512  
clang++   plain/unguarded/not_applicable       0.0040    0.0348    0.9676  
clang++   tbb/guarded/cairn_claim_assigned     0.0432    0.0828    1.8278  
clang++   tbb/guarded/library_default          2.7322    6.7740    7.8263  
clang++   tbb/unguarded/cairn_claim_assigned   0.0436    0.0949    1.7583  
clang++   tbb/unguarded/library_default        2.5718    6.0754    9.3998  
g++       cairn/guarded/not_applicable         0.0041    0.0467    1.0641  
g++       cairn_atomic/guarded/not_applicable  0.1026    1.4435    121.1186
g++       omp/guarded/cairn_claim_assigned     148.8468  148.9301  156.6840
g++       omp/guarded/cairn_claim_on_demand    111.2552  152.4086  150.2019
g++       omp/guarded/library_default          152.2758  151.6514  160.7774
g++       omp/unguarded/cairn_claim_assigned   150.7414  145.8709  148.6872
g++       omp/unguarded/cairn_claim_on_demand  157.5790  152.5060  153.2113
g++       omp/unguarded/library_default        149.4019  150.9463  119.6549
g++       plain/guarded/not_applicable         0.0032    0.0411    1.1459  
g++       plain/unguarded/not_applicable       0.0024    0.0327    0.9574  
g++       tbb/guarded/cairn_claim_assigned     0.0573    0.2813    6.1119  
g++       tbb/guarded/library_default          2.8810    6.8340    8.5373  
g++       tbb/unguarded/cairn_claim_assigned   0.0402    0.0770    1.7282  
g++       tbb/unguarded/library_default        2.8547    6.3021    7.8542  

== dot_f64 ==
claim: semantic_difference; in because a strict in-order fold and a reassociating reduction are different functions, not two speeds

safety boundaries
arm                      entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-----------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)          2      2        0           0           -               -                     
cairn|guarded|clang++    2      2        0           0           yes             present               
cairn|guarded|g++        2      2        0           0           yes             present               
omp|guarded|clang++      2      2        0           0           yes             present               
omp|guarded|g++          2      2        0           0           yes             present               
omp|unguarded|clang++    0      0        0           0           no              absent                
omp|unguarded|g++        0      0        0           0           no              absent                
plain|guarded|clang++    2      2        0           0           yes             present               
plain|guarded|g++        2      2        0           0           yes             present               
plain|unguarded|clang++  0      0        0           0           no              absent                
plain|unguarded|g++      0      0        0           0           no              absent                
tbb|guarded|clang++      2      2        0           0           yes             present               
tbb|guarded|g++          2      2        0           0           yes             present               
tbb|unguarded|clang++    0      0        0           0           no              absent                
tbb|unguarded|g++        0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build

did this arm compute the function the CAIRN source names, bit for bit
column                               compiler  at n=64
-----------------------------------  --------  -------
cairn/guarded/not_applicable         g++       yes    
plain/guarded/not_applicable         g++       yes    
plain/unguarded/not_applicable       g++       yes    
omp/guarded/library_default          g++       yes    
omp/unguarded/library_default        g++       no     
omp/guarded/cairn_claim_assigned     g++       yes    
omp/unguarded/cairn_claim_assigned   g++       yes    
omp/guarded/cairn_claim_on_demand    g++       yes    
omp/unguarded/cairn_claim_on_demand  g++       yes    
tbb/guarded/library_default          g++       yes    
tbb/unguarded/library_default        g++       yes    
tbb/guarded/cairn_claim_assigned     g++       yes    
tbb/unguarded/cairn_claim_assigned   g++       yes    
cairn/guarded/not_applicable         clang++   yes    
plain/guarded/not_applicable         clang++   yes    
plain/unguarded/not_applicable       clang++   yes    
omp/guarded/library_default          clang++   yes    
omp/unguarded/library_default        clang++   yes    
omp/guarded/cairn_claim_assigned     clang++   yes    
omp/unguarded/cairn_claim_assigned   clang++   yes    
tbb/guarded/library_default          clang++   yes    
tbb/unguarded/library_default        clang++   yes    
tbb/guarded/cairn_claim_assigned     clang++   yes    
tbb/unguarded/cairn_claim_assigned   clang++   yes    

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000608                      0.094343                          0.014381                     0.021741                            0.025456                       0.000615                      0.000599                        0.000748                          0.006075                     0.000674                            0.006722                     
10000      0.006761                      0.264196                          0.038310                     0.057938                            0.039343                       0.006423                      0.006716                        0.006636                          0.008465                     0.006972                            0.008020                     
100000     0.067235                      0.045512                          0.273233                     0.218583                            0.103312                       0.064759                      0.064695                        0.012636                          0.023366                     0.013258                            0.021562                     
1000000    0.649139                      0.229946                          0.747681                     0.189218                            0.101748                       0.648641                      0.651177                        0.065145                          0.057776                     0.061983                            0.084353                     
10000000   7.368776                      2.867701                          2.334635                     2.679230                            4.525032                       7.174132                      7.225394                        1.782634                          1.709261                     1.711566                            1.706226                     
100000000  75.200062                     33.288761                         35.083776                    32.735272                           34.826170                      75.001743                     73.551844                       30.612895                         30.177258                    30.570253                           30.243812                    

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000634                      0.100753                          0.102228                           0.104989                     0.104147                            0.110055                             0.101900                       0.000649                      0.000605                        0.000740                          0.007457                     0.000690                            0.007041                     
10000      0.007667                      0.101155                          0.106156                           0.106360                     0.103208                            0.101858                             0.104423                       0.008512                      0.008089                        0.006608                          0.011216                     0.008347                            0.008363                     
100000     0.073956                      0.111858                          0.107636                           0.156783                     0.106803                            0.105560                             0.111120                       0.080254                      0.081000                        0.016400                          0.022751                     0.011875                            0.014192                     
1000000    0.772009                      0.193832                          0.146415                           0.240724                     0.178701                            0.144574                             0.172933                       0.715522                      0.806348                        0.081968                          0.112667                     0.063910                            0.058238                     
10000000   9.142174                      1.971996                          1.963208                           2.054363                     2.033730                            1.785355                             1.964446                       8.283016                      8.979801                        1.833531                          1.876404                     1.832695                            1.780693                     
100000000  84.502941                     30.773112                         30.708523                          31.173753                    31.672873                           31.043029                            30.895697                      86.703535                     85.047910                       30.559671                         30.452193                    30.916557                           30.532064                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict   
-----------------------------------  ------------------  ------------------  ----------
omp/guarded/cairn_claim_assigned     0.44                0.36                loss      
omp/guarded/cairn_claim_on_demand    -                   0.36                incomplete
omp/guarded/library_default          0.47                0.37                loss      
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  
plain/guarded/not_applicable         1.00                1.03                level     
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/cairn_claim_assigned     0.41                0.36                loss      
tbb/guarded/library_default          0.40                0.36                loss      
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   0.983         
clang++   omp    library_default        0.993         
clang++   plain  not_applicable         0.981         
clang++   tbb    cairn_claim_assigned   0.999         
clang++   tbb    library_default        1.002         
g++       omp    cairn_claim_assigned   1.029         
g++       omp    cairn_claim_on_demand  1.011         
g++       omp    library_default        0.991         
g++       plain  not_applicable         0.981         
g++       tbb    cairn_claim_assigned   1.012         
g++       tbb    library_default        1.003         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0251    0.6189    10.6499 
clang++   omp/guarded/cairn_claim_assigned     46.4604   22.8788   58.3203 
clang++   omp/guarded/library_default          276.6776  30.7020   18.0966 
clang++   omp/unguarded/cairn_claim_assigned   19.7728   3.9106    79.1386 
clang++   omp/unguarded/library_default        37.5972   19.0941   36.8965 
clang++   plain/guarded/not_applicable         0.0247    0.6743    11.1195 
clang++   plain/unguarded/not_applicable       0.0244    0.6272    10.6403 
clang++   tbb/guarded/cairn_claim_assigned     0.0724    0.7532    9.1858  
clang++   tbb/guarded/library_default          2.7551    6.3963    8.8500  
clang++   tbb/unguarded/cairn_claim_assigned   0.0719    0.7439    10.3145 
clang++   tbb/unguarded/library_default        2.6583    6.7363    9.4903  
g++       cairn/guarded/not_applicable         0.0210    0.6211    12.5195 
g++       omp/guarded/cairn_claim_assigned     100.7410  94.4600   103.7340
g++       omp/guarded/cairn_claim_on_demand    102.8611  50.9126   104.0646
g++       omp/guarded/library_default          105.3721  107.3313  103.6121
g++       omp/unguarded/cairn_claim_assigned   115.3073  100.8605  101.4393
g++       omp/unguarded/cairn_claim_on_demand  105.5565  104.7473  104.9599
g++       omp/unguarded/library_default        95.1899   102.2749  116.8145
g++       plain/guarded/not_applicable         0.0213    0.6220    13.1280 
g++       plain/unguarded/not_applicable       0.0198    0.6200    13.0974 
g++       tbb/guarded/cairn_claim_assigned     0.0741    0.7090    10.2905 
g++       tbb/guarded/library_default          2.4751    6.5147    10.8165 
g++       tbb/unguarded/cairn_claim_assigned   0.0713    0.6831    9.5474  
g++       tbb/unguarded/library_default        2.6384    7.0245    8.9968  

== compact_even ==
claim: ratio; in because the certified collector against std::copy_if and a two-pass parallel compaction

safety boundaries
arm                      entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-----------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)          3      2        0           0           -               -                     
cairn|guarded|clang++    3      2        0           0           yes             present               
cairn|guarded|g++        3      2        0           0           yes             present               
omp|guarded|clang++      3      2        0           0           yes             present               
omp|guarded|g++          3      2        0           0           yes             present               
omp|unguarded|clang++    0      0        0           0           no              absent                
omp|unguarded|g++        0      0        0           0           no              absent                
plain|guarded|clang++    3      2        0           0           yes             present               
plain|guarded|g++        3      2        0           0           yes             present               
plain|unguarded|clang++  0      0        0           0           no              absent                
plain|unguarded|g++      0      0        0           0           no              absent                
tbb|guarded|clang++      3      2        0           0           yes             present               
tbb|guarded|g++          3      2        0           0           yes             present               
tbb|unguarded|clang++    0      0        0           0           no              absent                
tbb|unguarded|g++        0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000295                      0.076726                          0.018179                     0.073248                            0.075911                       0.000400                      0.000220                        0.000699                          0.003205                     0.000542                            0.003005                     
10000      0.003042                      0.211165                          0.035785                     0.075930                            0.057412                       0.004500                      0.002417                        0.005899                          0.007953                     0.004682                            0.006604                     
100000     0.034849                      0.459420                          0.237635                     0.312889                            0.341272                       0.047506                      0.027725                        0.040771                          0.036742                     0.035361                            0.031383                     
1000000    0.566854                      0.971649                          1.136443                     1.400659                            0.957732                       0.685995                      0.524937                        0.486337                          0.506281                     0.495245                            0.497557                     
10000000   7.296855                      8.735325                          7.043375                     7.691046                            7.517978                       7.566208                      6.833406                        4.715755                          5.047091                     4.486574                            4.882780                     
100000000  73.843733                     91.106135                         86.181122                    87.829220                           86.632258                      78.850232                     72.970788                       72.793045                         72.034816                    72.059372                           71.923437                    

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000384                      0.200248                          0.203256                           0.201684                     0.192661                            0.216097                             0.200355                       0.000584                      0.000375                        0.001054                          0.003659                     0.000621                            0.003277                     
10000      0.004018                      0.191690                          0.206018                           0.211670                     0.205829                            0.209138                             0.189340                       0.005299                      0.004044                        0.009891                          0.010435                     0.005169                            0.009088                     
100000     0.045853                      0.226309                          0.222650                           0.222907                     0.213772                            0.218537                             0.217684                       0.054363                      0.093136                        0.048092                          0.054220                     0.035717                            0.044208                     
1000000    0.996658                      0.578187                          0.535748                           0.567151                     0.495165                            0.462110                             0.530716                       0.729131                      1.033637                        0.543656                          0.648281                     0.474149                            0.547710                     
10000000   9.953695                      5.882674                          5.621765                           5.981470                     5.374204                            5.195948                             5.267034                       7.958954                      11.392952                       5.074873                          5.339600                     4.735201                            5.061361                     
100000000  111.237063                    73.199456                         73.128249                          73.274162                    73.061703                           72.158926                            72.452064                      85.738266                     113.234833                      71.386012                         71.849244                    72.166935                           72.346713                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict   
-----------------------------------  ------------------  ------------------  ----------
omp/guarded/cairn_claim_assigned     1.23                0.66                level     
omp/guarded/cairn_claim_on_demand    -                   0.66                incomplete
omp/guarded/library_default          1.17                0.66                level     
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  
plain/guarded/not_applicable         1.07                0.77                level     
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/cairn_claim_assigned     0.99                0.64                level     
tbb/guarded/library_default          0.98                0.65                level     
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   0.964         
clang++   omp    library_default        1.005         
clang++   plain  not_applicable         0.925         
clang++   tbb    cairn_claim_assigned   0.990         
clang++   tbb    library_default        0.998         
g++       omp    cairn_claim_assigned   0.998         
g++       omp    cairn_claim_on_demand  0.987         
g++       omp    library_default        0.989         
g++       plain  not_applicable         1.321         
g++       tbb    cairn_claim_assigned   1.011         
g++       tbb    library_default        1.007         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0186    0.3169    5.1233  
clang++   omp/guarded/cairn_claim_assigned     108.4925  134.2338  97.9485 
clang++   omp/guarded/library_default          17.5937   58.3217   39.5284 
clang++   omp/unguarded/cairn_claim_assigned   17.2567   37.6138   221.5515
clang++   omp/unguarded/library_default        39.1525   114.5123  60.5389 
clang++   plain/guarded/not_applicable         0.0262    0.3989    7.7347  
clang++   plain/unguarded/not_applicable       0.0146    0.2378    3.9498  
clang++   tbb/guarded/cairn_claim_assigned     0.1837    0.7726    14.8442 
clang++   tbb/guarded/library_default          2.3986    3.1255    9.7843  
clang++   tbb/unguarded/cairn_claim_assigned   0.1686    0.5325    12.1320 
clang++   tbb/unguarded/library_default        2.3173    3.0677    9.1376  
g++       cairn/guarded/not_applicable         0.0296    0.3899    6.9921  
g++       omp/guarded/cairn_claim_assigned     186.5403  197.1535  251.6539
g++       omp/guarded/cairn_claim_on_demand    207.4006  220.5580  213.2437
g++       omp/guarded/library_default          195.7715  222.4198  216.6308
g++       omp/unguarded/cairn_claim_assigned   181.5442  220.4982  192.6090
g++       omp/unguarded/cairn_claim_on_demand  196.4633  212.1554  199.0695
g++       omp/unguarded/library_default        199.1025  200.1527  174.5484
g++       plain/guarded/not_applicable         0.0312    0.6779    9.7456  
g++       plain/unguarded/not_applicable       0.0234    0.3839    6.5769  
g++       tbb/guarded/cairn_claim_assigned     0.2008    1.1535    20.1864 
g++       tbb/guarded/library_default          2.1747    3.7894    15.1905 
g++       tbb/unguarded/cairn_claim_assigned   0.1704    0.6578    14.0093 
g++       tbb/unguarded/library_default        2.1278    3.1186    10.1839 

== histogram_u32 ==
claim: expressiveness; in because the lane rule forbids the shared-bin parallel shape, so the CAIRN arm is sequential

safety boundaries
arm                      entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-----------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)          3      4        0           1           -               -                     
cairn|guarded|clang++    3      4        0           1           yes             present               
cairn|guarded|g++        3      4        0           1           yes             present               
omp|guarded|clang++      3      4        0           1           yes             present               
omp|guarded|g++          3      4        0           1           yes             present               
omp|unguarded|clang++    0      0        0           0           no              absent                
omp|unguarded|g++        0      0        0           0           no              absent                
plain|guarded|clang++    3      4        0           1           yes             present               
plain|guarded|g++        3      4        0           1           yes             present               
plain|unguarded|clang++  0      0        0           0           no              absent                
plain|unguarded|g++      0      0        0           0           no              absent                
tbb|guarded|clang++      3      4        0           1           yes             present               
tbb|guarded|g++          3      4        0           1           yes             present               
tbb|unguarded|clang++    0      0        0           0           no              absent                
tbb|unguarded|g++        0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000230                      0.038810                          0.057146                     0.080819                            0.047731                       0.000228                      0.000246                        0.000637                          0.010641                     0.000407                            0.009923                     
10000      0.002644                      0.236387                          0.117595                     0.145545                            0.041933                       0.002281                      0.002557                        0.005596                          0.014513                     0.003605                            0.012287                     
100000     0.024155                      0.111041                          0.096622                     0.103494                            0.163778                       0.023753                      0.024951                        0.021041                          0.027804                     0.015143                            0.016931                     
1000000    0.240145                      0.245083                          0.478593                     0.460242                            0.455456                       0.253157                      0.244910                        0.142829                          0.117512                     0.067146                            0.078723                     
10000000   2.541052                      0.942597                          1.084305                     1.502316                            0.732723                       2.381219                      2.367442                        0.896017                          0.546596                     0.438531                            0.379631                     
100000000  28.281748                     10.461452                         9.403389                     11.490734                           10.049647                      29.143055                     28.890209                       8.482640                          6.800715                     7.049824                            6.688416                     

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000328                      0.156038                          0.156199                           0.164894                     0.153787                            0.161170                             0.162068                       0.000326                      0.000310                        0.000605                          0.010357                     0.000404                            0.010247                     
10000      0.003088                      0.154684                          0.153662                           0.161809                     0.154822                            0.159423                             0.161611                       0.002830                      0.003610                        0.005064                          0.012154                     0.002794                            0.011438                     
100000     0.034714                      0.161571                          0.160048                           0.169419                     0.163769                            0.157782                             0.162138                       0.031367                      0.033228                        0.018776                          0.023706                     0.015432                            0.021980                     
1000000    0.334366                      0.222913                          0.201662                           0.267926                     0.232998                            0.199364                             0.229035                       0.307422                      0.302264                        0.144795                          0.150395                     0.059864                            0.102402                     
10000000   3.178367                      0.691775                          0.706530                           1.095861                     0.755129                            0.661735                             0.871377                       3.231323                      3.385599                        0.743026                          0.590948                     0.550266                            0.423916                     
100000000  34.726522                     8.614191                          7.435361                           10.572597                    9.028610                            7.174328                             8.813795                       34.824998                     35.473494                       7.893894                          6.688294                     6.852010                            6.644003                     

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict   
-----------------------------------  ------------------  ------------------  ----------
omp/guarded/cairn_claim_assigned     0.37                0.25                loss      
omp/guarded/cairn_claim_on_demand    -                   0.21                incomplete
omp/guarded/library_default          0.33                0.30                loss      
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded  
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  
plain/guarded/not_applicable         1.03                1.00                level     
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded  
tbb/guarded/cairn_claim_assigned     0.30                0.23                loss      
tbb/guarded/library_default          0.24                0.19                loss      
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded  
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded  

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   1.098         
clang++   omp    library_default        1.069         
clang++   plain  not_applicable         0.991         
clang++   tbb    cairn_claim_assigned   0.831         
clang++   tbb    library_default        0.983         
g++       omp    cairn_claim_assigned   1.048         
g++       omp    cairn_claim_on_demand  0.965         
g++       omp    library_default        0.834         
g++       plain  not_applicable         1.019         
g++       tbb    cairn_claim_assigned   0.868         
g++       tbb    library_default        0.993         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0277    0.2335    4.1935  
clang++   omp/guarded/cairn_claim_assigned     35.9035   75.3669   327.1655
clang++   omp/guarded/library_default          153.8352  99.0041   73.0143 
clang++   omp/unguarded/cairn_claim_assigned   63.1206   59.8424   388.4182
clang++   omp/unguarded/library_default        234.8593  39.6783   103.7761
clang++   plain/guarded/not_applicable         0.0277    0.2612    3.8084  
clang++   plain/unguarded/not_applicable       0.0278    0.2345    3.8833  
clang++   tbb/guarded/cairn_claim_assigned     0.6045    1.1282    12.4252 
clang++   tbb/guarded/library_default          4.6854    10.3149   14.0012 
clang++   tbb/unguarded/cairn_claim_assigned   0.8998    1.0989    10.1843 
clang++   tbb/unguarded/library_default        4.5510    9.4546    14.1493 
g++       cairn/guarded/not_applicable         0.0482    0.3259    4.5067  
g++       omp/guarded/cairn_claim_assigned     155.6722  152.9877  152.9548
g++       omp/guarded/cairn_claim_on_demand    146.9627  162.2446  230.0078
g++       omp/guarded/library_default          157.2688  158.8125  167.9148
g++       omp/unguarded/cairn_claim_assigned   156.0725  168.1220  154.9574
g++       omp/unguarded/cairn_claim_on_demand  156.2721  156.4797  169.0248
g++       omp/unguarded/library_default        156.7309  158.8999  159.7120
g++       plain/guarded/not_applicable         0.0494    0.3437    4.9853  
g++       plain/unguarded/not_applicable       0.0467    0.3369    5.4195  
g++       tbb/guarded/cairn_claim_assigned     0.8052    1.1586    12.4914 
g++       tbb/guarded/library_default          4.2051    12.3185   14.2526 
g++       tbb/unguarded/cairn_claim_assigned   0.7881    1.0441    8.4011  
g++       tbb/unguarded/library_default        4.2557    12.2098   12.5324 

== stencil_1d ==
claim: ratio; in because the out-of-place shape is accepted and the in-place shape is refused, which no C++ toolchain refuses
refusal: E-PARALLEL-RACE as preregistered; the compiler printed E-PARALLEL-RACE

safety boundaries
arm                         entry  element  arithmetic  conversion  equal to cairn  boundary in the object
--------------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)             3      6        3           0           -               -                     
cairn_wrap|guarded|clang++  3      6        0           0           no              present               
cairn_wrap|guarded|g++      3      6        0           0           no              present               
cairn|guarded|clang++       3      6        3           0           yes             present               
cairn|guarded|g++           3      6        3           0           yes             present               
omp|guarded|clang++         3      6        3           0           yes             present               
omp|guarded|g++             3      6        3           0           yes             present               
omp|unguarded|clang++       0      0        0           0           no              absent                
omp|unguarded|g++           0      0        0           0           no              absent                
plain|guarded|clang++       3      6        3           0           yes             present               
plain|guarded|g++           3      6        3           0           yes             present               
plain|unguarded|clang++     0      0        0           0           no              absent                
plain|unguarded|g++         0      0        0           0           no              absent                
tbb|guarded|clang++         3      6        3           0           yes             present               
tbb|guarded|g++             3      6        3           0           yes             present               
tbb|unguarded|clang++       0      0        0           0           no              absent                
tbb|unguarded|g++           0      0        0           0           no              absent                

not measured
arm  compiler  why          
---  --------  -------------
omp  clang++   did-not-build
omp  clang++   did-not-build

median milliseconds, clang++
n          cairn/guarded/not_applicable  cairn_wrap/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  ---------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000180                      0.000180                           0.018605                          0.002318                     0.069894                            0.001813                       0.000186                      0.000220                        0.000570                          0.006506                     0.000591                            0.006752                     
10000      0.002002                      0.001932                           0.172480                          0.018865                     0.250346                            0.071172                       0.001906                      0.002020                        0.005002                          0.008934                     0.005260                            0.009688                     
100000     0.025705                      0.021752                           0.237264                          0.126034                     0.132496                            0.103796                       0.020777                      0.020579                        0.015993                          0.026721                     0.023249                            0.023596                     
1000000    0.178058                      0.178452                           0.336500                          0.366807                     0.398394                            0.226050                       0.195470                      0.192185                        0.119759                          0.164606                     0.202554                            0.131249                     
10000000   1.513105                      1.229783                           1.855512                          1.344837                     1.768794                            1.834879                       2.611239                      2.266714                        0.933423                          0.782462                     1.069802                            0.759897                     
100000000  21.483955                     21.521392                          23.928380                         23.197379                    23.498968                           24.940123                      28.478932                     29.353106                       21.244013                         21.018128                    21.303238                           21.199721                    

median milliseconds, g++
n          cairn/guarded/not_applicable  cairn_wrap/guarded/not_applicable  omp/guarded/cairn_claim_assigned  omp/guarded/cairn_claim_on_demand  omp/guarded/library_default  omp/unguarded/cairn_claim_assigned  omp/unguarded/cairn_claim_on_demand  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/cairn_claim_assigned  tbb/guarded/library_default  tbb/unguarded/cairn_claim_assigned  tbb/unguarded/library_default
---------  ----------------------------  ---------------------------------  --------------------------------  ---------------------------------  ---------------------------  ----------------------------------  -----------------------------------  -----------------------------  ----------------------------  ------------------------------  --------------------------------  ---------------------------  ----------------------------------  -----------------------------
1000       0.000172                      0.000139                           0.097117                          0.105688                           0.102680                     0.101947                            0.099427                             0.098804                       0.000139                      0.000136                        0.000621                          0.006811                     0.000629                            0.006485                     
10000      0.001660                      0.001546                           0.095299                          0.104504                           0.114871                     0.112380                            0.102416                             0.086012                       0.001577                      0.001476                        0.006105                          0.009554                     0.006164                            0.008733                     
100000     0.020518                      0.021127                           0.128108                          0.109699                           0.124877                     0.105819                            0.105565                             0.107745                       0.017298                      0.014068                        0.019652                          0.032827                     0.019696                            0.027949                     
1000000    0.183713                      0.173920                           0.250951                          0.173923                           0.238206                     0.132598                            0.124067                             0.125460                       0.160947                      0.140958                        0.156281                          0.154246                     0.156354                            0.159741                     
10000000   1.077710                      1.285786                           1.178299                          1.253103                           1.428682                     0.511020                            0.413019                             0.444015                       1.946740                      1.702487                        0.969979                          0.932245                     1.055833                            2.159005                     
100000000  21.525035                     21.091371                          22.591157                         22.595147                          22.098227                    21.383213                           21.466073                            22.113959                      27.937944                     27.586523                       21.525368                         21.292179                    21.418988                           20.959426                    

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                               clang++             g++                 verdict            
-----------------------------------  ------------------  ------------------  -------------------
omp/guarded/cairn_claim_assigned     1.11                1.05                level              
omp/guarded/cairn_claim_on_demand    -                   1.05                incomplete         
omp/guarded/library_default          1.08                1.03                level              
omp/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded           
omp/unguarded/cairn_claim_on_demand  boundaries:unequal  boundaries:unequal  excluded           
omp/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded           
plain/guarded/not_applicable         1.33                1.30                win from n=10000000
plain/unguarded/not_applicable       boundaries:unequal  boundaries:unequal  excluded           
tbb/guarded/cairn_claim_assigned     0.99                1.00                level              
tbb/guarded/library_default          0.98                0.99                level              
tbb/unguarded/cairn_claim_assigned   boundaries:unequal  boundaries:unequal  excluded           
tbb/unguarded/library_default        boundaries:unequal  boundaries:unequal  excluded           

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm    grain row              at n=100000000
--------  -----  ---------------------  --------------
clang++   omp    cairn_claim_assigned   0.982         
clang++   omp    library_default        1.075         
clang++   plain  not_applicable         1.031         
clang++   tbb    cairn_claim_assigned   1.003         
clang++   tbb    library_default        1.009         
g++       omp    cairn_claim_assigned   0.947         
g++       omp    cairn_claim_on_demand  0.950         
g++       omp    library_default        1.001         
g++       plain  not_applicable         0.987         
g++       tbb    cairn_claim_assigned   0.995         
g++       tbb    library_default        0.984         

back to back regions, microseconds per region
compiler  column                               n=64      n=1024    n=16384 
--------  -----------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable         0.0168    0.2002    7.8267  
clang++   cairn_wrap/guarded/not_applicable    0.0178    0.2012    6.2521  
clang++   omp/guarded/cairn_claim_assigned     35.8314   77.2108   393.8614
clang++   omp/guarded/library_default          51.1540   160.1551  20.9991 
clang++   omp/unguarded/cairn_claim_assigned   34.1393   20.5040   184.8136
clang++   omp/unguarded/library_default        1.6302    36.7448   15.9449 
clang++   plain/guarded/not_applicable         0.0153    0.2048    3.7756  
clang++   plain/unguarded/not_applicable       0.0140    0.2084    3.8068  
clang++   tbb/guarded/cairn_claim_assigned     0.0629    0.5227    13.1226 
clang++   tbb/guarded/library_default          2.3166    5.7479    10.4400 
clang++   tbb/unguarded/cairn_claim_assigned   0.1906    0.6695    12.4630 
clang++   tbb/unguarded/library_default        2.3816    6.7265    11.9853 
g++       cairn/guarded/not_applicable         0.0130    0.1861    5.9116  
g++       cairn_wrap/guarded/not_applicable    0.0132    0.2931    5.7012  
g++       omp/guarded/cairn_claim_assigned     102.1761  97.2379   98.2454 
g++       omp/guarded/cairn_claim_on_demand    102.6967  52.7377   112.4688
g++       omp/guarded/library_default          97.3516   101.4182  109.6495
g++       omp/unguarded/cairn_claim_assigned   199.3911  105.4410  116.5985
g++       omp/unguarded/cairn_claim_on_demand  104.0749  84.7030   106.2226
g++       omp/unguarded/library_default        93.8662   97.0656   102.4733
g++       plain/guarded/not_applicable         0.0112    0.1577    3.2008  
g++       plain/unguarded/not_applicable       0.0112    0.1782    2.6192  
g++       tbb/guarded/cairn_claim_assigned     0.0817    0.6597    13.2832 
g++       tbb/guarded/library_default          2.4124    6.4707    13.6601 
g++       tbb/unguarded/cairn_claim_assigned   0.0700    0.6700    13.3637 
g++       tbb/unguarded/library_default        2.3911    6.8795    10.8181 

== tasks_split ==
claim: ratio; in because four visibly disjoint parts under leases, against std::thread, OpenMP sections and parallel_invoke

safety boundaries
arm                        entry  element  arithmetic  conversion  equal to cairn  boundary in the object
-------------------------  -----  -------  ----------  ----------  --------------  ----------------------
cairn (receipt)            2      5        4           4           -               -                     
cairn|guarded|clang++      2      5        4           4           yes             present               
cairn|guarded|g++          2      5        4           4           yes             present               
omp|guarded|clang++        2      5        4           4           yes             present               
omp|guarded|g++            2      5        4           4           yes             present               
omp|unguarded|clang++      0      0        0           0           no              absent                
omp|unguarded|g++          0      0        0           0           no              absent                
plain|guarded|clang++      2      5        4           4           yes             present               
plain|guarded|g++          2      5        4           4           yes             present               
plain|unguarded|clang++    0      0        0           0           no              absent                
plain|unguarded|g++        0      0        0           0           no              absent                
tbb|guarded|clang++        2      5        4           4           yes             present               
tbb|guarded|g++            2      5        4           4           yes             present               
tbb|unguarded|clang++      0      0        0           0           no              absent                
tbb|unguarded|g++          0      0        0           0           no              absent                
threads|guarded|clang++    2      5        4           4           yes             present               
threads|guarded|g++        2      5        4           4           yes             present               
threads|unguarded|clang++  0      0        0           0           no              absent                
threads|unguarded|g++      0      0        0           0           no              absent                

median milliseconds, clang++
n          cairn/guarded/not_applicable  omp/guarded/library_default  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/library_default  tbb/unguarded/library_default  threads/guarded/not_applicable  threads/unguarded/not_applicable
---------  ----------------------------  ---------------------------  -----------------------------  ----------------------------  ------------------------------  ---------------------------  -----------------------------  ------------------------------  --------------------------------
1000       0.206367                      0.000728                     0.000751                       0.000084                      0.000071                        0.000381                     0.000434                       0.214178                        0.207401                        
10000      0.206253                      0.000885                     0.000898                       0.001061                      0.001021                        0.002065                     0.001970                       0.211015                        0.206046                        
100000     0.208014                      0.003315                     0.003453                       0.010501                      0.010716                        0.008801                     0.010012                       0.208094                        0.210520                        
1000000    0.236388                      0.030390                     0.060981                       0.099516                      0.068333                        0.055163                     0.068393                       0.239626                        0.246674                        
10000000   0.745867                      0.747832                     0.394577                       1.720078                      1.679757                        0.620308                     0.669758                       0.780824                        0.755216                        
100000000  30.526885                     30.833989                    29.135396                      38.174478                     37.460389                       29.159971                    28.893646                      30.206792                       30.120571                       

median milliseconds, g++
n          cairn/guarded/not_applicable  omp/guarded/library_default  omp/unguarded/library_default  plain/guarded/not_applicable  plain/unguarded/not_applicable  tbb/guarded/library_default  tbb/unguarded/library_default  threads/guarded/not_applicable  threads/unguarded/not_applicable
---------  ----------------------------  ---------------------------  -----------------------------  ----------------------------  ------------------------------  ---------------------------  -----------------------------  ------------------------------  --------------------------------
1000       0.213608                      0.001189                     0.000878                       0.000093                      0.000098                        0.000400                     0.000411                       0.206427                        0.207192                        
10000      0.206777                      0.001523                     0.001491                       0.001277                      0.001030                        0.002110                     0.002097                       0.214887                        0.208861                        
100000     0.210515                      0.008210                     0.004800                       0.013102                      0.010912                        0.009348                     0.010999                       0.218571                        0.211499                        
1000000    0.238642                      0.034930                     0.035802                       0.108645                      0.097321                        0.057805                     0.062637                       0.256027                        0.246137                        
10000000   0.718095                      0.536325                     0.526120                       1.625584                      1.820316                        0.585763                     0.700612                       0.741846                        0.777683                        
100000000  30.565372                     30.151819                    29.609158                      38.631178                     38.258235                       30.822787                    31.220313                      30.482970                       30.754822                       

ratio of baseline time to cairn time, above one favours cairn; equal boundaries only
column                            clang++             g++                 verdict            
--------------------------------  ------------------  ------------------  -------------------
omp/guarded/library_default       1.01                0.99                level              
omp/unguarded/library_default     boundaries:unequal  boundaries:unequal  excluded           
plain/guarded/not_applicable      1.25                1.26                win from n=10000000
plain/unguarded/not_applicable    boundaries:unequal  boundaries:unequal  excluded           
tbb/guarded/library_default       0.96                1.01                level              
tbb/unguarded/library_default     boundaries:unequal  boundaries:unequal  excluded           
threads/guarded/not_applicable    0.99                1.00                level              
threads/unguarded/not_applicable  boundaries:unequal  boundaries:unequal  excluded           

what the safety boundary costs: unguarded time divided by guarded time
compiler  arm      grain row        at n=100000000
--------  -------  ---------------  --------------
clang++   omp      library_default  0.945         
clang++   plain    not_applicable   0.981         
clang++   tbb      library_default  0.991         
clang++   threads  not_applicable   0.997         
g++       omp      library_default  0.982         
g++       plain    not_applicable   0.990         
g++       tbb      library_default  1.013         
g++       threads  not_applicable   1.009         

back to back regions, microseconds per region
compiler  column                            n=64      n=1024    n=16384 
--------  --------------------------------  --------  --------  --------
clang++   cairn/guarded/not_applicable      206.8031  206.8534  208.4323
clang++   omp/guarded/library_default       0.7082    0.9440    1.2858  
clang++   omp/unguarded/library_default     0.7094    0.6982    1.1608  
clang++   plain/guarded/not_applicable      0.0097    0.0760    1.6666  
clang++   plain/unguarded/not_applicable    0.0065    0.0684    1.6301  
clang++   tbb/guarded/library_default       0.2714    0.3510    2.6506  
clang++   tbb/unguarded/library_default     0.2111    0.3459    2.9489  
clang++   threads/guarded/not_applicable    214.1846  206.2461  209.1544
clang++   threads/unguarded/not_applicable  204.8781  202.2703  207.7020
g++       cairn/guarded/not_applicable      207.4825  212.2446  210.7053
g++       omp/guarded/library_default       1.2271    0.9152    1.6636  
g++       omp/unguarded/library_default     1.2475    0.8131    1.7058  
g++       plain/guarded/not_applicable      0.0107    0.0880    2.0331  
g++       plain/unguarded/not_applicable    0.0082    0.0822    1.9275  
g++       tbb/guarded/library_default       0.2358    0.4206    2.7831  
g++       tbb/unguarded/library_default     0.2173    0.4151    3.4440  
g++       threads/guarded/not_applicable    213.1473  213.1410  213.1670
g++       threads/unguarded/not_applicable  212.3503  212.6053  217.1127

One machine, one lane count, two compilers. Not a tuned-kernel claim, and not another host's numbers.

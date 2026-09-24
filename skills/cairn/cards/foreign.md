# The foreign card

Selected by launch. Codes: E-FOREIGN E-LAUNCH.

```text
A foreign implementation is vendored C++ or CUDA standing for a CAIRN reference. The manifest's [foreign] table names each source and the symbols it defines, "vendor/stencil.cu" = ["stencil_1d_tiled"]; an extern gives each its signature and effects; and fn stencil_tiled(...) implements stencil_1d { unsafe { stencil_launch(n, out, x); } } is the implementation, whose reference's ceiling names the foreign effect (E-IMPL-EFFECT). extern "stencil_1d_tiled" fn stencil_launch(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) launch(n, 256) effects(); is a CUDA __global__ kernel: a host call launches it over n threads, 256 to a block, on the thread's stream and returns once it has run; it takes scalars and @device or @unified views, returns nothing, and a block is 32 to 1024 threads in whole warps (E-LAUNCH, E-PLACEMENT); its row adds par:device and trap. The build compiles each source unchanged with the program's flags and device target and asserts each extern's C++ types; a C++ symbol has C linkage. cairn foreign PATH --implementation F says what F has, each claim apart: a declared contract, trusted and not checked; native-built; device-inspected (registers, shared memory); validated against its reference, or not run when it runs device code.
```

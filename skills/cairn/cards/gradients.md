# The gradients card

Sent to an agent when the program uses `grad`. Codes: `E-GRAD`, `E-GRAD-CALL`, `E-GRAD-FORM`, `E-GRAD-RACE`.

```text
derive grad for f; writes f_grad(f's parameters, seed if f returns a float, d_x per differentiated parameter): reverse mode, adding seed times each partial into d_x:rw<T> or rw<T>[n], reading d_out:ro<T>[n] for each float view f writes, and returning f's result; zero the adjoints first. derive grad[w, b] for f; names the parameters, else every float parameter and ro float view (E-GRAD). Fragment: immutable lets, if whose paths return, reduce + or a let mut float that one for loop adds into (acc += e) and nothing reads until it ends, loops and regions writing out[i] once, + - * /, sqrt, abs, floor ceil trunc, f32/f64 conversions, std.math exp and log, calls of functions with their own derive grad (E-GRAD-FORM, E-GRAD-CALL). A region's lane reading a differentiated input off [i] is E-GRAD-RACE.
```

# The generics card

Sent to an agent when the program uses `impl`, `trait`. Codes: `E-BOUND`, `E-GENERIC-ARITY`, `E-GENERIC-KIND`, `E-INFER`, `E-STATIC`, `E-TRAIT-AMBIGUOUS`, `E-TRAIT-IMPL`, `E-TRAIT-OVERLAP`.

```text
fn largest[T](a:T, b:T) -> T and struct Pair[T] { a:T; b:T; } take type parameters; [K:nat] is a static natural. Instances are monomorphized on demand and each is checked as ordinary code; type arguments are inferred from values, literals and the expected type, or written f[u64](x), Pair[u8](1, 2), Option[u64].None.

trait Shape { fn area(self:ro<Self>) -> u64; } with impl Shape for Square { ... } dispatches statically on the Self argument, and [S:Shape] is checked when the instance is made. Bounds join with +: a trait, a kind (copy: reusable; affine: droppable and storable; none: may be linear) or a scalar class (integer unsigned signed float numeric scalar: operators and literals allowed). value.f(a) is f(value, a), found first in the module of the receiver's type; Trait.f(value) picks between two traits' f (E-TRAIT-AMBIGUOUS). A ro<T> or rw<T> parameter borrows the named place you pass (a local, a field, another borrow): write area(sq), never &sq, and rw needs a mutable place. There is no implicit boxing.
```

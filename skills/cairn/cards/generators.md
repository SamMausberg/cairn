# The generators card

Selected by derive family recipe. Codes: E-DERIVE-COLLISION E-DERIVE-DOMAIN E-DERIVE-FIELD E-DERIVE-RECIPE E-DERIVE-TYPE E-FAMILY-LIMIT E-FAMILY-TARGET E-RECIPE E-RECIPE-STATIC E-UNINSTANTIATED.

```text
A function may declare one [K:nat] parameter. family gain = scale[1..257]; instantiates gain_1..gain_256 with bounded expansion and no runtime dispatcher. No semicolon follows a function block. Generated entries are not direct edit targets; read the generator's contract. Large expansion is not a measured advantage over compact C++ templates.

derive wire for Packet; takes fixed-width unsigned fields and creates little-endian, declaration-order codecs with no padding; it infers no framing, authentication or validation. wire is a library recipe. recipe name[K:nat, F:fn] for R { ... } (F is a function's name, called as $F(x)) holds ordinary fn and struct declarations and static forms: each f in R { } over fields, or each k in lo..hi { }, at declaration, statement, field-list or call-argument level, where it splices a list ({ a.$f, b.$f } gives two per step). fold | each ... { e } joins the expansions with an operator or a two-operand function (fold add_wrap each ..). where a = offset(f), w = fold + each f in R { bytes(f) } names static values (naturals, min, max); the facts take a field or a type alike, unsigned(f) or bytes(R): bytes bits offset index count typeof unsigned signed integer float scalar record. $name splices into identifiers (encode_$R, value.$f) or stands for the natural or type; bare R is the type; require cond, "message"; states the domain. A generated field may declare an earlier usize field as its extent: $f:Buf[$t][rows];. A recipe may also hold impl Trait for R { ... }.

derive name[naturals] for Type; expands before checking into code of the deriving module, checked like any other. std offers derive eq|ord|hash for P; (impls of std.core's Eq, Ord and Hash).
```

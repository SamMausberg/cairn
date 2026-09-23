## What changed from `v1.3.0` to `13692b3e0333925b8f9fb2f2af2800a337360b05`

Compared by `cairn diff` (cairn-native/1.3.0): 60 identical-code, 21 identical-source, 1 smt-equivalent, 40 unknown, 129 added. A function whose code is identical, with everything it calls, is counted and not listed.

| function | class | evidence | compiler-established changes |
|---|---|---|---|
| `std.arena.find` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.arena.insert` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.arena.remove` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.io.close` | unknown | Statement 'unsafe' is not modeled. |  |
| `std.io.failed` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.io.read_file` | unknown | An owner inside a record, a sum or an array is not modeled. Its own code is identical; something it calls changed. | guards written 3 -> 4; guards discharged 1 -> 2 |
| `std.io.size` | unknown | Statement 'unsafe' is not modeled. |  |
| `std.map.contains` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.map.find` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.map.grow` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.map.insert` | unknown | A generic function no code instantiates, so there is no code to compare, and it names fill, grow, place, which changed. |  |
| `std.map.new` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.map.place` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.map.probe` | unknown | A generic function no code instantiates, so there is no code to compare, and it names same, which changed. |  |
| `std.map.remove` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.mem.equal` | unknown | A generic function no code instantiates, so there is no code to compare, and it names same, which changed. |  |
| `std.net.abandon` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.net.close` | unknown | Statement 'unsafe' is not modeled. |  |
| `std.net.connect_to` | unknown | Statement 'unsafe' is not modeled. Its own code is identical; something it calls changed. |  |
| `std.net.error` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.net.listen_on` | unknown | Statement 'unsafe' is not modeled. |  |
| `std.net.send` | unknown | Statement 'unsafe' is not modeled. | guards written 6 -> 1; guards discharged 1 -> 0; guards emitted 4 -> 1; unsafe 1 -> 0 |
| `std.sort.search` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.sort.sift` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.sort.sort` | unknown | A generic function no code instantiates, so there is no code to compare, and it names sort_by, which changed. |  |
| `std.sort.sort_by` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.text.hash_bytes` | unknown | A loop may run past the 16-iteration unrolling budget; deciding it needs a precondition that holds every trip count, and so every symbolic extent it reads, at 16 or below. |  |
| `std.text.parse_i64` | unknown | A loop may run past the 16-iteration unrolling budget; deciding it needs a precondition that holds every trip count, and so every symbolic extent it reads, at 16 or below. | guards written 14 -> 7; guards discharged 3 -> 1; guards emitted 11 -> 6 |
| `std.text.parse_u64` | unknown | A loop may run past the 16-iteration unrolling budget; deciding it needs a precondition that holds every trip count, and so every symbolic extent it reads, at 16 or below. | guards written 8 -> 1; guards discharged 2 -> 0; guards emitted 6 -> 1 |
| `std.text.push_i64` | unknown | An owner inside a record, a sum or an array is not modeled. Its own code is identical; something it calls changed. |  |
| `std.text.push_u64` | unknown | An owner inside a record, a sum or an array is not modeled. Its own code is identical; something it calls changed. |  |
| `std.vec.extend_from` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.vec.extend_from[u8]` | unknown | An owner inside a record, a sum or an array is not modeled. |  |
| `std.vec.find` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.vec.insert` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.vec.pop` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.vec.push` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.vec.remove` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.vec.swap_remove` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.vec.truncate` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `std.net.address` | smt-equivalent | Z3 found no input on which they differ |  |
| `std.core.Eq.T.same[u8]` | added | (ro<u8>, ro<u8>) -> bool |  |
| `std.draw.apart` | added | (std.draw.Mark, std.draw.Mark) -> bool |  |
| `std.draw.blit` | added | (rw<std.image.Image>, ro<std.image.Image>, i64, i64) -> void |  |
| `std.draw.blit_part` | added | (rw<std.image.Image>, ro<std.image.Image>, usize, usize, usize, usize, i64, i64) -> void |  |
| `std.draw.capture` | added | (ro<std.image.Image>, ro<std.draw.Layout>, usize) -> std.core.Result[bool, std.io.IoError] |  |
| `std.draw.circle` | added | (rw<std.image.Image>, i64, i64, i64, u32) -> void |  |
| `std.draw.find` | added | (ro<std.draw.Layout>, usize, ro<u8>[n]@host) -> usize |  |
| `std.draw.glyph` | added | (usize, usize) -> u8 |  |
| `std.draw.inside` | added | (std.draw.Mark, std.draw.Mark) -> bool |  |
| `std.draw.json` | added | (ro<std.draw.Layout>, ro<std.image.Image>, u64, rw<std.vec.Vec[u8]>) -> void |  |
| `std.draw.layer` | added | (rw<std.image.Image>, ro<std.image.Image>) -> void |  |
| `std.draw.layout` | added | () -> std.draw.Layout |  |
| `std.draw.line` | added | (rw<std.image.Image>, i64, i64, i64, i64, u32) -> void |  |
| `std.draw.mark` | added | (rw<std.draw.Layout>, usize, ro<u8>[n]@host, i64, i64, i64, i64) -> void |  |
| `std.draw.marked` | added | (ro<std.draw.Layout>, usize, ro<u8>[n]@host) -> bool |  |
| `std.draw.over` | added | (u32, u32) -> u32 |  |
| `std.draw.place` | added | (ro<std.draw.Layout>, usize, ro<u8>[n]@host) -> std.draw.Mark |  |
| `std.draw.plot` | added | (rw<std.image.Image>, i64, i64, u32) -> void |  |
| `std.draw.rect` | added | (rw<std.image.Image>, i64, i64, i64, i64, u32) -> void |  |
| `std.draw.text` | added | (rw<std.image.Image>, i64, i64, usize, ro<u8>[n]@host, u32, i64) -> void |  |
| `std.draw.text_width` | added | (usize, i64) -> i64 |  |
| `std.env.args` | added | () -> std.core.Result[std.env.Args, std.io.IoError] |  |
| `std.env.begin` | added | (ro<std.env.Args>, usize) -> usize |  |
| `std.env.count` | added | (ro<std.env.Args>) -> usize |  |
| `std.env.end` | added | (ro<std.env.Args>, usize) -> usize |  |
| `std.env.var` | added | (usize, ro<u8>[n]@host) -> std.core.Result[std.core.Option[std.vec.Vec[u8]], std.io.IoError] |  |
| `std.fmt.below` | added | (ro<u64>[40]@host, usize) -> bool |  |
| `std.fmt.bit` | added | (ro<u64>[40]@host, usize) -> bool |  |
| `std.fmt.bytes` | added | (rw<std.vec.Vec[u8]>, usize, ro<u8>[n]@host) -> void |  |
| `std.fmt.fixed` | added | (rw<std.vec.Vec[u8]>, f64, usize) -> void |  |
| `std.fmt.hex` | added | (rw<std.vec.Vec[u8]>, u64, usize) -> void |  |
| `std.fmt.int` | added | (rw<Vec[u8]>, T) -> void |  |
| `std.fmt.int[i64]` | added | (rw<std.vec.Vec[u8]>, i64) -> void |  |
| `std.fmt.left` | added | (rw<std.vec.Vec[u8]>, usize, ro<u8>[n]@host, usize, u8) -> void |  |
| `std.fmt.lower` | added | (rw<u64>[40]@host, usize) -> void |  |
| `std.fmt.padded` | added | (rw<Vec[u8]>, T, usize, u8) -> void |  |
| `std.fmt.raise` | added | (rw<u64>[40]@host, usize) -> void |  |
| `std.fmt.repeat` | added | (rw<std.vec.Vec[u8]>, usize, u8) -> void |  |
| `std.fmt.right` | added | (rw<std.vec.Vec[u8]>, usize, ro<u8>[n]@host, usize, u8) -> void |  |
| `std.fmt.tenth` | added | (rw<u64>[40]@host) -> u8 |  |
| `std.fmt.times` | added | (rw<u64>[40]@host, u64) -> void |  |
| `std.fmt.uint` | added | (rw<Vec[u8]>, T) -> void |  |
| `std.fmt.uint[u64]` | added | (rw<std.vec.Vec[u8]>, u64) -> void |  |
| `std.fmt.uint[usize]` | added | (rw<std.vec.Vec[u8]>, usize) -> void |  |
| `std.fmt.zero` | added | (ro<u64>[40]@host) -> bool |  |
| `std.fs.append` | added | (usize, ro<u8>[n]@host, usize, ro<u8>[m]@host) -> std.core.Result[usize, std.io.IoError] |  |
| `std.fs.exists` | added | (usize, ro<u8>[n]@host) -> bool |  |
| `std.fs.open` | added | (usize, ro<u8>[n]@host, i32) -> std.core.Result[std.io.File, std.io.IoError] |  |
| `std.fs.put` | added | (usize, ro<u8>[n]@host, usize, ro<u8>[m]@host, i32) -> std.core.Result[usize, std.io.IoError] |  |
| `std.fs.read` | added | (usize, ro<u8>[n]@host) -> std.core.Result[std.vec.Vec[u8], std.io.IoError] |  |
| `std.fs.remove` | added | (usize, ro<u8>[n]@host) -> std.core.Result[usize, std.io.IoError] |  |
| `std.fs.rename` | added | (usize, ro<u8>[n]@host, usize, ro<u8>[k]@host) -> std.core.Result[usize, std.io.IoError] |  |
| `std.fs.terminated` | added | (usize, ro<u8>[n]@host, rw<u8>[4096]@host) -> std.core.Result[usize, std.io.IoError] |  |
| `std.fs.write` | added | (usize, ro<u8>[n]@host, usize, ro<u8>[m]@host) -> std.core.Result[usize, std.io.IoError] |  |
| `std.image.adler` | added | (usize, ro<u8>[n]@host) -> u32 |  |
| `std.image.alpha` | added | (u32) -> u8 |  |
| `std.image.blue` | added | (u32) -> u8 |  |
| `std.image.chunk` | added | (rw<std.vec.Vec[u8]>, ro<u32>[256]@host, ro<u8>[4]@host, usize, ro<u8>[n]@host) -> void |  |
| `std.image.crc` | added | (ro<u32>[256]@host, u32, usize, ro<u8>[n]@host) -> u32 |  |
| `std.image.crc_table` | added | (rw<u32>[256]@host) -> void |  |
| `std.image.differ` | added | (ro<std.image.Image>, ro<std.image.Image>) -> usize |  |
| `std.image.fill` | added | (rw<std.image.Image>, u32) -> void |  |
| `std.image.get` | added | (ro<std.image.Image>, usize, usize) -> u32 |  |
| `std.image.green` | added | (u32) -> u8 |  |
| `std.image.new` | added | (usize, usize) -> std.image.Image |  |
| `std.image.packed` | added | (usize, usize, usize, ro<u8>[n]@host, rw<std.vec.Vec[u8]>) -> void |  |
| `std.image.png` | added | (ro<std.image.Image>, rw<std.vec.Vec[u8]>) -> void |  |
| `std.image.ppm` | added | (ro<std.image.Image>, rw<std.vec.Vec[u8]>) -> void |  |
| `std.image.put` | added | (rw<u8>[13]@host, usize, u32) -> void |  |
| `std.image.red` | added | (u32) -> u8 |  |
| `std.image.rgba` | added | (u8, u8, u8, u8) -> u32 |  |
| `std.image.save_png` | added | (ro<std.image.Image>, usize, ro<u8>[n]@host) -> std.core.Result[usize, std.io.IoError] |  |
| `std.image.scanlines` | added | (ro<std.image.Image>) -> std.vec.Vec[u8] |  |
| `std.image.set` | added | (rw<std.image.Image>, usize, usize, u32) -> void |  |
| `std.image.shade` | added | (rw<std.image.Image>, ro<fn(usize, usize) -> u32>) -> void |  |
| `std.image.stored` | added | (rw<std.vec.Vec[u8]>, usize, ro<u8>[n]@host) -> void |  |
| `std.image.word` | added | (rw<std.vec.Vec[u8]>, u32) -> void |  |
| `std.io.outcome` | added | (i64) -> std.core.Result[usize, std.io.IoError] |  |
| `std.io.read_to_end` | added | (ro<std.io.File>, rw<std.vec.Vec[u8]>) -> std.core.Result[usize, std.io.IoError] |  |
| `std.map.get` | added | (ro<Map[K, V]>, ro<K>) -> Option[V] |  |
| `std.map.resolve` | added | (ro<Map[K, V]>, Slot) -> Option[usize] |  |
| `std.map.slot` | added | (ro<Map[K, V]>, ro<K>) -> Option[Slot] |  |
| `std.map.update` | added | (rw<Map[K, V]>, ro<K>, ro<fn(rw<V>)>) -> bool |  |
| `std.math.atan2` | added | (f64, f64) -> f64 |  |
| `std.math.c_atan2` | added | (f64, f64) -> f64 |  |
| `std.math.c_cos` | added | (f64) -> f64 |  |
| `std.math.c_exp` | added | (f64) -> f64 |  |
| `std.math.c_log` | added | (f64) -> f64 |  |
| `std.math.c_log2` | added | (f64) -> f64 |  |
| `std.math.c_pow` | added | (f64, f64) -> f64 |  |
| `std.math.c_sin` | added | (f64) -> f64 |  |
| `std.math.c_tan` | added | (f64) -> f64 |  |
| `std.math.cos` | added | (f64) -> f64 |  |
| `std.math.exp` | added | (f64) -> f64 |  |
| `std.math.log` | added | (f64) -> f64 |  |
| `std.math.log2` | added | (f64) -> f64 |  |
| `std.math.pow` | added | (f64, f64) -> f64 |  |
| `std.math.sin` | added | (f64) -> f64 |  |
| `std.math.tan` | added | (f64) -> f64 |  |
| `std.mem.equal[u8]` | added | (usize, ro<u8>[n]@host, usize, ro<u8>[m]@host) -> bool |  |
| `std.net.send_all` | added | (i32, usize, ro<u8>[n]@host) -> std.core.Result[usize, std.io.IoError] |  |
| `std.sort.radix_pass` | added | (usize, ro<T>[n]@host, rw<T>[n]@host, usize) -> void |  |
| `std.sort.radix_sort` | added | (usize, rw<T>[n]@host, rw<T>[n]@host) -> void |  |
| `std.sys.access` | added | (ro<u8>[1]@host, i32) -> i32 |  |
| `std.sys.nanosleep` | added | (ro<i64>[2]@host, rw<i64>[2]@host) -> i32 |  |
| `std.text.digits` | added | (usize, ro<u8>[n]@host, usize, u64) -> std.core.Result[u64, std.text.ParseError] |  |
| `std.time.clock` | added | (i32) -> u64 |  |
| `std.time.now` | added | () -> std.time.Instant |  |
| `std.time.since` | added | (std.time.Instant) -> u64 |  |
| `std.time.sleep` | added | (u64) -> void |  |
| `std.time.wall_ns` | added | () -> u64 |  |
| `std.vec.get[std.draw.Mark]` | added | (ro<std.vec.Vec[std.draw.Mark]>, usize) -> std.draw.Mark |  |
| `std.vec.get[usize]` | added | (ro<std.vec.Vec[usize]>, usize) -> usize |  |
| `std.vec.new[std.draw.Mark]` | added | () -> std.vec.Vec[std.draw.Mark] |  |
| `std.vec.new[u8]` | added | () -> std.vec.Vec[u8] |  |
| `std.vec.new[usize]` | added | () -> std.vec.Vec[usize] |  |
| `std.vec.push[std.draw.Mark]` | added | (rw<std.vec.Vec[std.draw.Mark]>, std.draw.Mark) -> void |  |
| `std.vec.push[u8]` | added | (rw<std.vec.Vec[u8]>, u8) -> void |  |
| `std.vec.push[usize]` | added | (rw<std.vec.Vec[usize]>, usize) -> void |  |
| `std.vec.reserve[std.draw.Mark]` | added | (rw<std.vec.Vec[std.draw.Mark]>, usize) -> void |  |
| `std.vec.reserve[usize]` | added | (rw<std.vec.Vec[usize]>, usize) -> void |  |
| `std.vec.truncate[u8]` | added | (rw<std.vec.Vec[u8]>, usize) -> void |  |
| `std.zlib.adler32` | added | (u32, usize, ro<u8>[n]@host) -> u32 |  |
| `std.zlib.adler_of` | added | (u64, ro<u8>[n]@host, usize) -> u64 |  |
| `std.zlib.bound` | added | (usize) -> usize |  |
| `std.zlib.compress` | added | (usize, ro<u8>[n]@host, i32) -> std.core.Result[std.vec.Vec[u8], std.zlib.ZError] |  |
| `std.zlib.crc32` | added | (u32, usize, ro<u8>[n]@host) -> u32 |  |
| `std.zlib.crc_of` | added | (u64, ro<u8>[n]@host, usize) -> u64 |  |
| `std.zlib.deflate_all` | added | (rw<u8>[1]@host, rw<usize>, ro<u8>[n]@host, usize, i32) -> i32 |  |

Types: `std.draw.Layout` added, `std.draw.Mark` added, `std.env.Args` added, `std.image.Image` added, `std.map.Map` changed, `std.map.Slot` added, `std.time.Instant` added, `std.zlib.ZError` added.

Predicted, not measured (AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes): std.io.close x1.0, std.io.read_file x1.0, std.io.size x1.0, std.net.address x1.0, std.net.close x1.0, std.net.connect_to x1.0, std.net.listen_on x1.0, std.text.hash_bytes x1.0, std.text.parse_i64 x1.0, std.text.parse_u64 x1.0, std.text.push_i64 x1.0, std.text.push_u64 x1.0, std.vec.extend_from[u8] x1.0.

Semantic version: **major**, because type std.map.Map's definition changed; std.core.Eq.T.same[u8] was added; std.draw.apart was added; std.draw.blit was added; std.draw.blit_part was added; std.draw.capture was added; std.draw.circle was added; std.draw.inside was added; std.draw.json was added; std.draw.layer was added; std.draw.layout was added; std.draw.line was added; std.draw.mark was added; std.draw.marked was added; std.draw.over was added; std.draw.place was added; std.draw.plot was added; std.draw.rect was added; std.draw.text was added; std.draw.text_width was added; std.env.args was added; std.env.begin was added; std.env.count was added; std.env.end was added; std.env.var was added; std.fmt.bytes was added; std.fmt.fixed was added; std.fmt.hex was added; std.fmt.int[i64] was added; std.fmt.left was added; std.fmt.right was added; std.fmt.uint[u64] was added; std.fmt.uint[usize] was added; std.fs.append was added; std.fs.exists was added; std.fs.open was added; std.fs.read was added; std.fs.remove was added; std.fs.rename was added; std.fs.write was added; std.image.alpha was added; std.image.blue was added; std.image.differ was added; std.image.fill was added; std.image.get was added; std.image.green was added; std.image.new was added; std.image.packed was added; std.image.png was added; std.image.ppm was added; std.image.red was added; std.image.rgba was added; std.image.save_png was added; std.image.scanlines was added; std.image.set was added; std.image.shade was added; std.io.outcome was added; std.io.read_to_end was added; std.math.atan2 was added; std.math.cos was added; std.math.exp was added; std.math.log was added; std.math.log2 was added; std.math.pow was added; std.math.sin was added; std.math.tan was added; std.mem.equal[u8] was added; std.net.send_all was added; std.sys.access was added; std.sys.nanosleep was added; std.time.now was added; std.time.since was added; std.time.sleep was added; std.time.wall_ns was added; std.vec.get[std.draw.Mark] was added; std.vec.get[usize] was added; std.vec.new[std.draw.Mark] was added; std.vec.new[u8] was added; std.vec.new[usize] was added; std.vec.push[std.draw.Mark] was added; std.vec.push[u8] was added; std.vec.push[usize] was added; std.vec.reserve[std.draw.Mark] was added; std.vec.reserve[usize] was added; std.vec.truncate[u8] was added; std.zlib.adler32 was added; std.zlib.compress was added; std.zlib.crc32 was added; std.fmt.int was added; std.fmt.padded was added; std.fmt.uint was added; std.map.get was added; std.map.resolve was added; std.map.slot was added; std.map.update was added; std.sort.radix_sort was added; type std.draw.Layout was added; type std.draw.Mark was added; type std.env.Args was added; type std.image.Image was added; type std.map.Slot was added; type std.time.Instant was added; type std.zlib.ZError was added.

It is these public functions are unproven, and cannot raise it further: `std.arena.find`, `std.arena.insert`, `std.arena.remove`, `std.io.close`, `std.io.read_file`, `std.io.size`, `std.map.contains`, `std.map.find`, `std.map.insert`, `std.map.new`, `std.map.remove`, `std.mem.equal`, `std.net.close`, `std.net.connect_to`, `std.net.listen_on`, `std.net.send`, `std.sort.search`, `std.sort.sort`, `std.sort.sort_by`, `std.text.hash_bytes`, `std.text.parse_i64`, `std.text.parse_u64`, `std.text.push_i64`, `std.text.push_u64`, `std.vec.extend_from`, `std.vec.extend_from[u8]`, `std.vec.find`, `std.vec.insert`, `std.vec.pop`, `std.vec.push`, `std.vec.remove`, `std.vec.swap_remove`, `std.vec.truncate`.

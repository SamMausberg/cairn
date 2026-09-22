"""std.image and std.draw against an independent rasterizer written here from the stated rules: every scene is drawn
by the CAIRN program and by Python, and the two images must agree pixel for pixel. The PNG and PPM files are
decoded by Python's zlib and by hand, and the text is drawn from the console font file itself."""

import gzip
import json
import os
import struct
import zlib
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import SANITIZED, contract, native, refused, watched

FONT = Path("/usr/share/consolefonts/Lat15-Fixed13.psf.gz")
W, H = 160, 128  # 20480 pixels: past the lane pool's cutoff, so fill, shade and layer run on the pool


def rgba(r, g, b, a):
    return (r << 24) | (g << 16) | (b << 8) | a


def over(d, s):
    a = s & 255
    if a == 255:
        return s
    if a == 0:
        return d
    k = 255 - a
    ch = [((s >> sh & 255) * a + (d >> sh & 255) * k + 127) // 255 for sh in (24, 16, 8)]
    return rgba(*ch, a + ((d & 255) * k + 127) // 255)


class Canvas:
    def __init__(self, w, h):
        self.w, self.h, self.px = w, h, [0] * (w * h)

    def plot(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y * self.w + x] = over(self.px[y * self.w + x], c)

    def fill(self, c):
        self.px = [c] * (self.w * self.h)

    def shade(self):
        self.px = [rgba(x % 256, y % 256, 128, 255) for y in range(self.h) for x in range(self.w)]

    def rect(self, x, y, w, h, c):
        for yy in range(max(y, 0), min(y + h, self.h)):
            for xx in range(max(x, 0), min(x + w, self.w)):
                self.plot(xx, yy, c)

    def line(self, x0, y0, x1, y1, c):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 <= x1 else -1), (1 if y0 <= y1 else -1)
        err, x, y = dx + dy, x0, y0
        while True:
            self.plot(x, y, c)
            if (x, y) == (x1, y1):
                return
            e2 = 2 * err
            if e2 >= dy:
                err, x = err + dy, x + sx
            if e2 <= dx:
                err, y = err + dx, y + sy

    def circle(self, cx, cy, r, c):
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.plot(x, y, c)

    def blit(self, src, x, y):
        for sy in range(src.h):
            for sx in range(src.w):
                self.plot(x + sx, y + sy, src.px[sy * src.w + sx])

    def layer(self, top):
        self.px = [over(d, s) for d, s in zip(self.px, top.px, strict=True)]

    def text(self, x, y, s, c, scale):
        for k, ch in enumerate(s.encode()):
            glyph = GLYPHS[ch if 32 <= ch <= 126 else ord("?")]
            for row, bits in enumerate(glyph):
                for col in range(8):
                    if bits >> (7 - col) & 1:
                        self.rect(x + (k * 8 + col) * scale, y + row * scale, scale, scale, c)


def glyphs() -> dict[int, bytes]:
    """The glyph of each ASCII byte, read from the PSF 1 file through its own Unicode table."""
    with gzip.open(FONT) as font:
        data = font.read()
    assert data[:2] == b"\x36\x04"
    size, count = data[3], 512 if data[2] & 1 else 256
    table, found, g, i = data[4 + count * size :], {}, 0, 0
    while g < count and i + 1 < len(table):
        (u,) = struct.unpack("<H", table[i : i + 2])
        i, g = i + 2, g + (u == 0xFFFF)
        if u not in (0xFFFE, 0xFFFF):
            found.setdefault(u, g)
    return {c: data[4 + found[c] * size : 4 + (found[c] + 1) * size] for c in range(32, 127)}


GLYPHS = glyphs() if FONT.exists() else {}

# One scene: shapes inside, across and outside every edge, opaque and translucent, text at two scales.
SCENE = [
    ("shade",),
    ("rect", 10, 12, 60, 30, rgba(255, 136, 0, 255)),
    ("rect", -20, 100, 50, 50, rgba(0, 0, 255, 128)),
    ("rect", 150, -5, 40, 20, rgba(0, 255, 0, 77)),
    ("rect", 5, 5, 0, 10, rgba(255, 255, 255, 255)),
    ("line", 0, 0, 159, 127, rgba(255, 255, 255, 255)),
    ("line", -30, 60, 190, 20, rgba(255, 0, 0, 200)),
    ("line", 80, 5, 80, 5, rgba(0, 0, 0, 255)),
    ("circle", 120, 90, 25, rgba(40, 200, 120, 160)),
    ("circle", -5, -5, 12, rgba(250, 250, 0, 255)),
    ("circle", 60, 60, 0, rgba(0, 0, 0, 255)),
    ("text", 4, 50, "CAIRN draws: {x}", rgba(255, 255, 255, 255), 1),
    ("text", 90, 104, "ok?", rgba(0, 0, 0, 180), 2),
    ("text", 150, 2, "clip\x7f", rgba(255, 0, 255, 255), 1),
    ("blit", 30, 70),
    ("layer",),
]


def source_of(op) -> str:
    kind, *a = op
    if kind == "shade":
        return "image.shade(img, |x:usize, y:usize| -> u32 { return image.rgba(u8(x % 256), u8(y % 256), 128, 255); });"
    if kind == "text":
        escaped = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else f"\\x{ord(ch):02x}" for ch in a[2])
        return f'draw.text(img, {a[0]}, {a[1]}, "{escaped}", {a[3]}, {a[4]});'
    if kind == "blit":
        return f"draw.blit(img, sprite, {a[0]}, {a[1]});"
    if kind == "layer":
        return "draw.layer(img, veil);"
    return f"draw.{kind}(img, {', '.join(map(str, a))});"


def program(out: str) -> str:
    body = "\n  ".join(source_of(op) for op in SCENE)
    return f"""import std.draw;
import std.fs;
import std.image (Image);
import std.vec (Vec);

fn main() -> i32 {{
  let mut sprite = image.new(24, 16);
  draw.rect(sprite, 0, 0, 24, 16, {rgba(20, 20, 20, 255)});
  draw.circle(sprite, 12, 8, 6, {rgba(255, 60, 60, 255)});
  let mut veil = image.new({W}, {H});
  image.fill(veil, {rgba(0, 0, 40, 60)});
  let mut img = image.new({W}, {H});
  {body}
  let mut png = vec.new[u8]();
  image.png(img, png);
  match fs.write("{out}.png", png.data[0..png.len]) {{
    Ok(n) => {{}}
    Err(e) => return 1;
  }}
  let mut ppm = vec.new[u8]();
  image.ppm(img, ppm);
  match fs.write("{out}.ppm", ppm.data[0..ppm.len]) {{
    Ok(n) => {{}}
    Err(e) => return 2;
  }}
  return 0;
}}
"""


def expected() -> Canvas:
    sprite = Canvas(24, 16)
    sprite.rect(0, 0, 24, 16, rgba(20, 20, 20, 255))
    sprite.circle(12, 8, 6, rgba(255, 60, 60, 255))
    veil = Canvas(W, H)
    veil.fill(rgba(0, 0, 40, 60))
    img = Canvas(W, H)
    for kind, *a in SCENE:
        if kind == "blit":
            img.blit(sprite, *a)
        elif kind == "layer":
            img.layer(veil)
        else:
            getattr(img, kind)(*a)
    return img


def decode_png(data: bytes) -> tuple[int, int, list[int]]:
    """A PNG of one IHDR, IDAT chunks and IEND, every CRC checked, 8-bit RGBA, rows of filter 0."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    at, idat, head = 8, b"", None
    while at < len(data):
        (size,) = struct.unpack(">I", data[at : at + 4])
        kind, body = data[at + 4 : at + 8], data[at + 8 : at + 8 + size]
        (crc,) = struct.unpack(">I", data[at + 8 + size : at + 12 + size])
        assert crc == zlib.crc32(kind + body), kind
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        at += 12 + size
    w, h, depth, colour, _, _, interlace = head
    assert (depth, colour, interlace) == (8, 6, 0)
    raw, pitch, px = zlib.decompress(idat), 1 + 4 * w, []
    for y in range(h):
        row = raw[y * pitch : (y + 1) * pitch]
        assert row[0] == 0
        px += [struct.unpack(">I", row[1 + 4 * x : 5 + 4 * x])[0] for x in range(w)]
    return w, h, px


@pytest.mark.skipif(not FONT.exists(), reason="the text oracle reads /usr/share/consolefonts/Lat15-Fixed13.psf.gz")
@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_pixel_is_the_independent_rasterizer_s(tmp_path, cxx):
    native(tmp_path, program(str(tmp_path / "scene")), cxx)
    want = expected()
    w, h, px = decode_png((tmp_path / "scene.png").read_bytes())
    assert (w, h) == (W, H)
    wrong = [(i % W, i // W, hex(p), hex(q)) for i, (p, q) in enumerate(zip(px, want.px, strict=True)) if p != q]
    assert not wrong, f"{len(wrong)} pixels differ, first {wrong[:5]}"
    ppm = (tmp_path / "scene.ppm").read_bytes()
    header = f"P6\n{W} {H}\n255\n".encode()
    assert ppm[: len(header)] == header
    assert ppm[len(header) :] == b"".join(bytes([p >> 24, p >> 16 & 255, p >> 8 & 255]) for p in want.px)


@pytest.mark.parametrize("sanitizer", ["address,undefined", "thread"])
def test_the_scene_is_sanitizer_clean(tmp_path, sanitizer):
    generated = compile_source(program(str(tmp_path / "scene")), roots=("main",))[0]
    if sanitizer == "thread":
        done = watched(tmp_path, generated, "clang++", "thread")  # fill, shade and layer run on the lane pool
    else:
        env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1"}
        done = contract(tmp_path, generated, "clang++", *SANITIZED[1:], env=env)
    assert done.returncode == 0, done.stderr[-3000:]
    if GLYPHS:  # the instrumented program draws the same pixels as the plain one
        assert decode_png((tmp_path / "scene.png").read_bytes())[2] == expected().px


def test_a_large_image_spans_several_stored_blocks(tmp_path):
    """300 * 300 RGBA rows are 360300 bytes, six stored blocks; zlib reads them and the Adler-32 back."""
    source = f"""import std.fs;
import std.image (Image);
import std.vec (Vec);
fn main() -> i32 {{
  let mut img = image.new(300, 300);
  image.fill(img, {rgba(1, 2, 3, 4)});
  image.set(img, 299, 299, {rgba(9, 8, 7, 6)});
  match image.save_png(img, "{tmp_path / "big.png"}") {{
    Ok(n) => return 0;
    Err(e) => return 1;
  }}
}}
"""
    native(tmp_path, source)
    w, h, px = decode_png((tmp_path / "big.png").read_bytes())
    assert (w, h) == (300, 300) and px[-1] == rgba(9, 8, 7, 6) and set(px[:-1]) == {rgba(1, 2, 3, 4)}


def test_a_png_from_std_zlib_is_the_same_image_smaller(tmp_path):
    source = f"""import std.fs;
import std.image (Image);
import std.vec (Vec);
import std.zlib;
fn main() -> i32 {{
  let mut img = image.new(64, 64);
  image.shade(img, |x:usize, y:usize| -> u32 {{ return image.rgba(u8(x * 4), u8(y * 4), 0, 255); }});
  let raw = image.scanlines(img);
  match zlib.compress(raw.data[0..raw.len], 9) {{
    Ok(z) => {{
      let mut out = vec.new[u8]();
      image.packed(img.w, img.h, z.data[0..z.len], out);
      match fs.write("{tmp_path / "small.png"}", out.data[0..out.len]) {{
        Ok(n) => return 0;
        Err(e) => return 1;
      }}
    }}
    Err(e) => return 2;
  }}
}}
"""
    native(tmp_path, source)
    data = (tmp_path / "small.png").read_bytes()
    w, h, px = decode_png(data)
    assert (w, h) == (64, 64) and len(data) < 64 * 64 * 4
    assert px == [rgba(x * 4, y * 4, 0, 255) for y in range(64) for x in range(64)]


def test_differ_counts_pixels_and_a_column_past_the_width_traps(tmp_path):
    ok = """import std.image (Image);
fn main() -> i32 {
  let mut a = image.new(8, 4);
  let b = image.new(8, 4);
  image.set(a, 7, 3, 1);
  image.set(a, 0, 0, 2);
  let tall = image.new(4, 8);
  if image.differ(a, b) != 2 || image.differ(a, tall) != 32 { return 1; }
  if image.get(a, 7, 3) != 1 { return 2; }
  return 0;
}
"""
    native(tmp_path, ok)
    (tmp_path / "trap").mkdir()
    with pytest.raises(AssertionError, match="exit -6"):  # (8, 0) is inside the pixel array, but not the image
        native(tmp_path / "trap", ok.replace("image.get(a, 7, 3)", "image.get(a, 8, 0)"))


def test_an_empty_image_traps_where_it_is_made(tmp_path):
    source = "import std.image (Image);\nfn main() -> i32 { let img = image.new(0, 5); return 0; }\n"
    with pytest.raises(AssertionError, match="exit -6"):
        native(tmp_path, source)


def test_a_blit_of_an_image_onto_itself_is_refused():
    refused(
        "E-ALIAS",
        "import std.draw;\nimport std.image (Image);\nfn main() -> i32 { let mut a = image.new(2, 2); draw.blit(a, a, 0, 0); return 0; }\n",
    )


def test_a_layout_record_is_json_with_every_mark(tmp_path):
    source = f"""import std.draw;
import std.fs;
import std.image (Image);
import std.vec (Vec);
fn main() -> i32 {{
  let img = image.new(40, 30);
  let mut l = draw.layout();
  draw.mark(l, "panel", 2, 3, 20, 10);
  draw.mark(l, "a \\"quoted\\\\ name\\x01", -1, 0, 5, 5);
  let mut out = vec.new[u8]();
  draw.json(l, img, 7, out);
  match fs.write("{tmp_path / "layout.json"}", out.data[0..out.len]) {{
    Ok(n) => return 0;
    Err(e) => return 1;
  }}
}}
"""
    native(tmp_path, source)
    record = json.loads((tmp_path / "layout.json").read_text())
    assert record == {
        "width": 40,
        "height": 30,
        "at_ns": 7,
        "elements": [
            {"name": "panel", "x": 2, "y": 3, "w": 20, "h": 10},
            {"name": 'a "quoted\\ name\x01', "x": -1, "y": 0, "w": 5, "h": 5},
        ],
    }

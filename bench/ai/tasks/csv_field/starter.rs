// Prints field k of every row of a comma-separated file.
use std::io::{self, Read, Write};

// Writes field k of the row text[lo..hi], where hi is the row's newline or the end of the input.
fn print_field(out: &mut impl Write, text: &[u8], lo: usize, hi: usize, k: usize) {
    let mut start = lo;
    for _ in 0..k {
        while start < hi && text[start] != b',' {
            start += 1;
        }
        if start == hi {
            out.write_all(b"none\n").unwrap();
            return;
        }
        start += 1; // past the comma
    }
    let mut end = start;
    while text[end] != b',' && text[end] != b'\n' {
        end += 1;
    }
    out.write_all(&text[start..end]).unwrap();
    out.write_all(b"\n").unwrap();
}

fn main() {
    let mut text = Vec::new();
    io::stdin().read_to_end(&mut text).unwrap();
    let mut out = io::stdout().lock();
    let mut pos = text.iter().position(|&b| b == b'\n').unwrap_or(text.len());
    let k: usize = std::str::from_utf8(&text[..pos]).unwrap().trim().parse().unwrap();
    pos += 1; // past the first line's newline
    while pos < text.len() {
        let mut hi = pos;
        while hi < text.len() && text[hi] != b'\n' {
            hi += 1;
        }
        print_field(&mut out, &text, pos, hi, k);
        pos = hi + 1;
    }
}

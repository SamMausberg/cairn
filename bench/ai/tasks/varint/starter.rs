// Decodes a stream of unsigned LEB128 integers given as hexadecimal on standard input.
use std::io::{self, Read, Write};

fn from_hex(hex: &str) -> Vec<u8> {
    hex.as_bytes()
        .chunks(2)
        .map(|p| u8::from_str_radix(std::str::from_utf8(p).unwrap(), 16).unwrap())
        .collect()
}

enum Status {
    Ok(u64),
    Truncated,
    Overflow,
}

// Decodes the integer that starts at `pos`, advancing `pos` past it.
fn decode(bytes: &[u8], pos: &mut usize) -> Status {
    let mut value = 0u64;
    for i in 0.. {
        let b = bytes[*pos];
        if i == 9 && b > 1 {
            return Status::Overflow;
        }
        value |= u64::from(b & 0x7f) << (7 * i);
        *pos += 1;
        if b < 0x80 {
            return Status::Ok(value);
        }
    }
    unreachable!()
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let bytes = from_hex(input.trim());
    let mut out = io::stdout().lock();
    let mut pos = 0;
    while pos < bytes.len() {
        let start = pos;
        match decode(&bytes, &mut pos) {
            Status::Ok(value) => writeln!(out, "{}", value).unwrap(),
            Status::Truncated => {
                writeln!(out, "err truncated {}", start).unwrap();
                return;
            }
            Status::Overflow => {
                writeln!(out, "err overflow {}", start).unwrap();
                return;
            }
        }
    }
}

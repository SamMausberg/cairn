use std::io::{self, Read};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut tokens = input.split_ascii_whitespace();
    let cap: usize = tokens.next().unwrap().parse().unwrap();
    let hex = tokens.next().unwrap_or("").as_bytes();
    let packed: Vec<u8> = hex
        .chunks(2)
        .map(|p| u8::from_str_radix(std::str::from_utf8(p).unwrap(), 16).unwrap())
        .collect();

    let mut out: Vec<u8> = Vec::with_capacity(cap);
    let mut pos = 0;
    while pos < packed.len() {
        let c = packed[pos] as usize;
        let body = if c < 128 { c + 1 } else { 1 };
        if packed.len() - pos - 1 < body {
            println!("err truncated {}", pos);
            return;
        }
        let run = if c < 128 { c + 1 } else { c - 126 };
        if run > cap - out.len() {
            println!("err full {}", pos);
            return;
        }
        if c < 128 {
            out.extend_from_slice(&packed[pos + 1..pos + 1 + run]);
        } else {
            out.resize(out.len() + run, packed[pos + 1]);
        }
        pos += 1 + body;
    }
    let hash = out.iter().fold(14695981039346656037u64, |h, &b| (h ^ u64::from(b)).wrapping_mul(1099511628211));
    println!("ok {} {:016x}", out.len(), hash);
}
